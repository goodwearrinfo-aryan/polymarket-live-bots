#!/usr/bin/env python3
"""
market_paper.py — FORWARD paper-trading book for the index trend signal → Obsidian.

Each daily run: pull ~3mo daily closes (reuses market_history), compute the trend
signal per index, and run a paper long/flat book — long while "constructive"
(above 50d SMA AND +1m momentum), flat otherwise. Marks open positions to the
latest close; books a paper return on each flip. Tracks a BUY-HOLD control per
index (the signal must beat just holding, or the timing adds nothing).

HONESTY (project discipline — paper-only hard rule, controls-must-win, the Gate):
  - PAPER ONLY. No real orders, no funds. Indices aren't the bot's venue (Polymarket).
  - FORWARD, not backtest: the book starts today and accumulates 1 obs/index/day, so
    the ≥30-closed-trade gate is months away — by design (an in-sample 3mo momentum
    backtest is the strategy-graveyard fiction; we don't claim edge from one).
  - GATE before any "edge": n≥30 closed AND signal beats buy-hold AND (later) bootstrap
    CI>0. Until then this is ACCUMULATING, not validated.
launchd-scheduled; logs to /tmp (exit-78 lesson).
"""
import json, os, sys, statistics
from datetime import datetime, timezone

POLY  = os.path.dirname(os.path.abspath(__file__))
VAULT = os.path.join(os.path.expanduser("~"), "Documents/PolymarketVault")
STATE = os.path.join(POLY, "market_paper_state.json")
LOG   = os.path.join(POLY, "market_paper_log.jsonl")
NOTE  = os.path.join(VAULT, "Markets Paper.md")
NOTIONAL = 1000.0  # paper $ per position (for readable P&L; signal tracked in %)

sys.path.insert(0, POLY)
import market_history as mh   # get_hist / analyze (reuse the keyless 3mo fetch)


def _load():
    try:
        return json.load(open(STATE))
    except Exception:
        return {"started": datetime.now(timezone.utc).isoformat()[:16] + "Z", "indices": {}}


def _save(s):
    tmp = STATE + ".tmp"
    json.dump(s, open(tmp, "w"), indent=1)
    os.replace(tmp, STATE)


def _constructive(a):
    return bool(a["above50"]) and a["mom1m"] > 0


def run(data):
    """Update the paper book from pre-fetched {label: analyze_dict} (no re-fetch —
    market_history does the single keyless pull and passes its results here)."""
    ts = datetime.now(timezone.utc).isoformat()[:16] + "Z"
    today = ts[:10]
    s = _load()
    acted = []
    for label, source, sym in mh.SYMBOLS:
        a = data.get(label)
        if not a or a.get("n", 0) < 50:   # need 50d SMA for the signal
            continue
        px = a["last"]
        st = s["indices"].setdefault(label, {"baseline_price": px, "baseline_date": today,
                                             "position": None, "closed": []})
        want_long = _constructive(a)
        pos = st["position"]
        if want_long and pos is None:
            st["position"] = {"entry_price": px, "entry_date": today}
            acted.append(f"OPEN {label} @ {px:,.0f}")
        elif (not want_long) and pos is not None:
            ret = (px / pos["entry_price"] - 1) * 100
            st["closed"].append({"entry_price": pos["entry_price"], "exit_price": px,
                                 "ret_pct": round(ret, 3), "entry_date": pos["entry_date"],
                                 "exit_date": today})
            st["position"] = None
            acted.append(f"CLOSE {label} @ {px:,.0f} ({ret:+.1f}%)")
        st["_last_px"] = px  # for MTM/board
    _save(s)

    # ── aggregate (signal vs buy-hold control) ──
    def signal_ret(st):
        r = sum(c["ret_pct"] for c in st["closed"])
        if st["position"]:
            r += (st["_last_px"] / st["position"]["entry_price"] - 1) * 100
        return r
    def buyhold_ret(st):
        return (st["_last_px"] / st["baseline_price"] - 1) * 100 if st.get("_last_px") else 0.0

    inds = s["indices"]
    n_closed = sum(len(st["closed"]) for st in inds.values())
    sig_tot = sum(signal_ret(st) for st in inds.values())
    bh_tot  = sum(buyhold_ret(st) for st in inds.values())
    beats = sum(1 for st in inds.values() if signal_ret(st) >= buyhold_ret(st))

    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": ts, "n_closed": n_closed, "signal_sum_pct": round(sig_tot, 2),
                            "buyhold_sum_pct": round(bh_tot, 2), "acted": acted}) + "\n")

    L = ["---", "type: paper", f"updated: {today}", "tags:\n  - markets\n  - paper", "---", "",
         "# Markets Paper (forward, signal long/flat)",
         f"> [!warning] PAPER ONLY, no real orders. FORWARD book (started {s['started'][:10]}), accumulating "
         f"1 obs/index/day — **not a backtest, not a validated edge**. Gate: n≥30 closed AND beats buy-hold "
         f"AND bootstrap CI>0. Signal = trend (above 50d SMA + +1m momentum). · {ts}", "",
         "## Scoreboard",
         f"- Closed paper trades: **{n_closed} / 30** to gate",
         f"- Signal cumulative: **{sig_tot:+.1f}%** · Buy-hold control: **{bh_tot:+.1f}%** · "
         f"signal beats hold on {beats}/{len(inds)} indices",
         f"- Verdict: {'ACCUMULATING (gate not met)' if n_closed < 30 else ('signal ' + ('BEATS' if sig_tot > bh_tot else 'LAGS') + ' buy-hold')}",
         "", "## Per index", "",
         "| Index | Pos | Entry | Last | Open MTM | Closed | Signal cum | Buy-hold |",
         "|---|---|--:|--:|--:|--:|--:|--:|"]
    for label, _, _ in mh.SYMBOLS:
        st = inds.get(label)
        if not st:
            continue
        pos = st.get("position"); last = st.get("_last_px")
        mtm = f"{(last/pos['entry_price']-1)*100:+.1f}%" if (pos and last) else "—"
        posc = f"LONG @{pos['entry_price']:,.0f}" if pos else "flat"
        entry = f"{pos['entry_price']:,.0f}" if pos else "—"
        L.append(f"| {label} | {posc} | {entry} | {last:,.0f} | {mtm} | {len(st['closed'])} | "
                 f"{signal_ret(st):+.1f}% | {buyhold_ret(st):+.1f}% |")
    L += ["", "_Linked: [[Markets]] (live board) · [[Markets Analysis]] (3-month study)._"]

    os.makedirs(VAULT, exist_ok=True)
    with open(NOTE + ".tmp", "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    os.replace(NOTE + ".tmp", NOTE)
    print(f"market_paper: {len(inds)} indices tracked, {n_closed} closed, "
          f"signal {sig_tot:+.1f}% vs hold {bh_tot:+.1f}% · {'; '.join(acted) or 'no flips'}")


def main():
    """Standalone: consume the cache market_history wrote (single shared fetch)."""
    cache = os.path.join(POLY, "market_history_cache.json")
    try:
        data = json.load(open(cache, encoding="utf-8"))
    except Exception:
        print("market_paper: no market_history_cache.json — run market_history.py first")
        return
    run(data)


if __name__ == "__main__":
    main()
