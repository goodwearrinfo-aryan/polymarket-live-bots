# 10 Ollama Agents — Autonomous Tweaks

**Status:** ✅ DEPLOYED  
**Framework:** Local ollama (llama3.1:8b, $0 cost)  
**Integration:** Auto-run daily at 09:00 UTC via daily_test_check_ollama.py

---

## The 10 Agents

### AGENT 1: Alert Monitor
**Purpose:** Decide what warrants an alert (Slack/email)  
**Runs:** Daily after metrics collection  
**Input:** Day's edge, positions, verdict  
**Output:** `{should_alert, severity, message, channel}`  
**Action:** Trigger Slack if critical, email if warning  

```
Example:
  Input: Day 3, edge=$0.15/trade, Red verdict
  Output: should_alert=true, severity=critical
  → Sends Slack: "CRITICAL: Edge degrading, stop-disable hypothesis failing"
```

---

### AGENT 2: Stress Test
**Purpose:** Simulate market crash (BTC -20%) on your positions  
**Runs:** Daily  
**Input:** Current BTC positions (windowshutrand bets)  
**Output:** `{estimated_loss, liquidation_risk, action}`  
**Action:** Alert if liquidation risk HIGH, recommend closes  

```
Example:
  Input: 5 NO-side BTC range bets at entry 0.95
  Output: estimated_loss=$150, liquidation_risk=high
  → Recommend: "Close 2 riskiest positions before Friday"
```

---

### AGENT 3: Strategy Mutate
**Purpose:** Generate 3 variant strategies to test in parallel  
**Runs:** Daily  
**Input:** Best leg (windowshutrand), current edge  
**Output:** `{variant_1, variant_2, variant_3, expected_edges}`  
**Action:** Queue variants for parallel testing  

```
Example:
  Input: windowshutrand $0.19/trade
  Output:
    - Variant 1: Tighter ranges ($68k-$72k) → +$0.23/trade
    - Variant 2: Longer holds (no time stop) → +$0.21/trade
    - Variant 3: Vol fade hybrid → +$0.25/trade
  → Creates 3 new legs for parallel testing
```

---

### AGENT 4: Auto Prune
**Purpose:** Kill underperforming legs automatically  
**Runs:** Daily  
**Input:** All legs' stats (n trades, win rate, P&L)  
**Output:** `{kill_list, keep_list, rationale}`  
**Action:** Disable killed legs, reallocate capital  

```
Example:
  Input: newsmove (18.8% WR, n=30, -$3.34)
  Output: kill_list=[newsmove]
  → Disables newsmove, frees $1-2 capital
```

---

### AGENT 5: Hot Reload
**Purpose:** Decide if we should swap strategies mid-test  
**Runs:** Daily  
**Input:** Current strategy perf, candidate strategies  
**Output:** `{reload_now, timing, risk_level}`  
**Action:** Swap live (if risk_level=low) or wait  

```
Example:
  Input: scalp underperforming, IMDEA arb available
  Output: reload_now=false, timing=after_test, risk_level=high
  → Protects test integrity; swaps on 2026-07-30
```

---

### AGENT 6: GitHub Issues
**Purpose:** Auto-create GitHub issues on RED verdicts  
**Runs:** On FAIL verdicts (2026-07-30 or mid-test)  
**Input:** Failure context (edge, positions, reason)  
**Output:** Creates GitHub issue in edge-bots repo  
**Action:** Issue links test logs, suggests fixes  

```
Example:
  Input: FAIL — edge degraded from +$0.19 to -$0.05
  Output: Creates issue:
    Title: "[FAIL Day 4] Stop-disable hypothesis failed"
    Body: Logs, comparison, next steps
```

---

### AGENT 7: Dashboard Gen
**Purpose:** Generate shareable HTML dashboard from daily logs  
**Runs:** Daily  
**Input:** SEVEN_DAY_TEST_LOG.json  
**Output:** daily_report_YYYYMMDD.html  
**Action:** Push to GitHub, shareable link  

```
Example Output:
  dashboard.html shows:
  - Day 1-7 Sharpe trend chart
  - Open positions heatmap
  - Edge vs baseline graph
  - Verdicts timeline (Green→Yellow→Red)
```

---

### AGENT 8: Sharpe Tracker
**Purpose:** Compute day-over-day Sharpe improvement  
**Runs:** Daily  
**Input:** Daily edges, backtest baseline  
**Output:** `{day_sharpe, improvement_pct, trend}`  
**Action:** Log to results, watch for reversals  

```
Example:
  Day 1: Sharpe = $0.1900/trade (+19% vs baseline -$0.159)
  Day 2: Sharpe = $0.1750/trade (+10% vs baseline) ← declining
  → Trend: DECLINING (flag for Day 3 attention)
```

---

### AGENT 9: Telegram Bot
**Purpose:** Push daily verdicts to Telegram  
**Runs:** Daily after verdict  
**Input:** Daily verdict JSON  
**Output:** Sends Telegram message  
**Action:** You get push notification on phone  

```
Example:
  📊 Day 2/7: 🟡 YELLOW
  Edge: $0.1900 (✓ beating baseline)
  Positions: 12 open
  → Tap to see full logs on GitHub
```

---

### AGENT 10: A/B Test
**Purpose:** Split positions, test stops vs no-stops on subsets  
**Runs:** Daily (optional, requires config)  
**Input:** Current positions, randomizer seed  
**Output:** `{stops_on_positions, stops_off_positions}`  
**Action:** Run parallel experiment (proves causation)  

```
Example:
  Randomize 50% of 12 positions:
  - Group A (6 positions): stops DISABLED (hypothesis)
  - Group B (6 positions): stops ENABLED (control)
  → Compare Sharpe at end: isolates stop effect
```

---

## How They Work Together

```
09:00 UTC DAILY:

daily_test_check_ollama.py
├─ Collect metrics
├─ Ollama verdict
├─ GitHub sync
│
└─ Run ollama agents:
   ├─ Agent 1 (Alert Monitor)
   │  └─ Decides if Slack alert needed
   ├─ Agent 2 (Stress Test)
   │  └─ Warns on liquidation risk
   ├─ Agent 3 (Strategy Mutate)
   │  └─ Queues 3 variants for testing
   ├─ Agent 4 (Auto Prune)
   │  └─ Kills underperformers
   ├─ Agent 5 (Hot Reload)
   │  └─ Decides if strategy swap is safe
   ├─ Agent 6 (GitHub Issues)
   │  └─ Creates issue on failures
   ├─ Agent 7 (Dashboard Gen)
   │  └─ Generates HTML report
   ├─ Agent 8 (Sharpe Tracker)
   │  └─ Logs improvement trend
   ├─ Agent 9 (Telegram Bot)
   │  └─ Sends verdict to phone
   └─ Agent 10 (A/B Test)
      └─ Runs parallel control experiment
```

---

## Running Agents Manually

```bash
# Run single agent
python3 ollama_agents.py --alert-monitor
python3 ollama_agents.py --stress-test
python3 ollama_agents.py --strategy-mutate
python3 ollama_agents.py --auto-prune
python3 ollama_agents.py --hot-reload
python3 ollama_agents.py --github-issues
python3 ollama_agents.py --dashboard-gen
python3 ollama_agents.py --sharpe-tracker
python3 ollama_agents.py --telegram-bot
python3 ollama_agents.py --a-b-test

# Run all 10
python3 ollama_agents.py --all
```

---

## Integration Status

| Agent | Status | Auto-Run | Cost |
|-------|--------|----------|------|
| 1 Alert Monitor | ✅ Implemented | Daily 09:00 | $0 |
| 2 Stress Test | ✅ Implemented | Daily 09:00 | $0 |
| 3 Strategy Mutate | ✅ Implemented | Daily 09:00 | $0 |
| 4 Auto Prune | ✅ Implemented | Daily 09:00 | $0 |
| 5 Hot Reload | ✅ Implemented | Daily 09:00 | $0 |
| 6 GitHub Issues | ✅ Implemented | On FAIL | $0 |
| 7 Dashboard Gen | ✅ Implemented | Daily 09:00 | $0 |
| 8 Sharpe Tracker | ✅ Implemented | Daily 09:00 | $0 |
| 9 Telegram Bot | ✅ Implemented | Daily 09:00 | $0 |
| 10 A/B Test | ✅ Implemented | Optional | $0 |

---

## What This Means

- **Autonomous:** No human intervention needed (ollama decides everything)
- **Zero cost:** All agents run on local llama3.1:8b
- **Self-improving:** Agents mutate strategies, prune bad ones, reload winners
- **Real-time:** Alerts before disasters (stress test, liquidation)
- **Documented:** GitHub issues + HTML dashboards + Telegram summaries
- **Scientific:** A/B test isolates stop-loss effect (not just observation)

---

## Next: Full Automation Pipeline

```
2026-07-24 09:00 UTC: Agent suite v1 launches
2026-07-25 09:00 UTC: Daily loops begin
2026-07-30 22:30 UTC: Final verdict (+ GitHub issue created)

All 10 agents run autonomously, zero human input needed.
```

---

**Status: 10 Ollama Agents Ready. Zero-Cost Autonomy Deployed.**
