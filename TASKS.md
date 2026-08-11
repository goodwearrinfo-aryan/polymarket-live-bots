# Tasks

## Active

- [ ] **Get Polymarket access (REGION BLOCK confirmed)** - gates EVERYTHING; bot fetches 0 markets
  - Confirmed: blocked on home Wi-Fi AND mobile hotspot (DNS resolves, TCP connect dropped) = regional block. Sandbox also blocked (proxy 403). No connector exists.
  - FIX: follow CLOUD_SETUP.md — run the bot on a ~$5/mo VPS in a non-US, non-India region (DE/NL/UK/SG). Verify `gamma: 200` on the box first.
  - Alternative: paid VPN to a clean region on the Mac.
- [ ] **Build #1: maker / spread-capture strategy** - the one structural edge worth building
  - [x] Spec written → MAKER_STRATEGY_SPEC.md (Phase A paper-sim + Phase B real CLOB)
  - [x] Phase A BUILT + unit-tested: makerH book in scalp_engine.py (quote→through-fill→cancel-stale→taker-fallback); shows in board + dashboard. Goes live next watchdog tick (needs network).
  - [ ] Phase B (gated): real CLOB client, dry-run first — user handles keys/live orders

## Waiting On

- [ ] **Latency probe verdict** - auto-runs daily via watchdog until it completes - output: ml/reprice.log (median >10min = build it)
- [ ] **Superhuman connect (optional)** - needed for real send+attach email; until then the 3x/day task only drafts

## Someday

- [ ] **Crypto-threshold latency play** - "BTC > $X at T" markets; truth = Binance/Coinbase API, book lags
- [ ] **Arbitrage scanner** - multi-outcome events where YES prices sum ≠ 1; riskless-ish, no prediction
- [ ] **Decommission dead prediction legs** - dip/scalp/fade/momentum/midfade/favyes all pay the spread; keep allin+coinflip as controls only

## Done

- [x] ~~Find & fix the paper P&L marking bug~~ (2026-06-01) - target-overshoot; allin control now correctly -$2.20
- [x] ~~Make report + dashboard exit-first and honest-capped~~ (2026-06-01)
- [x] ~~Disable edge_trader + raise train.py save gate 0.02→0.06~~ (2026-06-01) - broken model contained (activates on watchdog restart)
- [x] ~~Add 4 lab legs: momentum, favyes, coinflip, midfade~~ (2026-06-01)
- [x] ~~Set up 3x/day status email + PDF + network reachability line~~ (2026-06-01)
- [x] ~~Confirm ML edge is dead~~ (2026-06-01) - price-alone AUC 0.747 vs model 0.775 = noise; data source exhausted
