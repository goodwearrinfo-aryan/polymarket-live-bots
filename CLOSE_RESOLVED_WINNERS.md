# Close Resolved Winners — Procedure

**Total Capital to Free:** $4.05  
**Action:** Execute immediately (these markets have resolved)

---

## WINNERS TO CLOSE (5 total)

### OLD WINNERS (>2 weeks, close immediately)

| # | Leg | Market | Side | P&L | Closed | Action |
|---|-----|--------|------|-----|--------|--------|
| 1 | allin | Knicks vs Spurs | YES | +$1.6667 | 2026-06-05 | ✓ CLOSE |
| 2 | coinflip | LoL: Karmine Corp vs GIANTX | YES | +$1.0833 | 2026-06-03 | ✓ CLOSE |
| 3 | momentum | Seoul Mayoral Election | YES | +$0.8709 | 2026-06-05 | ✓ CLOSE |

**Subtotal: $3.62**

### RECENT WINNERS (Last 7 days, already closed)

| # | Leg | Market | Side | P&L | Closed | Status |
|---|-----|--------|------|-----|--------|--------|
| 4 | nearres | Counter-Strike: Aurora vs FOKUS | NO | +$0.1749 | 2026-07-23 | Closed |
| 5 | nearres | LoL: T1 vs Gen.G | NO | +$0.2588 | 2026-07-19 | Closed |

**Subtotal: $0.43**

---

## CLOSURE CHECKLIST

- [ ] **Verify each market has resolved** (cross-check Polymarket for each slug)
- [ ] **Confirm exit_reason = "resolved"** in state file for each trade
- [ ] **Record final P&L** for each position (already recorded in state file: pnl_usdc field)
- [ ] **Remove from open positions** (update scalp_lab_state.json to move from "open" to "closed" if still in open)
- [ ] **Log closure** to SEVEN_DAY_TEST_MONITOR.md with timestamp
- [ ] **Update capital ledger** (add $4.05 to available allocation pool)

---

## CAPITAL REALLOCATION PLAN (Post-closure)

| Action | Amount | Destination |
|--------|--------|-------------|
| Free from closed winners | +$4.05 | Capital pool |
| Retire coinflip leg | -$66 (sunk loss, don't recover) | Write-off |
| Retire microscalp | -$0 (0% WR) | Write-off |
| Allocate to Fed basket | ~$4-5 | New 5-leg arb structure |
| Allocate to windowshutrand | ~$0-1 | Keep running (profitable during test?) |

---

## TIMING

**Close immediately:** allin, coinflip, momentum (these are 3-4 weeks old)  
**Already closed:** nearres positions (just update state file)  
**Effective:** 2026-07-24 (today)  
**Capital available for Fed basket:** 2026-07-24 onward

---

## FED BASKET SPEC (TBD)

Pending decision on 2026-07-30 (if test PASSES), structure:
- 5 complete-set basket legs
- 0.95% edge (from backtest)
- Allocation: $4-5 initial (freed capital) + additional paper allocation

---

## LOG ENTRY

```
[2026-07-24 00:00] CLOSED RESOLVED WINNERS
  ✓ allin       Knicks vs Spurs     YES  +$1.6667
  ✓ coinflip    LoL Karmine         YES  +$1.0833
  ✓ momentum    Seoul Election      YES  +$0.8709
  ✓ nearres     Aurora CS           NO   +$0.1749
  ✓ nearres     T1 Gen.G            NO   +$0.2588
  
  Total freed: $4.05
  Reallocate to: Fed basket (2026-07-30 allocation decision)
```
