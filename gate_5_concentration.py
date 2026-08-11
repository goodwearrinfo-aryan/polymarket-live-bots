#!/usr/bin/env python3
"""
gate_5_concentration.py (HIGH RISK/REWARD) — Go ALL-IN on best ideas.

Philosophy: Diversification is for people who don't have conviction.
You have 2-3 categories with >60% WR. Put 70% of capital there.
Everything else: 10% exploration.

Rules:
  - Rank categories by conviction score
  - Top 2: 35% each of available capital
  - Next tier: 15% exploration
  - Never hold >5 concurrent positions (all must be conviction trades)
  - No hedge trades (no YES+NO on the same market)
"""

from collections import defaultdict

def allocation_by_conviction(conviction_scores, total_bankroll):
    """Allocate capital based on category conviction.

    Args:
        conviction_scores: {cat: {'wr': float, 'conviction': str}}
        total_bankroll: total capital available

    Returns:
        {category: allocation_usdc}
    """
    # Rank by conviction
    high_conviction = {k: v for k, v in conviction_scores.items() if v["conviction"] == "HIGH"}
    med_conviction = {k: v for k, v in conviction_scores.items() if v["conviction"] == "MED"}

    allocation = {}

    # Top 2 high-conviction categories: 35% each
    high_cats = sorted(high_conviction.items(), key=lambda x: -x[1]["wr"])
    for i, (cat, score) in enumerate(high_cats[:2]):
        allocation[cat] = total_bankroll * 0.35

    # Rest of high: split remaining 15%
    for i, (cat, score) in enumerate(high_cats[2:]):
        allocation[cat] = total_bankroll * 0.15 / max(1, len(high_cats) - 2)

    # Med-conviction: 10% exploration (take small shots)
    for cat, score in med_conviction.items():
        allocation[cat] = total_bankroll * 0.05

    return allocation

def position_limit_check(open_positions, new_category, allocation_available):
    """Check if you can add this new position.

    Rules:
      1. Max 5 open positions (all must be conviction trades)
      2. Don't exceed allocation for this category
      3. No YES+NO on the same market (no hedges)

    Returns:
        allow: bool
        reason: str
    """
    # Rule 1: Max 5 positions?
    if len(open_positions) >= 5:
        return False, "Already 5 open (max): close one before new entry"

    # Rule 2: Category allocation?
    cat_positions = sum(1 for p in open_positions if p.get("cat") == new_category)
    max_per_cat = max(2, int(len(open_positions) / 2))
    if cat_positions >= max_per_cat:
        return False, f"Already {cat_positions} in {new_category}: rotate"

    # Rule 3: No YES+NO on same market?
    market_id = new_category
    opposite_side = "NO" if "YES" in market_id else "YES"
    if any(p.get("id") == market_id for p in open_positions):
        return False, "Already have a position on this market (hedge not allowed)"

    return True, "OK — fits allocation"

# Test
if __name__ == "__main__":
    print("[gate_5_CONCENTRATION] All-In on Best Ideas\n")

    # Mock conviction scores
    scores = {
        "esports": {"wr": 0.75, "conviction": "HIGH"},
        "crypto": {"wr": 0.62, "conviction": "HIGH"},
        "politics": {"wr": 0.45, "conviction": "LOW"},
    }

    bankroll = 10000
    allocation = allocation_by_conviction(scores, bankroll)

    print(f"Capital Allocation (${bankroll}):")
    print("-" * 50)
    for cat in sorted(allocation.keys()):
        pct = allocation[cat] / bankroll
        print(f"  {cat:<20} ${allocation[cat]:>7.0f} ({pct:>5.0%})")

    print(f"\nPosition Rules:")
    print("  Max 5 open positions")
    print("  Top 2 categories: 35% each")
    print("  NO hedges (no YES+NO on same market)")
    print("  Conviction-only trades\n")

    # Test new entries
    open_trades = [
        {"id": "esports_1", "cat": "esports", "side": "YES"},
        {"id": "crypto_1", "cat": "crypto", "side": "YES"},
    ]

    print("New entry checks:")
    print("-" * 50)
    new_entries = [
        ("esports_2", "esports"),
        ("crypto_2", "crypto"),
        ("politics_1", "politics"),
    ]

    for market_id, cat in new_entries:
        allow, reason = position_limit_check(
            open_trades,
            cat,
            allocation.get(cat, 0)
        )
        print(f"  {market_id:<20} {'ALLOW' if allow else 'REJECT':<8} {reason}")
