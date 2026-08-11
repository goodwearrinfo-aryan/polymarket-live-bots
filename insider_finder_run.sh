#!/usr/bin/env bash
# insider_finder_run.sh — token-free conversion of the every-10-min Claude task.
# Runs the discovery/profiling script (it iMessages qualifiers itself), then the
# sanity checks; alerts loudly via iMessage on errors. The daily 13:00 re-vet
# stays with the Claude task (needs judgment).
PY=/Library/Frameworks/Python.framework/Versions/3.14/bin/python3
PM="$HOME/Documents/polymarket"
LOG="$PM/insider_finder_run.log"

alert() {
  osascript -e "tell application \"Messages\"
    set s to 1st service whose service type = iMessage
    send \"🚨 insider-finder: $1\" to buddy \"krisharyan@icloud.com\" of s
  end tell" 2>/dev/null
}

OUT="$(cd "$PM" && "$PY" insider_finder.py 15 2>&1)"
RC=$?
echo "[$(date -u '+%F %T')] rc=$RC $(echo "$OUT" | tail -1)" >> "$LOG"
[ $RC -ne 0 ] && alert "insider_finder.py exited $RC: $(echo "$OUT" | tail -1 | cut -c1-120)"

# watchlist JSON must stay valid
"$PY" -c "import json;json.load(open('$PM/insider_wallets.json'))" 2>/dev/null \
  || alert "insider_wallets.json is INVALID JSON"

# watcher must be alive (launchd owns it — never start a second copy)
pgrep -f insider_watch.py >/dev/null || alert "insider_watch.py watcher is DOWN"
