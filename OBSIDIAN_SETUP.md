# Obsidian Vault — Complete Setup & Integration

**Status:** ✅ FULLY CONFIGURED  
**Vault Location:** `~/Documents/PolymarketVault`  
**GitHub Sync:** Every 3 hours (auto-commit & push)  
**BRAIN:** Stored in vault, live graph integrated

---

## How to Open the Vault

### Option 1: Direct Open in Obsidian (Easiest)
```bash
# Open the vault in Obsidian
open -a Obsidian ~/Documents/PolymarketVault
```

### Option 2: Via Obsidian App
1. Launch Obsidian
2. Click "Open folder as vault"
3. Select `~/Documents/PolymarketVault`
4. Click "Open"

### Option 3: Command Line
```bash
# Navigate and use Obsidian command
cd ~/Documents/PolymarketVault
obsidian .
```

---

## Vault Structure (What You'll See)

```
PolymarketVault/
├─ 🧠 Shared Brain.md ..................... Central knowledge hub
├─ Activity.md ........................... Live task activity (pulsing green)
├─ Status Hub.md ......................... One-page health check
├─ Brain Live Gate.md .................... BRAIN decision rules
│
├─ Bot/ (20 files) ...................... LIVE BOT STATE
│  ├─ scalp_lab_state.md ................ Current positions + P&L
│  ├─ analyst_scorecard.md .............. Edge scores + top 5 strategies
│  ├─ basket_arb.md ..................... Live basket locks
│  ├─ crypto_trader_live.md ............. Crypto trading state
│  ├─ belief_calibration.md ............. Forecast accuracy (Brier)
│  └─ 15+ more state files
│
├─ brain/ (concepts + resources) ........ BRAIN GRAPH
│  ├─ Concepts/ .......................... 72+ concept nodes
│  │  ├─ calibrated-mids.md
│  │  ├─ gap-through.md
│  │  ├─ resolution-misread.md
│  │  └─ ... (all lessons)
│  ├─ Resources/ ......................... Reference materials
│  │  ├─ basket-locks.md
│  │  ├─ edge-patterns.md
│  │  └─ workflow-rules.md
│  └─ Hubs/ ............................ Category hubs
│
├─ Reports/ (20+ files) ................. ANALYSIS OUTPUT
│  ├─ analyst_agent.md .................. Edge findings
│  ├─ analyst_data_gate.md .............. Data arb opportunities
│  ├─ arb_track.md ...................... Arb performance
│  ├─ edge_screen.md .................... Edge screening
│  ├─ whalecopy.md ...................... Whale-following analysis
│  └─ 15+ more reports
│
├─ Live/ (per-task monitors) ............ LIVE TASK STATUS
│  ├─ scalp_lab.md ...................... Trading watchdog status
│  ├─ analyst.md ........................ Analyst edge status
│  ├─ basket_watch.md ................... Basket lock monitor
│  ├─ daily_check.md .................... Daily check cycle status
│  └─ ... (one per task)
│
├─ Connectors/ (195 files) .............. DATA SOURCE CONNECTORS
│  ├─ Status of each feed
│  ├─ Connection health
│  └─ Last update time
│
├─ Data Sources/ (36 folders) .......... WORLD DATA
│  ├─ Crypto/
│  ├─ Markets/
│  ├─ News/
│  ├─ Weather/
│  └─ ... (all free data sources)
│
├─ Agents/ (multiple squads) ........... AGENT FLEET
│  ├─ Squad notes per capability
│  └─ Agent descriptions
│
├─ Analyst/ .............................. ANALYST TRACK
│  ├─ Analyst Positions.md .............. Open bets
│  ├─ Analyst Candidate Queue.md ........ Pipeline
│  └─ Analyst Calibration.md ............ Forecast accuracy
│
├─ OpenAlice/ (10+ files) ............... TRADING SYSTEM
│  ├─ OpenAlice Fleet.md ................ System status
│  ├─ Live Pipeline.md .................. Real-time order flow
│  ├─ Candle Legs.md .................... Pattern monitoring
│  └─ Tournament.md ..................... Strategy tournament
│
├─ Canvas Files (visual maps) ........... KNOWLEDGE GRAPHS
│  ├─ Brain Gradient Map.canvas ......... BRAIN structure
│  ├─ Fleet Brain.canvas ................ Agent fleet view
│  ├─ System Map.canvas ................. Full system architecture
│  ├─ Strategy Map.canvas ............... Strategy landscape
│  └─ Bot Architecture.canvas ........... Trading system diagram
│
└─ 🧠 Shared Brain.md .................... Master knowledge hub

.obsidian/ ............................. Obsidian config
├─ app.json
├─ appearance.json
├─ workspace.json
├─ core-plugins.json
├─ community-plugins.json
├─ graph.json
├─ plugins/ ........................... Installed plugins
└─ themes/ ............................ Custom themes
```

---

## Key Pages to Explore

### 1. **Status Hub** (Start Here)
```
Opens: Status Hub.md
Shows: One-page health check
- Active tasks (green dots)
- Recent findings
- Next scheduled checks
- Test progress (Day 2 of 7)
```

### 2. **Activity** (Live Updates)
```
Opens: Activity.md
Shows: Pulsing indicators per task
- scalp_lab ......................... Trading bot
- healing-monitor ................... Healing cycles
- analyst-agent .................... Edge finding
- daily-check ...................... 09:00 UTC check
Updates: Within seconds of state change
```

### 3. **BRAIN** (Knowledge Graph)
```
Opens: 🧠 Shared Brain.md → Concepts/
Shows: All lessons + patterns
- Why did nearres fade fail?
- What disables a leg?
- Is this edge proven?
- Graph view (visual layout)
Query via: `brain "<question>"` (CLI)
```

### 4. **Live Bot State** (Current Positions)
```
Opens: Bot/scalp_lab_state.md
Shows: 
- Open positions (12 current)
- Unrealized P&L
- Per-leg performance
- Forecast accuracy (Brier)
Updated: Every 6h by obsidian_snapshot.py
```

### 5. **Canvas Maps** (Visual Architecture)
```
Double-click any .canvas file:
- System Map.canvas → Full system diagram
- Brain Gradient Map.canvas → BRAIN structure
- Fleet Brain.canvas → Agent fleet
- Bot Architecture.canvas → Trading system
```

---

## Live Sync: How Obsidian Connects to Automation

### Updates Flow

```
BOT RUNS                          OBSIDIAN DISPLAYS
(every 60s)
│                                 
├─ scalp_lab.py ────────────────→ (state generated)
│
├─ obsidian_snapshot.py (every 6h)
│  └─ Reads scalp_lab_state.json
│     ↓
│  └─ Writes Bot/scalp_lab_state.md
│     ↓
│  └─ Git commit (auto-staged)
│     ↓
│  └─ Vault syncs to GitHub (next 3h window)
│
└─ activity_light.py (per second)
   └─ Writes Live/<task>.md + Activity.md
      ↓
   └─ Updates show in Obsidian instantly
      (no sync needed — local file)
```

### Workflow Integration

```
09:00 UTC: daily_test_check_ollama.py runs
    ↓
30 agents analyze (Tier 0→4)
    ↓
Learning agents update BRAIN
    ↓
Results written to vault (bot/ folder)
    ↓
vault_sync.py pushes to GitHub at 09:00
    ↓
Obsidian shows latest findings
    ↓
User opens Obsidian, reads new insights
```

---

## Using Obsidian: Key Features

### 1. Graph View (See Connections)
```
Menu: Graph view (top-left icon)
Shows:
- All 844 notes as nodes
- Links between concepts
- BRAIN connectivity
- Concept clusters
Colors:
- Green ........................ Active concepts
- Blue ......................... BRAIN nodes
- Red .......................... Graveyard (dead legs)
```

### 2. Search (Find Anything)
```
Hotkey: Cmd+P
Search: "status", "edge", "basket", "win-rate"
Shows: Matching notes + preview
Example:
  Search: "why nearres"
  Result: graveyard/nearres-fade-failure.md
```

### 3. BRAIN Queries (Ask Questions)
```
Terminal: brain "<question>"
Example: brain "Is this edge proven?"
Returns: Relevant concepts + lessons
Updates automatically as BRAIN grows
```

### 4. Backlinks (See References)
```
Any note: Shows "Linked from" section
Example: Open Bot/scalp_lab_state.md
Shows all notes that reference it
Helps trace data flow
```

### 5. Canvas (Visual Mapping)
```
File: System Map.canvas (visual diagram)
Shows: Full system architecture
Zoom, pan, explore connections
Can edit + add new nodes
```

---

## Real-Time Updates in Obsidian

### Instant Updates (No Sync Needed)
- `Activity.md` — Updates per second (task status)
- `Live/<task>.md` — Per-task monitors
- `Status Hub.md` — Summary view

### 6-Hour Updates (From Snapshot)
- `Bot/scalp_lab_state.md` — Position snapshot
- `Bot/analyst_scorecard.md` — Edge scores
- `Bot/basket_arb.md` — Basket locks
- `Bot/belief_calibration.md` — Forecast accuracy
- `brain_snapshot.md` — BRAIN excerpt

### Daily Updates (From Daily Check)
- `Reports/analyst_agent.md` — New findings
- `Reports/edge_screen.md` — Screening results
- `BRAIN concepts/` — New lessons learned

### Every 3 Hours (GitHub Backup)
- All vault changes pushed
- `goodwearrinfo-aryan/PolymarketVault`
- Full team access + history

---

## Obsidian Plugins Configured

### Enabled Core Plugins
- **Graph view** — Visualize connections
- **Backlinks** — See references
- **Outline** — Navigate document structure
- **Search** — Find notes
- **Command palette** — Hotkey access

### Community Plugins (if configured)
- Data view (optional)
- Canvas maps (built-in)
- Custom CSS (themes)

---

## Workflow Examples

### Example 1: Check Daily Verdict (2026-07-25 09:00 UTC)
```
1. Open Obsidian
2. Go to: Bot/scalp_lab_state.md
3. See: Live positions + P&L
4. Check: Reports/analyst_agent.md
5. Read: Latest edge findings
6. Query: brain "edge hypothesis status"
7. Result: Shows if hypothesis is VALIDATED/UNCERTAIN/INVALIDATED
```

### Example 2: Investigate Stuck Position (Any Time)
```
1. Open Obsidian
2. Go to: Activity.md (see green dots)
3. Click: Live/healing-monitor.md
4. Read: What was healed last cycle
5. Query: brain "Why do positions get stuck?"
6. Result: Past lessons on stuck positions
7. Action: Manual check or wait for next 5-min cycle
```

### Example 3: Learn from Loss (After Resolution)
```
1. Market resolves (opposite your position)
2. loss_autopsy.py runs automatically
3. Obsidian updates: brain/Graveyard/
4. New lesson added to BRAIN
5. Next daily check uses that lesson
6. Other agents learn from your loss
```

---

## GitHub Access (Team Collaboration)

### View on GitHub
```
URL: github.com/goodwearrinfo-aryan/PolymarketVault
Access: Private repo (team only)
Branch: main
History: Every 3-hour sync is a commit
```

### Review History
```
GitHub → Commits
Shows: What changed each sync
Example: "Auto-sync vault: 47 files changed"
Timestamp: When it happened (3h cadence)
```

### Pull Latest (Manual)
```bash
cd ~/Documents/PolymarketVault
git pull origin main
# (normally happens automatically at 3h intervals)
```

---

## Test Progress in Obsidian

### During 7-Day Test (2026-07-23 to 2026-07-30)

#### Day 1 (2026-07-24)
```
Activity.md: 🟢🟢🟢 All systems running
Bot/scalp_lab_state.md: 12 open positions
Reports/analyst_agent.md: "Edge beating baseline"
Status: 🟡 YELLOW (good edge, low volume)
```

#### Daily Updates
```
09:00 UTC: daily_test_check_ollama.py runs
    ↓
Obsidian auto-updates via obsidian_snapshot.py
    ↓
Read: Status Hub.md for verdict
    ↓
Check: Reports/ for analysis
    ↓
Query: brain "hypothesis status?"
```

#### Day 7 (2026-07-30)
```
Test ends: 22:33 UTC
Final verdict: GREEN / YELLOW / RED
Post-analysis: loss_autopsy evaluates entire test
BRAIN updates: New lessons learned
GitHub: Full 7-day history archived
```

---

## Quick Commands

### Terminal Access
```bash
# Open vault in Obsidian
open -a Obsidian ~/Documents/PolymarketVault

# Check latest status
cat ~/Documents/PolymarketVault/Activity.md

# Read current positions
cat ~/Documents/PolymarketVault/Bot/scalp_lab_state.md

# Query BRAIN
brain "Is this edge proven?"

# Check vault sync status
tail -10 /tmp/vault-sync.log

# View GitHub history
cd ~/Documents/PolymarketVault && git log --oneline -20
```

---

## Summary: Obsidian in Your System

```
OBSIDIAN VAULT
(~/Documents/PolymarketVault)
    │
    ├─ LIVE UPDATES
    │  ├─ Activity.md (per second)
    │  ├─ Live/<task>.md (per second)
    │  └─ Status Hub.md (per second)
    │
    ├─ 6-HOUR SNAPSHOTS
    │  ├─ Bot/scalp_lab_state.md
    │  ├─ Bot/analyst_scorecard.md
    │  └─ brain_snapshot.md
    │
    ├─ BRAIN GRAPH
    │  ├─ 72+ concepts (lessons learned)
    │  ├─ Strategy graveyard (why legs died)
    │  ├─ Workflow rules (never do X)
    │  └─ Query via: brain "<question>"
    │
    ├─ REPORTS (Analysis)
    │  ├─ Edge findings
    │  ├─ Basket locks
    │  ├─ Whale activity
    │  └─ Updated daily
    │
    ├─ VISUAL MAPS
    │  ├─ System Map.canvas
    │  ├─ Brain Gradient Map.canvas
    │  ├─ Fleet Brain.canvas
    │  └─ Double-click to open
    │
    └─ GITHUB SYNCED
       ├─ Every 3h auto-push
       ├─ goodwearrinfo-aryan/PolymarketVault
       └─ Full team access + history
```

✅ **OBSIDIAN IS YOUR WINDOW INTO THE ENTIRE SYSTEM**

Open it. Explore. Query. Learn. The vault knows everything.

---

## Test Dashboard (Live in Obsidian)

**Open:** `Status Hub.md`

```
╔═══════════════════════════════════════╗
║   7-DAY STOP-LOSS TEST — DAY 2 OF 7   ║
╚═══════════════════════════════════════╝

ACTIVE TASKS ........................ 🟢🟢🟢
├─ scalp_lab ........................ Running
├─ healing-monitor .................. Running  
├─ daily-check ...................... Next: 2026-07-25 09:00
└─ vault-sync ....................... Next: 2026-07-25 00:00

LATEST VERDICT ...................... 🟡 YELLOW
├─ Edge: $0.1900/trade (beats -$0.159)
├─ Open positions: 12
└─ Outlook: Edge good, volume low

BRAIN STATUS ........................ ✅ LIVE
├─ Concepts: 72
├─ Lessons: Strategy graveyard
└─ Last updated: 2026-07-24 09:05 UTC

GITHUB SYNC ......................... ✅ OK
├─ Last sync: 2026-07-24 09:00 UTC
├─ Pending: 47 files
└─ Next sync: 2026-07-24 12:00 UTC

NEXT ACTION ......................... Wait for Day 2 check
```

---

**Open Obsidian. Everything is there.**
