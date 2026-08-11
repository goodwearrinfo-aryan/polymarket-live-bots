#!/usr/bin/env python3
"""
bot_fastfade.py — Fast-window fade (INVESTIGATION MODE).

Backtest: 62% WR but -$0.0772/trade (ANOMALY)
Capital: $1,000 (10%)
Mode: diagnosis (tiny bets, heavy logging)
"""

import json, os, sys
from datetime import datetime
from bot_runner import BotBase

class BotFastfade(BotBase):
    def __init__(self, capital_usdc=1000, state_file="~/Documents/polymarket/fastfade.json"):
        super().__init__("fastfade", capital_usdc, state_file)
        self.target_band = (0.50, 0.80)
        self.target_exit_mult = 3.0
        self.stop_loss = 0.30
        self.max_window_minutes = 120
        self.conviction_threshold = 0.60

    def entry_signal(self, market):
        """NO on panicked YES in fast windows (<2h)."""
        q = market.get("q", "").lower()
        yes_price = market.get("yes", 0.5)
        mins_to_close = market.get("minutes_to_close", 1000)

        in_band = self.target_band[0] <= yes_price <= self.target_band[1]
        in_window = mins_to_close <= self.max_window_minutes

        if in_band and in_window:
            return True, 0.62
        return False, 0.0

    def position_size(self, bankroll, conviction):
        """TINY: 2% (investigation mode)."""
        if conviction < self.conviction_threshold:
            return 0.0
        return bankroll * 0.02

    def exit_signal(self, open_pos, market):
        """Same: +3x or -30%."""
        entry = open_pos.get("entry_price", 0.5)
        size = open_pos.get("size_usdc", 100)
        current = market.get("yes", 0.5)

        pnl = (entry - current) / entry * size if entry > 0 else 0

        if current <= entry / self.target_exit_mult:
            return True, "target_3x", pnl
        if current >= entry * (1 + self.stop_loss):
            return True, "stop_30pct", pnl

        return False, "", 0.0

    def run(self, markets):
        """Main bot logic (with heavy logging)."""
        entries = 0
        exits = 0

        for market in markets:
            if len(self.state["open"]) >= 5:
                break

            should_enter, conviction = self.entry_signal(market)
            if not should_enter:
                continue

            size = self.position_size(self.capital, conviction)
            if size <= 0:
                continue

            self.state["open"].append({
                "id": market.get("id"),
                "q": market.get("q"),
                "entry_price": market.get("yes", 0.5),
                "size_usdc": size,
                "conviction": conviction,
                "opened_at": datetime.now().isoformat(),
            })
            entries += 1

        still_open = []
        for pos in self.state["open"]:
            market = next((m for m in markets if m.get("id") == pos["id"]), None)
            if not market:
                still_open.append(pos)
                continue

            should_exit, reason, pnl = self.exit_signal(pos, market)
            if should_exit:
                pos["closed_at"] = datetime.now().isoformat()
                pos["pnl_usdc"] = pnl
                pos["exit_reason"] = reason
                self.state["closed"].append(pos)
                self.state["cumulative_pnl"] += pnl
                self.state["trades_closed"] += 1
                exits += 1

                if self.state["closed"]:
                    wins = sum(1 for t in self.state["closed"] if t.get("pnl_usdc", 0) > 0)
                    self.state["wr"] = wins / len(self.state["closed"])
            else:
                still_open.append(pos)

        self.state["open"] = still_open
        self._save_state()

        return {
            "bot": self.name,
            "entries": entries,
            "exits": exits,
            "open_count": len(self.state["open"]),
            "cumulative_pnl": self.state["cumulative_pnl"],
            "wr": self.state["wr"],
            "trades_closed": self.state["trades_closed"],
            "note": "Investigation mode: good WR but negative P&L",
        }


if __name__ == "__main__":
    markets = [
        {"id": "fast_1", "q": "Team A to win (30 min left)?", "yes": 0.75, "minutes_to_close": 30},
        {"id": "fast_2", "q": "Score >45 (60 min left)?", "yes": 0.62, "minutes_to_close": 60},
    ]

    bot = BotFastfade()
    result = bot.run(markets)
    print(json.dumps(result, indent=2))
