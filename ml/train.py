#!/usr/bin/env python3
"""
ml/train.py — Phase-1 HONEST evaluation.

Fixes the two foundation bugs found on 2026-05-31:
  1. Pseudo-replication: the same market is sampled many times (was ~7x). A random
     or time split then leaks the SAME market into train AND test, inflating AUC.
     -> We now split BY MARKET (group-aware): no condition_id in both sides.
  2. No baseline: a model can score high just by echoing the market price.
     -> We now report PRICE-ALONE AUC as the bar the model must beat.

Two questions:
  A) MOVE  model: predict whether price moves >5% in 24h (volatility / timing).
  B) DIRECTION test (the real edge): predict the final outcome. If the model's
     grouped AUC does NOT beat price-alone AUC, there is NO directional edge.

Self-installs numpy/pandas/scikit-learn. Usage: python3 train.py [dataset.csv]
"""
import os, sys, subprocess, json, pickle, random

HERE = os.path.dirname(os.path.abspath(__file__))


def _ensure(pkgs):
    for p in pkgs:
        mod = {"scikit-learn": "sklearn"}.get(p, p)
        try:
            __import__(mod)
        except ImportError:
            print(f"installing {p}…")
            subprocess.run([sys.executable, "-m", "pip", "install", p,
                            "--break-system-packages", "-q"], check=False)


_ensure(["numpy", "pandas", "scikit-learn"])

import numpy as np, pandas as pd                              # noqa: E402
from sklearn.ensemble import HistGradientBoostingClassifier   # noqa: E402
from sklearn.calibration import CalibratedClassifierCV         # noqa: E402
from sklearn.metrics import roc_auc_score, brier_score_loss    # noqa: E402
import features as F                                          # noqa: E402

DATASET = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "dataset.csv")
MODEL = os.path.join(HERE, "model.pkl")
META = os.path.join(HERE, "model_meta.json")


def group_split(df, frac=0.8, seed=42):
    """Split by market so no condition_id appears in both train and test."""
    cids = sorted(df["condition_id"].unique().tolist())
    random.Random(seed).shuffle(cids)
    cut = max(1, int(len(cids) * frac))
    train_cids = set(cids[:cut])
    tr = df[df["condition_id"].isin(train_cids)]
    te = df[~df["condition_id"].isin(train_cids)]
    return tr, te


def auc_safe(y, p):
    return roc_auc_score(y, p) if len(set(y)) > 1 else float("nan")


def bootstrap_edge(y, p_model, p_price, n=2000, seed=0):
    """Paired bootstrap of AUC(model) - AUC(price) on the test set.
    Resamples test rows with replacement; returns (mean, lo95, hi95, P(edge>0)).
    If the 95% CI includes 0, the ranking edge is NOT distinguishable from noise.
    Added 2026-06-01 to stop reporting a bare AUC with no significance."""
    yy = np.asarray(y); pm = np.asarray(p_model, float); pp = np.asarray(p_price, float)
    rng = np.random.default_rng(seed); N = len(yy); gaps = []
    for _ in range(n):
        idx = rng.integers(0, N, N)
        if len(set(yy[idx].tolist())) < 2:
            continue
        gaps.append(auc_safe(yy[idx], pm[idx]) - auc_safe(yy[idx], pp[idx]))
    if not gaps:
        return float("nan"), float("nan"), float("nan"), float("nan")
    g = np.sort(np.array(gaps))
    return (float(g.mean()), float(np.quantile(g, 0.025)),
            float(np.quantile(g, 0.975)), float((g > 0).mean()))


def fit_model():
    return HistGradientBoostingClassifier(
        max_iter=300, learning_rate=0.05, max_leaf_nodes=31,
        min_samples_leaf=30, l2_regularization=1.0, random_state=42)


def main():
    if not os.path.exists(DATASET):
        sys.exit(f"No dataset at {DATASET}. Run collect_history.py first.")
    df = pd.read_csv(DATASET)
    n, ndist = len(df), df["condition_id"].nunique()
    print("\n================ PHASE-1 HONEST EVAL ================")
    print(f" rows={n}  distinct markets={ndist}  (pseudo-replication ~{n/max(ndist,1):.1f}x)")
    if ndist < 60:
        print(f" ⚠️  Only {ndist} distinct markets — NOTHING here is trustworthy.")
        print(f"     Collect many more (aim 300+ distinct) before believing any number.")

    tr, te = group_split(df)
    print(f" group split -> train markets={tr['condition_id'].nunique()}  "
          f"test markets={te['condition_id'].nunique()}")
    X = F.FEATURE_NAMES

    # ── A) MOVE model (volatility/timing) ──────────────────────────────
    atr = tr.dropna(subset=["label_move"]); ate = te.dropna(subset=["label_move"])
    move_auc = float("nan")
    if len(set(atr["label_move"].astype(int))) > 1 and len(ate):
        m = fit_model(); m.fit(atr[X].astype(float).values, atr["label_move"].astype(int).values)
        p = m.predict_proba(ate[X].astype(float).values)[:, 1]
        move_auc = auc_safe(ate["label_move"].astype(int).values, p)
    print(f"\n A) MOVE model  (will price move >5%/24h):  grouped AUC = {move_auc:.3f}")
    print(f"    (0.5 = no signal. Knowing it'll move isn't direction — not directly tradable.)")

    # ── B) DIRECTION edge test + CALIBRATION (the one that matters) ────
    # Split train markets again into base-fit (75%) and calibration (25%),
    # group-aware, so calibration is learned on markets the base model didn't see.
    btr = tr.dropna(subset=["final_outcome"]); bte = te.dropna(subset=["final_outcome"])
    direction = {"price_auc": None, "model_auc": None, "edge": None,
                 "brier_raw": None, "brier_cal": None}
    cal_md = None
    if len(bte) and len(set(btr["final_outcome"].astype(int))) > 1 \
            and len(set(bte["final_outcome"].astype(int))) > 1:
        b_cids = sorted(btr["condition_id"].unique().tolist())
        random.Random(7).shuffle(b_cids)
        cut = max(1, int(len(b_cids) * 0.75))
        fit_cids = set(b_cids[:cut])
        base_df = btr[btr["condition_id"].isin(fit_cids)]
        cal_df = btr[~btr["condition_id"].isin(fit_cids)]

        yte = bte["final_outcome"].astype(int).values
        price_auc = auc_safe(yte, bte["prob"].astype(float).values)        # the bar

        base = fit_model()
        base.fit(base_df[X].astype(float).values, base_df["final_outcome"].astype(int).values)
        raw_p = base.predict_proba(bte[X].astype(float).values)[:, 1]
        model_auc = auc_safe(yte, raw_p)
        brier_raw = brier_score_loss(yte, raw_p) if len(set(yte)) > 1 else float("nan")

        # calibrate probabilities (isotonic) on the held-out calib markets.
        # sklearn >=1.6 removed cv="prefit" -> wrap the fitted base in FrozenEstimator.
        brier_cal = float("nan")
        if len(cal_df) and len(set(cal_df["final_outcome"].astype(int))) > 1:
            try:
                from sklearn.frozen import FrozenEstimator
                cal_md = CalibratedClassifierCV(FrozenEstimator(base), method="isotonic")
            except Exception:
                cal_md = CalibratedClassifierCV(base, method="isotonic", cv=3)  # fallback (refits via CV)
            cal_md.fit(cal_df[X].astype(float).values, cal_df["final_outcome"].astype(int).values)
            cal_p = cal_md.predict_proba(bte[X].astype(float).values)[:, 1]
            brier_cal = brier_score_loss(yte, cal_p) if len(set(yte)) > 1 else float("nan")

        edge = model_auc - price_auc
        b_mean, b_lo, b_hi, b_pos = bootstrap_edge(yte, raw_p, bte["prob"].astype(float).values)
        direction = {"price_auc": round(float(price_auc), 4),
                     "model_auc": round(float(model_auc), 4),
                     "edge": round(float(edge), 4),
                     "edge_ci95": [round(b_lo, 4), round(b_hi, 4)],
                     "edge_p_gt0": round(b_pos, 4),
                     "brier_raw": round(float(brier_raw), 4),
                     "brier_cal": None if (brier_cal != brier_cal) else round(float(brier_cal), 4)}
        print(f"\n B) DIRECTION (predict final outcome), grouped:")
        print(f"    price-alone AUC : {price_auc:.3f}   <- the bar to beat")
        print(f"    model AUC       : {model_auc:.3f}   EDGE {edge:+.3f}  (ranking; calibration doesn't change this)")
        print(f"    bootstrap edge  : mean {b_mean:+.3f}  95% CI [{b_lo:+.3f}, {b_hi:+.3f}]  P(edge>0)={b_pos:.0%}")
        print(f"    Brier  raw={brier_raw:.4f}  calibrated={brier_cal:.4f}  (lower=better; calibration should drop it)")
        edge_real = (b_lo > 0)   # CI must EXCLUDE zero, not just point-estimate > 0.02
        if edge_real:
            print(f"    -> edge CI excludes 0 AND point edge {edge:+.3f}: plausibly real.")
        else:
            print(f"    -> edge CI INCLUDES 0: NOT distinguishable from noise (point edge {edge:+.3f} is a mirage).")
    else:
        print("\n B) DIRECTION: not enough varied outcomes in the grouped test set to evaluate.")

    print("====================================================\n")

    json.dump({"features": F.FEATURE_NAMES, "rows": int(n), "distinct_markets": int(ndist),
               "move_auc": None if np.isnan(move_auc) else round(float(move_auc), 4),
               "direction": direction, "split": "group-aware-by-market",
               "trained_at": pd.Timestamp.now().isoformat()}, open(META, "w"), indent=2)
    # save the CALIBRATED direction model only if it beat price on ranking.
    # Gate raised 0.02 -> 0.06 on 2026-06-01: price-alone AUC swings ~0.03 just
    # from row replication, so a 0.02 "edge" is within noise and was re-blessing
    # a broken model nightly. 0.06 requires a real, above-noise ranking edge.
    _ci_lo = (direction.get("edge_ci95") or [None])[0]
    _significant = _ci_lo is not None and _ci_lo == _ci_lo and _ci_lo > 0   # CI excludes 0 (and not NaN)
    if direction["edge"] and direction["edge"] > 0.06 and _significant and cal_md is not None:
        with open(MODEL, "wb") as f:
            pickle.dump({"model": cal_md, "features": F.FEATURE_NAMES,
                         "target": "final_outcome", "calibrated": "isotonic"}, f)
        print(f"saved CALIBRATED candidate-edge model -> {MODEL}")
    else:
        print("no edge model saved (no ranking edge, or calibration unavailable).")


if __name__ == "__main__":
    main()
