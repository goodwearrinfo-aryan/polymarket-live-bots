#!/usr/bin/env python3
"""
brainbot.py — the distilled bot. Everything 90 dead legs taught us, nothing else.

PAPER ONLY. Own state file (brainbot_state.json) — never touches scalp_lab's
Postgres or mirror, so it cannot race the watchdog.

Trades exactly THREE structures, each one earned its place:
  1. nearres   — esports favorites <4h to resolution, side-mid [0.88, 0.95],
                 ride to settlement with a 3c stop (exit-policy sweep, n=110 OOS).
  2. ladderarb — date-ladder monotonicity violation: later deadline priced BELOW
                 an earlier one → buy the later YES (mechanical mispricing).
  3. insider   — mirror trades >= $1k by vetted SHARP wallets from
                 insider_wallets.json (NOT raw whale-follow, which died at 44%
                 WR n=136 — these wallets are P&L-vetted first).

Baked-in lessons (the tuition was ~$200 paper):
  L3  resolution pressure mandatory       L4  entry cap 0.95 (no WR padding)
  L5  live-match gaps / padded endDates   L6  double-negative questions blocked
  L8  R:R is structurally capped at favorite prices — ride w/ stop, no targets
  L9  inverting dead legs is not an edge  L10 off-thesis → quarantine, not delete

Run:  python3 brainbot.py once      (cron/watchdog-friendly)
      python3 brainbot.py report
"""

import json, os, re, sys, time, urllib.parse, urllib.request
from datetime import datetime, timezone

BASE     = os.path.dirname(os.path.abspath(__file__))
STATE_F  = os.path.join(BASE, "brainbot_state.json")
INSIDERS = os.path.join(BASE, "insider_wallets.json")
GAMMA    = "https://gamma-api.polymarket.com/markets"
DATA_API = "https://data-api.polymarket.com"
UA       = "Mozilla/5.0"

CFG = {
    "bet_usdc":            1.0,
    "spread_cost":         0.01,   # honest fill: pay half-spread each way
    "max_open_per_leg":    8,
    "cooldown_hours":      12,
    # nearres (lead candidate, exit-policy era)
    "nr_band_lo":          0.88,   # nearreslow OOS-killed the floor below this
    "nr_band_hi":          0.95,   # L4: above this, payoff can't pay for the tail
    "nr_max_hours":        4,
    "nr_min_vol":          25_000,
    "nr_stop":             0.03,   # ride to settlement WITH stop = +4.7c/trade OOS
    # ladderarb
    "la_gap_min":          0.04,
    "la_min_vol":          200_000,
    "la_gain":             0.06, "la_stop": 0.04, "la_max_hold_h": 96,
    # insider mirror
    "in_min_trade_usd":    1_000,
    "in_max_age_min":      30,
    "in_gain":             0.06, "in_stop": 0.04, "in_max_hold_h": 48,
}

ESPORTS_KW = ("counter-strike", "cs2", "valorant", "league of legends", "lol:",
              "dota", "overwatch", "rainbow six", "iem ", "pgl major",
              "blast premier", "vct ", "lck ", "lpl ", "esport")

# coinflip mill: short-dated crypto Up/Down series (coinup/coindown territory) —
# calibrated 5-min markets that resolve before any copy can land, so mirroring an
# insider's coinflip trade is pure noise. Detection mirrors newmarket_logger.MILL_*.
_MILL_Q_HINTS    = ("up or down",)
_MILL_SLUG_HINTS = ("updown", "up-or-down", "-up-down-", "hourly")

def is_coinflip_mill(title, slug=""):
    t = (title or "").lower(); s = (slug or "").lower()
    return any(h in t for h in _MILL_Q_HINTS) or any(h in s for h in _MILL_SLUG_HINTS)


# ── plumbing ─────────────────────────────────────────────────────────────────
def _get(url, params=None):
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def hours_since(iso):
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - dt).total_seconds() / 3600
    except Exception:
        return 1e9

def days_to_end(end_iso):
    try:
        dt = datetime.fromisoformat(end_iso.replace("Z", "+00:00"))
        return (dt - datetime.now(timezone.utc)).total_seconds() / 86400
    except Exception:
        return None

def load_state():
    if os.path.exists(STATE_F):
        return json.load(open(STATE_F))
    return {leg: {"open": [], "closed": []} for leg in ("nearres", "ladderarb", "insider")}

def save_state(s):
    tmp = STATE_F + ".tmp"
    json.dump(s, open(tmp, "w"), indent=1)
    os.replace(tmp, STATE_F)        # atomic — a crash never half-writes state


# ── market feed ──────────────────────────────────────────────────────────────
def fetch_markets(pages=8):
    out, seen = [], set()
    for p in range(pages):
        try:
            raw = _get(GAMMA, {"closed": "false", "active": "true",
                               "order": "volumeNum", "ascending": "false",
                               "limit": 100, "offset": p * 100}) or []
        except Exception as e:
            sys.stderr.write(f"[feed] page {p}: {e}\n")
            break
        for m in raw:
            try:
                cid = str(m.get("conditionId") or m.get("id"))
                if cid in seen:
                    continue
                prices = m.get("outcomePrices")
                if isinstance(prices, str):
                    prices = json.loads(prices)
                if not prices or len(prices) < 2:
                    continue
                seen.add(cid)
                out.append({"id": cid, "q": (m.get("question") or "")[:90],
                            "yes": float(prices[0]),
                            "vol": float(m.get("volumeNum") or m.get("volume") or 0),
                            "end": m.get("endDate"),
                            "events": m.get("events")})
            except Exception:
                continue
        if len(raw) < 100:
            break
    return out

def fetch_yes_price(cid):
    """Price one held market directly — resolved markets vanish from the
    top-volume feed (Arch #3); without this, exits censor as stale_nodata."""
    try:
        raw = _get(GAMMA, {"condition_ids": cid})
        if raw:
            prices = raw[0].get("outcomePrices")
            if isinstance(prices, str):
                prices = json.loads(prices)
            if prices:
                return float(prices[0])
    except Exception:
        pass
    return None


# ── guards (Lessons 5 & 6) ──────────────────────────────────────────────────
def is_live_match(q):
    ql = q.lower()
    return bool(re.search(r"\bvs\.?\b", ql)) or "o/u" in ql or "spread:" in ql

def is_double_negative(q):
    return q.lower().startswith("will no ")

def is_esports(q):
    ql = q.lower()
    return any(k in ql for k in ESPORTS_KW)


# ── entries ──────────────────────────────────────────────────────────────────
def _own_and_cooldown(book, cooldown_h):
    own = {p["id"] for p in book["open"]}
    cd  = {r["id"] for r in book["closed"] if hours_since(r.get("closed_at", "")) < cooldown_h}
    return own, cd

def _open(book, leg, mid, q, side, out_mid, note=""):
    entry = round(out_mid + CFG["spread_cost"], 4)        # taker fill, honest
    if entry <= 0 or entry >= 1:
        return
    book["open"].append({"id": mid, "q": q, "leg": leg, "side": side,
                         "entry_mid": round(out_mid, 4), "entry_fill": entry,
                         "size": round(CFG["bet_usdc"] / entry, 4),
                         "opened_at": now_iso(), "note": note})
    print(f"  [OPEN {leg}] {side} @ {entry:.3f}  {q[:55]}  {note}")

def scan_nearres(state, markets):
    book = state["nearres"]
    own, cd = _own_and_cooldown(book, CFG["cooldown_hours"])
    for m in markets:
        if len(book["open"]) >= CFG["max_open_per_leg"]:
            break
        if m["id"] in own or m["id"] in cd:
            continue
        if not is_esports(m["q"]) or is_double_negative(m["q"]):
            continue
        if m["vol"] < CFG["nr_min_vol"]:
            continue
        d = days_to_end(m["end"])
        if d is None or d <= 0 or d > CFG["nr_max_hours"] / 24:
            continue
        yes = m["yes"]
        side = "YES" if yes >= CFG["nr_band_lo"] else ("NO" if yes <= 1 - CFG["nr_band_lo"] else None)
        if side is None:
            continue
        out_mid = yes if side == "YES" else 1 - yes
        if not (CFG["nr_band_lo"] <= out_mid <= CFG["nr_band_hi"]):   # L4 cap
            continue
        _open(book, "nearres", m["id"], m["q"], side, out_mid)

_MONTHS = {m: i + 1 for i, m in enumerate(
    ("january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"))}

def _deadline_from_question(q):
    """Parse the 'by/before <date>' tail into a sortable (year, month, day).
    Returns None for non-date tails ('before GTA VI') — those rungs are
    UNORDERABLE and must be skipped. Never trust endDate here (L5: padded)."""
    mt = re.search(r"\s(?:by|before)\s+([^?]{1,30})\?$", q.lower())
    if not mt:
        return None
    tail = mt.group(1).strip()
    yr = re.search(r"\b(20\d\d)\b", tail)
    mo = next((v for k, v in _MONTHS.items() if k in tail), None)
    dy = re.search(r"\b([0-3]?\d)\b(?!\d)", re.sub(r"20\d\d", "", tail))
    if not yr and not mo:
        return None                       # no recognizable date at all
    year  = int(yr.group(1)) if yr else datetime.now(timezone.utc).year
    month = mo if mo else 12              # bare year = end of that year
    day   = int(dy.group(1)) if (dy and mo) else 28
    return (year, month, min(day, 31))

def scan_ladderarb(state, markets):
    book = state["ladderarb"]
    own, cd = _own_and_cooldown(book, CFG["cooldown_hours"])
    # group date-ladder variants: question stripped of any "by <date>" tail
    groups = {}
    for m in markets:
        # strip only a "by/before <date>" TAIL — never a mid-question "on"
        # (L7: "US strike on Mexico by Dec 31" must not group as "US strike")
        key = re.sub(r"\s(by|before)\s[^?]{0,30}\?$", "", m["q"].lower()).strip()
        if key == m["q"].lower().strip():
            continue                     # no date tail → not a ladder rung
        dl = _deadline_from_question(m["q"])
        if dl is None:
            continue                     # 'before GTA VI' — unorderable, skip
        m["_deadline"] = dl
        groups.setdefault(key, []).append(m)
    for rungs in groups.values():
        if len(book["open"]) >= CFG["max_open_per_leg"]:
            break
        if len(rungs) < 2:
            continue
        rungs.sort(key=lambda r: r["_deadline"])   # question date, NOT endDate
        for early, late in zip(rungs, rungs[1:]):
            if early["yes"] - late["yes"] <= CFG["la_gap_min"]:
                continue
            if late["id"] in own or late["id"] in cd:
                continue
            if min(early["vol"], late["vol"]) < CFG["la_min_vol"]:
                continue
            _open(book, "ladderarb", late["id"], late["q"], "YES", late["yes"],
                  note=f"gap {early['yes'] - late['yes']:.2f} vs '{early['q'][:40]}'")

def scan_insider(state):
    return  # DISABLED 2026-06-15 — copy-trading loses OOS (-$14.16, 42%% WR, n=134); +0.29 backtest was survivorship artifact. Alerts-only; scan_exits still rides open positions.
    book = state["insider"]
    own, cd = _own_and_cooldown(book, CFG["cooldown_hours"])
    try:
        wallets = json.load(open(INSIDERS))
    except Exception:
        return
    now = time.time()
    for addr, label in wallets.items():
        if "|bot" in (label or "").lower():
            continue                       # tagged bot (coinflip-HFT etc.) — not copyable info
        if len(book["open"]) >= CFG["max_open_per_leg"]:
            break
        try:
            trades = _get(f"{DATA_API}/trades", {"user": addr, "limit": 20})
        except Exception:
            continue
        for t in trades:
            if len(book["open"]) >= CFG["max_open_per_leg"]:
                break
            cid = str(t.get("conditionId"))
            if cid in own or cid in cd:
                continue
            if t.get("side") != "BUY":
                continue
            usd = float(t.get("size", 0)) * float(t.get("price", 0))
            if usd < CFG["in_min_trade_usd"]:
                continue
            if (now - int(t.get("timestamp", 0))) / 60 > CFG["in_max_age_min"]:
                continue
            q = t.get("title", "")
            if is_double_negative(q):
                continue
            if is_coinflip_mill(q, t.get("slug", "")):
                continue                   # 5-min crypto Up/Down — resolves before copy lands
            price = float(t.get("price", 0))
            if not (0.10 <= price <= 0.90):
                continue
            outcome = t.get("outcome", "")
            side = "YES" if t.get("outcomeIndex") in (0, "0") else "NO"
            _open(book, "insider", cid, q, side, price,
                  note=f"{label} ${usd:,.0f} {outcome}")
            own.add(cid)


# ── exits ────────────────────────────────────────────────────────────────────
def scan_exits(state, markets):
    feed = {m["id"]: m["yes"] for m in markets}
    rules = {
        "nearres":   {"stop": CFG["nr_stop"], "gain": None,         "max_h": None},  # ride to settlement
        "ladderarb": {"stop": CFG["la_stop"], "gain": CFG["la_gain"], "max_h": CFG["la_max_hold_h"]},
        "insider":   {"stop": CFG["in_stop"], "gain": CFG["in_gain"], "max_h": CFG["in_max_hold_h"]},
    }
    for leg, rule in rules.items():
        book = state[leg]
        still = []
        for p in book["open"]:
            yes = feed.get(p["id"])
            if yes is None:
                yes = fetch_yes_price(p["id"])      # CLOB-style fallback, no censoring
            if yes is None:
                still.append(p)                     # unpriceable this cycle — hold
                continue
            cur  = yes if p["side"] == "YES" else 1 - yes
            chg  = cur - p["entry_fill"]
            held = hours_since(p["opened_at"])
            reason = None
            if cur <= 0.02 or cur >= 0.98:
                reason = "resolved"
            elif chg <= -rule["stop"]:
                reason = "stop"
            elif rule["gain"] and chg >= rule["gain"]:
                reason = "target"; cur = p["entry_fill"] + rule["gain"]   # L1: cap at limit fill
            elif rule["max_h"] and held > rule["max_h"]:
                reason = "time"
            if reason:
                exit_px = round(cur - CFG["spread_cost"], 4)
                pnl = round((exit_px - p["entry_fill"]) * p["size"], 4)
                p.update({"closed_at": now_iso(), "exit_px": exit_px,
                          "exit": reason, "pnl": pnl})
                book["closed"].append(p)
                print(f"  [EXIT {leg}/{reason}] {'+' if pnl >= 0 else ''}{pnl:.2f}  {p['q'][:50]}")
            else:
                still.append(p)
        book["open"] = still


# ── report ───────────────────────────────────────────────────────────────────
def report(state):
    total = 0.0
    print(f"\n  BRAINBOT — {now_iso()[:16]}")
    print(f"  {'leg':<10} {'open':>4} {'closed':>6} {'WR':>6} {'P&L':>9}")
    for leg in ("nearres", "ladderarb", "insider"):
        b = state[leg]
        closed = b["closed"]
        wins = sum(1 for r in closed if r["pnl"] > 0)
        pnl  = sum(r["pnl"] for r in closed)
        wr   = f"{wins / len(closed) * 100:.0f}%" if closed else "—"
        total += pnl
        print(f"  {leg:<10} {len(b['open']):>4} {len(closed):>6} {wr:>6} {pnl:>+9.2f}")
    print(f"  {'TOTAL':<10} {'':>4} {'':>6} {'':>6} {total:>+9.2f}\n")


def once():
    state = load_state()
    markets = fetch_markets()
    if not markets:
        sys.stderr.write("[brainbot] empty feed — no trades this cycle (fail-safe)\n")
        return
    print(f"[brainbot] {len(markets)} markets")
    scan_exits(state, markets)
    scan_nearres(state, markets)
    scan_ladderarb(state, markets)
    scan_insider(state)
    save_state(state)
    report(state)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "once"
    if cmd == "report":
        report(load_state())
    else:
        once()
