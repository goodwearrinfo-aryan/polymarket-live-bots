# Glossary — Polymarket paper-trading bot

Decoder for this project's shorthand. (Solo research project — no team/people memory.)
Updated 2026-06-13. Canonical state lives in BRAIN.md — if this file and BRAIN disagree, BRAIN wins.

## Strategy legs (scalp_lab.py — all paper)
| Leg | What it does | Side | Status |
|-----|--------------|------|--------|
| **dip** | buy YES 0.30–0.40, sell ~0.49 (falling-knife reversion) | YES | KILLED (proven loser) |
| **scalp** | buy near-certain favorites 0.95–0.97, +2¢ | YES | live |
| **fade** | buy NO on elevated YES (0.60–0.85), patient +10¢ | NO | live, the old "house pick" |
| **fastfade** | same NO signal, quick +3¢, churns | NO | live |
| **allin** | buy EVERYTHING 0.05–0.95 — the negative CONTROL | YES | live (must be negative) |
| **momentum** | buy YES that's rising (chg≥+4¢) — trend continuation | YES | added 2026-06-01 |
| **favyes** | buy favorites 0.80–0.90, hold to resolution | YES | added 2026-06-01 |
| **coinflip** | buy ~0.50 indiscriminately — 2nd negative CONTROL | YES | added 2026-06-01 |
| **midfade** | fade the recent drift back toward 0.50 (reversion) | YES/NO | added 2026-06-01 |
| **nearres** | esports favorites <4h to resolution, side-mid [0.88,0.95] | YES | **lead candidate** — 24/30 exits, 83% WR, CI excludes 0; DSR-passed OOS n=150 |
| **truefade** | buy NO on YES[0.20,0.55] (the corrected fade band) | NO | KILLED 2026-06-10 (6% WR, long-dated politics never moved) |
| **nearresfade** | NO on YES[0.22,0.52], 1–30d to resolution | NO | replacement for truefade, live |
| **coinup/coindown/diverg/feargreed/lateprox** | crypto Up/Down coinflip legs (coindown is a 3rd control) | mixed | accumulating data |
| **noevent/newsno/btc15no/weatherno/ytbuzz** | house 3.0 R:R legs (gain 9¢/stop 3¢) | NO/mixed | live |
| **nearrestitle** | nearres minus Dota2/handicap (per-title FLB gate, Finding C) | YES/NO | added 2026-06-13 |

Controls (allin, coinflip, coindown) SHOULD lose after spread — if positive, the marking is broken.
Note: the original **fade** leg above was mis-banded; the corrected thesis was truefade → killed → nearresfade. See BRAIN.md for the lineage.

## Terms
| Term | Meaning |
|------|---------|
| **taker** | crossing the spread (buy at ask / sell at bid) — pays the ~2¢ tax; every current leg is taker |
| **maker** | posting limit orders to EARN the spread — the one structural edge worth building (option #1) |
| **the marking bug** | target exits booked the full overshoot past take-profit while stops ate full adverse move; fixed (cap at limit fill) |
| **honest / capped** | re-marking target exits at bid(target) so P&L isn't inflated; see honest_report.py |
| **edge_trader** | ML-divergence paper daemon — DISABLED 2026-06-01 (model confirmed broken, pins favorites to NO) |
| **scalp_engine** | separate real-scalping daemon with taker+maker books; maker book is where option #1 gets built |
| **the edge / +0.022** | model AUC 0.775 vs price-alone 0.747 — within noise, i.e. NO real edge |
| **latency probe** | measure_reprice.py — does the book reprice slowly enough to front-run? (median >10min = yes) |
| **resolution latency** | the candidate structural edge: act on a real-world fact before the book reprices |

## Key files
| File | Role |
|------|------|
| scalp_lab.py | the multi-leg paper A/B lab + dashboard generator; runs `once` per ~60s via watchdog — NEVER start a `run`-mode daemon (races Postgres) |
| scalp_lab_state.json | read-only MIRROR of lab state — real state is PostgreSQL (db.py); don't hand-edit |
| fade_checkpoint.py | graduation gate — now points at nearresfade (30 exits + CI>0 + controls lose) |
| honest_report.py | read-only honest re-mark; powers the 3x/day report + network reachability line |
| build_status_pdf.py | regenerates polymarket_status.pdf (honest numbers + NETWORK line) |
| watchdog_loop.sh | keeps daemons alive on the Mac; runs scalp_lab each tick; has the daily latency probe |
| verify_model.command | gate that must PASS before edge_trader is ever re-enabled |
| ml/train.py | model trainer; save gate raised 0.02→0.06 (above noise) |

## Hard rules (from CLAUDE.md)
- Optimize DOLLARS not win rate. Closed/exit trades only; need 20–30 before any verdict.
- A leg needs ~10 exits before its number means anything.
- Don't restart edge_trader until verify_model.command PASSES.
- The Mac↔Polymarket network is the recurring blocker — check the NETWORK line first.
