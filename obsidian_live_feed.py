#!/usr/bin/env python3
"""
obsidian_live_feed.py — Live sync of analyst data to Obsidian vault.

Runs every 60s. Mirrors:
  - analyst.json → Analyst Positions.md
  - scorecard.json → Analyst Calibration.md
  - judge_panel verdicts → Judge Panel Verdicts.md
  - live market prices → Live Market Data.md

Feeds other bots via Obsidian queries (e.g. bot_newsmove reads from News Thesis.md).
"""

import json
import os
from datetime import datetime
from pathlib import Path

class ObsidianLiveFeed:
    def __init__(self):
        self.polymarket_dir = Path(os.path.expanduser("~/Documents/polymarket"))
        self.obsidian_dir = Path(os.path.expanduser("~/Documents/PolymarketVault/Analyst"))  # canonical vault (was dead ~/vaults/polymarket, no .obsidian)
        self.analyst_file = self.polymarket_dir / "analyst.json"
        self.scorecard_file = self.polymarket_dir / "scorecard.json"
        self.obsidian_dir.mkdir(parents=True, exist_ok=True)
    
    def sync_analyst_positions(self):
        """Mirror open analyst bets to Obsidian."""
        if not self.analyst_file.exists():
            return
        
        state = json.load(open(self.analyst_file))
        md = "# Analyst Positions\n\n"
        md += f"**Updated**: {datetime.now().isoformat()}\n"
        md += f"**Capital**: ${state.get('capital_usdc', 0)}\n"
        md += f"**Open bets**: {len(state.get('open', []))}\n"
        md += f"**Closed bets**: {len(state.get('closed', []))}\n"
        md += f"**P&L**: ${state.get('cumulative_pnl', 0):.2f}\n\n"
        
        md += "## Open Positions\n\n"
        for bet in state.get('open', []):
            md += f"### {bet['q']}\n"
            md += f"- Entry price: {bet.get('entry_price', 0):.2f}\n"
            md += f"- Conviction: {bet.get('conviction', 0):.0%}\n"
            md += f"- Size: ${bet.get('size_usdc', 0):.0f}\n"
            md += f"- Thesis: {bet.get('thesis', 'N/A')}\n"
            md += f"- Opened: {bet.get('opened_at', 'N/A')}\n\n"
        
        (self.obsidian_dir / "Analyst Positions.md").write_text(md)
    
    def sync_calibration(self):
        """Mirror Brier scores to Obsidian."""
        if not self.scorecard_file.exists():
            return
        
        scorecard = json.load(open(self.scorecard_file))
        md = "# Analyst Calibration\n\n"
        md += f"**Updated**: {datetime.now().isoformat()}\n\n"
        
        if scorecard.get('calibration_report'):
            md += "## Brier Scores by Conviction Band\n\n"
            for band, stats in scorecard['calibration_report'].items():
                md += f"### {band}\n"
                md += f"- Brier: {stats.get('brier_score', 0):.3f}\n"
                md += f"- Samples: {stats.get('count', 0)}\n"
                md += f"- Accuracy: {stats.get('accuracy', 0):.1%}\n\n"
        
        (self.obsidian_dir / "Analyst Calibration.md").write_text(md)
    
    def sync_candidates(self):
        """Mirror the analyst_hunt review queue (real-data candidates) to Obsidian.

        This is the feed other bots / the daily edge-analyst task read to pick
        up fresh research-worthy markets surfaced from live Polymarket data."""
        queue_file = self.polymarket_dir / "analyst_candidates.json"
        if not queue_file.exists():
            return
        try:
            q = json.load(open(queue_file))
        except Exception:
            return
        md = "# Analyst Candidate Queue\n\n"
        md += f"**Updated**: {datetime.now().isoformat()}\n"
        md += f"**Pending review**: {q.get('pending_review', 0)}\n"
        md += f"**Source**: analyst_hunt.py (live gamma-api scan, every 6h)\n\n"
        md += "> Review queue — NOT live bets. Research, write a thesis + conviction, "
        md += "run judge_panel.py, then deploy.\n\n"
        md += "## Candidates (ranked by research-worthiness)\n\n"
        md += "| Score | Category | YES | Days | Volume | Question |\n"
        md += "|------:|----------|----:|-----:|-------:|----------|\n"
        for c in q.get("candidates", []):
            md += (f"| {c.get('score',0):.2f} | {c.get('category','?')} | "
                   f"{c.get('yes_price',0):.2f} | {c.get('days_to_resolve','?')} | "
                   f"${c.get('volume',0):,} | {c.get('question','')[:60]} |\n")
        (self.obsidian_dir / "Analyst Candidate Queue.md").write_text(md)

    def sync_index(self):
        """Create Obsidian index."""
        md = "# Analyst Edge Knowledge Graph\n\n"
        md += "## Core Concepts\n"
        md += "- [[Analyst Positions]] - live bets (Book 2)\n"
        md += "- [[Analyst Candidate Queue]] - real-data hunt output (feed for research)\n"
        md += "- [[Analyst Calibration]] - Brier score tracking\n"
        md += "- [[Judge Panel Verdicts]] - 3-lens refutation panel\n"
        md += "- [[Live Market Data]] - real-time prices\n\n"
        md += "## Categories\n"
        md += "- Geopolitical (Israel-Hezbollah, Taiwan, Iran)\n"
        md += "- Macro (Fed rates, CPI, employment)\n"
        md += "- Crypto (regulatory clarity, adoption, BTC/ETH)\n\n"
        md += "## Codebase Knowledge Graph\n"
        md += "- [[codegraph/index|Codebase graph]] - 2,175 nodes, 215 communities (graphify)\n"
        md += "- Open `codegraph/graph.canvas` in Obsidian, or `graphify-out/graph.html` in a browser\n\n"
        md += "## Self-Organizing Brain\n"
        md += "- [[brain/index|Brain index]] - drop notes in `_inbox/`, auto-filed into PARA + wiki-linked\n"
        md += "- Runs every 5 min (brain_ingest.py); fuses 5 vetted second-brain patterns, sandboxed to `brain/`\n\n"
        md += f"**Last sync**: {datetime.now().isoformat()}\n"
        
        (self.obsidian_dir / "index.md").write_text(md)
    
    def run(self):
        """Run all syncs."""
        self.sync_analyst_positions()
        self.sync_calibration()
        self.sync_candidates()
        self.sync_index()
        print(f"[obsidian_live_feed] Synced to {self.obsidian_dir}")

if __name__ == "__main__":
    feed = ObsidianLiveFeed()
    feed.run()
