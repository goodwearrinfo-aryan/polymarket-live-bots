#!/usr/bin/env python3
"""
gate_1_aggressive_kelly.py (HIGH RISK/REWARD) — Size BIG on high-conviction trades.

Philosophy: You only trade when you're SURE. When sure, go all-in.
- Conviction 90%+: bet 5% of bankroll (aggressive Kelly)
- Conviction 80-90%: bet 3% of bankroll
- Conviction <80%: skip entirely

Expected: 70% of bets are winners (on your conviction picks), 30% are max loss.
Risk: one bad conviction = -30% of bet size.
Reward: winners run 3-4x (tight stop, let it ride).
"""

import sys

def conviction_to_kelly_fraction(conviction_pct):
    """Map conviction → Kelly bet size (aggressive).

    Conviction <50%: don't trade (no edge)
    50-70%: skip (weak conviction)
    70-80%: 1.5% bankroll (small)
    80-90%: 3% bankroll (medium)
    90%+: 5% bankroll (aggressive all-in)
    """
    if conviction_pct < 0.70:
        return 0.0, "SKIP — conviction <70%, no edge"
    elif conviction_pct < 0.80:
        return 0.015, "Small: 1.5% bankroll"
    elif conviction_pct < 0.90:
        return 0.03, "Medium: 3% bankroll"
    else:
        return 0.05, "AGGRESSIVE: 5% bankroll (all-in)"

def aggressive_position_size(bankroll, conviction_pct, market_vol=None):
    """Size position for high conviction, aggressive upside.

    Args:
        bankroll: current account value
        conviction_pct: 0-1, your confidence in this trade
        market_vol: optional, scale down if market is thin

    Returns:
        position_usdc: float (bet size)
        kelly_frac: float (% of bankroll)
        reason: str
    """
    kelly, reason = conviction_to_kelly_fraction(conviction_pct)

    if kelly == 0.0:
        return 0.0, 0.0, reason

    # Base position
    position = bankroll * kelly

    # If market is thin, scale down slightly (avoid slippage death)
    if market_vol and market_vol < 100_000:
        position *= 0.75
        reason += " (thin market: -25%)"

    return position, kelly, reason

# Test
if __name__ == "__main__":
    print("[gate_1_AGGRESSIVE_KELLY] Size Big on High Conviction\n")

    scenarios = [
        (10000, 0.92, 300_000, "95% confidence, deep book"),
        (10000, 0.75, 150_000, "75% confidence, moderate book"),
        (10000, 0.65, 50_000, "65% confidence (too low, skip)"),
    ]

    for bankroll, conv, vol, desc in scenarios:
        pos, kelly, reason = aggressive_position_size(bankroll, conv, vol)
        print(f"{desc}:")
        print(f"  Position: ${pos:,.0f} ({kelly:.1%} of bankroll) — {reason}\n")
