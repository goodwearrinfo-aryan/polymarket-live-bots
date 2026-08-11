#!/bin/bash
# Autonomous Polymarket agent sweep — READ-ONLY, runs headless (NO
# --dangerously-skip-permissions). Edit/Write/NotebookEdit and destructive Bash
# (rm/kill/pkill/sudo/launchctl/git-commit/git-push) are DENIED, so the agent can
# only read & analyze — never edit config, kill a process, commit, or trade.
# Output → a dated report + a one-line status log. Launched daily by
# com.aryan.polymarket-agentsweep.
set -u
export HOME=/Users/aryanagarwal
export PATH="/Users/aryanagarwal/.nvm/versions/node/v22.22.3/bin:/usr/local/bin:/usr/bin:/bin"
CLAUDE=/Users/aryanagarwal/.nvm/versions/node/v22.22.3/bin/claude
cd /Users/aryanagarwal/Documents/polymarket || exit 1

# Headless auth: ANTHROPIC_API_KEY from the gitignored .ta_env that the OPERATOR
# fills (this script never contains the key). Uses pay-as-you-go API billing,
# separate from the Claude Code subscription. Without a key, claude -p exits
# "Not logged in" and the sweep is a no-op.
if [ -f "$HOME/Documents/polymarket/.ta_env" ]; then
    _k=$(grep -E '^ANTHROPIC_API_KEY=' "$HOME/Documents/polymarket/.ta_env" | head -1 | cut -d= -f2- | tr -d '"'"'"' ')
    [ -n "$_k" ] && export ANTHROPIC_API_KEY="$_k"
fi
# Easy fallback (no terminal needed): a plain Desktop file whose name has
# 'anthropic'/'claude'/'apikey' in it, containing the key (sk-ant-...). Extract
# the token regardless of file format (plain or TextEdit/RTF).
if [ -z "${ANTHROPIC_API_KEY:-}" ] && [ -d "$HOME/Desktop" ]; then
    for f in "$HOME"/Desktop/*; do
        [ -f "$f" ] || continue
        case "$(basename "$f" | tr 'A-Z' 'a-z')" in
            *anthropic*|*claude*|*apikey*|*api_key*|*ant_key*)
                _k=$(grep -oE 'sk-ant-[A-Za-z0-9_-]{20,}' "$f" 2>/dev/null | head -1)
                [ -n "$_k" ] && { export ANTHROPIC_API_KEY="$_k"; break; } ;;
        esac
    done
fi
REPORT="/tmp/agent_sweep_$(date +%Y%m%d-%H%M).md"

read -r -d '' PROMPT <<'EOP'
AUTONOMOUS Polymarket agent sweep — PAPER ONLY, READ-ONLY. Hard rules: never edit
scalp_lab config or any _enabled flag, never pkill/kill a process, never place orders
or move funds, never commit/push. Work from ~/Documents/polymarket.

Run these agents and synthesize:
1. pm-leg-auditor — kill-rule leg health sweep: list any enabled non-control leg meeting
   the kill rule (n>=10 + pnl<-$0.50 + WR<60%, or n>=5 + WR<=20% + negative), and confirm
   controls (allin/coinflip) are still NEGATIVE (a positive control = marking bug, flag it).
2. pm-data-resolver — scan today's live objective-series Polymarket markets (crypto klines,
   BLS/CPI/jobs, ESPN) for a settled-but-mispriced gap (the only edge type that ever beat
   this market). Report any market where the resolving data is already knowable but the
   price hasn't caught up.
3. pm-bias-detective — read BRAIN/memory + current claims and name any self-deception
   happening now (thin-positive-as-edge, sunk-cost on dead legs, relitigating dead-ends).

For ANY candidate edge from step 2, run pm-resolution-checker, then 3x pm-edge-refuter,
then pm-dsr-gatekeeper — only report it if it survives.

OUTPUT (tight): (a) legs to kill if any; (b) any edge that cleared the full vet, with
numbers; (c) any self-deception flagged. If nothing actionable, say so in ONE line.
This is read-only intelligence — recommend, do not act. The operator decides.
EOP

"$CLAUDE" -p "$PROMPT" \
  --allowedTools Read Grep Glob Bash WebSearch WebFetch Task \
  --disallowedTools Edit Write NotebookEdit \
    'Bash(rm:*)' 'Bash(pkill:*)' 'Bash(kill:*)' 'Bash(sudo:*)' 'Bash(launchctl:*)' \
    'Bash(git commit:*)' 'Bash(git push:*)' 'Bash(git add:*)' 'Bash(mv:*)' 'Bash(cp:*)' \
  > "$REPORT" 2>&1
rc=$?
echo "[agent-sweep] $(date '+%Y-%m-%d %H:%M') exit=$rc -> $REPORT" >> /tmp/agent_sweep.log
exit $rc
