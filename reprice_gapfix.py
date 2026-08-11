#!/usr/bin/env python3
"""reprice_gapfix.py (2026-06-14) — one-shot: re-price cached backtest trades
under the gap-through-honest stop fill (backtest_nearres.simulate, now fixed).

The cached store kept only (reason, pnl, entry) per trade, not the realized
gapped mid, so the gap loss cannot be reconstructed from cache — we re-fetch
each cid's CLOB price history and re-run the (now honest) simulate(). The store
is overwritten IN PLACE so backtest_results.json uses ONE consistent methodology
(no mixing clean-fill and gap-honest trades on future accumulate runs).

Read-only vs Polymarket (GET only). Writes backtest_results.json + a fresh
backtest_summary.json. Run: python3 reprice_gapfix.py [label ...]
"""
import json, math, random, sys, time
import backtest_nearres as B   # import does NOT run its __main__ block

store = json.load(open(B.RESULTS_F))

GROUPS = [("esports_hi", 0.88, 0.95, 4),
          ("esports_hi_2h", 0.88, 0.95, 2),
          ("esports_lo", 0.80, 0.8799, 4),
          ("sports_hi", 0.88, 0.95, 4)]

def stats(rows):
    pnls = [t["pnl"] for t in rows]
    if not pnls:
        return None
    n = len(pnls); w = sum(1 for p in pnls if p > 0)
    random.seed(11)
    means = sorted(sum(random.choices(pnls, k=n)) / n for _ in range(10000))
    mu = sum(pnls) / n
    std = math.sqrt(sum((p - mu) ** 2 for p in pnls) / (n - 1)) or 1e-9
    raw_sr = mu / std * math.sqrt(n)
    corr = math.sqrt(math.log(90))
    dsr = raw_sr - corr
    return dict(n=n, wr=round(w / n * 100, 1), pnl=round(sum(pnls), 2),
                avg=round(mu, 4), ci_lo=round(means[250], 4),
                ci_hi=round(means[9750], 4), raw_sr=round(raw_sr, 3),
                dsr=round(dsr, 3), dsr_passes=dsr > 0)

def reprice(label, lo, hi, wh):
    rows = store["trades"].get(label, [])
    out = []
    for i, t in enumerate(rows):
        m = {"conditionId": t["cid"], "question": t.get("q", "")}
        r = B.simulate(m, lo, hi, window_h=wh)
        if r:
            out.append({"cid": t["cid"], "q": t.get("q", ""),
                        "reason": r[0], "pnl": r[1], "entry": round(r[2], 4)})
        if (i + 1) % 25 == 0:
            print(f"  {label}: {i+1}/{len(rows)} re-fetched", flush=True)
    return out

only = sys.argv[1:] or [g[0] for g in GROUPS]
summary = {"updated": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
           "methodology": "gap-through-honest stop fill (2026-06-14)",
           "markets_processed": len(store.get("done", {})), "bands": {}}

for label, lo, hi, wh in GROUPS:
    if label not in only:
        continue
    old = stats(store["trades"].get(label, []))
    print(f"\n=== {label} [{lo},{hi}] {wh}h ===", flush=True)
    print("OLD (clean-stop) :", old, flush=True)
    new_rows = reprice(label, lo, hi, wh)
    store["trades"][label] = new_rows
    new = stats(new_rows)
    print("NEW (gap-honest) :", new, flush=True)
    if new:
        summary["bands"][label] = new

json.dump(store, open(B.RESULTS_F, "w"))
json.dump(summary, open(B.SUMMARY_F, "w"), indent=1)
print("\nstore + summary re-priced in place ✓", flush=True)
