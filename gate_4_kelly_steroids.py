#!/usr/bin/env python3
"""
gate_4_kelly_steroids.py (HIGH RISK/REWARD) — Scale UP when winning.

Philosophy: Kelly criterion for POSITIVE edge + winning streak = size up.
- When up 20%+: increase position size 25% (compound your edge)
- When down 20%: back to baseline (rest)
- When on 3-trade winning streak: size up 15% (momentum)

Effect: Your winners compound. Your losers are capped at -30%.
This creates a positive skew (big wins >> small losses).
"""

import sys

def kelly_multiplier_aggressive(bankroll, starting_bankroll, recent_pnl):
    """Scale position size based on winning momentum.

    Args:
        bankroll: current account value
        starting_bankroll: initial value (to measure gain)
        recent_pnl: list of last 5 trade P&Ls

    Returns:
        multiplier: float (scale to apply to base position)
        reason: str
    """
    # Check 1: Bankroll up significantly?
    gain_pct = (bankroll - starting_bankroll) / starting_bankroll if starting_bankroll > 0 else 0

    if gain_pct > 0.20:
        multiplier = 1.25
        reason = f"Up {gain_pct:.0%}: scale up 25%"
    elif gain_pct > 0.10:
        multiplier = 1.10
        reason = f"Up {gain_pct:.0%}: scale up 10%"
    elif gain_pct < -0.20:
        multiplier = 0.85
        reason = f"Down {gain_pct:.0%}: scale down, rest"
    else:
        multiplier = 1.0
        reason = "Baseline"

    # Check 2: Winning streak?
    if recent_pnl and len(recent_pnl) >= 3:
        last_3 = recent_pnl[-3:]
        if all(p > 0 for p in last_3):
            multiplier *= 1.15
            reason += " + 3-win streak (+15%)"

    return multiplier, reason

def position_size_aggressive(base_bet, bankroll, starting_bankroll, recent_pnl):
    """Compute aggressive position size.

    Args:
        base_bet: nominal bet (e.g., 5% of bankroll for high conviction)
        bankroll: current account
        starting_bankroll: starting value
        recent_pnl: list of recent P&Ls

    Returns:
        position_usdc: float
        multiplier: float
        details: str
    """
    mult, reason = kelly_multiplier_aggressive(bankroll, starting_bankroll, recent_pnl)
    position = base_bet * mult

    return position, mult, reason

# Test
if __name__ == "__main__":
    print("[gate_4_KELLY_STEROIDS] Scale UP When Winning\n")

    scenarios = [
        (10000, 10000, [], "Starting position (break-even)"),
        (12000, 10000, [100, 50, 75], "Up 20%, 3-win streak"),
        (8000, 10000, [], "Down 20% (backoff)"),
        (10500, 10000, [50], "Up 5%, single win"),
    ]

    base_bet = 500  # 5% of $10k

    print(f"{'Scenario':<40} {'Base Bet':<12} {'Adjusted':<12} {'Multiplier':<12}")
    print("-" * 80)

    for bankroll, start, recent, desc in scenarios:
        pos, mult, reason = position_size_aggressive(base_bet, bankroll, start, recent)
        print(f"{desc:<40} ${base_bet:<11.0f} ${pos:<11.0f} {mult:.2f}x")
        print(f"  {reason}\n")
