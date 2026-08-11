#!/bin/bash
# last30days daily research runner - wired into polymarket bot pipeline
# Topics: rotate through esports, prediction markets, POLY token, nearres-related signals
set -euo pipefail

SKILL_DIR="$HOME/.claude/skills/ab-testing/skills/last30days"
MEMORY_DIR="$HOME/Documents/polymarket/last30days"
OBSIDIAN_DIR="$HOME/Documents/polymarket/obsidian_vault/Last30Days"
mkdir -p "$MEMORY_DIR" "$OBSIDIAN_DIR"

for py in python3.14 python3.13 python3.12 python3; do
  command -v "$py" >/dev/null 2>&1 || continue
  "$py" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)' || continue
  LAST30DAYS_PYTHON="$py"
  break
done

TOPICS=(
  "polymarket esports prediction markets"
  "polymarket POLY token airdrop"
  "esports betting odds accuracy"
  "prediction markets edge strategy"
)

# Rotate topic by day-of-week (4 topics, runs daily)
DOW=$(date +%u)  # 1=Mon ... 7=Sun
IDX=$(( (DOW - 1) % ${#TOPICS[@]} ))
TOPIC="${TOPICS[$IDX]}"

echo "[last30days] $(date -u +%Y-%m-%dT%H:%M:%SZ) running topic: $TOPIC"

"$LAST30DAYS_PYTHON" "$SKILL_DIR/scripts/last30days.py" "$TOPIC" \
  --subreddits=Polymarket,predictionmarkets,esports,GlobalOffensive,leagueoflegends,DotA2,Kalshi \
  --tiktok-hashtags=polymarket,predictionmarkets,esportsbetting \
  --auto-resolve \
  --emit=compact \
  --save-dir="$MEMORY_DIR" \
  --save-suffix=v3 2>&1 | tee -a "$MEMORY_DIR/last30days.log"

# Mirror latest raw file to Obsidian vault
LATEST=$(ls -t "$MEMORY_DIR"/*-raw-v3.md 2>/dev/null | head -1)
if [ -n "$LATEST" ]; then
  SLUG=$(basename "$LATEST" -raw-v3.md)
  cp "$LATEST" "$OBSIDIAN_DIR/$SLUG-$(date +%Y-%m-%d).md"
  echo "[last30days] Mirrored to Obsidian: $OBSIDIAN_DIR/$SLUG-$(date +%Y-%m-%d).md"
fi
