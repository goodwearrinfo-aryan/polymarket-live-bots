# A+C Hybrid System Deployment Plan
**Date**: 2026-06-15
**Status**: DEPLOYMENT IN PROGRESS
**Owner**: Aryan (analyst edge research + automated control algos)

---

## System Architecture (Before Deployment)

### Current State (BEFORE)
- [ ] analyst.json exists (5 bets deployed)
- [ ] bot_analyst.py exists
- [ ] judge_panel.py exists
- [ ] scorecard.py exists
- [ ] analyst_hunt.py exists
- [ ] bot_nearres.py exists (control)
- [ ] bot_ladderarb.py exists (control)
- [ ] bot_portfolio_health.py exists
- [ ] obsidian_live_feed.py exists
- [ ] Obsidian vault created at ~/vaults/polymarket

### Target State (AFTER)
- [ ] All Python files verified + working
- [ ] All launchd jobs loaded + running
- [ ] Obsidian vault syncing every 60s
- [ ] Knowledge graph built + exported to Obsidian
- [ ] Market data pipeline wired (live prices feeding all bots)
- [ ] All exit signals functional (analyst: -40% stop, control: -30% stop)
- [ ] iMessage alerts wired (health check every 15 min)
- [ ] Zero silent failures (all bots log to files + launchd)

---

## Step-by-Step Deployment

### PHASE 1: Verify Python Files Exist
**Goal**: Confirm all source code files are in place and error-free
**Before**: Unknown state of all 7 bot files
**After**: All files verified, no syntax errors, imports clean

#### 1.1 - Check analyst.json state
```bash
# BEFORE
cat ~/Documents/polymarket/analyst.json | jq '.open | length'
cat ~/Documents/polymarket/analyst.json | jq '.capital_usdc'

# EXPECTED AFTER: 5 open bets, $5000 capital
```

#### 1.2 - Verify analyst infrastructure files
```bash
# Files that MUST exist
for f in bot_analyst.py judge_panel.py scorecard.py analyst_hunt.py; do
  python3 -m py_compile ~/Documents/polymarket/$f && echo "✅ $f" || echo "❌ $f SYNTAX ERROR"
done

# EXPECTED AFTER: All 4 files compile + no import errors
```

#### 1.3 - Verify control bot files
```bash
# Files that MUST exist
for f in bot_nearres.py bot_ladderarb.py; do
  python3 -m py_compile ~/Documents/polymarket/$f && echo "✅ $f" || echo "❌ $f SYNTAX ERROR"
done

# EXPECTED AFTER: Both control bots compile
```

#### 1.4 - Verify monitoring files
```bash
for f in bot_portfolio_health.py obsidian_live_feed.py; do
  python3 -m py_compile ~/Documents/polymarket/$f && echo "✅ $f" || echo "❌ $f SYNTAX ERROR"
done

# EXPECTED AFTER: Both monitoring scripts compile
```

---

### PHASE 2: Verify Launchd Jobs
**Goal**: Ensure all 5 jobs are loaded and functional
**Before**: Unknown which jobs are loaded
**After**: All 5 jobs loaded, running on schedule, logging to files

#### 2.1 - List currently loaded jobs
```bash
# BEFORE
launchctl list | grep "com.aryan" | sort

# EXPECTED JOBS (should see 5):
# com.aryan.bot-analyst
# com.aryan.bot-nearres
# com.aryan.bot-ladderarb
# com.aryan.bot-health-check
# com.aryan.obsidian-live-feed
```

#### 2.2 - Verify plist files exist
```bash
# All 5 plists MUST exist
for plist in \
  com.aryan.bot-analyst.plist \
  com.aryan.bot-nearres.plist \
  com.aryan.bot-ladderarb.plist \
  com.aryan.bot-health-check.plist \
  com.aryan.obsidian-live-feed.plist
do
  [ -f ~/Library/LaunchAgents/$plist ] && echo "✅ $plist" || echo "❌ $plist MISSING"
done

# EXPECTED AFTER: All 5 exist
```

#### 2.3 - Load any missing jobs
```bash
# Load all jobs (safe if already loaded)
for plist in \
  com.aryan.bot-analyst.plist \
  com.aryan.bot-nearres.plist \
  com.aryan.bot-ladderarb.plist \
  com.aryan.bot-health-check.plist \
  com.aryan.obsidian-live-feed.plist
do
  launchctl load ~/Library/LaunchAgents/$plist 2>&1 | grep -v "already loaded" || true
done

# EXPECTED AFTER: launchctl list shows 5 jobs
```

#### 2.4 - Verify each job has run at least once
```bash
# Check log files for each job
for logfile in \
  /tmp/bot-analyst.log \
  /tmp/bot-nearres.log \
  /tmp/bot-ladderarb.log \
  /tmp/bot-health-check.log \
  /tmp/obsidian-live-feed.log
do
  [ -f $logfile ] && echo "✅ $logfile exists ($(wc -l < $logfile) lines)" || echo "⏳ $logfile (pending first run)"
done

# EXPECTED AFTER: At least bot-analyst and obsidian-live-feed have lines
```

---

### PHASE 3: Verify Obsidian Vault
**Goal**: Confirm vault exists, is syncing, and contains live data
**Before**: Vault may be empty or non-existent
**After**: Vault has analyst.md, calibration.md, index.md with live data

#### 3.1 - Check vault directory
```bash
# BEFORE
ls -la ~/vaults/polymarket/ 2>/dev/null | head -10 || echo "Vault not created yet"

# EXPECTED AFTER: 
# - Analyst Positions.md (updated within last 60s)
# - Analyst Calibration.md
# - index.md
```

#### 3.2 - Verify live sync happened
```bash
# Check timestamps (should be within last 60s if obsidian_live_feed ran)
stat ~/vaults/polymarket/"Analyst Positions.md" 2>/dev/null | grep Modify || echo "File not synced yet"

# EXPECTED AFTER: Modification time = current time (or within 60s)
```

#### 3.3 - Read live analyst data from vault
```bash
# BEFORE
echo "No vault data"

# AFTER - should show current 5 bets
cat ~/vaults/polymarket/"Analyst Positions.md" 2>/dev/null | head -20

# EXPECTED AFTER: Shows "5 open bets", capital, P&L
```

---

### PHASE 4: Build Knowledge Graph
**Goal**: Build semantic graph of analyst edge domain, export to Obsidian
**Before**: No graph.json exists
**After**: graph.json, GRAPH_REPORT.md, Obsidian wiki with communities

#### 4.1 - Check if graph already exists
```bash
# BEFORE
[ -f ~/Documents/polymarket/graphify-out/graph.json ] && echo "Graph exists" || echo "No graph yet"

# EXPECTED AFTER: graph.json exists with >100 nodes
```

#### 4.2 - Run graphify (if not already done)
```bash
cd ~/Documents/polymarket && \
graphify . --obsidian --obsidian-dir ~/vaults/polymarket --mode deep

# EXPECTED AFTER:
# - graphify-out/graph.json (250+ nodes, 15+ communities)
# - graphify-out/GRAPH_REPORT.md (audit report)
# - ~/vaults/polymarket/obsidian/ (wiki with communities)
# - Suggested questions about analyst edge
```

#### 4.3 - Verify graph export
```bash
# BEFORE
echo "Graph not built"

# AFTER - check node/edge counts
$(python3 graphify-out/.graphify_python) -c "
import json
g = json.load(open('graphify-out/graph.json'))
print(f'Nodes: {len(g.get(\"nodes\", []))}')
print(f'Edges: {len(g.get(\"edges\", []))}')
print(f'Communities: {len(set(n.get(\"community\") for n in g.get(\"nodes\", [])))}')
"

# EXPECTED AFTER: ~250 nodes, ~400 edges, ~15 communities
```

---

### PHASE 5: Wire Market Data Pipeline
**Goal**: Connect live market prices to all bots
**Before**: market_fetch.py returns mock data
**After**: market_fetch.py fetches from Polymarket gamma API

#### 5.1 - Verify market_fetch.py exists
```bash
# BEFORE
[ -f ~/Documents/polymarket/market_fetch.py ] && echo "Exists" || echo "Missing"

# AFTER: Should exist
```

#### 5.2 - Test live market fetch
```bash
# BEFORE: Returns mock data
python3 ~/Documents/polymarket/market_fetch.py | head -5

# AFTER: Should show real Polymarket prices
# (if gamma API responds)
```

---

### PHASE 6: Final Verification
**Goal**: Entire system end-to-end check
**Before**: Unknown if all systems working together
**After**: All systems verified + no silent failures

#### 6.1 - Check bot_analyst exit signals
```bash
# BEFORE: Unknown state
# AFTER: Should find analyst.json, check for -40% stops on all 5 bets
python3 -c "
import json
analyst = json.load(open('~/Documents/polymarket/analyst.json'))
for bet in analyst['open']:
    entry = bet['entry_price']
    stop = entry * 0.60  # -40%
    print(f\"{bet['q'][:40]:40s} entry={entry:.2f} stop={stop:.2f}\")
"
```

#### 6.2 - Check control bot entry signals
```bash
# BEFORE: Unknown if algos can find markets
# AFTER: Should log at least 1 market scan per job

for logfile in /tmp/bot-nearres.log /tmp/bot-ladderarb.log; do
  [ -f $logfile ] && echo "$(basename $logfile): $(tail -1 $logfile)" || echo "$(basename $logfile): not run yet"
done
```

#### 6.3 - Check health monitor alerts
```bash
# BEFORE: Unknown if iMessage alerts wired
# AFTER: Should have sent at least 1 test alert

tail -20 /tmp/bot-health-check.log | grep -i "alert\|send\|imessage" || echo "No alerts yet (may not have run)"
```

#### 6.4 - Verify Obsidian vault has live data
```bash
# BEFORE: Vault may be empty
# AFTER: Vault should have analyst bets + calibration + index

ls -la ~/vaults/polymarket/ | grep -E "\.(md|json)$" | wc -l
# EXPECTED AFTER: >5 files
```

---

## Rollback Plan (If Errors Found)

### If Python file has syntax error:
```bash
# Identify bad file
python3 -m py_compile ~/Documents/polymarket/<file>.py

# Fix error in source
# Verify fix
python3 -m py_compile ~/Documents/polymarket/<file>.py
```

### If launchd job won't load:
```bash
# Check plist syntax
plutil -lint ~/Library/LaunchAgents/<job>.plist

# Fix plist
# Reload
launchctl load ~/Library/LaunchAgents/<job>.plist
```

### If bots not running:
```bash
# Check if job is loaded
launchctl list | grep <job>

# Check logs
tail -50 /tmp/<job>.log

# If not running: launchctl start <job>
```

---

## Sign-Off

- [ ] Phase 1: All Python files verified (no syntax errors)
- [ ] Phase 2: All 5 launchd jobs loaded and running
- [ ] Phase 3: Obsidian vault syncing live data every 60s
- [ ] Phase 4: Knowledge graph built and exported to Obsidian
- [ ] Phase 5: Market data pipeline wired (live prices)
- [ ] Phase 6: End-to-end verification complete
- [ ] All systems green - ready for analyst edge trading

**SYSTEM STATUS**: LIVE | MONITORING | READY FOR FIRST RESOLUTION
