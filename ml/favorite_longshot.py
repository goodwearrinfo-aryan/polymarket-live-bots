#!/usr/bin/env python3
"""favorite_longshot.py — model-free favorite-longshot edge test (READ-ONLY).

The project's strongest edge candidate is NOT the ML model (its group-aware
direction edge is negative). It's a raw price-band fade: prediction markets
priced low ("longshots") resolve YES *less* often than their price implies, so
betting NO on them is +EV after spread. This phenomenon is strongest in SPORTS.

This script measures that directly on a collected dataset (one observation per
market, EARLIEST snapshot = entry price, NO look-ahead leak), then bootstraps a
band-fade strategy P&L with a spread charged. It also prints the per-category
composition of the tradeable band so a one-category / one-era artifact is
visible at a glance.

OUT-OF-TIME USE (the only thing that confirms the edge):
    1. First run on the in-sample dataset and freeze the baseline:
         python3 ml/favorite_longshot.py --freeze
       -> writes ml/favorite_longshot_baseline.json
    2. Collect a FRESH dataset of markets resolving AFTER the baseline era
       (reuse ml/collect_history.py), then:
         python3 ml/favorite_longshot.py --data ml/dataset_oot.csv --compare
       -> reports whether the 0.20-0.40 fade band still holds out-of-time.

Nothing here trades, trains, or hits the network. It only reads a CSV.

Required CSV columns: condition_id, as_of, prob, final_outcome, category
"""
import csv, sys, json, random, os, argparse, datetime as dt
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DATA = os.path.join(HERE, "dataset.csv")
BASELINE = os.path.join(HERE, "favorite_longshot_baseline.json")

# The tradeable fade band, fixed up front so the OOT test can't be tuned to fit.
FADE_BAND = (0.20, 0.40)
SPREAD = 0.02            # half-spread applied to the NO entry; realistic-ish
CAL_BINS = [(0.0, 0.10), (0.10, 0.20), (0.20, 0.35), (0.35, 0.50),
            (0.50, 0.65), (0.65, 0.80), (0.80, 1.01)]


def load_markets(path):
    """Collapse a snapshot CSV to one (entry_price, outcome, category, as_of)
    per market, using the EARLIEST snapshot to avoid look-ahead leakage."""
    rows = list(csv.DictReader(open(path)))
    by = defaultdict(list)
    for r in rows:
        try:
            by[r["condition_id"]].append(
                (int(r["as_of"]), float(r["prob"]),
                 int(float(r["final_outcome"])), r.get("category", "?")))
        except (KeyError, ValueError):
            continue
    mk = []
    for cid, obs in by.items():
        obs.sort()                      # earliest as_of first
        _, p, y, cat = obs[0]
        mk.append((p, y, cat))
    span = None
    ts = [int(r["as_of"]) for r in rows if r.get("as_of")]
    if ts:
        span = (dt.datetime.utcfromtimestamp(min(ts)).date().isoformat(),
                dt.datetime.utcfromtimestamp(max(ts)).date().isoformat())
    return mk, span


def boot_ci(vals, n=4000, seed=1):
    if len(vals) < 2:
        return (float("nan"), float("nan"), float("nan"))
    rng = random.Random(seed); N = len(vals); means = []
    for _ in range(n):
        means.append(sum(vals[rng.randrange(N)] for _ in range(N)) / N)
    means.sort()
    return (sum(vals) / N, means[int(n * 0.025)], means[int(n * 0.975)])


def fade_pnls(mk, lo, hi, spread=SPREAD):
    """Per-$1 P&L of betting NO at entry on every market priced in [lo,hi)."""
    out = []
    for p, y, _ in mk:
        if lo <= p < hi:
            cost = (1 - p) + spread          # buy NO at (1-p) + half-spread
            payout = 1.0 if y == 0 else 0.0  # NO pays 1 if market resolves NO
            out.append(payout - cost)
    return out


def summarize(mk, span, label):
    n = len(mk); yes = sum(y for _, y, _ in mk) / n if n else 0
    print(f"\n=== {label} ===")
    print(f"markets: {n}   overall YES rate: {yes:.3f}"
          + (f"   era: {span[0]} -> {span[1]}" if span else ""))

    print("\nCALIBRATION (entry price vs realized YES; gap>0 = YES overpriced -> fade NO)")
    print(f"{'price bin':12} {'n':>4} {'mean_p':>7} {'YES':>6} {'gap':>7}")
    for lo, hi in CAL_BINS:
        g = [(p, y) for p, y, _ in mk if lo <= p < hi]
        if not g:
            continue
        m = len(g); mp = sum(p for p, _ in g) / m; ry = sum(y for _, y in g) / m
        print(f"{f'[{lo:.2f},{hi:.2f})':12} {m:>4} {mp:>7.3f} {ry:>6.3f} {mp - ry:>+7.3f}")

    lo, hi = FADE_BAND
    pn = fade_pnls(mk, lo, hi)
    mean, l, h = boot_ci(pn)
    verdict = "+EV" if l > 0 else ("losing" if h < 0 else "noise")
    print(f"\nFADE BAND [{lo:.2f},{hi:.2f})  NO bet, {SPREAD:.2f} half-spread charged")
    print(f"  n={len(pn)}  meanP&L/$1={mean:+.4f}  CI[{l:+.3f},{h:+.3f}]  -> {verdict}")

    # category composition of the band — exposes one-category/one-era artifacts
    band = [(p, y, c) for p, y, c in mk if lo <= p < hi]
    cc = defaultdict(lambda: [0, 0])
    for p, y, c in band:
        cc[c][0] += 1; cc[c][1] += y
    print("  band composition by category:")
    for c, (cn, cy) in sorted(cc.items(), key=lambda x: -x[1][0]):
        print(f"    {c:12} n={cn:>3}  YES_rate={cy / cn:.2f}")
    return {"n_band": len(pn), "mean": mean, "ci": [l, h], "verdict": verdict,
            "band": list(FADE_BAND), "spread": SPREAD, "era": span,
            "overall_yes": yes, "n_markets": n}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=DEFAULT_DATA, help="snapshot CSV path")
    ap.add_argument("--freeze", action="store_true",
                    help="write current result as the baseline to compare future runs against")
    ap.add_argument("--compare", action="store_true",
                    help="compare current result to the frozen baseline (OOT test)")
    a = ap.parse_args()

    if not os.path.exists(a.data):
        print(f"ERROR: dataset not found: {a.data}"); sys.exit(1)
    mk, span = load_markets(a.data)
    if not mk:
        print(f"ERROR: no usable rows in {a.data}"); sys.exit(1)

    res = summarize(mk, span, os.path.basename(a.data))

    if a.freeze:
        json.dump(res, open(BASELINE, "w"), indent=2)
        print(f"\nbaseline frozen -> {BASELINE}")

    if a.compare:
        if not os.path.exists(BASELINE):
            print("\nNo baseline to compare against. Run with --freeze on the "
                  "in-sample dataset first."); return
        base = json.load(open(BASELINE))
        print("\n=== OUT-OF-TIME COMPARISON ===")
        print(f"  baseline era {base.get('era')}: meanP&L {base['mean']:+.4f} "
              f"CI{base['ci']} ({base['verdict']})")
        print(f"  current  era {res.get('era')}: meanP&L {res['mean']:+.4f} "
              f"CI{res['ci']} ({res['verdict']})")
        held = res["ci"][0] > 0
        # does the new CI even overlap the old point estimate's sign?
        if held and base["verdict"] == "+EV":
            print("  VERDICT: ✅ fade band HELD out-of-time — edge is real, not a regime artifact.")
        elif res["verdict"] == "losing":
            print("  VERDICT: ❌ fade band REVERSED out-of-time — in-sample result was an artifact.")
        else:
            print("  VERDICT: ⚠️ inconclusive — band no longer +EV but not clearly losing. "
                  "Need more out-of-time markets.")


if __name__ == "__main__":
    main()
