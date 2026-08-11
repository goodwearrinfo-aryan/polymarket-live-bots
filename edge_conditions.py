#!/usr/bin/env python3
"""
edge_conditions.py — conditional factor analysis over logged exits (2026-06-13).

Implements the idea behind the cloned quant frameworks (qlib factor buckets,
Lean alpha-by-regime) on the bot's OWN data — stdlib only, no external code run.

A flat per-leg mean (edge_audit.py) hides a real edge that only fires in ONE
regime. This slices every leg's P&L by:
  • category      (sports / politics / crypto / esports / world / other)
  • entry price   (longshot <0.35 / mid 0.35-0.65 / favorite >0.65)
  • liquidity     (thin <100k / mid 100-500k / deep >500k)
  • exit reason   (target / stop / time / resolved)
and surfaces buckets where mean P&L > 0 with n>=8 — candidate CONDITIONAL edges
worth a focused leg, even when the leg's overall mean is negative.

  python3 edge_conditions.py                 # pooled across all legs + per-leg top buckets
  python3 edge_conditions.py nearres         # one leg, full breakdown
"""
import json, sys
from pathlib import Path
from collections import defaultdict

BASE = Path(__file__).resolve().parent
STATE = BASE / "scalp_lab_state.json"
MIN_BUCKET = 8

def price_bucket(p):
    if p is None: return "?"
    return "longshot<.35" if p < 0.35 else ("favorite>.65" if p > 0.65 else "mid.35-.65")

def liq_bucket(v):
    if not v: return "?"
    return "thin<100k" if v < 100_000 else ("deep>500k" if v > 500_000 else "mid100-500k")

def collect(leg_filter=None):
    s = json.loads(STATE.read_text())
    rows = []
    for leg, book in s.items():
        if not isinstance(book, dict) or "closed" not in book: continue
        if leg_filter and leg != leg_filter: continue
        for r in book["closed"]:
            p = r.get("pnl_usdc")
            if p is None: continue
            rows.append({
                "leg": leg, "pnl": p,
                "cat": r.get("cat") or "none",
                "price": price_bucket(r.get("entry_mid")),
                "liq": liq_bucket(r.get("vol")),
                "reason": r.get("reason", "?"),
                "side": r.get("side", "?"),
            })
    return rows

def bucket_stats(rows, dim):
    agg = defaultdict(list)
    for r in rows:
        agg[r[dim]].append(r["pnl"])
    out = []
    for k, xs in agg.items():
        n = len(xs)
        if n >= MIN_BUCKET:
            mean = sum(xs) / n
            win = 100 * sum(1 for x in xs if x > 0) / n
            out.append((mean, k, n, win))
    out.sort(reverse=True)
    return out

def show(title, rows):
    print(f"\n=== {title}  (n={len(rows)}) ===")
    for dim in ("cat", "price", "liq", "reason", "side"):
        st = bucket_stats(rows, dim)
        if not st: continue
        print(f"  by {dim}:")
        for mean, k, n, win in st:
            flag = "  ★+EV" if mean > 0 else ""
            print(f"    {k:<16} n={n:<4} mean={mean:+.4f}  win={win:.0f}%{flag}")

def main():
    if len(sys.argv) > 1:
        leg = sys.argv[1]
        rows = collect(leg)
        if not rows:
            print(f"no priced exits for '{leg}'"); return
        show(f"leg: {leg}", rows)
        return
    # pooled view + cross-leg conditional winners
    allrows = collect()
    show("ALL LEGS POOLED", allrows)
    # hunt: any (leg, cat) or (leg, price) bucket that is +EV with n>=8
    print("\n=== CONDITIONAL EDGE HUNT — +EV buckets (n>=8), any leg ===")
    by = defaultdict(list)
    for r in allrows:
        by[(r["leg"], "cat", r["cat"])].append(r["pnl"])
        by[(r["leg"], "price", r["price"])].append(r["pnl"])
        by[(r["leg"], "liq", r["liq"])].append(r["pnl"])
    hits = []
    for (leg, dim, val), xs in by.items():
        n = len(xs)
        if n >= MIN_BUCKET:
            mean = sum(xs) / n
            if mean > 0.02:   # meaningfully positive after spread
                win = 100 * sum(1 for x in xs if x > 0) / n
                hits.append((mean, leg, dim, val, n, win))
    hits.sort(reverse=True)
    if not hits:
        print("  none — no leg has a +EV conditional bucket above +0.02/exit")
    for mean, leg, dim, val, n, win in hits[:20]:
        print(f"  {leg:<14} {dim}={val:<16} n={n:<4} mean={mean:+.4f} win={win:.0f}%")
    print("\nNOTE: conditional buckets are hypotheses, not edges — they need the")
    print("usual gate (>=30 exits IN THAT BUCKET + bootstrap CI>0) before trusting.")

if __name__ == "__main__":
    main()
