#!/usr/bin/env python3
"""whalecopy_probe.py — GATED PAPER LEG for the single-whale copy edge (PAPER, read-only).

The 2026-06-15 measurement chain (whale_drift_backtest / _spread_feasibility /
_lagimpact) found a THIN positive: +2.8c raw whale edge − 0.5c spread − 1.36c lag
= ~+0.9c/share, but ON the noise floor (CI includes 0). The ONLY honest resolution
is a forward paper test to >=30 real exits with bootstrap CI>0 and a losing control.
This is that test. It does NOT place real orders.

Mechanism (per cycle, `once`):
  - whitelist = ACTIVE sharp whales (insider_wallets WHALE-* + whale_candidates_active).
  - pull each whale's fresh BUYs (<= LOOKBACK_M min old, >= MIN_USD) so the copy is
    PROMPT (matches the 60-900s lag we measured, not a 6h-stale follow).
  - GATE: slow market (endDate >= SLOW_MIN_DAYS), copyable band [0.15,0.85],
    live CLOB book half-spread <= MAX_HALF_SPREAD (1c — the validated threshold).
  - OPEN a paper copy at the live ASK (mid + half-spread = what a copier really pays),
    PLUS a control on the RANDOM side of the same market (must lose).
  - hold to RESOLUTION; settle on-chain via ctf_resolution (gap-honest).
GRADUATION (not by this probe): >=30 closed exits AND bootstrap CI>0 AND beats the
random control AND DSR — only then does it earn a real config slot.

Usage: python3 whalecopy_probe.py once   |   report
"""
import os, sys, json, re, time, random, statistics, calendar, urllib.request, urllib.parse
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ctf_resolution

STATE = os.path.join(HERE, "whalecopy_state.json")
LOG = os.path.join(HERE, "whalecopy_log.jsonl")
VAULT = os.path.expanduser("~/Documents/PolymarketVault/Reports/whalecopy.md")
UA = {"User-Agent": "Mozilla/5.0"}

WR_MIN = 40              # active whales clear a lower bar (the edge is information, not WR)
MIN_USD = 1000          # min whale conviction to copy
LOOKBACK_M = 45         # only copy buys this fresh (prompt copy → realistic lag)
SLOW_MIN_DAYS = 5       # market resolves >= this far out (no imminent gap-through)
BAND_LO, BAND_HI = 0.15, 0.85
MAX_HALF_SPREAD = 0.01  # <=1c half-spread — the gate that makes the edge net-positive
BET_USDC = 1.0
MAX_OPEN = 60


def _get(url, timeout=15):
    try:
        return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout))
    except Exception:
        return None


def load_whitelist():
    wl = {}
    p = os.path.join(HERE, "insider_wallets.json")
    if os.path.exists(p):
        for addr, label in json.load(open(p)).items():
            if "WHALE" in (label or "").upper():
                wl[addr.lower()] = label
    p = os.path.join(HERE, "whale_candidates_active.json")
    if os.path.exists(p):
        try:
            for w in json.load(open(p)):
                wl.setdefault(w["addr"].lower(), f"whale-{w.get('win_rate','?')}pct")
        except Exception:
            pass
    return wl


def fresh_buys(addr):
    u = "https://data-api.polymarket.com/activity?" + urllib.parse.urlencode({"user": addr, "limit": 40})
    rows = _get(u) or []
    cut = time.time() - LOOKBACK_M * 60
    return [r for r in rows if r.get("type") == "TRADE" and r.get("side") == "BUY"
            and float(r.get("usdcSize") or 0) >= MIN_USD and int(r.get("timestamp") or 0) >= cut]


_mk = {}
def market_by_cid(cid):
    if cid in _mk:
        return _mk[cid]
    m = _get("https://gamma-api.polymarket.com/markets?" + urllib.parse.urlencode({"condition_ids": cid}))
    _mk[cid] = (m[0] if m else None)
    return _mk[cid]


def days_left(mk):
    end = mk.get("endDate") or mk.get("endDateIso")
    if not end:
        return None
    try:
        ts = calendar.timegm(time.strptime(end[:19], "%Y-%m-%dT%H:%M:%S"))
        return (ts - time.time()) / 86400
    except Exception:
        return None


def book_halfspread_and_ask(token):
    b = _get(f"https://clob.polymarket.com/book?token_id={token}")
    if not b:
        return None, None
    bids = b.get("bids") or []; asks = b.get("asks") or []
    if not bids or not asks:
        return None, None
    bb = max(float(x["price"]) for x in bids); ba = min(float(x["price"]) for x in asks)
    if ba <= bb:
        return None, None
    return (ba - bb) / 2.0, ba


def load_state():
    if os.path.exists(STATE):
        try:
            return json.load(open(STATE))
        except Exception:
            pass
    return {"open": {}, "rng": 0}


def save_state(st):
    tmp = STATE + ".tmp"; json.dump(st, open(tmp, "w")); os.replace(tmp, STATE)


def _log(rec):
    with open(LOG, "a") as f:
        f.write(json.dumps(rec) + "\n")


def detect(wl, st):
    rng = random.Random(st.get("rng", 0))
    opened = 0
    for addr in wl:
        if len(st["open"]) >= MAX_OPEN:
            break
        for r in fresh_buys(addr):
            cid = r.get("conditionId"); oi = r.get("outcomeIndex"); tok = r.get("asset")
            if cid is None or oi is None or not tok:
                continue
            key = f"{cid}|{oi}"
            if key in st["open"]:
                continue
            mk = market_by_cid(cid)
            if not mk:
                continue
            d = days_left(mk)
            if d is None or d < SLOW_MIN_DAYS:
                continue
            hs, ask = book_halfspread_and_ask(str(tok))
            if hs is None or hs > MAX_HALF_SPREAD:
                continue                                  # the gate: only tight-spread markets
            if not (BAND_LO <= ask <= BAND_HI):
                continue
            # OPEN the copy at the live ask (what a copier pays), + a random-side control
            ctrl_side = rng.random() < 0.5
            st["open"][key] = {
                "cid": cid, "oi": int(oi), "tok": str(tok), "entry": round(ask, 4),
                "half_spread": round(hs, 4), "title": (r.get("title") or "")[:80],
                "whale": addr, "whale_price": float(r.get("price") or 0),
                "t0": int(time.time()), "ctrl_oi": (1 - int(oi)) if ctrl_side else int(oi),
            }
            opened += 1
            _log({"ev": "open", **st["open"][key]})
    st["rng"] = rng.randint(0, 10**9)
    return opened


def revisit(st):
    closed = 0
    for key in list(st["open"].keys()):
        pos = st["open"][key]
        try:
            win = ctf_resolution.winning_outcome(pos["cid"])
        except Exception:
            win = None
        if win is None or win == -1:
            continue                                       # unresolved
        terminal = 1.0 if win == pos["oi"] else 0.0
        pnl = round((terminal - pos["entry"]) * (BET_USDC / pos["entry"]) if pos["entry"] else 0, 4)
        # control: bought the ctrl_oi at ITS OWN price. Opposite side ≈ 1-entry (YES+NO=1);
        # same side ≈ entry. Marking it at `entry` regardless flattered the control (hid
        # losses), defeating the "control must lose" check — price each side honestly.
        ce = pos["entry"] if pos["ctrl_oi"] == pos["oi"] else round(1.0 - pos["entry"], 4)
        cterm = 1.0 if win == pos["ctrl_oi"] else 0.0
        cpnl = round((cterm - ce) * (BET_USDC / ce), 4) if ce else 0
        _log({"ev": "close", "key": key, "title": pos["title"], "entry": pos["entry"],
              "terminal": terminal, "pnl": pnl, "ctrl_pnl": cpnl,
              "hold_h": round((time.time() - pos["t0"]) / 3600, 1)})
        del st["open"][key]
        closed += 1
    return closed


def boot_ci(xs, n=2000, seed=0):
    if len(xs) < 2:
        return (float("nan"), float("nan"))
    rnd = random.Random(seed); m = len(xs)
    means = sorted(sum(rnd.choice(xs) for _ in range(m)) / m for _ in range(n))
    return (means[int(0.025 * n)], means[int(0.975 * n)])


def summarize():
    pnls, ctrls = [], []
    if os.path.exists(LOG):
        for ln in open(LOG):
            try:
                r = json.loads(ln)
            except Exception:
                continue
            if r.get("ev") == "close":
                pnls.append(r["pnl"]); ctrls.append(r.get("ctrl_pnl", 0))
    st = load_state()
    out = {"closed": len(pnls), "open": len(st["open"])}
    if pnls:
        lo, hi = boot_ci(pnls)
        out.update(mean=round(statistics.mean(pnls), 4), ci_lo=round(lo, 4), ci_hi=round(hi, 4),
                   control=round(statistics.mean(ctrls), 4))
    return out


def export_obsidian(s):
    try:
        os.makedirs(os.path.dirname(VAULT), exist_ok=True)
        verdict = "accumulating (need n>=30)"
        if s["closed"] >= 30 and s.get("ci_lo", -1) > 0 and s.get("mean", 0) > s.get("control", 0):
            verdict = "✅ CI>0 at n>=30, beats control — GRADUATE to a real leg"
        elif s["closed"] >= 30:
            verdict = "❌ n>=30 but CI includes 0 / loses to control — edge not real"
        lines = ["# Whale-copy gated paper probe", "",
                 f"- closed exits: **{s['closed']}** (need ≥30) · open: {s['open']}",
                 f"- mean P&L/exit: **{s.get('mean','—')}**  95% CI [{s.get('ci_lo','—')}, {s.get('ci_hi','—')}]",
                 f"- random-side control: {s.get('control','—')} (must be < mean)",
                 f"- verdict: {verdict}", "",
                 "Tests the +0.9c single-whale copy edge (spread-gated ≤1c, slow band, held to resolution). PAPER only."]
        open(VAULT, "w").write("\n".join(lines))
    except Exception:
        pass


def gradecheck():
    """One-line machine verdict for the watchdog graduation watcher:
      STATUS|n|mean|ci_lo|ci_hi|control   STATUS in PENDING/GRADUATED/DEAD/INCONCLUSIVE
    GRADUATED = n>=30 AND ci_lo>0 AND mean>control (the only +EV claim this project allows).
    DEAD = n>=30 AND ci_hi<=0. INCONCLUSIVE = n>=30 but CI straddles 0 / loses to control."""
    s = summarize()
    n = s["closed"]; mean = s.get("mean"); lo = s.get("ci_lo"); hi = s.get("ci_hi"); ctrl = s.get("control")
    if n < 30 or mean is None:
        status = "PENDING"
    elif lo > 0 and mean > (ctrl if ctrl is not None else 0):
        status = "GRADUATED"
    elif hi is not None and hi <= 0:
        status = "DEAD"
    else:
        status = "INCONCLUSIVE"
    print(f"{status}|{n}|{mean}|{lo}|{hi}|{ctrl}")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "once"
    if cmd == "report":
        print(json.dumps(summarize(), indent=2)); return
    if cmd == "gradecheck":
        gradecheck(); return
    st = load_state()
    wl = load_whitelist()
    opened = detect(wl, st)
    closed = revisit(st)
    save_state(st)
    s = summarize()
    export_obsidian(s)
    print(f"whalecopy: whitelist={len(wl)} opened={opened} closed_now={closed} "
          f"total_closed={s['closed']} open={s['open']} mean={s.get('mean','—')} "
          f"CI=[{s.get('ci_lo','—')},{s.get('ci_hi','—')}] ctrl={s.get('control','—')}")


if __name__ == "__main__":
    main()
