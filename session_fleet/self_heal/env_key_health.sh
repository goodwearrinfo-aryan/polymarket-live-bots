#!/bin/bash
# env_key_health.sh — free ($0, no Claude) weekly self-heal for the LLM key chain.
# Detects the 2026-07-29 bug class: duplicate/placeholder .env assignments that clobber
# a real key (last `source` wins), and dead keys. Backs up, warns, logs to /tmp (TCC-safe).
set -uo pipefail
ENV=~/Documents/polymarket/.env
LOG=/tmp/env_key_health.log
BK=~/.secrets-backup
exec >>"$LOG" 2>&1
echo "==== $(date '+%F %T') env_key_health ===="

[ -f "$ENV" ] || { echo "FATAL: $ENV missing"; exit 1; }
mkdir -p "$BK"; chmod 700 "$BK"

# 1. Detect duplicate KEY= assignments (the clobber risk)
DUPES=$(grep -oE '^[A-Z_]+=' "$ENV" | sort | uniq -d | sed 's/=//')
if [ -n "$DUPES" ]; then
  echo "WARN duplicate assignments (clobber risk): $DUPES"
  cp "$ENV" "$BK/polymarket-env.$(date +%Y%m%d-%H%M%S).autoheal.bak"; chmod 600 "$BK"/*.bak
else
  echo "OK no duplicate assignments"
fi

# 2. Live-test each provider key (auth only, fast; values never printed)
set -a; source "$ENV"; set +a
check(){ local n="$1" u="$2" k="$3"; local c
  c=$(curl -s -o /dev/null -w '%{http_code}' -m 15 "$u" -H "Authorization: Bearer $k" 2>/dev/null)
  case "$c" in 200) echo "OK   $n ($c)";; 429) echo "WARN $n rate-limited ($c) — valid, will recover";;
    *) echo "DEAD $n ($c) — refresh key in provider console";; esac; }
check GROQ      https://api.groq.com/openai/v1/models       "${GROQ_API_KEY:-}"
check CEREBRAS  https://api.cerebras.ai/v1/models           "${CEREBRAS_API_KEY:-}"
check SAMBANOVA https://api.sambanova.ai/v1/models          "${SAMBANOVA_API_KEY:-}"
check NVIDIA    https://integrate.api.nvidia.com/v1/models  "${NVIDIA_API_KEY:-}"
O=$(curl -s -o /dev/null -w '%{http_code}' -m 8 http://localhost:11434/api/tags 2>/dev/null)
[ "$O" = 200 ] && echo "OK   OLLAMA floor ($O)" || echo "DEAD OLLAMA floor ($O) — restart ollama"
echo "---- done ----"
