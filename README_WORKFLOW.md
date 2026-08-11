# Workflow & BRAIN: Quick Reference

## What's Running Right Now (2026-07-24)

### 1. **LIVE TRADING** (continuous)
- **Bot:** `scalp_lab.py` watches positions every ~60s
- **State:** `scalp_lab_state.json` tracks all positions & P&L
- **Guard:** Stop-Loss Guardian checks every 5min to block stop re-enables

### 2. **BRAIN (Procedural Memory)**
- **Location:** `~/.graphify/global-graph.json` (live, real-time)
- **Updated by:** Learning agents (daily) + loss_autopsy (on resolution)
- **Queried by:** ALL agents when making decisions
- **Examples:**
  - "Why did nearres fade fail?" → Returns: resolution misread
  - "What disables a leg?" → Returns: <20% WR, >$50 loss
  - "Is this edge in the graveyard?" → Returns: YES + reason

### 3. **DAILY WORKFLOW** (09:00 UTC each day)
```
09:00 → Collect metrics (state, backtest, config)
09:01 → Run 30 agents (Tier 0→4)
09:02 → Query BRAIN (verify leg validity, check rules)
09:03 → Generate verdict (GREEN/YELLOW/RED)
09:04 → Push to GitHub
09:05 → Update BRAIN with learning
```

### 4. **5-MINUTE HEALING CYCLE** (continuous, every 5 min)
```
:00 → Scan for problems (stuck positions, stale feed, stops re-enabled)
:01 → Query BRAIN (known causes? safe fix available?)
:02 → Execute fix (re-disable stops, reconnect feed, etc.)
:03 → Log result
:04 → Ready for next cycle
```

### 5. **VAULT SYNC** (every 3 hours)
- **When:** 00:00, 03:00, 06:00, 09:00, 12:00, 15:00, 18:00, 21:00 UTC
- **What:** Auto-commit vault changes, push to GitHub
- **Mirror:** `obsidian_snapshot.py` auto-updates vault every 6h with bot state
- **Result:** Vault always synced to `goodwearrinfo-aryan/PolymarketVault`

---

## How BRAIN Integrates

### Query Flow
```
Agent decision point
    ↓
"Should we disable leg X?"
    ↓
Query BRAIN via: brain "<question>"
    ↓
BRAIN returns relevant concepts + lessons
    ↓
Agent makes informed decision based on history
    ↓
Decision logged to BRAIN for next cycle
```

### Update Flow
```
Learning_EdgeLearner (Tier 4)
    ↓
"Edge is beating baseline + trending up"
    ↓
Record to BRAIN: "hypothesis = VALIDATED"
    ↓
Next day's agents query BRAIN & see: "this edge is validated"
    ↓
Confidence builds with each cycle
```

### Example: Stop Re-Enable Detection

**Cycle:** 2026-07-24 03:35 UTC (5-min healing)
```
Agent 3: Config Fixer detects wf_stop = 0.08 (should be 1.0)
    ↓
Queries BRAIN: "What causes stops to re-enable?"
    ↓
BRAIN: "Manual edit, config reload, app crash (from past analysis)"
    ↓
Agent checks log: "No crash, not a manual edit"
    ↓
Concludes: Likely config reload (safe to re-disable)
    ↓
Action: Re-disable wf_stop = 1.0
    ↓
Records to BRAIN: "Stop re-enable caught, safe fix applied"
    ↓
Next cycle Agent 7 (Stop-Loss Guardian) verifies fix
```

---

## Launchd Jobs (Auto-Orchestration)

### com.aryan.daily-test-check
- **Trigger:** 09:00 UTC daily
- **Script:** `daily_test_check_ollama.py`
- **Does:** Collects metrics, runs backtest, spawns 30 agents, queries BRAIN, generates verdict

### com.aryan.self-healing-monitor
- **Trigger:** Every 5 minutes (00, 05, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55)
- **Script:** `self_healing_agents.py --monitor`
- **Does:** 10 core healing agents (detect + fix problems)

### Extended Healing (same trigger)
- **Script:** `self_healing_agents_extended.py --monitor`
- **Does:** 10 extended agents (validate + verify)

### com.aryan.vault-sync
- **Trigger:** Every 3 hours (00, 03, 06, 09, 12, 15, 18, 21)
- **Script:** `vault_sync.py`
- **Does:** Commits vault changes, pushes to GitHub, keeps vault synced

---

## The 30 Agents (Distinct, Interconnected)

### TIER 0: Sensors (4)
- StateReader, LogReader, ConfigReader, TestLogReader
- **Job:** Read raw data (no analysis)
- **Output:** Feed to Tier 1

### TIER 1: Diagnostics (8)
- ConfigIntegrity, PositionHealth, EdgeQuality, StateIntegrity
- FeedHealth, PerformanceTrend, CapitalAllocation, LegPerformance
- **Job:** Analyze one specific domain each
- **Output:** Feed to Tier 2 (synthesis)

### TIER 2: Synthesis (4)
- SafetyVerdict, EdgeVsBaseline, HealthScore, ActionPriority
- **Job:** Combine Tier 1 findings into decisions
- **Output:** Feed to Tier 3 (execution) + Daily verdict

### TIER 3: Actions (5)
- ConfigHealer, LegDisabler, PositionCloser, FeedReconnector, StrategyMutator
- **Job:** Execute specific fixes
- **Output:** Side effects (config changes, closes) + logs

### TIER 4: Learning (3)
- EdgeLearner, LegQualityLearner, CapitalEfficiencyLearner
- **Job:** Extract patterns, update BRAIN
- **Output:** BRAIN updates for next cycle

### Continuous Healing (20)
- Agents 1-10 (core): Stuck, Feed, Config, State, Order, Memory, Stops, Spread, Liquidity, Fills
- Agents 11-20 (extended): Validator, Slippage, Fills, Correlation, Drawdown, WinRate, Leaks, Fees, Webhooks, Recovery

---

## Test Hypothesis

**Hypothesis:** Removing stops allows longer holds → improves Sharpe

**Test Parameters:**
- Stop multiplier: 1.0 (disabled) for all 7 days
- Edge expectation: >-$0.159/trade (baseline control: all-in)
- Test window: 2026-07-23 22:33 → 2026-07-30 22:33 UTC

**Day 1 Result:**
- Edge: $0.1900/trade (BEATS baseline ✓)
- Verdict: 🟡 YELLOW (edge good, volume low)
- Status: On track

---

## Files to Monitor

### State Files
- `scalp_lab_state.json` — Live position state
- `SEVEN_DAY_TEST_LOG.json` — Daily verdicts & metrics
- `scalp_engine_config.json` — Trading parameters (verify stops=1.0)

### Logs
- `/tmp/daily-test-check.log` — Daily check results
- `/tmp/self-healing-monitor.log` — Healing cycle activity
- `/tmp/vault-sync.log` — Vault sync status

### BRAIN
- `~/.graphify/global-graph.json` — Live knowledge graph
- Query via: `brain "<question>"` (command-line interface)

### Vault
- `~/Documents/PolymarketVault/` — Local (844 files)
- GitHub: `goodwearrinfo-aryan/PolymarketVault`

---

## Autonomous Operations Per Day

| Operation | Frequency | Count | Total Agents |
|-----------|-----------|-------|--------------|
| Healing cycles | Every 5 min | 288 | 20 each cycle |
| Daily analysis | 09:00 UTC | 1 | 30 |
| Vault syncs | Every 3h | 8 | 1 each |
| **TOTAL OPS** | — | **297** | — |

---

## When to Intervene (If Needed)

**DO NOT touch:**
- ✋ scalp_lab.py (bot is self-healing)
- ✋ Config (watchdog auto-fixes)
- ✋ State file (agents validate & repair)

**OK to check:**
- 👀 Logs (`/tmp/*-check.log`, `/tmp/*-healing.log`)
- 👀 Daily verdict (`SEVEN_DAY_TEST_LOG.json`)
- 👀 Vault state (GitHub or local folder)
- 👀 BRAIN (`brain "<question>"`)

**Emergency only:**
- 🚨 Watchdog crashed? → `launchctl load ~/Library/LaunchAgents/com.aryan.scalp-watchdog.plist`
- 🚨 Healing stuck? → Check `/tmp/self-healing-monitor.log`
- 🚨 Daily check failed? → Check `/tmp/daily-test-check.log`

---

## Next Steps

- **Tomorrow (2026-07-25 09:00 UTC):** Day 2 check, verify edge trend
- **Day 7 (2026-07-30 22:33 UTC):** Final verdict, resolve hypothesis
- **Post-test:** loss_autopsy analysis, BRAIN update with learnings

---

**Test Status:** ✅ LIVE  
**Workflow:** ✅ RUNNING (297 ops/day)  
**BRAIN:** ✅ ACTIVE (queried every cycle)  
**Vault:** ✅ SYNCED (GitHub backup every 3h)  

All systems autonomous. Zero manual intervention needed.
