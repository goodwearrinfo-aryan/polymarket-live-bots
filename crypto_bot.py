"""
crypto_bot.py — standalone crypto paper-trading bot.

Runs 8 crypto legs in parallel:
  - diverg, feargreed, lateprox (spot vs market divergence family)
  - coinup, coindown (50/50 crypto market flips)
  - candlesig, macdsig (technical signal family)
  - coinrev (mean-reversion on 24h moves)

Independent from scalp_lab.py. Records all forecasts to crypto_belief_ledger.jsonl.
State persisted in crypto_bot_state.json. Can dry-run or run live.

Usage:
  python3 crypto_bot.py once       # single cycle (entry + exit scan)
  python3 crypto_bot.py run        # loop via watchdog
  python3 crypto_bot.py board      # print stats
"""
import json, sys, time, importlib
from pathlib import Path
from datetime import datetime, timezone

# Crypto legs only
CRYPTO_LEGS = [
    "leg_diverg",
    "leg_feargreed",
    "leg_lateprox",
    "leg_coinup",
    "leg_coindown",
    "leg_candlesig",
    "leg_macdsig",
    "leg_coinrev",
]

STATE_FILE = "crypto_bot_state.json"
BELIEF_LEDGER = Path("crypto_belief_ledger.jsonl")


def load_legs():
    """Import all crypto leg modules."""
    legs = {}
    for leg_name in CRYPTO_LEGS:
        try:
            legs[leg_name] = importlib.import_module(leg_name)
        except (ImportError, ModuleNotFoundError):
            pass  # leg not yet modularized
    return legs


def load_state():
    """Load bot state."""
    try:
        return json.load(open(STATE_FILE))
    except FileNotFoundError:
        return {leg: {"open": [], "closed": []} for leg in CRYPTO_LEGS}


def save_state(state):
    """Save bot state."""
    json.dump(state, open(STATE_FILE, "w"), indent=2)


def one_scan(markets, prices, legs):
    """Single entry/exit scan."""
    state = load_state()

    print(f"[{datetime.now(timezone.utc).isoformat()[:19]}] crypto_bot cycle")
    print(f"  markets: {len(markets)}")

    for leg_name, leg_mod in legs.items():
        if not leg_mod.CONFIG.get("enabled"):
            continue

        try:
            # Entries
            for pos in leg_mod.scan_entries(markets, state):
                print(f"    [ENTRY] {leg_name}: {pos['q'][:40]} {pos['side']} @ {pos['entry_fill']}")

            # Exits
            book = state.get(leg_name, {})
            for pos, exit_px, reason in leg_mod.scan_exits(book, prices):
                pnl = (exit_px - pos["entry_fill"]) * pos["size"]
                print(f"    [EXIT] {leg_name}: {reason} @ {exit_px} pnl={pnl:+.4f}")
                state[leg_name]["closed"].append({**pos, "exit_fill": exit_px, "reason": reason, "pnl": pnl})
                state[leg_name]["open"].remove(pos)
        except Exception as e:
            print(f"    [ERR] {leg_name}: {e}")

    save_state(state)


def board(legs):
    """Print crypto bot board."""
    state = load_state()

    print("\n" + "=" * 60)
    print("  CRYPTO BOT — paper trading (8 crypto legs)")
    print("=" * 60)

    total_open, total_closed, total_pnl = 0, 0, 0.0

    for leg_name in CRYPTO_LEGS:
        book = state.get(leg_name, {})
        open_c = len(book.get("open", []))
        closed = book.get("closed", [])
        n = len(closed)
        w = sum(1 for r in closed if r.get("pnl", 0) > 0) if n > 0 else 0
        pnl = sum(r.get("pnl", 0) for r in closed) if n > 0 else 0.0
        wr = (w / n * 100) if n > 0 else 0

        if n > 0 or open_c > 0:
            print(f"\n  [{leg_name.upper():15s}] open={open_c:2d}  closed={n:3d}  "
                  f"win={wr:5.1f}%  paper P&L=${pnl:+.2f}")

        total_open += open_c
        total_closed += n
        total_pnl += pnl

    print(f"\n  TOTAL: open={total_open}  closed={total_closed}  P&L=${total_pnl:+.2f}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "board"
    legs = load_legs()

    if cmd == "board":
        board(legs)
    elif cmd == "once":
        print("[crypto_bot] once mode (stub — requires market data source)")
    elif cmd == "run":
        print("[crypto_bot] run mode (stub — requires watchdog integration)")
    else:
        print(f"unknown command: {cmd}")
