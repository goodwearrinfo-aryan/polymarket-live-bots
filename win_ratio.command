#!/bin/bash
# Win-Ratio Dashboard — double-click to open a live updating view
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
clear
echo "📊  Polymarket Win-Ratio Tracker — live view (Ctrl+C to stop)"
echo ""
python3 "$DIR/win_tracker.py" --watch
