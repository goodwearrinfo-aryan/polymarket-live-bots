#!/bin/bash
# run_workflow_headless.sh — run a Claude Code workflow headlessly on Grok (xAI), for launchd 24/7.
# xAI exposes an Anthropic-compatible /v1/messages endpoint, so Claude Code can point at it.
# Auth: reads XAI_API_KEY (or GROK_API_KEY) from ~/Documents/polymarket/.env — NO key hardcoded.
# Usage:  run_workflow_headless.sh <workflow-name> "<actionable-criteria sentence>"
# Logs:   /tmp/wf_<name>.log  (per the exit-78 lesson — never under ~/Documents)
set -uo pipefail

WF="${1:?usage: run_workflow_headless.sh <workflow-name> <actionable-criteria>}"
CRIT="${2:-the verdict is actionable}"
ENVF="$HOME/Documents/polymarket/.env"
SCRIPT="$HOME/.claude/workflows/${WF}.js"
LOG="/tmp/wf_${WF}.log"
STAMP="$(date '+%Y-%m-%d %H:%M')"

# load keys (keyless-first house rule; this is the one keyed path)
set -a; . "$ENVF" 2>/dev/null; set +a

KEY="${XAI_API_KEY:-${GROK_API_KEY:-}}"
if [ -z "$KEY" ]; then
  echo "$STAMP $WF SKIP: no XAI_API_KEY/GROK_API_KEY in .env" >> "$LOG"; exit 0
fi
[ -f "$SCRIPT" ] || { echo "$STAMP $WF SKIP: no workflow script $SCRIPT" >> "$LOG"; exit 0; }

export ANTHROPIC_BASE_URL="https://api.x.ai"
export ANTHROPIC_API_KEY="$KEY"
MODEL="${XAI_MODEL:-grok-4}"

PROMPT="[AUTONOMOUS] Run Workflow({scriptPath:'${SCRIPT}'}) and wait for it to finish. ACTIONABLE = ${CRIT}. If actionable, send ONE concise iMessage to krisharyan@icloud.com via: osascript -e 'tell application \"Messages\" to send \"<msg>\" to buddy \"krisharyan@icloud.com\" of (1st service whose service type = iMessage)'. If not actionable, send nothing (silence = healthy null). Always append one line \"${STAMP} ${WF} <verdict>\" to /tmp/auto_runs.log. Do not ask questions; run, alert-if-actionable, finish."

echo "$STAMP $WF START (model=$MODEL)" >> "$LOG"
claude -p "$PROMPT" --model "$MODEL" --permission-mode bypassPermissions >> "$LOG" 2>&1
echo "$STAMP $WF DONE exit=$?" >> "$LOG"
