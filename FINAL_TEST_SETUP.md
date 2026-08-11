# 7-Day Stop-Loss Test — Final Setup (Complete)

**Status:** ✅ READY TO LAUNCH  
**Test Start:** 2026-07-23 22:33  
**Test End:** 2026-07-30 22:33  
**Monitoring:** Autonomous (ollama + GitHub bot benchmarks)

---

## System Components (All Active)

### 1. **Core Test Infrastructure**
- ✅ Stop-loss disabled (all *_stop = 1.0)
- ✅ Backup created: scalp_lab.py.backup.1784830289
- ✅ 12 open positions monitored (windowshutrand ×5, newsmove ×3, nearterm ×2, others ×2)
- ✅ 5 resolved winners identified & ready to close (+$4.05 freed)

### 2. **Daily Autonomous Monitoring**
- ✅ Script: `daily_test_check_ollama.py`
- ✅ Judge: Local ollama (llama3.1:8b)
- ✅ Frequency: 09:00 UTC daily (auto-run via launchd)
- ✅ Output: `SEVEN_DAY_TEST_LOG.json` (append-only, timestamped)
- ✅ Logs: `/tmp/daily-test-check.log` (launchd output)

### 3. **GitHub Bot Benchmarking** (NEW)
- ✅ Top 5 GitHub bots analyzed:
  1. **IMDEA Arbitrage** — $39.59M proven extraction (academic research)
  2. **warproxxx/poly-maker** — Market-making spreads (1.1k stars)
  3. **ent0n29/polybot** — Multi-service execution (Java/ClickHouse)
  4. **Benjam1nCup/V2** — Copy trading + farming
  5. **skharchikov** — ML ensemble + Bayesian voting

- ✅ Benchmark document: `GITHUB_BOTS_BENCHMARK.md`
- ✅ Control strategies identified for daily comparison

### 4. **Decision Framework** (2026-07-30)
- ✅ Script: `test_decision_2026_07_30.py` (runs 22:30 UTC)
- ✅ Output: `TEST_VERDICT.json` (PASS / NEUTRAL / FAIL)
- ✅ Verdict includes: edge vs baseline, capital allocation recommendation

### 5. **Capital Management**
- ✅ Resolved winners to close: +$4.05
- ✅ Allocation plan: Fed basket (5 legs, 0.95% edge)
- ✅ Toxic strategies to retire: newsmove (18.8% WR), coinflip (-$66), microscalp (0% WR)

---

## Daily Monitoring Strategy

### Automated (09:00 UTC)
```bash
python3 daily_test_check_ollama.py
# Output format:
# - Day N/7 metrics collected
# - Edge per-trade vs -$0.159 baseline
# - Ollama colored verdict (🟢 Green / 🟡 Yellow / 🔴 Red)
# - Logged to SEVEN_DAY_TEST_LOG.json
```

### What Ollama Watches
1. **Your edge:** Does $0.1900/trade persist or improve?
2. **Baseline comparison:** vs -$0.159 (allin control, worst case)
3. **GitHub bots:** vs IMDEA ($39.59M), warproxxx (MM), ent0n29 (execution cadence)
4. **Hypothesis signals:** Are long holds capturing more delta or just drawdown?
5. **Red flags:** Trade volume drop, unrealized P&L spike, position aging >168h

### Manual Checks (As Needed)
- Iran escalation (data-arb trigger) → check PortWatch chokepoint traffic
- LeBron announcement (sports reprices) → affects 2 newsmove positions
- Fed rate decision → affects BTC range expectations
- BTC price action → monitor windowshutrand thresholds ($55k-$75k)

---

## Backtest Baseline (For 2026-07-30 Comparison)

| Metric | Baseline (4,548 trades) |
|--------|------------------------|
| Total P&L | -$369.43 |
| Win rate | 31% |
| Sharpe (allin control) | -0.159/trade |
| Stop-bleed loss | -$446 (-$0.308/trade × 1,449) |
| Toxic strategies | allin (-$117), coinflip (-$66), microscalp (0% WR) |

**Hypothesis:** Removing stops allows positions to run 20-50% longer → captures larger deltas → improves Sharpe above -0.159 baseline

---

## Decision Rules (2026-07-30 22:30)

### PASS (Deploy stops-disabled permanently)
- ✅ Live Sharpe > -$0.159/trade
- ✅ Win rate improved >1pp (>32%)
- ✅ Stop-bleed saved >$50 total
- ✅ No excessive drawdown (unrealized P&L < -$100)

**Action:** Close winners, add Fed basket, retire toxics, reallocate capital

### NEUTRAL (Extend test 7 more days)
- Mixed results (edge good, volume low; OR Sharpe flat, but fewer early exits)
- Need more data to disambiguate

**Action:** Keep disable, collect 7 more days, run decision again on 2026-08-06

### FAIL (Restore stops immediately)
- Live Sharpe ≤ -$0.159/trade
- Win rate degraded <30%
- No evidence stops are the bottleneck

**Action:** Restore from backup, root-cause (signal quality? execution cadence?), redesign

---

## Files Deployed

| File | Purpose |
|------|---------|
| `daily_test_check_ollama.py` | Daily metrics + ollama verdict |
| `SEVEN_DAY_TEST_MONITOR.md` | Manual monitoring checklist |
| `GITHUB_BOTS_BENCHMARK.md` | Competitor strategy comparison |
| `test_decision_2026_07_30.py` | Automated verdict + allocation script |
| `CLOSE_RESOLVED_WINNERS.md` | Closure procedure for 5 winners |
| `STOP_LOSS_DISABLED.txt` | Test metadata + re-enable instructions |
| `com.aryan.daily-test-check.plist` | Launchd job (09:00 UTC daily) |

---

## Critical Paths (Do Not Miss)

- ❌ **Do NOT enable stops during test** (test corrupted)
- ❌ **Do NOT manually close positions** except resolved winners (test contaminated)
- ❌ **Do NOT change scalp_engine_config.json stops** (restore from backup on 2026-07-30 only)
- ✅ **DO run daily_test_check.py** (or trust launchd, but verify with manual run)
- ✅ **DO monitor unrealized P&L** (watch for unexpected drawdowns)
- ✅ **DO note any market closures** (resolved markets break the test)

---

## Launch Checklist

- [x] Stop-loss confirmed disabled (STOP_LOSS_DISABLED.txt created)
- [x] Backup created and verified (scalp_lab.py.backup.1784830289)
- [x] 12 open positions confirmed
- [x] 5 resolved winners identified (+$4.05 total)
- [x] Daily check script tested and working
- [x] Launchd job loaded (com.aryan.daily-test-check)
- [x] Ollama analysis integrated
- [x] GitHub bot benchmarks added
- [x] Decision script prepared
- [x] Capital allocation plan ready

**Status:** ✅ READY FOR LIVE TEST

---

## Next Step

**Run the daily test now (optional manual run to verify it works):**
```bash
cd ~/Documents/polymarket
python3 daily_test_check_ollama.py
```

Then let the launchd job run automatically at 09:00 UTC daily until 2026-07-30.

**Questions?** Check `SEVEN_DAY_TEST_MONITOR.md` for the full monitoring guide.
