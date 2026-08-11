#!/usr/bin/env python3
"""
ml/backtest_oot.py — TIME-SPLIT (out-of-time) version of the divergence backtest.

Why this exists
---------------
backtest_edge.py holds out a RANDOM 20% of markets from the SAME era, so it
answers "does the edge generalize across markets" but NOT "does it survive a
later time period." This script does a TEMPORAL split instead:

  • TRAIN on markets that RESOLVED before a time cutoff.
  • TEST  on markets that RESOLVED at/after the cutoff (the model never saw them,
    and they are strictly later in time — a genuine out-of-time check).
  • Retrain from scratch on the train slice (so there is no leakage from the
    shipped model.pkl, which was fit on the whole era).
  • Trade the model's DIVERGENCE from price, charge the spread on entry, and
    compare to the ALLIN control and to price-alone AUC.

This is the strongest test you can run on the EXISTING dataset. The truly
definitive test is still a FRESH collection of markets that resolve AFTER today
(run collect_history.py later, then this script) — but this catches a regime
break within the data you already have.

Run on the Mac (needs sklearn):  python3 ml/backtest_oot.py [dataset.csv] [cutoff_frac]
  cutoff_frac (default 0.7): fraction of the resolve-time range used for training.
"""
import os, sys, csv, json, random

HERE = os.path.dirname(os.path.abspath(__file__))
DATASET = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "dataset.csv")
CUTOFF_FRAC = float(sys.argv[2]) if len(sys.argv) > 2 else 0.70

EDGE_MIN = 0.10
SPREAD = 0.02
BAND = (0.05, 0.95)


def _ensure(pkgs):
    import subprocess
    for p in pkgs:
        mod = {"scikit-learn": "sklearn"}.get(p, p)
        try:
            __import__(mod)
        except ImportError:
            print(f"installing {p}…")
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
    X = F.FEATURE_NAMES

    df = pd.read_csv(DATASET)
    df = df[df["final_outcome"].isin([0, 1]) | df["final_outcome"].astype(str).isin(["0", "1"])]
    df["final_outcome"] = df["final_outcome"].astype(int)
    df["resolve"] = df["resolve"].astype(float)

    # ── Temporal cutoff on RESOLVE time ────────────────────────────────────
    rmin, rmax = df["resolve"].min(), df["resolve"].max()
    cutoff = rmin + (rmax - rmin) * CUTOFF_FRAC
    tr = df[df["resolve"] < cutoff]
    te = df[df["resolve"] >= cutoff]
    # one row per TEST market (latest as_of) = one independent, later-in-time bet
    te = te.sort_values("as_of").groupby("condition_id", as_index=False).tail(1)

    import datetime as _dt
    def d(ts): return _dt.datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d")
    print("\n============== OUT-OF-TIME (TEMPORAL) BACKTEST ==============")
    print(f" resolve range {d(rmin)} → {d(rmax)}   cutoff {d(cutoff)} (frac={CUTOFF_FRAC})")
    print(f" train markets={tr['condition_id'].nunique()}  rows={len(tr)}")
    print(f" test  markets={te['condition_id'].nunique()}  rows={len(te)}  (all resolve AFTER cutoff)")
    if len(te) < 20 or tr["final_outcome"].nunique() < 2 or te["final_outcome"].nunique() < 2:
        print(" ⚠️  Too few later-period markets to trust this — collect a fresh batch and rerun.")
        if len(te) == 0:
            print("============================================================\n"); return

    # ── Retrain on the EARLIER slice only, calibrate on a held-out part ─────
    cids = sorted(tr["condition_id"].unique().tolist())
    random.Random(7).shuffle(cids)
    cut = max(1, int(len(cids) * 0.75))
    fit_df = tr[tr["condition_id"].isin(set(cids[:cut]))]
    cal_df = tr[~tr["condition_id"].isin(set(cids[:cut]))]

    base = HistGradientBoostingClassifier(
        max_iter=300, learning_rate=0.05, max_leaf_nodes=31,
        min_samples_leaf=30, l2_regularization=1.0, random_state=42)
    base.fit(fit_df[X].astype(float).values, fit_df["final_outcome"].values)

    model = base
    if len(cal_df) and cal_df["final_outcome"].nunique() > 1:
        try:
            from sklearn.frozen import FrozenEstimator
            model = CalibratedClassifierCV(FrozenEstimator(base), method="isotonic")
        except Exception:
            model = CalibratedClassifierCV(base, method="isotonic", cv=3)
        model.fit(cal_df[X].astype(float).values, cal_df["final_outcome"].values)

    yte = te["final_outcome"].values
    mp = model.predict_proba(te[X].astype(float).values)[:, 1]
    price = te["prob"].astype(float).values

    price_auc = roc_auc_score(yte, price) if len(set(yte)) > 1 else float("nan")
    model_auc = roc_auc_score(yte, mp) if len(set(yte)) > 1 else float("nan")

    div_pnl = div_n = div_w = 0.0
    allin_pnl = n_allin = 0
    for p_market, p_model, outcome in zip(price, mp, yte):
        if not (BAND[0] <= p_market <= BAND[1]):
            continue
        n_allin += 1
        allin_pnl += (outcome - (p_market + SPREAD / 2))
        if abs(p_model - p_market) < EDGE_MIN:
            continue
        if p_model > p_market:
            entry = p_market + SPREAD / 2; pnl = outcome - entry
        else:
            entry = (1 - p_market) + SPREAD / 2; pnl = (1 - outcome) - entry
        div_pnl += pnl; div_n += 1; div_w += 1 if pnl > 0 else 0

    print("-" * 60)
    print(f" price-alone AUC = {price_auc:.3f}   model AUC = {model_auc:.3f}   edge = {model_auc-price_auc:+.3f}")
    if div_n:
        print(f" DIVERGENCE: trades={int(div_n)}  win={div_w/div_n:.0%}  "
              f"total={div_pnl:+.3f}  per-trade={div_pnl/div_n:+.4f}")
    else:
        print(" DIVERGENCE: 0 trades (model never disagreed with price by >= edge_min)")
    if n_allin:
        print(f" ALLIN control: trades={n_allin}  per-trade={allin_pnl/n_allin:+.4f}")
    print("-" * 60)
    if div_n and div_pnl / div_n > 0 and (not n_allin or div_pnl / div_n > allin_pnl / n_allin):
        print(" VERDICT: edge SURVIVES out-of-time — divergence is +EV later in time AND beats allin.")
    elif div_n:
        print(" VERDICT: edge does NOT survive out-of-time — it was a same-era / regime artifact.")
    print("============================================================\n")

    json.dump({"cutoff_frac": CUTOFF_FRAC, "train_markets": int(tr["condition_id"].nunique()),
               "test_markets": int(te["condition_id"].nunique()),
               "price_auc": None if price_auc != price_auc else round(float(price_auc), 4),
               "model_auc": None if model_auc != model_auc else round(float(model_auc), 4),
               "div_trades": int(div_n),
               "div_pnl_per_trade": round(div_pnl / div_n, 4) if div_n else None,
               "div_win": round(div_w / div_n, 3) if div_n else None,
               "allin_pnl_per_trade": round(allin_pnl / n_allin, 4) if n_allin else None},
              open(os.path.join(HERE, "backtest_oot_result.json"), "w"), indent=2)


if __name__ == "__main__":
    main()
