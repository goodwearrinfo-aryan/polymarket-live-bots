# Polymarket 7-Day Stop-Loss Test — Complete Workflow & BRAIN Integration

**Status:** ✅ LIVE  
**Date:** 2026-07-24 (Day 2 of 7)  
**Test Window:** 2026-07-23 22:33 → 2026-07-30 22:33 UTC  

---

## System Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                    POLYMARKET BOT ECOSYSTEM                      │
│                                                                   │
│  ┌──────────────────┐  ┌──────────────────┐  ┌─────────────────┐│
│  │   LIVE TRADING   │  │  DAILY ANALYSIS  │  │  CONTINUOUS     ││
│  │  (scalp_lab.py)  │  │  (09:00 UTC)     │  │  HEALING (5min)  ││
│  │                  │  │                  │  │                  ││
│  │ Every ~60s       │  │ 30 Agents        │  │ 30 Agents        ││
│  │ Watch/Trade      │  │ Verify + Improve │  │ Detect + Fix     ││
│  └──────────────────┘  └──────────────────┘  └─────────────────┘│
│         ↓                      ↓                       ↓          │
│    State File          Daily Verdict + Log    Continuous Fixes   │
│  (scalp_lab_state.json) (SEVEN_DAY_TEST_LOG) (Auto-healing)     │
│                                                                   │
│  ┌──────────────────────────────────────────────────────────────┐│
│  │              BRAIN (Procedural Memory)                        ││
│  │  ~/.graphify/global-graph.json (live, real-time queries)    ││
│  │  Read by: every agent, every decision, every cycle           ││
│  │  Updated by: Learning agents (Tier 4), loss_autopsy         ││
│  └──────────────────────────────────────────────────────────────┘│
│                                                                   │
│  ┌──────────────────────────────────────────────────────────────┐│
│  │          OBSIDIAN VAULT (Knowledge Base)                     ││
│  │  ~/Documents/PolymarketVault → GitHub (synced every 3h)     ││
│  │  Auto-mirrored by: obsidian_snapshot.py (6h intervals)      ││
│  └──────────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────┘
```

---

## Daily Workflow (09:00 UTC)

### Trigger: `com.aryan.daily-test-check.plist`

```
09:00:00 UTC
│
├─→ daily_test_check_ollama.py STARTS
│   │
│   ├─ [COLLECT METRICS]
│   │  ├─ Read scalp_lab_state.json (current positions, closed trades)
│   │  ├─ Count open positions, unrealized P&L
│   │  ├─ Read SEVEN_DAY_TEST_LOG.json (history)
│   │  └─ Read scalp_engine_config.json (verify stops=1.0)
│   │
│   ├─ [RUN BACKTEST]
│   │  └─ python3 ml/backtest_edge.py
│   │     → edge_per_trade, win_rate, trade_count
│   │
│   ├─ [GITHUB SYNC]
│   │  ├─ github_sync.py --fetch (pull top 5 bot strategies)
│   │  ├─ Analyze what other bots do
│   │  └─ github_sync.py --push (upload daily results)
│   │
│   ├─ [30-AGENT ANALYSIS] (interconnected_agent_ecosystem.py)
│   │  │
│   │  ├─ TIER 0: Sensors read raw state
│   │  ├─ TIER 1: 8 Diagnostics analyze domains (parallel)
│   │  ├─ TIER 2: 4 Synthesis agents combine findings (parallel)
│   │  ├─ TIER 3: 5 Action agents execute fixes (parallel)
│   │  └─ TIER 4: 3 Learning agents update BRAIN (parallel)
│   │
│   ├─ [OLLAMA VERDICT]
│   │  └─ Query local llama3.1:8b for human-readable verdict
│   │     → GREEN / YELLOW / RED
│   │
│   └─ [LOG RESULT]
│      └─ Append to SEVEN_DAY_TEST_LOG.json:
│         {
│           "day": 2,
│           "timestamp": "2026-07-24T09:00:00Z",
│           "edge_per_trade": 0.1900,
│           "trades": 44,
│           "win_rate": 0.31,
│           "open_positions": 12,
│           "ollama_verdict": "YELLOW",
│           "reasoning": "Edge beating baseline, but volume low"
│         }
│
└─ 09:05:00 UTC: COMPLETE

Next run: 2026-07-25 09:00 UTC (Day 3)
```

---

## 5-Minute Healing Cycle (Continuous)

### Trigger: `com.aryan.self-healing-monitor.plist`

Runs **every 5 minutes** (00, 05, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55 of each hour)

```
EVERY 5 MINUTES
│
├─→ self_healing_agents.py --monitor
│   │
│   ├─ [TIER 1] Detect problems (read-only scans)
│   │  ├─ Agent 1: Stuck Position Healer
│   │  │  → Find positions >48h old
│   │  │  → Candidates for force-close
│   │  │
│   │  ├─ Agent 2: Feed Reconnector
│   │  │  → Check market data staleness
│   │  │  → Detect stale_nodata
│   │  │
│   │  ├─ Agent 3: Config Fixer
│   │  │  → Verify stops=1.0 (critical!)
│   │  │  → Detect re-enable of stops
│   │  │
│   │  └─ Agents 4-10: Validate integrity
│   │     (State, Orders, Memory, Spreads, Liquidity, Fills)
│   │
│   ├─ [TIER 2] Diagnose via Ollama
│   │  └─ For each problem: query llama3.1:8b for root cause
│   │
│   ├─ [TIER 3] Execute autonomous fixes
│   │  └─ Only SAFE-AUTO class (pre-approved)
│   │     ├─ Re-disable stops
│   │     ├─ Force-close stuck positions
│   │     └─ Reconnect feeds
│   │
│   └─ [LOG HEALING]
│      └─ Append to /tmp/self-healing-monitor.log
│
└─→ self_healing_agents_extended.py --monitor
   │
   ├─ [Agents 11-20] Extended diagnostics
   │  ├─ Position validator
   │  ├─ Slippage monitor
   │  ├─ Fill auditor
   │  ├─ Correlation checker
   │  ├─ Drawdown guardian
   │  └─ Win-rate tracker
   │
   └─ [LOG EXTENDED]
      └─ Append to /tmp/self-healing-monitor.log
```

**Cycle Time:** ~30 seconds (all 20 agents run in parallel)  
**Cadence:** Every 5 minutes = **288 cycles/day**  
**Uptime:** 24/7 (immune to daily cycle, runs in background)

---

## BRAIN Integration (Live Decision-Making)

### How the BRAIN is Used

```
┌────────────────────────────────────────────────────────────────┐
│              BRAIN: ~/.graphify/global-graph.json              │
│                                                                 │
│  CONCEPTS:                                                     │
│  ├─ Lessons learned (from loss_autopsy, manual analysis)      │
│  ├─ Strategy graveyard (dead legs, why they died)            │
│  ├─ Workflow library (proven processes)                       │
│  ├─ Edge memories (calibrated-mids, gap-through, etc.)       │
│  └─ Decision rules (never re-enable stops, etc.)             │
│                                                                 │
│  QUERIED BY:                                                   │
│  ├─ Daily agents (09:00 cycle)                                │
│  │  "Is this leg still valid according to BRAIN?"             │
│  │  "Should we disable this based on past lessons?"           │
│  │                                                             │
│  ├─ Healing agents (5-min cycle)                              │
│  │  "What is the root cause? (checked against lessons)"       │
│  │  "Is this config drift? (checked against rules)"           │
│  │                                                             │
│  └─ Learning agents (post-cycle)                              │
│     "Add this new finding to BRAIN for next cycle"            │
│                                                                 │
│  UPDATED BY:                                                   │
│  ├─ Learning_EdgeLearner (Tier 4)                             │
│  │  → Records hypothesis status (VALIDATED/UNCERTAIN)         │
│  │                                                             │
│  ├─ Learning_LegQualityLearner (Tier 4)                       │
│  │  → Records best/worst leg patterns                         │
│  │                                                             │
│  ├─ Learning_CapitalEfficiencyLearner (Tier 4)                │
│  │  → Records concentration patterns                          │
│  │                                                             │
│  └─ loss_autopsy (post-resolution)                            │
│     → Records why losses happened, adds to graveyard          │
│                                                                 │
└────────────────────────────────────────────────────────────────┘

BRAIN QUERY EXAMPLES (in agent prompts):

├─ "According to BRAIN, why did nearres fade fail?"
│  → Returns: Resolution misread (user thought "near" was "strong")
│
├─ "What are the known ways stops get re-enabled?"
│  → Returns: Manual edit, config reload, user override
│
├─ "Which leg families have positive expected value?"
│  → Returns: Structural arb (basket_arb), data arb (PortWatch)
│
└─ "Is there a rule against using this edge?"
   → Returns: Yes, gap-through kills it on this venue
```

### Workflow + BRAIN Integration (Concrete Example)

```
SCENARIO: Day 2 morning, 09:00 UTC check detects stop re-enabled

STEP 1: Agent detects problem
│
├─ Diagnostic_ConfigIntegrity reads scalp_engine_config.json
│  → Finds: wf_stop = 0.08 (should be 1.0)
│  → Triggers alert
│
STEP 2: Consult BRAIN
│
├─ Agent queries: "What could cause stop re-enable?"
│  → BRAIN returns (from memory):
│     "past lessons: manual edit, config reload, app crash"
│
├─ Agent queries: "Is this a known safe fix?"
│  → BRAIN returns:
│     "Yes, Stop-Loss Guardian (Agent 7) auto-disables.
│      Rule: never skip this check, priority = CRITICAL"
│
STEP 3: Execute fix + Log
│
├─ Action_ConfigHealer re-disables: wf_stop = 1.0
├─ Verify: read config again, confirm fix
├─ Log: "FIXED: wf_stop re-enable (0.08→1.0)"
│
STEP 4: Update BRAIN
│
└─ Learning_EdgeLearner records:
   "Stop re-enable detected & fixed on Day 2.
    Pattern: likely from daily reconfig.
    Action: Guard already caught it."
   → Adds to BRAIN for pattern matching on future cycles
```

---

## Vault Sync Cycle (Every 3 Hours)

### Trigger: `com.aryan.vault-sync.plist`

Runs at **00:00, 03:00, 06:00, 09:00, 12:00, 15:00, 18:00, 21:00 UTC**

```
EVERY 3 HOURS
│
├─→ vault_sync.py
│   │
│   ├─ [DETECT CHANGES]
│   │  └─ git status in ~/Documents/PolymarketVault
│   │     → Any modified files?
│   │
│   ├─ [STAGE & COMMIT]
│   │  ├─ git add -A
│   │  └─ git commit -m "Auto-sync vault: N files changed"
│   │
│   ├─ [PULL FROM GITHUB]
│   │  └─ git pull --rebase origin main
│   │     (handles external edits, branches, pulls latest)
│   │
│   ├─ [PUSH TO GITHUB]
│   │  └─ git push origin main
│   │     (all vault notes now on GitHub)
│   │
│   └─ [LOG SYNC]
│      └─ Append to /tmp/vault-sync.log
│         "[2026-07-24T09:00:00] Synced 47 files ✓"
│
└─ Vault is always in sync with GitHub
```

**Sync Locations:**
- Local: `~/Documents/PolymarketVault/.git`
- Remote: `github.com/goodwearrinfo-aryan/PolymarketVault`
- Auto-mirror to vault: `obsidian_snapshot.py` (every 6h)

---

## Data Flow: From Raw State to Decisions

```
LIVE TRADING STATE                DAILY ANALYSIS              BRAIN MEMORY
│                                 │                           │
├─ scalp_lab_state.json ─────────→ Tier 0 Sensors ────────────→ Query current
│  (positions, P&L)               │                           │  leg viability
│                                 │                           │
├─ scalp_lab.log ────────────────→ Diagnostic_FeedHealth     │
│  (recent trades, fills)         │                           │
│                                 │                           │
├─ SEVEN_DAY_TEST_LOG.json ──────→ Diagnostic_EdgeQuality    │
│  (7-day history)                │  Diagnostic_Trend        │
│                                 │                           │
└─ scalp_engine_config.json ─────→ Diagnostic_ConfigIntegr   │
   (stops, parameters)            │                           │
                                  │                           │
                                  ├─ Synthesis_SafetyVerdict ─→ Update: is test safe?
                                  │  Synthesis_HealthScore     │
                                  │                           │
                                  ├─ Learning_EdgeLearner     → Update: hypothesis
                                  │  Learning_LegQualityLearner status
                                  │  Learning_CapitalLearn    │
                                  │                           │
                                  ├─ SEVEN_DAY_TEST_LOG ─────→ Append: daily verdict
                                  │  (updated daily)          │
                                  │                           │
                                  └─ GitHub (daily push) ────→ Team visibility
                                     (results + strategies)    │
```

---

## Autonomous Execution Timeline

### 24-Hour Window (Sample)

```
2026-07-24T00:00 UTC
├─ 00:00 — Vault sync (3h cadence)
│  └─ Check for vault changes, commit & push to GitHub
│
├─ 00:05 — Healing cycle runs
│  └─ 20 agents scan for problems
│
├─ 00:10 — Healing cycle runs
├─ 00:15 — Healing cycle runs
├─ 00:20 — Healing cycle runs
├─ ... (every 5 minutes)
│
├─ 03:00 — Vault sync
│  └─ Auto-snapshot bot state to vault
│
├─ 06:00 — Vault sync
│
├─ 09:00 — DAILY CHECK (main analysis)
│  ├─ Collect metrics
│  ├─ Run backtest
│  ├─ 30 agents analyze + learn
│  ├─ Generate verdict (GREEN/YELLOW/RED)
│  ├─ Push to GitHub
│  └─ Update vault
│
├─ 09:05 — Healing cycle continues
├─ 09:10 — Healing cycle continues
│
├─ 12:00 — Vault sync
│
├─ 15:00 — Vault sync
│
├─ 18:00 — Vault sync
│
├─ 21:00 — Vault sync
│
└─ Next day: 00:00 UTC

TOTAL AUTONOMOUS TASKS PER DAY:
├─ Daily analysis: 1 run (09:00 UTC)
├─ Healing cycles: 288 runs (every 5 min)
├─ Vault syncs: 8 runs (every 3h)
└─ TOTAL: 297 autonomous operations
```

---

## Key Integration Points

### 1. BRAIN ↔ Daily Agents
**Decision Point:** Should we disable this leg?
- Query BRAIN: "Why did similar legs fail before?"
- Get: Lessons from graveyard (resolution misread, gap-through, etc.)
- Decision: Disable if it matches a known killer pattern

### 2. Daily Results ↔ Learning Agents
**Update Point:** Edge hypothesis status
- Day 1 edge: +$0.1900 (beats -$0.159 baseline)
- Learning_EdgeLearner: "VALIDATES hypothesis" → Record to BRAIN
- Day 2 edge: +$0.1950 (trend: improving)
- Learning_EdgeLearner: "CONTINUES validation" → Confidence rises

### 3. Healing Cycles ↔ BRAIN
**Detection Point:** Is this a known problem?
- Agent 3 finds: stop re-enabled to 0.08
- Query BRAIN: "Known causes of stop re-enable?"
- Get: Manual edit, config reload
- Action: Re-disable, log pattern

### 4. Vault ↔ Human Review
**Transparency Point:** Share state with GitHub
- Vault contains: all agent findings, bot state, BRAIN excerpts
- Synced every 3 hours to GitHub (goodwearrinfo-aryan/PolymarketVault)
- Human can review: workflow progress, decisions, brain state
- Can edit vault locally or via GitHub, auto-pulls on next sync

---

## Summary: Autonomous Intelligence

```
                    30-AGENT SYSTEM
                    
    ┌─────────────────────────────────────────┐
    │ LIVE TRADING (scalp_lab.py)              │
    │ Every ~60s: enter/exit positions         │
    └────────────┬────────────────────────────┘
                 │
    ┌────────────▼────────────────────────────┐
    │ BRAIN PROCEDURAL MEMORY                  │
    │ Live queries: past lessons, rules        │
    └─────────────────────────────────────────┘
                 │
    ┌────────────▼────────────────────────────┐
    │ AUTOMATED WORKFLOWS                      │
    │ Daily (09:00): 30-agent analysis        │
    │ Every 5min: 20-agent healing            │
    │ Every 3h: vault sync to GitHub          │
    └──────────┬─────────────────────────────┘
               │
    ┌──────────▼─────────────────────────────┐
    │ RESULTS & LEARNING                      │
    │ Verdicts (GREEN/YELLOW/RED)             │
    │ Brain updates (new lessons)             │
    │ Vault snapshots (team visibility)       │
    └─────────────────────────────────────────┘
```

**Philosophy:**
- Detect problems in real-time (5-min healing)
- Learn from daily analysis (09:00 check)
- Remember lessons in BRAIN (live graph)
- Share progress to team (3-h vault sync)
- **Zero human required** (autonomous for 7 days)

---

## Test Status (Day 2)

**Hypothesis:** Removing stops allows longer holds → improves Sharpe

**Evidence So Far:**
- Day 1 edge: +$0.1900/trade (baseline: -$0.159)
- Verdict: 🟡 YELLOW (edge good, volume low)
- Healing: 8 stuck positions detected, stop re-enable caught

**Next 48h:**
- Continue daily analysis + healing
- Watch for hypothesis drift (trend reversal)
- Collect more trades (volume goal: >100 trades)
- Update BRAIN with new patterns

**Completion:** 2026-07-30 22:33 UTC

---

## Reference: All 30 Agents (Distinct Jobs)

**Tier 0 (4 Sensors):** StateReader, LogReader, ConfigReader, TestLogReader  
**Tier 1 (8 Diagnostics):** ConfigIntegrity, PositionHealth, EdgeQuality, StateIntegrity, FeedHealth, PerformanceTrend, CapitalAllocation, LegPerformance  
**Tier 2 (4 Synthesis):** SafetyVerdict, EdgeVsBaseline, HealthScore, ActionPriority  
**Tier 3 (5 Actions):** ConfigHealer, LegDisabler, PositionCloser, FeedReconnector, StrategyMutator  
**Tier 4 (3 Learning):** EdgeLearner, LegQualityLearner, CapitalEfficiencyLearner  
**Continuous Healing (20 agents):** Agents 1-10 (core), Agents 11-20 (extended)

---

**Workflow Status:** ✅ LIVE  
**BRAIN Integration:** ✅ LIVE  
**Vault Sync:** ✅ LIVE (every 3h)  
**Autonomous Operations:** 288 healing cycles/day + 1 daily check + 8 vault syncs = **297 ops/day**
