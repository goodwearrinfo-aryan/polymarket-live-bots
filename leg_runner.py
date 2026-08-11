"""
leg_runner.py — orchestrate all paper-trading legs independently.

Each leg is a standalone module (leg_diverg.py, leg_feargreed.py, etc).
Leg runner:
- Loads markets + prices once
- Dispatches entry/exit scans to all enabled legs in parallel
- Aggregates forecasts → shared belief_ledger.jsonl
- Prints unified board across all legs

Usage:
  python3 leg_runner.py scan      # entry/exit scan (run via watchdog ~60s)
  python3 leg_runner.py board     # unified board (all legs)
  python3 leg_runner.py --leg diverg board  # single leg stats
"""
import json, sys, importlib, time
from pathlib import Path
from collections import defaultdict

# Leg modules (add new ones here)
LEG_MODULES = [
    "leg_diverg",
    "leg_feargreed",
    "leg_lateprox",
    "leg_coinup",
    "leg_coindown",
]

BELIEF_LEDGER = Path("belief_ledger.py")


def load_legs():
    """Import all leg modules."""
    legs = {}
    for leg_name in LEG_MODULES:
        try:
            legs[leg_name] = importlib.import_module(leg_name)
        except (ImportError, ModuleNotFoundError) as e:
            print(f"[leg_runner] skip {leg_name}: {e}")
    return legs


def scan(markets, prices, legs):
    """Run entry/exit scans for all enabled legs."""
    entries, exits = [], []

    for leg_name, leg_mod in legs.items():
        if not leg_mod.CONFIG.get("enabled"):
            continue

        state = leg_mod.load_state()

        # Entries
        try:
            for pos in leg_mod.scan_entries(markets, state):
                entries.append((leg_name, pos))
        except Exception as e:
            print(f"[{leg_name}] entry error: {e}")

        # Exits
        try:
            book = state.get(leg_name, {})
            for pos, exit_px, reason in leg_mod.scan_exits(book, prices):
                exits.append((leg_name, pos, exit_px, reason))
        except Exception as e:
            print(f"[{leg_name}] exit error: {e}")

        leg_mod.save_state(state)

    return entries, exits


def board(legs, single_leg=None):
    """Print unified board across all legs, or single leg."""
    print("\n" + "=" * 60)
    print("  LEGS RUNNER — paper trading (all legs)")
    print("=" * 60)

    for leg_name, leg_mod in legs.items():
        if single_leg and leg_name != single_leg:
            continue
        try:
            state = leg_mod.load_state()
            leg_mod.board(state)
        except Exception as e:
            print(f"  [{leg_name.upper()}] error: {e}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "board"
    single_leg = sys.argv[3] if "--leg" in sys.argv else None

    legs = load_legs()

    if cmd == "board":
        board(legs, single_leg)
    elif cmd == "scan":
        print("[leg_runner] scan mode (stub — requires market data source)")
    else:
        print(f"unknown command: {cmd}")
