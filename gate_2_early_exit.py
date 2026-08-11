#!/usr/bin/env python3
"""
gate_2_early_exit.py (REDESIGNED) — Kill trades early when conviction drops.

Old strategy: convergence inversion (fancy signal weighting)
New strategy: CUT LOSSES FAST when the thesis is broken

On a losing baseline, every losing trade you hold costs you. Stop losses are your friend.

Rules:
  - If position is underwater >3%, check: is conviction still high?
  - If conviction drops (price moved opposite direction >5c), kill it immediately
  - This caps max loss per trade, cuts bad trades early

Example: buy YES at 0.40, it moves to 0.35 (down 5c). Your thesis said 0.55.
The move AGAINST you suggests thesis was wrong. Exit now instead of hoping.
"""

import sys

def check_early_exit(open_position, current_market_data):
    """Check if a live position should be force-exited.

    Args:
        open_position: dict with 'entry_mid', 'side', 'size', 'entry_time'
        current_market_data: dict with 'yes' (current price), 'id'

    Returns:
        should_exit: bool
        reason: str
        loss_so_far: float (estimated)
    """
    entry_mid = open_position.get("entry_mid", 0.5)
    side = open_position.get("side", "YES")
    current_yes = current_market_data.get("yes", 0.5)

    # Compute current mid-price mark
    if side == "YES":
        current_mid = current_yes
        direction = "up" if current_yes > entry_mid else "down"
        move = current_yes - entry_mid
    else:  # NO
        current_mid = 1 - current_yes
        direction = "down" if current_yes < entry_mid else "up"
        move = entry_mid - current_yes

    # Check conviction drop: if market moved >5c AGAINST our side, thesis broken
    if move < -0.05:
        return True, f"Thesis broken: {direction} {abs(move)*100:.0f}c against us", move

    # Check hard stop: if loss > 3%, cut it
    entry_price = entry_mid + 0.01 if side == "YES" else entry_mid - 0.01  # assume filled at mid+spread
    current_price = current_mid
    loss_pct = (current_price - entry_price) / entry_price if entry_price > 0 else 0

    if loss_pct < -0.03:  # down >3%
        return True, f"Hard stop: down {loss_pct:.1%}", loss_pct

    return False, "Position healthy", 0.0

# Test
if __name__ == "__main__":
    test_scenarios = [
        {
            "open": {"entry_mid": 0.40, "side": "YES", "size": 100},
            "current": {"yes": 0.35},
            "desc": "YES at 0.40 → now 0.35 (down 5c, thesis broken)"
        },
        {
            "open": {"entry_mid": 0.60, "side": "NO", "size": 50},
            "current": {"yes": 0.65},
            "desc": "NO at 0.40 → YES now 0.65 (thesis broken)"
        },
        {
            "open": {"entry_mid": 0.50, "side": "YES", "size": 75},
            "current": {"yes": 0.48},
            "desc": "YES at 0.50 → now 0.48 (small loss, thesis intact)"
        },
    ]

    print("[gate_2_REDESIGNED] Early Exit on Thesis Break\n")
    for scenario in test_scenarios:
        should_exit, reason, loss = check_early_exit(scenario["open"], scenario["current"])
        print(f"{scenario['desc']}: {'EXIT' if should_exit else 'HOLD'} — {reason}")
