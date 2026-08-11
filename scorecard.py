#!/usr/bin/env python3
"""
scorecard.py — Resolution-verified edge tracker.

For each analyst bet:
  1. Log entry (conviction, thesis, market state)
  2. Track market price over time
  3. On resolution: measure realized vs predicted
  4. Compute Brier score (calibration)
  5. Validate with 3-lens panel (correctness, base-rate, security)

Only bets that SURVIVE 3-lens refutation go into portfolio.
"""

import json, os, sys
from datetime import datetime

class Scorecard:
    def __init__(self, scorecard_file="~/Documents/polymarket/scorecard.json"):
        self.file = os.path.expanduser(scorecard_file)
        self.bets = self._load()

    def _load(self):
        if os.path.exists(self.file):
            return json.load(open(self.file))
        return {"active": [], "resolved": [], "refuted": []}

    def _save(self):
        tmp = self.file + ".tmp"
        json.dump(self.bets, open(tmp, "w"), indent=2)
        os.replace(tmp, self.file)

    def log_entry(self, market_id, question, entry_price, conviction, thesis):
        """Log a new analyst bet."""
        bet = {
            "id": market_id,
            "q": question,
            "entry_price": entry_price,
            "conviction": conviction,
            "thesis": thesis,
            "logged_at": datetime.now().isoformat(),
            "panel_verdicts": {},
            "resolved_at": None,
            "resolution_price": None,
            "brier_score": None,
        }
        self.bets["active"].append(bet)
        self._save()
        return bet

    def add_panel_verdict(self, market_id, lens, survived_refutation):
        """Record 3-lens panel verdict: correctness, base_rate, security."""
        for bet in self.bets["active"]:
            if bet["id"] == market_id:
                bet["panel_verdicts"][lens] = survived_refutation
                self._save()
                return bet
        return None

    def resolve(self, market_id, resolution_price, outcome_yes_or_no):
        """Mark a bet as resolved."""
        for bet in self.bets["active"]:
            if bet["id"] == market_id:
                verdicts = bet["panel_verdicts"]
                survived_all = all(verdicts.get(lens, False) for lens in ["correctness", "base_rate", "security"])

                bet["resolved_at"] = datetime.now().isoformat()
                bet["resolution_price"] = resolution_price
                bet["outcome"] = outcome_yes_or_no

                outcome_binary = 1.0 if outcome_yes_or_no == "YES" else 0.0
                bet["brier_score"] = (bet["conviction"] - outcome_binary) ** 2

                if survived_all:
                    self.bets["resolved"].append(bet)
                else:
                    self.bets["refuted"].append(bet)

                self.bets["active"].remove(bet)
                self._save()
                return bet
        return None

    def calibration_report(self):
        """Brier score and calibration by conviction band."""
        if not self.bets["resolved"]:
            return {"message": "No resolved bets yet"}

        resolved = self.bets["resolved"]
        avg_brier = sum(b.get("brier_score", 0) for b in resolved) / len(resolved)

        return {
            "total_resolved": len(resolved),
            "avg_brier": avg_brier,
            "avg_conviction": sum(b["conviction"] for b in resolved) / len(resolved),
        }

    def status(self):
        """Portfolio status."""
        return {
            "active": len(self.bets["active"]),
            "resolved": len(self.bets["resolved"]),
            "refuted": len(self.bets["refuted"]),
            "active_bets": self.bets["active"],
        }


if __name__ == "__main__":
    sc = Scorecard()
    print(json.dumps(sc.status(), indent=2))
