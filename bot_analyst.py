#!/usr/bin/env python3
"""
bot_analyst.py — Analyst track: manual high-conviction bets gated by 3-lens panel.

Capital: $5,000 (50% of portfolio)
Entry: Only high-conviction theses that survive refutation
Exit: Resolution (take YES/NO @ market price) or cut loss at -40%

Bets live in scorecard.json with verdicts tracked.
"""

import json, os, sys
from datetime import datetime
from bot_runner import BotBase

class BotAnalyst(BotBase):
    def __init__(self, capital_usdc=5000, state_file="~/Documents/polymarket/analyst.json"):
        super().__init__("analyst", capital_usdc, state_file)
        self.scorecard_file = os.path.expanduser("~/Documents/polymarket/scorecard.json")
        self.max_per_position = capital_usdc * 0.20  # Max $1k per bet

    def entry_signal(self, market):
        """
        PLACEHOLDER: Manual entry via scorecard.py
        
        In production:
        - Analyst researches market + thesis
        - Judges panel evaluates (3 lenses)
        - If survived: log to scorecard + return True
        - If refuted: return False
        """
        # For now: no auto-entry (manual only)
        return False, 0.0

    def position_size(self, bankroll, conviction):
        """Dynamic sizing based on conviction."""
        # Don't auto-size; use fixed per analyst bet
        return 0.0

    def exit_signal(self, open_pos, market):
        """Exit: resolution or -40% hard stop."""
        entry = open_pos.get("entry_price", 0.5)
        size = open_pos.get("size_usdc", 100)
        current = market.get("yes", 0.5)

        # For NO positions (shorting YES)
        pnl = (entry - current) / entry * size if entry > 0 else 0

        # Profit target: hold to resolution or +50%
        if current <= entry * 0.67 or current == 0:  # YES dropped 33% or resolved
            return True, "resolution_or_target", pnl

        # Hard stop: -40%
        if current >= entry * 1.40:
            return True, "stop_40pct", pnl

        return False, "", 0.0

    def run(self, markets):
        """Check exits for active analyst positions."""
        exits = 0
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
            "exits": exits,
            "open_count": len(self.state["open"]),
            "cumulative_pnl": self.state["cumulative_pnl"],
            "wr": self.state["wr"],
            "trades_closed": self.state["trades_closed"],
            "note": "Manual research + 3-lens panel gate",
        }

    def add_analyst_bet(self, market_id, question, entry_price, conviction, thesis):
        """
        Manually add a bet that survived panel.
        (In production: called by human after scorecard validation)
        """
        size = min(self.max_per_position, conviction * self.capital * 0.10)
        
        self.state["open"].append({
            "id": market_id,
            "q": question,
            "entry_price": entry_price,
            "size_usdc": size,
            "conviction": conviction,
            "thesis": thesis,
            "opened_at": datetime.now().isoformat(),
        })
        self._save_state()
        return self.state["open"][-1]


if __name__ == "__main__":
    markets = []  # No auto-markets; analyst positions are manual
    bot = BotAnalyst()
    
    # Example: add Israel-Hezbollah bet manually
    if len(sys.argv) > 1 and sys.argv[1] == "add_israel":
        bet = bot.add_analyst_bet(
            "israel_hezbollah_1",
            "Israel-Hezbollah conflict resolved by 2026-12-31?",
            0.86,  # NO @ 0.86
            0.65,
            "Regional stability prevails; escalation unlikely given cost to both sides."
        )
        print(f"Added bet: {bet['id']}")

    result = bot.run(markets)
    print(json.dumps(result, indent=2))
