# Project memory — Polymarket bot

## READ FIRST
Full roadmap is in **BUILD_PLAN.md** (this folder). Start there.

## Where we are — EXPERIMENT CONCLUDED (2026-08-11)
- **Verdict: no edge found.** Every edge family was measured and closed.
  - All 92 scalp legs: noise or losing (paper −$184.73, weighted WR 25.5%)
  - Controls (allin −0.137, coinflip −0.096/exit): properly negative → accounting honest
  - ML divergence: noise (P(edge>0)=0.035, CI includes 0)
  - Resolution-source latency: dead (book reprices 1.1 min median; slow tail all <$10K vol)
  - Stale sports settlement: bug fixed but 0 game-level markets exist live
  - Market-making: closed by its own README (compressed spreads); real MM winners run 3s cadence, unreplicable
  - Only positive-mean legs (scalp +0.011, diverg +0.386/exit) each have just 4 exits = noise
- **Do NOT** churn strategy config on mood or re-litigate closed families. Each change resets the experiment.
- If revisiting: watch for (a) game-level sports markets, (b) short-dated crypto thresholds, (c) any leg reaching n≥10 with positive CI. Until then the paper system runs as a monitoring harness, not an edge hunt.

## Running paper system (don't break it)
- `scalp_lab.py` — 5 legs: fade, fastfade, scalp, allin (control), + (dip & taker = KILLED). Runs via watchdog every ~60s. Hot-reload config: `scalp_engine_config.json`.
- `scalp_engine.py` — real scalping engine, taker+maker books (taker killed, maker live).
- `scalp_lab_dashboard.html` — live dashboard, auto-refresh.
- Daily 9 AM P&L report is scheduled.
- Main bot: `polymarket_bot.py` (config fixes applied: min_price 0.35, min_confidence 0.48, BUY gate, midline off). Watchdog: `watchdog_loop.sh`.

## 2026-08-11 — resolution-latency edge: feasibility VERDICT (dead, recorded)
- The "pursue next" edge from CLAUDE.md. All gates measured on the Mac; result:
  **no currently-capturable latency edge.**
- `ml/measure_reprice.py`: book median reprice **1.1 min** (67% ≤2 min). Front-running
  the repricing is dead — you can't beat a book that snaps in one tick.
- `ml/measure_reprice_by_segment.py`: the slow tail (p75/p90 ≈ 91 min) is **entirely
  <$10K volume** markets (exact-score props, obscure soccer). Spread/fees eat any edge
  → untradeable. No slow + liquid segment exists.
- `edge3_stale.py` (stale sports settlement): FIXED a real bug — `sports_data.json`
  `winner` is a side label ("home"/"away"), edge3 matched the literal word "home"
  against team names → could never match. Now resolves to the actual team AND requires
  BOTH teams of the completed game in the market question (kills season-futures false
  positives). After fix: **0 game-level sports markets exist live** → edge can't fire.
- `resolution_watcher.py` (crypto threshold): FIXED blindness — CoinGecko free tier 429s
  constantly; now uses `feeds.coingecko()` fallback chain (paprika→coinlore) so the
  signal never goes blind; replaced the placeholder 0.7/0.3 tick-direction logic with
  `ml/resfeed.res_signal()` real threshold signal. Daemon healthy. BUT: all 37 liquid
  threshold markets resolve "by Dec 31, 2026" (long-dated, spot doesn't decide them);
  **0 resolve within 7 days** → still no trades to make.
- LESSON: the resolution-latency family is structurally dead in this market state.
  Do NOT rebuild/relitigate; watch for game-level sports markets + short-dated crypto
  thresholds to reappear before revisiting.

## 2026-06-05 — two survivorship bugs fixed (both verified on the Mac's mounted state)
1. **scan_exits was network-gated** (`scalp_lab.py one_scan`): exits only ran inside
   `if markets:`, so when the gamma feed was empty (Mac blocked), NO exits evaluated —
   losers were parked in open[] past their hold caps, faking +EV via survivorship. The
   stale_nodata safety valve was dead code under the exact condition it was built for.
   FIX: `scan_exits` now runs every cycle; only prune_stale/scan_entries stay behind
   `if markets:`. Stale/time-capped positions force-close even with no feed.
2. **leg_health counted breakeven no-data closes as exits**: stale_nodata closes at
   entry_fill = $0 P&L. Those zeros (a) diluted the mean and (b) defeated the
   survivorship guard (it counted stale_nodata as a "loss-exit"), flipping fade/fastfade
   to a FALSE "REAL +EV". FIX: leg_health now EXCLUDES stale_nodata from the bootstrap,
   the >=10 gate, and the verdict (reports them as "+N no-data excl"); the survivorship
   guard counts only stop/time as real losses. Added an EXIT-REASON BREAKDOWN table.
   RESULT: fade/fastfade back to !! SURVIVORSHIP UNRELIABLE (5 wins/0 losses + 14 no-data;
   16/0 + 12 no-data). midfade is the only honestly-closing leg (8/8), and it's noise.
   STILL no +EV leg. Verdict rule now: REAL needs priced-exits>=10 AND CI excl 0 AND real
   (stop/time) loss exits — not $0 no-data closes.
NOTE: both fixes are on disk; the live scalp_lab daemon needs a watchdog restart to load
the scan_exits change. leg_health.py is run fresh each time so it's already live.

## Hard rules
- Optimize DOLLARS, not win rate. Win rate is vanity.
- Closed trades only; need 20–30 before any verdict. One win = noise.
- Check backtests for data leaks / non-independent samples before trusting them.
- Don't change strategy settings on mood — each change resets the experiment.

## Coding guidelines (Karpathy) — full skill: `karpathy-guidelines`
- **Think before coding** — state assumptions; if multiple readings exist, surface them, don't pick silently; if unclear, stop and ask.
- **Simplicity first** — minimum code that solves it; no speculative features/abstractions/config/error-handling. 200 lines that could be 50 → rewrite.
- **Surgical changes** — touch only what the task needs; match existing style; don't refactor what isn't broken; flag unrelated dead code, don't delete it. Every changed line traces to the request.
- **Goal-driven** — turn the task into a verifiable check (bug → failing test that reproduces it → make it pass), then loop to green. Pairs with "closed trades only" / verify-on-next-live-cycle.

## Phase 1 — DONE (code) / blocked on data
- Found: dataset = 393 rows but only **59 distinct markets** (7x pseudo-replication → leaked AUC).
- Fixed `ml/train.py`: group-aware split by condition_id + explicit PRICE-ALONE baseline + DIRECTION edge test (only saves a model if it beats price).
- Fixed `ml/collect_history.py`: caps 3 samples/market, targets 400 distinct markets.
- RAN on the Mac (run_phase1.command): collected **363 distinct markets**, 1139 rows.
- **RESULT (surprising — candidate edge found):** group-aware DIRECTION test:
    price-alone AUC = **0.752** (the earlier 0.92 was a small-sample artifact)
    model AUC       = **0.819**
    EDGE (model - price) = **+0.067**  → the model beats the market at predicting outcomes.
  (move_auc = 0.903 too, but that's volatility/timing, not direction.)
- Candidate-edge model saved to ml/model.pkl (target=final_outcome).
- CAVEATS: AUC edge ≠ profit (must beat spread); one split / 363 mkts (needs cross-val / fresh period); watch for subtle feature leakage.

## Phase 3 — DONE (encouraging): `ml/backtest_edge.py` + run_phase3.command
- Held-out (group-split) backtest, spread charged:
    DIVERGENCE strategy: **+$0.116/trade, 69% win** (55 trades, 73 held-out mkts)
    ALLIN control:       **-$0.140/trade**  (correctly negative → accounting is honest)
- The +0.067 AUC edge CONVERTS to dollars out-of-sample and beats the control by ~0.26/trade.
- NOT yet proof: 73 mkts, single collection era; 55/73 triggered (model diverges a lot — scrutinize).

## NEXT (the only thing that confirms it)
- OUT-OF-TIME test: collect a FRESH batch of markets resolving AFTER today, re-run backtest_edge.py.
  If +0.116 holds on a different period → real. If it collapses → it was a regime artifact.
- Watchdog already runs a daily ML retrain (collect+train); consider also auto-running the backtest.
- Phase 4 (tiny real money) is GATED on the out-of-time confirmation. Do NOT skip it.

## Live: edge_trader.py = THE house strategy (ML divergence), paper
- Trades where model P(YES) diverges from market price by >0.10. Runs as a DETACHED
  daemon (watchdog keep-alive); NOT inline (inline blocked the watchdog → false bot
  relaunches; fixed). Dashboard card = "EDGE·ML". State: edge_trader_state.json.

## Recalibration done (2026-05-31): isotonic, FrozenEstimator (cv="prefit" removed in sklearn>=1.6)
- Brier 0.208 -> 0.192 (better). One market fixed (US-Iran nuclear 0.014->0.176).
- STILL heavy NO-bias: most markets ~0.000 incl. Vegas 0.41 -> 0.000. Verdict ⚠️ not ❌.
- Edge THINNER now: +0.022 AUC (was +0.067) — calib split shrank base training data.
- Read: model is a "fade longshots to NO" engine. Real basis (favorite-longshot bias) but
  overconfident + high-variance on true favorites. MARGINAL edge, NOT a money-maker.
- Calibrated model saved to ml/model.pkl. edge_trader needs a WATCHDOG RESTART to load it.

## OPEN RISKS (verify on Mac before trusting edge_trader live)
1. Model is OVERCONFIDENT: outputs ~0.004 P(YES) on markets priced 0.41 → bets NO on
   real favorites. Either the favorite-longshot edge, or miscalibration. Watch closely.
2. Train/serve skew: predict.py was "1w" history, training was "max". FIXED to "max"
   (applies on next edge_trader daemon restart). MUST verify predict.py now outputs
   SANE probabilities (run on Mac: `python3 ml/predict.py <condition_id>`).
3. Live universe = 30-day markets only; backtest was all-horizon. Live edge may differ
   from the +0.116 backtest. Treat live as a fresh test, not a confirmation.

## Still TODO
- Investigate why `allin` (buy-everything control) shows POSITIVE paper P&L — marking/fill optimism bug in scalp_lab; fix before trusting any paper P&L.
- After predict.py verified sane: let edge_trader accumulate 20-30 closed trades, compare to allin control.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).

### Shared real-time memory — every agent

There are TWO graphs; keep them straight:
- `graphify-out/graph.json` — THIS repo's **code structure** (local). Use for codebase/architecture questions.
- `~/.graphify/global-graph.json` — the LIVE cross-session **second brain** (BRAIN.md + vault concepts + wired findings: lessons, strategy graveyard, arb track, the Gate, etc.). Shared by ALL agents, updated in real time, **never mirrored** into the vault or baked into agent files.

Every agent — main loop, subagents, and the launchd fleet — reads strategy/lesson/decision memory from the live brain **in real time** via the `brain` wrapper (on PATH), not from a snapshot or a vault copy:
- `brain "<question>"`        — live BFS context from the shared brain
- `brain path "<A>" "<B>"`    — relationship between two concepts
- `brain explain "<concept>"` — focused explanation of one node

`brain` always reads the current `~/.graphify/global-graph.json` (reads only `~/.graphify`, so launchd stays TCC-clear). `BRAIN.md` still supersedes on direct conflict. Do NOT mirror brain content into the vault or into agent files — query it live.
