---
type: hub
updated: 2026-08-12
---

# POLYMARKET BOT — WORKING BRAIN
Canonical. Curated sections hand-maintained; LIVE STATE auto-rewritten by
brain_update.py (launchd, 30 min). Read FIRST each session. Supersedes the
bot-brain skill. Shorthand decoder: memory/glossary.md (this file wins on conflict).

**System Design**: See [BRAIN_DESIGN.md](BRAIN_DESIGN.md) for complete inference pipeline,
validation gates, edge mortality model, and bet-sizing formula.

## Mission & Hard Rules (never override)
- PAPER ONLY. No real orders, no trading API keys, no funds. Ever. *(Scope: Polymarket binary markets. The crypto trader at `~/trader` is a SEPARATE system with its own LIVE_TRADING=1 opt-in; see [[Bot/crypto_trader]] + [[memory/crypto-trader-2026-06-22]].)*
- Goal: ONE proven edge — ≥30 priced exits + bootstrap CI>0 + DSR + controls negative.
- Optimize DOLLARS, not win rate. Controls (allin, coinflip) MUST lose.
- No config churn: filter/thesis bugs fixable anytime; band/stop tuning needs n≥5.
- Never re-enable edge_trader. Never start `scalp_lab.py run` (races Postgres).

## Architecture
1. State = PostgreSQL (db.py, lab_trades, DELETE+rewrite). scalp_lab_state.json = mirror.
2. Bot = `scalp_lab.py once` via watchdog_loop.sh every ~60s; config edits live next cycle.
3. Resolved markets vanish from gamma; fetch_price_clob() prices them (else exits censor).
4. Attention caches persist in scalp_lab_cache.json (in-memory dies between once-runs).
5. State surgery: pause watchdog first, else the next save clobbers it.
6. pnl_baseline.json = balance reset 2026-06-10 (lifetime was −$159). Dollars since reset.
7. PandaScore live (.pandascore_key): pandascore.py — match status feed; gates psconfirm,
   logs ps_status on nearres. OpenAlice local API :47331/api/news — alicenews/quietfade/panel.
   hermes_news.py :47333/api/news (2026-06-12, Hermes OSS port) — keyless 18-RSS-feed
   sibling; quietfade merges both feeds so neither is a single point of failure.
8. lab_api.py = read-only REST :8787 (/v1/status, /v1/legs, /v1/health). GET-only.
9. **belief_ledger.py** (2026-06-15): append-only forecast + calibration ledger. Records
   every entry (p = entry_fill, the de-vigged belief) + settlement outcome (0/1) to
   belief_settlements.jsonl. Run `python3 belief_ledger.py [leg]` for Brier + reliability
   deciles per leg — calibration check that win-rate can't give. All legs record automatically.
10. **sports_data.py** (2026-06-15): keyless ESPN all-sports results feed. Pulls 30d
    every day 5:55am (before bot 6am), 630 events, 12 leagues. Queryable by bot:
    `sports_team_form("TOR")` → [wins, losses] (team form covariate),
    `sports_match_result(league, home, away, date)` → winner (resolution confirm).
    Wired into scalp_lab.py as _sd global; available to any leg.
11. **obsidian_snapshot.py** (2026-06-15): auto-mirror bot state to vault every 6h
    (6:15am, 12:15pm, 6:15pm, 12:15am). Writes ~/Documents/PolymarketVault/Bot/:
    belief_calibration.md (Brier + reliability per leg), sports_summary.md (30d leagues),
    brain_snapshot.md (live excerpt). One source of truth: this BRAIN.md; vault is read-only live mirror.
12. **WhatsApp alerts via OpenWA** (2026-06-15): local gateway (oss-bots/OpenWA, NestJS+whatsapp-web.js)
    on 127.0.0.1:2785; `wa_alert.py` sends fail-soft (down/unlinked → returns False, never raises).
    Wired into hourly_pnl_alert.py + analyst_scorecard.py (dual-send w/ iMessage). Key at
    ~/Library/Application Support/PolymarketBot/openwa.key (NOT synced); config wa_config.json (non-secret).
    Session `polybot-alerts` 129bf70e…; ONE-TIME QR scan with a BURNER number (whatsapp-web.js = unofficial,
    ban risk), then AUTO_START_SESSIONS=true reconnects forever. Persistence plist (com.aryan.openwa) =
    user-install (classifier blocks agent plist installs). Full doc: vault "WhatsApp Alerts (OpenWA)".
13. **5-edge scan** (2026-06-15): edge1_flb..edge5_maker.py + edge_common.py (keyless gamma/kalshi
    helpers) — probed edge types immune to the 3 killers (gap-through/spread/calibration).
    Survivors: #4 basket arb (LEAD — ⚠️ RETRACTED 2026-06-21, mark-to-mid+gross-fee fiction, 0 verified live; see #14), #1 FLB hold-to-res (unconfirmed, forward-test). Dead on this
    venue: #3 stale-resolution (gamma resolves fast), #5 maker (liquid book median spread 0.1¢).
    #2 xvenue needs a SEMANTIC matcher (lexical = false "win" vs "occur" matches). Full: EDGE_SCAN_FINDINGS.md.
14. **basket_arb.py** (2026-06-15): REUSABLE combinatorial-arb engine — the lead survivor. Mutually-
    exclusive (negRisk) events where Σask(YES)<1 = locked profit, immune to all 3 killers (no view,
    no stop, settles 0/1). EXHAUSTIVENESS FILTER: trust a lock only if Σmid∈[0.93,1.08] (complete
    field) + every leg two-sided — kills incomplete-field artifacts (Lebanon Σmid=0.15). Public API
    any leg/brain imports: `get_locked_baskets(min_edge)`, `best_basket_edge()` (live float), 
    `snapshot_basket()`. ⚠️ **2 verified locks live (2026-06-20, was 16)** — the rest were partial-field
    fakes; Tennessee Σask=0.971 is a FAKE (2 of 13 pool legs priced, see EXHAUSTIVENESS RETRACTION at #15). Snapshots to
    vault/Bot/basket_arb.md (obsidian_snapshot --only basket).
    ⚠️⚠️ **RETRACTION 2026-06-21 (arb-lock-vet audit)**: "basket arb = LEAD +EV survivor" was MARK-TO-MID
    fiction. TWO bugs fixed in basket_arb.py: (BUG 1) it gated on gamma Σmid≈1, but the live CLOB is
    illiquid — the famed "+4% BTC/Gold/SPX lock" has live Σask≈0.96 (or 0.99 phantom), so it was never
    executable; `verified` now re-prices on the LIVE CLOB book. (BUG 2) the gate used GROSS edge — the
    same +4% lock nets **−0.2%** after the crypto taker fee (rate·p·(1−p)/leg, schedule from basketlock.py:6-9,
    geopolitics FREE); `edge`/`verified`/`best_basket_edge()`/basket_paper booking now all use the NET-of-fee
    LIVE edge. **Live re-scan 2026-06-21: 0 verified locks across 366 negRisk events, best_basket_edge()=0.000.**
    The lead arb edge does NOT survive honest executable+net pricing. Only settled-but-mispriced DATA arb
    (Hormuz/PortWatch) has ever truly won. See memory [[workflow-library-2026-06-21]].
15. **basket_depth.py** (2026-06-15): depth+exhaustiveness VERIFIER (basket_arb screens, this confirms).
    CAVEAT FROM #14 RESOLVED: every leg's CLOB book carries `neg_risk=true` → exhaustive BY PROTOCOL
    (Polymarket neg-risk adapter makes NO==Σ other YESes, so NO untracked outcome can sink the lock).
    The Σmid<1 gap was overround/spread, not a missing tail. 8/8 top locks confirmed exhaustive.
    ⚠️ **EXHAUSTIVENESS RETRACTION (2026-06-20) — this caveat was the BUG'S SOURCE.** The negRisk POOL
    is exhaustive (ground-truthed: Tennessee = 13 legs, ONE negRiskMarketID 0xd8ffa0dc…), BUT the bot
    prices+buys only the LIQUID subset (2 of 13); the 11 unpriced placeholders ("candidate not listed
    above" etc.) are REAL pool members carrying the residual tail — so the bought subset IS sinkable
    (an omitted outcome winning zeroes both bought legs). Σmid≈1 over the RETURNED legs ≠ complete set
    → it admitted PARTIAL-FIELD fake locks (Tennessee 2/13, Minnesota 2/13, French 36/128). FIX: `scan_baskets`
    now requires `complete_field` (priced legs == full non-closed pool via `edge_common.n_markets_total`).
    IMPACT: live verified 13→2; paper book 56/64 voided as fakes (→ book['voided']); "$17.5 pending" was
    ~$45.90 fiction + $7 real; the n=1/30 "CI>0 structural" is SURVIVORSHIP (one lucky NY-08 win). basket-arb
    is NOT the proven +EV lead it was claimed. Backups `*.bak-exhaustfix-2026-06-20`. The "8/8 confirmed
    exhaustive" + "Minnesota +1.8% to $1k" claims below are RETRACTED — those were fakes.
    ⚠️ **FEE CONTRADICTION RESOLVED (2026-06-20):** Polymarket fees are NEITHER "zero" (old basketlock.py
    claim) NOR "~2%" (the oddpool/CLAUDE figure). Since 2026-03-30: MAKER 0% + daily rebate; TAKER =
    `feeRate·p²·(1−p)` per share (peak 1.8% at p=0.5, →0 near 0/1; **GEOPOLITICS/world-events FREE**;
    politics/finance 0.04, econ/culture/weather 0.05, sports 0.03, crypto 0.07). A real complete-set
    lock's taker haircut is ~0.5–1% summed (longshot legs ≈0) — genuine locks stay net-positive (verified
    2026-06-20: 8/8 booked, live Bitcoin-vs-Gold +3.16% net of taker fee). So FEES are SECOND-ORDER; the
    EXHAUSTIVENESS bug above is what made basket-arb mostly fiction, not fees. basket_arb/basket_paper
    still report GROSS (maker-equivalent) edge — subtract ~0.5–1% for taker fills, 0 for maker fills.
    REAL CONSTRAINT = DEPTH + LOCKUP: edge is real but thin — locks ~+1–2.5% at $50 payout, mostly
    GONE by $200, NEGATIVE by $1k (ask ladder walks past the lock). Best: Minnesota Gov +1.8% holds
    to $1k (+$16.86). Capacity ~$50–200/event; capital locked till resolution (elections = months →
    annualize before celebrating). VERDICT: basket arb is a REAL, exhaustive-confirmed, LOW-CAPACITY
    edge — legit CI>0 candidate at small size, not a scalable money machine. vault/Bot/basket_depth.md.
16. **basket_paper.py** (2026-06-15): PAPER TRACK RECORD for basket_arb — the complement to the
    leg cull. Enters every verified lock once ($50 payout notional), holds to resolution, books the
    locked edge → accumulates the n≥30 real-resolution record that graduates basket arb. A vanishing
    EDGE ≠ resolution (lock held at entry). CI>0 is STRUCTURAL for locks, so the record proves
    PERSISTENCE/RECURRENCE/capacity, not edge-sign — its REAL job is catching a lock that DOESN'T pay.
    SETTLEMENT-VERIFIED (done 2026-06-15): resolution = all legs' markets `closed` via
    /markets/{condition_id} (the live /price?token_id 404s once the book is gone); realized =
    $50×(Σsettled_YES − Σask) for LONG / $50×(Σbid − Σsettled_YES) for SHORT — so a FAILED field
    (0 winners) books −$47.50 with lock_held=False, not fake profit. condition_id threaded
    edge_common.poly_events → basket_arb legs → here. Verified: resolved US-Iran→YES 1.0, clean lock
    +$2.50. Snapshots vault/Bot/basket_paper.md + WhatsApp/iMessage settle-alert (flags anomalies),
    both fail-soft. State basket_paper_book.json; watchdog %30 → basket_paper.log. 16 locks, ~$17.5
    pending. NOTE: short locks (Σbid>1, e.g. OH-09) are VALID, not artifacts — kind-aware output.
17. **analyst_data_gate.py** (2026-06-16): v5 of the analyst track. After v3+v4 (23 markets, 0
    booked under argument-only panels), the lesson was "only HARD DATA produces an edge." This
    INVERTS the funnel: a market earns analysis ONLY if its resolving series is fetchable, and it
    pulls the actual data FIRST. Implemented sources: crypto-threshold (ccxt daily klines, BTC/ETH/
    SOL/XRP/DOGE "dip to/reach $X") + portwatch (IMF PortWatch ArcGIS 7d-MA) + fed (NY Fed TGCR
    proxy) + **bls (WIRED 2026-06-19**: keyless POST to api.bls.gov v2 — unemployment LNS14000000
    direct %, CPI-YoY computed from CUUR0000SA0 index ratio, nonfarm payrolls CES0000000001 MoM Δ;
    BLS is the NAMED resolver so resolution-identity risk is LOW. Two guards: RELEASE-CALENDAR — if
    the market's target month isn't published yet, `already_triggered=None`/`released=False` and it
    NEVER fires (the print IS the unresolved event); NEAR-THRESHOLD — within a per-series buffer
    (unrate 0.05/CPI 0.10/payrolls 25k) the arb is SUPPRESSED because a revision could flip it;
    YEAR-WITHOUT-MONTH (added 2026-06-19, commit 1efe670) — a title giving only a year ("unemployment
    in 2026 exceed 4%") is an annual-average/any-month SPAN; a single monthly print can't resolve it,
    so it returns released=False/already_triggered=None and never fires (snapping to the latest month
    would be a resolution-identity mismatch = the #1 leg killer). Month-specified + no-date cases
    unchanged.
    Verified live: unrate May'26=4.3%, CPI-YoY=4.25%, payrolls May=172k; release-gate confirmed on
    a future month; near-threshold confirmed suppressing payrolls 172k vs 150k thr. 25 queries/day
    keyless cap respected via per-run series cache. lmarena = DEAD source, stays dark). Stub left: none.
    Core output = per-market "data dossier" {current, realized min/max IN-WINDOW, already_triggered?,
    distance%}. Flags the LAGGING-MARKET ARB (data says resolves-YES but YES trades <0.95 = settled-
    but-mispriced, the cleanest data edge). TWO BUGS FOUND+FIXED via live verify: (a) start-date must
    query gamma by `condition_ids` NOT `id` (0x cid ≠ numeric id → silent None → wrong window); (b)
    ccxt `since` is a hint — MUST explicitly clip bars to [start,now] or the pre-window ATH leaks
    (falsely marked BTC "reach $100k" already-triggered when window-max was really $97.9k). Wired:
    watchdog %30 (180s timeout) → analyst_data_gate_log.jsonl + Vault/Reports/analyst_data_gate.md;
    fail-soft WhatsApp+iMessage on ARB only. First live scan: 61 matched, 27 data-ok, 0 arb today.
    Richest find = Hormuz term structure (4 deadlines, same MA7=5.14 data, prices 0.185/0.003/0.605/0.905).

18. **oddpool_multibot.py** (Codex/is-git-installed/, 2026-06-16): Multi-profile Oddpool orchestrator
    — runs all strategies in ONE process sharing ONE quota budget. Deployed as com.aryan.oddpool-bot
    every 12h (2 runs/day ≈ 16 live calls/run, budget guard hard-stops at 900/mo). 4 profiles:
    arb-hunter (arb+diff, alerts ≥2¢), whale-tracker (≥$1.5k prints, alerts), crypto-watch (BTC/ETH
    divergences, silent), new-markets (recent event discovery, silent). KEY ADVANTAGE over vanilla
    Oddpool premium: RECONSTRUCTS arb+whale from FREE data (no premium/pro plan needed) via
    OddpoolClient engine in oddpool_bot.py. Verified live 2026-06-17: whale-tracker found 6 prints
    $2.3k–$5.9k; budget 787/900 remaining. Config: oddpool_multibot.config.json (edit profiles
    there). Vault: Vault/Reports/oddpool_multibot_digest.md. NOTE: ~/Documents/polymarket/
    oddpool_multibot.py is a SUPERSEDED stub (skips premium endpoints instead of reconstructing) —
    ignore it. SIGNAL USE: Trump-out whale at $5.9k NO @0.89 → cross-check against analyst track.

19. **analyst_book.py + analyst_agent.py** (2026-06-17): the analyst PAPER book (analyst_positions.json)
    is now LIVE-MONITORED 24/7. `analyst_book.mark_book()` is the shared marking path: marks every
    position to live, and BANKS+locks any newly-resolved one. CRITICAL FIX (stale_nodata class):
    resolved markets vanish from gamma → the book showed a WIN as flat $0.000 (US-Iran ceasefire YES
    @0.735 had resolved YES=+$0.265 but read as 0). Now falls back to CLOB /markets/{cid} (winner
    flag) and writes status:settled + realized_pnl back to the JSON so the win survives even if CLOB
    later drops it. Book splits realized vs unrealized (was −$0.199 flat → actually +$0.066: realized
    +0.265 / unrealized −0.199). **analyst_agent.py** = the 24/7 agent (launchd com.aryan.analyst-agent,
    900s, RunAtLoad): each cycle marks the book, diffs vs analyst_agent_state.json → EVENTS (RESOLVED /
    ADVERSE ≥0.08 unreal drop / EDGE-GONE = market converged to our true_prob), runs ONE free-NVIDIA-LLM
    judgment per event position (intact|weakening|broken → hold|re-vet|note; steady-state = 0 LLM calls
    = quiet), alerts iMessage+Obsidian on events only, reports correlation concentration (corr.driver_of).
    Outputs: analyst_agent_log.jsonl + Vault/Reports/analyst_agent.md. Live-verified: exit 0, flags
    "4 open positions share driver mideast_peace_tempo". NOTE: legacy com.aryan.bot-analyst (bot_analyst.py,
    300s) is a DO-NOTHING stub (runs on empty analyst.json, markets=[]) — analyst_agent supersedes it.

20. **Book gate-integrity (vet_book.py + agent gate-fail + scorecard cohorts)** (2026-06-17): the book had
    UN-GATED positions (a parallel/autonomous session booked them without the panel — e.g. Hormuz "end of
    June" NO added in commit eb4e3b0). `vet_book.py` re-vets EVERY open position through the 3-lens panel and
    writes a `gate` block {survived, hard_refuters, lenses} back per position — NEVER removes any (pruning
    pre-resolution = survivorship, Lesson 13). First run (2026-06-17): **3 survived / 3 REFUTED** — REFUTED:
    BTC dip $57.5k YES (3/3, genuinely dead — resolution-misread@100 + overconfident vs mkt 0.136), Brazil
    Renan Santos NO (2/3, correct-side-but-priced), Hormuz end-of-June NO (2/3, correct-side-no-edge +
    redundant w/ the July-31 one). SURVIVED: Iran-enrichment NO (0/3), Hezbollah NO (0/3), Hormuz **July-31**
    NO (1/3, the data edge holds). Enforcement is now LIVE: analyst_agent.py flags every ungated/refuted open
    position as GATE-FAIL each cycle + iMessages once when a NEW one appears (catches any future bypass within
    15min). analyst_resolution_scorecard.py is now COHORT-AWARE: reports a "GATED TRACK ONLY" sub-score
    (panel-survivors) as THE June-30 verdict, with un-vetted bookings excluded from the edge claim (shown for
    transparency). So an ungated lucky win can't pad the milestone. **AUTONOMOUS (2026-06-17):** vet_book is
    scheduled — **com.aryan.analyst-vet** (StartInterval 12h, RunAtLoad) re-vets the whole book unattended.
    `gate` is the FROZEN entry-equivalent verdict (set once, never overwritten — the scorecard reads it, so a
    near-resolution re-vet can't peek at outcome-correlated data and retro-reassign the cohort = look-ahead
    guard); each run also refreshes `gate_live` (informational drift). `vet_book.py --refreeze` = manual
    override to reset the frozen gate. Self-maintaining loop: DETECT (analyst-agent 15min flags ungated/refuted)
    → JUDGE (analyst-vet 12h assigns/refreshes verdict) → SCORE (scorecard reads frozen gate at resolution).
    **AGENT-STACK RECONCILE (same day):** canonical live judgment path = `llm_client`(NVIDIA
    meta/llama-3.3-70b-instruct) via pm_pipeline.py + standalone tools (refute_edge / check_resolution /
    vet_book) + DETERMINISTIC math (dsr_gate.py, analyst_correlation.py). `pm_agents_24x7.py` = DORMANT alt
    (no launchd, not running); already math-safe (g_dsr pre-computes the gate in Python, LLM only narrates).
    Do NOT schedule it as a second stack — pm_pipeline is the one system.

21. **Autonomous logger-interpretation + self-heal layer** (2026-06-17): the loggers self-fill; this tier
    INTERPRETS them on schedule. `sentinel_sweep.py` = deterministic (no-LLM, zero-cost) twin of the 3
    read-only logger sentinels — xvenue (oddpool history tx-dedup recurrence / xvenue divergence / basket
    locks / funding), dataedge (analyst_data_gate alerts + news_arb edge), knowledge (candle EFFECTIVE-n =
    distinct det_t, multi-regime gate). Wired into watchdog_loop.sh at `%30 -eq 15`, quiet steady-state,
    loud+deduped (.sentinel_seen.json) only on a real signal; verified firing. On-demand twins = agents
    pm-{xvenue,dataedge,knowledge}-sentinel. `analyst_correlation.cluster_block_bootstrap_ci()` resamples
    whole DRIVER clusters → honest effective-n CI, wired into BOTH scorecards so the June-30 milestone can't
    claim "ANALYST BEAT MARKET" off correlated mideast draws (single-cluster → degenerate, refuses a CI).
    **news_arb now runs AUTONOMOUSLY (2026-06-19):** `news_arb.py --scan --n 6` was on-demand only (single
    `--cid`); added a scan driver that picks its OWN candidates (liquid ≥$100k + ≥2 matching hermes headlines +
    not near-certain 0.05<YES<0.95), judges up to 6 on the free LLM, logs + writes Reports/news_arb_latest.md,
    iMessages ONLY a real edge (edge+timing_ok+conf≥70, dedup news_arb_scan_state.json). Wired watchdog_loop.sh
    2h-throttle (`.last_news_arb`). NOTE: every news *taker* leg is dead (alicenews/wcnews/panel/newsno, calibrated
    mids) — this is NON-predictive (is a settled catalyst mispriced?), the only news path that isn't a known null.
    Live verify: 192 candidates, 3 judged, all already_priced@100 / 0 edges = the EXPECTED efficient-market steady
    state. Watchdog bash-loop body change → activates on next watchdog RESTART (the --scan Python is live now).
    `pm-watchdog-warden` agent = on-demand watchdog heal (see Lesson 17: nohup-relaunch, never the plist).
    lmarena data-gate source: keyless `mathewhe/chatbot-arena-elo` fetcher built + 21d freshness guard, but
    that mirror froze 2025-07-18 → driver stays DARK (no fake arbs from stale rankings), auto-revives if it
    resumes. oddpool_multibot.py also writes append-only oddpool_multibot_log.jsonl (history the digest lost).

22. **Autonomy self-supervision LIVE in the watchdog (2026-06-19)** — the verify→solve→heal trio +
    night-analyst (were `~/.claude/agents/pm-autonomy-*.md` + `pm-night-analyst.md`, Claude-subagent-only
    = NOT runnable from a bash loop) are now ported to FREE-LLM Python runners wired into watchdog_loop.sh:
    `pm_autonomy.py heal` every 30min (`.last_pm_autonomy`) + `pm_night_analyst.py` every 6h
    (`.last_night_analyst`), both hot-reloaded in (no plist — Lesson 17). **pm_autonomy** = VERIFY (each
    autonomous worker's output fresh-within-cadence/non-empty/provenance-sane + the Ollama keyless FLOOR)
    → SOLVE (known-failure catalog; the LLM only NARRATES, it can't unlock a SAFE-AUTO beyond the
    allowlist, so it works fully offline) → HEAL (hard allowlist ONLY: restart `ollama serve` if down /
    move-aside a solver-named stale cache; heal-not-kill — NO pkill, NO plist load, NEVER relaunches the
    watchdog from inside itself, copybot_watchdog.sh owns that; Postgres-safe). On a healthy fleet solve
    finds 0 items → 0 LLM calls (free). **Also CLOSES the ollama-liveness gap** (FLOOR-DOWN verify +
    auto-restart). **pm_night_analyst** = cross-fleet dollars-first brief → `Vault/Reports/night_brief.md`
    (DETERMINISTIC fallback brief, stamped, if no LLM reachable — the night shift never goes dark);
    iMessage ONLY on a material flag (control-bug / lock-didn't-pay / gate-cross / floor-down), deduped.
    Verified firing live from the watchdog 2026-06-19 17:12. Artifacts: `pm_autonomy_ledger.json`,
    `pm_autonomy_state.json`, `pm_night_state.json`, `Vault/Reports/autonomy.md` + `night_brief.md`.

## Lessons (paid for in paper — do not relearn)
1. Cap target exits at limit fill (_honest_pnl) — overshoot booking made controls positive.
2. Never censor exits as None — CLOB-price them; censoring hid wins AND losses (265 records).
3. Long-dated markets don't move — fades need ≤30d resolution pressure (truefade, 6% WR).
4. Win-rate padding: 0.999 entries = risk $2 win $0.002. nearres_max_entry 0.95. WR ≠ EV.
5. Single-match end-dates LIE (padded days out): horizon legs gap 0.42→0.02 (cost ~$2.50,
   guard = is_live_match); resolution legs starve on the same lie — gate sports on
   gameStartTime, never endDate.
6. "Will NO X happen" inverts fades — block negated questions (newsno_skip_kw).
7. Compound theses need AND gates — flat OR keyword lists overmatch (btc15no).
8. 80% WR + 3:1 R:R is impossible at favorite prices (max gain 12¢−spread). Per-leg
   targets; optimize EV. High WR⟷favorites⟷small pay; 3:1⟷longshots⟷low WR.
9. Inverting a dead leg ≠ edge (spread paid both ways; inverse-newmarket = dead momentum).
10. Quarantine off-thesis records to <leg>_misfire; never delete history.
11. Local imports shadow module names for the WHOLE function (datetime UnboundLocalError
    killed every scan) — alias uniquely inside scan_entries.
12. DISK-FULL → PG-DOWN → STATE-WIPE (2026-06-14): disk filled → Postgres shut
    down → watchdog kept running scalp_lab.py → lab_load_state returns EMPTY on
    connection failure → save_state DELETE+rewrote DB+mirror with nothing (gate
    read 0/30). Recovered by restarting Postgres (data intact). GUARD added:
    main() aborts the cycle if _db._conn() fails. WATCH DISK: moves_log re-grows
    fast; iCloud oss-bots (12G) re-materializes locally — keep ≥3G free.
13. Backtests must model GAP-THROUGH (2026-06-14): a stop on an esports favorite fills
    at the realized post-gap mid (−45–65¢), NOT the −3¢ trigger. Booking stops at the
    trigger turned nearres OOS from −$0.088/exit (DSR FAIL) into a fake +$0.025 DSR-PASS.
    Same bug found in nearres_oos/_gamma/kalshi_oos. Sim fills must be ≥ as pessimistic
    as live, or the CI is fiction. See "nearres OOS edge RESOLVED" below.
14. SPREAD-CAPTURE / SCALPING IS A STRUCTURAL LOSER (2026-06-15 leg cull): microscalp
    ran n=413 at 0% WR, −2.3¢/exit, CI[−0.027,−0.020] — a scalper with no directional
    edge IS the spread-payer (every round-trip costs the bid-ask; calibrated mids leave
    no edge to recover it). spreadcap same pattern (n=25, −3.7¢). The program's "execution
    edge is sub-spread" proven at n=413. RULE: no spread-capture/scalping legs, ever.
15. TAKER-DIRECTIONAL LEGS ON CALIBRATED MIDS LOSE (2026-06-15 leg cull): panel(12%WR),
    peacefwd(0%WR), dipladder(0%WR) all took a directional view on liquid markets and bled
    (same root as the whole fade family + the FL-premium audit). On a calibrated venue a
    taker pays the spread with no edge to recover. RULE: stop adding taker-directional legs
    on liquid markets; only STRUCTURAL theses (basket/arb, genuine info edge w/ CI≥0) earn a
    slot. The scalp-lab leg bot is a CONFIRMED HONEST NULL — its remaining job is the
    marking-honesty control harness, NOT edge discovery. +EV lives in ANALYST + basket_arb.
16. WINNING LEGS ARE LUCKY LONGSHOT TAILS, NOT EDGE — APPLY CI TO WINNERS TOO (2026-06-15):
    the top "winners" are single-trade variance, not skill: geopolbomb +$28.23 (n=5, 67% from ONE
    11¢ Trump-blockade longshot), conviction +$3.82 (127% from one trade), alicenews +$3.15 (147%),
    ladderarb +$0.18 (681% from one 4¢ Iran-peace ticket). All cheap longshots (4–20¢) on the SAME
    Iran geopolitical cluster that happened to resolve YES this month. Positive-side variance of the
    identical null — longshots are calibrated (FL audit), so they regress with more trials. RULE:
    never promote/scale a leg on a lucky tail; a positive leg at small n with single-trade
    concentration is survivorship, not edge — gate winners at n≥30 + CI>0 SAME as everything else.
    The ONLY non-variance leg-type is the LOCKED ARB (basket_arb): profit independent of any outcome
    (buy all YESes <$1, one must pay $1). 17 live locks, top +4.9% (Warner Bros Σask=0.951). That,
    not the lottery legs, is where structural attention goes.
17. WATCHDOG RUNS AS A NOHUP ORPHAN — NEVER launchd (2026-06-17, cost a bot-down): launchd-spawned
    `/bin/bash` gets "Operation not permitted" (exit 126) reading ~/Documents — TCC, and it persists
    EVEN with /bin/bash granted Full Disk Access (TCC attributes to the responsible launchd job, not
    bash). Loading `com.aryan.scalp-watchdog.plist` thrashes at 126 and TOOK THE BOT DOWN (I did this;
    killed the working orphan, restored via nohup). `watchdog_loop.sh` is MEANT to run as a nohup
    orphan (ppid=1) — a SINGLE orphan = HEALTHY (one of the "2 known strays"); 2-3 transient
    `watchdog_loop.sh` pids mid-cycle are subshells that collapse in ~8s (real dup = ≥2 persistent
    ppid=1). Relaunch when dead: `nohup /bin/bash …/watchdog_loop.sh & disown`. The ONLY TCC-clear
    launchd path into ~/Documents is FRAMEWORK PYTHON (/Library/Frameworks/Python.framework/.../python3)
    → subprocess bash (probe-verified rc=0) — that's why copybot_watchdog.sh can self-heal it and why
    `$PY signal_alert.py` works. RULE: never load the scalp-watchdog plist; heal via nohup or the
    pm-watchdog-warden agent; never broad-pkill (kill explicit pids only).

## Deploy Protocol (non-negotiable)
0. Pre-flight: restate goal, sketch, hand-TRACE one market, name edge cases
   (shadowed names, padded dates, negated questions, None fields, empty feeds).
1. Atomic edit script: exact anchors, abort on miss-count, ast-parse before write.
2. Verify on the NEXT live cycle: mirror mtime advanced + clean log tail. ast ≠ runtime.
3. Silence ≠ success. Watchdog alerts → iMessage.
4. New-leg checklist: config + LEGS + entry + exit wiring + _honest_pnl map +
   alert SIGNAL_LEGS + brain registry.

## Decision Rules
- Kill: n≥10 + pnl<−$0.50 + WR<60%, or n≥5 + WR≤20% + negative.
- Tune (n≥5): WR<60% → tighten entries; good WR bad R:R → adjust exits (Lesson 8 limits).
- New legs: 9¢/3¢ default, $1-2 bets, judge at n≥10. Extend proven structures;
  independent signals only, no price costumes. Backtest-first when simulable.
- Gate at n=30 config-consistent: CI>0 + DSR (≈90 legs tried → raw CI>0 is ~90% luck).
- **Analyst-book concentration cap (2026-06-17)**: no single DRIVER cluster (analyst_correlation.driver_of)
  > ~40-50% of open analyst $ exposure. Book is currently 71% mideast_peace_tempo — a surprise
  Iran/Israel resolution sinks 4 positions at once. Correlated positions are NOT independent draws:
  count effective-n (cluster-block bootstrap), not row-count, for any CI/graduation. Source the NEXT
  analyst entry from a NON-correlated driver (crypto-native threshold, US-domestic, sports). Nested
  same-series pairs (e.g. Hormuz Jun-30 + Jul-31 NO) = ONE draw, not two. Don't force a diversifier
  if none is data-confirmed — wait for a real lead.

## Evidence (what's measured, 2026-06-12)
- **DSR PASSED (2026-06-12) — ⚠️ RETRACTED 2026-06-14**: was a clean-stop-fill artifact.
  Gap-honest re-price (Lesson 13) → esports_hi n=297 −$0.088/exit, CI [−0.129,−0.049],
  DSR −6.48 FAIL. Original (now void): n=103, wr=63%, CI [+0.0057,+0.0533], DSR +0.28.
- **2h window experiment (research finding A/D)**: n=19, wr=74%, $/trade +5.0¢ vs +2.9¢
  at 4h — directionally confirms underreaction peaks near resolution, but CI still
  includes 0 at this n. Accumulating; do NOT switch live config until n≥30 and CI>0.

- **nearres band [0.88,0.95] esports = the edge — ⚠️ RETRACTED 2026-06-14**: the OOS
  CI>0 and "triple-validated (p=0.008, 5/5 WF)" were all the clean-stop-fill artifact.
  Gap-honest re-price + re-run validation = NO EDGE (sign-flip p=1.0, bootstrap CI<0,
  walk-forward 0/5). See "nearres OOS edge RESOLVED" below.
- **Exit policy**: ride-to-settlement + 3¢ stop (+4.7¢/trade) > +9¢ target (+2.7¢)
  > no-stop (94% WR variance trap). nearres rides w/ stop since 2026-06-11.
- **Band floor real**: [0.80,0.88) OOS-negative (n≈98) — nearreslow killed.
- **Esports-only phenomenon**: same band on MLB/NBA/soccer OOS-negative; tennis the
  one positive sub-sport (+3.4¢, n=14) — sportres = tennis-only, late-game, kill at
  n=5 if WR<60%.

## Leg Registry
- **nearres** — ⚠️ edge RETRACTED 2026-06-14 (gap-honest OOS = NO EDGE; resolution section).
  Was LEAD. Esports favorites <4h, side-mid [0.88,0.95], rides w/ 3¢ stop. Paper-only, harmless to leave running.
- **psconfirm** — nearres entries only when PandaScore says match RUNNING (A/B).
- **nearrestitle** — nearres minus Dota2/handicap/tennis-leaks (Finding C: Dota2
  reverse-FLB, OOS 50% WR negative mean n=24; LoL CI>0 standalone). Added 2026-06-13.
- **sportres** — tennis-only nearres arm (gameStartTime-gated, ≥1h into match).
- **nearresfade** — fade YES[0.22,0.52] ≤30d; conflict-keywords skipped; live-match guarded.
- **quietfade** — conflict fade ONLY when OpenAlice news feed quiet (recovers skipped space).
- **panel** — TradingAgents distilled: 3 analyst votes (momentum/whale/news) + bear veto,
  ≥2 votes to enter YES[0.55,0.85]. Votes logged per position.
- **tasignal** — LIVE TradingAgents agent graph (2026-06-14, vs panel which only distilled
  it). `tradingagents_feed.py` (ta-venv, launchd com.aryan.polymarket-tradingagents, hourly)
  runs the LLM graph on a few markets → verdict cache; leg trades the DIVERGENCE (≥0.20)
  between TA's BUY/SELL lean and implied YES. NEEDS LLM key in `.ta_env` (Anthropic) — idle
  ($0) without it. `tasignal_enabled=False` until the first keyed run is validated.
- **alicenews / newslag** — news-confirmed entries (other-session legs).
- **Attention**: ytbuzz (live-match guarded) / wikivol / redditbuzz — persisted caches.
- **"Nothing happens" family**: noevent, newsno, nohappen, weatherno, btc15no — negation
  + compound-keyword guarded.
- **Accumulating**: ladderarb (+$2.92, 4/4 — watch), longshortbias, polyflup (kill-watch),
  bookpress, kellyfav, gtrend, vlrtop, wcmatch, famsum, thetadk, certsnipe, dipladder,
  clusterarb, socspread, peacefwd, fogbuy, crashbuy, polvol, latefade, candlesig.
- **Graveyard** (data-killed; don't resurrect without NEW evidence): scalp02 (0.2% target < spread, structural), esportsdog, polyflup, ytbuzz, midfield, clobimbal,
  nearreslow, truefade, deepfade, nsfade, wangfade, momentum family, flow family
  (walletcopy/whale/multiwhale/buyflow/sellflow — whale-follow dead at n=136),
  newmarket, midfade, favyes, coinup/coindown, ~60 more.
- **Cut 2026-06-15 (kill rule, loss-makers)**: fogbuy (n=18, -$3.56, 0% WR), alicenews (n=7, -$4.57, 0% WR). Controls allin/coinflip kept (must lose). All bigger losers (microscalp/crashbuy/gridbounce/moonshot) already disabled.
- **Cut 2026-06-14 (never-fired dead weight — distinct from data-killed losers)**: acumvol,
  wikivol, spreadfade, redditbuzz, predictit, metaculus, lowliq — all enabled since 06-07,
  0 opens / 0 closes in 7d (never trigger). Disabled to shrink the 76-leg scan + DSR
  selection surface; reversible (flip `_enabled` back). The kill RULE itself yielded 0 cuts
  — every CI-excludes-0 loser was already off or is a required control (allin/coinflip).
  NOTE: wikivol/redditbuzz are attention-scrape legs; week-long silence may be a stale
  cache/feed, not a weak signal — check the scrape if you want those back. candlesig/coinrev
  (0-data, 3d old) are next-in-line if still silent.

## Modular Leg System (2026-06-15)
**Phase 1 (Architecture)**: leg_diverg.py (template), leg_runner.py (orchestrator).
**Phase 2 (Library)**: 7 stub modules ready for full impl (feargreed, lateprox, coinup,
coindown, candlesig, macdsig, coinrev). Each provides independent entry/exit/state/board.
**Phase 3 (Family bots)**: crypto_bot.py (8 crypto legs), esports_bot.py (4 esports),
sports_bot.py (sports resolution), macro_bot.py (macro/political fades). Each bot runs
standalone with its own watchdog cycle and state file.

**Design**: Per-leg modularity enables independent testing, A/B experiments, dry-runs,
and hot-reload without restarting scalp_lab.py. All legs record forecasts to shared
belief_ledger.jsonl. Scalp_lab.py remains the primary live bot; family bots are
parallel testing ground for new strategies. See leg_diverg.py for the template pattern.

## Research Findings (2026-06-12)
Literature sweep across 7 topics. High-priority findings only.

### A · Late-resolution underreaction validates nearres (HIGH)
arxiv:2606.07811 — Markets move only 0.64 units per 1-unit public signal; the 0.36
residual drifts over subsequent minutes. Effect is *strongest* in low-liquidity,
high-salience (near-certainty) states — exactly the [0.88,0.95] esports window.
**Experiment queued**: test <2h entry vs. current <4h; expect tighter CI and better
$/trade as underreaction peaks in the final hour.

### B · Polymarket microstructure (HIGH)
- arxiv:2604.24366: On-chain `OrderFilled` trade direction is 59% correlated with
  public CLOB feed — use on-chain for `collect_onchain.py`, not the feed.
- arxiv:2605.00493: Informed insiders concentrate in *political* markets, not esports.
  nearres competes in the cleanest, least-insider-polluted segment.
- High-probability YES (0.88-0.95) has tightest spreads → best execution quality.

### C · FLB is title-dependent in esports (MEDIUM)
Whelan 2024 (*Economica*): standard FLB (favorites underpriced) confirmed for CS2/LoL;
reverse FLB documented in some titles. Validate nearres hit-rate by game title — if
any title shows <60% WR at n≥10, gate it out.

### D · Wang λ is time-varying (MEDIUM)
Yang 2026 (SSRN 6468338): λ increases as resolution approaches, which explains why
the <4h window works. Test: does nearres $/trade correlate with time-to-resolution
at entry? If yes, the <2h experiment (A above) has theoretical backing.

### E · Complete-set arb (LOW — skip for now)
~$40M extracted by dedicated bots (Saguillo et al. 2025). Requires monitoring 93K+
markets simultaneously. Not worth building until nearres is fully proven.

### F · On-chain Solana burn signals (SKIP)
No evidence linking incinerator/DEX flow to Polymarket crypto outcomes. Do not build
without a backtest hypothesis and controlled experiment with ≥30 exits.

## Research Findings — Round 2 (2026-06-12)
Deeper sweep: latency, microstructure, bracket effects, DSR.

### G · PandaScore Live API = map-win latency edge (HIGH)
PandaScore Live API delivers map/round completion at 300ms from game server.
Polymarket WebSocket propagates to casual traders in 1–2s. Window: ~0.7–1.7s where
the market still prices "match incomplete" while map is already won.
**Action**: extend psconfirm to poll PandaScore `/live` endpoint each watchdog cycle;
if map_score changed since last cycle AND favorite price still <0.97, treat as
late-entry opportunity. Free tier = 1,000 calls/month; upgrade if nearres scales.

### H · Bid-ask spread at entry = quality signal (HIGH)
Stoikov inventory theory: market makers skew spread when one side dominates flow.
Spread >3¢ on YES at [0.88-0.95] = market maker hedged out, price sticky → better
fill window. Spread <1¢ = tight, competitive, less edge.
**Action**: log `spread_at_entry` (ask - bid from CLOB at time of fill) for all
nearres trades. After n=10 new entries, split WR by spread bucket.

### I · Tournament round amplifies FLB (MEDIUM)
Whelan 2024 (*Economica*): FLB intensifies in semifinals/finals vs. group stage —
favorites are MORE underpriced at the same price point in later rounds.
**Action**: tag all nearres trades with `tournament_round` from PandaScore metadata.
Retroactively tag existing 24 trades. Hypothesis: playoff WR > group-stage WR at
same entry price.

### J · DSR correction before scaling (MEDIUM)
With N≥8 legs tested, effective Sharpe threshold rises ~1.47× (√ln(8)).
nearres CI [+0.012,+0.065] likely survives but should be formally computed via
backtest_nearres.py before adding capital or new legs. Bailey & Lopez de Prado
(2014, SSRN 2460551) is the reference.
**Action**: add DSR calculation to backtest_nearres.py output.

### K · Self-research gaps to log (LOW — build dataset)
- Log `market_created_at` vs `match_start_time` on all nearres entries (early listing
  may predict tighter spreads and more exits).
- Tag entries by match local time zone to check Asian-hours spread widening (VCT/LoL).

## OSS Harvest (2026-06-11)
Five repos cloned and audited. Paths: `~/Documents/Codex/2026-05-28/is-git-installed/oss-bots/`.

| Repo | Verdict |
|------|---------|
| **prediction-market-arbitrage-bot** | Jaccard+Levenshtein fuzzy matcher ported → `market_match.py` |
| **polybot** | Gabagool complete-set spec ported → `csarb` leg. NOTE: polybot's own run lost −$126.80 despite original making $4.3k — reverse-engineering decay is real. |
| **Awesome-Prediction-Market-Tools** | Curated list; diff daily via oss-bots-daily-maintenance task for new entries (Marketlens, Oddpool, pykalshi). |
| **poly-maker** | Market-making now unprofitable per its own README (compressed spreads). Do not replicate. |
| **TradeTheEvent** | Event classifier for news-driven markets. Useful framing; no direct port yet. |

**Meta-signals**: (1) Market making is dead at retail scale — spreads too thin. (2) Reverse-engineered strategies decay faster than original implementations — the advantage lives in the edge, not the code.

**Kalshi OOS (kalshi_oos.py, 2026-06-11)**: INCONCLUSIVE — 378 settled markets but only
18 with two-sided books (Kalshi candlestick books mostly one-sided). nearres n=9
CI[-0.078,+0.058] incl. 0; controls properly negative (replay honest). Not evidence
against nearres — evidence Kalshi public data is too thin to test it. Don't re-run
without a better data source.

## polybot harvest — execution-not-signal proof (2026-06-13)
ent0n29/polybot reverse-engineered the Gabagool22 complete-set strategy
(oss-bots/polybot/research/final_strategy_findings.py). Their own data:
- **85% of P&L is EXECUTION edge, not direction** — money comes from maker fills
  better than mid, NOT from predicting up/down. "Core strategy is market-neutral
  complete-set execution."
- 15min-BTC = 70% of P&L. UP/DOWN win-rates flip across regimes — no directional bias.
**Closes csarb**: confirms its edge is 3-second maker cadence we cannot paper-
replicate with 60s taker-fill assumptions. csarb stays as a near-never-triggers
honesty probe, never a real leg. No new leg ported — the edge is non-reproducible
for us by construction, which is itself the finding.

## Capacity finding (2026-06-13) — the edge can't scale on this venue
capacity_scan.py: nearres edge × book depth across categories. Result is
structural and decisive:
- **Edge lives where depth doesn't.** esports/sports favorites-near-resolution
  = the edge, but books $1-5k deep (can't size past ~$10/trade).
- **Depth lives where the edge doesn't.** politics ($5M books) almost NEVER
  hit the setup (n=4 from 400 mkts — no frequent scheduled resolutions);
  crypto up/down ($8M books) are coinflips at 0.5, no persistent favorite
  (n=23 CI=[-0.039,+0.065], no edge).
- nearres needs: frequent scheduled resolution + clear favorite + underreaction
  window = a SPORTS property. Deep markets structurally lack that combo.
**Implication**: no path to real SIZE via nearres on Polymarket. Real-money
test still worth it to prove the METHOD (fills vs paper), but expect $-tens/mo
ceiling. To scale the method, the venue has to change (sportsbooks / deeper
exchanges), not the strategy.

## OSS Harvest — Round 2 (2026-06-12, 40-min deep sweep)
~75 candidates vetted across 4 angles; 11 cloned to oss-bots/. Verdicts:

| Find | Why it matters |
|------|----------------|
| **Jon-Becker/prediction-market-analysis** (3.5k★) | Largest public Polymarket+Kalshi dataset (36GiB parquet, one `make setup`) — replaces thin Kalshi candles for OOS backtests of nearres bands. |
| **warproxxx/poly_data** (2.1k★) | OrderFilled reader for CTF V2 via raw eth_getLogs, resumable, free-RPC friendly — upgrade path for collect_onchain.py (which already has V2 addr ✓). |
| **awpy + ESTA** (576★ / KDD paper) | CS2 demo parser + 41,782 labeled pro rounds → train an independent CS2 win-prob fair-value model vs the [0.88,0.95] band. ESTA = 7.5GB on disk. |
| **oracle3** (249★) | Wang Transform engine, MLE λ≈0.183 on 291k resolved contracts — independently cross-validates our λ≈0.22. Port coefficients, not engine. |
| **Cyclododecene/newsfeed + gdelt-doc-api** | Free GDELT event/intensity layers — `timelinevol` per conflict keyword = one-call news-intensity covariate for quietfade/peacefwd. |
| **suislanchez weather bot** (433★) | GFS 31-member ensemble fraction→probability = fair-value model for weatherno. Kalshi temp markets overprice uncertainty ~1.27x (Oalkhadra). |
| **PredictionMarketBench** (Oddpool) | Replay-based backtest harness w/ execution constraints — the honest-fill pattern scalp_lab validation should copy. |
| **ATPBetting** (462★) | Confidence-decile bet selection: only bet top-percentile model-vs-market divergence. Methodology port for any leg with a fair-value model. |

**Meta**: high-star "polymarket arbitrage bot" repos (radioman etc.) are star-farmed
scam bait — excluded. Esports prediction on GitHub = parsers+datasets, not models;
Valorant has no good OSS win-prob repo (VLR scrape + own Elo is the path).
Free L2 orderbook archives: archive.pmxt.dev + pmdata.dev (hourly parquet, no keys).

**Ports executed 2026-06-12**: gdelt_intensity.py (DOC timelinevol, 10s throttle, cache
persisted in scalp_lab_cache.json) → logged as `gdelt_intensity` on quietfade entries;
`wang_fair(p, lam=0.183)` (oracle3 coefficient) → logged as `wang_fair_yes` on
nearresfade entries. Covariates only — no gating, no config churn. Jon-Becker 36GiB
dataset download BLOCKED ON DISK (14GiB free; esta clone = 7.5GB) — free space first.

## SHARP Wallet Intelligence (2026-06-12)
7 profitable wallets tracked (`insider_wallets.json`, watcher = insider_watch.py →
iMessage; profiler = wallet_algo.py → wallet_algo.json). Reverse-engineered algos:

| Wallet | Algo |
|--------|------|
| 0x16bc (74% $47k), 0xbf8d (49% $34k), 0xee55 (62% $23k) | **Complete-set/MM bots on 5-min crypto Up-or-Down** — fire every 3-4s, buy BOTH sides ~50/50, $4-14 bets. Their "WR" is set-redemption, not direction. Validates csarb thesis — but they're makers at 3s cadence; our 60s once-mode can't honestly replicate. |
| 0x84cf (80% $112k) | Sports-spread ladder bot, live-game, averages in at 0.5-0.6, 95% gaps <60s. |
| 0xa2fc (46% $1.4k) | Mixed sports/esports ladder bot buying favorites at 0.8-0.9 — **closest analogue to nearres**, but ladders instead of one-shots. Watch for laddering as a nearres upgrade hypothesis. |
| 0x493c (57% $199) | **The only human** (~2 trades/day): geopolitical news, buys near-certainty 0.9-1.0 (27% of entries) — a politics nearres. Holds; occasional scalps. |
| 0xeface (18% $3.7k) | Wide-net politics/event bot, $10k max bets, sells ⅓ of positions early — longshot lottery w/ active risk mgmt. |

**Lessons**: (1) The profitable crypto-coinflip wallets are NOT predicting direction —
they arb the set. Confirms coinup/coindown graveyard verdict. (2) High-WR wallets are
favorites-laddering bots — same FLB family as nearres. (3) Only one human in the top 7;
manual trading at scale is rare.

**Position-level anatomy (wallet_deep.py, 2026-06-12)**: net incl. open positions —
0x16bc +$50.5k, 0x84cf +$41.5k, 0xee55 +$22.2k real winners; 0xa2fc (the nearres-like
favorites ladder, no time gate) NET −$5.8k = negative control: band alone isn't the
edge, the <4h esports filter is. ALL profitable wallets earn in the 0.0-0.3 entry band
and lose at 0.4-0.9. Even the best wallet is −$54k in esports (n=20, one −$44.5k LoL
bet) — whale money avoids/loses esports, leaving nearres's lane clean (finding B).

## Open-Trades Audit (every 6h)
`open_trades_audit.py` prices every open position vs live web (gamma → CLOB fallback);
flags RESOLVED / STALE_GONE / DEEP_RED (>50% unrealized loss) / PAST_END / AGED.
Wired in as check #5 of polymarket-6h-routine. RESOLVED >10 = exit scan broken (LOUD).
Output: open_trades_audit.json.

<!-- AUTO:BEGIN — everything below is regenerated by brain_update.py -->
## LIVE STATE (auto)
Updated 2026-08-12 08:30 UTC · mirror age 256s (healthy)

| leg | exits | open | WR | R:R | P&L since reset |
|---|---|---|---|---|---|
| nearres | 78 | 0 |  67% |  0.4 | -2.89 |
| sportres | 7 | 0 |  43% |  0.2 | -1.98 |
| nearreslow | 1 | 0 | 100% |  inf | +0.18 |
| nearresfade | 32 | 0 |  50% |  0.6 | -1.20 |
| ytbuzz | 13 | 0 |  23% |  0.7 | -1.50 |
| nohappen | 9 | 0 |  56% |  0.7 | -0.05 |
| longshortbias | 19 | 0 |  16% |  0.5 | -0.48 |
| clobimbal | 20 | 0 |  35% |  0.6 | -1.42 |
| polyflup | 6 | 0 |  17% |  1.0 | -0.51 |
| noevent | 61 | 0 |  34% |  1.1 | -1.64 |
| newsno | 42 | 0 |  38% |  0.9 | -2.84 |
| btc15no | 13 | 0 |  15% |  1.6 | -0.51 |
| fogbuy | 26 | 0 |  46% |  1.3 | +0.35 |
| crashbuy | 19 | 0 |  37% |  1.2 | -1.46 |
| polvol | 27 | 0 |  48% |  0.5 | -1.14 |
| latefade | 14 | 0 |  43% |  0.8 | -0.73 |
| gtrend | 4 | 0 |  75% |  2.1 | +0.33 |
| bookpress | 52 | 0 |  42% |  1.0 | -0.64 |
| kellyfav | 23 | 0 |  52% |  0.2 | -5.28 |
| midfield | 13 | 0 |   8% |  1.5 | -5.00 |
| wcmatch | 8 | 0 |  62% |  0.6 | -0.04 |
| famsum | 20 | 0 |  25% |  0.6 | -0.39 |
| mapdown | 1 | 0 |   0% |  0.0 | -0.17 |
| ladderarb | 29 | 0 |  48% |  0.7 | -3.03 |
| thetadk | 6 | 0 |  33% |  0.7 | -0.33 |
| certsnipe | 4 | 0 |  50% |  0.3 | -0.12 |
| dipladder | 9 | 0 |   0% |  0.0 | -4.87 |
| clusterarb | 18 | 0 |  11% |  0.7 | -0.15 |
| socspread | 10 | 0 |  30% |  0.6 | -1.15 |
| peacefwd | 7 | 0 |   0% |  0.0 | -2.17 |

### Health
- watchdog_loop.sh: RUNNING ✓
- stray run-mode bots: none ✓
- watchdog last: `[2026-08-12 13:56:52] HEALTHY - 22s ago`
- failures in last 20 watchdog lines: 0

- **nearres gate: 69/30 config-consistent exits** (selection-corrected significance still pending — best-of-90-legs means raw CI>0 happens ~90% of the time by luck; DSR at n=30)
- Controls lifetime: $-183.10 (negative — marking honest ✓)
- Open positions (focus legs): 0
- OOS backtest (2026-08-12 08:20 UTC, 16838 resolved markets):
  - esports_hi: n=2035 wr=62.7% pnl=$-139.72 [-0.0831,-0.055] CI incl. 0
  - esports_lo: n=2321 wr=47.8% pnl=$-395.12 [-0.186,-0.1542] CI incl. 0
  - sports_hi: n=6360 wr=46.4% pnl=$-1042.57 [-0.173,-0.1548] CI incl. 0
  - esports_hi_2h: n=2013 wr=64.9% pnl=$-120.33 [-0.0741,-0.046] CI incl. 0

### Markets
- Markets: Nifty 50 24,298 (-0.7%) · S&P 500 7,728 (-0.3%) · Nasdaq 26,445 (-0.6%) · Bitcoin 63,760 (+0.6%) · USD/INR 95 (-0.0%) · Gold 4,468 (+0.6%)
<!-- AUTO:END -->

## Exit-dispatch bug fix (2026-06-13)
10 legs (bookpress, kellyfav, newslag, birdeye, gtrend, liqcrush, nearterm, newscrypto, newsesports, vlrtop) had _gain/_stop configured but were NEVER in scan_exits dispatch — ran with no TP/SL, all positions drifted to time-exit. Found via edge_conditions.py (bookpress 6/6 time-exits, incl +0.135 move not taken as target). Fixed: added to the catch-all gain/stop group. Their PRE-2026-06-13 closed exits are CONTAMINATED (never exited as designed) — exclude from edge reads. Honest win/loss data starts now.

## hours_left + up/down parser bugs (2026-06-13)
"Watch macdsig" uncovered an 8-leg silent-death bug: coinup/coindown/diverg/feargreed/lateprox/candlesig/coinrev/macdsig all gated on m["days_to_expiry"]/m["days"] which were NEVER keys in the market dict (keys: ask,bid,cat,chg,end,id,q,start,token,vol,yes) → days always 999 → hours_left always 23976 → every leg with `hours_left > max` check NEVER fired. candlesig/coinup/coinrev confirmed 0 exits ever despite "enabled". FIX: use days_to_end(m["end"]) (end is populated 794/800). Verified via 6-agent workflow: all FIXED, none broken.
SECOND finding: Polymarket has DAILY "Bitcoin Up or Down on June X?" markets (not 5-min), only in [1,12]h window for a few hrs/day pre-resolution. _parse_price_market only handles "above $X" threshold markets, returns None for "Up or Down". macdsig rewired to try _coin_from_updown first (YES=Up/NO=Down, no strike) then fall back to threshold. coinup already used _coin_from_updown so it's revived by the hours fix alone.
LESSON: "enabled: True" ≠ working. Three silent-breakage bugs found this session (exit-dispatch no-TP/SL on 10 legs, hours_left on 8 legs, fav75 esports). Audit that legs actually FIRE and EXIT, not just that they're enabled.

## Cross-examination of leg edges (2026-06-13) — CORRECTS prior beliefs
5-skeptic adversarial workflow interrogated the leg analysis. Verdicts:
- nearres LIVE edge REFUTED: +$0.020/exit is outlier+dust-driven (drop top-3 → +0.0017), asymmetric (avg win +0.074 vs avg loss -0.416, 5.6:1), and the WINS ARE OUTSIDE THE THESIS — live esports bucket is NEGATIVE (-0.058), [0.91-0.95] band NEGATIVE (-0.035); all +P&L from 'other' cat at entry<0.88. 8/24 wins are $0.001 dust. Only support is nearres_validation.json esports_hi (n=55, 67% win, CI excl 0, WF 5/5) — but live esports CONTRADICTS it. RESOLVE before real money.
- ladderarb REFUTED: same favorite-longshot win-rate padding that killed truefade. 6/9 cheap OTM longshots, +5c target=+125% on noise, while 2 still-open longshots (Russia-Ukraine, Israel-Syria) hold unbooked tail → 0. n=9, superseded params.
- Controls HONEST (high conf): 1002/1002 exits reconstruct exactly from fills, anti-optimistic asymmetry. Foundation solid.
- No TA leg works (high conf): every TA leg w/ data negative, 7/8 CI excl 0; tailspin only positive (+0.002, CI on zero). Post-bugfix TA still negative.
- CORRECTION: emacross/gridbounce were NOT contaminated — legitimately killed with working exits. Only candlesig/coinrev never fired (0 data). Don't overstate contamination.
LESSON: the favorite-longshot win-rate-padding trap recurs (truefade→ladderarb→nearres 'other' wins). High win% + tiny avg win + rare huge loss + unbooked open tails = NOT an edge. Judge $/exit and avg-win:avg-loss, never win%.

## Learn-from-positives mining (2026-06-13) — NO new edge in the profits
9-agent workflow mined the profitable P&L across 6 angles, adversarially verified each. Result: 6 miners → 3 candidates (all nearres variants) → 0 build-worthy survivors. The alicenews/ladder/killed-leg/big-win miners honestly found NOTHING.
- nearres_padding_purge REFUTED: stripping $0.001 dust from nearres EXPOSES no edge — drop top-3 winners → mean goes NEGATIVE. 3 winners carry the whole signal. First-half +0.074 / second-half -0.018 (decaying live).
- nearres_yes_hold_to_resolution REFUTED: simulated the "no stop, hold to resolution" fix → still -0.022/exit. Polymarket mids are CALIBRATED: a favorite dropping 0.94→0.63 mid-match genuinely has ~37% loss prob. No-stop just swaps gap-loss for equal expected-loss. THERE IS NO FIX for esports gap-through — the market is right.
- nearres_no_only (NO on cheap esports underdogs) = LOW confidence, NOT BUILT: same gap-through shape that killed fav75/nearres-live.
LESSON: the profitable P&L is outliers + one decaying esports signal, not a learnable edge. Mining wins harder does not manufacture an edge that isn't there. Only nearres_validation.json esports_hi (n=55, CI excl 0, WF 5/5) remains the one real candidate — and even it is contradicted by live gap-through. Calibrated mids = no free lunch from favorite-buying.

## Exhaustive profitable-subset search (2026-06-13) — NONE EXISTS
Brute-force searched ALL 1,492 non-dust non-control closed trades for a profitable implementable rule: every leg × side × category × entry-band × combination, n>=12. RESULT: zero subsets are positive AND survive outlier-drop AND survive OOS time-split. The only positive-mean subsets (nearres +0.027, tailspin +0.003) FAIL the OOS split (positive early, negative/decaying later).
VERDICT: there is no profitable leg discoverable in the paper history. "Make a profitable one based on the data" has a definitive answer — you cannot, because no profitable pattern exists. Every apparent winner = outliers + dust + decay. Building one anyway = fabrication / marking-bug.
This CONFIRMS the project's honest status: no proven edge yet. The controls losing correctly proves the marking is honest, so this negative is TRUSTWORTHY. nearresno + spreadcap remain forward HYPOTHESES (not data-proven) that must earn their own >=30-exit CI. Do not relitigate — the search was exhaustive.
RE-CONFIRMED 2026-06-17 (data grew, conclusion HELD): jackknife of the 18 positive-P&L legs (pool +$37.83) — remove the single biggest trade → +$19.02 (50% was ONE geopolbomb longshot); remove top-3 trades → **−$0.34 (101% of all profit came from 3 trades)**; 90% of profit = the Iran/geopolitical cluster (geopolbomb/conviction/alicenews/ladderarb), correlated, one month's resolved outcome. The only n≥10 positives (ladderarb +0.18, fogbuy +0.14, tailspin +0.04, nearres +0.27=RETRACTED) are all ~0¢/exit favorite-padding. "Combining the profitable legs" = stacking ONE correlated lucky tail — no edge to learn or combine. The correct answer to "make a profitable leg from the data" is the parallel session's dataarb.py (non-predictive settled-arb), NOT a 96th predictive/combined leg.
**LEARN-FROM-PROFITABLE-LEGS (2026-06-17): `arb_memory.py`** — the self-learning loop over the ARB track (procedural_memory analog, but for the arbs not the analyst calibration). Reads resolved exits from ALL THREE +EV arb legs — basket_paper + dataarb + monoarb (same set multiarb combines) + their controls — and learns two honest things: (1) REALIZATION = realized/expected — does the lock pay what entry promised? The gap-through/execution-erosion guard that nearres failed; structural arb should hold ≈1.0. Verified at n=1: basket NY-08 realized $1.60/$1.60 = ratio 1.0 (no gap-through — the structural edge is real, opposite of nearres). (2) REGIME = per-feature realized edge (n_outcomes/edge-band/spread/source), DORMANT until n≥30 (no small-n overfit). + controls-must-lose. Wired watchdog %30 -eq 22 (after multiarb); Vault/Reports/arb_memory.md + arb_memory.json + a lessons() feed. The honest learner for the only legs worth learning from.

## Category learning (2026-06-14)
1648 non-control exits by category, outlier + OOS tested:
- NO category is genuinely profitable. "world" +$0.036 is a MIRAGE — drop-top-1 → NEGATIVE (-0.035), OOS-unstable (neg 1st half / pos 2nd). 22-leg grab-bag + 1 lucky trade, not an edge.
- esports = -$0.381/trade = MONEY PIT, 3x worse than any other category, consistent across both OOS halves (gap-through at category scale). politics -0.072, crypto/other -0.093, sports -0.122 all lose.
ACTION: broad/non-thesis legs should SKIP esports (no reason to be there, costs -$0.38). Esports-SPECIFIC legs (nearres/nearresno) keep testing whether the validated esports_hi edge survives live gap-through. Do NOT blanket-ban esports — nearres is the lead candidate and validation says esports_hi has an edge; the issue is stops gapping, not esports per se.

## nearres OOS edge RESOLVED — it was a backtest execution artifact (2026-06-14)
Answers the open question above ("live esports CONTRADICTS the n=295 OOS edge — RESOLVE
before real money"). RESOLVED: the OOS edge was a clean-stop-fill FICTION in
backtest_nearres.py, not a real edge. The "DSR PASSED (2026-06-12)" headline is RETRACTED.
- ROOT CAUSE: backtest_nearres.py:113 booked every stopped trade at the TRIGGER
  (mid0 − 3¢), assuming a clean limit fill. Esports favorites gap 0.93→~0.30 in ONE
  tick at map resolution, so the live stop fills 45–65¢ below entry, not 3¢.
- THE PROOF (reprice_gapfix.py re-priced the cached OOS set under honest fills — same
  markets, IDENTICAL win rate):
  | esports_hi | n | wr | pnl | $/exit | CI95 | DSR |
  |---|---|---|---|---|---|---|
  | OLD clean-stop | 297 | 60.9% | +$7.49 | +0.0252 | [+0.011,+0.039] | +1.335 PASS |
  | NEW gap-honest | 297 | 60.9% | −$26.10 | −0.0879 | [−0.129,−0.049] | −6.479 FAIL |
  P&L swings $33.6 on stop-fill realism ALONE; CI flips to exclude 0 on the NEGATIVE
  side. 2h window flips the same way (OLD +0.0338 PASS → negative). backtest_summary.json
  regenerated gap-honest; AUTO block reflects it next cycle.
- LIVE CONFIRMS (n=28): 3 gapped stops cost −$1.02 vs −$0.25 the clean model assumes;
  in-thesis band [0.88,0.95] side-mid = −$0.123/exit (n=5); drop top-5 winners → leg
  negative; +P&L was 5 outliers + dust (8/28 <0.2¢). See [[13 A backtest that assumes clean stop fills lies]].
- TRIPLE-VALIDATION re-run on the corrected store (nearres_validation.py): esports_hi
  VERDICT = NO EDGE — sign-flip p=1.0, bootstrap CI [−0.128,−0.050], walk-forward 0/5
  folds positive (all FAIL). The stale "p=0.008 / 5-5 WF / EDGE" verdict is overwritten.
  (Its 4th test "fair-market MC" conditioned on reason==resolve, excluding every gap-stop
  loss = survivorship bias — FIXED 2026-06-15: fair_market_mc now asserts ALL exit reasons
  are kept; reason==resolve/target slices forbidden. Re-confirmed by audit 2026-06-17.)
- AUDIT: same clean-fill fiction found in nearres_oos.py + nearres_oos_gamma.py (fixed
  +9¢/−3¢ win-loss, never walk path) → marked SUPERSEDED; kalshi_oos.py replay → fixed;
  capacity_scan.py imports the fix (auto-clean); coin_updown/bot_backtest use honest settle.
- FIX (deployed, ast-verified): backtest_nearres.py:113 now books stops at realized bar
  mid − HALF_SPREAD; targets keep the limit fill (asymmetric & correct — a limit fills
  at its price on a favorable gap, only stops gap through). Backup: backtest_results.preGapfix.bak.json.
- CORRECTS the paragraph above + the Evidence section: esports_hi has NO live-realizable
  edge. nearres is NOT a proven edge. Confirms Lessons 4 & 8 and the exhaustive-search
  verdict. Calibrated mids → favorite-buying has no free lunch. DO NOT put real money behind nearres.

## Analyst edge — United Russia REFUTED on resolution check (2026-06-14)
The fresh-edge hunt flagged "United Russia gain most seats" as a 35pt edge (true 0.95 vs market 0.60). RESOLUTION VERIFICATION killed it: the market resolves on the party that "gains the GREATEST number of seats compared to BEFORE the election" — potentially a net-seat-CHANGE test, not "holds the most." UR holds ~323/450 (ceiling) so it can't GAIN the most; a growing small party would. AND the same event shows UR at 96% in one place vs 0.60 in the market I matched — a discrepancy meaning wrong-market-match OR resolution nuance. Either way NOT a clean edge. PULLED from analyst book. LESSON: always verify the exact resolution text before trusting an edge — "most seats" ≠ "most seats GAINED". This is the #1 prediction-market trap and the verification step caught it.

## Three-track profitability program + scorecard.py (2026-06-14)
After accepting "the algo bot is not profitable," opened THREE explicit tracks and built `scorecard.py` to print one honest number across all of them (mirrored to Obsidian as "Edge Scorecard.md" + iCloud, every snapshot cycle):
- **Track 3 — algo legs (the honest null):** 120 legs total −$321; controls lose −$150 over 1210 exits (✅ marking honest); ZERO legs clear 30 exits with bootstrap CI>0. No edge, but the negative is TRUSTWORTHY. This is a finding, not a failure.
- **Track 2 — maker vs taker execution:** taker (allin) −$0.140/trade vs maker (microscalp) −$0.023/trade = **maker loses 6.0x less, saves $0.116/trade**. Execution edge is REAL but smaller than the spread it must overcome — narrows the loss, doesn't cross into profit alone. spreadcap (Avellaneda-Stoikov quoting) n=6, too young.
- **Track 1 — research analyst (the only real shot):** sourced, resolution-gated, settle-at-date edges. This is the ONLY track that ever produced positive expectancy.

## Analyst hunt v2 + adversarial refutation panel (2026-06-14)
Ran 8-market analyst hunt with a MANDATORY resolution-confirm gate, then a 3-lens adversarial refutation panel (resolution-misread / fresh-counter-evidence / market-is-right, each default-to-refuted) on every recommended edge. Outcome:
- **5 of 8 markets = EFFICIENT** (Strait of Hormuz, Israel airspace, Crude $70, BTC $70k, Israel→Yemen). Analysts correctly DECLINED rather than manufacture edges. Most liquid markets ARE right — that discipline is the point.
- **0 resolution-gate failures** this round (every analyst fetched+matched resolution before estimating).
- **Israel×Hezbollah permanent peace by June 30 — NO @ 0.86 — SURVIVED refutation 0/3.** Resolution requires Hezbollah ITSELF to be a party to a PERMANENT deal; the June 2-3 trilateral was Israel+Lebanese-GOVERNMENT only and Hezbollah rejected it June 4. Temporary ceasefires excluded. Term structure corroborates (sister "by June 15" already YES 0.047). true ~0.04 vs market 0.14, 10pt edge. ADDED to analyst book (conditionId 0x86f43746…). 5th position, 4 now resolve June 30.
- **Starmer out by June 30 — NO — REFUTED 2/3, NOT added.** Panel killed the marginal 6pt edge: (fresh) June 11 defence resignations pushed Kalshi back UP to ~31%, breaking the "odds falling" thesis; (efficient) resolution triggers YES on mere ANNOUNCEMENT not handover, so "procedurally impossible by June 30" is moot and 6pt sits inside the noise.
- LESSON: the adversarial panel is the operational form of the United Russia lesson — a single analyst's resolution read is not enough; ≥2-of-3 independent skeptics must fail to refute before an edge enters the book. It caught a marginal edge resting on a procedural argument the resolution clause defeats.

## pm_calibration.py — Wang λ MLE + calibration buckets, live (2026-06-14)
Stdlib-only tool that ports two GitHub repo methods WITHOUT downloading their datasets, runs them on a live-fetched resolved-market sample:
- **Jon-Becker/prediction-market-analysis** — decile-bucket calibration curve + ECE. Winner label = `outcome_prices` p>0.99 (honest, same as Jon-Becker). No 36GiB parquet needed.
- **YichengYang-Ethan/prediction-market-pricing (oracle3)** — Wang(2000) MLE: λ̂ = argmax Σ[y·ln Φ(z−λ) + (1−y)·ln(1−Φ(z−λ))], z=Φ⁻¹(p_mkt). Golden-section minimizer, 80 iterations.

**Verified findings (2026-06-14 runs):**
- Synthetic self-test: injected λ=0.18 → recovered +0.173 ✅ (error <0.007)
- Live n=143: λ̂=+0.129, ECE=0.0764, FL spread=+0.157 (structural FL bias confirmed)
- Live n=50 quick-run: λ̂=+0.228, FL spread=+0.184 (BRAIN's λ≈0.22 in fade markets = plausible)
- Three sources bracket: paper 0.176/0.183, live 0.129–0.228 across samples, BRAIN 0.22
- Favorite-longshot bias structural (not behavioral): λ>0 → longshots overpriced, favorites underpriced, but on Polymarket calibrated mids mean ONE-TICK gaps absorb the implied edge → no free lunch (nearres lesson)

> ⚠️ **RETRACTED 2026-06-15 (FL-premium harvest audit, 4-finder + 3-lens workflow, verdict PREMIUM_REAL_BUT_UNHARVESTABLE).** The "+0.157/+0.184 FL spread, structural bias confirmed" above is a **lifetime-average-price CONTAMINATION ARTIFACT** + small-sample CLOB-proxy noise (n=58–169). On the gap-immune fully-on-chain goldsky 726-market truth the FL spread is **+0.002** (independently re-verified this session: favorite>0.7 gap −0.018, longshot<0.3 gap −0.020, FL = +0.0020; the >0.9 decile realizes 0.978 vs priced 0.975 — near-perfect calibration). Favorites do NOT carry a positive realized premium; they UNDERperform their price by ~1.2–1.8¢. Clean early/entry-median FL = +0.005 (Thread A n=188). **The λ≈0.22 "Wang premium that refuses to die" was never in clean data.** Every backtest cell that clears zero does so only by conditioning on reason∈{resolve,target} (survivorship — drops the gap-stops that carry the losses); whole-population realized PnL is strictly negative in every band (esports_hi −0.076 CI[−0.112,−0.041], sports_hi −0.123 CI[−0.145,−0.101]). Gap-immune maker side never clears break-even (best cell goldsky>0.90 n=93 +0.023 CI[−0.009,+0.047], fails Bonferroni). **DEAD END: any FL/Wang favorite-harvest on Polymarket, taker OR maker. Generalized Lesson 13: no reason==resolve/target slice may EVER be reported as edge (conditions on the outcome); any FL-spread claim MUST use early/entry-median price, never lifetime-average.** Analyst track confirmed as the only +EV path.

**How to run:**
```
python3 ~/Documents/polymarket/pm_calibration.py           # default n=150
python3 ~/Documents/polymarket/pm_calibration.py --n 50   # faster quick-check
```
Output auto-mirrored to `Obsidian Vault/Polymarket/PM Calibration.md` + iCloud every 6h via `obsidian_snapshot.py`.

## Analyst hunt v3 — Polymarket Iran/WC cluster (2026-06-15)
Ran 11-market analyst pipeline (World Cup outrights + US-Iran diplomatic cluster). **Book: EMPTY — 0 survived.**
- 8 gated: all 6 WC outrights (0.3–2.6% edge, all <5% threshold) + China/Taiwan 2.6% + US-Iran Dec 2026 (resolution unconfirmed).
- 3 reached refutation panel:
  - **US-Iran permanent deal by Jun 15 (NO @ 0.262, edge 22.2%)** — 3/3 refuted. Analyst correctly identified MOU ≠ "permanent peace deal" but all three lenses found the Islamabad Declaration may contain war-ending language that satisfies the criteria. Market was right to price ambiguity at 26%. Resolves today — track for calibration.
  - **Strait of Hormuz normal by Jun 30 (NO @ 0.185, edge 11.5%)** — 2/3 refuted. Best near-edge: resolution-misread lens AGREED (IMF Portwatch 7-day MA ~6 vs 60 threshold, correct read). Killed by counter-evidence (mine clearance underway) + market-is-right (18.5% already prices slow recovery). Worth a manual Portwatch data check before expiry.
  - **US-Iran nuclear deal by Jun 30 (YES @ 0.655, edge 7.5%)** — 3/3 refuted. Resolution requires explicit nuclear language in the signed document; the MOU/Islamabad Declaration doesn't have it. Israel strikes post-ceasefire threaten signing window. "Text agreed" already priced.
- WC cluster finding: Polymarket prices WC outrights 1–3pp below sportsbook consensus — real but sub-threshold noise, not tradeable.
- LESSON: the 5% gate is pulling its weight (killed 8 noise signals). The resolution-misread lens is the sharpest blade (caught both Iran definitional traps). Full report: `PolymarketVault/Reports/Analyst Hunt Run 2 2026-06-15.md`.

## Hormuz NO — first DATA-CONFIRMED analyst edge (2026-06-16)
The analyst track's first edge that survives a hard-data resolution check, not just argument.
**Market:** "Strait of Hormuz traffic returns to normal by end of June?" YES @ 0.185 (cid 0x348cd9ad…). Resolves YES iff IMF PortWatch 7-day MA of transit calls >=60 by June 30 2026.
**THE DATA (pulled live from IMF PortWatch ArcGIS `Daily_Chokepoints_Data` FeatureServer, portname='Strait of Hormuz'):**
- 7-day MA (latest 7 days, Jun 1-7) = **5.1**. Threshold = 60. Max single day in last 30 = **12**. Pre-crisis baseline ~90-100/day.
- Feed lags ~9 days (latest=Jun 7 on Jun 16). Blind spot Jun 8-16 filled by news: **US says mine clearance will take SIX MONTHS** (CNN 2026-06-02, IMO); ~6 ships/day Mar 1-May 24; insurers withholding safe-passage cover. No surge, physically can't reach 60 by Jun 30 (waterway not cleared until ~Aug-Sep).
- **Verdict: true YES ~= 0.02-0.05 vs market 0.185 -> NO edge ~14pt, objective + time-bounded (2wk).** Highest-conviction analyst signal to date. PAPER track - one high-conviction data point, NOT a 30-exit proven edge.
**HOW TO PULL THE DATA (reusable):** `Daily_Chokepoints_Data/FeatureServer/0/query?where=portname='Strait of Hormuz'&outFields=date,n_total&orderByFields=date DESC` on `services9.arcgis.com/weJ1QsnbMYJlCHdG`. n_total = daily transit calls. Same org has `Daily_Ports_Data`, `PortWatch_chokepoints_database`.
**METHODOLOGY LESSONS (two, both important):**
1. **Soft-refute > strict default-to-refuted.** The strict panel (Run 2) KILLED Hormuz 2/3 by counting a barely-confident efficient-market objection as a full veto. Variant C forced each lens to state confidence 0-100 and only counted refutations >70%; the efficient-market lens then honestly reported **38%** ("I am NOT confident the market is right") -> Hormuz survived. The strict panel was OVER-KILLING. Confidence-weighted refutation is the better design.
2. **Add a DATA-RESOLUTION-CHECK step.** Argument-only panels can't tell a real edge from a plausible one. The thing that CONFIRMED Hormuz was pulling the actual resolving series (PortWatch MA=5.1) + a news check on the data blind spot. Any market that resolves on an objective series (PortWatch, BLS, USGS, ESPN) MUST have that series fetched before entering the book.
**REJECTED same run:** China-invades-Taiwan NO @ 0.061 "survived" variant C but I killed it manually - staking 93.9c to win 6.1c on a 6.5-month tail is the FLB capital-inefficient tail-sell (same structural trap as nearres/FAV75), not an edge. Soft-refute let it through; judgment killed it. Guard: a NO at <0.10 on a long-dated tail is almost never a real edge regardless of "true prob."
Full report: `PolymarketVault/Reports/Hormuz Data-Confirmed Edge 2026-06-16.md`.

## Analyst hunt v4 — FRESH de-themed universe, strict panel (2026-06-16)
Followed up v3's empty book with the diagnosis "filter wasn't the problem, the MARKET SET was." Pointed the SAME strict pipeline at a fresh lower-liquidity universe ($5k–200k liq, max 3/theme, Iran/WC/United-Russia excluded): Brazil/Colombia elections, Hamilton F1, Fed cut/hike, BTC $45k/$55k touch, Satoshi, Anthropic-best-model. **Book: EMPTY — 0/12 survived.**
- 8 gated (<5% edge or resolution unconfirmed) — fresh markets are efficient too, even at lower liquidity. Largest gated edge 3.8% (Anthropic-best-model).
- **Fed rate HIKE in 2026 (YES, claimed 26.5% edge) — 2/3 REFUTED — the panel caught a 26pt ANALYST BLUNDER.** Analyst anchored P(hike) to ~70%, but that's the MARCH-2027 cumulative FedWatch number; the DECEMBER-2026 figure this market settles on is ~51% (term-structure mislabel). market-is-right lens caught the anchor error; counter-evidence lens noted the analyst's OWN named risk (oil retraces post-Iran) materialized June 15 (ceasefire → crude −20% → UBS cut hike odds). resolution-misread correctly did NOT refute (read was airtight) and scoped it to the other lenses. Both refuters HIGH-confidence → robust to soft-refute. This is the panel working: kill plausible-but-wrong big numbers on the most-arbitraged macro instrument in the world.
- Starmer-out / Satoshi-moves-BTC / BTC-$45k-dip all 3/3 refuted (low-bar announcement tail / irreducible year-long tail premium / path-dependent touch the market prices efficiently).
- **META (v3+v4 = 23 markets, 0 booked under strict panel):** single-analyst-argument-vs-market produces NO edges on Polymarket, even at lower liquidity. The ONLY confirmed edge (Hormuz NO) came from a HARD-DATA resolution check, not argument. Narrative analysis = robust null; the two things that work are (1) hard-data checks on objective-series markets, (2) structural (basket arb). v4 used the STRICT binary panel — adopt Variant C confidence-weighting + mandatory data-resolution-check in v5. Full report: `PolymarketVault/Reports/Analyst Hunt v4 Fresh Universe 2026-06-16.md`.

## Analyst variant D — thin markets + soft-refute (2026-06-16)
Tested "do THIN markets ($11-54k liq) have exploitable edges?" with the production pipeline (soft-refute conf>70 + mandatory data-resolution-check) on 8 real non-sports event markets. Ran in 2 batches (3 rate-limited, re-run clean).
**Finding: thin markets surface BIGGER nominal edges (33-40pt vs liquid cluster's ~14pt max) but carry MORE resolution traps. Net: 1 news-resolved candidate, 0 NEW data-confirmed edges. Hormuz 60-MA NO stays the only clean one.**
- **GATED (gate working hard):** Trump-unfreeze-assets @0.695 (resolution unconfirmed, live "agrees" dispute), Israel→Yemen @0.10 (4% <5%, matches prior "efficient"), next-US-Iran-meeting-Qatar @0.386 (16pt nominal killed by location-trap ambiguity), Mike-Lindell-MN-Gov @0.165 (1.5%), Sweden-PM-Kristersson @0.185 (4.5%).
- **Iran end ALL enrichment by Jul 31 — NO @0.43, true 0.035, 39.5pt — survived 0/3 hi-conf.** Strong thesis (ending ALL enrichment = Iran's reddest line; JCPOA KEPT 3.67%). BUT news-resolved → generous-reading trap risk (same failure mode as the killed "US-Iran nuclear deal" market). Strong NO lean, NOT data-confirmable. Rank below Hormuz.
- **40 ships transit Hormuz/day by Jun 30 — NO @0.505 "survived" but I REJECT it (soft-refute false positive).** THE LESSON OF THE RUN: same PortWatch series as the confirmed 60-MA edge, but resolution MECHANICS flip the answer. 60-MA needs SUSTAINED 60/day for a week (impossible) = clean NO. 40-ships needs ONE single day ≥40 — a backlog flush of ~850 queued ships could clear it in one day once the strait opens (Geneva signing ~Jun 21). The data-and-counter lens caught this at 62% (Kpler ~40/day within a month + 850-ship flush) — strong/specific/sourced — but soft-refute's >70 cutoff let it pass. Market @0.505 ≈ fair; analyst's 0.17 too low.
- **UK warships through Hormuz @0.184 — NO, 7.4pt — survived but REJECT (marginal tail-sell, market-is-right doubted 58%).**
**TWO NEW METHODOLOGY LESSONS:**
1. **Resolution MECHANICS > headline.** "Is Hormuz traffic low?" is the wrong question — single-day-40 vs sustained-60-MA on the IDENTICAL series give OPPOSITE edges. The exact trigger shape is everything; the analyst/panel must reason about the precise resolution mechanic, not the topic.
2. **Soft-refute has its OWN blind spot** (mirror of the strict panel's over-kill): a strong, specific, falsifiable counter at 62% SHOULD matter but the >70 cutoff auto-passed it. Fix: a specific mechanism-based refutation counts even <70, OR surface 50-70% refutations for human review instead of auto-pass. Neither strict nor pure-soft is right — confidence AND specificity both matter.
**RATE-LIMIT OPS NOTE:** never launch 2 workflows concurrently — ~20 simultaneous agents trips Anthropic server rate-limit (killed B+C double-launch, then D's first batch when B was draining). Run analyst workflows ONE AT A TIME (11 agents = safe; variant C alone succeeded twice). Re-run only the failed markets as a mini-workflow.
Full report: `PolymarketVault/Reports/Analyst Variant D Thin Markets 2026-06-16.md`.

## Hormuz July 31 NO — SECOND data-confirmed edge, vetted by the pm-* agent pipeline (2026-06-16)
First edge produced by the new project subagents (`~/.claude/agents/pm-*`, see [[pm-agent-suite-2026-06-16]]) composed as a pipeline: pm-data-resolver + pm-resolution-checker (parallel) → 3× pm-edge-refuter panel. This is the data-gate approach paying off where argument-only hunts (v3/v4, 23 markets, 0 booked) could not.
- **EDGE: "Strait of Hormuz traffic returns to normal by July 31?" NO @ market 0.605** (cid 0xb8e6d129…). Same PortWatch 60-MA mechanic as the June 30 edge but the MID term-structure point. Live ArcGIS: 7-day MA **5.14**, latest feed Jun 7, last 14 days 2-10 ships/day, NO ramp (trending down). Blind spot Jun 8-16 filled by news (CNN Jun 15 "most ships staying put"; MOU signs ~Jun 19; mine clearance 40-50d optimistic→6mo; insurers ~4000x; EIA: no normalization till early 2027).
- **For YES the MA must climb 5.14→60 SUSTAINED for a full week within ~6 weeks** — and Kpler's own first-month ceiling for the signed deal is ~40/day (<60). Backlog-flush is a ONE-TIME event (helps the single-day-40 market, NOT the 7-day-MA market — the resolution-mechanics lesson from variant D).
- **Panel: 0/3 refuted at >70%.** resolution-misread false@88 (read airtight), counter-evidence false@~80 (every projection caps <60), market-is-right false@78 (0.605 internally inconsistent with the 0.185 June / 0.900 Dec term structure; pricing a fast-flush tail as the MEDIAN). SURVIVES the <2-refute gate cleanly.
- **true P(YES) ~0.08-0.30** (analyst 0.08-0.15; market-is-right lens allows fast-flush tail to ~0.30). Even at 0.30, NO @ 0.605 = ~30pt edge. **R:R is GOOD** (risk 0.605 to win 0.395 — a near-even bet with a big edge, NOT the FLB <0.10 tail-sell trap). Better expression than the June 30 NO @ 0.185.
- **Correlated with the June 30 Hormuz NO** (same physical thesis: strait can't normalize fast) → treat the two as ONE thesis for sizing, not two independent bets.
- **Dec 31 @ 0.900 = FAIR, NOT an edge** (both data-resolver + resolution-checker agree: 6.5mo runway makes 60-MA the base case; do not transfer the July thesis). Monitored continuously by analyst_data_gate.py (alerts if MA climbs past 30). Full report: `PolymarketVault/Reports/Hormuz July31 Agent-Vetted Edge 2026-06-16.md`.

## Strategy graveyard analysis — 95 dead legs, ONE cause (2026-06-16)
Triggered by a leg-cleanup pass. CLEANUP RESULT: 0 enabled legs meet the kill rule (a concurrent session already disabled the last batch: newsno/noevent/nearresfade/spreadcap). Controls PASS (allin −$111, coinflip −$58 = marking honest = losses REAL). The on-screen losers are ALREADY-DISABLED legs showing historical trades — DO NOT re-kill them (FAV75 trap; check enabled==True AND closed-exits, never the cycle P&L line).
**95 killed legs sort into ~9 families, all dying the SAME death (calibrated mids):**
- FADE (fade/midfade/deepfade/nsfade/wangfade/truefade/nearresfade/latefade/fastfade/snaprev) — spread paid both ways; Wang λ≈0.22 premium is REAL but ≤ spread = paid-for-risk not alpha.
- FLB/favorite (nearres-RETRACTED/fav75/nearreslow/nearrestitle/esportsdog) — gap-through on stops (Lesson 13); discount unrealizable on calibrated mids.
- MAKER/spread-capture (microscalp 413ex −$9.70 "cleanest proof"/spreadcap/scalp02/scalp) — capturing spread w/ no edge IS paying it every round-trip.
- TA-TRANSLATION (emacross/rsifade/gridbounce/tadip/breakoutyes/gapfill/stalefish/priceleap) — OHLC indicators "don't translate"; PM prices are probabilities not asset prices.
- DIP/falling-knife (dip/dipladder/crashbuy/buyflow/coinpump/impulse/bigswing) — move continues.
- NEWS/event (newsno/noevent/newslag/panel/peacefwd/geopolbomb) — news already priced on calibrated mids.
- WHALE/copy (walletcopy/whale/multiwhale/clobimbal/sellflow) — 60s-lag copy = execution-not-signal.
- VOL-timing (volburst/volcrush/cryptovol/sportsvol) — no directional edge.
- R:R-broken/WR-trap (pricerev 77%win −$0.51/flipflop/snaprev/coindip/scalp) — Lesson 8, WR≠EV.
**THE ONE CAUSE:** Polymarket mids are well-calibrated (belief_ledger Brier + goldsky 726-mkt null). So every TAKER predictive leg pays the spread on entry and needs an info/structural edge > spread to win — and fades/TA/momentum/vol/dip/copy supply NO info the mid lacks. They are price-costumes on the same dead bet ("I out-predict a calibrated mid"). You can't.
**DON'T-REPEAT (strategic):** STOP building predictive taker legs — the bot is leg-saturated and leg #96 is negative-EV before it's written. The ONLY survivors are NON-predictive: Hormuz NO (objective-data arb) + basketlock (structural complete-set arb). New effort → (a) objective-data arb (data-gate BEFORE analysis) + (b) structural arb. A predictive idea's null hypothesis is "pays the spread and dies" — must clear it in a GAP-THROUGH-modeled backtest (Lesson 13) before earning a slot.
Full report: `PolymarketVault/Reports/Strategy Graveyard Analysis 2026-06-16.md`.

## Data-gate window-start bug fix (2026-06-16)
**Bug:** `analyst_data_gate.py` used `startDate` (= month start) as the kline window_start for ALL crypto-threshold markets. A market created mid-month (e.g., "Will BTC reach $70k in June?" created 2026-06-02 16:28 UTC) that says "price action before this market's creation will not be considered" leaked pre-creation candles → false RESOLVES-YES flag on a $71k June-1 print.
**Fix (committed):** `_market_start()` now detects "from the creation of this market" in the description → uses `createdAt` with full datetime precision. The `window_start` is printed in every output line so stale windows are visible.
**Lesson:** Always read the description for window-start language. "Reaches $X in June" ≠ "reaches $X since June 1" — mid-month markets explicitly cut off prior price action. The window IS the resolution condition; getting it wrong is the same class of error as gap-through clean fills (Lesson 13).

## dataarb false-positive guards + funding_basis carry leg + keyless data layer (2026-06-18)
**dataarb was firing FALSE POSITIVES, not dormant.** The data-gate emitted 2 "settled-but-mispriced" signals, both wrong, and dataarb had already booked the BTC one as its only "real" position:
- **"BTC reach $72,500 in June?" YES@0.065 flagged RESOLVES-YES** — the gate read a $74,092 high off a **1-day UTC** candle, but the resolution is **1-minute ET-bounded**; the June-1 UTC candle straddles the May-31-ET boundary, so the true in-window high was ~$71,409 (< $72,500). Market@0.065 was RIGHT; our read was the bug. (Extends the 2026-06-16 window-start fix: granularity+timezone, not just window leak.)
- **"No change after the July 2026 FOMC meeting?" YES@0.795** — gate's "0 cuts YTD + <45d → resolves YES" misfired on a SINGLE-MEETING market: the Jul 28-29 FOMC hadn't happened, so the meeting IS the unresolved event.
**Fixes (committed, verified on the live gate → EDGE FLAGS dropped both):** `ARB_SANITY_FLOOR=0.50` in `analyst_data_gate.arb_signals/find_alerts` (if data says resolves-YES but YES<0.50, the market disagrees >50pts → SUPPRESS, trust the market — the nearres rule as code) + `_FOMC_2026` schedule + `_fomc_pending(end)` (only treat "0 cuts" as settled if NO FOMC meeting remains before resolution). Quarantined the booked BTC position + cleared the 2 stale signals. **Generalized lesson:** evaluating a fine-grained, TZ-bounded resolution with coarse data manufactures phantom touches; and a model-vs-market gap >50pts means the MODEL is wrong (trust the price).

**funding_basis.py — NON-predictive crypto CARRY paper leg (the honest "trade crypto" answer).** Directional crypto is the hardest version of the dead taker pattern (the 14 crypto legs are −$42.24, all CI<0); the only structural crypto edge is delta-neutral funding harvest. REAL = take the side that RECEIVES funding (short perp when funding>0, long perp when <0); CONTROL = the mirror that PAYS (must lose on either sign — verified deterministically). **Fee-hurdle gate**: only opens when |funding| clears round-trip cost ×1.5 (~14.6%/yr taker, ~5.5%/yr maker) — the ONLY honest "make it profitable": trade only when a real premium exists, never fake the number. Idle in calm regimes is correct. Gate: n≥30 + CI>0 + beats control. Watchdog `-eq 18`.

**Keyless OPEN-SOURCE data layer → Obsidian (read-only RESEARCH, explicitly NOT legs — graveyard rule upheld; these are regime/context + the carry map, never bet-generators):**
- `funding_landscape.py` (`-eq 9`) — cross-venue perp funding (binance/bybit/okx/hyperliquid), annualized, flags where a carry clears the hurdle. **WIRES funding_landscape→funding_basis**: harvest where the premium is real (live: ETH-Hyperliquid +10.9%/yr, SOL-Bybit/HL ≈−10% clear; Binance sub-hurdle → why the leg sits idle there).
- `stablecoin_flows.py` (`-eq 3`) — DeFiLlama total stablecoin mcap = crypto liquidity regime ($314B, flat).
- `open_interest.py` (`-eq 6`) — cross-venue perp OI / leverage (Hyperliquid OI not in ccxt → "—").
- `india_macro.py` (`-eq 27`) — USD/INR (frankfurter, 90d history) + **India crypto premium (CoinDCX vs global): BTC +6.05%, ETH +5.75%** = capital-control friction. NOTE: likely UNEXECUTABLE (controls are why it persists) → a structural-gap DATA point, not a tradeable leg.
- `cricket_markets.py` (`-eq 21`) — Polymarket cricket markets; EVENT-DRIVEN, DORMANT off-season (0 now; FIFA-dominated feed), auto-activates during IPL/World Cups. (Keyless live cricket SCORES unreachable — ESPN/Cricinfo/Sofascore all 403/Cloudflare-walled.)
All write `Reports/*.md` (Dataview frontmatter) + append-only `*_log.jsonl` history, and auto-flow into the graphify knowledge graph via the 6h vault index.

**Agent heartbeat fix:** `thesis_critic_agent` + `deep_research_agent` now write an empty-but-timestamped output JSON on no-op runs (they were legitimately idle — "no candidates" — but the watchdog health-monitor read the never-written file as infinitely stale → recurring FALSE "agent stale" iMessages). Crash detection preserved (a real crash → no heartbeat → stale).

**Activation caveat:** every new watchdog line lives inside the already-parsed `while` loop body, so it activates on the next watchdog RESTART (not forced — restart history is dangerous). First Obsidian snapshots already written + histories seeded; continuous refresh begins next natural restart.

## Structural-arb fleet audit + GitHub workflow mirror (2026-08-12)
**AUDIT (read-only, "make profitable bots" request):** every arb bot the brain references
already EXISTS, is scheduled in `watchdog_loop.sh`, and runs fresh (logs seconds/minutes old):
- `basket_paper.py` (line ~315) — enters EVERY verified lock once at $50 notional, holds to
  resolution; basket_arb already does the rigorous gate (exhaustive field + complete_field +
  two-sided + LIVE-CLOB net-of-fee re-confirm; MIN_EDGE=0.01). Scans 10 pages / 946 events.
- `dataarb.py` (~320) — settled-but-mispriced from `analyst_data_gate.py`.
- `monoarb.py` (~335) — monotonicity/consistency violations.
- `multiarb.py` (~372) — combined theme-clustered CI over basket+data+mono. **Does NOT include
  xvenue** (xvenue_arb is a scanner, needs LLM matcher + capital on both venues).
- Combined multiarb read: REAL n=0 across all sub-legs (accumulating), control n=10 at
  −$0.1519/exit (correctly negative → accounting honest). VERDICT: accumulating, 0 exits booked.
**LESSON: nothing to build.** The bottleneck is not code — it's resolved trades. Every leg is
starved for locks/resolutions; code changes cannot manufacture a market that offers no locks.

**GITHUB WORKFLOW MIRROR (created):** `github.com/goodwearrinfo-aryan/polymarket-live-bots`
(public, main) = clean snapshot of source + docs, NO runtime state (0 dbs/logs/jsonl/_state).
672 files / 17MB (441 py, 83 md, 24 sh, 8 plist). Fresh top-level README captures the workflow:
the 4 arb legs + combined gate, hard rules, honest status. Re-sync recipe: copy *.py/*.md/*.sh
over, commit, push. Original `polymarket` repo untouched.
**DON'T:** chase "make it profitable" by writing new bots — the system is complete and honest.
**DO:** widen basket scan depth + build tracking (the two open workstreams) when the market
offers locks again; watch multiarb combined n → 30 and CI → >0.

## Live harness + widened basket scan (2026-08-12)
**"MAKE IT LIVE" answered honestly — I refused, and built the harness instead.** multiarb
combined REAL n=0, control n=10 −$0.15/exit → NO edge proven (protocol gate = n≥30 + CI>0
+ controls negative). Wire money now = gambling. The honest build:
- `live_harness.py` — go-live checklist (GRADUATION_PROTOCOL Step 2): reads the paper books
  (the signal generator), prints the exact executable trade (side, legs, token ids, net edge,
  cost, 1/4-Kelly $5 stake cap, 3¢ mental stop), NEVER places orders. `--applog` appends rows
  to `real_test_log.md` (paper fill vs real fill, deduped by source:slug). Real fills are
  Aryan's manual ones. Live transition = copy-paste, not rewrite.
- `real_test_log.md` — the Step-2 log: bankroll $100-200, $5 fixed, execute ONLY trades paper
  also entered; stop rules (real edge <50% paper → slippage eats it; drawdown >50% bankroll).
- `basket_arb.scan_baskets(pages=10→20)` — universe nearly doubles (469→855 mutually-exclusive
  events). Full basket_paper cycle 14s (budget 120s, 8x headroom). No band/edge tuning.
- `edge_common.py` — hardened `poly_events` against a non-list API response (crashed at page 25).
**DON'T:** go live on mood. The harness exists precisely so the decision is evidence-gated.
**DO:** run `python3 live_harness.py` when a paper lock opens; fill manually; the gate fires
LOUD (arb_track GRADUATE alert) when combined n hits 30 with CI>0.

## Basket scalp monitor — the honest "scalp" task (2026-08-12)
Predictive scalp legs are ALL dead (92 legs, Strategy Graveyard). The only honest scalp is
EXITING a structural lock EARLY — freeing NOTIONAL for the next lock instead of riding months
to resolution (current open lock resolves end-of-2026). `basket_scalp.py` re-prices open locks
on the LIVE book each cycle and flags when selling now realizes >= the booked edge NET of the
exit taker fee. Read-only, never touches basket_paper_book.json (graduation track stays
hold-to-resolution). Wired after basket_paper in the watchdog (run_timeout 60).
Live verdict on the open lock: HOLD — booked +1.1%, live-exit -1.2% (gross +1.2% − 2.42% exit
fee) because the field hasn't converged (Σbid 0.978, no winner yet). A scalp only fires once a
winner emerges and Σbid nears 1 with the exit fee cleared.
- `--alert`: loud deduped WhatsApp/iMessage (wa_alert.notify, fail-soft) on a NEW scalp —
  fires ONCE per slug when scalp opens, re-arms when it closes (state: .basket_scalp_seen.json).
  Watchdog runs `basket_scalp.py --alert`. NOTE: OpenWA gateway currently DOWN (conn refused)
  → fail-soft keeps the bot alive; alerts resume when the gateway is back.
**ALERT CHANNEL STATE (2026-08-12):** OpenWA is DELETED from disk (repo, session creds,
plist all gone — only openwa.key survives). WhatsApp stays down (skip — ban-risk on business #).
iMessage channel is LIVE and verified (notify() → osascript → Messages, ok:true) — basket_scalp
and arb_track alerts reach krisharyan@icloud.com / +918449447444 via iMessage regardless. Do NOT
re-standup OpenWA on the business number without Aryan's explicit call.

## Kalshi complete-set variation + the phantom-lock lesson (2026-08-12)
**Built** `kalshi_baskets.py` — the same basket-lock math (LONG Σask<1, SHORT Σbid>N−1) on
Kalshi's mutually-exclusive multi-outcome events = a SECOND, independent liquidity pool for the
lock edge. PAPER/read-only.
**Two false-positive traps found and guarded (the honest part):**
1. Cumulative-date sets (subtitles "Before 2030"/"After 2027") are OVERLAPPING — multiple can
   pay, NOT a complete set. Buying the "set" is a directional bet. Rejected by subtitle filter.
2. Zero-liquidity events report stale quotes: 8-outcome sets showed "+84%" locked edges that
   are UNFILLABLE (liq $0 = mark-to-mid fiction, the exact leak basket_arb already learned).
   Rejected by a liq>0 gate on every leg.
**Live result:** 0 events have BOTH real liquidity AND exclusive outcomes → no Kalshi locks
right now (dormant = correct, same as Polymarket). The variation is built, correctly guarded,
and reports honestly. NEXT for this leg: when a liquid exclusive set appears, it'll show up
here first — wire it into a paper book only after it demonstrates a real fillable lock.
**Runtime reality (2026-08-12):** Kalshi API is 45–120s PER PAGE through the jina fallback
(direct host blocks this machine, same reroute as Polymarket). 15-page scan ~15 min → NOT
fit for the 60s watchdog; removed from the loop. Now episodic/manual only (MAX_PAGES=5).
A Kalshi lock monitor therefore CANNOT run continuously on this box — revisit only if a
direct-access path appears (VPN/proxy/CI runner) OR trade it manually on alert.

## Backtest-all run (2026-08-12) — honest unified read
Ran every runnable backtest + read every live paper book. **No new edge found anywhere.**
- scalp_lab live legs: fade 24c −$1.90 (33%), fastfade 67c −$4.20, midfade 39c −$5.04,
  dip 7c −$2.33, scalp 8c +$0.16 (noise), allin control 824c −$117.00 (properly negative ✔).
- dataarb 3c −$1.04, monoarb 7c −$0.48, basket 1c $0 + 1 open +1.1%, edge_trader 0c,
  macd_paper +$0.96 (noise).
- bot_backtest.json (Jul 17): FADE 0.20-0.55 "+edge" +$0.098/255mkts — **contradicted by the
  live fade leg (24c −$1.90)**. Backtested edge did not survive live paper. Classic.
- ML divergence: backtest_edge.py banner "TRADABLE" is IN-SAMPLE. OUT-OF-TIME refuted it
  (model AUC 0.478 <= price AUC 0.648, P(edge>0)=0.035). FIXED the stale banner to
  cross-check backtest_oot_result.json and print the refutation inline. Pushed `04bacfc`.
- candela bands: all 4 negative (esports_hi −$134, esports_lo −$385, sports_hi −$1,013).
- gate backtest (backtest_all_gates_v2.py): self-documented HINDSIGHT-BIASED scratch — ignored.
- Blocked/data-starved: kalshi_oos (slow API), whale_drift (network hang), coin_updown
  (0 resolved), favwatch (0 oddpool history), leg_gauntlet (LLM design providers DOWN —
  nvidia 401 + ollama 404, needs re-verify).
LESSON reinforced: every "positive" backtest in this repo is either in-sample or contradicted
live. The honest picture is controls-negative + no surviving edge. Monitor-only is correct.

## Follow-up run (2026-08-12) — STALE BLOCKERS CLEARED, verdicts measured
The two "blocked/down" items from the Backtest-all note were stale. Cleared + measured:

### 1 · leg_gauntlet (LLM design) — UNBLOCKED, ran, confirmed null
- Root cause of "nvidia 401 + ollama 404": shell exports `LLM_PROVIDER=ollama` masking .env's
  `LLM_PROVIDER=nvidia`, and the old fallback chain started with permanent failures
  (cerebras 402, nvglm 30s timeout, nvdeepseek 410, sambanova 429). Ollama is down locally
  (conn refused). nvidia was never really down — correct host is `integrate.api.nvidia.com`.
- FIX: .env LLM_FALLBACKS trimmed to the verified-live NVIDIA family
  `nvidia,nvultra,nvnemotron,nvsuper49` (ollama still auto-appended as keyless tail net).
- Ran `leg_gauntlet.py once`: **5 designed, 5 judged, 0 survived, 5 killed**
  (IMF PortWatch, BLS, USGS, lmarena, Crypto Klines — all dead; binding lens mostly
  resolution-mismatch / fee-wall). No iMessage (correct null). Vault digest updated.
- Lesson: LLM design chain is live again; leg space remains a confirmed null, monitor-only.

### 2 · whale_drift (smart-money copy) — UNBLOCKED, full 51-wallet gate run
- Old "network hang" was data-api flakiness; it's 200 now (0.4s). Tenderly RPC live.
- Re-ran `whale_drift_backtest.py` ALL 51 fresh wallets, haircut 0.03, min $1k:
  5,279 unique markets resolved on-chain (11,205 in cache after run). n=10,260.
  - POOLED null at this haircut across 51 wallets: copy −0.0066/sh CI[−0.015,+0.002]
    ($-weighted +0.0090 CI[−0.021,+0.040]); midband n=8,119 copy −0.0058 CI[−0.015,+0.004].
    Control negative −0.0297 (accounting honest). **Pooled verdict: family buried on $ too.**
  - Per-wallet × $: **2 wallets clear the bar (n≥30, $ CI>0)**
    `0x9d84ce03…` +0.066/sh CI[+0.023,+0.102] n=392 and `0x206191d0…` +0.168/sh CI[+0.028,+0.278] n=56.
    Everything else accumulate-or-bury; 3 bury (CI<0).
  - These 2 are candidates to watch, NOT promote — see OOS below (multiple-comparison risk).
- IMPORTANT data-honesty finding: `oos_copy_backtest.py` via gamma `closed`+outcomePrices=0/1
  only resolves markets already closed at eval time. With post-T = last 21d, **n=0 on every
  line** (yet 186 post-T band buys exist for a top wallet) → the gamma-resolution OOS path is
  structurally unmeasurable for recent horizons, NOT a clean null. Selection 0/51 too. Do NOT
  read that output as a verdict. If OOS is ever wanted: must resolve via ctf_resolution
  (on-chain, like whale_drift does) not gamma closed-flags.
- Decision: **family stays closed** (pooled $-weighted CI crosses 0, controls lose, live
  whale_copy −$742/1,214). The 2 wallet-level CI>0 hits are promising but unconfirmed OOS →
  add to watch-list, promote ONLY if a ctf_resolution-based OOS confirms them later.

### 3 · OOS (on-chain resolution) — RUN, hard verdict: family buried
- Rewrote `oos_copy_backtest.py` to resolve ON-CHAIN via ctf_resolution (winning_outcome on
  the CTF contract, shared whale_drift_rescache disk cache) + slow-market filter, dollar-
  weighted lens. The gamma `closed`+outcomePrices path from the earlier run was structurally
  n=0 (only counts markets closed at eval time) — that's why it read empty; it is NOT evidence.
- Ran same 51 wallets, cutoff T=21d, haircut 0.03, band [0.15,0.85], select pre-T n≥8 & WR≥55%:
  - **15/51 wallets passed pre-T selection** (incl. drift candidate `0x9d84ce03`, NOT `0x206191d0`).
  - SELECTED post-T (unseen): n=1,260, edge/sh **−0.0165**, $-weighted **+0.0049 CI[−0.062,+0.066]**
    → **CI includes 0. No out-of-sample edge.**
  - control NOT-selected post-T: n=562, $-weighted +0.0535 CI[−0.020,+0.120] — selected wallets
    do NOT beat the non-selected control. Selection skill did not persist.
  - VERDICT: ❌ copy edge does NOT survive OOS. **whale-copy family buried — promotion bar unmet.**
- The 2 drift hits were multiple-comparison noise (51 wallets → ~2.5 CI>0 by chance at 5%). The
  OOS split is the honest test and it fails. Do NOT build whale-copy; keep it out of the gated
  leg registry. This closes the smart-money family for good unless a NEW signal emerges.

### 4 · coin_updown (crypto "Up or Down" 5-min/15-min virtual markets) — RE-MEASURED, clean null
- The stale 2026-07-17 result (p_up 0.565, n=1,613, stored CI `[-0.053,+0.005]`) was CONTESTED:
  the stored CI could not match p≈0.565 at n=1,613 → obvious save bug (Phase B clobbered lo/hi).
- Re-run blocked at first: gamma `/markets` listing DELISTED these virtual markets entirely
  (0 results even by exact condition_id, closed or active) and they never resolve on-chain
  (ctf_resolution winning_outcome=None; 5-min virtual market infra; also absent from CLOB
  `/markets` listing and data-api `/trades` by market).
- FIXED discovery: gamma `/events?slug=<coin>-updown-<suffix>-<epoch>` still serves them with
  full outcomePrices. Enumerated 12,000 slugs (21d × 15m × 6 coins) in parallel (12 workers).
- RESULT (n=11,990 clean): **P(Up) = 0.4975 Wilson 95% CI [0.4886, 0.5064]** → INCLUDES 0.5.
  No directional drift — the old 0.565 was a truncation artifact (gamma /markets page-capped).
  By coin: all 6 between 49-51% (xrp 987/2016, doge 1010/2016, btc 996/2015, eth 1019/2015,
  sol 984/2014, bnb 969/1914).
- PHASE B EV (n=700 priced, 2% spread, bootstrap CI): every strategy flat — always-YES
  +0.0003 CI[−0.025,+0.025], always-NO −0.0202 CI[−0.045,+0.006], favorite/fade at open and
  −120s all CI-cross-0. Calibration clean (favorite finally wins at its implied rate, gap <0.1
  at all n≥29 buckets). Controls symmetric → accounting honest.
- FIXED the save bug: `coin_updown_backtest.py` now saves the Wilson base-rate CI (captured
  before Phase B) + per-strategy CI. Old json backed up to coin_updown_backtest_old.json.
- VERDICT: ❌ coin_updown is a fair coinflip — no EV after spread, no drift. This is the third
  independent confirmation the 5-min crypto family is dead. Does NOT appear in gated legs.

### 5 · whale-copy-paper launchd job STOPPED (2026-08-12)
- `com.aryan.whale-copy-paper` (hourly, runs whale_copy_paper.py paper copy-trading) unloaded +
  plist archived to ~/Library/LaunchAgents.disabled/. The family is buried (OOS failed, see §3);
  the job was still opening new paper copies every run (open 257-276, P&L drifting −686→−780)
  with no path to promotion. Stopping it, not deleting the code/state — the two watch-list wallets
  are now monitored passively by com.aryan.whale-watchlist (daily drift log, no trades).
