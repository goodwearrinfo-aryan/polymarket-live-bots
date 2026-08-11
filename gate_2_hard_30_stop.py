#!/usr/bin/env python3
"""
gate_2_hard_30_stop.py (HIGH RISK/REWARD) — HARD stop-loss at -30%.

Philosophy: You took the risk, you size big. But you DO NOT hold past -30%.
- Hit -30%, you EXIT. Full stop. No negotiation.
- This caps your loss per bet to 1.5% of bankroll (5% bet × 30% stop)
- Frees capital for the next high-conviction trade

Rule: THESIS BREAK OR -30%, whichever comes first.
Don't hope, don't average down. Kill it and move on.
"""

import sys

def should_hard_exit(open_position, current_market_data):
    """Check if position has hit the -30% hard stop.

    Args:
        open_position: {'entry_mid': float, 'entry_fill': float, 'side': str, 'size': float}
        current_market_data: {'yes': float}

    Returns:
        should_exit: bool
        loss_pct: float
        reason: str
    """
    entry_mid = open_position.get("entry_mid", 0.5)
    entry_fill = open_position.get("entry_fill", entry_mid)
    side = open_position.get("side", "YES")
    size = open_position.get("size", 0)
    current_yes = current_market_data.get("yes", 0.5)

    # Current mark
    current_mid = current_yes if side == "YES" else (1 - current_yes)

    # Loss %
    loss_pct = (current_mid - entry_fill) / entry_fill if entry_fill > 0 else 0

    # Check: have we hit -30%?
    if loss_pct <= -0.30:
        return True, loss_pct, f"HARD STOP: down {loss_pct:.0%} (exit now)"

    # Early warning at -20%
    if loss_pct <= -0.20:
        return False, loss_pct, f"⚠️ WARNING: down {loss_pct:.0%}, approaching hard stop"

    return False, loss_pct, f"OK: down {loss_pct:.0%}"

def target_exit(open_position, current_market_data, target_pnl=3.0):
    """Check if position has hit the +3x target (on winning side).

    Args:
        open_position: as above
        current_market_data: as above
        target_pnl: how many X returns before taking profit (default 3x)

    Returns:
        should_exit: bool
        gain_pct: float
        reason: str
    """
    entry_fill = open_position.get("entry_fill", 0.5)
    side = open_position.get("side", "YES")
    current_yes = current_market_data.get("yes", 0.5)

    current_mid = current_yes if side == "YES" else (1 - current_yes)
    gain_pct = (current_mid - entry_fill) / entry_fill if entry_fill > 0 else 0

    # 3x target
    if gain_pct >= (target_pnl - 1):  # e.g., 2.0 for 3x total (100% + 200% gain)
        return True, gain_pct, f"TARGET HIT: +{gain_pct:.0%}, take the win"

    # Halfway to target
    if gain_pct >= (target_pnl - 1) * 0.5:
        return False, gain_pct, f"+{gain_pct:.0%}, halfway to 3x"

    return False, gain_pct, f"+{gain_pct:.0%}"

# Test
if __name__ == "__main__":
    print("[gate_2_HARD_30_STOP] Hard Stop + 3x Target\n")

    scenarios = [
        ({"entry_mid": 0.40, "entry_fill": 0.41, "side": "YES", "size": 100},
         {"yes": 0.30}, "Down 27% (approaching stop)"),
        ({"entry_mid": 0.40, "entry_fill": 0.41, "side": "YES", "size": 100},
         {"yes": 0.27}, "Down 34% (HIT hard stop)"),
        ({"entry_mid": 0.40, "entry_fill": 0.41, "side": "YES", "size": 100},
         {"yes": 0.82}, "Up 100% (halfway to 3x)"),
        ({"entry_mid": 0.40, "entry_fill": 0.41, "side": "YES", "size": 100},
         {"yes": 1.23}, "Up 200% (target 3x hit)"),
    ]

    print(f"{'Scenario':<40} {'Action':<20} {'Details':<30}")
    print("-" * 90)
    for open_pos, market, desc in scenarios:
        # Check hard stop
        hard_exit, hard_loss, hard_reason = should_hard_exit(open_pos, market)
        target_exit_flag, gain, target_reason = target_exit(open_pos, market)

        if hard_exit:
            action = "EXIT (STOP)"
            detail = hard_reason
        elif target_exit_flag:
            action = "EXIT (TARGET)"
            detail = target_reason
        else:
            action = "HOLD"
            detail = hard_reason if hard_loss < 0 else target_reason

        print(f"{desc:<40} {action:<20} {detail:<30}")
