#!/usr/bin/env python3
"""
bot_newstrategy.py — Design phase (NOT TRADING).

Candidate: candlesig 8h mean-reversion on crypto
Capital: $500 (5%)
Status: awaiting full signal spec
"""

import json, os, sys
from datetime import datetime
from bot_runner import BotBase

class BotNewstrategy(BotBase):
    def __init__(self, capital_usdc=500, state_file="~/Documents/polymarket/newstrategy.json"):
        super().__init__("newstrategy", capital_usdc, state_file)
        self.edge_type = "candlesig_8h_meanrev"
        self.status = "design_phase"

    def entry_signal(self, market):
        """PLACEHOLDER: not trading yet."""
        return False, 0.0

    def position_size(self, bankroll, conviction):
        """PLACEHOLDER: not trading yet."""
        return 0.0

    def exit_signal(self, open_pos, market):
        """PLACEHOLDER: not trading yet."""
        return False, "", 0.0

    def run(self, markets):
        """Design phase: no trades."""
        return {
            "bot": self.name,
            "status": "design_phase",
            "candidate_edge": self.edge_type,
            "message": "Not trading yet - awaiting signal specification",
            "open_count": 0,
            "cumulative_pnl": 0.0,
            "wr": 0.0,
        }


if __name__ == "__main__":
    markets = []
    bot = BotNewstrategy()
    result = bot.run(markets)
    print(json.dumps(result, indent=2))
