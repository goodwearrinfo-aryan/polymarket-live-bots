# Fade Strategy Spec — directional mispricing (favorite-longshot)

Status: design, grounded in the 363-market resolved analysis (2026-06-01). Paper only.
This is the ONE strategy worth pursuing; it replaces the committee of 9 correlated legs.

## 1. The thesis, and exactly how strong it is

The market systematically **overprices YES** below ~0.6 and **underprices favorites** above
it — the classic favorite-longshot bias. Measured per price bucket (deduped, 363 markets):

| price band | n | mkt-implied YES | realized YES | YES overpricing (= fade edge/contract) |
|---|---|---|---|---|
| 0.20–0.30 | 38 | 0.236 | 0.053 | **+0.183** |
| 0.30–0.40 | 20 | 0.341 | 0.150 | **+0.191** |
| 0.40–0.50 | 78 | 0.489 | 0.436 | +0.053 |
| 0.50–0.60 | 124 | 0.506 | 0.403 | **+0.102** |
| 0.60–0.70 | 20 | 0.654 | 0.700 | −0.046 (edge GONE — do not fade) |
| 0.70+ | 12 | — | — | strongly negative (favorites underpriced) |

Fade = buy NO on markets priced in the overpriced band, hold to resolution. In-sample:
**+$0.096/trade over 260 trades, 66% win, after spread.** Broad-based (top-5 = 11% of P&L).

## 2. The three caveats that gate real money (read before believing any of it)

1. **78% sports.** 203 of 260 fade trades are sports markets. "Will team X win" has a
   structural NO-skew (most teams lose) that may not be a *pricing* inefficiency. The edge
   could be partly an artifact of question structure, not market error.
2. **Statistically marginal mid-band.** The 0.5–0.6 overpricing (0.40 vs 0.50) is ~2 SE over
   124 markets — real-ish, not overwhelming. The big 0.2–0.4 gaps are small-n (38, 20).
3. **One era, selection-biased** to high-volume closed markets. In-sample ≠ forward.

**Verdict: a real, mechanism-backed candidate — NOT a confirmed edge. It must survive a
forward, out-of-sample run on fresh markets before any real capital. The live paper fade
leg IS that test.**

## 3. Parameters — derived from the data, kept few (per the overfitting rule)

Few knobs on purpose; every parameter is a degree of freedom to overfit.

| param | value | derived from |
|---|---|---|
| `fade_band` | buy NO when YES price ∈ **[0.20, 0.55]** | overpricing is strong below 0.55 and reverses by 0.60; exclude 0.55–0.60 as the buffer to the flip |
| `min_edge` | **0.08** (gross gap mkt-YES vs model/fair NO) | spread (0.02) + fee margin + model-uncertainty buffer; the measured gaps (0.10–0.19) clear it, 0.4–0.5's +0.05 does NOT (correctly excluded) |
| `max_spread` | **0.03** | keep existing; never fade an untradeable book |
| `min_volume` | **200,000** | liquidity to exit; existing fade filter |
| hold | **to resolution** | mispricing capture realizes at resolution; no take-profit needed |
| `invalidation` | price rises back above 0.60 before resolution → exit | the edge is gone above 0.60 (table); don't hold through the flip |
| sizing | **flat, tiny** until forward-confirmed; then **¼-Kelly** | full Kelly blows up on estimation error you don't have |
| `max_per_market` / `max_total` / `max_concurrent` | small caps | solvency, not strategy |
| `max_correlated_exposure` | **cap sports as ONE bet** | 78% sports = correlated; five sports fades are ~one bet, size to that |
| `max_daily_loss` | kill-switch | halt on breach |

## 4. What changes in the lab

- **Retire** dip, momentum, midfade, favyes, scalp, fastfade as live legs — they're
  correlated price-costumes, and the fade is the one with evidence. Keep their code, stop
  funding them. KEEP allin + coinflip as negative controls (free falsification).
- **Tighten the fade leg** to the [0.20, 0.55] band and add the >0.60 invalidation exit.
- **Track edge BY CATEGORY live** — if forward edge is all sports, treat it as a sports-
  structure bet, not a general mispricing, and size/judge it as such.
- Everything else (honest marking, bootstrap significance, controls-negative tripwire) stays.

## 5. The confirmation gate (the only thing that earns real money)

Run the tightened fade forward on FRESH markets (the VPS, paper). Do NOT size up or go
real until, on out-of-sample closed trades:
- the fade's P&L 95% CI (via leg_health.py) **excludes zero**, AND
- it holds **outside sports** (or you accept it as an explicitly sports-structure bet), AND
- ≥30 independent closed fades, not 30 correlated sports markets from one weekend.

If it collapses out-of-sample → it was a sample artifact, and the correct action is to stop.
That outcome is a success of the process, not a failure.

## 6. Honest one-liner
The data found a real, textbook mispricing — and also told us it's mostly one correlated
category in one era. The spec captures it cautiously and refuses to believe it until fresh
markets agree. That discipline is the whole edge; the parameters are secondary.
