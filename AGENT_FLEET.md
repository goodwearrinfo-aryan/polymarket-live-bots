# Agent Fleet — registry & wiring (Polymarket bot)

Single source of truth for the agent fleet. Four tiers + 24/7 workers, all on a free
backbone. **Discipline (2026-06-20 lessons): facts stay facts; verify every cheap/free-model
conclusion. Default verdict is NULL — the venue is calibrated.**

## Flow
```
trigger → GATHER (thrift · info-peek) → DECIDE (peek-synth · edge squad) → verdict → human acts
                                  └────────── free model: minimax-m3 → Ollama ($0) ──────────┘
```

## Tier 1 — Thrift (grunt-work, cheap reads)  [10]
`brain-lookup` `state-peek` `launchd-peek` `log-peek` `find-code` `git-peek` `freshness-check` `calc` `vault-scribe` `memory-scribe`
→ Use for routine chores; keeps big reads/writes off the expensive model.

## Tier 2 — Info-peek (fetch ONE external fact)  [12]
`price-peek` `market-peek` `news-peek` `chain-peek` `research-peek` `venue-peek` `vol-peek` `funding-peek` `events-peek` `whale-peek` `oddpool-peek` `basket-peek`
→ Use when you need a live fact (price, executable book, news, vol, funding, calendar, whale, cross-venue, basket completeness). Keyless-first; `market-peek`/`basket-peek` use the EXECUTABLE book, never the mid.

## Tier 3 — Decision capstone: `peek-synth`  [1]
→ Gathers the relevant peeks for a question and returns ONE honest answer, splitting **VERIFIED FACTS** (deterministic fetches) from **CONCLUSIONS → verify**. Entry point for "answer this question."

## Tier 4 — Edge squad (hunt → verify → verdict)
- **Hunt** (4 mechanisms): `edge-logic-hunter` (structural) · `edge-resolution-hunter` (reading) · `edge-data-hunter` (settled-mispriced, the one proven vein) · `edge-newmarket-hunter` (timing)
- **Conduct**: `edge-hunt-orchestrator` — or the **`edge-hunt` skill** (the entry point; runs the whole pass)
- **Verify**: `edge-verifier` (pre-entry, one candidate) · `pm-lock-auditor` (post-entry basket book sweep) · `basket-peek` (completeness check)
- **Verdict**: `edge-verdict` → EDGE / ACCUMULATE / NULL (dollars-first, self-deception guards)

## Workers — 24/7 (launchd, on the free model)  [4]
`com.aryan.night-brief` (hourly) · `com.aryan.catalyst-watch` (30 min) · `com.aryan.survivor-watch` (hourly) · `com.aryan.edge-scan` (6 h, deterministic, alert-only)
→ write the vault + iMessage; quiet 23:00–07:00 IST. Scripts: `night_brief.py`, `catalyst_watch.py`, `survivor_watch.py`, `edge_scan.py`, shared `worker_lib.py`.

## Learning loop (closed 2026-06-20)
- **Fast loop** — `edge_ledger.py`: `edge-verdict` records every verdict + kill-reason; `edge-hunt-orchestrator` reads `edge_ledger.py patterns` before each hunt to pre-empt repeated false-positives (hallucination · mid-mirage · partial-field · fee-myth · parsing-bug · …).
- **Slow loop** — `edge_ledger.py settle/scorecard`: EDGE/ACCUMULATE verdicts carry `market`+`p`; once a market resolves, the verdict is Brier-scored (does an EDGE call actually pay?).
- **One brain** — `edge-verdict` consults `arb_memory.json` realization ratios before EDGE.
- **Governance** — `fleet_audit.py`: diffs disk + workers vs THIS registry; run it after any fleet edit. Related deep analyst (not a peek): `vol-hedge-analyst`.

## Backbone
`llm_client.py`: **nvidia / minimaxai/minimax-m3 → Ollama (llama3.1:8b) floor**. $0 keyless judgment; powers every judgment step above. NVIDIA 429 under load → auto-falls-back to local Ollama.

## Orchestration skills (the "for my own use" layer — already built; invoke, don't duplicate)
These fan out the agents above into one synthesized result — reach for the SKILL, not its parts.
- **`fleet-health`** — read-only ops sweep (book·jobs·services·freshness·signals·arb) → one brief. Morning / after-deploy / "is it all working."
- **`arb-lock-vet`** — open basket-book integrity sweep (pm-lock-auditor → edge-verifier) → true locked-edge verdict.
- **`leg-graduation`** — full n≥30 gate for ONE leg (DSR · capacity · correlation · regime). `args = {leg}`.
- **`edge-hunt`** — discover→verify→verdict edge pass (the edge squad, end-to-end).
- **`venue-edge-sweep`** — 4× pm-venue-scout → pm-venue-ranker (find a thinner venue).

## Routing table — what to reach for
| You want… | Use |
|-----------|-----|
| a chore (state/logs/jobs/calc/write a note) | the matching **Tier-1** thrift agent |
| one external fact | the matching **Tier-2** peek |
| an answer to a question (multi-fact) | **peek-synth** |
| "is there an edge / hunt the book" | **`edge-hunt` skill** → edge-verifier → edge-verdict |
| vet ONE candidate | **edge-verifier** → **edge-verdict** |
| continuous monitoring | the 3 **workers** (already running) |

_Built 2026-06-20. Heavy judgment runs free; only thin orchestration touches the paid model._
