#!/usr/bin/env bash
# Token-free Kalshi market ingester (cache-only). Logs count + timestamp.
PY=/Library/Frameworks/Python.framework/Versions/3.14/bin/python3
"$PY" "$HOME/Documents/polymarket/kalshi_feed.py" --cache >> "$HOME/Documents/polymarket/kalshi_feed.log" 2>&1
echo "[$(date -u '+%F %T')] cache refreshed" >> "$HOME/Documents/polymarket/kalshi_feed.log"
