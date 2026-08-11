# Polymarket Lab — Project Map & Priorities
_Snapshot 2026-06-13. One page. BRAIN.md is canonical detail; this is the org chart._

## The One Goal
Prove ONE edge with real money. Everything else is in service of that or is noise.
Status: **nearres at 27/30 forward exits, 88.9% WR** — 3 from graduation.

---

## TIER 0 — THE CRITICAL PATH (only this moves the needle)
1. **nearres gate 27→30** — auto, ~hours-to-days. Hourly Pulse fires 🎓 at 30.
2. **Switch <4h→<2h entry** — on graduation (2h graduated OOS: n=65, DSR 0.976,
   +4.6¢ vs +3.1¢). One config line, I do it on your word.
3. **Real-money micro-test** — YOURS, manual. $100-200, $5/trade, only trades the
   bot also took. Rules pre-registered in GRADUATION_PROTOCOL.md. Measures the
   one unknown paper can't: paper-fill vs real-fill slippage.

→ Nothing in Tier 1-3 should distract from finishing Tier 0.

---

## TIER 1 — LIVE INFRASTRUCTURE (keep alive, don't churn)
| System | What | Health |
|--------|------|--------|
| scalp_lab + watchdog | the lab, `once`/60s, Postgres state | UP, mirror fresh |
| copy bot (dry-run) | wallet-flow paper trader | UP (Decimal crash fixed today) |
| insider stack | watch (100 wallets, scored) + finder (10min) + bot_id | UP |
| hermes_news :48571 | 53-feed keyless RSS, dual with OpenAlice | UP |
| lab_api :8787 | read-only REST | UP |
| terminal tools | pmterm / orderflow / heatmap (built today) | on-demand |
| 16 launchd jobs | pulse, routines, pdf, covariates, backups | running |

**Single point of failure: this Mac.** VPS deploy is the only real resilience gap.

---

## TIER 2 — ACCUMULATING (judge later, hands off until n≥thresh)
- **nearresfade** 19/30 — second potential edge, CI still spans 0
- **brainbot** — distilled 3-structure paper bot, insider mirror building
- **Covariates logging, analyze at n≥30**: gdelt_intensity (quietfade),
  wang_fair_yes (nearresfade), cs2_fair_leader (psconfirm), vol_regime, spread
  bucket, tournament round
- **kalshi arb / edge_conditions / belief_ledger** — research utilities

## TIER 3 — DISCIPLINE (the part that's working)
- **89 legs killed by rule.** Today: emacross/tadip/gridbounce/rsifade/esportsdog.
  Meta-finding: crypto-TA & OSS trend signals DON'T translate to PM favorite pricing.
- Controls (allin/coinflip) lifetime −$120.55, honest ✓ — marking is sound.
- Vet-before-clone; kill on evidence; no config churn.

---

## OPEN DECISIONS (need Aryan)
- [ ] Switch to 2h on graduation? (recommend yes, after 30/30)
- [ ] Real-money micro-test — go / no-go when gate clears
- [ ] VPS deploy — remove the single-Mac failure point?
- [ ] Disk: ~4.5G more reclaimable in oss-bots (trading-extra/meta) — clear?

## DON'T TOUCH
OpenAlice (:47331 feed), jesse + jesse-env (launchd), pnl_baseline.json,
Postgres state, the kill graveyard (no resurrection without NEW evidence).
