#!/bin/bash
# run_oot.command — double-click to run the out-of-time (temporal) backtest NOW.
# Retrains on markets that resolved BEFORE the cutoff, tests on markets that
# resolved AFTER it. Paper-only; reads ml/dataset.csv, writes ml/backtest_oot_result.json.
cd "$(dirname "$0")" || exit 1
PY="$(command -v python3)"
echo "=== Out-of-time backtest — $(date) ==="
"$PY" ml/backtest_oot.py "$@"
echo
echo "Result saved to ml/backtest_oot_result.json"
echo "Tip: the test set only becomes a TRUE post-today sample once the daily"
echo "collector has added markets that resolved after today (~1-2 weeks)."
echo "Press Return to close."
read _
