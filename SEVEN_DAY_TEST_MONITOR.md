# 7-Day Stop-Loss Test: Monitoring & Decision Framework
**Started:** 2026-07-23 22:33  
**Ends:** 2026-07-30 22:33  
**Hypothesis:** Removing stops allows longer holds → improves Sharpe + edge capture

---

## TEST STATE (Snapshot: 2026-07-23 23:50)

### Current Portfolio
| Metric | Value |
|--------|-------|
| **Open positions** | 12 |
| **Active legs** | windowshutrand (5), newsmove (3), nearterm (2), others (2) |
| **Backtest baseline** | -$369.43 PnL (31% WR, 4,548 trades) |
| **Stop-loss status** | DISABLED (all parameters = 1.0) |
| **Backup** | scalp_lab.py.backup.1784830289 |

### Key Positions to Monitor

#### High Activity (Last 48h)
- **windowshutrand** (3 new): BTC range bets, all NO side, ages 10-16h
- **newsmove** (2 new): LeBron (YES/NO), Ballon d'Or (NO), ages 13-42h

#### Stale/High Hold Time
- **nearterm** (2): Trump Iran sanctions (952 days), Claude 5 release (1080h) — likely resolved or should close soon
- **windowshutrand** (2): Old June positions, 588h+ age

#### Resolved Winners Ready to Close
1. **allin** | Knicks vs Spurs YES | +$1.6667 (Closed 2026-06-05)
2. **coinflip** | LoL Karmine Corp vs GIANTX YES | +$1.0833 (Closed 2026-06-03)
3. **momentum** | Seoul Mayoral Election YES | +$0.8709 (Closed 2026-06-05)

#### Recent Resolved (Last 7 days, already closed)
- **nearres** | Counter-Strike Aurora vs FOKUS NO | +$0.1749 (Closed 2026-07-23)
- **nearres** | LoL T1 vs Gen.G NO | +$0.2588 (Closed 2026-07-19)

**Total capital to free up: $4.05**

---

## MONITORING CHECKLIST (Until 2026-07-30)

### Daily (09:00 UTC)
- [ ] **Sharpe check**: `python3 ml/backtest_edge.py` → compare live Sharpe vs backtest (baseline -0.159 allin control)
- [ ] **Open P&L**: Track unrealized P&L across 12 positions (watch for early exits still happening despite stops disabled)
- [ ] **Position ages**: Flag any position hitting 168h+ hold without resolution
- [ ] **Resolution check**: Scan for markets that should have closed

### Event-Driven (Market Volatility)
- [ ] **Iran escalation**: Monitor `analyst_candidates.json` for geopolitical data arbs (PortWatch chokepoint traffic)
- [ ] **LeBron announcement**: Track sports news for immediate reprices (affects 2 newsmove positions)
- [ ] **Fed decisions**: Watch for surprise rate announcements affecting BTC range expectations
- [ ] **BTC price action**: Monitor windowshutrand positions if BTC approaches range thresholds ($70k, $72.5k, $75k, $57.5k, $55k)

### Alerts to Set
- [ ] Position unrealized loss > -$50 (unexpected drawdown without stops)
- [ ] Any position without clear stop fires a resolve (data error)
- [ ] Market resolved but position still open (close stale)

---

## DECISION FRAMEWORK (2026-07-30)

### Step 1: Collect Live Data
```bash
# Run on 2026-07-30 22:00
python3 ml/backtest_edge.py
python3 ml/backtest_oot.py
```

### Step 2: Compare Metrics
| Metric | Backtest (Baseline) | 7-Day Live | Status |
|--------|-------------------|-----------|--------|
| **Sharpe (allin control)** | -0.159 per trade | TBD | ✓ if > -0.15 |
| **Win rate** | 31% (4,548 trades) | TBD | ✓ if > 32% |
| **Total PnL** | -$369.43 | TBD | ✓ if < losses |
| **Stop-bleed (avg $/trade)** | -$0.308 | TBD | ✓ if stopped bleeding |
| **Mean hold time** | TBD | TBD | ✓ if longer |

### Step 3: Hypothesis Validation
- **PASS**: Live Sharpe > backtest baseline (-0.159) AND stops saved >$50
  - **Action**: Restore stops, retire newsmove/others, allocate to Fed basket
  
- **FAIL**: Live Sharpe ≤ backtest, stops-disabled didn't improve edge
  - **Action**: Restore stops immediately, investigate root cause, redesign signal
  
- **NEUTRAL**: Mixed results (Sharpe flat, but fewer early exits)
  - **Action**: Extend test by 7 more days, monitor refined hypothesis

### Step 4: Capital Allocation Decision

#### If PASS (hypothesis validated)
1. **Close 5 resolved winners** → **Free up $4.05 capital**
   - allin Knicks: +$1.6667
   - coinflip LoL Karmine: +$1.0833
   - momentum Seoul Election: +$0.8709
   - nearres Aurora CS: +$0.1749
   - nearres T1 Gen.G: +$0.2588
   
2. **Add Fed basket** (5 legs, 0.95% edge):
   - Basket depth, structure TBD
   - Allocation: $4+ from freed capital + new paper allocation
   
3. **Retire toxic strategies**:
   - newsmove (18.8% WR, worst performer) — has 3 open positions, close after stop-loss re-enable
   - coinflip (-$66 total, already used capital on closed winner)
   - microscalp (0% WR)
   - allin (now serves as control only, close winner on 2026-07-30)
   
4. **Tighten stops** on nearterm once re-enabled
5. **Reallocate capital** to windowshutrand + structural arb legs + Fed basket

#### If FAIL
1. **Restore stops at original values** (from backup)
2. **Root-cause investigation**:
   - Are long holds capturing more delta or just drawdown?
   - Is stop-bleed the real culprit, or are the signals themselves toxic?
   - Run `scalp_lab.py --analyze-exits` to classify exit reasons
3. **Redesign**: Focus on signal quality (entry), not hold duration

---

## RESOLVED WINNERS (Reconciliation Needed)

**Current state shows:** 0 resolved ready to close  
**User brief mentioned:** 3 resolved winners  
**Action:** User to confirm which positions are marked as resolved

Once identified:
- [ ] Close position and realize P&L
- [ ] Log market slug + exit reason + final P&L
- [ ] Update allocation plan

---

## STOP-LOSS RE-ENABLE (2026-07-30 22:33)

### Restore Procedure
```bash
# 1. Restore from backup
cp scalp_lab.py.backup.1784830289 scalp_lab.py

# 2. Verify stops are back
grep "_stop" scalp_engine_config.json | grep -v "1.0"

# 3. Restart watchdog
launchctl kickstart -k system/com.aryan.scalp-watchdog

# 4. Confirm live config
curl http://localhost:8077/scalp/config | jq '.stop_loss'
```

### Parameters to Re-Enable
- `fade_stop`: 0.08
- `fastfade_stop`: 0.06
- `scalp_stop`: 0.03
- `wf_stop`: 0.08
- All other *_stop values from original config

---

## KEY LEARNINGS TO CAPTURE

By end of test, document:
1. **Stop-bleed diagnosis**: Was the -$0.308/trade loss real or signal quality?
2. **Hold-time effect**: Did longer holds capture more alpha or just absorb more noise?
3. **Win-rate hypothesis**: Does removing stops increase WR or just reduce realized losses?
4. **Sharpe improvement**: Is the 7-day Sharpe actually better, or just noise?

---

## APPENDIX: Positions to Monitor Real-Time

### windowshutrand (BTC Range)
- 5 positions open, all NO (bearish BTC)
- Entry prices: 0.853–0.964 (short odds)
- Key levels: $55k, $57.5k, $60k, $70k, $72.5k, $75k

### newsmove (Sports/News)
- 3 positions: LeBron YES, LeBron NO, Ballon d'Or NO
- Watch for announcement that reprices

### nearterm (Older, Likely Stale)
- Trump Iran sanctions: 951 days old — likely resolved
- Claude 5 release: 1080h old — likely expired

---

## NEXT STEPS

1. **Reconcile resolved winners** (user input)
2. **Confirm no stops are firing** despite disable (log check each day)
3. **Set daily Sharpe check** (automate if possible)
4. **Alert on market resolution** for open positions
5. **Prepare Fed basket spec** for post-test allocation
