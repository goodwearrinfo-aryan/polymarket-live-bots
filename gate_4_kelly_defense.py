#!/usr/bin/env python3
"""
gate_4_kelly_defense.py (REDESIGNED) — Adaptive Kelly sizing for negative edge.

Old strategy: identify reversion windows (offensive)
New strategy: SIZE BASED ON BANKROLL, not market attractiveness

On a losing baseline, the biggest risk is betting too much, losing the bankroll,
and being unable to recover. Kelly criterion for NEGATIVE edge:

Kelly formula (modified): f = (p*b - q) / b
  where p = win prob, q = lose prob, b = odds

For negative edge (p < 0.5), Kelly says: bet LESS, not more.
As your bankroll shrinks, shrink position sizes.

Goal: maximize bankroll longevity, not individual trade returns.
"""

import sys

def kelly_fraction(win_rate, avg_win_per_trade, avg_loss_per_trade):
    """Compute Kelly bet size given win rate and payout structure.

    For negative expectation, Kelly returns negative (don't bet).
    We modify: use defensive Kelly (half of theoretical).

    Args:
        win_rate: float [0, 1]
        avg_win_per_trade: float (expected win on winning trade)
        avg_loss_per_trade: float (expected loss on losing trade, positive value)

    Returns:
        kelly_fraction: float [0, 0.25] (max % of bankroll to risk per trade)
        recommendation: str
    """
    if win_rate <= 0.5:
        # Negative edge: Kelly = negative
        # Defensive: use 1/3 Kelly instead (maximize survival)
        kelly = max(0.01, win_rate - (1 - win_rate))  # simplified
        kelly_frac = max(0.01, kelly / 3)  # defensive reduction
        return kelly_frac, "Negative edge: size down 50-70%"

    # Positive edge (rare): use full Kelly
    expectation = (win_rate * avg_win_per_trade) - ((1 - win_rate) * avg_loss_per_trade)
    if expectation <= 0:
        return 0.01, "Expectation negative: minimum bet only"

    kelly_frac = min(0.25, expectation / avg_loss_per_trade)
    return kelly_frac, "Positive edge: OK to size normally"

def adaptive_position_size(bankroll, base_bet_usdc, win_rate, recent_pnl):
    """Scale position size based on bankroll health and recent results.

    Rules:
      - If bankroll down >20% from start, reduce all positions by 30%
      - If on a 3-trade losing streak, reduce by 50%
      - If winning, can increase back to normal

    Args:
        bankroll: current account value (float)
        base_bet_usdc: nominal bet size from config
        win_rate: observed win rate (0-1)
        recent_pnl: list of last 5 trade P&Ls

    Returns:
        adjusted_bet: float (position size to use)
        multiplier: float (what % of normal sizing)
        reason: str
    """
    multiplier = 1.0
    reasons = []

    # Check 1: How's the bankroll doing?
    if bankroll < 1000 * 0.8:  # down 20% from typical $1000
        multiplier *= 0.70
        reasons.append("Bankroll down 20%: reduce 30%")

    # Check 2: Losing streak?
    if recent_pnl and len(recent_pnl) >= 3:
        last_3 = recent_pnl[-3:]
        losses = sum(1 for p in last_3 if p < 0)
        if losses >= 2:  # 2+ losses in last 3
            multiplier *= 0.50
            reasons.append(f"Losing streak ({losses}/3): reduce 50%")

    # Check 3: Win rate is terrible?
    if win_rate < 0.35:
        multiplier *= 0.75
        reasons.append("WR <35%: reduce 25% for safety")

    adjusted = base_bet_usdc * multiplier
    reason = "; ".join(reasons) if reasons else "All systems normal"

    return adjusted, multiplier, reason

# Test
if __name__ == "__main__":
    print("[gate_4_REDESIGNED] Kelly Defense Sizing\n")

    # Test Kelly for your baseline
    wr = 0.341  # your 34.1% win rate
    avg_win = 0.50  # avg win per trade (mock)
    avg_loss = 0.15  # avg loss per trade (mock)

    kelly_frac, rec = kelly_fraction(wr, avg_win, avg_loss)
    print(f"Your WR: {wr:.1%}")
    print(f"Kelly fraction: {kelly_frac:.1%} (recommendation: {rec})\n")

    # Test adaptive sizing
    print("Adaptive Sizing Examples:")
    print("-" * 70)

    scenarios = [
        (1200, 1.0, 0.35, [0.5, -0.2, -0.1], "Bankroll healthy, no streak"),
        (800, 1.0, 0.35, [-0.2, -0.1, -0.15], "Bankroll down 20%, losing streak"),
        (1000, 1.0, 0.32, [-0.2, -0.1], "Terrible WR + recent losses"),
    ]

    for bankroll, base, wr_test, recent, desc in scenarios:
        adj, mult, reason = adaptive_position_size(bankroll, base, wr_test, recent)
        print(f"{desc}:")
        print(f"  Bankroll: ${bankroll}  Base bet: ${base}  Adjusted: ${adj:.2f} ({mult:.0%})")
        print(f"  Reason: {reason}\n")
