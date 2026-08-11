#!/bin/bash
# Double-click to start the bot watchdog. It launches the watchdog loop fully
# detached (survives closing this window) and then exits.
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

# Restart cleanly: stop any existing watchdog, then start fresh.
pkill -f "watchdog_loop.sh" 2>/dev/null && echo "Stopped old watchdog." && sleep 1

chmod +x "$DIR/watchdog_loop.sh" 2>/dev/null
nohup "$DIR/watchdog_loop.sh" >> "$DIR/watchdog.log" 2>&1 < /dev/null &
disown 2>/dev/null || true

echo "✅ Watchdog started in the background."
echo "   It checks the bot every 60s and relaunches it if it dies or hangs."
echo "   Activity is logged to watchdog.log."
echo ""
echo "This window can be closed — the watchdog keeps running."
sleep 4
