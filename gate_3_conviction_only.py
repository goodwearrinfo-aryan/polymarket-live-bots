#!/usr/bin/env python3
"""
gate_3_conviction_only.py (HIGH RISK/REWARD) — Only trade high-conviction categories.

Philosophy: If you're going to size big and accept -30% losses, you ONLY do it
on categories where you have a real edge.

Rules:
  - Only trade categories where you've proven WR ≥ 60% (not 34% baseline)
  - Skip everything else (no FOMO trades)
  - Focus capital on your best 2-3 categories

Effect: fewer trades, but much higher win rate on the ones you take.
With 60% WR and 3-4x targets: expect 70% of bets hit target, 30% hit stop.
"""

from collections import defaultdict

def category_conviction_score(closed_trades_by_cat, min_trades=20):
    """Score each category on real edge.

    Returns:
        {category: {'wr': float, 'pnl': float, 'conviction': 'HIGH'/'MED'/'LOW', 'n': int}}
    """
    results = {}

    for cat, trades in closed_trades_by_cat.items():
        if len(trades) < min_trades:
            results[cat] = {
                "wr": 0.0, "pnl": 0.0, "conviction": "SKIP (too few trades)",
                "n": len(trades)
            }
            continue

        wr = len([t for t in trades if (t.get("pnl_usdc") or 0) > 0]) / len(trades)
        pnl = sum(t.get("pnl_usdc") or 0 for t in trades)

        if wr >= 0.65:
            conviction = "HIGH"
        elif wr >= 0.55:
            conviction = "MED"
        elif wr >= 0.45:
            conviction = "LOW"
        else:
            conviction = "AVOID"

        results[cat] = {
            "wr": wr, "pnl": pnl, "conviction": conviction, "n": len(trades)
        }

    return results

def should_trade_category(cat, conviction_scores):
    """Decide: is this category worthy of big bets?

    High-risk strategy: only trade HIGH conviction (WR >= 60%).
    Everything else is noise.

    Returns:
        should_trade: bool
        reason: str
        expected_roi: float (mock estimate)
    """
    if cat not in conviction_scores:
        return False, "No history", 0.0

    score = conviction_scores[cat]
    conviction = score["conviction"]
    wr = score["wr"]

    if conviction == "HIGH":  # WR >= 65%
        # Expected: 70% hit 3x target, 30% hit -30% stop
        roi = (0.70 * 3.0 - 0.30 * 0.30)
        return True, f"HIGH conviction ({wr:.0%} WR) — GO", roi

    elif conviction == "MED":  # WR 55-65%
        return False, f"MEDIUM conviction ({wr:.0%} WR) — too risky, skip", 0.0

    else:
        return False, f"LOW/AVOID conviction ({wr:.0%} WR) — blacklisted", 0.0

# Test
if __name__ == "__main__":
    print("[gate_3_CONVICTION_ONLY] Trade Only High-Confidence Categories\n")

    test_trades = {
        "esports": [
            {"pnl_usdc": 5.0}, {"pnl_usdc": 3.0}, {"pnl_usdc": 2.0},
            {"pnl_usdc": -1.0}, {"pnl_usdc": 4.0}, {"pnl_usdc": 6.0},
            {"pnl_usdc": 3.0}, {"pnl_usdc": 2.0}, {"pnl_usdc": -0.5},
            {"pnl_usdc": 5.0},  # 8/10 = 80% WR
        ] + [{"pnl_usdc": 3.0} for _ in range(10)],  # 20 trades
        "politics": [
            {"pnl_usdc": -5.0}, {"pnl_usdc": -2.0}, {"pnl_usdc": 1.0},
            {"pnl_usdc": -3.0}, {"pnl_usdc": -1.0},
        ] * 4,  # 20 trades, mostly losses
        "crypto": [
            {"pnl_usdc": 2.0}, {"pnl_usdc": -1.0}, {"pnl_usdc": 1.0},
            {"pnl_usdc": -2.0},
        ] * 5,  # 20 trades, 50% WR
    }

    scores = category_conviction_score(test_trades)

    print(f"{'Category':<15} {'WR':<8} {'Conviction':<15} {'Trade?':<8} {'Expected ROI':<12}")
    print("-" * 70)

    for cat in sorted(scores.keys()):
        score = scores[cat]
        should_trade, reason, roi = should_trade_category(cat, scores)
        wr_str = f"{score['wr']:.0%}" if score["wr"] > 0 else "N/A"
        roi_str = f"{roi:.1f}x" if roi > 0 else "N/A"

        print(f"{cat:<15} {wr_str:<8} {score['conviction']:<15} "
              f"{'YES' if should_trade else 'NO':<8} {roi_str:<12}")
