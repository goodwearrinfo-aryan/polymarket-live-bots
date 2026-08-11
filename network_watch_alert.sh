#!/usr/bin/env bash
# network_watch_alert.sh — hourly reachability + iMessage only on DOWN (token-free).
bash "$HOME/Documents/polymarket/network_watch.sh"
LAST=$(tail -1 "$HOME/Documents/polymarket/network_watch.log")
case "$LAST" in
  *DOWN*) osascript -e "tell application \"Messages\"
      set s to 1st service whose service type = iMessage
      send \"🚨 Polymarket gamma-api UNREACHABLE — regional block may be back. Bot is collecting dead exits. VPS fallback: CLOUD_SETUP.md\" to buddy \"krisharyan@icloud.com\" of s
    end tell" 2>/dev/null ;;
esac
