#!/bin/bash
# Daily report job (durable in ~/Documents/polymarket). Builds the PDF then sends it.
# Fail-soft: if either step fails the next run still tries. Backgrounded so launchd
# isn't held; AbandonProcessGroup in the plist keeps the children alive past exit.
D="/Users/aryanagarwal/Documents/polymarket"
PY="/Library/Frameworks/Python.framework/Versions/3.14/bin/python3"; [ -x "$PY" ] || PY="$(command -v python3)"
LOG="$D/send_mail.log"
(
  "$PY" "$D/bot_report.py" >> "$LOG" 2>&1 || true
  "$PY" "$D/send_mail.py"  >> "$LOG" 2>&1 || true
) &
disown 2>/dev/null || true
