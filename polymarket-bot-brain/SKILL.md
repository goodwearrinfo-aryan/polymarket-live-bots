---
name: polymarket-bot-brain
description: >-
  Full working context for Aryan's Polymarket paper-trading bot project. Use this
  skill WHENEVER the conversation touches the Polymarket bot, the scalp lab, trading
  legs (dip/scalp/fade/fastfade/allin/momentum/favyes/coinflip/midfade), the ML edge
  model, the maker/spread-capture engine, edge_trader, the favorite-longshot fade
  thesis, the cloud/VPS deployment, or any file in ~/Documents/polymarket — even if
  the user doesn't restate the background. It encodes the project's decisions, hard
  rules, current blockers, and what's already been proven or killed, so you don't
  relitigate settled questions or repeat dead ends. Load it before proposing changes,
  running reports, or giving strategy advice on this project.
---

# Polymarket Bot — Working Brain

This project is a **PAPER-trading research bot** for Polymarket. The goal is to find
ONE real, proven edge — not to "make money fast." Treat profit claims with suspicion;
the job is honest measurement, and a confident "no edge here" is a successful outcome.

## Hard rules (these override enthusiasm — including the user's)
1. **Optimize DOLLARS, not win rate.** A high win rate usually means buying favorites
   where one loss erases many wins. Win rate is vanity.
2. **Closed/exit trades only.** A leg needs **≥10 closed exits**, ideally 20–30, before
   any verdict. One win is noise.
3. **Controls must be negative.** `allin` and `coinflip` buy indiscriminately; after
   spread they MUST lose. If a control shows positive P&L, the accounting is broken —
   that's a marking-bug alarm, not a discovery.
4. **Significance, not point estimates.** An edge counts only if its bootstrap 95% CI
   excludes zero (see `leg_health.py`, `train.py`). A point estimate above some threshold
   is not enough — price-AUC alone swings with SD ~0.066 across splits.
5. **Don't churn strategy on mood.** Each config change resets the experiment. Derive
   thresholds from data (min_edge from spread/cost, stops from realized vol), not round
   numbers that feel right.
6. **Paper only. Never place real orders, enter API keys, or move funds.** Real-money
   execution (polymarket.us signed orders) is the user's to do, not Claude's. And it's
   gated on forward-confirmed edge, which doesn't exist yet.

## What's been PROVEN or KILLED (don't reopen without new data)
- **ML divergence edge: DEAD.** Price-alone AUC 0.752 vs model 0.775 on the held-out
  test = +0.022, and the bootstrap CI straddles zero. The model is also miscalibrated
  (pins favorites to NO: a 0.95 market scores 0.32). `edge_trader` is DISABLED. Do not
  re-enable until `verify_model.command` PASSES. `train.py` save-gate raised to 0.06 AND
  requires CI>0, so nightly retrains stop re-blessing the dead model.
- **Data source exhausted.** ~1005 markets already collected; a fresh run added +0. More
  collection won't help; the lever is distinct-market COUNT (thousands), not retuning.
- **The marking bug: FOUND & FIXED.** Target exits used to book the full overshoot past
  the take-profit while stops ate the full adverse move → allin falsely showed +$0.43.
  Fixed by capping target exits at the limit fill (`scalp_lab.py` ~lines 400-411). Honest
  re-mark: allin −$2.20 (correct), lab total −$3.65. `honest_report.py` enforces this.

## The ONE candidate edge: favorite-longshot FADE (see FADE_STRATEGY_SPEC.md)
- The market **overprices YES below ~0.6** and underprices favorites above it (textbook
  favorite-longshot bias; clean sign-flip at 0.6). Fading (buy NO) in [0.20, 0.55] showed
  **+$0.096/trade, 66% win, in-sample, after spread** — broad-based (top-5 = 11% of P&L).
- **Caveats that gate real money:** 78% of the edge is SPORTS markets (possible structural
  NO-skew, not pricing error); the mid-band is ~2 SE; one selection-biased era. It is a
  real CANDIDATE, NOT confirmed. Confirm forward on fresh markets (CI excludes 0, ≥30
  independent fades, holds outside sports) before sizing. Then ¼-Kelly, never full.
- Consolidation plan (not yet applied to code): retire the correlated price-costume legs
  (dip/momentum/midfade/favyes/scalp/fastfade), keep the fade + controls. Don't apply
  silently — it changes the running experiment.

## Current blocker (the ONLY thing stopping live paper trading)
**Polymarket is network-blocked.** `gamma-api.polymarket.com` is unreachable from the
user's Mac (regional/ISP block in India — DNS resolves, TCP connect dropped; fails on
Wi-Fi AND mobile) and from Claude's sandbox (proxy 403). No Polymarket connector exists
in the registry. The fix is `CLOUD_SETUP.md`: a ~$5/mo VPS in a NON-US, NON-India region
(Germany/Singapore), verify `gamma: 200`, rsync the project, run the systemd service.
Claude CANNOT provision servers, pay, or SSH — that's the user's step. Until then the bot
fetches 0 markets and nothing fills; this is not a code bug.

## File map (~/Documents/polymarket)
- `scalp_lab.py` — 9-leg paper A/B lab + dashboard generator; watchdog re-execs it fresh
  every ~60s (so edits go live next tick, no restart). State: `scalp_lab_state.json`.
- `scalp_engine.py` — real-book scalper; taker + optimistic maker + **makerH** (honest
  maker: quote→fill-only-on-through-trade→cancel-stale→taker-fallback; Phase A built/tested).
- `honest_report.py` — read-only honest re-mark; powers reports + the Mac→Polymarket NETWORK line.
- `leg_health.py` — bootstrap P&L significance per leg (REAL vs noise).
- `calibration_report.py` — Brier/log-loss/reliability + fade backtest; runs offline on dataset.csv.
- `build_status_pdf.py` → `polymarket_status.pdf`; `watchdog_loop.sh` (keeps daemons alive,
  daily latency probe, edge_trader DISABLED); `verify_model.command` (gate before any model restart).
- `ml/train.py` (bootstrap edge CI + 0.06 save gate), `ml/measure_reprice.py` (latency-edge test).
- `CLOUD_SETUP.md` + `polymarket-bot.service` (VPS deploy), `FADE_STRATEGY_SPEC.md`, `TASKS.md`,
  `dashboard.html`, `memory/glossary.md`.

## Scheduled tasks (Cowork, run on the Mac, draft-only email — Gmail can't send/attach)
3x/day status, daily leg-health, weekly bootstrap review, daily latency probe. They READ
state and draft to goodwearr.info@gmail.com; they don't train or trade.

## How to behave on this project
- Be the honest advisor: lead with the uncomfortable truth, tag confidence [Certain]/[Likely]/[Guessing],
  don't promise profit, don't fold under pressure to build a "money printer" or "never-lose" bot.
- Verify offline whatever you can (everything except live Polymarket runs in the sandbox/Mac).
- Don't reopen the dead ML edge, don't re-enable edge_trader, don't churn configs, don't
  place real orders. Do confirm edges forward with significance before believing them.
