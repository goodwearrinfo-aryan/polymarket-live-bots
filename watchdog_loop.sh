#!/bin/bash
# Polymarket bot watchdog loop.
# Every CHECK seconds: if the bot process is gone OR run_output.txt has been
# silent longer than STALE seconds, relaunch the bot (dry-run, detached).
# Started by watchdog.command; logs to watchdog.log.

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"
# Framework Python 3.14 has requests + the bot's full deps and is the TCC-clear
# interpreter (same one copybot_watchdog.sh uses). A bare `command -v python3` can
# resolve to a deps-light Python → the relaunched bot dies instantly on
# `ModuleNotFoundError: No module named 'requests'` and crash-loops. Pin it.
PY="/Library/Frameworks/Python.framework/Versions/3.14/bin/python3"
[ -x "$PY" ] || PY=$(command -v python3 || command -v python)
LOG="$DIR/watchdog.log"
CHECK=60      # seconds between checks
STALE=240     # log silence (s) before considered hung

# ── Self-heal thresholds (added 2026-06-07: "fix it when it stops / doesn't
#    open trades / stops refreshing" — runs unattended, no human needed) ───────
ENGINE_STALE=180     # scalp_engine_state.json silent this long => hung => restart
LAB_STALE=200        # scalp_lab_state.json silent this long => not refreshing
LAB_RUN_TIMEOUT=420  # cap one scalp_lab 'once' so a hang can't freeze the loop.
                     # 2026-06-29: was 120 but a full once-cycle now takes ~304s
                     # (1127 markets x many legs on a 4364-exit book). At 120s every
                     # cycle was KILLED mid-scan -> state never refreshed -> the
                     # kill/relaunch storm left held DB locks (EDEADLK in belief_ledger)
                     # and froze the book from 2026-06-24. 420s = ~116s margin for feed variance.
ZERO_FETCH_ALERT=5   # consecutive 0-market fetches => feed down => cannot trade

log() { echo "[$(date '+%F %T')] $1" >> "$LOG"; }
alert() { osascript -e "display notification \"$1\" with title \"Polymarket watchdog\"" 2>/dev/null \
            || echo "[$(date '+%F %T')] ALERT: $1" >> "$DIR/alerts.log"; }  # Linux/VPS has no osascript -> log it

# alert_throttled KEY MSG [COOLDOWN_SEC] -> notify at most once per cooldown per
# KEY (default 30min), so a sustained outage doesn't spam a notification/cycle.
# log() still records every cycle; only the macOS popup is throttled.
ALERT_COOLDOWN=1800
# 2026-06-11: alerts also go to iMessage — a dying bot must never fail silently
# again (UnboundLocalError killed every scan for 5 min; only the log knew).
imsg_alert() {
  for t in "krisharyan@icloud.com" "+918449447444"; do
    osascript -e "tell application \"Messages\"
      set s to 1st service whose service type = iMessage
      send \"🚨 watchdog: $1\" to buddy \"$t\" of s
    end tell" >/dev/null 2>&1 || true
  done
}
alert_throttled() {
  local f="$DIR/.alert_$1" now last
  now=$(date +%s); last=$(cat "$f" 2>/dev/null || echo 0)
  if [ $(( now - last )) -ge "${3:-$ALERT_COOLDOWN}" ]; then
    echo "$now" > "$f"; alert "$2"; imsg_alert "$2"
  fi
}

mtime_of() {
  stat -f %m "$1" 2>/dev/null || stat -c %Y "$1" 2>/dev/null || echo 0
}

# proc_matches PID PATTERN -> true if PID is alive AND its command matches PATTERN
# (guards against PID reuse: a recycled pid for an unrelated process won't match).
proc_matches() {
  [ -n "$1" ] && ps -p "$1" -o command= 2>/dev/null | grep -q "$2"
}

# fresh FILE MAXAGE -> success if FILE was modified within MAXAGE seconds
fresh() {
  [ -f "$1" ] || return 1
  [ $(( $(date +%s) - $(mtime_of "$1") )) -le "$2" ]
}

# run_timeout SECS cmd... -> kill cmd after SECS (macOS has no `timeout`)
run_timeout() { perl -e 'alarm shift; exec @ARGV' "$@"; }

is_alive() {
  [ -f "$DIR/bot.pid" ] || return 1
  proc_matches "$(cat "$DIR/bot.pid" 2>/dev/null)" "polymarket_bot.py"
}

relaunch() {
  # PATH-QUALIFIED: match only THIS dir's bot. A bare "polymarket_bot.py" pattern
  # also matches the launchd-supervised copy in the Claude outputs dir, so the two
  # watchdogs were killing each other's bot (~every 15min). Both are now anchored
  # to their own absolute path (this bot is launched as "$DIR/polymarket_bot.py").
  pkill -f "$DIR/polymarket_bot.py" 2>/dev/null
  sleep 2
  nohup "$PY" "$DIR/polymarket_bot.py" --dry-run >> "$DIR/run_output.txt" 2>&1 < /dev/null &
  echo $! > "$DIR/bot.pid"
  disown 2>/dev/null || true
  log "RELAUNCHED by watchdog (pid $(cat "$DIR/bot.pid"))"
}

# On (re)start, refresh the scalp engine so it picks up any code changes — the
# keep-alive block below will relaunch it with the current scalp_engine.py.
# Path-qualified; clear the engine pid so the first loop relaunches deterministically.
pkill -f "$DIR/scalp_engine.py" 2>/dev/null
pkill -f "$DIR/edge_trader.py" 2>/dev/null
pkill -f "$DIR/resolution_watcher.py" 2>/dev/null
: > "$DIR/scalp_engine.pid"
sleep 2
log "watchdog started (check=${CHECK}s, stale>${STALE}s)"
while true; do
  now=$(date +%s)
  if [ -f "$DIR/run_output.txt" ]; then
    age=$(( now - $(mtime_of "$DIR/run_output.txt") ))
  else
    age=999999
  fi

  if ! is_alive; then
    log "DOWN - process not running, relaunching"
    relaunch
  elif [ "$age" -gt "$STALE" ]; then
    log "HUNG - log silent ${age}s, restarting"
    relaunch
  else
    log "HEALTHY - ${age}s ago"
  fi

  now2=$(date +%s)

  # Hourly P&L iMessage — driven HERE (the nohup-orphan watchdog), NOT the launchd
  # com.aryan.polymarket-pnl-alert job: launchd can't drive osascript->Messages (no Automation
  # grant / no GUI session), so that job exit-78'd and never delivered. This orphan HAS the
  # interactive-context grant, so the send actually lands. Throttled to 3600s. Fail-soft.
  pnlstamp=$(cat "$DIR/.last_pnl" 2>/dev/null || echo 0)
  if [ $(( now2 - pnlstamp )) -ge 3600 ]; then
    run_timeout 60 "$PY" "$DIR/hourly_pnl_alert.py" >> "$DIR/pnl_alert.log" 2>&1 || true
    echo "$now2" > "$DIR/.last_pnl"
  fi

  # Venue-watch sentinel every ~6h: fires only when a THINNER venue (Limitless/Base) lists a
  # real non-coinflip / negRisk multi-outcome market = a structural-arb substrate Polymarket is
  # too calibrated to offer. Read-only, keyless, fail-soft. Today: all coinflips → quiet.
  vwstamp=$(cat "$DIR/.last_venuewatch" 2>/dev/null || echo 0)
  if [ $(( now2 - vwstamp )) -ge 21600 ]; then
    run_timeout 40 "$PY" "$DIR/venue_watch.py" >> "$DIR/venue_watch.out" 2>&1 || true
    echo "$now2" > "$DIR/.last_venuewatch"
  fi

  # SURVIVOR-WATCH: track the legs left enabled after the kill-sweeps. Deterministic (no LLM),
  # read-only. Silent unless a survivor GRADUATES (n>=30, bootstrap CI>0, beats control -> run DSR)
  # or turns KILL-NOW (CI<0). ~6h cadence; iMessage only on a NEW graduate/kill event. Fail-soft.
  swstamp=$(cat "$DIR/.last_survivorwatch" 2>/dev/null || echo 0)
  if [ $(( now2 - swstamp )) -ge 21600 ]; then
    run_timeout 60 "$PY" "$DIR/survivor_watch.py" >> "$DIR/survivor_watch.out" 2>&1 || true
    echo "$now2" > "$DIR/.last_survivorwatch"
  fi

  # Refresh the phone-viewable PDF of live trades every ~15 min (throttled)
  pdfstamp=$(cat "$DIR/.last_pdf" 2>/dev/null || echo 0)
  if [ $(( now2 - pdfstamp )) -ge 900 ]; then
    "$PY" "$DIR/trades_report.py" >/dev/null 2>&1 || true
    echo "$now2" > "$DIR/.last_pdf"
  fi

  # Resolution-latency probe: does a latency edge even exist? Runs at most once
  # per ~24h and STOPS permanently once it produces a real verdict (network is
  # flaky, so it self-retries daily until one run completes). Fail-soft; never
  # touches the bot. Writes ml/reprice.log; sentinel ml/.latency_done disables it.
  if [ ! -f "$DIR/ml/.latency_done" ]; then
    latstamp=$(cat "$DIR/.last_latency" 2>/dev/null || echo 0)
    if [ $(( now2 - latstamp )) -ge 86400 ]; then
      echo "$now2" > "$DIR/.last_latency"
      "$PY" "$DIR/ml/measure_reprice.py" --max-markets 150 --fidelity 1 > "$DIR/ml/reprice.log" 2>&1 || true
      if grep -q "=== VERDICT ===" "$DIR/ml/reprice.log" 2>/dev/null; then
        touch "$DIR/ml/.latency_done"
        log "latency probe SUCCEEDED -> ml/reprice.log (probe disabled)"
      else
        log "latency probe attempt failed (network?); will retry in ~24h"
      fi
    fi
  fi

  # Oddpool nearres scanner: surface esports markets eligible for nearres entry.
  # Runs every 10 cycles (~10 min) to stay within free tier (1K req/month).
  # 2 API calls per run = ~8K/month at 10-min cadence — well within limit.
  ODDPOOL_CYCLE_FILE="$DIR/.oddpool_cycle"
  oddpool_cycle=$(cat "$ODDPOOL_CYCLE_FILE" 2>/dev/null || echo 0)
  oddpool_cycle=$(( oddpool_cycle + 1 ))
  echo "$oddpool_cycle" > "$ODDPOOL_CYCLE_FILE"
  if [ $(( oddpool_cycle % 180 )) -eq 0 ]; then
    source "$DIR/.env" 2>/dev/null || true
    run_timeout 30 "$PY" "$DIR/oddpool_nearres.py" >> "$DIR/oddpool_nearres.log" 2>&1 || true
  fi

  # Read-only research LOGGERS (no trades, paper instrumentation). Each ~15 cycles
  # (~15 min): detect events + revisit pending + refresh the Obsidian report.
  # Fail-soft, timeout-wrapped. These answer make-or-break Qs for 2 candidate legs
  # (sharpdrift = vetted-wallet consensus drift; latticesnap = ladder-break re-cohere)
  # WITHOUT spending a config slot — graduate to a leg only if the logs show edge.
  if [ $(( oddpool_cycle % 15 )) -eq 0 ]; then
    run_timeout 90 "$PY" "$DIR/sharpdrift_probe.py" once >> "$DIR/sharpdrift.log" 2>&1 || true
    run_timeout 120 "$PY" "$DIR/whalecopy_probe.py" once >> "$DIR/whalecopy.log" 2>&1 || true
    run_timeout 60 "$PY" "$DIR/latticesnap_logger.py" once >> "$DIR/latticesnap.log" 2>&1 || true
    run_timeout 90 "$PY" "$DIR/tapeshock_logger.py" once >> "$DIR/tapeshock.log" 2>&1 || true
    run_timeout 90 "$PY" "$DIR/whalexit_logger.py" once >> "$DIR/whalexit.log" 2>&1 || true
    run_timeout 90 "$PY" "$DIR/newsmove_logger.py" once >> "$DIR/newsmove.log" 2>&1 || true
  fi

  # Deribit option-chain snapshot -> forward tape for the optopsy skew backtest
  # (deribit_optopsy_bridge.py, now lives in claud-live/). Pure-stdlib + keyless; the
  # script self-guards to ONE real fetch/day (marker file), so these ~3h checks are
  # cheap no-ops after the grab. Builds the time series optopsy.simulate() needs.
  DERIBIT_BRIDGE="/Users/aryanagarwal/claud-live/deribit_optopsy_bridge.py"
  DERIBIT_LOG="/Users/aryanagarwal/claud-live/deribit_snapshot.log"
  if [ $(( oddpool_cycle % 180 )) -eq 0 ] && [ -f "$DERIBIT_BRIDGE" ]; then
    run_timeout 60 "$PY" "$DERIBIT_BRIDGE" snapshot --asset BTC >> "$DERIBIT_LOG" 2>&1 || true
    run_timeout 60 "$PY" "$DERIBIT_BRIDGE" snapshot --asset ETH >> "$DERIBIT_LOG" 2>&1 || true
    # weekly DVOL/realized history refresh (--if-stale 7 self-throttles); keeps vrp-backtest current
    run_timeout 60 "$PY" "$DERIBIT_BRIDGE" backfill --asset BTC --if-stale 7 >> "$DERIBIT_LOG" 2>&1 || true
    run_timeout 60 "$PY" "$DERIBIT_BRIDGE" backfill --asset ETH --if-stale 7 >> "$DERIBIT_LOG" 2>&1 || true
  fi
  # WHALE-COPY GRADUATION WATCHER (the one real edge candidate). Every ~60 cycles,
  # check if the gated paper leg crossed its verdict at n>=30. Fire iMessage ONCE per
  # terminal state (sentinel), then go quiet — the only +EV claim this project allows
  # is n>=30 + CI>0 + beats control, and a confirmed DEAD is just as worth knowing.
  if [ $(( oddpool_cycle % 60 )) -eq 0 ]; then
    gv=$(run_timeout 60 "$PY" "$DIR/whalecopy_probe.py" gradecheck 2>/dev/null)
    gstatus=${gv%%|*}
    if [ -n "$gstatus" ] && [ "$gstatus" != "PENDING" ] && [ ! -f "$DIR/.whalecopy_$gstatus" ]; then
      : > "$DIR/.whalecopy_$gstatus"
      case "$gstatus" in
        GRADUATED) imsg_alert "🏆 whale-copy leg GRADUATED — $gv (n>=30, CI>0, beats control). First proven edge — run DSR + decide real config." ;;
        DEAD)      imsg_alert "⚰️ whale-copy leg DEAD — $gv (n>=30, CI<=0). The +0.9c was noise. Honest null." ;;
        *)         imsg_alert "🟡 whale-copy leg INCONCLUSIVE — $gv (n>=30 but CI straddles 0 / loses to control)." ;;
      esac
      log "whalecopy graduation watcher fired: $gv"
    fi
  fi

  # 24/7 pm-AGENTS (ALL 15) — LLM-powered by Aryan's FREE OpenAI-compatible API
  # via .agent_llm.json. Each gathers data in Python, asks the LLM, writes
  # Obsidian Reports/agent_*.md. Safe no-op until api_key is filled. Staggered
  # by each agent's schedule so free-tier rate limits are never hit at once.
  # CADENCE (2026-06-18): sped up from 6-24h to HOURLY per request. Every individual LLM agent now
  # runs every 60 cycles (~1h), staggered 3 cycles apart. Ollama (numeric) agents are CONTIGUOUS
  # (offsets 0-33) so the 4.7GB model stays warm across them (cold-load only if >5min idle); the 4
  # NVIDIA reasoning agents follow (36-45). Each dispatch BLOCKS the watchdog for its runtime, so the
  # bot's once-cycle is briefly delayed ~16x/h — acceptable for paper. digest/grade/chain stay slower.
  # ── Ollama numeric (contiguous → warm) ──
  if [ $(( oddpool_cycle % 60 )) -eq 0 ]; then run_timeout 280 "$PY" "$DIR/pm_agents_24x7.py" leg-auditor >> "$DIR/pm_agents.log" 2>&1 || true; fi
  if [ $(( oddpool_cycle % 60 )) -eq 3 ]; then run_timeout 280 "$PY" "$DIR/pm_agents_24x7.py" onchain-settler >> "$DIR/pm_agents.log" 2>&1 || true; fi
  if [ $(( oddpool_cycle % 60 )) -eq 6 ]; then run_timeout 280 "$PY" "$DIR/pm_agents_24x7.py" regime-watcher >> "$DIR/pm_agents.log" 2>&1 || true; fi
  if [ $(( oddpool_cycle % 60 )) -eq 9 ]; then run_timeout 280 "$PY" "$DIR/pm_agents_24x7.py" dsr-gatekeeper >> "$DIR/pm_agents.log" 2>&1 || true; fi
  if [ $(( oddpool_cycle % 60 )) -eq 12 ]; then run_timeout 280 "$PY" "$DIR/pm_agents_24x7.py" resolution-checker >> "$DIR/pm_agents.log" 2>&1 || true; fi
  if [ $(( oddpool_cycle % 60 )) -eq 15 ]; then run_timeout 280 "$PY" "$DIR/pm_agents_24x7.py" whale-vetter >> "$DIR/pm_agents.log" 2>&1 || true; fi
  if [ $(( oddpool_cycle % 60 )) -eq 18 ]; then run_timeout 280 "$PY" "$DIR/pm_agents_24x7.py" capacity-estimator >> "$DIR/pm_agents.log" 2>&1 || true; fi
  if [ $(( oddpool_cycle % 60 )) -eq 21 ]; then run_timeout 280 "$PY" "$DIR/pm_agents_24x7.py" counterparty-profiler >> "$DIR/pm_agents.log" 2>&1 || true; fi
  if [ $(( oddpool_cycle % 60 )) -eq 24 ]; then run_timeout 280 "$PY" "$DIR/pm_agents_24x7.py" news-arb >> "$DIR/pm_agents.log" 2>&1 || true; fi
  if [ $(( oddpool_cycle % 60 )) -eq 27 ]; then run_timeout 280 "$PY" "$DIR/pm_agents_24x7.py" data-resolver >> "$DIR/pm_agents.log" 2>&1 || true; fi
  if [ $(( oddpool_cycle % 60 )) -eq 30 ]; then run_timeout 280 "$PY" "$DIR/pm_agents_24x7.py" backtest-auditor >> "$DIR/pm_agents.log" 2>&1 || true; fi
  if [ $(( oddpool_cycle % 60 )) -eq 33 ]; then run_timeout 280 "$PY" "$DIR/pm_agents_24x7.py" correlation-auditor >> "$DIR/pm_agents.log" 2>&1 || true; fi
  # ── NVIDIA reasoning (after the Ollama run; free-tier, 24 runs/day each) ──
  if [ $(( oddpool_cycle % 60 )) -eq 36 ]; then run_timeout 280 "$PY" "$DIR/pm_agents_24x7.py" anomaly-scout >> "$DIR/pm_agents.log" 2>&1 || true; fi
  if [ $(( oddpool_cycle % 60 )) -eq 39 ]; then run_timeout 280 "$PY" "$DIR/pm_agents_24x7.py" bias-detective >> "$DIR/pm_agents.log" 2>&1 || true; fi
  if [ $(( oddpool_cycle % 60 )) -eq 42 ]; then run_timeout 280 "$PY" "$DIR/pm_agents_24x7.py" edge-refuter >> "$DIR/pm_agents.log" 2>&1 || true; fi
  if [ $(( oddpool_cycle % 60 )) -eq 45 ]; then run_timeout 280 "$PY" "$DIR/pm_agents_24x7.py" quant >> "$DIR/pm_agents.log" 2>&1 || true; fi

  # pm-DIGEST: synthesis + iMessage on critical flags — every 6h (NOT hourly: avoids alert spam)
  if [ $(( oddpool_cycle % 360 )) -eq 48 ]; then run_timeout 280 "$PY" "$DIR/pm_digest.py" >> "$DIR/pm_agents.log" 2>&1 || true; fi
  # pm-GRADE: self-grading scorecard of agent reliability — every 12h
  if [ $(( oddpool_cycle % 720 )) -eq 51 ]; then run_timeout 200 "$PY" "$DIR/pm_grade.py" >> "$DIR/pm_agents.log" 2>&1 || true; fi
  # pm-CHAIN: auto-gauntlet (vetter→resolution→counterparty→capacity→verdict) ~every 2 days
  if [ $(( oddpool_cycle % 2880 )) -eq 1200 ]; then run_timeout 400 "$PY" "$DIR/pm_chain.py" >> "$DIR/pm_agents.log" 2>&1 || true; fi
  # LEG-GAUNTLET: design-stage hunt for a NEW leg — 4 designers (structural/mechanical/data/
  # wildcard) → 5-lens kill (calibrated-mids/gap-through/fee-wall/already-covered/resolution-
  # mismatch) → survivors. FREE LLM (zero Claude tokens). `--cron` SELF-GATES to weekly (stamps
  # .last_leg_gauntlet before running, so a timeout can't retry-storm). Quiet on the expected
  # 0-survivor null; iMessage ONLY if a candidate clears all 5. Checked hourly (offset 54, free
  # in the %60 stagger); the real run fires ~weekly. Read-only, fail-soft.
  if [ $(( oddpool_cycle % 60 )) -eq 54 ]; then run_timeout 600 "$PY" "$DIR/leg_gauntlet.py" --cron >> "$DIR/leg_gauntlet.log" 2>&1 || true; fi

  # vault frontmatter: re-stamp Dataview YAML every ~10 cycles. Independent jobs (analyst-agent
  # every 15m, the analysis loggers) overwrite Reports/*.md WITHOUT frontmatter, so a note can
  # be invisible to Dataview until the next stamp. Was inside the %30 block (~30m) — too sparse:
  # left freshly-overwritten Reports notes bare longer than the 15m overwrite. %10 bounds the gap
  # under that. Idempotent + cheap (only touches notes lacking frontmatter).
  if [ $(( oddpool_cycle % 10 )) -eq 0 ]; then
    run_timeout 30 "$PY" "$DIR/vault_frontmatter.py" >> "$DIR/vault_frontmatter.log" 2>&1 || true
  fi

  # mid-cadence loggers (gamma + some on-chain) every ~30 cycles
  if [ $(( oddpool_cycle % 30 )) -eq 0 ]; then
    run_timeout 120 "$PY" "$DIR/favwatch_logger.py" once >> "$DIR/favwatch.log" 2>&1 || true
    run_timeout 60 "$PY" "$DIR/overround_logger.py" once >> "$DIR/overround.log" 2>&1 || true
    # geo rollup: re-tag all logger outputs by country -> country_breakdown.md
    run_timeout 30 "$PY" "$DIR/country_rollup.py" >> "$DIR/country_rollup.log" 2>&1 || true
    # procedural memory: refresh the agents' self-calibration inspection note from resolved
    # bets (Reports/procedural_memory.md). Dormant/no-op until the analyst book resolves.
    run_timeout 20 "$PY" "$DIR/procedural_memory.py" --md >> "$DIR/procedural_memory.log" 2>&1 || true
    # lesson resurfacer: weekly spaced-repetition of the paid lessons -> Reports/Lesson Resurfacer.md
    lesstamp=$(cat "$DIR/.last_lesson" 2>/dev/null || echo 0)
    if [ $(( now2 - lesstamp )) -ge 604800 ]; then
      "$PY" "$DIR/lesson_resurface.py" >> "$DIR/lesson_resurface.log" 2>&1 && echo "$now2" > "$DIR/.last_lesson"
    fi
    # ANALYST PIPELINE (NVIDIA NIM): auto edge-hunt — data-gate → resolution/news/3-lens-refute
    # chain on fresh candidates, survivors → the analyst book. Every PM_EVERY sec (default 6h →
    # ~4 runs/day × ≤4 candidates × ~5 LLM calls = ≤80 NVIDIA calls/day, free-tier-safe). --max
    # caps cost/run; the chain self-checks llm_client.configured() and no-ops if the key is gone.
    PM_EVERY=${PM_EVERY:-21600}
    pmstamp=$(cat "$DIR/.last_pm_pipeline" 2>/dev/null || echo 0)
    if [ $(( now2 - pmstamp )) -ge "$PM_EVERY" ]; then
      source "$DIR/.env" 2>/dev/null || true
      run_timeout 300 "$PY" "$DIR/pm_pipeline.py" --discover --max 4 >> "$DIR/pm_pipeline.log" 2>&1 \
        && echo "$now2" > "$DIR/.last_pm_pipeline" \
        && log "analyst pipeline ran (NVIDIA, every ${PM_EVERY}s)"
    fi
    # ports-terminal analysis loggers: refresh ports_data.json + 7 read-only analyses
    # (legpnl/exposure/flow/mover/analyst_mtm/controls/edge_screen) -> *_log.jsonl + Obsidian Reports/
    run_timeout 120 bash "$DIR/run_analysis_loggers.sh" >> "$DIR/analysis_loggers.log" 2>&1 || true
    # basket_arb paper book: enter new verified locks, settle resolved, accumulate the
    # n≥30 track record that graduates basket arb (the lead structural edge). Read-only.
    run_timeout 120 "$PY" "$DIR/basket_paper.py" >> "$DIR/basket_paper.log" 2>&1 || true
    # basket_scalp: EARLY-EXIT scalp watch on open locks (read-only, reads the fresh book).
    # Flags when an open lock can exit early at >= its booked edge, freeing NOTIONAL
    # for the next lock. Graduation track stays hold-to-resolution. Cheap.
    run_timeout 60 "$PY" "$DIR/basket_scalp.py" >> "$DIR/basket_scalp.log" 2>&1 || true
    # analyst DATA GATE (v5): for markets that resolve on a fetchable series (crypto klines,
    # IMF PortWatch), pull the actual data + flag settled-but-mispriced ARB markets. Read-only,
    # fail-soft alert on the ARB kind only. -> analyst_data_gate_log.jsonl + Vault/Reports/.
    run_timeout 180 "$PY" "$DIR/analyst_data_gate.py" once >> "$DIR/analyst_data_gate.log" 2>&1 || true
    # dataarb PAPER LEG: settle resolved positions + open new ones from the data-gate's
    # settled-but-mispriced signals (the ONE edge type that beat this market). Hold-to-resolution,
    # control + bootstrap gate. Runs right AFTER the gate so it reads the fresh datagate_arbs.json.
    run_timeout 60 "$PY" "$DIR/dataarb.py" once >> "$DIR/dataarb.log" 2>&1 || true
  fi
  # sentinel sweep: autonomous deterministic twin of the 3 read-only logger sentinels
  # (xvenue/dataedge/knowledge). Quiet steady-state; loud (deduped, fail-soft iMessage+WA)
  # only on a real signal. No LLM, read-only. Staggered off the gate (-eq 15).
  if [ $(( oddpool_cycle % 30 )) -eq 15 ]; then
    run_timeout 90 "$PY" "$DIR/sentinel_sweep.py" all --alert >> "$DIR/sentinel_sweep.log" 2>&1 || true
  fi
  # monoarb: MONOTONICITY/CONSISTENCY ARB leg — scans the data-gate ladders for logical
  # violations (harder outcome priced above easier) that clear the fee = free locks. Non-
  # predictive, can't lose by construction; dormant-rare on a liquid venue. Staggered -eq 12.
  if [ $(( oddpool_cycle % 30 )) -eq 12 ]; then
    run_timeout 50 "$PY" "$DIR/monoarb.py" once >> "$DIR/monoarb.log" 2>&1 || true
  fi
  # funding_basis: NON-predictive crypto carry probe — delta-neutral funding harvest, gated by a
  # FEE HURDLE so it only opens when |funding| clears round-trip cost (idle in calm regimes, fires
  # in high-funding ones). The honest "trade crypto" lane — directional crypto is the dead taker
  # pattern (Strategy Graveyard). Read-only ccxt, PAPER, control + bootstrap gate. Staggered -eq 18.
  if [ $(( oddpool_cycle % 30 )) -eq 18 ]; then
    run_timeout 60 "$PY" "$DIR/funding_basis.py" once >> "$DIR/funding_basis.log" 2>&1 || true
  fi
  # funding_landscape: KEYLESS cross-venue perp funding DATA logger (read-only research, NOT a leg).
  # Annualizes funding for BTC/ETH/SOL across binance/bybit/okx/hyperliquid + flags where a carry
  # clears the fee hurdle → Obsidian Reports/funding_landscape.md + JSONL history. Open-source data;
  # keeps accumulating. Feeds funding_basis + the venue-edge question. Staggered -eq 9.
  if [ $(( oddpool_cycle % 30 )) -eq 9 ]; then
    run_timeout 60 "$PY" "$DIR/funding_landscape.py" >> "$DIR/funding_landscape.log" 2>&1 || true
  fi
  # KEYLESS open-source DATA loggers (read-only research → Obsidian + JSONL history; NOT legs):
  #   stablecoin_flows (DeFiLlama crypto-liquidity regime) -eq 3
  #   open_interest    (cross-venue perp OI / leverage)    -eq 6
  #   india_macro      (USD/INR history + India crypto premium, capital-control signal) -eq 27
  if [ $(( oddpool_cycle % 30 )) -eq 3 ]; then
    run_timeout 60 "$PY" "$DIR/stablecoin_flows.py" >> "$DIR/stablecoin_flows.log" 2>&1 || true
  fi
  if [ $(( oddpool_cycle % 30 )) -eq 6 ]; then
    run_timeout 60 "$PY" "$DIR/open_interest.py" >> "$DIR/open_interest.log" 2>&1 || true
  fi
  if [ $(( oddpool_cycle % 30 )) -eq 27 ]; then
    run_timeout 60 "$PY" "$DIR/india_macro.py" >> "$DIR/india_macro.log" 2>&1 || true
  fi
  # cricket_markets: KEYLESS Polymarket CRICKET-market logger → Obsidian. Event-driven (dormant
  # off-season, auto-activates during IPL / World Cups / series). Read-only research. Staggered -eq 21.
  if [ $(( oddpool_cycle % 30 )) -eq 21 ]; then
    run_timeout 60 "$PY" "$DIR/cricket_markets.py" >> "$DIR/cricket_markets.log" 2>&1 || true
  fi
  # multiarb: STRUCTURAL-ARB MULTI-LEG — combined CI over basket_paper + dataarb + monoarb (the
  # real +EV sources). Read-only over the books; writes Vault/Reports/multiarb.md. Staggered -eq 20.
  if [ $(( oddpool_cycle % 30 )) -eq 20 ]; then
    run_timeout 40 "$PY" "$DIR/multiarb.py" once >> "$DIR/multiarb.log" 2>&1 || true
  fi
  # arb_track: DETERMINISTIC every-cycle read of the arb multi-leg (cheap, no CLOB) — digest +
  # LOUD deduped alert ONLY on an actionable transition (graduate / control-bug / realization
  # drop / n≥30). The pm-arb-track LLM agent stays on-demand. Staggered -eq 25.
  if [ $(( oddpool_cycle % 30 )) -eq 25 ]; then
    run_timeout 30 "$PY" "$DIR/arb_track.py" once >> "$DIR/arb_track.log" 2>&1 || true
  fi
  # arb_memory: SELF-LEARNING over the profitable arb legs — learns REALIZATION (does the lock
  # pay what entry promised? the gap-through guard) + REGIME (which conditions convert, DORMANT
  # until n>=30) from resolved basket_paper + dataarb exits. Read-only; Vault/Reports/arb_memory.md
  # + arb_memory.json. Runs right after multiarb (same books). Staggered -eq 22.
  if [ $(( oddpool_cycle % 30 )) -eq 22 ]; then
    run_timeout 40 "$PY" "$DIR/arb_memory.py" once >> "$DIR/arb_memory.log" 2>&1 || true
  fi
  # launchd-health sentinel: catch any com.aryan.* job that silently goes exit>0 / 126 (TCC).
  # Deterministic (no LLM) — the copybot exit-1 only surfaced on a manual check; this makes it
  # autonomous + loud (iMessage on a NEW fault, deduped). ~20-cycle cadence. Fail-soft.
  if [ $(( oddpool_cycle % 20 )) -eq 7 ]; then
    run_timeout 30 "$PY" "$DIR/launchd_health.py" >> "$DIR/launchd_health.log" 2>&1 || true
  fi
  # slower-moving loggers (heavy on-chain ctf calls) run less often to spare RPC budget
  if [ $(( oddpool_cycle % 60 )) -eq 0 ]; then
    run_timeout 120 "$PY" "$DIR/deadline_logger.py" once >> "$DIR/deadline.log" 2>&1 || true
  fi
  if [ $(( oddpool_cycle % 120 )) -eq 0 ]; then
    run_timeout 150 "$PY" "$DIR/redeemdisc_logger.py" once >> "$DIR/redeemdisc.log" 2>&1 || true
  fi
  # Logger headlines digest -> iMessage, at most every ~12h (notable findings across
  # all loggers + hottest geo). Self-throttles to only-new since last send.
  hdstamp=$(cat "$DIR/.last_headlines_run" 2>/dev/null || echo 0)
  if [ $(( now2 - hdstamp )) -ge 43200 ]; then
    echo "$now2" > "$DIR/.last_headlines_run"
    run_timeout 60 "$PY" "$DIR/logger_headlines.py" >> "$DIR/headlines.log" 2>&1 || true
  fi

  # (2026-06-21) Removed the legacy mirror into "~/Documents/Obsidian Vault/Polymarket
  # Reports/" — that standalone vault was merged into the canonical PolymarketVault and
  # retired. Reports live only in ~/Documents/PolymarketVault/Reports/ now.
  # Consensus probe every ~2h (120 cycles): Manifold vs Polymarket price divergences
  # + Wikipedia attention spikes. Read-only; writes consensus_cache.json + Obsidian.
  if [ $(( oddpool_cycle % 120 )) -eq 0 ]; then
    run_timeout 180 "$PY" "$DIR/consensus_probe.py" once >> "$DIR/consensus_probe.log" 2>&1 || true
  fi

  # Sentiment probe every ~3h (180 cycles): Google News RSS headline count/sentiment
  # + Wikipedia attention spikes per market. Read-only; writes sentiment_cache.json + Obsidian.
  if [ $(( oddpool_cycle % 180 )) -eq 0 ]; then
    run_timeout 180 "$PY" "$DIR/sentiment_probe.py" once >> "$DIR/sentiment_probe.log" 2>&1 || true
  fi

  # Trending probe every ~2h (120 cycles): CoinGecko top-7 trending coins + HN frontpage
  # cross-referenced against live Polymarket markets. Attention precursor for crypto/tech.
  # Read-only; writes trending_cache.json + Obsidian. iMessage alert on trending coin hits.
  if [ $(( oddpool_cycle % 120 )) -eq 0 ]; then
    run_timeout 120 "$PY" "$DIR/trending_probe.py" >> "$DIR/trending_probe.log" 2>&1 || true
  fi

  # Order flow probe every ~1h (60 cycles): last-1h vs 24h-avg trade velocity + buy/sell
  # imbalance per market. Surfaces informed one-sided flow before it moves the price.
  # Read-only; writes orderflow_cache.json + Obsidian. iMessage alert on velocity >5x.
  if [ $(( oddpool_cycle % 60 )) -eq 0 ]; then
    run_timeout 240 "$PY" "$DIR/orderflow_probe.py" >> "$DIR/orderflow_probe.log" 2>&1 || true
  fi

  # Macro probe every ~1h (60 cycles): Fear & Greed index + DeFiLlama TVL trend.
  # Cross-references crypto sentiment with Polymarket crypto markets. Read-only;
  # writes macro_cache.json + Obsidian. Hourly cadence matches F&G update frequency.
  if [ $(( oddpool_cycle % 60 )) -eq 0 ]; then
    run_timeout 120 "$PY" "$DIR/macro_probe.py" >> "$DIR/macro_probe.log" 2>&1 || true
  fi

  # Tech probe every ~2h (120 cycles): GitHub trending repos + Guardian RSS feeds.
  # Cross-references tech/AI activity with Polymarket tech markets. Read-only;
  # writes tech_cache.json + Obsidian. Matches trending_probe cadence.
  if [ $(( oddpool_cycle % 120 )) -eq 0 ]; then
    run_timeout 120 "$PY" "$DIR/tech_probe.py" >> "$DIR/tech_probe.log" 2>&1 || true
  fi

  # Signal Hub every ~10min (10 cycles): cross-probe convergence mapper.
  # Reads all 4 probe caches, writes Signal Hub.md + Markets/*.md with [[wikilinks]],
  # then rsyncs to Obsidian vault. Obsidian graph view shows market nodes linking probes.
  if [ $(( oddpool_cycle % 10 )) -eq 0 ]; then
    run_timeout 60 "$PY" "$DIR/signal_hub.py" >> "$DIR/signal_hub.log" 2>&1 || true
  fi

  # Graphify --update every ~6h (360 cycles): incrementally re-indexes the polymarket/
  # corpus. Probes write *_findings.md after each run; graphify picks them up here,
  # keeping the knowledge graph fresh with latest signal findings.
  if [ $(( oddpool_cycle % 360 )) -eq 0 ]; then
    # graphify auto-detect finds no key it recognizes → the doc-semantic pass SILENTLY failed before
    # (2026-06-18). Wire the keyless NVIDIA 70B (.env LLM_BASE_URL/LLM_MODEL/NVIDIA_API_KEY) via the
    # OpenAI-compatible backend so docs (BRAIN.md + Reports) actually graph. Subshell = no env leak.
    ( source "$DIR/.env" 2>/dev/null
      export OPENAI_API_KEY="$NVIDIA_API_KEY" OPENAI_BASE_URL="$LLM_BASE_URL"
      run_timeout 300 /Users/aryanagarwal/.local/bin/graphify "$DIR" --update --no-viz \
        --backend=openai --model="$LLM_MODEL" >> "$DIR/graphify_update.log" 2>&1 ) || true
  fi
  # Graphify the OBSIDIAN VAULT too (~6h, offset 3h from the source run) so the curated
  # notes (structural_arb, the arb-track MOC, lessons) stay AUTO neural-linked into the
  # kb graph — no manual force needed. Backgrounded (big job) + fail-soft.
  if [ $(( oddpool_cycle % 360 )) -eq 180 ]; then
    ( source "$DIR/.env" 2>/dev/null
      export OPENAI_API_KEY="$NVIDIA_API_KEY" OPENAI_BASE_URL="$LLM_BASE_URL"
      run_timeout 400 /Users/aryanagarwal/.local/bin/graphify \
        "/Users/aryanagarwal/Documents/PolymarketVault" --update --no-viz \
        --backend=openai --model="$LLM_MODEL" >> "$DIR/graphify_vault.log" 2>&1 ) &
    disown 2>/dev/null || true
  fi

  # Insider co-trading study daily (~112 data-api calls): refresh insider_clusters.json
  # so sharpdrift's independence stays honest as the watchlist/behavior shifts.
  ctstamp=$(cat "$DIR/.last_cotrade" 2>/dev/null || echo 0)
  if [ $(( now2 - ctstamp )) -ge 86400 ]; then
    echo "$now2" > "$DIR/.last_cotrade"
    run_timeout 240 "$PY" "$DIR/insider_cotrade.py" >> "$DIR/insider_cotrade.log" 2>&1 || true
  fi
  # Insider anomaly scan WEEKLY (heavy ~600 data-api calls): unsupervised IsolationForest
  # surfaces informed wallets the threshold whale_screen misses -> insider_anomaly_candidates.json
  iastamp=$(cat "$DIR/.last_anomaly" 2>/dev/null || echo 0)
  if [ $(( now2 - iastamp )) -ge 604800 ]; then
    echo "$now2" > "$DIR/.last_anomaly"
    run_timeout 600 "$PY" "$DIR/insider_anomaly.py" --max 600 >> "$DIR/insider_anomaly.log" 2>&1 || true
  fi

  # HERMES keep-alive: the keyless news feed on :48580 (REAL port, not 48571) that
  # quietfade/newslag/tapeshock's news-quiet filter depend on. Runs unsupervised, so
  # if it stops serving (dead OR wedged: process alive but bind failed), heal it —
  # path-qualified kill (like the bot relaunch above) + relaunch. Heal-throttled to
  # 5min so a TIME_WAIT rebind retry doesn't churn. Was silently down before 2026-06-15.
  if ! curl -s -m 4 http://127.0.0.1:48580/api/health 2>/dev/null | grep -q 'ok'; then
    hzstamp=$(cat "$DIR/.last_hermes_heal" 2>/dev/null || echo 0)
    if [ $(( now2 - hzstamp )) -ge 300 ]; then
      echo "$now2" > "$DIR/.last_hermes_heal"
      pkill -f "$DIR/hermes_news.py" 2>/dev/null
      sleep 3
      nohup "$PY" "$DIR/hermes_news.py" >> "$DIR/hermes_news.log" 2>&1 < /dev/null &
      disown 2>/dev/null || true
      log "hermes not serving on :48580 - healed (relaunched)"
      alert_throttled hermes "hermes news feed was down on :48580 - relaunched."
    fi
  fi

  # PTY watchdog: alert when Terminal is close to the 512 hard limit
  pty_count=$(ls /dev/ttys* 2>/dev/null | wc -l | tr -d ' ')
  if [ "${pty_count:-0}" -ge 450 ]; then
    alert_throttled pty_high "Terminal PTY slots ${pty_count}/512 — restart Claude Code now to avoid lockout" 3600
  fi

  # Hermes news: keep alive (port 48580). Restart if not responding.
  if ! curl -sf http://127.0.0.1:48580/api/health >/dev/null 2>&1; then
    pkill -f "hermes_news.py" 2>/dev/null; sleep 2
    nohup "$PY" "$DIR/hermes_news.py" >> "$DIR/hermes_news.log" 2>&1 < /dev/null &
    disown 2>/dev/null || true
    log "hermes_news restarted"
  fi

  # Crypto one-touch tape: every ~15min mark every live dip/reach market
  # (model touch-prob vs market YES) into crypto_touch_daytrade.log — builds the
  # implied-vs-realized series, the only honest path to a vol-premium edge (15m
  # scalping is spread-dominated, proven). Discovery cache refreshed once/day.
  # Read-only PAPER; backgrounded so it never blocks the watchdog; never touches
  # the bot legs.
  cttape=$(cat "$DIR/.last_touch_tape" 2>/dev/null || echo 0)
  if [ $(( now2 - cttape )) -ge 900 ]; then
    echo "$now2" > "$DIR/.last_touch_tape"
    ctdisc=$(cat "$DIR/.last_touch_discover" 2>/dev/null || echo 0)
    if [ $(( now2 - ctdisc )) -ge 86400 ]; then
      echo "$now2" > "$DIR/.last_touch_discover"
      nohup "$PY" "$DIR/crypto_touch_daytrade.py" --discover >/dev/null 2>&1 < /dev/null &
      disown 2>/dev/null || true
    fi
    # mark the tape, then mirror the latest batch to Obsidian (chained so the vault
    # view is always fresh; backgrounded as one subshell so it never blocks the loop).
    ( "$PY" "$DIR/crypto_touch_daytrade.py" --once \
      && run_timeout 90 "$PY" "$DIR/obsidian_snapshot.py" --only touch ) >> "$DIR/obsidian_snapshot.log" 2>&1 &
    disown 2>/dev/null || true
  fi

  # Basketlock arb leg: every ~15min settle resolved baskets + capture executable
  # complete-set locks (priced <$1 at ask, real CLOB depth, ≤210d) + top up the
  # basketrand control. The one +EV thing in the system — structural arbitrage, no
  # prediction. Read-only PAPER, no orders; backgrounded so it never blocks the loop;
  # own log; never touches the bot legs. Verdict gated at 30 resolved + control loses.
  blstamp=$(cat "$DIR/.last_basketlock" 2>/dev/null || echo 0)
  if [ $(( now2 - blstamp )) -ge 900 ]; then
    echo "$now2" > "$DIR/.last_basketlock"
    # run the leg, then mirror its book/control/scoreboard to Obsidian (chained, one
    # backgrounded subshell so it never blocks the loop).
    ( "$PY" "$DIR/basketlock.py" >> "$DIR/basketlock.log" 2>&1 \
      && run_timeout 90 "$PY" "$DIR/obsidian_snapshot.py" --only basketlock >> "$DIR/obsidian_snapshot.log" 2>&1 ) &
    disown 2>/dev/null || true
    # combined structural-arb (basket+data+mono+multiarb) → ONE Obsidian page. Same 15min cadence;
    # backgrounded (the live basket CLOB pull is slow) so it never blocks the loop. Fail-soft.
    ( run_timeout 90 "$PY" "$DIR/obsidian_snapshot.py" --only structarb >> "$DIR/obsidian_snapshot.log" 2>&1 ) &
    disown 2>/dev/null || true
  fi

  # BRAIN live entry-gate → Obsidian ("show the brain working"). Mirrors the gate's stamped
  # verdicts + the shadow A/B tally into the vault so the brain-in-front-of-entries is visible.
  # Light/keyless refresh every 15min; the live-demo snapshot (real _gate_entry via NVIDIA-70B
  # on current open markets) refreshes every 6h to cap LLM calls. Read-only, own subshell, fail-soft.
  bgstamp=$(cat "$DIR/.last_brain_gate" 2>/dev/null || echo 0)
  if [ $(( now2 - bgstamp )) -ge 900 ]; then
    echo "$now2" > "$DIR/.last_brain_gate"
    bgdemo=$(cat "$DIR/.last_brain_gate_demo" 2>/dev/null || echo 0)
    if [ $(( now2 - bgdemo )) -ge 21600 ]; then
      echo "$now2" > "$DIR/.last_brain_gate_demo"
      ( run_timeout 60 "$PY" "$DIR/obsidian_brain_gate.py" --demo >> "$DIR/obsidian_snapshot.log" 2>&1 ) &
    else
      ( run_timeout 90 "$PY" "$DIR/obsidian_brain_gate.py" >> "$DIR/obsidian_snapshot.log" 2>&1 ) &
    fi
    disown 2>/dev/null || true
  fi

  # Health sentinel: deterministic launchd-fleet + service + marking-honesty sweep every ~30min.
  # The FREE 24/7 form of pm-launchd-warden + pm-service-sentinel (no LLM, no Claude credits);
  # iMessages ONLY on a NEW real problem (dedup'd in health_sentinel_state.json). Own subshell, fail-soft.
  hsstamp=$(cat "$DIR/.last_health_sentinel" 2>/dev/null || echo 0)
  if [ $(( now2 - hsstamp )) -ge 1800 ]; then
    echo "$now2" > "$DIR/.last_health_sentinel"
    ( "$PY" "$DIR/health_sentinel.py" >> "$DIR/health_sentinel.log" 2>&1 ) &
    disown 2>/dev/null || true
  fi

  # AUTONOMY self-supervision (verify→solve→heal) every ~30min — the FREE-LLM runner form of the
  # pm-autonomy-verifier/-solver/-healer subagents (a bash loop can't dispatch a Claude subagent).
  # VERIFY: did each autonomous WORKER actually do its job (output fresh within cadence, non-empty,
  # provenance sane) + is the keyless Ollama FLOOR up? SOLVE: match each flag to the known-failure
  # catalog. HEAL: auto-apply ONLY the hard allowlist (restart ollama if down / clear a solver-named
  # stale cache). heal-not-kill is absolute — NO pkill, NO plist load, NEVER relaunches the watchdog
  # from inside itself (copybot_watchdog.sh owns that; Lesson 17). Also closes the ollama-liveness gap.
  # Own subshell, fail-soft; iMessage only on a NEW real problem. Solve's LLM is enrichment only —
  # it can never unlock a SAFE-AUTO beyond the allowlist, so it works fully offline.
  pastamp=$(cat "$DIR/.last_pm_autonomy" 2>/dev/null || echo 0)
  if [ $(( now2 - pastamp )) -ge 1800 ]; then
    echo "$now2" > "$DIR/.last_pm_autonomy"
    ( run_timeout 120 "$PY" "$DIR/pm_autonomy.py" heal >> "$DIR/pm_autonomy.log" 2>&1 ) &
    disown 2>/dev/null || true
  fi

  # NIGHT ANALYST every ~6h — the FREE-LLM (NVIDIA 70B → Ollama failover) cross-fleet synthesis.
  # Reads the arb books + analyst book + gate distances + the autonomy ledger and writes ONE
  # dollars-first brief → Vault/Reports/night_brief.md (a DETERMINISTIC fallback brief, stamped as
  # such, if no LLM is reachable — the night shift never goes dark). Read-only; iMessage ONLY on a
  # material flag (control bug / a lock that didn't pay / gate cross / floor down). Own subshell, fail-soft.
  nightstamp=$(cat "$DIR/.last_night_analyst" 2>/dev/null || echo 0)
  if [ $(( now2 - nightstamp )) -ge 21600 ]; then
    echo "$now2" > "$DIR/.last_night_analyst"
    ( run_timeout 180 "$PY" "$DIR/pm_night_analyst.py" >> "$DIR/pm_night_analyst.log" 2>&1 ) &
    disown 2>/dev/null || true
  fi

  # GATE ETA every ~6h — time-to-DECIDABILITY (not distance). Pure math over the same honest
  # closed exits dsr_gate reads (no LLM, no new fill model): per accumulating leg, how many MORE
  # exits until a CI could even exclude 0 (power analysis) + the projected calendar date at the
  # current exit rate. n<5 = noise (no ETA, no alarm — the thin-positive trap). Read-only; writes
  # Vault/Reports/gate_eta.md; iMessage ONLY when a non-control leg is decidable within 7 days.
  etastamp=$(cat "$DIR/.last_gate_eta" 2>/dev/null || echo 0)
  if [ $(( now2 - etastamp )) -ge 21600 ]; then
    echo "$now2" > "$DIR/.last_gate_eta"
    ( run_timeout 90 "$PY" "$DIR/pm_gate_eta.py" >> "$DIR/pm_gate_eta.log" 2>&1 ) &
    disown 2>/dev/null || true
  fi

  # Cross-venue arb scanner (Polymarket↔Kalshi): hourly. LLM semantic matcher with a
  # persistent verdict cache so warm runs are ~free (each pair confirmed once ever; only
  # NEW candidate pairs cost an Opus call, capped XV_MAX_LLM/run). Catches the rare real
  # same-event divergence; big gaps are 'win vs run' artifacts (verified). Reads the key
  # from .env. Read-only PAPER; backgrounded; own log; chained → Obsidian xvenue panel.
  xvstamp=$(cat "$DIR/.last_xvenue" 2>/dev/null || echo 0)
  if [ $(( now2 - xvstamp )) -ge 3600 ]; then
    echo "$now2" > "$DIR/.last_xvenue"
    ( "$PY" "$DIR/xvenue_arb.py" --once >> "$DIR/xvenue_arb.log" 2>&1 \
      && run_timeout 90 "$PY" "$DIR/obsidian_snapshot.py" --only xvenue >> "$DIR/obsidian_snapshot.log" 2>&1 ) &
    disown 2>/dev/null || true
  fi

  # News-arb scan: AUTONOMOUS settled-but-mispriced catalyst check every 2h. Picks its own
  # candidates (liquid + news actually touches them + not near-certain), judges up to N on the
  # FREE LLM via the keyless hermes feed, logs to news_arb_log.jsonl + Reports/news_arb_latest.md,
  # and iMessages ONLY on a real edge (dedup'd in news_arb_scan_state.json). Efficient-market
  # prior is strong → 0 edges is the EXPECTED honest steady state, not a failure. Own subshell.
  nastamp=$(cat "$DIR/.last_news_arb" 2>/dev/null || echo 0)
  if [ $(( now2 - nastamp )) -ge 7200 ]; then
    echo "$now2" > "$DIR/.last_news_arb"
    ( run_timeout 180 "$PY" "$DIR/news_arb.py" --scan --n 6 >> "$DIR/news_arb.log" 2>&1 ) &
    disown 2>/dev/null || true
  fi

  # NewsNow → Obsidian: pull trending news (HN/GitHub/ProductHunt/etc.) into vault
  # Reports/News Feed.md every hour. Fail-soft: skips if newsnow isn't running.
  nnstamp=$(cat "$DIR/.last_newsnow" 2>/dev/null || echo 0)
  if [ $(( now2 - nnstamp )) -ge 3600 ]; then
    echo "$now2" > "$DIR/.last_newsnow"
    ( run_timeout 30 "$PY" "$DIR/newsnow_obsidian.py" >> "$DIR/newsnow_obsidian.log" 2>&1 ) &
    disown 2>/dev/null || true
  fi

  # Market scout: AI 3-stage pipeline (haiku prescreen → opus draft → haiku gate).
  # 6h throttle (~$0.25/run); auto-dedupes against book + candidate state.
  scoutstamp=$(cat "$DIR/.last_scout" 2>/dev/null || echo 0)
  if [ $(( now2 - scoutstamp )) -ge 21600 ]; then
    echo "$now2" > "$DIR/.last_scout"
    ( "$PY" "$DIR/market_scout.py" --once >> "$DIR/market_scout.log" 2>&1 \
      && run_timeout 90 "$PY" "$DIR/obsidian_snapshot.py" --only scout >> "$DIR/obsidian_snapshot.log" 2>&1 ) &
    disown 2>/dev/null || true
  fi

  # Position monitor: watches open analyst positions for price drift + breaking news.
  # 30min throttle; haiku action recommendations; iMessage on INVESTIGATE/EXIT.
  monstamp=$(cat "$DIR/.last_monitor" 2>/dev/null || echo 0)
  if [ $(( now2 - monstamp )) -ge 1800 ]; then
    echo "$now2" > "$DIR/.last_monitor"
    ( "$PY" "$DIR/position_monitor.py" --once >> "$DIR/position_monitor.log" 2>&1 \
      && run_timeout 90 "$PY" "$DIR/obsidian_snapshot.py" --only monitor >> "$DIR/obsidian_snapshot.log" 2>&1 ) &
    disown 2>/dev/null || true
  fi

  # Hypothesis lab: weekly opus synthesis of patterns → fresh edge seeds for scout.
  # 7d throttle (~10¢/run); seeds feed into next scout cycle.
  hypstamp=$(cat "$DIR/.last_hypothesis" 2>/dev/null || echo 0)
  if [ $(( now2 - hypstamp )) -ge 604800 ]; then
    echo "$now2" > "$DIR/.last_hypothesis"
    ( "$PY" "$DIR/hypothesis_lab.py" >> "$DIR/hypothesis_lab.log" 2>&1 \
      && run_timeout 90 "$PY" "$DIR/obsidian_snapshot.py" --only hypothesis >> "$DIR/obsidian_snapshot.log" 2>&1 ) &
    disown 2>/dev/null || true
  fi

  # Deep research agent: tool-use agentic deep-diver on gate-passing scout candidates.
  # 12h throttle; cap 3 candidates/run (~$0.30 in opus calls); chains obsidian snapshot.
  drstamp=$(cat "$DIR/.last_deepresearch" 2>/dev/null || echo 0)
  if [ $(( now2 - drstamp )) -ge 43200 ]; then
    echo "$now2" > "$DIR/.last_deepresearch"
    ( run_timeout 300 "$PY" "$DIR/deep_research_agent.py" --once >> "$DIR/deep_research_agent.log" 2>&1 \
      && run_timeout 90 "$PY" "$DIR/obsidian_snapshot.py" --only deepresearch >> "$DIR/obsidian_snapshot.log" 2>&1 ) &
    disown 2>/dev/null || true
  fi

  # Position defender agent: adversarially stress-tests INVESTIGATE positions from monitor.
  # 6h throttle (monitor fires every 30min; defender is deeper/more expensive); chains obsidian.
  defstamp=$(cat "$DIR/.last_defender" 2>/dev/null || echo 0)
  if [ $(( now2 - defstamp )) -ge 21600 ]; then
    echo "$now2" > "$DIR/.last_defender"
    ( run_timeout 300 "$PY" "$DIR/position_defender_agent.py" --once >> "$DIR/position_defender_agent.log" 2>&1 \
      && run_timeout 90 "$PY" "$DIR/obsidian_snapshot.py" --only defender >> "$DIR/obsidian_snapshot.log" 2>&1 ) &
    disown 2>/dev/null || true
  fi

  # Edge hunter agent: hypothesis-seed-driven opportunity finder.
  # Weekly throttle (same as hypothesis_lab); hunts up to 3 seeds/run; chains obsidian.
  huntstamp=$(cat "$DIR/.last_hunter" 2>/dev/null || echo 0)
  if [ $(( now2 - huntstamp )) -ge 604800 ]; then
    echo "$now2" > "$DIR/.last_hunter"
    ( run_timeout 300 "$PY" "$DIR/edge_hunter_agent.py" --once >> "$DIR/edge_hunter_agent.log" 2>&1 \
      && run_timeout 90 "$PY" "$DIR/obsidian_snapshot.py" --only hunter >> "$DIR/obsidian_snapshot.log" 2>&1 ) &
    disown 2>/dev/null || true
  fi

  # Internal consistency agent: logical price constraint violations across related markets.
  # 6h throttle; iMessage on STRONG + executable; pure arb, no directional bet.
  conststamp=$(cat "$DIR/.last_consistency" 2>/dev/null || echo 0)
  if [ $(( now2 - conststamp )) -ge 21600 ]; then
    echo "$now2" > "$DIR/.last_consistency"
    ( run_timeout 300 "$PY" "$DIR/internal_consistency_agent.py" --once >> "$DIR/internal_consistency_agent.log" 2>&1 \
      && run_timeout 90 "$PY" "$DIR/obsidian_snapshot.py" --only consistency >> "$DIR/obsidian_snapshot.log" 2>&1 ) &
    disown 2>/dev/null || true
  fi

  # Attention decay agent: dying news + stale elevated price = fade (NO). 5h throttle.
  decaystamp=$(cat "$DIR/.last_decay" 2>/dev/null || echo 0)
  if [ $(( now2 - decaystamp )) -ge 18000 ]; then
    echo "$now2" > "$DIR/.last_decay"
    ( run_timeout 300 "$PY" "$DIR/attention_decay_agent.py" --once >> "$DIR/attention_decay_agent.log" 2>&1 \
      && run_timeout 90 "$PY" "$DIR/obsidian_snapshot.py" --only decay >> "$DIR/obsidian_snapshot.log" 2>&1 ) &
    disown 2>/dev/null || true
  fi

  # Anchor hunter: behavioral round-number anchors + news pressure = snap entry. 4h throttle.
  anchorstamp=$(cat "$DIR/.last_anchor" 2>/dev/null || echo 0)
  if [ $(( now2 - anchorstamp )) -ge 14400 ]; then
    echo "$now2" > "$DIR/.last_anchor"
    ( run_timeout 300 "$PY" "$DIR/anchor_hunter_agent.py" --once >> "$DIR/anchor_hunter_agent.log" 2>&1 \
      && run_timeout 90 "$PY" "$DIR/obsidian_snapshot.py" --only anchor >> "$DIR/obsidian_snapshot.log" 2>&1 ) &
    disown 2>/dev/null || true
  fi

  # Liquidity scanner: CLOB sweep for spread/imbalance informed-positioning signals.
  # 4h throttle; iMessage on STRONG signals; backgrounded.
  liqstamp=$(cat "$DIR/.last_liquidity" 2>/dev/null || echo 0)
  if [ $(( now2 - liqstamp )) -ge 14400 ]; then
    echo "$now2" > "$DIR/.last_liquidity"
    ( run_timeout 300 "$PY" "$DIR/liquidity_scanner_agent.py" --once >> "$DIR/liquidity_scanner_agent.log" 2>&1 \
      && run_timeout 90 "$PY" "$DIR/obsidian_snapshot.py" --only liquidity >> "$DIR/obsidian_snapshot.log" 2>&1 ) &
    disown 2>/dev/null || true
  fi

  # News velocity agent: hermes acceleration vs market price lag, 2h throttle.
  velstamp=$(cat "$DIR/.last_velocity" 2>/dev/null || echo 0)
  if [ $(( now2 - velstamp )) -ge 7200 ]; then
    echo "$now2" > "$DIR/.last_velocity"
    ( run_timeout 300 "$PY" "$DIR/news_velocity_agent.py" --once >> "$DIR/news_velocity_agent.log" 2>&1 \
      && run_timeout 90 "$PY" "$DIR/obsidian_snapshot.py" --only velocity >> "$DIR/obsidian_snapshot.log" 2>&1 ) &
    disown 2>/dev/null || true
  fi

  # Confluence agent: news-accel + CLOB-imbalance double-signal, 3h throttle.
  # Always iMessages when it finds anything (highest quality signal).
  confstamp=$(cat "$DIR/.last_confluence" 2>/dev/null || echo 0)
  if [ $(( now2 - confstamp )) -ge 10800 ]; then
    echo "$now2" > "$DIR/.last_confluence"
    ( run_timeout 300 "$PY" "$DIR/confluence_agent.py" --once >> "$DIR/confluence_agent.log" 2>&1 \
      && run_timeout 90 "$PY" "$DIR/obsidian_snapshot.py" --only confluence >> "$DIR/obsidian_snapshot.log" 2>&1 ) &
    disown 2>/dev/null || true
  fi

  # Breaking news agent: hourly scan of hermes feed → finds mispriced markets.
  # 1h throttle; iMessage on CRITICAL/HIGH signals; chains obsidian.
  newsstamp=$(cat "$DIR/.last_news_agent" 2>/dev/null || echo 0)
  if [ $(( now2 - newsstamp )) -ge 3600 ]; then
    echo "$now2" > "$DIR/.last_news_agent"
    ( run_timeout 300 "$PY" "$DIR/breaking_news_agent.py" --once >> "$DIR/breaking_news_agent.log" 2>&1 \
      && run_timeout 90 "$PY" "$DIR/obsidian_snapshot.py" --only news >> "$DIR/obsidian_snapshot.log" 2>&1 ) &
    disown 2>/dev/null || true
  fi

  # Thesis critic agent: 3-lens pre-entry attack on unreviewed candidates.
  # 8h throttle; attacks scout gate-passes + edge-hunter dossiers before book entry.
  criticstamp=$(cat "$DIR/.last_critic" 2>/dev/null || echo 0)
  if [ $(( now2 - criticstamp )) -ge 28800 ]; then
    echo "$now2" > "$DIR/.last_critic"
    ( run_timeout 300 "$PY" "$DIR/thesis_critic_agent.py" --once >> "$DIR/thesis_critic_agent.log" 2>&1 \
      && run_timeout 90 "$PY" "$DIR/obsidian_snapshot.py" --only critic >> "$DIR/obsidian_snapshot.log" 2>&1 ) &
    disown 2>/dev/null || true
  fi

  # Opportunity ranker: daily cross-agent synthesis → TOP-5 priority list.
  # 24h throttle; synthesizes all 6 agents into one ranked action list.
  rankstamp=$(cat "$DIR/.last_ranker" 2>/dev/null || echo 0)
  if [ $(( now2 - rankstamp )) -ge 86400 ]; then
    echo "$now2" > "$DIR/.last_ranker"
    ( run_timeout 300 "$PY" "$DIR/opportunity_ranker_agent.py" --once >> "$DIR/opportunity_ranker_agent.log" 2>&1 \
      && run_timeout 90 "$PY" "$DIR/obsidian_snapshot.py" --only ranker >> "$DIR/obsidian_snapshot.log" 2>&1 ) &
    disown 2>/dev/null || true
  fi

  # Agent health monitor: iMessage if any agent's output JSON hasn't been written
  # within 2x its cadence. Checks output file mtime (written by the agent on
  # completion) rather than the throttle stamp (written before the agent runs),
  # so a crashed or timed-out agent is detected even though its stamp was reset.
  for _ah in \
    "news_agent:breaking_news_signals.json:3600" \
    "velocity:news_velocity_signals.json:7200" \
    "confluence:confluence_signals.json:10800" \
    "anchor:anchor_signals.json:14400" \
    "liquidity:liquidity_signals.json:14400" \
    "decay:attention_decay_signals.json:18000" \
    "consistency:consistency_violations.json:21600" \
    "defender:defender_verdicts.json:21600" \
    "critic:thesis_critic_verdicts.json:28800" \
    "deepresearch:deep_research_reports.json:43200" \
    "ranker:opportunity_rankings.json:86400" \
    "hunter:edge_hunter_dossiers.json:604800"; do
    _ah_name="${_ah%%:*}"; _ah_rest="${_ah#*:}"; _ah_file="${_ah_rest%%:*}"; _ah_cad="${_ah_rest##*:}"
    _ah_last=$(stat -f %m "$DIR/$_ah_file" 2>/dev/null || echo 0)
    _ah_age=$(( now2 - _ah_last ))
    if [ "$_ah_age" -gt $(( _ah_cad * 2 )) ]; then
      alert_throttled "ah_${_ah_name}" \
        "agent ${_ah_name} output stale ${_ah_age}s (expected <${_ah_cad}s)" "$_ah_cad"
    fi
  done

  # Scalp Lab: paper-only A/B (dip vs scalp vs fade). Runs every cycle,
  # read-only against Gamma, never touches the main bot. Fail-soft.
  # Timeout-wrapped so a hung 'once' (e.g. network stall) can't freeze the loop.
  # Capture THIS cycle's output so the zero-fetch detector reflects this run only
  # (not a stale 'fetched N' line from earlier in the append-only log).
  lab_out=$(run_timeout "$LAB_RUN_TIMEOUT" "$PY" "$DIR/scalp_lab.py" once 2>&1); lab_rc=$?
  printf '%s\n' "$lab_out" >> "$DIR/scalp_lab.log"
  [ "$lab_rc" -ne 0 ] && log "scalp_lab once failed/timed out rc=$lab_rc (>${LAB_RUN_TIMEOUT}s)"

  # HEALTH: is the lab still REFRESHING? (state stops updating => stuck/feed)
  if ! fresh "$DIR/scalp_lab_state.json" "$LAB_STALE"; then
    log "scalp_lab NOT refreshing (state stale >${LAB_STALE}s) - feed/process issue"
    alert_throttled lab "Scalp lab not refreshing."
  fi

  # HEALTH: is it OPENING TRADES? Proxy = feed returns markets THIS cycle. A run
  # that fetched 0 OR failed/timed-out (no 'fetched' line) counts toward the streak;
  # ZERO_FETCH_ALERT cycles => cannot trade (almost always the flaky India egress,
  # which the watchdog can't fix — but it surfaces it instead of failing silent).
  this_fetch=$(printf '%s' "$lab_out" | grep -aoE 'fetched [0-9]+' | tail -1 | grep -oE '[0-9]+')
  if [ -z "$this_fetch" ] || [ "$this_fetch" -eq 0 ]; then
    zf=$(( $(cat "$DIR/.zero_fetch" 2>/dev/null || echo 0) + 1 )); echo "$zf" > "$DIR/.zero_fetch"
    if [ "$zf" -ge "$ZERO_FETCH_ALERT" ]; then
      log "FEED DOWN: ${zf} cycles, no markets fetched - bot cannot open trades"
      alert_throttled feed "Feed down ${zf}x - no trades."
    fi
  else
    echo 0 > "$DIR/.zero_fetch"
  fi

  # HEALTH: is the lab-pdf launchd job (com.aryan.polymarket-lab-pdf) still
  # writing the iCloud PDF? It crashed silently every 10min for ~1.5 days
  # (2026-06-12: 196 OSError tracebacks, nobody noticed). Job runs every 10min,
  # so mtime >30min old = at least 2 missed runs => dead/crashing. Throttled
  # iMessage alert (Deploy Protocol #3: silence is not success).
  LAB_PDF="$HOME/Library/Mobile Documents/com~apple~CloudDocs/PolymarketBot-Lab/Lab Report.pdf"
  if ! fresh "$LAB_PDF" 1800; then
    pdf_age_min=$(( ( $(date +%s) - $(mtime_of "$LAB_PDF") ) / 60 ))
    log "lab-pdf STALE: Lab Report.pdf mtime ${pdf_age_min}min old (>30min) - launchd job dead/crashing (see lab_pdf.log)"
    alert_throttled labpdf "Lab PDF stale ${pdf_age_min}min - com.aryan.polymarket-lab-pdf dead/crashing."
  fi

  # HEALTH: vol_regime covariate (com.aryan.polymarket-volregime, daily 06:15).
  # CSV's last row must be from today or yesterday UTC; older = job dead or
  # CoinGecko down, and a silent gap would corrupt the covariate series.
  VOLCSV="$DIR/vol_regime.csv"
  vol_last=$(tail -1 "$VOLCSV" 2>/dev/null | cut -d, -f1)
  if [ "$vol_last" != "$(date -u +%F)" ] && [ "$vol_last" != "$(date -u -v-1d +%F 2>/dev/null || date -u -d yesterday +%F)" ]; then
    log "vol_regime STALE: last row '$vol_last' (>1 day old) - com.aryan.polymarket-volregime dead or CoinGecko down"
    alert_throttled volregime "vol_regime.csv stale (last $vol_last) - daily volregime job dead." 21600
  fi

# Edge Trader (house strategy): ML divergence, live paper.
# RE-ENABLED 2026-08-04: model passes out-of-time test (+$0.1475/trade, 62% win vs allin -$0.1095).
# Running in TEST PHASE with conservative config (max_open=5, edge_min=0.15, bet_usdc=0.5).
# Kill switch: touch ~/.edge_trader_KILL to stop. Auto-disables if P&L < -$5.
  et_pid=$(cat "$DIR/edge_trader.pid" 2>/dev/null)
  if [ -f ~/.edge_trader_KILL ]; then
    log "edge_trader KILL switch active - not launching"
    pkill -f "$DIR/edge_trader.py" 2>/dev/null
  elif [ -z "$et_pid" ] || ! ps -p "$et_pid" >/dev/null 2>&1; then
    nohup "$PY" "$DIR/edge_trader.py" run >> "$DIR/edge_trader.log" 2>&1 < /dev/null &
    echo $! > "$DIR/edge_trader.pid"
    disown 2>/dev/null || true
    log "edge_trader (re)launched (pid $(cat "$DIR/edge_trader.pid"))"
  fi

# Resolution Watcher: source latency edge (structural, non-predictive).
# Monitors official data feeds (FRED, CoinGecko, Binance) and acts when
# Polymarket prices haven't caught up to new public facts.
# Kill switch: touch ~/.resolution_watcher_KILL to stop.
  rw_pid=$(cat "$DIR/resolution_watcher.pid" 2>/dev/null)
  if [ -f ~/.resolution_watcher_KILL ]; then
    log "resolution_watcher KILL switch active - not launching"
    pkill -f "$DIR/resolution_watcher.py" 2>/dev/null
  elif [ -z "$rw_pid" ] || ! ps -p "$rw_pid" >/dev/null 2>&1; then
    nohup "$PY" "$DIR/resolution_watcher.py" run >> "$DIR/resolution_watcher.log" 2>&1 < /dev/null &
    echo $! > "$DIR/resolution_watcher.pid"
    disown 2>/dev/null || true
    log "resolution_watcher (re)launched (pid $(cat "$DIR/resolution_watcher.pid"))"
  fi

  # Scalp Engine: real paper scalping on a fast ~10s loop, runs as its own
  # detached daemon. Keep it alive here. Paper-only, read-only. Fail-soft.
  se_pid=$(cat "$DIR/scalp_engine.pid" 2>/dev/null)
  se_reason=""
  if ! proc_matches "$se_pid" "scalp_engine.py"; then
    se_reason="process not running"
  else
    # HUNG = pid alive but state frozen. Only after a launch-time GRACE, so a just-
    # relaunched engine (which needs warmup + a flaky first fetch) isn't re-killed
    # in a 60s loop. Requires BOTH: past grace since launch AND state stale.
    se_launch=$(cat "$DIR/.scalp_engine_launch" 2>/dev/null || echo 0)
    if [ $(( $(date +%s) - se_launch )) -gt "$ENGINE_STALE" ] \
       && [ -f "$DIR/scalp_engine_state.json" ] \
       && ! fresh "$DIR/scalp_engine_state.json" "$ENGINE_STALE"; then
      se_reason="HUNG (state silent >${ENGINE_STALE}s)"
      pkill -f "$DIR/scalp_engine.py" 2>/dev/null; sleep 2
      alert_throttled engine "Scalp engine frozen - restarting."
    fi
  fi
  if [ -n "$se_reason" ]; then
    # REAP-BEFORE-LAUNCH (2026-07-01): the "process not running" branch used to
    # relaunch WITHOUT killing stragglers, and $! was landing EMPTY in
    # scalp_engine.pid — so proc_matches failed every cycle and a NEW engine was
    # spawned ~every 2.5min (7 leaked daemons found, all racing Postgres/CLOB).
    # Path-qualified kill (only THIS dir's engine, never scalp_lab/the bot) makes
    # the relaunch idempotent, mirroring startup (:97) and the HUNG branch (:926).
    pkill -f "$DIR/scalp_engine.py" 2>/dev/null; sleep 1
    nohup "$PY" "$DIR/scalp_engine.py" run >> "$DIR/scalp_engine.log" 2>&1 < /dev/null &
    se_new=$!
    disown 2>/dev/null || true
    # $! has been observed empty here; take the authoritative pid from the live
    # process table so the pidfile actually tracks the engine and we STOP relaunching.
    sleep 1; se_live=$(pgrep -f "$DIR/scalp_engine.py" | tail -1)
    [ -n "$se_live" ] && se_new="$se_live"
    echo "$se_new" > "$DIR/scalp_engine.pid"
    date +%s > "$DIR/.scalp_engine_launch"          # start the grace clock
    touch "$DIR/scalp_engine_state.json" 2>/dev/null # reset staleness so we don't re-kill
    log "scalp_engine (re)launched [$se_reason] (pid $se_new)"
  fi

  # Analytics snapshot from data-api every ~30 min (throttled, best-effort)
  now2=$(date +%s)
  last=$(cat "$DIR/.last_snapshot" 2>/dev/null || echo 0)
  if [ $(( now2 - last )) -ge 1800 ]; then
    run_timeout 60 "$PY" "$DIR/polymarket_data.py" snapshot >/dev/null 2>&1 || true
    echo "$now2" > "$DIR/.last_snapshot"
    log "analytics snapshot taken"
  fi

  # Phone-view report EMAIL — actually SENDS (not just drafts) via Gmail SMTP with
  # the fresh bot-report PDF attached, so Aryan can glance at the bot on Android
  # (no LAN / IP needed). No-op until a real Gmail App Password is in
  # .smtp_creds.json (Claude can't type credentials). MAIL_EVERY sec (default 2h —
  # tune it); backgrounded so it never blocks the loop. Logs to send_mail.log.
  MAIL_EVERY=${MAIL_EVERY:-7200}
  mailstamp=$(cat "$DIR/.last_mail" 2>/dev/null || echo 0)
  if [ $(( now2 - mailstamp )) -ge "$MAIL_EVERY" ]; then
    echo "$now2" > "$DIR/.last_mail"
    ( "$PY" "$DIR/bot_report.py" >/dev/null 2>&1
      "$PY" "$DIR/send_mail.py" "$DIR/polymarket_bot_report.pdf" \
            "Polymarket bot — phone view $(date '+%m-%d %H:%M')" >> "$DIR/send_mail.log" 2>&1 ) &
    log "phone-view report email attempted (every ${MAIL_EVERY}s; sends only if SMTP creds set)"
  fi

  # Fade forward-confirmation CHECKPOINT: evaluate the gate daily; desktop-alert
  # ONLY on the rising edge (the cycle it first PASSES), so it never spams. The
  # bot never sizes real money — this just tells the human when it's worth a look.
  fcstamp=$(cat "$DIR/.last_fadecheck" 2>/dev/null || echo 0)
  if [ $(( now2 - fcstamp )) -ge 86400 ]; then
    echo "$now2" > "$DIR/.last_fadecheck"
    prev_gate=$(cat "$DIR/.fade_gate" 2>/dev/null || echo NOTYET)
    run_timeout 60 "$PY" "$DIR/fade_checkpoint.py" >> "$DIR/fade_checkpoint.log" 2>&1
    new_gate=$(cat "$DIR/.fade_gate" 2>/dev/null || echo NOTYET)
    if [ "$prev_gate" != "PASS" ] && [ "$new_gate" = "PASS" ]; then
      log "FADE GATE CLEARED — forward-confirmed (see fade_checkpoint.log)"
      alert_throttled fadegate "Fade gate CLEARED - forward-confirmed. Review before any real money." 0
    fi
  fi

  # Daily ML retrain: grow the dataset (+200 markets) and retrain. Backgrounded
  # so it never blocks the watchdog; throttled to once / 24h.
  mlstamp=$(cat "$DIR/.last_ml_run" 2>/dev/null || echo 0)
  if [ $(( now2 - mlstamp )) -ge 86400 ]; then
    echo "$now2" > "$DIR/.last_ml_run"
    ( "$PY" "$DIR/ml/collect_history.py" --max-markets 250 --category crypto >> "$DIR/ml/pipeline.log" 2>&1
      "$PY" "$DIR/ml/train.py" >> "$DIR/ml/pipeline.log" 2>&1 ) &
    log "daily ML retrain kicked off (background)"
  fi

  # Weekly OUT-OF-TIME test: retrain on pre-cutoff markets, test on the markets
  # that resolved later (which the daily collector keeps adding). This is the
  # real confirm/kill for the divergence edge — Phase 4 (real money) is gated on
  # it. Throttled to once / 7d, backgrounded, paper-only. Writes the verdict to
  # ml/pipeline.log and ml/backtest_oot_result.json.
  ootstamp=$(cat "$DIR/.last_oot" 2>/dev/null || echo 0)
  if [ $(( now2 - ootstamp )) -ge 604800 ]; then
    echo "$now2" > "$DIR/.last_oot"
    ( "$PY" "$DIR/ml/backtest_oot.py" >> "$DIR/ml/pipeline.log" 2>&1
      "$PY" "$DIR/ml/analyst_panel.py" >> "$DIR/ml/pipeline.log" 2>&1 ) &
    log "weekly out-of-time backtest + analyst panel kicked off (background)"
  fi

  # Log rotation (2026-06-12): append-only logs hit 51MB and slowed every grep.
  # copytruncate keeps open handles valid; archives gzipped, tails preserved.
  for lf in "$DIR/scalp_lab.log" "$DIR/run_output.txt" "$DIR/polymarket_bot.log" "$DIR/scalp_engine.log"; do
    if [ -f "$lf" ] && [ "$(stat -f %z "$lf" 2>/dev/null || echo 0)" -gt 10485760 ]; then
      mkdir -p "$DIR/log_archive"
      tail -2000 "$lf" > "$lf.keep"
      gzip -c "$lf" > "$DIR/log_archive/$(basename "$lf")-$(date +%Y%m%d-%H%M).gz"
      : > "$lf"; cat "$lf.keep" >> "$lf"; rm -f "$lf.keep"
      log "rotated $(basename "$lf") (>10MB)"
    fi
  done
  # keep only the 10 newest archives
  ls -t "$DIR/log_archive" 2>/dev/null | tail -n +11 | while read -r old; do rm -f "$DIR/log_archive/$old"; done

  # Live activity light for the vault — writes Activity.md with a pulsing green
  # dot per task performing right now (cheap: ps + launchctl + atomic write).
  "$PY" "$DIR/activity_light.py" >/dev/null 2>&1 || true
  # Connector health light — reachability of every data_sources.py connector;
  # self-throttled to ~15min, writes Connectors/ nodes (green=online / red=down).
  "$PY" "$DIR/connector_light.py" >/dev/null 2>&1 || true
  # Wiring keeper — self-heals the live-map: prune orphan nodes, re-assert graph color
  # groups Obsidian drops, proxy-reroute persistently-blocked connectors. ~30min throttle.
  "$PY" "$DIR/wiring_keeper.py" >/dev/null 2>&1 || true

  # Self-reload: if this script's mtime changed since last cycle, exec a fresh
  # copy so code changes go live within one 60s cycle without a manual restart.
  # The .watchdog_mtime sentinel prevents an infinite exec loop on first run.
  _wd_mt=$(stat -f %m "$0" 2>/dev/null || stat -c %Y "$0" 2>/dev/null || echo 0)
  _wd_mt_saved=$(cat "$DIR/.watchdog_mtime" 2>/dev/null || echo 0)
  if [ "$_wd_mt" != "$_wd_mt_saved" ]; then
    echo "$_wd_mt" > "$DIR/.watchdog_mtime"
    if [ "$_wd_mt_saved" != "0" ]; then
      log "watchdog script updated (mtime changed) — hot-reloading"
      exec "$0"
    fi
  fi

  sleep "$CHECK"
done
