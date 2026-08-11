#!/bin/bash
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

PY=$(command -v python3 || command -v python)
PIP=$(command -v pip3 || command -v pip)

# Kill any existing bot process
pkill -f "polymarket_bot.py" 2>/dev/null && echo "Stopped old bot instance." && sleep 1

# Install deps
$PIP install py-clob-client python-dotenv requests --break-system-packages -q 2>/dev/null || \
$PY -m pip install py-clob-client python-dotenv requests -q 2>/dev/null

# Launch in background, log everything to run_output.txt
nohup $PY "$DIR/polymarket_bot.py" --dry-run >> "$DIR/run_output.txt" 2>&1 &
BOT_PID=$!
echo $BOT_PID > "$DIR/bot.pid"

echo "✅ Bot started in background (PID $BOT_PID)"
echo "📄 Logs → run_output.txt"
echo "📊 Check status → run check_status.command"
echo ""
echo "This window can be closed. Bot will keep running."
sleep 5
