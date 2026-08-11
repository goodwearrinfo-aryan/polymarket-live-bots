#!/usr/bin/env bash
# crypto_tracking.sh — converted from the Claude scheduled task (token-free).
# Every 15 min: crypto snapshot + coinup/coindown leg health -> crypto_tracking.log
# Alarm: if coindown (control) is +EV while coinup is -EV => MARKING BUG -> iMessage.
LOG="$HOME/Documents/polymarket/crypto_tracking.log"
/Library/Frameworks/Python.framework/Versions/3.14/bin/python3 "$HOME/Documents/polymarket/crypto_data_report.py" >> "$LOG" 2>&1
HEALTH="$(/Library/Frameworks/Python.framework/Versions/3.14/bin/python3 "$HOME/Documents/polymarket/leg_health.py" 2>/dev/null)"
echo "$HEALTH" | grep -E "coinup|coindown|===|---" >> "$LOG"
echo "── $(date -u '+%Y-%m-%d %H:%M UTC') ─────────────────────────────────" >> "$LOG"
# marking-bug alarm (loud failure rule)
UP_PNL=$(echo "$HEALTH" | grep -i "coinup"  | grep -oE '[-+][0-9]+\.[0-9]+' | head -1)
DN_PNL=$(echo "$HEALTH" | grep -i "coindown"| grep -oE '[-+][0-9]+\.[0-9]+' | head -1)
if [ -n "$UP_PNL" ] && [ -n "$DN_PNL" ]; then
  if [ "$(echo "$DN_PNL > 0 && $UP_PNL < 0" | bc 2>/dev/null)" = "1" ]; then
    osascript -e "tell application \"Messages\"
      set s to 1st service whose service type = iMessage
      send \"🚨 MARKING BUG ALARM: coindown control +EV ($DN_PNL) while coinup -EV ($UP_PNL). Check scalp_lab marking.\" to buddy \"krisharyan@icloud.com\" of s
    end tell" 2>/dev/null
  fi
fi

# ports-terminal analysis loggers (read-only paper instrumentation): refresh snapshot + 7 analyses
# (legpnl/exposure/flow/mover/analyst_mtm/controls/edge_screen) -> *_log.jsonl + Obsidian Reports/.
# Rides this healthy 15-min job (the scalp-watchdog %30 path is launchd-blocked). Fail-soft; no trades.
bash "$HOME/Documents/polymarket/run_analysis_loggers.sh" >> "$HOME/Documents/polymarket/analysis_loggers.log" 2>&1 || true
