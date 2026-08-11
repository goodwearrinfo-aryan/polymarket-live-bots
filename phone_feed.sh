#!/bin/bash
# Bounded phone feed: deliver a timestamped Polymarket status PDF to iCloud Drive
# every 10 min. Send #1 is delivered synchronously by the caller; this script does
# the remaining sends (#2..#9) so the feed runs for ~1h20m total, then exits.
DIR="$HOME/Documents/polymarket"
PY=$(ls -t /Library/Frameworks/Python.framework/Versions/*/bin/python3 2>/dev/null | head -1); [ -z "$PY" ] && PY=python3
FEED="$HOME/Library/Mobile Documents/com~apple~CloudDocs/Polymarket-Feed"
TMP="$DIR/.feed_tmp"
LOG="$DIR/phone_feed.log"
mkdir -p "$FEED" "$TMP"
echo "$(date '+%F %T') feed started (sends #2..#9, 10 min apart)" >>"$LOG"
for i in 2 3 4 5 6 7 8 9; do
  sleep 600
  STAMP=$(date +%Y%m%d-%H%M)
  if "$PY" "$DIR/trades_report.py" "$DIR/trade_log.json" "$TMP" >>"$LOG" 2>&1 && [ -f "$TMP/Live Trades.pdf" ]; then
    cp "$TMP/Live Trades.pdf" "$FEED/Polymarket-status-$STAMP.pdf"
    echo "$(date '+%F %T') sent #$i -> Polymarket-status-$STAMP.pdf" >>"$LOG"
  else
    echo "$(date '+%F %T') #$i FAILED to generate PDF" >>"$LOG"
  fi
done
echo "$(date '+%F %T') feed complete (9/9 incl. #1)" >>"$LOG"
