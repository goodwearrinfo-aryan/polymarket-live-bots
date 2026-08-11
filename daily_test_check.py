#!/usr/bin/env python3
"""
Daily 7-day test check: monitor Sharpe, position count, stale positions.
Run this every 09:00 UTC from 2026-07-24 to 2026-07-30.

Usage:
  python3 daily_test_check.py

Outputs to: SEVEN_DAY_TEST_LOG.json (append)
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
import subprocess

TEST_START = datetime(2026, 7, 23, 22, 33, tzinfo=timezone.utc)
TEST_END = datetime(2026, 7, 30, 22, 33, tzinfo=timezone.utc)
LOG_FILE = Path("SEVEN_DAY_TEST_LOG.json")

def get_state():
    """Read current state."""
    try:
        with open("scalp_lab_state.json") as f:
            return json.load(f)
    except Exception as e:
        print(f"ERROR: Could not read state: {e}")
        return {}

def count_open_positions(state):
    """Count total open positions."""
    total = 0
    by_leg = {}
    for leg, leg_state in state.items():
        count = len(leg_state.get("open", []))
        if count > 0:
            by_leg[leg] = count
            total += count
    return total, by_leg

def get_unrealized_pnl(state):
    """Rough unrealized P&L (entry_fill vs current mid, if available)."""
    # This is simplified; real P&L requires live market data
    total_unrealized = 0.0
    for leg, leg_state in state.items():
        for pos in leg_state.get("open", []):
            entry = pos.get("entry_fill", 0)
            mid = pos.get("current_mid", entry)  # Placeholder
            size = pos.get("size", 0)
            side = pos.get("side", "YES")

            if side == "YES":
                unrealized = (mid - entry) * size
            else:  # NO
                unrealized = (entry - mid) * size

            total_unrealized += unrealized
    return total_unrealized

def run_backtest():
    """Run ml/backtest_edge.py and extract Sharpe/PnL."""
    try:
        result = subprocess.run(
            ["python3", "ml/backtest_edge.py"],
            capture_output=True,
            text=True,
            timeout=60
        )
        output = result.stdout + result.stderr

        # Parse output for key metrics (adjust regex to match actual output)
        import re
        metrics = {}

        # Look for "per-trade = +X.XXXX" pattern
        match = re.search(r"per-trade = ([+-]?\d+\.\d+)", output)
        if match:
            metrics["edge_per_trade"] = float(match.group(1))

        # Look for "trades=XX"
        match = re.search(r"trades=(\d+)", output)
        if match:
            metrics["edge_trades"] = int(match.group(1))

        # Look for win rate
        match = re.search(r"win=(\d+)%", output)
        if match:
            metrics["edge_win_rate"] = int(match.group(1)) / 100.0

        metrics["exit_code"] = result.returncode
        return metrics
    except Exception as e:
        return {"error": str(e), "exit_code": -1}

def check_stale_positions(state):
    """Find positions that should have resolved."""
    stale = []
    now = datetime.now(timezone.utc)

    for leg, leg_state in state.items():
        for pos in leg_state.get("open", []):
            opened = datetime.fromisoformat(pos.get("opened_at", "").replace("Z", "+00:00"))
            age_days = (now - opened).days

            if age_days > 60:
                stale.append({
                    "leg": leg,
                    "market": pos.get("q", "N/A")[:50],
                    "age_days": age_days
                })

    return stale

def main():
    now = datetime.now(timezone.utc)

    # Check if we're in the test window
    if now < TEST_START:
        print(f"Test has not started yet (starts {TEST_START})")
        return 1
    if now > TEST_END:
        print(f"Test has ended (ended {TEST_END})")
        return 1

    days_elapsed = (now - TEST_START).days
    print(f"\n=== 7-Day Test Check (Day {days_elapsed + 1}/7) ===")
    print(f"Time: {now.strftime('%Y-%m-%d %H:%M:%S UTC')}")

    # Collect metrics
    state = get_state()
    open_count, by_leg = count_open_positions(state)
    unrealized = get_unrealized_pnl(state)
    backtest_metrics = run_backtest()
    stale = check_stale_positions(state)

    # Build log entry
    entry = {
        "timestamp": now.isoformat(),
        "day": days_elapsed + 1,
        "open_positions": open_count,
        "by_leg": by_leg,
        "unrealized_pnl": round(unrealized, 2),
        "backtest": backtest_metrics,
        "stale_positions_count": len(stale),
        "stale_positions": stale[:3],  # Top 3
    }

    # Append to log
    logs = []
    if LOG_FILE.exists():
        with open(LOG_FILE) as f:
            logs = json.load(f)

    logs.append(entry)
    with open(LOG_FILE, "w") as f:
        json.dump(logs, f, indent=2)

    # Print summary
    print(f"\nOpen positions: {open_count}")
    print(f"Unrealized P&L: ${unrealized:.2f}")
    print(f"Backtest edge (per-trade): ${backtest_metrics.get('edge_per_trade', 0):.4f}")
    print(f"Backtest trades: {backtest_metrics.get('edge_trades', 0)}")
    print(f"Stale positions (>60d): {len(stale)}")

    if stale:
        print("\nStale positions to close:")
        for s in stale[:3]:
            print(f"  {s['leg']:15} {s['age_days']:3}d old: {s['market']}")

    print(f"\n✓ Log updated: {LOG_FILE}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
