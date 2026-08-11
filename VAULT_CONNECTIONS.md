# GitHub Vault — Complete System Connections

**Status:** ✅ FULLY INTEGRATED  
**Location:** `~/Documents/PolymarketVault` ↔ GitHub (synced every 3h)  
**Central Hub:** Vault is the **knowledge backbone** for all systems

---

## The Vault as Central Hub

```
                    GITHUB VAULT
                  (844 files synced
                   every 3 hours)
                        │
        ┌───────────────┼───────────────┐
        │               │               │
        ↓               ↓               ↓
    
    BOT SCRIPTS    ←→  VAULT  ←→  BRAIN GRAPH
    (write state)      (read      (live memory)
                       context)
        │               │               │
    ┌───┴───┐      ┌────┴────┐    ┌───┴───┐
    ↓       ↓      ↓         ↓    ↓       ↓
   Daily  Healing Observer  Reports Lessons
   Check  Cycles Scripts    Hub    Graveyard
```

---

## All Systems Connected to Vault

### 1. AUTO-MIRROR SYSTEMS (Write to vault)

#### **obsidian_snapshot.py** (every 6h via launchd)
- **What:** Auto-mirrors bot state to vault
- **Updates:**
  - `Bot/scalp_lab_state.md` — Live position snapshot
  - `Bot/belief_calibration.md` — Forecast accuracy (Brier score)
  - `Bot/sports_summary.md` — 30-day sports results
  - `Bot/crypto_trader_live.md` — Crypto trading state
  - `Bot/basket_arb.md` — Basket lock opportunities
  - `Bot/analyst_scorecard.md` — Analyst edge scores
  - `brain_snapshot.md` — BRAIN excerpt (top concepts)
- **Cadence:** Every 6h (00:15, 06:15, 12:15, 18:15 UTC)

#### **activity_light.py** (continuous, per task)
- **What:** Tracks which tasks are running in real-time
- **Updates:**
  - `Activity.md` — Pulsing green dot per active task
  - `Live/<task>.md` — Per-task status (running/stalled/error)
  - `Status Hub.md` — Fleet-wide health snapshot
- **Speed:** Updates within seconds of state change

#### **vault_sync.py** (every 3h)
- **What:** Git sync (add/commit/push)
- **Updates:** Pushes ALL vault changes to GitHub
- **Cadence:** 00:00, 03:00, 06:00, 09:00, 12:00, 15:00, 18:00, 21:00 UTC

---

### 2. ANALYSIS SYSTEMS (Write insights to vault)

#### **analyst_agent.py**
- **Writes:** `Reports/analyst_agent.md`
- **Content:** Live analyst edge findings

#### **analyst_data_gate.py**
- **Writes:** 
  - `Reports/analyst_data_gate.md`
  - `Reports/DataArb Radar.md`
- **Content:** Settled-but-mispriced arbs (Hormuz/PortWatch)

#### **analyst_scorecard.py**
- **Writes:** `Bot/analyst_scorecard.md`
- **Content:** Analyst edge scores, top 5 strategies

#### **arb_track.py**
- **Writes:** `Reports/arb_track.md`
- **Content:** Basket arb + structural arb live track

#### **arb_memory.py**
- **Writes:** `Reports/arb_memory.md`
- **Content:** Historical arb performance

#### **basket_paper.py**
- **Writes:** `Bot/basket_paper.md`
- **Content:** Paper basket locks, live edge

#### **basket_arb.py** & **basket_depth.py**
- **Writes:** `Bot/basket_arb.md`
- **Content:** Live basket arb opportunities + exhaustiveness checks

#### **bias_check.py**
- **Writes:** `Reports/bias_check_latest.md`
- **Content:** Project bias audit (confirmation bias, sunk cost, etc.)

#### **candle_fade_watch.py**
- **Writes:** `Candle Fade Watch.md`
- **Content:** Candle pattern monitoring

---

### 3. BRAIN GRAPH SYSTEMS (Store + query BRAIN)

#### **brain_agents.py**
- **Writes:** `brain/Resources/*.md` (concepts, lessons)
- **Reads:** BRAIN for agent decision-making
- **Content:** Agent-specific guidelines + historical patterns

#### **brain_ingest.py**
- **Writes:** Brain concepts from experience
- **Content:** Lessons from losses, edge graveyard

#### **brain_gradients.py**
- **Writes:** `Brain Gradients.md` (BRAIN structure analysis)
- **Content:** Principal axes of knowledge graph

#### **brain_rebuild_nv.py**
- **Rebuilds:** `ConceptGraph/graphify-out/graph.json`
- **Content:** Full BRAIN graph structure

#### **basket_watch.py**
- **Reads:** `brain/Resources/basket-locks.md`
- **Content:** Uses BRAIN to identify valid baskets

---

### 4. OBSERVER/MONITORING SYSTEMS (Read from vault)

#### **activity_light.py** (bidirectional)
- **Reads:** Current task status
- **Writes:** Updates to `Activity.md` + `Live/<task>.md`

#### **vault_connector.py**
- **Reads:** Vault notes for context
- **Writes:** Agent decisions to vault

#### **vault_librarian.py**
- **Reads:** Vault structure
- **Writes:** Organizes + categorizes vault notes

#### **vault_weaver.py**
- **Reads:** All vault notes
- **Writes:** Interconnection links ([[wikilinks]])

#### **vault_fixer_swarm.py**
- **Reads:** Vault health
- **Writes:** Repairs broken links, fixes frontmatter

#### **vault_frontmatter.py**
- **Reads:** Note metadata
- **Writes:** Updates frontmatter tags/types

---

### 5. REPORTING SYSTEMS (Generate reports from vault)

#### Reports Hub
- **Location:** `Reports/` folder (20+ .md files)
- **Content:**
  - `analyst_edge_refuter.md` — Edge refutation log
  - `bandcal.md` — Band calibration data
  - `edge_screen.md` — Edge screening results
  - `latticesnap.md` — Lattice positions
  - `newsmove.md` — News-driven market moves
  - `tapeshock.md` — Tape anomalies
  - `whalecopy.md` — Whale-following results
  - `whalexit.md` — Whale exit signals

#### Live Pipeline
- **Location:** `OpenAlice/Live Pipeline.md`
- **Content:** Real-time OpenAlice trading state

#### Connectors Hub
- **Location:** `Connectors/` folder (195 files)
- **Content:** Status of each data connector

#### Data Sources Hub
- **Location:** `Data Sources/` folder (36 folders)
- **Content:** Each data source + health status

---

### 6. DAILY WORKFLOW INTEGRATION

#### **daily_test_check_ollama.py** (09:00 UTC)
- **Reads from vault:**
  - Historical verdicts from `SEVEN_DAY_TEST_LOG.md`
  - BRAIN lessons for context
  - Previous agent findings
- **Writes to vault:**
  - Via `obsidian_snapshot.py` (next 6h sync)
  - Vault captures all agent outputs

#### **30 Agent System**
- **Tier 4 Learning Agents write:**
  - Edge hypothesis status → BRAIN
  - Leg quality patterns → BRAIN
  - Capital allocation rules → BRAIN
- **All agents read:**
  - BRAIN via `brain "<question>"`
  - Vault notes for strategy context

---

### 7. 5-MINUTE HEALING CYCLE INTEGRATION

#### **self_healing_agents.py** + **self_healing_agents_extended.py**
- **Reads from vault:**
  - Past healing decisions
  - Known problem patterns
  - Stuck position history
- **Writes to vault:**
  - Via `activity_light.py` (immediate status)
  - Via next `obsidian_snapshot.py` run (6h)
  - Via `vault_sync.py` push to GitHub

---

## Data Flow: Vault as Central Knowledge Base

```
RAW STATE                          PROCESSED INSIGHTS
│                                  │
├─ scalp_lab_state.json           │
├─ SEVEN_DAY_TEST_LOG.json        ├─ obsidian_snapshot.py
├─ scalp_lab.log                  │  (every 6h)
└─ scalp_engine_config.json       │
                                   ├→ Bot/scalp_lab_state.md
                                   ├→ Bot/analyst_scorecard.md
                                   ├→ Bot/basket_arb.md
                                   ├→ brain_snapshot.md
                                   │
                                   ↓
                              VAULT NOTES
                              (844 files)
                                   │
        ┌──────────────┬───────────┼───────────┬──────────────┐
        ↓              ↓           ↓           ↓              ↓
    BRAIN GRAPH    REPORTS      LIVE PIPELINE  CONNECTORS   DATA SOURCES
    (lessons)      (analysis)    (monitoring)   (status)     (feeds)
        │              │           │           │              │
        ├─ Vault        ├─ Vault    ├─ Vault    ├─ Vault       ├─ Vault
        │  Concept      │  Reports  │  Live     │  Connector   │  Source
        │  .md          │  Hub      │  .md      │  Status      │  Status
        │              │           │           │              │
        └──────────────┴───────────┴───────────┴──────────────┘
                                   │
                                   ↓
                          DAILY AGENTS QUERY
                          (09:00 UTC)
                                   │
                         ┌─────────┼─────────┐
                         ↓         ↓         ↓
                      CONTEXT    LESSONS   RULES
                      (found)    (BRAIN)   (workflow)
                         │         │         │
                         └─────────┼─────────┘
                                   ↓
                          INFORMED DECISIONS
                          (30 agents)
                                   │
                                   ↓
                          UPDATE BRAIN + VAULT
                          (Learning agents)
                                   │
                                   ↓
                              NEXT CYCLE
                         (improved decisions)
```

---

## GitHub Sync Pipeline

```
LOCAL VAULT              GITHUB REPO           TEAM ACCESS
│                        │                     │
├─ 844 files            └─ polymarket-vault    ├─ Pull vault
├─ git add -A               (private repo)     ├─ Review findings
├─ git commit           │                      ├─ Historical view
├─ git push             └─ Every 3h syncs     └─ Branch history
│                           via vault_sync.py
└─ Every 3h: 00, 03,
   06, 09, 12, 15,
   18, 21 UTC
```

---

## Vault as Source of Truth

### For Agents:
- **Daily agents:** Query `Reports/`, `Bot/`, BRAIN for context
- **Healing agents:** Read `Live/<task>.md` for current state
- **Learning agents:** Read BRAIN, update with new lessons

### For Humans:
- **Dashboard:** `Status Hub.md` — One-page health check
- **Activity:** `Activity.md` — Pulsing green dots of running tasks
- **Reports:** `Reports/` folder — 20+ analysis reports
- **BRAIN:** `brain/Resources/` — Full knowledge graph
- **GitHub:** Private repo — Team history, branch tracking

### For Automation:
- **Vault is the memory:** Persists across bot restarts
- **Git is the archive:** GitHub keeps 90-day history
- **BRAIN is the wisdom:** Lessons accumulated over time

---

## Live Vault Monitoring

### Check vault health:
```bash
# What's in the vault right now?
ls ~/Documents/PolymarketVault/Bot/
# → scalp_lab_state.md (current positions)
# → analyst_scorecard.md (edge scores)
# → basket_arb.md (live locks)

# What tasks are running?
cat ~/Documents/PolymarketVault/Activity.md
# → Shows green dots for active tasks

# What's the latest analysis?
cat ~/Documents/PolymarketVault/Reports/analyst_data_gate.md
# → Latest data arb findings

# Check BRAIN:
brain "Why did nearres fade fail?"
# → Returns lesson from BRAIN graph
```

### Vault sync status:
```bash
# Check last sync:
tail -20 /tmp/vault-sync.log

# See what changed:
cd ~/Documents/PolymarketVault && git log --oneline -10

# Push pending changes (manual):
cd ~/Documents/PolymarketVault && git push origin main
```

---

## Summary: Vault Connectivity

### 25+ Systems Write to Vault:
- ✅ obsidian_snapshot.py (bot state mirror)
- ✅ activity_light.py (task activity)
- ✅ analyst_agent.py through analyst_scorecard.py (analysis)
- ✅ arb_track.py, arb_memory.py (arb status)
- ✅ basket_paper.py, basket_arb.py, basket_depth.py (basket locks)
- ✅ bias_check.py (audit)
- ✅ brain_agents.py, brain_ingest.py, brain_gradients.py (BRAIN updates)
- ✅ vault_librarian.py, vault_weaver.py, vault_fixer_swarm.py (vault management)
- ✅ vault_frontmatter.py (metadata)
- ✅ candle_fade_watch.py (pattern monitoring)

### 30 Agents Query Vault:
- ✅ Daily agents (09:00 UTC) — read BRAIN + context
- ✅ Healing agents (every 5 min) — read current state
- ✅ Learning agents (after cycle) — update BRAIN

### GitHub Connection:
- ✅ vault_sync.py pushes every 3h
- ✅ 844 files on GitHub (goodwearrinfo-aryan/PolymarketVault)
- ✅ Full audit trail + branch history
- ✅ Team access to findings, reports, BRAIN

### BRAIN Connection:
- ✅ Stored in vault (`brain/Resources/`)
- ✅ Queried by all agents via `brain "<question>"`
- ✅ Updated by Learning agents (daily)
- ✅ Persists across restarts (git + GitHub)

---

## Result

**Vault is NOT just storage — it's the nervous system.**

Every decision → vault  
Every lesson → BRAIN → vault  
Every finding → vault → GitHub  
Every cycle → reads vault for context

All 30 agents, all workflows, all systems **converge on the vault**.

✅ **FULLY INTEGRATED**
