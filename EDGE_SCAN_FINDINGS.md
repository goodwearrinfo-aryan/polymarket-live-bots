---
type: hub
updated: 2026-06-15
tags: [spread]
---

# 5-Edge Scan — Findings (2026-06-15)

Built 5 separate scanners against LIVE data to test edge types immune to the
three killers (gap-through, spread-on-exit, calibration). Honest results below.
Scripts: edge1_flb.py … edge5_maker.py + edge_common.py (keyless helpers).

| # | Edge | Killer it dodges | Verdict | Next |
|---|------|------------------|---------|------|
| 1 | FLB hold-to-resolution | gap-through (no stop) | 🟡 UNCONFIRMED — suggestive | forward-test, cheap |
| 2 | Cross-venue divergence | calibration | ⚙️ METHOD BROKEN | needs semantic judge |
| 3 | Stale-resolution latency | prediction entirely | ❌ DEAD on Polymarket | venue resolves too fast |
| 4 | Combinatorial basket arb | ALL three | ✅ BEST LEAD | verify exhaustiveness+depth |
| 5 | Maker / earn-the-spread | spread (you earn it) | ❌ STRUCTURALLY HARD | liquid=0.1¢, wide=adverse |

---

## 1. Favorite-Longshot Bias (hold-to-resolution) — 🟡 UNCONFIRMED
- **Historical** (726 Goldsky resolved): longshot bands show positive *point* fade-EV
  (0.10→+0.10, 0.25→+0.13) but EVERY band's implied price sits INSIDE the realized
  Wilson CI. n per band = 14–55; too small to confirm. Not significant.
- **Live**: 211 long-dated (≥14d) longshots (YES≤0.12) fadeable right now
  (World Cup non-favorites, tail geopolitics, "aliens by 2027").
- **Why it's still alive**: the mechanism (longshot overpricing) is the most-replicated
  finding in betting markets, and hold-to-resolution is immune to gap-through.
  The on-chain sample is just too thin to prove it.
- **Action**: cheap forward-test — fade $1 across 30+ independent long-dated longshots,
  hold to zero, measure realized vs implied at n≥30. No stop = no execution killer.

## 2. Cross-Venue Divergence — ⚙️ METHOD BROKEN (not edge's fault)
- Poly (800) × Kalshi (9838), title Jaccard ≥0.34 → 136 "gaps>cost", but the top 130
  are FALSE MATCHES: "Will X win the 2028 election" (0.01) lexically matched to
  "Will the 2028 election OCCUR" (0.91). Same tokens, different question.
- **The edge can't even be measured with lexical matching.** Real test needs a
  semantic judge (LLM per candidate pair: same event + same resolution criteria?).
- **Action**: wire the analyst 3-lens gate as a pair-verifier; only then is any gap real.

## 3. Stale-Resolution Latency — ❌ DEAD on Polymarket
- 578 ESPN finals × 1000 live markets → 1 flag, itself a false match ("draw" token).
- Polymarket resolves sports fast; there is essentially no stale inventory to harvest.
- **Action**: none on Polymarket. The latency edge needs a venue with slow oracles.

## 4. Combinatorial Basket Arb — ✅ BEST LEAD
- 946 events, 378 mutually-exclusive (negRisk). 30 with >1¢ "locked" edge.
- **Big ones are artifacts**: "Lebanon, 23 outcomes, Σask=0.21" = incomplete candidate
  field (listed outcomes cover 15% of probability). Σ>1.5 "shorts" = wide multi-leg spread.
- **Real structure is the tight 2-outcome ones**: Tennessee/Texas/Minnesota Senate,
  Σask ≈ 0.97–0.98 → buy every YES for <$1, one pays $1, locked profit. Immune to
  gap-through (hold to resolution), calibration (no view), and spread-on-exit (settles 0/1).
- **Risk**: (a) field must be truly exhaustive+exclusive (missing tail outcome = fake Σ<1);
  (b) ask quotes must be fillable depth, not stale; (c) capital locked till resolution.
- **Action**: per-candidate verifier — confirm exhaustiveness, pull live book depth on
  each leg, size by min fillable quantity. This is the one to build next.

## 5. Maker / Earn-the-Spread — ❌ STRUCTURALLY HARD
- 918/1000 liquid markets (>$200k vol): median spread = **0.1¢**. Nothing to earn.
- Wide-spread markets (ETH-dip 8.7¢, launch-FDV props 9.9¢) are exactly where INFORMED
  flow runs a maker over (adverse selection). Classic MM dilemma, confirmed live.
- **Action**: only resolvable by fill-sim vs the tape; screen says the juice isn't there
  at retail on the liquid book. Deprioritize.

---

## Bottom line
Of 5 edge types tested live, **2 survive to a real next step**:
- **#4 basket arb** — real free-money structure on tight 2-outcome negRisk events;
  needs an exhaustiveness+depth verifier (highest conviction, fully killer-immune).
- **#1 FLB forward-test** — cheap, gap-through-immune, mechanism is textbook; on-chain
  sample too thin to confirm, so let live paper accumulate n.

**#2 needs a semantic layer before it's even testable. #3 and #5 look dead on this venue.**
The honest machine worked: it killed the false matches (xvenue), the incomplete fields
(basket big-ones), and the no-spread liquid book (maker) — and left two real leads standing.
