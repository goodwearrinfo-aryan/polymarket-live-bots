# session_fleet — versioned snapshot (2026-07-30)

Canonical runtime copies live in `~/.claude/` (where Claude loads agents and launchd
runs the scripts). These are git-tracked backups.

## agents/ (Claude subagents — invoke on demand)
- copy-edge-judge — judges whale_copy_paper for fake edge (6 lenses)
- settle-arb-judge — judges sports_settle_arb candidates for fake edge (6 lenses)
- env-key-doctor — dedupes/repairs the .env LLM key chain
- ollama-reaper — reclaims disk from unused ollama models

## self_heal/ (free autonomous launchd scripts, $0)
- env_key_health.sh — weekly key-chain check (com.aryan.env-key-health)
- nightly_backup.sh — nightly Postgres+state+vault backup (com.aryan.polymarket-backup)
