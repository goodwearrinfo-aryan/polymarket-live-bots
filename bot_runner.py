#!/usr/bin/env python3
"""
bot_runner.py — Minimal base class for independent trading bots.

Each bot inherits from BotBase and implements:
  - entry_signal(market) → (should_enter: bool, conviction: float)
  - position_size(bankroll, conviction) → position_usdc: float
  - exit_signal(open_pos, market) → (should_exit: bool, reason: str, pnl: float)
  - run(markets) → result dict

BotBase provides:
  - State management (load/save JSON)
  - iMessage alerts
  - That's it. Rest is up to the bot.
"""

import json, os, sys, time, subprocess
from datetime import datetime
from abc import ABC, abstractmethod

class BotBase(ABC):
    def __init__(self, bot_name, capital_usdc, state_file):
        self.name = bot_name
        self.capital = capital_usdc
        self.state_file = os.path.expanduser(state_file)
        self.state = self._load_state()
        self.alert_targets = ["krisharyan@icloud.com", "+918449447444"]

    def _load_state(self):
        """Load state from JSON, or initialize empty."""
        if os.path.exists(self.state_file):
            try:
                return json.load(open(self.state_file))
            except Exception as e:
                sys.stderr.write(f"[{self.name}] state load failed: {e}\n")
                return self._empty_state()
        return self._empty_state()

    def _empty_state(self):
        """Return fresh state dict."""
        return {
            "open": [],
            "closed": [],
            "cumulative_pnl": 0.0,
            "trades_closed": 0,
            "wr": 0.0,
        }

    def _save_state(self):
        """Atomic save: write to .tmp, then replace."""
        try:
            tmp = self.state_file + ".tmp"
            with open(tmp, "w") as f:
                json.dump(self.state, f, indent=2)
            os.replace(tmp, self.state_file)
        except Exception as e:
            sys.stderr.write(f"[{self.name}] state save failed: {e}\n")

    def _send_alert(self, title, body):
        """Send iMessage alert (best effort, don't crash if it fails)."""
        osa = ('on run argv\n'
               'tell application "Messages"\n'
               '  set s to 1st service whose service type = iMessage\n'
               '  set b to buddy (item 1 of argv) of s\n'
               '  send (item 2 of argv) to b\n'
               'end tell\n'
               'end run')

        msg = f"[{self.name}] {title}\n{body}"
        for target in self.alert_targets:
            try:
                subprocess.run(["osascript", "-e", osa, target, msg],
                              capture_output=True, text=True, timeout=5)
            except Exception:
                pass  # Silent failure — don't crash bot over alert

    @abstractmethod
    def entry_signal(self, market):
        """Decide if this market is tradeable. Return (bool, conviction: float)."""
        pass

    @abstractmethod
    def position_size(self, bankroll, conviction):
        """Size for this conviction level. Return position_usdc: float."""
        pass

    @abstractmethod
    def exit_signal(self, open_pos, market):
        """Decide if this position should close. Return (bool, reason: str, pnl: float)."""
        pass

    @abstractmethod
    def run(self, markets):
        """Main bot logic. Accept markets, return result dict."""
        pass
