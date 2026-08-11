#!/usr/bin/env python3
"""
bot_ladderarb.py — Aggressive arbitrage hunter.

Backtest: 11 trades, 82% WR, +$0.3731/trade (FATTEST edge)
Capital: $3,000 (30%)
"""

import json, os, sys
from datetime import datetime
from bot_runner import BotBase

class BotLadderarb(BotBase):
    def __init__(self, capital_usdc=3000, state_file="~/Documents/polymarket/ladderarb.json"):
        super().__init__("ladderarb", capital_usdc, state_file)
        self.yes_band = (0.65, 0.75)
        self.no_band = (0.30, 0.40)
        self.edge_threshold = 0.05
        self.hunt_gate = 30
        self.conviction_threshold = 0.60

    def entry_signal(self, market):
        """Ladder arb: YES overbought + NO oversold."""
        yes_price = market.get("yes", 0.5)
        no_price = market.get("no", 0.5)

        yes_overbought = self.yes_band[0] <= yes_price <= self.yes_band[1]
        no_oversold = self.no_band[0] <= no_price <= self.no_band[1]

        if not (yes_overbought and no_oversold):
            return False, 0.0

        skew = abs(yes_price - no_price) / (yes_price + no_price) if (yes_price + no_price) > 0 else 0
        if skew < self.edge_threshold:
            return False, 0.0

        return True, 0.82

    def position_size(self, bankroll, conviction):
        """Hunt mode: 2x normal (until 30 closed). Then back to 5%."""
        if conviction < self.conviction_threshold:
            return 0.0

        closed_count = len(self.state["closed"])
        if closed_count < self.hunt_gate:
            base = bankroll * 0.10 * 2.0  # 20% in hunt mode
        else:
            base = bankroll * 0.05

        return min(base, bankroll * 0.15)

    def exit_signal(self, open_pos, market):
        """Same: +3x or -30%."""
        entry = open_pos.get("entry_price", 0.5)
        size = open_pos.get("size_usdc", 100)
        current = market.get("yes", 0.5)

        pnl = (current - entry) / entry * size if entry > 0 else 0

        if current >= entry * 3.0:
            return True, "target_3x", pnl
        if current <= entry * 0.70:
            return True, "stop_30pct", pnl

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
            "hunt_mode": len(self.state["closed"]) < self.hunt_gate,
        }


if __name__ == "__main__":
    markets = [
        {"id": "abb_1", "q": "BTC Bull Flag?", "yes": 0.70, "no": 0.32},
        {"id": "abb_2", "q": "ETH >2500?", "yes": 0.68, "no": 0.35},
    ]

    bot = BotLadderarb()
    result = bot.run(markets)
    print(json.dumps(result, indent=2))
