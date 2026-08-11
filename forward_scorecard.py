#!/usr/bin/env python3
"""
Forward-only scorecard.

Reports performance ONLY on trades opened AFTER the config change made on
2026-05-30 (longshots off, midline off, min_confidence 0.48, BUY gate).
This is the honest test set: the threshold rules were fit on the OLD data,
so only trades opened after the cutoff are out-of-sample evidence.

The cutoff is stored in `.forward_cutoff` (frozen at the latest trade that
existed when the change shipped). Everything opened after it counts as forward.

Usage:  python3 forward_scorecard.py
"""
import json, os, sys
from collections import Counter

BASE = os.path.dirname(os.path.abspath(__file__))
LOG  = os.path.join(BASE, "trade_log.json")
CUT  = os.path.join(BASE, ".forward_cutoff")


def load_cutoff():
    if os.path.exists(CUT):
        return open(CUT).read().strip()
    # fallback: the timestamp the change shipped
    return "2026-05-30T22:26:17"


def stat(rows):
    n = len(rows)
    closed = [r for r in rows if "pnl_usdc" in r]
    w = sum(1 for r in closed if r.get("pnl_usdc", 0) > 0)
    pnl = sum(r.get("pnl_usdc", 0) for r in closed)
    wr = (w / len(closed) * 100) if closed else 0
    return n, len(closed), w, wr, pnl


def bar(label, rows):
    n, c, w, wr, pnl = stat(rows)
    flag = ""
    if c >= 5:
        flag = "  ✅ on track" if (wr >= 50 and pnl > 0) else "  ⚠️ below target"
    print(f"  {label:18s} opened={n:3d}  closed={c:3d}  "
          f"W={w:3d}  win={wr:5.1f}%  pnl=${pnl:+7.2f}{flag}")


def main():
    cutoff = load_cutoff()
    with open(LOG) as f:
        d = json.load(f)
    rows = d.get("open", []) + d.get("closed", [])
    fwd  = [r for r in rows if r.get("opened_at", "") > cutoff]
    old  = [r for r in rows if r.get("opened_at", "") <= cutoff]

    print("=" * 64)
    print("  FORWARD-ONLY SCORECARD  (out-of-sample, post-change)")
    print(f"  cutoff: trades opened after {cutoff}")
    print("=" * 64)
    if not fwd:
        print("\n  No forward trades yet. Restart the bot and let it run.")
        print(f"  (Baseline being excluded: {len(old)} pre-change trades.)\n")
        return

    print("\n  FORWARD (the only data that proves the change worked):")
    bar("ALL forward", fwd)
    print("  by side:")
    for s in ("SELL", "BUY"):
        bar(f"  {s}", [r for r in fwd if str(r.get("side")).upper() == s])
    print("  by strategy:")
    for s in sorted({r.get("strategy", "?") for r in fwd}):
        bar(f"  {s}", [r for r in fwd if r.get("strategy") == s])

    n, c, w, wr, pnl = stat(fwd)
    print("\n  " + "-" * 60)
    if c < 30:
        print(f"  ⏳ Only {c} forward trades closed. Need ~30+ before trusting")
        print(f"     the verdict — too early to conclude anything.")
    elif wr >= 50 and pnl > 0:
        print(f"  ✅ REAL EDGE holding: {wr:.0f}% win, ${pnl:+.2f} on fresh data.")
    else:
        print(f"  ⚠️ Change did NOT hold out-of-sample ({wr:.0f}% win, ${pnl:+.2f}).")
        print(f"     Likely overfit — loosen the thresholds.")
    print()


if __name__ == "__main__":
    main()
