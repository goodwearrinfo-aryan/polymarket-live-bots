#!/usr/bin/env python3
"""calibration_report.py — score forecasts against resolved outcomes. NO trading,
NO live API, NO Polymarket access needed. Runs on ml/dataset.csv (resolved markets
with market price `prob` + realized `final_outcome`).

Reports, for a probability source:
  * Brier score        (mean squared error; 0=perfect, 0.25=coinflip)
  * Log loss           (penalizes confident-and-wrong harder)
  * Reliability table  (predicted prob vs actual frequency per bin — is it calibrated?)
  * No-trade edge est. (vs market price: where source diverges, would it have won?)

By default it scores the MARKET PRICE itself = the bar any model must beat.
Pass a CSV with a `pred` column (your model's probabilities, keyed by condition_id)
to score the model instead — generate that on the Mac where sklearn runs.

Usage:
  python3 calibration_report.py                 # score the market price (baseline)
  python3 calibration_report.py preds.csv       # score model preds vs outcomes & market
Stdlib only.
"""
import sys, os, csv, math, collections

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "ml", "dataset.csv")

def clip(p, lo=1e-6, hi=1-1e-6):
    return max(lo, min(hi, p))

def brier(pairs):
    return sum((p - y) ** 2 for p, y in pairs) / len(pairs)

def logloss(pairs):
    return -sum(y * math.log(clip(p)) + (1 - y) * math.log(1 - clip(p)) for p, y in pairs) / len(pairs)

def reliability(pairs, nbins=10):
    bins = collections.defaultdict(list)
    for p, y in pairs:
        b = min(nbins - 1, int(p * nbins))
        bins[b].append((p, y))
    rows = []
    for b in range(nbins):
        g = bins.get(b, [])
        if not g:
            continue
        avg_p = sum(p for p, _ in g) / len(g)
        freq = sum(y for _, y in g) / len(g)
        rows.append((f"{b/nbins:.1f}-{(b+1)/nbins:.1f}", len(g), avg_p, freq))
    return rows

def no_trade_edge(rows, spread=0.02, thresh=0.10):
    """For each market where the SOURCE prob diverges from market price by >thresh,
    pretend we bought the cheaper side and held to resolution; net the round-trip
    spread. Pure paper, on resolved data — estimates whether the source had edge."""
    n = wins = 0
    pnl = 0.0
    for price, src, y in rows:
        if abs(src - price) < thresh:
            continue
        n += 1
        if src > price:          # source thinks YES underpriced -> buy YES at price
            entry = price + spread / 2
            payoff = (1.0 if y == 1 else 0.0) - entry
        else:                    # source thinks YES overpriced -> buy NO
            entry = (1 - price) + spread / 2
            payoff = (1.0 if y == 0 else 0.0) - entry
        pnl += payoff
        wins += 1 if payoff > 0 else 0
    return n, (wins / n * 100 if n else 0.0), (pnl / n if n else 0.0)

def fade_backtest(mk, lo, hi, spread=0.02):
    """Rule: on markets priced in [lo,hi], BUY NO (fade the overpriced YES) and
    hold to resolution. Charges the spread. Pure paper on resolved data — this
    quantifies the favorite-longshot edge the reliability table reveals."""
    n = wins = 0; pnl = 0.0
    for _, price, y in mk:
        if not (lo <= price <= hi):
            continue
        n += 1
        entry = (1 - price) + spread / 2          # buy NO at the ask
        payoff = (1.0 if y == 0 else 0.0) - entry  # NO pays 1 if outcome is NO
        pnl += payoff
        wins += 1 if payoff > 0 else 0
    return n, (wins / n * 100 if n else 0.0), (pnl / n if n else 0.0), pnl

def load_market():
    rows = list(csv.DictReader(open(DATA)))
    # dedupe to ONE row per market for honest independence (learned this the hard way)
    seen = collections.OrderedDict()
    for r in rows:
        seen.setdefault(r["condition_id"], r)
    out = []
    for r in seen.values():
        try:
            out.append((r["condition_id"], float(r["prob"]), int(float(r["final_outcome"]))))
        except Exception:
            pass
    return out

def main():
    mk = load_market()
    n = len(mk)
    base = sum(y for _, _, y in mk) / n
    print(f"Resolved markets (deduped, independent): {n}   base YES rate: {base:.3f}")

    # the source we're scoring: market price by default, or model preds if given
    if len(sys.argv) > 1 and os.path.exists(sys.argv[1]):
        preds = {r["condition_id"]: float(r["pred"]) for r in csv.DictReader(open(sys.argv[1]))}
        src_name = f"MODEL ({os.path.basename(sys.argv[1])})"
        scored = [(cid, price, preds[cid], y) for cid, price, y in mk if cid in preds]
        pairs = [(p, y) for _, _, p, y in scored]
        edge_rows = [(price, p, y) for _, price, p, y in scored]
    else:
        src_name = "MARKET PRICE (baseline — the bar to beat)"
        pairs = [(price, y) for _, price, y in mk]
        edge_rows = [(price, price, y) for _, price, y in mk]  # source == price -> no divergence

    print(f"\nScoring: {src_name}   (n={len(pairs)})")
    print(f"  Brier   : {brier(pairs):.4f}   (0=perfect, 0.25=coinflip; lower better)")
    print(f"  Log loss: {logloss(pairs):.4f}   (lower better)")
    print("\n  Reliability (is it calibrated?):")
    print(f"    {'bin':10} {'n':>4} {'predicted':>10} {'actual':>8}  gap")
    for label, cnt, ap, fr in reliability(pairs):
        flag = "" if abs(ap - fr) < 0.10 else "  <-- off"
        print(f"    {label:10} {cnt:>4} {ap:>10.3f} {fr:>8.3f}{flag}")

    if src_name.startswith("MODEL"):
        nn, wr, ppt = no_trade_edge(edge_rows)
        print(f"\n  No-trade edge vs market (divergence>0.10, spread charged):")
        print(f"    triggered on {nn} markets, {wr:.0f}% win, {ppt:+.4f}/trade")
        print("    >0 means the model would have beaten the market here (paper, resolved data).")
    else:
        print("\n  (Edge estimate needs a model: run `python3 calibration_report.py preds.csv`.)")
    # ── Fade rule backtest (the favorite-longshot edge), no trading ──
    print("\n  FADE rule backtest — buy NO on markets priced in a band, hold to resolution")
    print(f"    {'band':12} {'trades':>6} {'win%':>6} {'$/trade':>9} {'total$':>9}")
    for lo, hi in [(0.20, 0.60), (0.20, 0.50), (0.30, 0.60), (0.40, 0.60), (0.50, 0.60)]:
        nn, wr, ppt, tot = fade_backtest(mk, lo, hi)
        print(f"    {f'{lo:.2f}-{hi:.2f}':12} {nn:>6} {wr:>5.0f}% {ppt:>+9.4f} {tot:>+9.3f}")
    print("    (>0 $/trade = the favorite-longshot fade had historical edge AFTER spread)")

    print("\n  Honest note: this scores forecasts on PAST resolved markets, one era,")
    print("  selection-biased to high-volume closed markets. A good past score is")
    print("  necessary, not sufficient — out-of-sample/forward is the real test.")

if __name__ == "__main__":
    main()
