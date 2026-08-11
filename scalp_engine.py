#!/usr/bin/env python3
"""
scalp_engine.py — a REAL paper scalping engine (separate from scalp_lab).

What makes this actual scalping (vs. the 'favorite-harvest' the lab does):
  • watches a small set of LIQUID markets,
  • polls the live ORDER BOOK (best bid/ask) on a fast loop (~10s),
  • trades tiny mean-reversion wiggles: buy a small dip, sell the bounce,
  • exits in seconds-to-minutes, re-enters the same market freely.

HONESTY — two fill models run on the SAME entry/exit signals:
  • taker : enter at the ASK, exit at the BID  (you cross the spread both ways —
            realistic for a market-taker; must beat the round-trip spread).
  • maker : enter at the BID, exit at the ASK  (assumes your limit orders fill —
            the ONLY way scalping profits, but the fills are NOT guaranteed, so
            treat this P&L as an OPTIMISTIC UPPER BOUND, not reality).
The gap between the two IS the spread cost — shown side by side for full openness.

Paper only. No keys, no orders, stdlib only. Reads Polymarket's public CLOB +
Gamma. Safe to run anywhere.

Usage:
  python3 scalp_engine.py run     # fast loop (default ~10s)
  python3 scalp_engine.py once    # one burst of sub-polls (for the watchdog)
  python3 scalp_engine.py board   # print taker vs maker scoreboard
"""
import os, sys, json, time, re, urllib.parse, urllib.request
from datetime import datetime, timezone

BASE   = os.path.dirname(os.path.abspath(__file__))
STATE  = os.path.join(BASE, "scalp_engine_state.json")
GAMMA  = "https://gamma-api.polymarket.com/markets"
CLOB   = "https://clob.polymarket.com"
UA     = "scalp-engine-paper/1.0"
TIMEOUT = 8

# ── Wallet-follow mode paths ──────────────────────────────────────────────────
BOT_LOG   = os.path.join(BASE, "run_output.txt")   # primary bot ELITE OPEN lines
WF_LOG    = os.path.join(BASE, "wallet_scalp.log") # our own scalp log

CONFIG = {
    "poll_sec":            10,     # book poll cadence (real scalping rhythm)
    "subpolls_per_once":   5,      # 'once' does this many polls ~poll_sec apart
    "watchlist_size":      12,     # how many liquid markets to scalp
    "refresh_watch_sec":   900,    # rebuild the watchlist this often
    "wl_price_min":        0.15,   # mid-range markets oscillate; avoid 0/1 tails
    "wl_price_max":        0.85,
    "wl_min_volume":       300_000,
    "wl_max_spread":       0.03,   # only scalp reasonably tight books
    "ref_window":          6,      # rolling mids kept per market for the reference
    "entry_edge":          0.015,  # buy when mid dips >= this below the rolling mean
    "target":              0.015,  # take profit when mid recovers this much
    "stop":                0.02,   # bail if mid falls this much further
    "max_hold_sec":        420,    # don't let a 'scalp' become a bag-hold
    "max_open":            8,      # per fill-model book
    "bet_usdc":            1.0,
    "models":              ["taker", "maker"],  # which fill-books take NEW entries
                                                 # (set to ["maker"] to kill taker)
    "max_trades_per_market_day": 7,  # trade a single market up to 7x/day, then rest it
                                     # (repetition only helps positive-EV trades — see note)

    # ── makerH: HONEST maker (Phase A) ───────────────────────────────────────
    # The "maker" book above is optimistic — it assumes limit orders fill instantly.
    # makerH posts a resting quote that fills ONLY when the market trades THROUGH it,
    # cancels the quote if unfilled past the timeout, and on exit falls back to a
    # taker fill (booking the spread cost) if the sell quote doesn't get hit. This
    # is the realistic counterweight; compare makerH vs maker to see the fantasy.
    "makerh_enabled":          True,
    "maker_quote_timeout_sec": 90,   # cancel an unfilled entry quote after this
    "maker_exit_window_sec":   90,   # try to exit as maker this long, then take the bid

    # ── Mode ──────────────────────────────────────────────────────────────
    # "midpoint" = buy at/under midpoint_buy, sell at sell_target (the
    #              "buy till 50% / sell at 51" rule). 1¢ target < spread, so
    #              the TAKER book will lose; MAKER is the optimistic ceiling.
    # "meanrev"  = original dip/bounce scalping (buy below rolling mean).
    "mode":           "midpoint",
    "midpoint_buy":   0.50,         # buy when mid <= this (50% implied chance)
    "sell_target":    0.51,         # sell when mid >= this
    "midpoint_stop":  0.45,         # bail if it falls here

    # ── wallet_follow mode ────────────────────────────────────────────────────
    # Reads ELITE OPEN signals from the copy bot log, enters the same market
    # within seconds, targets a quick flip, exits fast.  The signal is the elite
    # wallet's opening; we ride the momentum they create.
    #
    # Paper only — same honesty rules as other modes.  taker fill model only
    # (we can't post limits fast enough to be a maker on these entries).
    #
    "wf_signal_window_sec":  90,     # only act on ELITE OPENs logged in last N secs
    "wf_min_rank":           0.00,   # wallet rank floor (0.00 = top of leaderboard)
    "wf_max_rank":           0.10,   # only follow top-10% ranked wallets
    "wf_min_yes_price":      0.25,   # skip cheap flyers (geo pump territory)
    "wf_max_yes_price":      0.65,   # skip expensive YES (0% WR zone)
    "wf_target":             0.025,  # take profit at +2.5¢ on mid
    "wf_stop":               0.020,  # stop at -2.0¢ on mid
    "wf_max_hold_sec":       300,    # max 5 min — if not hit, bail
    "wf_bet_usdc":           10.0,   # small bet per signal (scalp sizing)
    "wf_max_open":           5,      # max concurrent wallet-follow positions
    "wf_cooldown_sec":       180,    # re-entry cooldown per market after a trade
}


def load_overrides(cfg):
    """Hot-reload tunables from scalp_engine_config.json each cycle, so params
    can be changed WITHOUT restarting the daemon. Unknown/extra keys are fine."""
    p = os.path.join(BASE, "scalp_engine_config.json")
    if os.path.exists(p):
        try:
            cfg.update(json.load(open(p)))
        except Exception as e:
            sys.stderr.write(f"[override] {e}\n")
    return cfg


# ── HTTP (fail-soft, rate-limit aware) ───────────────────────────────────────
# Pacing + 429/5xx backoff (honors Retry-After). Network/DNS errors are NOT
# retried — that's the flaky-egress case; fail soft & fast so the 10s scalp loop
# stays responsive. Same helper as scalp_lab.py.
_MIN_INTERVAL = 0.20   # >=5 req/s pacing; far under CLOB's 150/s market-data cap
_last_call = [0.0]

def http_get(url, params=None, retries=3):
    if params:
        url += "?" + urllib.parse.urlencode(params)
    backoff = 1.0
    for attempt in range(retries + 1):
        wait = _MIN_INTERVAL - (time.time() - _last_call[0])
        if wait > 0:
            time.sleep(wait)
        _last_call[0] = time.time()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except Exception as e:
            code = getattr(e, "code", None)
            if attempt < retries and (code == 429 or (code and 500 <= code < 600)):
                ra = None
                try:
                    ra = float(e.headers.get("Retry-After"))  # honor server hint
                except Exception:
                    ra = None
                time.sleep(ra if ra else backoff)
                backoff = min(backoff * 2, 8.0)
                continue
            sys.stderr.write(f"[http] {url[:70]} failed: {e}\n")
            return None


def midpoint(token_id):
    """Authoritative mid from /midpoint (more reliable than /book — see #180)."""
    d = http_get(f"{CLOB}/midpoint", {"token_id": token_id})
    try:
        return float(d["mid"]) if isinstance(d, dict) and d.get("mid") is not None else None
    except Exception:
        return None


def book(token_id):
    """Best bid/ask/mid/spread for a token from the CLOB. None on failure."""
    b = http_get(f"{CLOB}/book", {"token_id": token_id})
    if not isinstance(b, dict):
        return None
    bids, asks = b.get("bids") or [], b.get("asks") or []
    if not bids or not asks:
        return None
    try:
        bid = max(float(x["price"]) for x in bids)
        ask = min(float(x["price"]) for x in asks)
        if not (0 < bid < ask < 1):
            return None
        mid = (bid + ask) / 2
        # py-clob-client #180: /book can return a stale, blown-out spread while
        # /midpoint stays accurate. If the book spread looks implausible (>5c),
        # trust /midpoint for the MARK; real entry/exit fills still use bid/ask.
        if ask - bid > 0.05:
            mp = midpoint(token_id)
            if mp is not None and bid <= mp <= ask:
                mid = mp
        return {"bid": bid, "ask": ask, "mid": mid, "spread": ask - bid}
    except Exception:
        return None


def build_watchlist(cfg):
    """Liquid, mid-range, tight-spread markets worth scalping."""
    out, seen = [], set()
    for p in range(6):
        raw = http_get(GAMMA, {
            "closed": "false", "active": "true",
            "order": "volumeNum", "ascending": "false",
            "limit": 100, "offset": p * 100,
        }) or []
        if not raw:
            break
        for m in raw:
            try:
                cid = str(m.get("conditionId") or m.get("id"))
                if cid in seen:
                    continue
                prices = m.get("outcomePrices")
                if isinstance(prices, str):
                    prices = json.loads(prices)
                toks = m.get("clobTokenIds")
                if isinstance(toks, str):
                    toks = json.loads(toks)
                if not prices or not toks:
                    continue
                yes = float(prices[0])
                vol = float(m.get("volumeNum") or m.get("volume") or 0)
                if not (cfg["wl_price_min"] <= yes <= cfg["wl_price_max"]):
                    continue
                if vol < cfg["wl_min_volume"]:
                    continue
                seen.add(cid)
                out.append({"id": cid, "q": (m.get("question") or "")[:80],
                            "token": toks[0], "vol": vol})
            except Exception:
                continue
        if len(out) >= cfg["watchlist_size"] * 2:
            break
    return out[: cfg["watchlist_size"]]


# ── State ───────────────────────────────────────────────────────────────────
def load_state():
    if os.path.exists(STATE):
        try:
            return json.load(open(STATE))
        except Exception:
            pass
    return {"taker": {"open": [], "closed": []},
            "maker": {"open": [], "closed": []},
            "wallet_follow": {"open": [], "closed": []},
            "wf_cooldowns": {},
            "refs": {}, "watchlist": [], "wl_ts": 0}


def save_state(s):
    json.dump(s, open(STATE, "w"), indent=2)


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def secs_since(iso):
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(iso)).total_seconds()
    except Exception:
        return 0.0


# ── One book poll → update refs, run entries/exits for BOTH fill models ──────
def poll_once(state, cfg):
    load_overrides(cfg)                       # hot-reload tunables (no restart needed)
    # refresh watchlist if stale
    if (time.time() - state.get("wl_ts", 0)) > cfg["refresh_watch_sec"] or not state["watchlist"]:
        wl = build_watchlist(cfg)
        if wl:
            state["watchlist"] = wl
            state["wl_ts"] = time.time()

    books = {}
    for m in state["watchlist"]:
        bk = book(m["token"])
        if bk:
            books[m["id"]] = (m, bk)

    refs = state.setdefault("refs", {})
    for cid, (m, bk) in books.items():
        r = refs.setdefault(cid, [])
        r.append(round(bk["mid"], 4))
        del r[:-cfg["ref_window"]]          # keep only the last N mids

    # Exits run for BOTH books always, so a disabled book still winds down its
    # open positions. Entries only for ENABLED models (cfg["models"]).
    if cfg.get("mode") == "wallet_follow":
        wallet_follow_poll(state, cfg)
        return   # wallet_follow runs its own entry/exit logic, no book-poll needed

    for model in ("taker", "maker"):
        _exits(model, state, books, cfg)
    for model in cfg.get("models", ["taker", "maker"]):
        _entries(model, state, books, cfg)
    _maker_honest(state, books, cfg)          # honest-fill maker book (Phase A)


def _entries(model, state, books, cfg):
    bookrec = state[model]
    held = {p["id"] for p in bookrec["open"]}
    today = now_iso()[:10]
    # count today's completed round-trips per market (the "7 times" cap)
    today_count = {}
    for r in bookrec["closed"]:
        if r.get("closed_at", "")[:10] == today:
            today_count[r["id"]] = today_count.get(r["id"], 0) + 1
    for cid, (m, bk) in books.items():
        if len(bookrec["open"]) >= cfg["max_open"]:
            break
        if cid in held:
            continue
        if today_count.get(cid, 0) >= cfg["max_trades_per_market_day"]:
            continue   # rested this market for the day after 7 round-trips
        if bk["spread"] > cfg["wl_max_spread"]:
            continue
        if cfg.get("mode") == "midpoint":
            if bk["mid"] > cfg["midpoint_buy"]:
                continue                     # only buy at/under the midpoint
        else:
            ref = state["refs"].get(cid, [])
            if len(ref) < cfg["ref_window"]:
                continue                     # need history to define a 'dip'
            mean = sum(ref) / len(ref)
            if bk["mid"] > mean - cfg["entry_edge"]:
                continue                     # not a dip
        fill = bk["ask"] if model == "taker" else bk["bid"]
        size = round(cfg["bet_usdc"] / fill, 4) if fill > 0 else 0
        if size <= 0:
            continue
        bookrec["open"].append({
            "id": cid, "q": m["q"], "model": model,
            "entry_mid": round(bk["mid"], 4), "entry_fill": round(fill, 4),
            "size": size, "opened_at": now_iso(),
        })


def _exits(model, state, books, cfg):
    bookrec = state[model]
    keep = []
    for p in bookrec["open"]:
        rec = books.get(p["id"])
        if rec is None:
            if secs_since(p["opened_at"]) > cfg["max_hold_sec"]:
                _close(bookrec, p, p["entry_fill"], "stale", cfg)
            else:
                keep.append(p)
            continue
        _, bk = rec
        reason = None
        if cfg.get("mode") == "midpoint":
            if bk["mid"] >= cfg["sell_target"]:
                reason = "target"
            elif bk["mid"] <= cfg["midpoint_stop"]:
                reason = "stop"
            elif secs_since(p["opened_at"]) > cfg["max_hold_sec"]:
                reason = "time"
        else:
            move = bk["mid"] - p["entry_mid"]
            if move >= cfg["target"]:
                reason = "target"
            elif move <= -cfg["stop"]:
                reason = "stop"
            elif secs_since(p["opened_at"]) > cfg["max_hold_sec"]:
                reason = "time"
        if reason:
            fill = bk["bid"] if model == "taker" else bk["ask"]
            _close(bookrec, p, fill, reason, cfg)
        else:
            keep.append(p)
    bookrec["open"] = keep


def _maker_honest(state, books, cfg):
    """HONEST maker book (Phase A). Resting limit orders that fill ONLY when the
    market trades through them; entry quotes cancel on timeout; exit quotes fall
    back to a taker fill (booking the spread cost) if not hit. Lifecycle status:
    quoting_entry -> open -> quoting_exit -> closed. Quotes that never fill are
    dropped with NO trade booked (that non-fill IS the honest cost of being a maker)."""
    rec_book = state.setdefault("makerH", {"open": [], "closed": []})
    qt = cfg.get("maker_quote_timeout_sec", 90)
    xw = cfg.get("maker_exit_window_sec", 90)
    today = now_iso()[:10]
    tcount = {}
    for r in rec_book["closed"]:
        if r.get("closed_at", "")[:10] == today:
            tcount[r["id"]] = tcount.get(r["id"], 0) + 1
    held = {p["id"] for p in rec_book["open"]}
    keep = []
    for p in rec_book["open"]:
        rec = books.get(p["id"])
        if rec is None:  # market left the watchlist
            if p.get("status") == "open" and secs_since(p.get("opened_at", now_iso())) > cfg["max_hold_sec"]:
                _close(rec_book, p, p["entry_fill"], "stale", cfg)
            elif p.get("status") in ("quoting_entry", "quoting_exit"):
                if p.get("status") == "quoting_exit" and secs_since(p["exit_placed_at"]) > xw:
                    _close(rec_book, p, p["entry_fill"], "stale", cfg)  # can't fill, unwind flat
                # quoting_entry with no book -> just cancel (drop)
            else:
                keep.append(p)
            continue
        _, bk = rec
        st = p.get("status")
        if st == "quoting_entry":
            if bk["ask"] <= p["limit"] + 1e-9:                 # someone crossed down to our bid -> filled
                p["status"] = "open"; p["entry_fill"] = round(p["limit"], 4); p["opened_at"] = now_iso()
                keep.append(p)
            elif secs_since(p["placed_at"]) > qt:              # cancel stale quote, NO trade
                pass
            else:
                keep.append(p)
        elif st == "quoting_exit":
            if bk["bid"] >= p["exit_limit"] - 1e-9:            # someone crossed up to our ask -> exit filled (maker)
                _close(rec_book, p, round(p["exit_limit"], 4), "target", cfg)
            elif secs_since(p["exit_placed_at"]) > xw:         # fallback: take the bid, book the spread cost
                _close(rec_book, p, round(bk["bid"], 4), "target_takerfallback", cfg)
            else:
                keep.append(p)
        elif st == "open":
            reason = None
            if cfg.get("mode") == "midpoint":
                if bk["mid"] >= cfg["sell_target"]: reason = "target"
                elif bk["mid"] <= cfg["midpoint_stop"]: reason = "stop"
                elif secs_since(p["opened_at"]) > cfg["max_hold_sec"]: reason = "time"
            else:
                move = bk["mid"] - p["entry_mid"]
                if move >= cfg["target"]: reason = "target"
                elif move <= -cfg["stop"]: reason = "stop"
                elif secs_since(p["opened_at"]) > cfg["max_hold_sec"]: reason = "time"
            if reason == "target":                              # post a maker sell quote at the ask
                p["status"] = "quoting_exit"; p["exit_limit"] = round(bk["ask"], 4); p["exit_placed_at"] = now_iso()
                keep.append(p)
            elif reason in ("stop", "time"):                    # bail immediately as taker (hit the bid)
                _close(rec_book, p, round(bk["bid"], 4), reason, cfg)
            else:
                keep.append(p)
        else:
            keep.append(p)
    rec_book["open"] = keep
    # NEW entry quotes
    if cfg.get("makerh_enabled", True):
        for cid, (m, bk) in books.items():
            if len(rec_book["open"]) >= cfg["max_open"]:
                break
            if cid in held or tcount.get(cid, 0) >= cfg["max_trades_per_market_day"]:
                continue
            if bk["spread"] > cfg["wl_max_spread"]:
                continue
            if cfg.get("mode") == "midpoint":
                if bk["mid"] > cfg["midpoint_buy"]:
                    continue
            else:
                ref = state.get("refs", {}).get(cid, [])
                if len(ref) < cfg["ref_window"] or bk["mid"] > sum(ref) / len(ref) - cfg["entry_edge"]:
                    continue
            if bk["bid"] <= 0:
                continue
            rec_book["open"].append({
                "id": cid, "q": m["q"], "model": "makerH", "status": "quoting_entry",
                "limit": round(bk["bid"], 4), "entry_mid": round(bk["mid"], 4),
                "size": round(cfg["bet_usdc"] / bk["bid"], 4), "placed_at": now_iso(),
            })


# ── Wallet-follow scalper ─────────────────────────────────────────────────────
# Parse pattern: "🔥 ELITE OPEN │ 0xABCD… (rank 0.03) → YES on "Question" size=1234"
_WF_PAT = re.compile(
    r'ELITE OPEN.*?(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*?'
    r'rank\s+([\d.]+)\).*?→\s+(\w+)\s+on\s+"([^"]+)"',
    re.IGNORECASE
)
# Simpler pattern without needing log timestamp in the line
_WF_LINE = re.compile(
    r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+.*?ELITE OPEN.*?rank\s+([\d.]+)\).*?→\s+(\w+)\s+on\s+"([^"]+)"'
)


def _parse_elite_opens(window_sec=90):
    """Read wf_signals.json (written by the primary bot each scan).
    Falls back to parsing the log file if the signals file doesn't exist yet."""
    WF_SIG = os.path.join(BASE, "wf_signals.json")
    signals = []

    # ── Primary source: structured signal file (has conditionId) ──────────────
    if os.path.exists(WF_SIG):
        try:
            raw = json.load(open(WF_SIG))
            now_utc = datetime.now(timezone.utc)
            for s in raw:
                try:
                    ts = datetime.fromisoformat(s["ts"].replace("Z", "+00:00"))
                    age = (now_utc - ts).total_seconds()
                    if age > window_sec:
                        continue
                    signals.append({
                        "cid":     s.get("cid"),
                        "title":   s.get("title", ""),
                        "outcome": s.get("outcome", "").upper(),
                        "rank":    float(s.get("rank", 1.0)),
                        "age_sec": round(age, 1),
                    })
                except Exception:
                    continue
            if signals:
                return signals
        except Exception:
            pass

    # ── Fallback: parse bot log lines (no conditionId) ────────────────────────
    try:
        with open(BOT_LOG, "r") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 50_000))
            lines = f.read().splitlines()
    except Exception:
        return signals

    now = datetime.now()  # local time — bot logs are in local (IST) timezone
    for line in lines:
        m = _WF_LINE.search(line)
        if not m:
            continue
        ts_str, rank_str, outcome, title = m.groups()
        try:
            rank = float(rank_str)
            ts = datetime.fromisoformat(ts_str)
            age = (now - ts).total_seconds()
            if age > window_sec:
                continue
            signals.append({
                "cid": None,  # unknown from log
                "title": title, "rank": rank,
                "outcome": outcome.upper(), "age_sec": round(age, 1),
            })
        except Exception:
            continue
    return signals


def _clob_market_tokens(cid, outcome=None):
    """Get token ID and current mid for a conditionId + outcome via Gamma.
    Returns {"token": ..., "mid": ..., "yes_mid": ...} or None."""
    try:
        result = http_get(GAMMA, {"conditionId": cid, "limit": 1}) or []
        if not result:
            return None
        m = result[0]
        toks = m.get("clobTokenIds")
        if isinstance(toks, str):
            toks = json.loads(toks)
        prices = m.get("outcomePrices")
        if isinstance(prices, str):
            prices = json.loads(prices)
        outcomes = m.get("outcomes")
        if isinstance(outcomes, str):
            outcomes = json.loads(outcomes)
        if not toks or not prices:
            return None

        yes_mid = float(prices[0])

        # Match outcome to the right token index
        idx = 0  # default: first outcome (usually YES or team[0])
        if outcome and outcomes:
            ou = outcome.upper()
            for i, o in enumerate(outcomes):
                if o.upper() == ou:
                    idx = i
                    break

        token = toks[idx] if idx < len(toks) else toks[0]
        mid   = float(prices[idx]) if idx < len(prices) else yes_mid

        return {
            "token":    token,
            "mid":      mid,
            "yes_mid":  yes_mid,
        }
    except Exception:
        pass
    return None


def _wf_log(msg):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    try:
        with open(WF_LOG, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def wallet_follow_poll(state, cfg):
    """Wallet-follow scalper poll. Reads ELITE OPEN signals, enters fast, exits fast."""
    if cfg.get("mode") != "wallet_follow":
        return

    wf = state.setdefault("wallet_follow", {"open": [], "closed": []})
    cooldowns = state.setdefault("wf_cooldowns", {})   # cid → last_closed_ts

    now_ts = time.time()
    max_open   = cfg.get("wf_max_open", 5)
    target     = cfg.get("wf_target", 0.025)
    stop_      = cfg.get("wf_stop", 0.020)
    max_hold   = cfg.get("wf_max_hold_sec", 300)
    bet        = cfg.get("wf_bet_usdc", 10.0)
    cooldown   = cfg.get("wf_cooldown_sec", 180)
    min_rank   = cfg.get("wf_min_rank", 0.00)
    max_rank   = cfg.get("wf_max_rank", 0.10)
    min_yes    = cfg.get("wf_min_yes_price", 0.25)
    max_yes    = cfg.get("wf_max_yes_price", 0.65)
    window     = cfg.get("wf_signal_window_sec", 90)

    # ── 1. EXITS — re-price all open positions ────────────────────────────────
    keep = []
    for p in wf["open"]:
        token = p["token"]
        bk = book(token)
        age = now_ts - p["opened_ts"]
        if bk is None:
            if age > max_hold:
                # Can't re-price, close at entry (conservative)
                _wf_close(wf, p, p["entry_fill"], "stale_noprice")
            else:
                keep.append(p)
            continue

        mid_now = bk["mid"]
        move = mid_now - p["entry_mid"]
        reason = None
        if move >= target:
            reason = "target"
        elif move <= -stop_:
            reason = "stop"
        elif age > max_hold:
            reason = "time"

        if reason:
            exit_fill = bk["bid"]   # taker exit (realistic)
            _wf_close(wf, p, exit_fill, reason)
            cooldowns[p["cid"]] = now_ts
            _wf_log(f"EXIT [{reason}] {p['title'][:40]} | entry={p['entry_fill']:.3f} exit={exit_fill:.3f} "
                    f"pnl=${(exit_fill - p['entry_fill']) * p['size']:+.3f} age={age:.0f}s")
        else:
            keep.append(p)
    wf["open"] = keep

    # ── 2. ENTRIES — read fresh elite signals ─────────────────────────────────
    if len(wf["open"]) >= max_open:
        return

    signals = _parse_elite_opens(window_sec=window)
    held_cids = {p["cid"] for p in wf["open"]}

    for sig in signals:
        if len(wf["open"]) >= max_open:
            break
        rank = sig["rank"]
        if not (min_rank <= rank <= max_rank):
            continue

        cid = sig.get("cid")
        if not cid:
            continue   # need conditionId to look up tokens (log fallback has no cid)

        # Skip if already held or on cooldown
        if cid in held_cids:
            continue
        last_close = cooldowns.get(cid, 0)
        if now_ts - last_close < cooldown:
            continue

        outcome   = sig["outcome"]
        token_id  = sig.get("token_id")   # direct from bot signal (preferred)

        if token_id:
            # Fast path: token_id known, just price it via CLOB book
            token = token_id
            bk_pre = book(token)
            if bk_pre is None:
                continue
            entry_mid = bk_pre["mid"]
        else:
            # Slow path: look up via Gamma (less reliable)
            mkt = _clob_market_tokens(cid, outcome=outcome)
            if mkt is None:
                continue
            entry_mid = mkt["mid"]
            token     = mkt["token"]

        # Price filter on the outcome token mid
        if not (min_yes <= entry_mid <= max_yes):
            continue

        bk = book(token) if not token_id else bk_pre
        if bk is None or bk["spread"] > 0.04:
            continue   # too wide to scalp

        entry_fill = bk["ask"]   # taker entry (realistic)
        if entry_fill <= 0:
            continue

        size = round(bet / entry_fill, 4)
        pos = {
            "cid": cid, "token": token, "title": sig["title"][:60],
            "outcome": outcome, "rank": rank,
            "entry_mid": round(entry_mid, 4), "entry_fill": round(entry_fill, 4),
            "size": size, "opened_ts": now_ts, "opened_at": now_iso(),
        }
        wf["open"].append(pos)
        held_cids.add(cid)
        _wf_log(f"ENTER {outcome} | rank={rank:.2f} | {sig['title'][:45]} "
                f"| fill={entry_fill:.3f} age={sig['age_sec']:.0f}s signal")


def _wf_close(wf, p, exit_fill, reason):
    pnl = round((exit_fill - p["entry_fill"]) * p["size"], 4)
    rec = dict(p)
    rec.update({"exit_fill": round(exit_fill, 4), "reason": reason,
                "pnl_usdc": pnl, "closed_at": now_iso()})
    wf["closed"].append(rec)


def _wf_board(state):
    wf = state.get("wallet_follow", {"open": [], "closed": []})
    c = wf["closed"]
    opens = wf["open"]
    n = len(c)
    w = sum(1 for r in c if r.get("pnl_usdc", 0) > 0)
    pnl = sum(r.get("pnl_usdc", 0) for r in c)
    wr = (w / n * 100) if n else 0
    avg = pnl / n if n else 0
    print(f"\n  [WALLET_FOLLOW] (taker fill — realistic scalp)")
    print(f"     open={len(opens)}  closed={n}  win={wr:.1f}%  "
          f"paper P&L=${pnl:+.3f}  avg=${avg:+.4f}/trade")
    for p in opens:
        age = time.time() - p.get("opened_ts", time.time())
        print(f"     OPEN: {p['title'][:40]} | {p['outcome']} | "
              f"fill={p['entry_fill']:.3f} | age={age:.0f}s")


def _close(bookrec, p, exit_fill, reason, cfg):
    pnl = round((exit_fill - p["entry_fill"]) * p["size"], 4)
    rec = dict(p)
    rec.update({"exit_fill": round(exit_fill, 4), "reason": reason,
                "pnl_usdc": pnl, "closed_at": now_iso()})
    bookrec["closed"].append(rec)


# ── Reporting ────────────────────────────────────────────────────────────────
def board(state):
    print("=" * 58)
    print("  SCALP ENGINE — real scalping, two fill models (paper)")
    print("=" * 58)
    _wf_board(state)
    notes = {"taker": "(honest taker)", "maker": "(optimistic upper-bound)",
             "makerH": "(HONEST maker — fills only on through-trade)"}
    for model in ("taker", "maker", "makerH"):
        bookm = state.get(model)
        if not bookm:
            continue
        c = bookm["closed"]
        opens = bookm["open"]
        n = len(c)
        w = sum(1 for r in c if r["pnl_usdc"] > 0)
        pnl = sum(r["pnl_usdc"] for r in c)
        wr = (w / n * 100) if n else 0
        print(f"\n  [{model.upper()}] {notes.get(model, '')}")
        if model == "makerH":
            quoting = sum(1 for p in opens if p.get("status", "").startswith("quoting"))
            filled_open = sum(1 for p in opens if p.get("status") == "open")
            tf = sum(1 for r in c if r.get("reason") == "target_takerfallback")
            print(f"     quoting={quoting}  filled-open={filled_open}  closed={n}  win={wr:.1f}%  paper P&L=${pnl:+.3f}")
            print(f"     (taker-fallback exits: {tf}/{n} — when the sell quote didn't get hit)")
        else:
            print(f"     open={len(opens)}  closed={n}  win={wr:.1f}%  paper P&L=${pnl:+.3f}")
    print()


# ── Loop ──────────────────────────────────────────────────────────────────
def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "once"
    if cmd == "board":
        board(load_state()); return
    if cmd == "once":
        state = load_state()
        for i in range(CONFIG["subpolls_per_once"]):
            poll_once(state, CONFIG)
            save_state(state)
            if i < CONFIG["subpolls_per_once"] - 1:
                time.sleep(CONFIG["poll_sec"])
        board(load_state()); return
    if cmd == "run":
        print("scalp_engine running (paper, real scalping). Ctrl-C to stop.")
        state = load_state()
        while True:
            try:
                poll_once(state, CONFIG)
                save_state(state)
            except Exception as e:
                sys.stderr.write(f"[loop] {e}\n")
            time.sleep(CONFIG["poll_sec"])
    print(__doc__)


if __name__ == "__main__":
    main()
