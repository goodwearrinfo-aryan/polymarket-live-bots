# BOT BRAIN — Inference Pipeline & Edge Validation

## Mission
Discover ONE statistically significant edge (≥30 exits, CI>0, DSR, controls negative) before live trading.
Paper-only validation → proved edges only → live with risk limits.

---

## System Architecture

### Tier 1: Signal Generation (Legs)
**33 independent trading legs** feed raw signals:
- **Esports favorites** (nearres, nearrestitle, psconfirm, sportres)
- **Crypto divergence** (diverg, feargreed, lateprox, coinup, coindown, coinrev)
- **Technical signals** (candlesig, macdsig, windowshut)
- **Macro/attention** (newsno, noevent, btc15no, weatherno, ytbuzz, etc.)

Each leg = independent hypothesis (entry criteria + exit rules + position sizing).
**State**: scalp_lab_state.json (Postgres mirror) + leg-specific `.json` files.

---

### Tier 2: Entry Conviction (Belief Ledger)
**Append-only forecast record** at entry-open time:
```python
belief_ledger.append_forecast(
    pos=position_object,
    leg='diverg',
    entry_fill=0.42,      # actual fill
    prob=0.58,            # de-vigged belief (entry_fill is implicit market price; prob is "real" odds)
    market_id='0x...'
)
```

**Per-leg calibration** (run anytime):
```
DIVERG: n=127, Brier=0.0847, gap=-0.012 ✅ WELL-CALIBRATED
  Implied 0.520 | Realized 0.508 | decile mismatch <5%
```

**What this proves:**
- Entry probabilities are honest (match realized outcomes)
- No entry-fill padding or selection bias
- Decile reliability = forecaster is not overconfident

---

### Tier 3: Exit Honesty (CLOB Pricing)
**No exit censoring** — all positions priced:
- Open positions: fetch_price_clob() from clob.polymarket.com (live trades)
- Resolved positions: CLOB price at resolution time (not Gamma, which vanishes)
- Gap-through modeling: stops book at (mid − half_spread), not trigger price

**Gap-through rule** (Lesson 13, paid for):
```
Esports favorite gaps 0.93 → ~0 in one tick.
Live stop at 0.90 triggers → fills at realized bar mid (−45–65¢), NOT −3¢.
Backtest must model this or CI is fiction.
```

---

### Tier 4: Edge Validation Gate
**3-lens adversarial panel** (analyst_gate.py):

Each hypothesis survives only if ≥2/3 lenses fail to refute:
1. **Correctness lens**: Does the signal actually predict YES/NO? (market accuracy)
2. **Survivorship lens**: Do closed positions (n≥30) show edge, or just open-window luck?
3. **Execution lens**: Can we fill at entry probability + collect stop (realized gap-through)?

**Decision criteria** (conservative):
```
PASS (≥2/3 lenses fail to refute):
  - n ≥ 30 closed positions
  - bootstrap CI > 0 (median profit > 0)
  - DSR ≥ 1.0 (Deflated Sharpe > noise threshold)
  - win_rate > 52% (Brier gap < 0.05)
  - Controls (allin, coinflip, coindown) n≥10 each, all WR≤50%

FAIL (signal dies):
  - Any single lens refutes (e.g., survivorship shows edge only in open window)
  - CI includes 0 or DSR<0
  - Controls win > 50%
```

---

### Tier 5: Position Sizing & Risk
**Per-edge allocation** (when edge passes gate):
```python
# After n=30 + CI>0 + DSR>0:
risk_per_edge = $50 / CI_width  # scaled by confidence interval width
max_open_per_edge = 30 positions
max_size_per_pos = $2-5 (set at entry, fixed at exit)
stop_loss = −3¢ (realized fill on gap)
target = +9¢ (ride to settlement preferred, but cap at realized fill)
```

**Portfolio-level**:
```
Total paper capital = unlimited (paper-only)
Concurrent positions = 45 legs × avg 5 open = ~225 positions
Drawdown limit = −$500 before review (triggers BRAIN update)
```

---

### Tier 6: Live Monitoring & Feedback
**Real-time metrics** (belief_calibration.md, obsidian_snapshot every 6h):

Per leg:
- **n** (exits) — progress toward n≥30 gate
- **Brier** — forecast accuracy
- **calibration_gap** — overconfidence signal
- **realized_win_rate** — actual outcomes
- **DSR** — Deflated Sharpe (accounts for multiple-comparison bias)
- **CI [lo, hi]** — bootstrap confidence interval on median $/exit

Portfolio:
- **Total P&L** — dollars, not %, optimize this
- **Open/Closed ratio** — position lifecycle
- **Control leg WR** — sanity check (must lose)

---

## Decision Flows

### Signal → Entry
```
1. Leg scans market for entry condition (e.g., diverg: spot vol 24h > mkt 10%)
2. Position opens at limit order (market price)
3. belief_ledger.append_forecast(pos, prob=de_vigged_entry_fill)
4. Add to scalp_lab_state.json["leg_name"]["open"]
```

### Open → Exit
```
1. Every cycle: scan exits (target hit, stop hit, resolution)
2. CLOB-price all open positions (no censoring)
3. belief_ledger.append_settlement(pos, exit_px, pnl, reason)
4. Move to scalp_lab_state.json["leg_name"]["closed"]
5. Update Brier + DSR + CI live
```

### Edge → Gate → Live
```
Hypothesis (new leg) → n≥10 paper exits → analyst_gate (3-lens panel)
  ↓ (if ≥2/3 lenses pass)
Provisional edge: keep accumulating (n→30)
  ↓ (at n=30)
Gate check: CI>0 + DSR + controls negative
  ↓ (if PASS)
LIVE EDGE: scale position size, schedule monitoring alerts
  ↓ (if FAIL)
Dead edge: retire leg, log postmortem to BRAIN (Lesson library)
```

---

## Validation Checkpoints

### Pre-Live (Paper Only)
**Atomic checks before any real capital:**
1. ✅ Leg code parses without syntax error
2. ✅ Entry scan runs (finds ≥1 position in 30-day backtest)
3. ✅ Exit scan runs (closes ≥10 in paper)
4. ✅ Belief ledger records forecasts + settlements
5. ✅ Calibration output: Brier < 0.50, gap < 0.10
6. ✅ n ≥ 30, CI>0, DSR>0
7. ✅ Control legs all WR<52%
8. ✅ BRAIN.md updated with findings + decision
9. ✅ Watchdog cycle runs clean (no timeouts, no DB lockups)
10. ✅ Obsidian vault updated (belief_calibration.md, analyst_gate.md)

**Sign-off: Run live for 7 days (168 cycles), CI holds, no gaps**

### Live Monitoring
```
Hourly:
  - P&L iMessage alert (launchd com.aryan.polymarket-pnl-alert)
  - Belief ledger stale check (last update <65min)
  - Control leg sanity (should be losing)

Daily (6:15am):
  - Obsidian snapshot (belief_calibration.md, sports_summary.md)
  - Gap-through audit (stops vs realized fills)
  - New entry log (entries_today.log)

Weekly:
  - DSR recalculation (if n grew by ≥20)
  - CI width check (if width > initial, investigate overfitting)
  - BRAIN update (new lessons, edge mortality tracker)
```

---

## Edge Mortality Model

**Expected death rates** (observed):
- **Oversized edges** (80% WR): die in live → realized 52% WR (gap-through, execution friction)
- **Statistical flukes** (CI barely > 0): die within n=100
- **Time-decaying edges** (e.g., Twitter sentiment): half-life ≤30 days
- **Crowd-discovered edges** (e.g., favorite underreaction): die when vol increases (faster bookmakers)

**Why nearres died** (2026-06-14):
```
Backtest artifact: stops booked at −3¢ trigger (clean fill fiction)
Reality: stops gap-through to −45–65¢ (one tick on esports favorite)
Gap-honest re-price: −$0.088/exit (DSR FAIL)
Decision: retired (execution gap unclosable on Polymarket venue)
```

**Survival factors:**
- Execution friction < 50% of gross edge (e.g., if spread=2¢, edge must >4¢)
- Signal independent (not crowded → crowd hasn't killed it yet)
- Long-dated (slow decay) vs short-dated (faster execution)
- Resilience to vol regime change (edge ∝ vol^k, low k = robust)

---

## Bet Sizing Formula

**When edge passes n=30 gate:**
```
sigma = realized_win_rate - 0.50 (edge strength, 0 = fair flip)
roi = avg_profit / avg_loss (realized return per exit)
kelly = sigma / roi  (fractional bet size)
actual_size = kelly / 4  (quarter-Kelly, conservative)

Example:
  sigma = 0.55 - 0.50 = 0.05 (5% edge)
  roi = 0.09 / 0.03 = 3x (win 9¢, lose 3¢)
  kelly = 0.05 / 3 = 0.0167 (1.67% of capital per exit)
  actual = 0.0167 / 4 = 0.417% (ultra-conservative)
```

**Practical allocation** (paper capital unlimited):
```
Each edge: $50–200 max concurrent allocation
  (e.g., 10 open @$5 = $50, or 5 open @$10 = $50)
Target: 3–5 edges live simultaneously
Rebalance: quarterly (adjust sigma, roi after new n=30 batch)
```

---

## Knowledge Graph (Lessons)
```
Lesson 1: Clean fill fiction kills edges
  → gap-through modeling mandatory
  → all backtests must replay live fills

Lesson 2: Win-rate ≠ EV
  → 80% WR at $0.002 = −$0.2/trade
  → optimize dollars, not percentage

Lesson 3: Crowded edges die fast
  → signal half-life ∝ crowd size
  → find asymmetric edges (expensive to implement)

Lesson 4: Controls validate marking honesty
  → allin, coinflip, coindown MUST lose
  → if they win → marking bug, not edge

Lesson 5: DSR > raw CI
  → multiple-comparison bias = ~90% false edges at CI>0 alone
  → DSR filters to top 5–10% likelihood

Lesson 6: Execution > signal
  → if you can't fill at fair odds, edge is dead
  → venue matters (Polymarket spreads 2¢, Kalshi 5¢)

Lesson 7: Resolution window = gold
  → nearres edge was real (favorites DO underreact <4h)
  → but gap-through ate it all (execution)
  → edge exits exist, not realizable on Polymarket
```

---

## Success Criteria

### Phase 1: Prove One Edge (Current)
- ✅ n=30 closed positions
- ✅ CI > 0 (bootstrap confidence interval)
- ✅ DSR ≥ 1.0 (accounts for multiple-comparison bias)
- ✅ All control legs WR ≤ 52%
- ✅ Gap-honest P&L (stops model realized fills, not triggers)
- ✅ Live for ≥7 days without CI collapse
- **Deadline**: 2026-06-30 (6 weeks from 2026-05-15 start)

### Phase 2: Optimize Live Edge (Post-Validation)
- Scale position sizing per Kelly formula
- Reduce stop loss (track realized gap-through)
- Add position management (ladder exits, rebalancing)
- Wire to real-money API (with kill switch)

### Phase 3: Scale (Year 2+)
- 3–5 concurrent edges
- $10k–50k allocation per edge
- Automated rebalancing, drawdown gates
- Real-time Hermes + on-chain oracle integration

---

## Current Status (2026-06-15)

**Live Infrastructure**:
✅ Belief ledger (131 legs recorded, calibration live)
✅ Sports data (630 events, ESPN keyless)
✅ Obsidian vault mirror (6h cycle, 5 snapshots)
✅ 33 legs modularized (7 stubs + 26 full)
✅ Analyst gate (3-lens panel, scorecard.py)
✅ Control legs (allin, coinflip, coindown all tracking)

**Validated Findings**:
❌ nearres (edge RETRACTED 2026-06-14 — gap-through ate it)
❌ truefade (6% WR, long-dated politics never moved)
❌ FL-premium harvest (lifetime avg contamination, clean = +0.002)
🟡 Whale-copy (n=5574, +2.8¢ raw, but CI>0 only at ≤1¢ all-in cost)

**Next Gate Check**: 2026-06-30
- Target: ≥1 leg at n≥30, CI>0, DSR>0, live 7 days
- If no leg qualifies: extend paper-only by 30d, audit signal pipeline

---

## Risk Safeguards

1. **Paper-only enforcement**: No real API keys in code; all positions on paper
2. **DB failsafe**: Postgres down → scalp_lab.py aborts cycle (no zombie trades)
3. **Disk-full guard**: Restart Postgres + watchdog (prevent WIPE)
4. **Control sanity**: If allin WR > 52%, BRAIN alerts + manual review
5. **Edge death detection**: If CI flips negative, auto-retire leg + log postmortem
6. **Watchdog timeout**: Cycle >120s → logged, next cycle runs (no cumulative lag)

---

## Summary

**Brain = Belief → Calibration → Validation → Position Sizing → Execution → Monitoring → Lesson**

Each leg feeds honest forecasts (belief_ledger) → calibration proves honesty (Brier, deciles) → 3-lens gate kills lucky edges → survivors get sized (Kelly) → live execution tracks realized fills (CLOB, gap-through) → every result recorded + analyzed → lessons feed next iteration.

**No magic. No oversized wins. Just one proven edge, honestly measured, live on paper, then real capital with risk limits.**

