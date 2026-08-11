#!/bin/bash
# run_panel.command — double-click to run the 3-analyst debate panel NOW.
# Three independent-view analysts (momentum / liquidity / news) argue YES vs NO;
# a judge bets via blend / consensus / agreement-gated rules. Paper-only.
cd "$(dirname "$0")" || exit 1
PY="$(command -v python3)"
echo "=== Analyst-panel debate — $(date) ==="
"$PY" ml/analyst_panel.py "$@"
echo
echo "Result saved to ml/analyst_panel_result.json"
echo "Press Return to close."
read _
