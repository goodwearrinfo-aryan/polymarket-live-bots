#!/bin/bash
# lawyer_deploy_sync.sh — auto-sync + redeploy agent for the Personal Lawyer app.
#
# Checks whether the source files (lawyer.py, lawyer_ui.py, lawyer_agents/, etc. in
# ~/Documents/polymarket) or the law DB have changed since the last sync. If so,
# syncs them into ~/Documents/lawyer-app (reapplying the cloud-only patches via
# sync_from_polymarket.sh) and pushes a new deploy to Railway. No-ops if nothing changed.
set -e
SRC=~/Documents/polymarket
APP=~/Documents/lawyer-app
STAMP="$APP/.last_sync_hash"

cd "$APP"

# hash the source files that matter for a deploy
NEW_HASH=$(cat "$SRC/lawyer.py" "$SRC/lawyer_ui.py" "$SRC/llm_client.py" \
                "$SRC"/lawyer_agents/*.py ~/Documents/lawdb.sqlite 2>/dev/null | shasum -a 256 | cut -d' ' -f1)
OLD_HASH=$(cat "$STAMP" 2>/dev/null || echo "")

if [ "$NEW_HASH" = "$OLD_HASH" ]; then
    echo "[$(date)] no changes — skipping deploy"
    exit 0
fi

echo "[$(date)] changes detected — syncing + redeploying"
bash sync_from_polymarket.sh
cp -r "$SRC/lawyer_agents" "$APP/"
cp "$SRC/tools_registry.py" "$SRC/llm_client.py" "$APP/" 2>/dev/null || true

rm -f -- "$APP"/lawdb.sqlite-shm "$APP"/lawdb.sqlite-wal
sqlite3 ~/Documents/lawdb.sqlite "PRAGMA wal_checkpoint(TRUNCATE);" >/dev/null 2>&1 || true
cp ~/Documents/lawdb.sqlite "$APP/lawdb.sqlite"

~/.railway/bin/railway up --detach 2>&1

echo "$NEW_HASH" > "$STAMP"
echo "[$(date)] deploy triggered"
