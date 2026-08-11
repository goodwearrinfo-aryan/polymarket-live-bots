#!/bin/bash
# run_analysis_loggers.sh — refresh the ports snapshot if stale, then run every READ-ONLY ports
# analysis logger ONCE, in PARALLEL (wall-time = the slowest logger, not the sum). Paper-only, no
# trades. Hosts: crypto_tracking.sh (15min), obsidian_snapshot (6h), serve.sh loop (60s), on-demand.
set -u
POLY="/Users/aryanagarwal/Documents/polymarket"
TERM_DIR="/Users/aryanagarwal/Documents/Codex/2026-05-28/claud"
PY="$(command -v python3)"
DATA="$TERM_DIR/ports_data.json"

# Refresh only if stale (>120s) — serve.sh keeps it fresh every 60s, so this skips the 27MB
# heatmap reload on the hot path (loggers then read the small 158KB ports_data.json).
mtime=$(stat -f %m "$DATA" 2>/dev/null || echo 0)
if [ ! -f "$DATA" ] || [ "$(( $(date +%s) - mtime ))" -gt 120 ]; then
  "$PY" "$TERM_DIR/gen_ports.py" >/dev/null 2>&1 || true
fi

cd "$POLY" || exit 0
# fire all 7 loggers concurrently; they write disjoint files + are read-only, so no contention
for lg in legpnl exposure flow mover analyst_mtm controls edge_screen bandcal; do
  "$PY" "$POLY/${lg}_logger.py" once >> "$POLY/${lg}.log" 2>&1 &
done
wait
echo "analysis loggers ran $(date -u +%FT%TZ)"
