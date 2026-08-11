# Polymarket-Live — Structural-Arb Research & Paper System

Research monorepo for Polymarket trading. After ~95 evaluated strategies, the honest
verdict is recorded in `CLAUDE.md`: **predictive taker legs die to calibrated mids.**
The only edges that structurally survive are non-predictive **structural arbs** — market
structure mispricings whose profit does not depend on forecasting an outcome.

This repo is the **source + docs** mirror of the live research workspace
(`~/polymarket-live`). Runtime state (dbs, logs, `*_state.json`, `.jsonl`) is intentionally
excluded — it lives only on the local machine.

## Hard Rules (whole workspace)

1. **Paper only** — no real orders, no API keys in code, no fund movement.
2. **Optimize dollars, not win rate** — win rate is vanity.
3. **Controls must lose** — if allin/coinflip controls win, the accounting is buggy.
4. **≥30 closed real exits AND bootstrap CI > 0** before claiming an edge graduates.
5. **Don't churn config on mood** — each change resets the experiment.

## The Live System

Everything runs on a watchdog loop (`watchdog_loop.sh`, launchd-managed). The active
structural-arb legs, all **paper**, hold-to-resolution:

| Leg | File | Edge type | Status |
|---|---|---|---|
| Basket arb | `basket_arb.py` / `basket_paper.py` | Locked combinatorial field (Σask < 1) | accumulating n |
| Data arb | `dataarb.py` + `analyst_data_gate.py` | Settled-but-mispriced (resolving series already determined) | accumulating n |
| Mono arb | `monoarb.py` | Monotonicity/consistency violations | accumulating n |
| Cross-venue | `xvenue_arb.py` | Same event cheaper on Kalshi vs Polymarket | scanner |
| Combined | `multiarb.py` | Theme-clustered combined CI over the arbs | accumulating n |

Fatal-to-track: **zero real exits booked yet** — every leg is in accumulating mode, waiting
on resolution. Control legs are properly negative (accounting honest).

## Key Docs

- `CLAUDE.md` — project memory; "Where we are — EXPERIMENT CONCLUDED" + do-not-relitigate list
- `BRAIN.md` — canonical working brain (lessons, graveyard, edge track)
- `WORKFLOW_AND_BRAIN.md` / `README_WORKFLOW.md` — workflow / runbook
- `GRADUATION_PROTOCOL.md` — the n≥30 + CI > 0 gate that graduates a leg
- `PROJECT_MAP.md` — file map

## Local Operations (in `~/polymarket-live`, not this mirror)

```bash
python3 multiarb.py once          # combined structural-arb read (basket+data+mono)
python3 basket_paper.py --report  # basket book + stats
python3 dataarb.py once           # data-arb leg
python3 monoarb.py once           # mono-arb leg
python3 scalp_lab.py once         # single scalp scan (legacy; all dead)
./watchdog_loop.sh                # keep-alive daemon (launchd managed)
```
