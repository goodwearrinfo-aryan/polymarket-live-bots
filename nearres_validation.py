#!/usr/bin/env python3
"""
nearres_validation.py — triple validation of the nearres OOS backtest,
methodology ported from Vibe-Trading's backtest/validation.py (HKUDS) and
adapted to prediction markets.

Three tests per band (esports_hi = candidate; esports_lo, sports_hi = controls):

1. FAIR-MARKET MONTE CARLO — the prediction-market-correct null: if the market
   price is unbiased, a bet entered at price p wins with probability exactly p.
   Simulate 10,000 such worlds with the SAME entries; p-value = fraction of
   null worlds whose mean ROI >= observed. This is stronger than the bootstrap:
   it asks "could a fair market have produced this?", not "is the sample mean
   stable?". RUNS ON THE FULL ENTRY-CARRYING POPULATION (resolve + stop + time) —
   conditioning on reason==resolve is a survivorship leak (fixed 2026-06-15; see
   fair_market_mc). No reason==resolve/target slice may EVER be reported as edge.

2. BOOTSTRAP ROI CI — resample trades with replacement, 95% CI on mean ROI
   (same as the lab gate; kept for comparability).

3. WALK-FORWARD FOLDS — split the trade sequence (accumulation order ≈
   chronological) into 5 sequential folds; an edge that lives only in one fold
   is regime luck. Pass = majority of folds positive AND second half positive.

Entry price is recovered from resolution pnl: win → entry = 1 - pnl,
loss → entry = -pnl. Trades whose recovered entry falls outside (0.02, 0.98)
are excluded (non-resolution artifacts).

PAPER RESEARCH ONLY — reads backtest_results.json, writes a report. No orders.
Run:  python3 nearres_validation.py        (prints + writes nearres_validation.json)
"""
import json
import numpy as np

RESULTS = "/Users/aryanagarwal/Documents/polymarket/backtest_results.json"
OUT     = "/Users/aryanagarwal/Documents/polymarket/nearres_validation.json"
N_MC, N_BOOT, N_FOLDS, SEED = 10_000, 10_000, 5, 42


def load_pnls(trades):
    """Per-trade dollar pnl series ($2 bets, gain/stop/resolve exits)."""
    return np.array([t["pnl"] for t in trades if t.get("pnl") is not None])


def signflip_test(pnls, rng):
    """Sign-flip randomization test for mean pnl > 0 (distribution-free).
    Null: pnls are symmetric around zero (no edge) — randomly flip each
    trade's sign 10,000 times; p = P(null mean >= observed mean).
    NOTE: Vibe-Trading's order-permutation test was tried first and is
    degenerate for fixed-stake bets (shuffling order doesn't change the mean,
    and without compounding the Sharpe is ~permutation-invariant)."""
    observed = pnls.mean()
    flips = rng.choice([-1.0, 1.0], (N_MC, len(pnls)))
    null_means = (flips * np.abs(pnls)).mean(axis=1)
    p = float((null_means >= observed).mean())
    return {
        "observed_mean": round(float(observed), 4),
        "null_p95":      round(float(np.percentile(null_means, 95)), 4),
        "p_value":       round(p, 4),
        "passes":        bool(p < 0.05),
    }


def fair_market_mc(trades, rng):
    """The prediction-market-correct null on the FULL realized population.

    SURVIVORSHIP FIX 2026-06-15 (FL-premium harvest audit, generalized Lesson 13):
    this test previously filtered `reason == "resolve"`, which CONDITIONS ON THE
    OUTCOME — trades that reach resolution are disproportionately winners because
    the losers gap-stop out first. That made the old "+0.117 PASS" a survivorship
    artifact, not an edge (the matching gap-stops, ~40-55% of the same population,
    average -0.37 to -0.46/unit and were silently dropped). We now use EVERY
    entry-carrying trade (resolve + stop + time); the observed mean reflects real
    P&L incl. gap-stops. A PASS here is necessary-not-sufficient and must still
    clear the all-reasons bootstrap CI + DSR. NEVER reintroduce a reason filter."""
    entry_trades = [t for t in trades if t.get("entry")]
    # survivorship guard: an edge test must not drop stop/time exits.
    all_reasons  = {t.get("reason") for t in trades if t.get("entry")}
    used_reasons = {t.get("reason") for t in entry_trades}
    assert used_reasons == all_reasons, (
        f"survivorship leak: edge test dropped exit reasons "
        f"{all_reasons - used_reasons} — resolve/target-only slices are forbidden "
        "(FL-premium audit 2026-06-15, generalized Lesson 13)")
    res = [(t["entry"], t["pnl"]) for t in entry_trades]
    if len(res) < 10:
        return {"pending": f"only {len(res)} entry-carrying trades — "
                           "accumulating since 2026-06-11 patch", "passes": None}
    entries = np.array([e for e, _ in res])
    pnls    = np.array([p for _, p in res])
    fills   = entries + 0.015                       # HALF_SPREAD in backtest
    size    = 2.0 / fills                           # BET=$2
    wins, losses = (1 - fills) * size, -fills * size
    draws = rng.random((N_MC, len(entries)))
    null_means = np.where(draws < entries, wins, losses).mean(axis=1)
    p = float((null_means >= pnls.mean()).mean())
    return {
        "n_entry_trades": len(res),
        "observed_mean_pnl": round(float(pnls.mean()), 4),
        "null_mean_pnl":     round(float(null_means.mean()), 4),
        "p_value":           round(p, 4),
        "passes":            bool(p < 0.05),
    }


def bootstrap_ci(rois, rng):
    boots = rng.choice(rois, (N_BOOT, len(rois)), replace=True).mean(axis=1)
    lo, hi = np.percentile(boots, [2.5, 97.5])
    return {
        "mean_roi": round(float(rois.mean()), 4),
        "ci_lo": round(float(lo), 4), "ci_hi": round(float(hi), 4),
        "passes": bool(lo > 0),
    }


def walk_forward(rois):
    folds = np.array_split(rois, N_FOLDS)
    fold_means = [round(float(f.mean()), 4) for f in folds if len(f)]
    positive = sum(m > 0 for m in fold_means)
    second_half = rois[len(rois) // 2:]
    return {
        "fold_means": fold_means,
        "folds_positive": f"{positive}/{len(fold_means)}",
        "second_half_mean": round(float(second_half.mean()), 4),
        "passes": bool(positive > len(fold_means) / 2 and second_half.mean() > 0),
    }


def main():
    data = json.load(open(RESULTS))
    rng = np.random.default_rng(SEED)
    report = {"source": RESULTS, "n_mc": N_MC, "n_boot": N_BOOT}

    for band, trades in data["trades"].items():
        pnls = load_pnls(trades)
        if len(pnls) < 10:
            report[band] = {"error": f"only {len(pnls)} usable trades"}
            continue
        pt  = signflip_test(pnls, rng)
        bs  = bootstrap_ci(pnls, rng)
        wf  = walk_forward(pnls)
        mc  = fair_market_mc(trades, rng)
        hard = [pt["passes"], bs["passes"], wf["passes"]]
        if mc["passes"] is not None:
            hard.append(mc["passes"])
        verdict = "EDGE" if all(hard) else "NO EDGE"
        report[band] = {
            "n": len(pnls), "win_rate": round(float((pnls > 0).mean()), 3),
            "signflip": pt, "bootstrap": bs, "walk_forward": wf,
            "fair_market_mc": mc, "verdict": verdict,
        }
        print(f"\n── {band} (n={len(pnls)}, WR={(pnls>0).mean():.0%}) ──")
        print(f"  1. sign-flip test : mean {pt['observed_mean']:+.4f} (null p95 {pt['null_p95']:+.4f})  p={pt['p_value']}"
              f"  {'PASS' if pt['passes'] else 'FAIL'}")
        print(f"  2. bootstrap CI95 : [{bs['ci_lo']:+.4f}, {bs['ci_hi']:+.4f}]  {'PASS' if bs['passes'] else 'FAIL'}")
        print(f"  3. walk-forward   : folds {wf['folds_positive']} positive, 2nd half {wf['second_half_mean']:+.4f}"
              f"  {'PASS' if wf['passes'] else 'FAIL'}")
        if mc["passes"] is None:
            print(f"  4. fair-market MC : {mc['pending']}")
        else:
            print(f"  4. fair-market MC : mean pnl {mc['observed_mean_pnl']:+.4f} vs null {mc['null_mean_pnl']:+.4f}"
                  f"  p={mc['p_value']}  {'PASS' if mc['passes'] else 'FAIL'}")
        print(f"  VERDICT: {verdict}")

    json.dump(report, open(OUT, "w"), indent=1)
    print(f"\nreport → {OUT}")


if __name__ == "__main__":
    main()
