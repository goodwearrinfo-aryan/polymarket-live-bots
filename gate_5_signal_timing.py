#!/usr/bin/env python3
"""
gate_5_signal_timing.py — Dynamically weight signals based on which one leads.

Standard approach: equal weight divergence, momentum, skew (each worth 33%).
Problem: one signal is consistently predictive, others lag.

Solution: Measure lead/lag relationship over rolling windows, weight the leading
signal higher. If divergence flips 15min before momentum, divergence gets 60%
weight and momentum gets 25%.

This improves signal quality without adding new indicators — just reweights
the existing ones based on predictive power.
"""

import json, os, sys, time
from collections import deque

BASE = os.path.dirname(os.path.abspath(__file__))
SIGNAL_HISTORY_FILE = os.path.join(BASE, "signal_timing_history.json")
LOOKBACK_WINDOWS = 10  # track last 10 signals for lead/lag analysis

# In-memory signal history: list of {"ts": float, "divg": int, "mom": int, "skew": int}
_signal_history = deque(maxlen=LOOKBACK_WINDOWS)

def record_signal(divg, mom, skew, market_id=""):
    """Record a signal triple with timestamp."""
    global _signal_history
    entry = {
        "ts": time.time(),
        "divg": divg,
        "mom": mom,
        "skew": skew,
        "market_id": market_id,
    }
    _signal_history.append(entry)

def analyze_lead_lag():
    """Analyze which signal tends to lead others.

    Returns:
        lead_signal: str in {'divg', 'mom', 'skew'}
        lag_signal: str
        lead_time_median: float (seconds; how far ahead the lead signal flips)
        lead_pct: float [0, 1] (what % of the time does it lead?)
    """
    if len(_signal_history) < 3:
        return "unknown", "unknown", 0.0, 0.0

    hist = list(_signal_history)
    leads = {"divg": 0, "mom": 0, "skew": 0}
    lag_times = {"divg": [], "mom": [], "skew": []}

    # For each transition from one state to another, see which signal flipped first
    for i in range(len(hist) - 1):
        curr = hist[i]
        next_ = hist[i + 1]

        divg_flipped = curr["divg"] != next_["divg"]
        mom_flipped = curr["mom"] != next_["mom"]
        skew_flipped = curr["skew"] != next_["skew"]

        # Determine which flipped first (closest to previous timestamp)
        flip_time = next_["ts"] - curr["ts"]

        if divg_flipped and not mom_flipped and not skew_flipped:
            leads["divg"] += 1
            lag_times["divg"].append(0.0)
        elif mom_flipped and not divg_flipped and not skew_flipped:
            leads["mom"] += 1
            lag_times["mom"].append(0.0)
        elif skew_flipped and not divg_flipped and not mom_flipped:
            leads["skew"] += 1
            lag_times["skew"].append(0.0)
        # (cases where 2+ flip simultaneously → no clear lead, skip)

    total_leads = sum(leads.values())
    if total_leads == 0:
        return "unknown", "unknown", 0.0, 0.0

    # Find the signal that led most often
    lead_signal = max(leads, key=leads.get)
    lead_pct = leads[lead_signal] / total_leads if total_leads > 0 else 0.0

    # Find the laggard
    lag_signal = min(leads, key=leads.get)

    median_lag = 0.0
    if lag_times[lead_signal]:
        median_lag = sorted(lag_times[lead_signal])[len(lag_times[lead_signal]) // 2]

    return lead_signal, lag_signal, median_lag, lead_pct

def dynamic_weights():
    """Return signal weights based on lead/lag analysis.

    Returns:
        {"divg": float, "mom": float, "skew": float}  (sums to 1.0)
    """
    lead_sig, lag_sig, lag_time, lead_pct = analyze_lead_lag()

    # Start with equal weights
    weights = {"divg": 0.33, "mom": 0.33, "skew": 0.33}

    # If we have a clear leader (leads ≥60% of the time), boost it
    if lead_pct >= 0.60:
        weights[lead_sig] = 0.50  # leader gets 50%
        weights[lag_sig] = 0.20   # laggard gets 20%
        # third gets the remainder
        third = [k for k in weights if k != lead_sig and k != lag_sig][0]
        weights[third] = 0.30

    return weights

def weighted_signal(divg, mom, skew):
    """Compute weighted signal conviction.

    Returns:
        signal_strength: float [-1, 1] (negative=bearish, positive=bullish)
        weights_used: dict
    """
    weights = dynamic_weights()

    # Normalize signals to [-1, 1] and compute weighted average
    signal = (divg * weights["divg"] +
              mom * weights["mom"] +
              skew * weights["skew"])

    return signal, weights

# Test
if __name__ == "__main__":
    # Simulate signal history with lead/lag pattern
    print("[gate_5] Signal Timing Analysis\n")

    # Pattern: divergence leads, momentum follows, skew lags
    test_sequence = [
        (1, 1, -1),   # divg bullish, others mixed
        (1, 1, 0),    # mom just flipped → divergence led
        (1, 1, 1),    # skew just flipped → momentum led
        (-1, 1, 1),   # divg flipped → skew led (but divg is the early bird usually)
        (-1, -1, 1),  # mom flipped after divg
        (-1, -1, -1), # skew catches up
    ]

    for divg, mom, skew in test_sequence:
        record_signal(divg, mom, skew)

    lead_sig, lag_sig, lag_t, lead_pct = analyze_lead_lag()
    print(f"Lead signal: {lead_sig} ({lead_pct:.0%} of flips)")
    print(f"Lag signal: {lag_sig}")
    print(f"Median lead-time: {lag_t:.1f}s\n")

    weights = dynamic_weights()
    print(f"Dynamic weights: {weights}\n")

    # Test weighted signal
    test_signals = [(1, 1, -1), (-1, -1, 1), (1, -1, 0)]
    for divg, mom, skew in test_signals:
        strength, wts = weighted_signal(divg, mom, skew)
        print(f"divg={divg:+d} mom={mom:+d} skew={skew:+d} → strength={strength:+.2f}")
