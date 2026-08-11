#!/usr/bin/env python3
"""
gate_5_correlation_hedge.py (REDESIGNED) — Avoid correlated open positions.

Old strategy: dynamic signal weighting (technical)
New strategy: DON'T TAKE SAME BET TWICE

If you have 3 open positions betting YES on crypto, and you see another great
crypto YES trade, don't add it. You're now all-in on ONE outcome.

Rules:
  - Track your open position exposure (sum of sides + outcomes)
  - If you already have >3 positions on the same outcome, decline new entries
  - Diversify: force yourself to take NO positions if you have too many YESes
  - This caps tail risk (won't get wiped out if one thesis fails)
"""

from collections import defaultdict

def portfolio_exposure(open_positions):
    """Compute exposure by outcome and category.

    Args:
        open_positions: list of open trade dicts

    Returns:
        {
            "by_outcome": {"YES": 5, "NO": 2},
            "by_category": {"esports": 3, "politics": 2, "crypto": 2},
            "total_exposure": 7,
        }
    """
    by_outcome = defaultdict(int)
    by_category = defaultdict(int)

    for pos in open_positions:
        side = pos.get("side", "UNKNOWN")
        cat = pos.get("cat") or pos.get("_leg") or "unknown"

        by_outcome[side] += 1
        by_category[cat] += 1

    return {
        "by_outcome": dict(by_outcome),
        "by_category": dict(by_category),
        "total_exposure": len(open_positions),
    }

def should_diversify(open_positions, new_position, cfg=None):
    """Check if adding this position would be over-concentrated.

    Rules:
      1. No more than 3 of the same outcome (YES/NO) concurrently
      2. No more than 2 positions in the same category
      3. Force opposite side if you're already 3+ on one side

    Returns:
        allow_entry: bool
        reason: str
        suggested_side_override: str or None (force opposite if you're unbalanced)
    """
    exposure = portfolio_exposure(open_positions)
    new_side = new_position.get("side", "UNKNOWN")
    new_cat = new_position.get("cat", "unknown")

    # Check 1: Too many of this outcome?
    same_side_count = exposure["by_outcome"].get(new_side, 0)
    if same_side_count >= 3:
        # Suggest opposite side to rebalance
        opposite = "NO" if new_side == "YES" else "YES"
        return False, f"Already 3x {new_side}, too concentrated", opposite

    # Check 2: Too many in this category?
    same_cat_count = exposure["by_category"].get(new_cat, 0)
    if same_cat_count >= 2:
        return False, f"Already 2x {new_cat}, reduce category overlap", None

    # Check 3: Overall imbalance (e.g., 5 YES vs 1 NO)?
    yes_count = exposure["by_outcome"].get("YES", 0)
    no_count = exposure["by_outcome"].get("NO", 0)
    imbalance_ratio = max(yes_count, no_count) / (min(yes_count, no_count) + 1)

    if imbalance_ratio > 3 and new_side == "YES":  # we're 3x over-indexed on YES
        return False, f"Portfolio {yes_count}Y vs {no_count}N (3x imbalance) — need NO", "NO"

    return True, "OK — portfolio remains diversified", None

# Test
if __name__ == "__main__":
    open_trades = [
        {"side": "YES", "cat": "esports"},
        {"side": "YES", "cat": "esports"},
        {"side": "YES", "cat": "crypto"},
        {"side": "NO", "cat": "politics"},
    ]

    exposure = portfolio_exposure(open_trades)
    print("[gate_5_REDESIGNED] Correlation Hedge (Avoid Concentration)\n")
    print(f"Current portfolio:")
    print(f"  By outcome: {exposure['by_outcome']}")
    print(f"  By category: {exposure['by_category']}")
    print(f"  Total: {exposure['total_exposure']} open\n")

    test_new = [
        {"side": "YES", "cat": "esports", "desc": "Another esports YES"},
        {"side": "NO", "cat": "crypto", "desc": "Crypto NO (rebalance)"},
        {"side": "YES", "cat": "crypto", "desc": "Crypto YES (already 2)"},
    ]

    print("New entries:")
    print("-" * 70)
    for new_pos in test_new:
        allow, reason, override = should_diversify(open_trades, new_pos)
        decision = "ACCEPT" if allow else "REJECT"
        override_str = f"(suggest {override})" if override else ""
        print(f"  {new_pos['desc']:<30} {decision:<8} {override_str}")
        print(f"    {reason}\n")
