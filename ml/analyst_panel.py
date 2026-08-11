#!/usr/bin/env python3
"""
ml/analyst_panel.py — adversarial analyst-panel debate, measured honestly.

THE IDEA (done the only way that can work):
  Three analysts, each trained on a DIFFERENT slice of the data, and crucially
  NONE of them sees the market price ("prob"). They predict the outcome from
  their own evidence, then "argue" by taking YES/NO sides relative to the market:
      • MOMENTUM analyst  : price-change / volatility / drift features
      • LIQUIDITY analyst : volume / liquidity / activity / time-to-resolve
      • NEWS analyst      : news sentiment (+ distance from 50/50)
  A JUDGE then bets using all three of the requested outputs:
      A) BLEND        : average the analysts, bet when blend diverges from market
      B) CONSENSUS    : bet only when ALL agree on a side AND blend diverges
      C) DISAGREEMENT : rule A, but only when the analysts mostly agree
                        (high internal disagreement -> sit out)

WHY THIS SHAPE: two clones of the same field can't add information — their
"argument" is just fitting noise. Edge can only come from INDEPENDENT views,
so the analysts are split by feature family and blinded to the price. Whether
it actually beats the market is decided by the numbers below, not by the idea.

Temporal (out-of-time) split: train on markets that resolved BEFORE a cutoff,
test on the ones that resolved AFTER. Spread charged on entry; marked to the
real 0/1 resolution. Compared against a single full-feature model (sees price,
= today's edge_trader) and the ALLIN control.

Run on the Mac (needs sklearn):  python3 ml/analyst_panel.py [dataset.csv] [cutoff_frac]
"""
import os, sys, json, random

HERE = os.path.dirname(os.path.abspath(__file__))
DATASET = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "dataset.csv")
CUTOFF_FRAC = float(sys.argv[2]) if len(sys.argv) > 2 else 0.70

EDGE_MIN = 0.10        # bet only when |prob_estimate - market| >= this
DIS_THRESH = 0.10      # "they agree" if std(analyst probs) <= this
SPREAD = 0.02
BAND = (0.05, 0.95)

# ── The panel ─────────────────────────────────────────────────────────────
# Out-of-time proxy run (49 later markets) showed only MOMENTUM carried signal
# (AUC 0.66); liquidity (0.46) and news (0.47) were noise and, when blended,
# DRAGGED the panel down to a coin flip (0.50). So the panel is momentum-only.
CORE_VIEWS = {
    "momentum": ["chg_1h", "chg_6h", "chg_24h", "vol_24h", "drift_24h", "range_24h"],
}

# ── SLOT for a genuine second voice (the only thing that can add edge) ───────
# A second analyst is worth adding ONLY if it sees information the market price
# has NOT already absorbed — i.e. the resolution-source / news-latency feed in
# CLAUDE.md, not another recombination of price history.
# To wire it: add the feed's column(s) to ml/dataset.csv (via collect_history /
# features.py), then list them here. The judge auto-includes it as a 2nd voice,
# and rules B (consensus) and C (agree/disagree gate) become meaningful again.
# Example once you have a feed:  EXTERNAL_VIEWS = {"resfeed": ["res_signal", "res_lead_h"]}
EXTERNAL_VIEWS = {}

VIEWS = {**CORE_VIEWS, **EXTERNAL_VIEWS}


def _ensure(pkgs):
    import subprocess
    for p in pkgs:
        mod = {"scikit-learn": "sklearn"}.get(p, p)
        try:
            __import__(mod)
        except ImportError:
            subprocess.run([sys.executable, "-m", "pip", "install", p,
                            "--break-system-packages", "-q"], check=False)


def main():
    _ensure(["numpy", "pandas", "scikit-learn"])
    import numpy as np, pandas as pd
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.calibration import CalibratedClassifierCV
    from sklearn.metrics import roc_auc_score
    sys.path.insert(0, HERE)
    import features as F
    ALL = F.FEATURE_NAMES

    df = pd.read_csv(DATASET)
    # Guard: drop any EXTERNAL view whose feed columns aren't in the dataset yet,
    # so flipping on a feed before it's been collected can never crash the run.
    for _vn in list(VIEWS):
        _missing = [c for c in VIEWS[_vn] if c not in df.columns]
        if _missing:
            print(f" (skipping view '{_vn}': columns not in dataset yet: {_missing})")
            del VIEWS[_vn]
    df = df[df["final_outcome"].astype(str).isin(["0", "1"])]
    df["final_outcome"] = df["final_outcome"].astype(int)
    df["resolve"] = df["resolve"].astype(float)

    rmin, rmax = df["resolve"].min(), df["resolve"].max()
    cutoff = rmin + (rmax - rmin) * CUTOFF_FRAC
    tr = df[df["resolve"] < cutoff].copy()
    te = df[df["resolve"] >= cutoff].copy()
    te = te.sort_values("as_of").groupby("condition_id", as_index=False).tail(1)

    import datetime as _dt
    d = lambda ts: _dt.datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d")
    print("\n============ ANALYST-PANEL DEBATE (out-of-time) ============")
    print(f" resolve {d(rmin)} → {d(rmax)}  cutoff {d(cutoff)} (frac={CUTOFF_FRAC})")
    print(f" train markets={tr['condition_id'].nunique()}  test markets={te['condition_id'].nunique()} rows={len(te)}")
    if len(te) < 20 or tr["final_outcome"].nunique() < 2 or te["final_outcome"].nunique() < 2:
        print(" ⚠️  Too few later-period markets to trust — collect a fresh batch and rerun.")
        if len(te) == 0:
            print("============================================================\n"); return

    def fit_cal(cols):
        """HistGBM on `cols`, isotonic-calibrated on a held-out market slice."""
        cids = sorted(tr["condition_id"].unique().tolist())
        random.Random(7).shuffle(cids)
        cut = max(1, int(len(cids) * 0.75))
        fit_df = tr[tr["condition_id"].isin(set(cids[:cut]))]
        cal_df = tr[~tr["condition_id"].isin(set(cids[:cut]))]
        base = HistGradientBoostingClassifier(max_iter=300, learning_rate=0.05,
            max_leaf_nodes=31, min_samples_leaf=30, l2_regularization=1.0, random_state=42)
        base.fit(fit_df[cols].astype(float).values, fit_df["final_outcome"].values)
        model = base
        if len(cal_df) and cal_df["final_outcome"].nunique() > 1:
            try:
                from sklearn.frozen import FrozenEstimator
                model = CalibratedClassifierCV(FrozenEstimator(base), method="isotonic")
            except Exception:
                model = CalibratedClassifierCV(base, method="isotonic", cv=3)
            model.fit(cal_df[cols].astype(float).values, cal_df["final_outcome"].values)
        return model

    analysts = {name: fit_cal(cols) for name, cols in VIEWS.items()}
    baseline = fit_cal(ALL)  # single model that DOES see price = today's approach

    yte = te["final_outcome"].values
    market = te["prob"].astype(float).values
    probs = {name: m.predict_proba(te[VIEWS[name]].astype(float).values)[:, 1] for name, m in analysts.items()}
    base_p = baseline.predict_proba(te[ALL].astype(float).values)[:, 1]
    P = np.vstack([probs[n] for n in VIEWS])      # (3, N)
    blend = P.mean(axis=0)
    disagree = P.std(axis=0)

    print("-" * 60)
    for n in VIEWS:
        try: print(f" analyst[{n:<9}] AUC={roc_auc_score(yte, probs[n]):.3f}")
        except Exception: print(f" analyst[{n:<9}] AUC=n/a")
    try: print(f" blend AUC={roc_auc_score(yte, blend):.3f}   single-model AUC={roc_auc_score(yte, base_p):.3f}   market AUC={roc_auc_score(yte, market):.3f}")
    except Exception: pass

    def trade(est, market_p, outcome):
        """One bet given an estimate vs the market; spread on entry, mark to 0/1."""
        if not (BAND[0] <= market_p <= BAND[1]):
            return None
        if abs(est - market_p) < EDGE_MIN:
            return None
        if est > market_p:
            return outcome - (market_p + SPREAD / 2)          # buy YES
        return (1 - outcome) - ((1 - market_p) + SPREAD / 2)   # buy NO

    def report(name, picker):
        pnl = n = w = 0.0
        for i in range(len(yte)):
            r = picker(i)
            if r is None:
                continue
            pnl += r; n += 1; w += 1 if r > 0 else 0
        line = f" {name:<26}"
        if n: line += f"trades={int(n):3d}  win={w/n:4.0%}  $/trade={pnl/n:+.4f}  total={pnl:+.2f}"
        else: line += "trades=0 (never diverged enough)"
        print(line)
        return {"trades": int(n), "win": round(w/n, 3) if n else None,
                "pnl_per_trade": round(pnl/n, 4) if n else None}

    print("-" * 60)
    res = {}
    res["A_blend"] = report("A) blend divergence",
        lambda i: trade(blend[i], market[i], yte[i]))
    def consensus(i):
        side_yes = all(probs[n][i] > market[i] for n in VIEWS)
        side_no  = all(probs[n][i] < market[i] for n in VIEWS)
        if not (side_yes or side_no):
            return None
        return trade(blend[i], market[i], yte[i])
    res["B_consensus"] = report("B) consensus + divergence", consensus)
    res["C_disagree_gated"] = report("C) divergence, agree-gated",
        lambda i: trade(blend[i], market[i], yte[i]) if disagree[i] <= DIS_THRESH else None)
    res["baseline_single"] = report("baseline single model", lambda i: trade(base_p[i], market[i], yte[i]))
    # ALLIN control
    allin_pnl = n_allin = 0
    for i in range(len(yte)):
        if BAND[0] <= market[i] <= BAND[1]:
            n_allin += 1; allin_pnl += (yte[i] - (market[i] + SPREAD/2))
    if n_allin: print(f" {'ALLIN control':<26}trades={n_allin:3d}  $/trade={allin_pnl/n_allin:+.4f}")
    print("-" * 60)
    best = max((v["pnl_per_trade"] or -9, k) for k, v in res.items())
    print(f" best rule: {best[1]}  @ {best[0]:+.4f}/trade")
    print(" NOTE: if no rule clearly beats the single-model baseline AND the market,")
    print(" the debate added theatre, not edge. The independence is the test.")
    print("============================================================\n")
    res["allin_pnl_per_trade"] = round(allin_pnl/n_allin, 4) if n_allin else None
    res["cutoff_frac"] = CUTOFF_FRAC; res["test_markets"] = int(te["condition_id"].nunique())
    json.dump(res, open(os.path.join(HERE, "analyst_panel_result.json"), "w"), indent=2)


if __name__ == "__main__":
    main()
