#!/bin/bash
# nightly_backup.sh — recreated 2026-07-30 (replaces the orphaned polymarket-live/nightly_backup.sh).
# Backs up the irreplaceable bot state to ~/Backups/polymarket/<date>/, keeps 7 days.
# Fail-soft: logs errors, still exits 0 so launchd never thrashes. Free ($0, local).
set -uo pipefail
LOG=/tmp/nightly_backup.log
exec >>"$LOG" 2>&1
echo "==== $(date '+%F %T') nightly_backup ===="

ROOT=~/Backups/polymarket
DEST="$ROOT/$(date +%Y%m%d)"
mkdir -p "$DEST" || { echo "FATAL cannot mkdir $DEST"; exit 0; }

PGBIN=/Applications/Postgres.app/Contents/Versions/18/bin

# 1. Postgres — all databases + globals (catches whichever DB the bot uses)
if [ -x "$PGBIN/pg_dumpall" ]; then
  if "$PGBIN/pg_dumpall" -p 5432 2>/dev/null | gzip > "$DEST/postgres_all.sql.gz"; then
    echo "OK   postgres dump ($(du -h "$DEST/postgres_all.sql.gz" | awk '{print $1}'))"
  else echo "WARN postgres dump failed (server down?)"; fi
else echo "WARN pg_dumpall missing"; fi

# 2. Key state files (both live copies + keys)
for p in ~/Documents/polymarket/scalp_lab_state.json \
         ~/polymarket-live/scalp_lab_state.json \
         ~/Documents/polymarket/.env; do
  [ -f "$p" ] && { cp "$p" "$DEST/$(echo "$p" | sed 's#/#_#g')" && echo "OK   copied $p"; } || echo "SKIP absent $p"
done

# 3. Obsidian vault (tarball)
if [ -d ~/Documents/PolymarketVault ]; then
  tar -czf "$DEST/vault.tar.gz" -C ~/Documents PolymarketVault 2>/dev/null \
    && echo "OK   vault ($(du -h "$DEST/vault.tar.gz" | awk '{print $1}'))" || echo "WARN vault tar failed"
fi

# 4b. Push to iCloud (secrets STRIPPED — never upload .env/keys) for off-machine backup
ICLOUD="$HOME/Library/Mobile Documents/com~apple~CloudDocs/PolymarketBackups/$(date +%Y%m%d)"
if [ -d "$HOME/Library/Mobile Documents/com~apple~CloudDocs" ]; then
  mkdir -p "$ICLOUD"
  for f in "$DEST"/*; do
    base=$(basename "$f")
    case "$base" in *.env*|*secret*|*.key|*passphrase*|*private*) echo "iCLOUD-SKIP $base (secret)"; continue;; esac
    cp "$f" "$ICLOUD/" 2>/dev/null && echo "OK   iCloud ← $base"
  done
  # prune iCloud to last 7 too
  ls -dt "$HOME/Library/Mobile Documents/com~apple~CloudDocs/PolymarketBackups"/20* 2>/dev/null | tail -n +8 | while read old; do rm -rf "$old"; done
else echo "WARN iCloud Drive not available — skipped cloud push"; fi

# 4. Lock down (contains .env) + prune to last 7 days
chmod -R go-rwx "$DEST" 2>/dev/null
# keep newest 7 date-dirs (macOS head has no -n -N), delete the rest
ls -dt "$ROOT"/20* 2>/dev/null | tail -n +8 | while read old; do
  rm -rf "$old" && echo "PRUNE removed $old"
done
echo "---- done: $(du -sh "$DEST" | awk '{print $1}') in $DEST ----"
exit 0
