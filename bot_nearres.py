#!/usr/bin/env python3
"""
bot_nearres.py — Steady, conviction-based nearres bot.

Strategy:
  - Esports favorites 1-30d to resolution
  - Entry: YES [0.22, 0.52] mid
  - Exit: +3x or -30% stop
  - Capital: $4,000 (40% of $10k portfolio)
  - Gates: gate_3_conviction_only + gate_4_kelly_steroids
  - Mode: steady accumulation

Backtest: 55 trades, 90% WR, +$0.0271/trade
"""

import json, os, sys
from datetime import datetime
from bot_runner import BotBase

class BotNearres(BotBase):
    def __init__(self, capital_usdc=4000, state_file="~/Documents/polymarket/nearres.json"):
        super().__init__("nearres", capital_usdc, state_file)
        self.target_band = (0.22, 0.52)
        self.target_exit_mult = 3.0
        self.stop_loss = 0.30
        self.max_daysout = 30
        self.conviction_threshold = 0.60

    def entry_signal(self, market):
        """Esports YES [0.22, 0.52], <30d. Conviction: 90%."""
        q = market.get("q", "").lower()
        yes_price = market.get("yes", 0.5)
        daysout = market.get("daysout", 100)

        is_esports = any(x in q for x in ["esport", "dota", "league", "cs2", "valorant"])
        in_band = self.target_band[0] <= yes_price <= self.target_band[1]
        in_time = daysout <= self.max_daysout

        if is_esports and in_band and in_time:
            return True, 0.90
        return False, 0.0

    def position_size(self, bankroll, conviction):
        """5% base + Kelly steroids (scale on wins)."""
        if conviction < self.conviction_threshold:
            return 0.0

        base = bankroll * 0.05
        mult = 1.0

        # Scale on profit
        pnl = self.state.get("cumulative_pnl", 0.0)
        if pnl > bankroll * 0.20:
            mult *= 1.25
        elif pnl > bankroll * 0.10:
            mult *= 1.10

        # Scale on 3-win streak
        recent = [t.get("pnl_usdc", 0) for t in self.state["closed"][-3:]]
        if len(recent) == 3 and all(p > 0 for p in recent):
            mult *= 1.15

        return min(base * mult, bankroll * 0.10)

    def exit_signal(self, open_pos, market):
        """+3x target or -30% stop."""
        entry = open_pos.get("entry_price", 0.5)
        size = open_pos.get("size_usdc", 100)
        current = market.get("yes", 0.5)

        pnl = (current - entry) / entry * size if entry > 0 else 0

        if current >= entry * self.target_exit_mult:
            return True, "target_3x", pnl
        if current <= entry * (1 - self.stop_loss):
            return True, "stop_30pct", pnl

        return False, "", 0.0

    def run(self, markets):
        """Main bot logic: entry, exit, state update."""
        entries = 0
        exits = 0

        # Process entries
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

        # Process exits
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

                # Recalc WR
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
    # Test with mock markets
    markets = [
        {"id": "esports_1", "q": "Dota2: TI Grand Final, Team A?", "yes": 0.30, "daysout": 3},
        {"id": "esports_2", "q": "LoL LEC: G2 to win?", "yes": 0.45, "daysout": 5},
        {"id": "crypto_1", "q": "BTC up 10%?", "yes": 0.55, "daysout": 7},
    ]

    bot = BotNearres()
    result = bot.run(markets)
    print(json.dumps(result, indent=2))
