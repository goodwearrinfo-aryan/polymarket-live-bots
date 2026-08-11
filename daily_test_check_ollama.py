#!/usr/bin/env python3
"""
7-Day Test Daily Check with Ollama Analysis
Run daily at 09:00 UTC (2026-07-24 to 2026-07-29)

Collects metrics, runs backtest, then feeds to ollama for human-readable analysis.
Outputs: SEVEN_DAY_TEST_LOG.json (append) + ollama verdict printed

Usage:
  python3 daily_test_check_ollama.py

"""

import json
import sys
import subprocess
from datetime import datetime, timezone
from pathlib import Path
import os

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

def run_backtest():
    """Run ml/backtest_edge.py and extract metrics."""
    try:
        result = subprocess.run(
            ["python3", "ml/backtest_edge.py"],
            capture_output=True,
            text=True,
            timeout=60
        )
        output = result.stdout + result.stderr

        # Parse output for key metrics
        import re
        metrics = {}

        match = re.search(r"per-trade = ([+-]?\d+\.\d+)", output)
        if match:
            metrics["edge_per_trade"] = float(match.group(1))

        match = re.search(r"trades=(\d+)", output)
        if match:
            metrics["edge_trades"] = int(match.group(1))

        match = re.search(r"win=(\d+)%", output)
        if match:
            metrics["edge_win_rate"] = int(match.group(1)) / 100.0

        metrics["exit_code"] = result.returncode
        return metrics
    except Exception as e:
        return {"error": str(e), "exit_code": -1}

def query_ollama_for_verdict(day, metrics, prev_entries):
    """Ask ollama: is the test on track?"""

    # Build context for ollama
    context = f"""
You are analyzing a 7-day stop-loss disable test for a Polymarket trading bot.
Test: Remove all stops, see if positions run longer and improve Sharpe.
Goal: Validate hypothesis by 2026-07-30.

TODAY'S METRICS (Day {day}/7):
- Open positions: {metrics.get('open_positions', 0)}
- Unrealized P&L: ${metrics.get('unrealized_pnl', 0):.2f}
- Backtest edge (per-trade): ${metrics.get('backtest', {}).get('edge_per_trade', 0):.4f}
- Backtest trades: {metrics.get('backtest', {}).get('edge_trades', 0)}

BASELINE (for comparison):
- Expected edge: -$0.159/trade (allin control, worst case)
- Expected P&L total: -$369.43 (on 4,548 trades, 31% WR)
- Test hypothesis: Removing stops should improve Sharpe (edge > -0.159)

QUESTION: Is the test on track? Give a brief honest verdict:
1. Green (improving as expected)
2. Yellow (mixed signals, unclear)
3. Red (hypothesis failing)

Include: (a) whether edge is beating baseline, (b) any surprising patterns, (c) what to watch next 24h.
Keep it to 2-3 sentences."""

    try:
        result = subprocess.run(
            ["ollama", "run", "llama3.1:8b"],
            input=context,
            capture_output=True,
            text=True,
            timeout=60
        )
        if result.returncode == 0:
            return result.stdout.strip()
        else:
            return f"[Ollama error: {result.stderr[:200]}]"
    except subprocess.TimeoutExpired:
        return "[Ollama timeout — taking too long]"
    except FileNotFoundError:
        return "[Ollama not installed or not in PATH]"
    except Exception as e:
        return f"[Ollama error: {str(e)[:100]}]"

def sync_github():
    """Pull latest bot strategies from GitHub."""
    try:
        result = subprocess.run(
            ["python3", "github_sync.py", "--fetch"],
            capture_output=True,
            text=True,
            timeout=120
        )
        return result.returncode == 0
    except:
        return False

def push_results_to_github():
    """Push test results to GitHub."""
    try:
        result = subprocess.run(
            ["python3", "github_sync.py", "--push"],
            capture_output=True,
            text=True,
            timeout=60
        )
        return result.returncode == 0
    except:
        return False

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
    print(f"\n{'='*70}")
    print(f"7-DAY TEST DAILY CHECK — Day {days_elapsed + 1}/7")
    print(f"Time: {now.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"{'='*70}\n")

    # Collect metrics
    state = get_state()
    open_count, by_leg = count_open_positions(state)
    backtest_metrics = run_backtest()

    # Build log entry
    entry = {
        "timestamp": now.isoformat(),
        "day": days_elapsed + 1,
        "open_positions": open_count,
        "by_leg": by_leg,
        "backtest": backtest_metrics,
    }

    # Load prior entries for context
    logs = []
    if LOG_FILE.exists():
        with open(LOG_FILE) as f:
            logs = json.load(f)

    # Get ollama verdict
    print(f"Analyzing with ollama...\n")
    verdict = query_ollama_for_verdict(days_elapsed + 1, entry, logs)

    entry["ollama_verdict"] = verdict

    # Append to log
    logs.append(entry)
    with open(LOG_FILE, "w") as f:
        json.dump(logs, f, indent=2)

    # Print summary
    print(f"METRICS:")
    print(f"  Open positions: {open_count}")
    print(f"  Backtest edge (per-trade): ${backtest_metrics.get('edge_per_trade', 0):.4f}")
    print(f"  Backtest trades: {backtest_metrics.get('edge_trades', 0)}")

    print(f"\nOLLAMA VERDICT:")
    print(f"  {verdict}")

    print(f"\n✓ Log updated: {LOG_FILE}")

    # Sync with GitHub
    print(f"\nGITHUB SYNC:")
    if sync_github():
        print(f"  ✓ Fetched latest bot strategies")
    else:
        print(f"  ⚠ Could not fetch bot strategies")

    if push_results_to_github():
        print(f"  ✓ Pushed results to goodwearrinfo-aryan/edge-bots")
    else:
        print(f"  ⚠ Could not push results to GitHub")

    # Run ollama agents (autonomous tweaks)
    print(f"\nOLLAMA AGENTS (Autonomous Tweaks):")
    try:
        result = subprocess.run(
            ["python3", "ollama_agents.py", "--alert-monitor"],
            capture_output=True,
            text=True,
            timeout=60
        )
        if result.returncode == 0:
            print(f"  ✓ Agent 1 (Alert Monitor): ran")

        # Run stress test
        result = subprocess.run(
            ["python3", "ollama_agents.py", "--stress-test"],
            capture_output=True,
            text=True,
            timeout=60
        )
        if result.returncode == 0:
            print(f"  ✓ Agent 2 (Stress Test): ran")

        # Run strategy mutation
        result = subprocess.run(
            ["python3", "ollama_agents.py", "--strategy-mutate"],
            capture_output=True,
            text=True,
            timeout=60
        )
        if result.returncode == 0:
            print(f"  ✓ Agent 3 (Strategy Mutate): ran")

        # Run auto prune
        result = subprocess.run(
            ["python3", "ollama_agents.py", "--auto-prune"],
            capture_output=True,
            text=True,
            timeout=60
        )
        if result.returncode == 0:
            print(f"  ✓ Agent 4 (Auto Prune): ran")

        print(f"  ℹ Agents 5-10: placeholder modes")

    except Exception as e:
        print(f"  ⚠ Agents error: {e}")

    return 0

if __name__ == "__main__":
    sys.exit(main())
