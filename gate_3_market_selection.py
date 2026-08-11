#!/usr/bin/env python3
"""
gate_3_market_selection.py (REDESIGNED) — Avoid losing market categories.

Old strategy: macro divergence (Deribit vs Polymarket)
New strategy: TRACK WHICH CATEGORIES ARE LOSING and blacklist them

Your data: 3063 closed trades, -$326 overall. But losses aren't uniform.
Some categories (e.g., politics, low-conviction markets) might be -80% WR.
Other categories (e.g., esports, high-conviction) might be 55% WR.

Avoid the losers. Trade only the winners.
"""

from collections import defaultdict

def category_performance(closed_trades):
    """Analyze P&L and win rate by market category.

    Args:
        closed_trades: list of closed trade dicts

    Returns:
        dict: {category_name: {"wr": float, "pnl": float, "n": int, "avg_bet": float}}
    """
    by_cat = defaultdict(lambda: {"pnl": 0.0, "wins": 0, "n": 0, "size_total": 0.0})

    for t in closed_trades:
        cat = t.get("cat") or t.get("_leg") or "unknown"
        pnl = t.get("pnl_usdc") or 0.0
        size = t.get("size") or 0.0

        by_cat[cat]["pnl"] += pnl
        by_cat[cat]["n"] += 1
        by_cat[cat]["size_total"] += size
        if pnl > 0:
            by_cat[cat]["wins"] += 1

    # Convert to readable format
    results = {}
    for cat, stats in by_cat.items():
        results[cat] = {
            "pnl": stats["pnl"],
            "wr": stats["wins"] / stats["n"] if stats["n"] > 0 else 0.0,
            "n": stats["n"],
            "avg_bet": stats["size_total"] / stats["n"] if stats["n"] > 0 else 0.0,
        }

    return results

def should_skip_category(cat, performance_by_cat, cfg=None):
    """Decide: should we skip trading this category?

    Rules:
      - Skip if WR < 40% (worse than coin flip)
      - Skip if loss > 50% of the category's total trades (category is net negative)
      - Skip if less than 10 trades (not statistically significant)

    Returns:
        should_skip: bool
        reason: str
        edge_score: float (higher = safer to trade)
    """
    if cat not in performance_by_cat:
        return False, "Category has no history", 0.5  # neutral

    perf = performance_by_cat[cat]
    wr = perf["wr"]
    n = perf["n"]
    pnl = perf["pnl"]

    # Check 1: Not enough data?
    if n < 10:
        return True, f"Only {n} trades — too few, skip", 0.0

    # Check 2: Win rate terrible?
    if wr < 0.40:
        return True, f"WR {wr:.0%} < 40% — losers, skip", 0.0

    # Check 3: Category is net negative?
    if pnl < 0:
        return True, f"Category P&L {pnl:+.0f} negative — blacklist", 0.0

    # Category is OK — keep trading it
    edge_score = wr  # simple: higher WR = higher edge
    return False, f"OK: {wr:.0%} WR on {n} trades, {pnl:+.0f} P&L", edge_score

# Test
if __name__ == "__main__":
    test_closed = [
        {"cat": "esports", "pnl_usdc": 5.0, "size": 100},
        {"cat": "esports", "pnl_usdc": -2.0, "size": 100},
        {"cat": "esports", "pnl_usdc": 3.0, "size": 100},
        {"cat": "politics", "pnl_usdc": -10.0, "size": 200},
        {"cat": "politics", "pnl_usdc": -5.0, "size": 200},
        {"cat": "crypto", "pnl_usdc": 2.0, "size": 50},
        {"cat": "crypto", "pnl_usdc": -1.0, "size": 50},
    ]

    perf = category_performance(test_closed)

    print("[gate_3_REDESIGNED] Market Selection (Avoid Losing Categories)\n")
    print(f"{'Category':<15} {'WR':<8} {'P&L':<12} {'N':<6} {'Skip?':<8} {'Reason':<40}")
    print("-" * 90)

    for cat in sorted(perf.keys()):
        skip, reason, score = should_skip_category(cat, perf)
        wr_str = f"{perf[cat]['wr']:.0%}"
        pnl_str = f"${perf[cat]['pnl']:+.0f}"
        n_str = str(perf[cat]["n"])
        skip_str = "YES" if skip else "NO"

        print(f"{cat:<15} {wr_str:<8} {pnl_str:<12} {n_str:<6} {skip_str:<8} {reason:<40}")
