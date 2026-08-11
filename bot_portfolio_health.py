#!/usr/bin/env python3
"""
bot_portfolio_health.py — Watch the live bots, alert on silence or crashes.

Monitors (2026-06-24): only the bots that actually have a runner.
  - nearres (4000 USDC) — launchd com.aryan.bot-nearres, logs to /tmp
  - ladderarb (3000 USDC) — launchd com.aryan.bot-ladderarb, logs to /tmp

Removed 2026-06-24 (were firing false silent_* alerts since ~Jun 15):
  - fade / fastfade — killed legs (Jun 8/10, confirmed losers), no runner exists
  - newstrategy — design-phase stub, never deployed
  NOTE: the live bots write stdout to /tmp/bot_<name>.log (plist StandardOutPath),
  NOT to ~/Documents/polymarket/bot_<name>.log — the old paths were 0-byte stubs,
  which is why the monitor reported them silent while they were running fine.

Alerts:
  - Bot silent >1h (log file not updated)
  - Negative P&L exceeds threshold (-15% of capital)
  - Crash (error log grows, no valid state)
  - Portfolio underwater (-10% total capital)

Runs every 15min via launchd.
"""

import json, os, sys, time, subprocess
from datetime import datetime, timedelta

BOTS = {
    "nearres": {
        "state_file": "~/Documents/polymarket/nearres.json",
        "log_file": "/tmp/bot_nearres.log",
        "capital": 4000,
        "pct": 57,
    },
    "ladderarb": {
        "state_file": "~/Documents/polymarket/ladderarb.json",
        "log_file": "/tmp/bot_ladderarb.log",
        "capital": 3000,
        "pct": 43,
    },
}

TOTAL_CAPITAL = 7000
SILENCE_THRESHOLD_MIN = 60  # Alert if >1h silent
LOSS_THRESHOLD = 0.15  # Alert if -15% of capital
PORTFOLIO_LOSS_THRESHOLD = 0.10  # Alert if -10% total


def check_bot_health(bot_name, config):
    """Check one bot's health. Return (status: str, pnl: float, last_update: datetime)."""
    state_file = os.path.expanduser(config["state_file"])
    log_file = os.path.expanduser(config["log_file"])

    # Load state
    if not os.path.exists(state_file):
        return "missing_state", 0.0, None

    try:
        state = json.load(open(state_file))
    except Exception as e:
        return f"state_corrupt: {e}", 0.0, None

    pnl = state.get("cumulative_pnl", 0.0)

    # Check last log update
    if os.path.exists(log_file):
        last_update = datetime.fromtimestamp(os.path.getmtime(log_file))
        age_min = (datetime.now() - last_update).total_seconds() / 60
        if age_min > SILENCE_THRESHOLD_MIN:
            return f"silent_{int(age_min)}min", pnl, last_update
    else:
        return "log_missing", pnl, None

    return "healthy", pnl, datetime.fromtimestamp(os.path.getmtime(log_file))


def alert_iMessage(title, body):
    """Send iMessage alert."""
    osa = ('on run argv\n'
           'tell application "Messages"\n'
           '  set s to 1st service whose service type = iMessage\n'
           '  set b to buddy (item 1 of argv) of s\n'
           '  send (item 2 of argv) to b\n'
           'end tell\n'
           'end run')

    targets = ["krisharyan@icloud.com", "+918449447444"]
    msg = f"[PORTFOLIO] {title}\n{body}"

    for target in targets:
        try:
            subprocess.run(["osascript", "-e", osa, target, msg],
                          capture_output=True, text=True, timeout=10)
        except Exception as e:
            sys.stderr.write(f"Alert failed: {e}\n")


def run_health_check():
    """Run full portfolio health check."""
    print(f"\n[{datetime.now().isoformat()}] Portfolio Health Check")
    print("-" * 80)

    results = {}
    total_pnl = 0.0
    alerts = []

    for bot_name, config in BOTS.items():
        status, pnl, last_update = check_bot_health(bot_name, config)
        results[bot_name] = {"status": status, "pnl": pnl, "last_update": last_update}
        total_pnl += pnl

        # Logging
        pnl_pct = (pnl / config["capital"]) * 100 if config["capital"] > 0 else 0
        print(f"{bot_name:<15} ${pnl:>8.2f} ({pnl_pct:>6.1f}%) {status:<20}")

        # Check alerts
        if status != "healthy":
            alerts.append(f"{bot_name}: {status}")

        if pnl < -config["capital"] * LOSS_THRESHOLD:
            alerts.append(f"{bot_name}: LOSS LIMIT ({pnl_pct:.1f}%)")

    print("-" * 80)
    portfolio_pct = (total_pnl / TOTAL_CAPITAL) * 100
    print(f"{'PORTFOLIO':<15} ${total_pnl:>8.2f} ({portfolio_pct:>6.1f}%)")

    if total_pnl < -TOTAL_CAPITAL * PORTFOLIO_LOSS_THRESHOLD:
        alerts.append(f"PORTFOLIO LOSS: {portfolio_pct:.1f}%")

    # Send alerts
    if alerts:
        alert_iMessage("⚠️ PORTFOLIO ALERT", "\n".join(alerts))
        print(f"\n⚠️  ALERTS SENT: {len(alerts)}")
        for alert in alerts:
            print(f"  - {alert}")

    # Log to file
    log_file = os.path.expanduser("~/Documents/polymarket/bot_portfolio_health.log")
    with open(log_file, "a") as f:
        f.write(f"{datetime.now().isoformat()} | Total P&L: ${total_pnl:+.2f} ({portfolio_pct:+.1f}%) | Alerts: {len(alerts)}\n")

    return results


if __name__ == "__main__":
    results = run_health_check()
    print("\nHealth check complete.")
