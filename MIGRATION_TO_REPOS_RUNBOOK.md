# Staged Migration Runbook — make `~/repos/polymarket` the canonical runtime

**Status:** NOT YET EXECUTED. Written 2026-07-09 after the two-copy incident (see below).
**Do this only when you are at the keyboard to babysit it.** It restarts the
scalp-watchdog and the Postgres-writing `scalp_lab` — the project's #1
"never disturb carelessly" surface. A botched run can take the paper bot down
or race the Postgres book.

---

## Background (why this exists)

On 2026-07-09 the bot was found split across two directories:

| | `~/Documents/polymarket` | `~/repos/polymarket` |
|---|---|---|
| Role | **live runtime** — 108 launchd jobs + 8 processes point here | canonical code, git-tracked, updated daily |
| Files | had been gutted to 82 `.py` (llm_client.py etc. deleted) | 377 `.py`, full superset |

Immediate fix already applied: **additively restored 439 code files** repos→Documents
(manifest `/tmp/hr_restore_manifest_1783590971.txt`), excluding `.env`/secrets/state-JSON.
That un-broke the jobs. This runbook is the *optional* consolidation to a single
git-tracked home. It is NOT required for the bot to work.

## Preconditions / invariants (do not violate)

- **NEVER** `launchctl load com.aryan.scalp-watchdog` — that plist exit-126s under
  launchd (TCC: /bin/bash can't reach ~/Documents) and takes the bot DOWN. The
  watchdog runs as a **nohup orphan** (ppid=1) by design.
- **Only one** scalp_lab watchdog may run at a time (Postgres duplicate-writer race).
- Paper state lives in **Postgres**, shared regardless of code dir — no trade data
  is at risk from the code move itself.
- Every plist edit is **reversible**: back up the plist first.
- Keep `.env` / secrets exactly where they are; do not copy them between dirs
  (Documents `.env` is already byte-identical to repos').

## Steps

### 0. Snapshot for rollback
```bash
mkdir -p /tmp/plist_backup_$(date +%Y%m%d)
cp ~/Library/LaunchAgents/com.aryan.*.plist /tmp/plist_backup_$(date +%Y%m%d)/
launchctl list | grep com.aryan > /tmp/launchd_state_before.txt
psql -tAc 'select count(*) from lab_trades'   # record baseline row count
```

### 1. Reconcile secrets/state into repos (so nothing is stranded)
```bash
# copy ONLY the secret/state files repos lacks, into repos (review first):
#   .env is identical -> skip. agent_secrets.py, bot_id.json, analyst_positions.json,
#   .watchdog_last_open.json -> decide per-file whether repos needs them.
```
Confirm `~/repos/polymarket` runs a smoke check clean:
`cd ~/repos/polymarket && python3 -c "import ast; ast.parse(open('llm_client.py').read())"`

### 2. Repoint the 108 plists (batched, NOT all at once)
For each plist that references `/Users/aryanagarwal/Documents/polymarket`, rewrite the
path to `/Users/aryanagarwal/repos/polymarket`. Do it in tiers, verifying each tier
before the next:

- **Tier A — read-only loggers/digests** (obsidian-*, *-report, *-digest, gdelt,
  world-*, news-*). Lowest risk. Repoint, `launchctl kickstart`, confirm exit 0.
- **Tier B — data feeds / watchers** (wsfeed, pricefeed, volregime, moneyflow-*,
  insider-watch, xvenue-logger). Repoint, kickstart, confirm.
- **Tier C — LAST: scalp_lab / watchdog / anything writing Postgres.**
  Stop the old Documents process cleanly, wait for any in-flight `once`-cycle to
  finish, then start from repos as a **nohup orphan** (NOT via launchd for the
  watchdog). Re-check `psql` row count is advancing and only ONE watchdog runs.

### 3. Repoint the fleet's own path assumptions
- Global `~/.claude/CLAUDE.md` and any agent `.md` that hard-codes
  `~/Documents/polymarket` → update to `~/repos/polymarket`.

### 4. Decommission Documents
- Leave it in place (git-untracked) for a cooldown week as a fallback.
- Only after a clean week: archive or delete `~/Documents/polymarket`.

## Rollback
Restore plists from `/tmp/plist_backup_*/`, `launchctl bootout` + re-bootstrap, and
the old Documents processes/paths are exactly as before. Postgres is untouched.

## Verification (done = observed, not assumed)
- `launchctl list | grep com.aryan` shows no new exit-2/78 vs `/tmp/launchd_state_before.txt`
- `psql -tAc 'select count(*) from lab_trades'` advancing, exactly one watchdog PID
- `agents_dashboard.py` chips: RUNNING/OK up, ERROR down
