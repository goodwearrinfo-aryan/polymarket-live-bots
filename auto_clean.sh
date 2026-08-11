#!/bin/bash
# auto_clean.sh — weekly cache purge (run by launchd Sundays 03:00)
LOG="$HOME/Documents/polymarket/auto_clean.log"
echo "[$(date '+%F %T')] Starting auto-clean" >> "$LOG"

purge() {
  local path="$1"
  if [ -e "$path" ]; then
    local size; size=$(du -sh "$path" 2>/dev/null | cut -f1)
    rm -rf "$path"
    echo "[$(date '+%F %T')] Cleared $path ($size)" >> "$LOG"
  fi
}

purge ~/.cache
purge ~/Library/Caches/com.openai.codex
purge ~/Library/Caches/Google
purge ~/Library/Caches/GeoServices
purge ~/Library/Caches/pip
purge ~/Library/Caches/uv
purge ~/.npm/_cacache

# zsh sessions — keep 10 most recent
ls -t ~/.zsh_sessions/*.history ~/.zsh_sessions/*.session 2>/dev/null | tail -n +11 | xargs rm -f 2>/dev/null

echo "[$(date '+%F %T')] Auto-clean done. Disk free: $(df -h ~ | awk 'NR==2{print $4}')" >> "$LOG"
