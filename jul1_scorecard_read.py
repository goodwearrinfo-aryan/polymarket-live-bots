#!/usr/bin/env python3
"""
jul1_scorecard_read.py — autonomous honest read of the analyst scoreboard after Jun-30.

Most analyst positions resolve 2026-06-30, but a NUMBER nobody reads with the cluster
caveat is useless. This runs the (cluster-corrected) scorecard and iMessages a plain-English
read that leads with the honesty rail: ~2 EFFECTIVE independent draws (4 of 5 Jun-30
resolutions are one Mideast cluster), so this is a CHECK, not an edge verdict.

Fires Jul-1 via launchd (com.aryan.jul1-scorecard-read). Read-only.
"""
import os, subprocess, sys
from datetime import datetime, timezone

PM = os.path.dirname(os.path.abspath(__file__))
VAULT = os.path.expanduser("~/Documents/PolymarketVault/Reports/Jun30 Scorecard Read.md")

CAVEAT = ("⚠️ HONEST FRAME: of the positions resolving Jun-30, ~4/5 are ONE Mideast cluster "
          "(Iran/Israel/Hormuz) → effective independent draws ≈2, not 5. Even all-favorable, "
          "the cluster CI will be wide and almost certainly straddle 0. This is a CHECK on a "
          "couple of correlated bets, NOT proof of edge. The real edge test stays forward "
          "(structural-arb resolutions, slow). Read accordingly.")

def imessage(body):
    s = (f'tell application "Messages"\n set s to 1st service whose service type = iMessage\n'
         f' send "{body}" to buddy "krisharyan@icloud.com" of s\nend tell')
    try: subprocess.run(["osascript","-e",s], capture_output=True, timeout=10)
    except Exception: pass

def main():
    try:
        out = subprocess.run([sys.executable, os.path.join(PM,"analyst_resolution_scorecard.py")],
                             capture_output=True, text=True, timeout=120, cwd=PM).stdout.strip()
    except Exception as e:
        out = f"(scorecard run failed: {e})"
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    note = (f"---\ntype: dashboard\nstatus: live\n---\n\n# 🏁 Jun-30 Analyst Scorecard — honest read\n\n"
            f"*Auto {ts} by jul1_scorecard_read.py.*\n\n> {CAVEAT}\n\n## Scorecard\n```\n{out}\n```\n")
    try:
        os.makedirs(os.path.dirname(VAULT), exist_ok=True); open(VAULT,"w").write(note)
    except Exception as e:
        print(f"vault write failed: {e}", file=sys.stderr)
    imessage(f"🏁 Jun-30 analyst scorecard is in. {CAVEAT[:180]}")
    print("wrote scorecard read + iMessage")
    sys.exit(0)

if __name__ == "__main__":
    main()
