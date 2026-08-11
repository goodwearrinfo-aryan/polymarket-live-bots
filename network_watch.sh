#!/usr/bin/env bash
# Pings Polymarket gamma-api and logs reachability. Detects if the regional
# block returns. Read-only — does NOT touch the bot or the experiment.
LOG="$HOME/Documents/polymarket/network_watch.log"
STAMP="$(date '+%Y-%m-%d %H:%M:%S')"
code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 12 "https://gamma-api.polymarket.com/markets?limit=1" 2>/dev/null)
if [ "$code" = "200" ]; then
  echo "[$STAMP] OK   gamma-api reachable (HTTP 200)" >> "$LOG"
else
  echo "[$STAMP] DOWN gamma-api unreachable (HTTP ${code:-000}) — BLOCK MAY HAVE RETURNED, VPS fallback (CLOUD_SETUP.md) may be needed" >> "$LOG"
fi
