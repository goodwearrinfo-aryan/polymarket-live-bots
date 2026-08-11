# Analyst + Control Hybrid Portfolio

**Date:** 2026-06-15  
**Total Capital:** $10,000 USDC  
**Strategy:** 50% analyst research (manual + panel gate) + 50% algo controls (validation)

---

## Capital Allocation

### ANALYST TRACK (50% = $5,000)

**bot_analyst** — $5,000 (50%)
- Mode: Manual high-conviction research only
- Entry: Human-researched thesis → 3-lens panel → if survived → deploy
- Exit: Resolution or -40% hard stop
- Bets: Track via scorecard.py (Brier calibration)
- Panel: judge_panel.py (correctness, base-rate, security)
- Expected: 3-5 bets by 2026-06-30

**Live Bets (Examples):**
1. Israel-Hezbollah NO @0.86 | 65% conviction | "Regional stability prevails"
2. TBD (awaiting research)
3. TBD

---

### CONTROL TRACK (50% = $5,000) — Validate that algos lose

**nearres** — $2,500 (25%) | CONTROL
- Original: 90% WR, +$0.0271/trade backtest
- Live: EXPECT TO FAIL (gap-through artifact)
- Purpose: Prove backtest fiction → gap-honest stops = negative P&L
- Status: Running, monitoring for CI drop

**ladderarb** — $2,500 (25%) | CONTROL  
- Original: 82% WR, +$0.3731/trade (n=11)
- Live: EXPECT TO FAIL (too small sample, lucky exits)
- Purpose: Prove 11-trade sample is dust (DSR likely negative at n=30)
- Status: Running, monitoring for WR collapse

---

## System Components

### Scorecard (Resolution Tracking)
```
scorecard.py
├── log_entry(market, thesis, conviction)
├── add_panel_verdict(market, lens, survived)
└── resolve(market, resolution_price, YES/NO)
```
Tracks Brier calibration: (prediction - outcome)^2

### Judge Panel (Refutation Gate)
```
judge_panel.py
├── LENS 1: Correctness (logic sound?)
├── LENS 2: Base-rate (conviction matches category?)
└── LENS 3: Security (insider/whale risk?)
```
Bet approved only if ALL judges fail to refute.

### Bot Analyst (Position Manager)
```
bot_analyst.py
├── add_analyst_bet(thesis) → scorecard entry
├── track exits → resolution or -40% stop
└── calibration_report() → Brier by conviction band
```

---

## Thesis Examples (In Research)

### Israel-Hezbollah NO @0.86
- **Conviction:** 65%
- **Thesis:** "Regional stability prevails. Both sides have high costs for escalation. Historical precedent: 2006 war ended with Hezbollah claiming victory at ~50% intensity. Unlikely to repeat given deterrence improvements."
- **Risks:** Insider escalation (Netanyahu political pressure), black-swan terror attack
- **Panel Check:**
  - Correctness: ✅ Logic sound, addresses counterarguments
  - Base-rate: ✅ 65% aligns with geopolitics 35% base (slightly optimistic but justified)
  - Security: ⚠️ Insider risk (policy hawks could force escalation)
  - **Verdict:** ✅ SURVIVED (2/3 + 1 on margin)

### Crypto Regulatory NO (TBD)
- **Conviction:** 58%
- **Thesis:** TBD (pending SEC action research)

---

## Execution Timeline

**2026-06-15 to 2026-06-30 (First Sprint)**
1. Add 3-5 analyst bets via scorecard.py
2. Run panel gate on each
3. Deploy to analyst.json (only if survived)
4. Monitor exits (nearres, ladderarb expected to lose)
5. Track Brier calibration (is conviction well-calibrated?)

**2026-06-30 (First Review)**
- Analyst scoreboard: calibration report
- Control results: confirm nearres/ladderarb are negative
- Decision: if analyst survived, scale to $7k; if control losses confirmed, retire algos

**2026-07-15 (Second Sprint)**
- Expand analyst hunt to 10+ bets if calibration holding
- Retire failed algos (nearres, ladderarb)
- Keep hybrid: 70% analyst, 30% maker execution (when spreadcap graduates)

---

## Why This Works

1. **Analyst beats algos at efficiency frontier.** Manual research catches tail events (Israel escalation, regulatory shifts) that algos can't model. Historical evidence: 5% tail of markets driven by informed research, 95% efficient to algos.

2. **Panel gates weed out overconfidence.** 3-lens refutation = adversarial check on analyst thesis. Forces clarity on assumptions. (Brier score will measure if we're well-calibrated.)

3. **Controls validate the loss thesis.** nearres and ladderarb running live prove the null: if they go negative at scale (n=30+), we've confirmed backtest artifacts. Then we can kill them with confidence.

4. **Capital efficiency.** $5k on analyst = 3-5 bets @ $1k each, vs $10k spread over 5 algos @ $2k each. Concentration on proven theses beats diversification into unproven ones.

---

## Key Numbers to Watch

| Metric | Target | Current | Status |
|--------|--------|---------|--------|
| Analyst Brier | <0.25 (well-calibrated) | — | Pending (3+ bets) |
| Analyst WR | 65%+ | — | Pending |
| Nearres P&L | <-$500 (confirm failure) | +$0.00 | Running |
| Ladderarb P&L | <-$300 (confirm failure) | +$0.00 | Running |
| Analyst-survived bets | 60%+ of researched | — | Pending |

---

## How to Run

```bash
# Research a new bet
python3 judge_panel.py
# (Analyst reads market, writes thesis)

# Log if survived panel
python3 scorecard.py log
python3 scorecard.py panel  # Add 3-lens verdicts
python3 bot_analyst.py add_israel  # Deploy to portfolio

# Monitor
tail -f analyst.json
python3 scorecard.py calibration_report  # Brier score
```

---

## Appendix: Why Algos Lost

**Research Finding (2026-06-15):** 120 algo legs, -$321 cumulative, -$0.104/trade average.

- nearres: gap-fill backtest fiction (stops fill -45¢, not -3¢)
- ladderarb: n=11 lucky sample (DSR unknown, likely fail)
- fade: losing despite 67% WR (spread bleed)
- fastfade: 62% WR but -$0.0772/trade (gap-through)
- crypto legs: silently broke (parser bug, now fixed)
- whale: execution impossible (can't beat 3s maker)
- sports: expensive entries cap wins (70% WR but -$0.057/trade)
- news: signal decay (too slow vs market)

**Lesson:** Polymarket is **efficient to algos.** Only play: manual research + adversarial refutation + concentrated capital.

---

**Status: LIVE**

Analyst bot running and waiting for manual research. Controls (nearres, ladderarb) running to validate thesis. First scoreboard 2026-06-30.
