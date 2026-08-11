"""
ml/features.py — shared feature engineering for the Polymarket ML model.

Same code path is used for (a) building the historical training set and
(b) scoring live markets, so train/serve features stay identical.

A "price series" is a list of (timestamp_seconds, probability) points, oldest
first, for ONE token (the YES side). Everything is computed relative to an
"as-of" index t so we never peek into the future when labeling.
"""
from __future__ import annotations
import math
from typing import List, Tuple, Optional, Dict

Point = Tuple[int, float]   # (unix_seconds, prob)

FEATURE_NAMES = [
    "prob",            # current probability (YES price)
    "chg_1h",          # change over last 1h
    "chg_6h",          # change over last 6h
    "chg_24h",         # change over last 24h
    "vol_24h",         # realized volatility (std of prob) over last 24h
    "drift_24h",       # mean per-step drift over last 24h
    "range_24h",       # max-min over last 24h (proxy for order-book churn)
    "n_points_24h",    # how many ticks in last 24h (activity proxy)
    "time_to_res_h",   # hours until resolution
    "log_volume",      # log1p(market volume usd)
    "log_liquidity",   # log1p(market liquidity usd)
    "dist_from_50",    # |prob - 0.5|  (longshot vs coinflip)
    "news_sentiment",  # optional; 0.0 if unavailable
]


def _at_or_before(series: List[Point], t_sec: int) -> Optional[float]:
    """Last prob at or before t_sec."""
    val = None
    for ts, p in series:
        if ts <= t_sec:
            val = p
        else:
            break
    return val


def _window(series: List[Point], t_sec: int, hours: float) -> List[Point]:
    lo = t_sec - int(hours * 3600)
    return [(ts, p) for ts, p in series if lo <= ts <= t_sec]


def _std(xs: List[float]) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    m = sum(xs) / n
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))


def compute(series: List[Point], as_of_sec: int, resolve_sec: int,
            volume_usd: float = 0.0, liquidity_usd: float = 0.0,
            news_sentiment: float = 0.0) -> Optional[Dict[str, float]]:
    """Return the feature dict as-of `as_of_sec`, or None if not enough data."""
    series = sorted(series)
    prob = _at_or_before(series, as_of_sec)
    if prob is None:
        return None

    def chg(h):
        past = _at_or_before(series, as_of_sec - int(h * 3600))
        return (prob - past) if past is not None else 0.0

    w24 = _window(series, as_of_sec, 24)
    probs24 = [p for _, p in w24]
    feats = {
        "prob": prob,
        "chg_1h": chg(1),
        "chg_6h": chg(6),
        "chg_24h": chg(24),
        "vol_24h": _std(probs24),
        "drift_24h": (probs24[-1] - probs24[0]) / max(len(probs24) - 1, 1) if len(probs24) > 1 else 0.0,
        "range_24h": (max(probs24) - min(probs24)) if probs24 else 0.0,
        "n_points_24h": float(len(w24)),
        "time_to_res_h": max(0.0, (resolve_sec - as_of_sec) / 3600.0),
        "log_volume": math.log1p(max(0.0, volume_usd)),
        "log_liquidity": math.log1p(max(0.0, liquidity_usd)),
        "dist_from_50": abs(prob - 0.5),
        "news_sentiment": float(news_sentiment),
    }
    return feats


def label_move(series: List[Point], as_of_sec: int, horizon_h: float = 24.0,
               threshold: float = 0.05) -> Optional[int]:
    """1 if |prob(t+horizon) - prob(t)| > threshold, else 0. None if no future point."""
    series = sorted(series)
    p0 = _at_or_before(series, as_of_sec)
    p1 = _at_or_before(series, as_of_sec + int(horizon_h * 3600))
    if p0 is None or p1 is None:
        return None
    # require an actual future tick beyond as_of (not just the same last point)
    if not any(ts > as_of_sec for ts, _ in series):
        return None
    return 1 if abs(p1 - p0) > threshold else 0


def row_vector(feats: Dict[str, float]) -> List[float]:
    return [feats.get(k, 0.0) for k in FEATURE_NAMES]
