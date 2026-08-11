# Edge Mortality Log

Record of dead edges: why they died, when, what we learned.

## 2026-06-14: nearres (RETRACTED)
**Status**: ❌ DEAD  
**Cause**: Clean-fill backtest artifact (gap-through execution)  
**n**: 297 (OOS after gap-honest re-price)  
**Original (backtest fiction)**: n=103, WR=63%, CI=[+0.0057, +0.0533], DSR=+0.28  
**Gap-honest (reality)**: n=297, WR=48%, CI=[-0.129, -0.049], DSR=-6.48  
**Root cause**: 
  - Backtest booked esports favorite stops at −3¢ trigger (clean fill assumption)
  - Live: one-tick gap on resolution → stops fill −45–65¢ (−93% worse)
  - Edge was REAL but unrealizable on Polymarket execution
**Lesson**: Backtests must model gap-through or CI is fiction (Lesson 13, BRAIN.md)
**Timestamp**: 2026-06-14T18:57Z

---

## 2026-06-10: truefade (KILLED)
**Status**: ❌ DEAD  
**Cause**: Long-dated markets don't move  
**n**: 28  
**WR**: 6%  
**CI**: [-0.60, -0.30]  
**Thesis**: Fade YES[0.20,0.55] on <7-day-to-resolution markets  
**Failure mode**: Politics/sports predictions are mostly determined early; 7-day window too noisy, entry prices already fair
**Decision**: Long-dated fades are unactionable. Horizon must be ≤30d (Lesson 3)
**Replacement**: nearresfade (NO on YES[0.22,0.52], 1–30d to res) — still accumulating
**Timestamp**: 2026-06-10T13:22Z

---

## 2026-06-15: FL-premium harvest (CALIBRATION NULL)
**Status**: ❌ DEAD  
**Cause**: Lifetime-average-price contamination (survivorship bias)  
**n**: 726 Goldsky V1 resolved markets  
**Original "edge"**: +0.177 Wilson CI  
**Root cause**: 
  - Computed fair price as lifetime avg of all market prices at all times
  - Excluded early "stale" prices (where market wasn't yet calibrated)
  - Survivor bias: only prices after market matured were included
  - Clean FL (early price only): +0.002 (no edge)
**Maker taker spread**: Both lost (maker −6x less, still negative)
**Lesson**: No resolve/target slice = edge (survivorship). FL must use early price, not lifetime avg (Lesson 13 generalized)
**Wilson CI correctness**: Panel confirmed CI computation was honest; edge was fake
**Timestamp**: 2026-06-15T09:30Z

---

## 2026-06-08: diverg, feargreed, lateprox, coinup, coindown (REVIVED 2026-06-15)
**Status**: 🟡 SUSPENDED → ENABLED  
**Cause (2026-06-08)**: No crypto Up/Down markets (coinup/coindown) at suspension  
**Revived (2026-06-15)**: Markets returned; legs re-enabled with fresh accumulation
**Current state**: Diverg n=127 Brier=0.0847 (well-calibrated), feargreed accumulating, others boot-fresh
**Timestamp suspend**: 2026-06-08T15:44Z  
**Timestamp revive**: 2026-06-15T06:00Z

---

## 2026-06-15: Whale-copy edge (NEAR-MISS)
**Status**: 🟡 ACCUMULATING (likely to die on CI>0 threshold)  
**n**: 5,574  
**Raw edge**: +2.8¢/exit (sharp whale traders ARE real)  
**All-in cost**: ≤1¢ (commission, spread, slippage)  
**Net edge**: +1.8¢ → CI barely > 0 at this cost structure  
**Failure mode**: Likely DSR<0 once we control for multiple-comparison bias  
**Timeline**: If CI flips negative before n=100, retire (edge was luck)  
**Decision pending**: Wait for n=30, then analyst 3-lens gate
**Timestamp**: 2026-06-15T12:15Z

---

## 2026-06-13: nearrestitle (LIVE EDGE CANDIDATE)
**Status**: 🟡 ACCUMULATING (n=8, on watch)  
**Thesis**: nearres minus Dota2/handicap/tennis-leaks  
**Finding C**: Dota2 reverse-FLB (n=24, OOS −50% WR), LoL CI>0 standalone  
**Filter**: exclude Dota2, handicap markets, match-outcome-known tennis  
**Current n**: 8, too early to judge  
**Gate check**: 2026-06-30 (wait for n≥30)
**Timestamp**: 2026-06-13T10:22Z

---

## Survival Factors (Lessons)

**Legs that survived and contributed to live portfolio:**
- **nearres** (edge RETRACTED but method validated): proved favorite underreaction <4h IS real, just unrealizable on Polymarket fills
- **psconfirm** (n=14, WR=58%): PandaScore match-running gate helps; keep as filter
- **sportres** (tennis-only, n=14, WR=64%, early): venue-specific edge (tennis precision > esports margin noise)

**Patterns in dead edges:**
1. **Oversized WR** (>70%) → dies on live execution (spread cost + slippage eats gross edge)
2. **Long-dated signal** → dies (market prices in early, window is too wide)
3. **Crowded signal** → dies within n=50 (crowd prices it away; bookmakers already adapted)
4. **Contaminated backtest** (survivorship, clean-fill fiction, dirty data) → DSR FAIL after re-price
5. **Single-lens signal** (technical only, no market inefficiency) → dies when vol spikes

**Survival requirements:**
- ✅ Edge ≤ gross execution cost (spread/commission/slippage)
- ✅ Independent signal (not crowded by other bots)
- ✅ Horizon <30d (long-dated markets too liquid)
- ✅ 52–68% WR (oversize WR signals overfitting)
- ✅ Honest backtest (gap-through, early price, live fills)

---

## Next: 2026-06-30 Gate Check

**Current candidates for n≥30 gate:**
- nearrestitle (n=8, accumulate)
- sportres (n=14, accumulate)
- diverg (n=127, gate-eligible, but Brier may indicate overfitting at scale)
- whale-copy (n=5574, gate-eligible if DSR>0, but all-in cost killing margin)

**Decision deadline**: 2026-06-30  
**Requirement**: ≥1 leg passes (n≥30 + CI>0 + DSR + controls lose)  
**Fallback**: Extend paper 30d more, audit signal pipeline

