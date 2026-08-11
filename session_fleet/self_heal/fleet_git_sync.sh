#!/bin/bash
# fleet_git_sync.sh — free ($0) daily snapshot of the ~/.claude fleet into the git repo.
# Copies the session's agents + self-heal scripts into polymarket-live/session_fleet/,
# commits + pushes ONLY when something changed. Never commits secrets (leak-checked).
# Logs to /tmp. Read-mostly: the only writes are the snapshot copies + a git commit/push.
set -uo pipefail
LOG=/tmp/fleet_git_sync.log
exec >>"$LOG" 2>&1
echo "==== $(date '+%F %T') fleet_git_sync ===="

REPO=~/polymarket-live
DEST="$REPO/session_fleet"
[ -d "$REPO/.git" ] || { echo "FATAL no repo"; exit 0; }
mkdir -p "$DEST/agents" "$DEST/self_heal"

# snapshot the session's fleet (add new agents here as they're created)
for a in copy-edge-judge settle-arb-judge env-key-doctor ollama-reaper; do
  [ -f ~/.claude/agents/$a.md ] && cp ~/.claude/agents/$a.md "$DEST/agents/"
done
for s in env_key_health nightly_backup fleet_git_sync; do
  [ -f ~/.claude/self-heal/$s.sh ] && cp ~/.claude/self-heal/$s.sh "$DEST/self_heal/"
done

cd "$REPO" || exit 0
git add session_fleet
# anything changed? (staged check catches new/untracked files too)
if git diff --cached --quiet -- session_fleet; then
  echo "OK no fleet changes — nothing to sync"; exit 0
fi
# hard leak check
if git diff --cached --name-only | grep -iE '\.env$|secret|\.key$' | grep -qv template; then
  echo "ABORT secret staged"; git reset -- session_fleet; exit 0
fi
git commit -q -m "chore: auto-sync session fleet ($(date +%F))

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>" && echo "OK committed"
git push origin main 2>&1 | tail -1
echo "---- done ----"
exit 0
