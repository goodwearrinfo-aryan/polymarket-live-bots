#!/usr/bin/env python3
"""
bot_fade.py — Contrarian fade on overheated YES.

Backtest: 12 trades, 67% WR, +$0.0667/trade
Capital: $1,500 (15%)
Mode: observation (small capital, build confidence)
"""

import json, os, sys
from datetime import datetime
from bot_runner import BotBase

class BotFade(BotBase):
    def __init__(self, capital_usdc=1500, state_file="~/Documents/polymarket/fade.json"):
        super().__init__("fade", capital_usdc, state_file)
        self.target_band = (0.40, 0.70)
        self.target_exit_mult = 3.0
        self.stop_loss = 0.30
        self.conviction_threshold = 0.60

    def entry_signal(self, market):
        """NO on overheated YES [0.40, 0.70] (non-esports)."""
        q = market.get("q", "").lower()
        yes_price = market.get("yes", 0.5)

        in_band = self.target_band[0] <= yes_price <= self.target_band[1]
        not_esports = not any(x in q for x in ["esport", "dota", "league", "cs2"])

        if in_band and not_esports:
            return True, 0.67
        return False, 0.0

    def position_size(self, bankroll, conviction):
        """Conservative: 3% per trade (observation mode)."""
        if conviction < self.conviction_threshold:
            return 0.0
        return bankroll * 0.03

    def exit_signal(self, open_pos, market):
        """SHORT YES (NO position): exit if YES drops or rises."""
        entry = open_pos.get("entry_price", 0.5)
        size = open_pos.get("size_usdc", 100)
        current = market.get("yes", 0.5)

        pnl = (entry - current) / entry * size if entry > 0 else 0

        if current <= entry / self.target_exit_mult:
            return True, "target_3x_yes_drop", pnl
        if current >= entry * (1 + self.stop_loss):
            return True, "stop_30pct_yes_rise", pnl

        return False, "", 0.0

    def run(self, markets):
        """Main bot logic."""
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
        }


if __name__ == "__main__":
    markets = [
        {"id": "fade_1", "q": "Will the Fed raise rates?", "yes": 0.65},
        {"id": "fade_2", "q": "Trump indicted?", "yes": 0.55},
    ]

    bot = BotFade()
    result = bot.run(markets)
    print(json.dumps(result, indent=2))
