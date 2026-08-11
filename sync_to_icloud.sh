#!/bin/bash
SRC="$HOME/Documents/polymarket"
DEST="$HOME/Library/Mobile Documents/com~apple~CloudDocs/PolymarketBot-Code"
mkdir -p "$DEST"
rsync -a --delete \
  --include="*.py" \
  --include="*.sh" \
  --include="*.md" \
  --include="*.json" \
  --include="*.csv" \
  --exclude=".env" \
  --exclude="trade_log.json" \
  --exclude="brain.json" \
  --exclude="*.jsonl" \
  --exclude="*.log" \
  --exclude="*.bak*" \
  --exclude="__pycache__/" \
  --exclude=".git/" \
  --exclude="*.pyc" \
  --exclude=".smtp_creds.json" \
  --exclude="market_heatmap.json" \
  --exclude="trader_stats.json" \
  --exclude=".signal_seen.json" \
  --exclude="*/" \
  "$SRC/" "$DEST/"
