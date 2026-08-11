#!/bin/bash
BOT_DIR="/Users/aryanagarwal/Library/Application Support/Claude/local-agent-mode-sessions/d8dec959-d81d-4188-bd99-cbbf637922c3/53d36ba2-9737-4cb0-a403-f4792d7f53c2/local_416c1449-2db6-4813-8a0b-6bdfd71699f3/outputs"
LOG="$BOT_DIR/run_output.txt"; WLOG="$BOT_DIR/watchdog.log"; STALE=900
# Framework Python 3.14 (has requests + full deps; same interpreter the Documents bot runs).
PY="/Library/Frameworks/Python.framework/Versions/3.14/bin/python3"; [ -x "$PY" ] || PY="$(command -v python3)"
ts(){ date "+%Y-%m-%d %H:%M:%S"; }
note(){ echo "[$(ts)] $*" >> "$WLOG"; }
alert(){ osascript -e "display notification \"$1\" with title \"Polymarket watchdog\"" 2>/dev/null || true; }
# PATH-ANCHORED: pgrep/pkill only this dir's bot so it can never touch the Documents bot.
start_bot(){ cd "$BOT_DIR" || { note "ERROR cd"; return 1; }; nohup "$PY" "$BOT_DIR/polymarket_bot.py" --dry-run >> "$LOG" 2>&1 < /dev/null & sleep 10; if pgrep -f "$BOT_DIR/polymarket_bot.py" >/dev/null; then note "STARTED OK"; alert "Bot was down - restarted."; else note "START FAILED"; fi; }
proc_up(){ pgrep -f "$BOT_DIR/polymarket_bot.py" >/dev/null; }
log_age(){ [ -f "$LOG" ] || { echo 999999; return; }; echo $(( $(date +%s) - $(stat -f %m "$LOG") )); }
age=$(log_age)
if ! proc_up; then note "DOWN - starting"; start_bot
elif [ "$age" -gt "$STALE" ]; then note "HUNG - log silent ${age}s, killing"; pkill -f "$BOT_DIR/polymarket_bot.py"; sleep 12; if ! proc_up; then start_bot; else note "relaunched by wrapper"; fi
else note "HEALTHY - ${age}s ago"; fi

# Phone PDF: once this bot holds open trades, (re)generate "Live Trades.pdf" into a
# SEPARATE iCloud folder (PolymarketBot-CopyBot) so it doesn't clobber the primary's.
if [ -f "$BOT_DIR/trade_log.json" ] && "$PY" -c "import json,sys; sys.exit(0 if json.load(open('$BOT_DIR/trade_log.json')).get('open') else 1)" 2>/dev/null; then
  "$PY" "$BOT_DIR/trades_report.py" "$BOT_DIR/trade_log.json" "$HOME/Library/Mobile Documents/com~apple~CloudDocs/PolymarketBot-CopyBot" >/dev/null 2>&1 && note "trades PDF -> iCloud/PolymarketBot-CopyBot"
fi

# A/B time-series logger (durable): append both books' totals each cycle. Read-only; fail-soft.
[ -f "$BOT_DIR/ab_logger.py" ] && "$PY" "$BOT_DIR/ab_logger.py" "$BOT_DIR/trade_log.json" "/Users/aryanagarwal/Documents/polymarket/trade_log.json" "$BOT_DIR/ab_log.tsv" >/dev/null 2>&1 || true
