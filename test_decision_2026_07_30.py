#!/usr/bin/env python3
"""
7-Day Stop-Loss Test Decision Script
Run on 2026-07-30 22:30 to render the go/no-go verdict.

Compares live 7-day metrics vs backtest baseline.
Outputs: TEST_VERDICT.json + capital allocation recommendation

Usage:
  python3 test_decision_2026_07_30.py
"""

import json
from pathlib import Path
from datetime import datetime, timezone

BACKTEST_BASELINE = {
    "total_pnl": -369.43,
    "total_trades": 4548,
    "win_rate": 0.31,
    "avg_per_trade": -0.0813,
    "stop_bleed_loss": -446.00,  # -$0.308/trade × 1,449 stop-triggered trades
    "control_per_trade": -0.1590,  # allin control
}

def load_7day_log():
    """Load daily check logs."""
    log_file = Path("SEVEN_DAY_TEST_LOG.json")
    if not log_file.exists():
        print("ERROR: SEVEN_DAY_TEST_LOG.json not found. Did you run daily_test_check.py?")
        return []

    with open(log_file) as f:
        return json.load(f)

def compute_7day_summary(logs):
    """Summarize the 7 days of data."""
    if not logs:
        return {}

    # Get latest metrics
    latest = logs[-1]

    # Compute averages
    edge_per_trades = [
        log.get("backtest", {}).get("edge_per_trade", 0)
        for log in logs
        if log.get("backtest", {}).get("edge_per_trade")
    ]
    avg_edge = sum(edge_per_trades) / len(edge_per_trades) if edge_per_trades else 0

    win_rates = [
        log.get("backtest", {}).get("edge_win_rate", 0)
        for log in logs
        if log.get("backtest", {}).get("edge_win_rate")
    ]
    avg_win_rate = sum(win_rates) / len(win_rates) if win_rates else 0

    return {
        "days_logged": len(logs),
        "avg_edge_per_trade": round(avg_edge, 4),
        "avg_win_rate": round(avg_win_rate, 2),
        "latest_unrealized_pnl": latest.get("unrealized_pnl", 0),
        "latest_open_positions": latest.get("open_positions", 0),
        "stale_positions": latest.get("stale_positions_count", 0),
    }

def render_verdict(baseline, live_summary):
    """Render PASS / FAIL / NEUTRAL verdict."""

    if not live_summary:
        return {
            "verdict": "ERROR",
            "reason": "Insufficient data (no 7-day logs)",
            "recommendation": "Run daily_test_check.py for 7 days, then retry."
        }

    edge_live = live_summary["avg_edge_per_trade"]
    edge_baseline = baseline["control_per_trade"]  # allin control as baseline
    wr_live = live_summary["avg_win_rate"]
    wr_baseline = baseline["win_rate"]

    # Decision thresholds
    edge_threshold = edge_baseline * 0.95  # Must beat control by at least 5%
    wr_threshold = wr_baseline + 0.01  # Must improve win rate by >1pp

    passed_edge = edge_live > edge_threshold
    passed_wr = wr_live > wr_threshold
    no_stale_issues = live_summary["stale_positions"] == 0

    # Render verdict
    if passed_edge and passed_wr:
        verdict = "PASS"
        reason = f"Live edge {edge_live:.4f} > baseline {edge_threshold:.4f} AND WR improved to {wr_live:.1%}"
    elif passed_edge or passed_wr:
        verdict = "NEUTRAL"
        reason = f"Mixed: edge {'PASS' if passed_edge else 'FAIL'}, WR {'PASS' if passed_wr else 'FAIL'}"
    else:
        verdict = "FAIL"
        reason = f"Live edge {edge_live:.4f} ≤ baseline {edge_threshold:.4f} AND WR {wr_live:.1%} did not improve"

    return {
        "verdict": verdict,
        "reason": reason,
        "details": {
            "edge_live": edge_live,
            "edge_baseline": edge_threshold,
            "edge_passed": passed_edge,
            "wr_live": wr_live,
            "wr_baseline": wr_threshold,
            "wr_passed": passed_wr,
            "stale_issues": live_summary["stale_positions"] > 0,
        }
    }

def capital_allocation_recommendation(verdict):
    """Recommend capital allocation based on verdict."""

    if verdict["verdict"] == "PASS":
        return {
            "action": "PROCEED",
            "steps": [
                "1. Close 3 resolved winners (pending reconciliation)",
                "2. Retire toxic strategies: newsmove (18.8% WR), coinflip (-$66), microscalp (0% WR)",
                "3. Add Fed basket: 5 legs, 0.95% edge, $50-100 paper allocation",
                "4. Restore stops from backup (scalp_lab.py.backup.1784830289)",
                "5. Tighten stops on nearterm positions post-re-enable",
                "6. Monitor for stop-bleed on new allocation vs backtest"
            ],
            "capital_reallocation": {
                "close": ["3 resolved winners (TBD)"],
                "retire": ["newsmove", "coinflip", "microscalp"],
                "add": ["Fed basket (5 legs, 0.95% edge)"],
                "keep": ["windowshutrand (5 BTC range positions)", "edge_trader (ML divergence)"],
            }
        }

    elif verdict["verdict"] == "NEUTRAL":
        return {
            "action": "EXTEND_TEST",
            "steps": [
                "1. Extend test by 7 more days (until 2026-08-06)",
                "2. Collect additional data to disambiguate mixed results",
                "3. Run period-specific analysis: which leg types benefited from no-stops?",
                "4. If edge improves further: escalate to PASS",
                "5. If edge stalls or degrades: downgrade to FAIL, investigate signal quality"
            ]
        }

    else:  # FAIL
        return {
            "action": "RESTORE_AND_INVESTIGATE",
            "steps": [
                "1. Restore stops immediately from backup (scalp_lab.py.backup.1784830289)",
                "2. Revert scalp_engine_config.json stops to original values",
                "3. Root-cause analysis: run 'python3 scalp_lab.py --analyze-exits'",
                "4. Investigate: is stop-bleed a real problem, or are signals toxic?",
                "5. Redesign: focus on entry quality (signal), not hold duration",
                "6. Hypothesis revision: stops may be correct; the issue is signal selection"
            ]
        }

def main():
    now = datetime.now(timezone.utc)
    print(f"\n{'='*70}")
    print(f"7-DAY STOP-LOSS TEST DECISION (Run: {now.strftime('%Y-%m-%d %H:%M UTC')})")
    print(f"{'='*70}\n")

    # Load and summarize 7-day data
    logs = load_7day_log()
    summary = compute_7day_summary(logs)

    if not summary:
        print("ERROR: No data to analyze. Run daily_test_check.py for 7 days first.")
        return 1

    print(f"7-Day Summary:")
    print(f"  Days logged: {summary['days_logged']}/7")
    print(f"  Avg edge (per trade): ${summary['avg_edge_per_trade']:.4f}")
    print(f"  Avg win rate: {summary['avg_win_rate']:.1%}")
    print(f"  Latest unrealized P&L: ${summary['latest_unrealized_pnl']:.2f}")
    print(f"  Open positions: {summary['latest_open_positions']}")
    print(f"  Stale positions (>60d): {summary['stale_positions']}\n")

    # Render verdict
    verdict = render_verdict(BACKTEST_BASELINE, summary)

    print(f"VERDICT: {verdict['verdict']}")
    print(f"Reason: {verdict['reason']}\n")

    print("Detailed Metrics:")
    for key, val in verdict["details"].items():
        print(f"  {key}: {val}")

    # Capital allocation recommendation
    allocation = capital_allocation_recommendation(verdict)

    print(f"\n{'='*70}")
    print(f"CAPITAL ALLOCATION RECOMMENDATION: {allocation['action']}")
    print(f"{'='*70}\n")

    for step in allocation["steps"]:
        print(f"  {step}")

    if "capital_reallocation" in allocation:
        print(f"\nReallocation Plan:")
        for action, items in allocation["capital_reallocation"].items():
            print(f"  {action.upper()}: {', '.join(items)}")

    # Save verdict to file
    verdict_file = Path("TEST_VERDICT.json")
    verdict_output = {
        "timestamp": now.isoformat(),
        "verdict": verdict,
        "allocation": allocation,
        "backtest_baseline": BACKTEST_BASELINE,
        "live_summary": summary,
    }

    with open(verdict_file, "w") as f:
        json.dump(verdict_output, f, indent=2)

    print(f"\n✓ Verdict saved to {verdict_file}")
    return 0

if __name__ == "__main__":
    import sys
    sys.exit(main())
