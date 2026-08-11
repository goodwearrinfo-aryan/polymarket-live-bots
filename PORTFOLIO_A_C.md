# A+C Hybrid: Analyst + Control Portfolio

**Deployed:** 2026-06-15 01:15 UTC  
**Total Capital:** $10,000 USDC (paper only)

---

## Architecture

### ANALYST TRACK (50% = $5,000)

**bot_analyst** — Manual research + 3-lens panel gate
- Entry: Human researches market → writes thesis → panel evaluates (3 lenses) → if survived → deploy
- Capital per bet: $500–$1,000 (max 20% of $5k)
- Exit: Resolution or -40% hard stop
- Tracking: scorecard.py (Brier calibration + resolution)
- Panel gate: judge_panel.py (correctness, base-rate, security)
- Expected: 3–5 active bets by 2026-06-30

**Example Bet (Israel-Hezbollah):**
```
Market:     Israel-Hezbollah conflict by 2026-12-31?
Entry:      NO @ 0.86
Conviction: 65%
Thesis:     "Regional stability prevails. Both sides face high escalation costs."
Panel:      ✅ All 3 lenses survived refutation
Status:     ⏳ Awaiting manual deployment to analyst.json
```

---

### CONTROL TRACK (50% = $5,000) — Validate that algos lose

**nearres** — $2,500 (25%) | CONTROL MODE
- Original: 90% WR, +$0.0271/trade (backtest)
- Live: EXPECT FAILURE (gap-through stops fill -45¢, not -3¢)
- Purpose: Confirm backtest artifact → honest stops = negative P&L
- Status: ✅ Running, launchctl com.aryan.bot-nearres

**ladderarb** — $2,500 (25%) | CONTROL MODE
- Original: 82% WR, +$0.3731/trade (n=11 lucky sample)
- Live: EXPECT FAILURE (DSR unknown, likely negative)
- Purpose: Prove n=11 is dust when extrapolated to n=30
- Status: ✅ Running, launchctl com.aryan.bot-ladderarb

**Retired (Unloaded):**
- fade (losing despite 67% WR)
- fastfade (investigation mode, expected -$0.0772/trade)
- newstrategy (design phase, 0 trades)

---

## System Components

### 1. Scorecard (scorecard.py)
Tracks resolution outcomes + Brier calibration
```python
scorecard.log_entry(market_id, thesis, conviction)  # Log bet
scorecard.add_panel_verdict(market_id, lens, survived)  # Add judge verdict
scorecard.resolve(market_id, resolution_price, YES_or_NO)  # Mark resolved
scorecard.calibration_report()  # Brier score by conviction band
```

### 2. Judge Panel (judge_panel.py)
3-lens adversarial refutation gate
```
LENS 1: Correctness  — Is the logic sound?
LENS 2: Base-rate    — Does conviction match category history?
LENS 3: Security     — Can insider/whale reverse this?

Bet approved ONLY if all judges fail to refute (verdict=True for all 3)
```

### 3. Analyst Bot (bot_analyst.py)
Position management for analyst bets
```python
bot_analyst.add_analyst_bet(market_id, thesis, conviction)  # Manual entry
bot_analyst.run(markets)  # Check exits (resolution or -40% stop)
```

### 4. Health Monitor (bot_portfolio_health.py)
Watches all bots, alerts on silence/losses
- Checks every 15 min
- Detects: silent bots (>1h no log update), losses (>-15% capital)
- Alerts: iMessage to krisharyan@icloud.com + +918449447444

---

## Execution Flow

### Week 1 (2026-06-15 to 2026-06-21)
1. **Analyst researches** 3–5 high-conviction markets
2. **Write theses** (500+ words each, addressing counterarguments)
3. **Submit to panel** (judge_panel.py evaluates 3 lenses)
4. **If survived:** log to scorecard.py + deploy to analyst.json
5. **If refuted:** reject, research next candidate
6. **Monitor controls:** nearres, ladderarb expect negative trajectory

### Week 2 (2026-06-22 to 2026-06-30)
1. **Analyst bets live:** track market prices daily
2. **Panel verdicts** added to scorecard as time passes
3. **First exits**: some bets may resolve or hit -40% stop
4. **Compute Brier:** is conviction well-calibrated?
5. **Control status:** confirm nearres/ladderarb losses

### 2026-06-30 DECISION POINT
- **If analyst Brier <0.25 + WR ≥60%:** Scale analyst to $7k
- **If control losses confirmed:** Retire nearres/ladderarb, redeploy capital
- **Decision:** Analyst-primary (70%) or continue hybrid (50/50)

---

## Key Metrics

| Metric | Target | Monitor | Status |
|--------|--------|---------|--------|
| **Analyst Brier** | <0.25 | scorecard.calibration_report() | Pending (need 3+ resolved) |
| **Analyst WR** | ≥60% | analyst.json wr field | Pending |
| **Analyst Conviction** | 60–70% avg | scorecard.active[].conviction | Pending |
| **Panel Survival** | 60%+ of theses | scorecard.refuted.count vs active.count | Pending |
| **Nearres P&L** | <-$500 | nearres.json cumulative_pnl | +$0.00 (monitoring) |
| **Ladderarb P&L** | <-$300 | ladderarb.json cumulative_pnl | +$0.00 (monitoring) |

---

## Why This Works

1. **Analyst beats algos on efficiency frontier.** 
   - Algos: fit to 95% of price, miss 5% tail events
   - Analyst: hunt the 5% (Israel escalation, regulatory shock, black-swan)
   - Expected: analyst 15–20% ROI vs algos -10% ROI

2. **Panel gate prevents overconfidence.**
   - Solo analyst: prone to confirmation bias
   - 3-lens judges: force clarity on assumptions
   - Brier score: measure if well-calibrated

3. **Controls validate the null.**
   - If nearres/ladderarb go negative at scale: confirms backtest fiction
   - Then retire with confidence (not FOMO about "maybe it'll work")

4. **Capital concentration.**
   - Analyst: $5k on 3–5 high-conviction bets (@$1–1.5k each)
   - Controls: $5k on 2 proven failures (validation)
   - vs. $10k spread over 5 unproven algos

---

## File Manifest

```
~/Documents/polymarket/
├── PORTFOLIO_A_C.md           ← This file
├── ANALYST_HYBRID.md          ← Architecture deep-dive
├── scorecard.py               ← Resolution tracking + Brier
├── judge_panel.py             ← 3-lens refutation gate
├── bot_analyst.py             ← Position manager
├── bot_nearres.py             ← Control 1 (expect -30% by 2026-07-15)
├── bot_ladderarb.py           ← Control 2 (expect -30% by 2026-07-15)
├── analyst.json               ← State (empty, manual entries only)
├── scorecard.json             ← Active/resolved/refuted bets
├── nearres.json               ← Control 1 state
├── ladderarb.json             ← Control 2 state
├── bot_analyst.log            ← Exit checks (every 5 min)
├── bot_nearres.log            ← Control 1 log
├── bot_ladderarb.log          ← Control 2 log
└── bot_portfolio_health.log   ← Health monitor alerts
```

```
~/Library/LaunchAgents/
├── com.aryan.bot-analyst.plist       ← Analyst (every 5 min)
├── com.aryan.bot-nearres.plist       ← Control 1 (every 60s)
├── com.aryan.bot-ladderarb.plist     ← Control 2 (every 60s)
└── com.aryan.bot-health-check.plist  ← Monitor (every 15 min)
```

---

## How to Add a Bet

```bash
# 1. Research the market
#    (read news, model scenarios, write 500+ word thesis)

# 2. Test thesis against panel
python3 judge_panel.py
# (Copy-paste thesis into the code, run)

# 3. If survived (all 3 lenses), deploy
python3 scorecard.py log market_id
python3 scorecard.py panel market_id correctness True
python3 scorecard.py panel market_id base_rate True
python3 scorecard.py panel market_id security True

# 4. Add position to analyst portfolio
python3 bot_analyst.py add_israel  # (example)

# 5. Monitor
tail -f analyst.json
tail -f bot_analyst.log
```

---

## Success Criteria (by 2026-06-30)

✅ **Analyst delivers:**
- 3–5 bets logged to scorecard
- 60%+ survive 3-lens panel
- Brier <0.25 (well-calibrated predictions)
- First resolutions tracked (calibration report)

✅ **Controls confirm thesis:**
- nearres P&L <-$500 (gap-through stops confirmed)
- ladderarb P&L <-$300 (sample size curse confirmed)
- Both show WR holding but $/trade negative

✅ **System operational:**
- Health checks running (every 15 min)
- iMessage alerts working
- Logs flowing cleanly

---

**Status: LIVE**

Analyst bot deployed. Scorecard tracking. Judge panel ready. Controls running as validation. First scoreboard 2026-06-30.

Next: Research Israel-Hezbollah, crypto regulatory, and 1–2 macro bets for analyst portfolio.
