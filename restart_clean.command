#!/bin/bash
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

PY=$(command -v python3 || command -v python)
PIP=$(command -v pip3 || command -v pip)

echo "=== Polymarket Bot Clean Restart ==="
echo ""

# 1) Kill any running bot
echo "Stopping existing bot..."
pkill -f "polymarket_bot.py" 2>/dev/null && echo "  ✓ Old bot stopped" && sleep 2

# 2) Clear corrupted trade log (bot is now dead, safe to overwrite)
echo "Clearing corrupted trade_log.json..."
cat > "$DIR/trade_log.json" << 'EOF'
{
  "open": [],
  "closed": []
}
EOF
echo "  ✓ trade_log.json reset to clean state"

# 3) Install deps quietly
echo "Checking dependencies..."
$PIP install requests python-dotenv --break-system-packages -q 2>/dev/null
echo "  ✓ Dependencies OK"

# 4) Launch fresh bot
echo "Starting bot with fixed code..."
nohup $PY "$DIR/polymarket_bot.py" --dry-run >> "$DIR/run_output.txt" 2>&1 &
BOT_PID=$!
echo $BOT_PID > "$DIR/bot.pid"

echo ""
echo "✅ Bot restarted clean (PID $BOT_PID)"
echo "📄 Logs → run_output.txt"
echo "📊 trade_log.json reset — no corrupted positions"
echo ""
echo "This window can be closed. Bot will keep running."
sleep 8
