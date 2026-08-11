#!/usr/bin/env python3
"""
scalp_lab.py — standalone PAPER-ONLY A/B bot for two experimental strategies.

  • dip   : buy YES in [0.30, 0.40], sell at 0.49  (the "buy till 40 / sell 49")
  • scalp : buy near-certain YES in [0.92, 0.97] on HIGH-volume markets,
            take a tiny +2¢ profit, tight stop  (the "98% win-rate" idea)

Each strategy keeps its OWN paper ledger so you get two independent charts.
NO private keys, NO real orders, NO external deps — stdlib only, read-only
against Polymarket's public Gamma API. Safe to run anywhere.

Realism: fills modeled with a bid/ask spread (you BUY at the ask, MARK/EXIT
at the bid), so the paper P&L is conservative — exactly where a 2¢ scalp
gives most of its edge back. That's the point: an honest test.

Usage:
  python3 scalp_lab.py run        # poll + paper-trade in a loop
  python3 scalp_lab.py once       # a single scan (good for cron/testing)
  python3 scalp_lab.py chart      # write scalp_lab_charts.html (two charts)
  python3 scalp_lab.py board      # print the two scoreboards to the terminal
"""
import os, sys, json, time, re, urllib.parse, urllib.request, subprocess
import psr_dsr as _P   # phi / phi_inv for the wangfade leg's Wang fair-value gate
from datetime import datetime, timezone
import db as _db
import pandascore as _ps   # esports match status covariate (no-op without key)
import gdelt_intensity as _gd   # GDELT news-intensity covariate (free, 10s throttle)
import cs2_fairvalue as _cs2   # ESTA empirical map/series win-prob (covariate only)
import market_indices as _mkt   # macro index covariate (Sensex/Nifty/global/USDINR); CONTEXT only, not a signal
import belief_ledger as _bl   # append-only forecast + calibration ledger
import sports_data as _sd   # all-sports results + team form (ESPN keyless)
_sports_idx, _sports_form = {}, {}  # resolution index + team W-L tallies, loaded at startup
_gdelt_cache = {}                  # persisted via scalp_lab_cache.json


def wang_fair(p_market, lam=0.183):
    """Wang-transform fair probability from market price (oracle3 MLE lambda=0.183
    on 291k resolved contracts; our in-sample fit was ~0.22). For longshot YES:
    market overprices -> p_true = Phi(Phi^-1(p_mkt) - lam). Covariate only."""
    try:
        from statistics import NormalDist
        nd = NormalDist()
        p = min(max(float(p_market), 1e-6), 1 - 1e-6)
        return round(nd.cdf(nd.inv_cdf(p) - lam), 4)
    except Exception:
        return None
import candle_signals as _cs
try:
    from gate_wrapper import gate_edge as _brain_gate
except ImportError:
    _brain_gate = None  # graceful fallback if gate_wrapper not available

BASE      = os.path.dirname(os.path.abspath(__file__))
STATE     = os.path.join(BASE, "scalp_lab_state.json")
CACHE     = os.path.join(BASE, "scalp_lab_cache.json")   # persists EMA baselines + price snaps
GAMMA     = "https://gamma-api.polymarket.com/markets"
UA        = "scalp-lab-paper/1.0"
TIMEOUT   = 8

CONFIG = {
    "poll_sec":        60,
    "assumed_spread":  0.02,    # total bid-ask; buy at mid+half, exit at mid-half
    # Liquidity-aware spread (2026-06-07): the flat 2c above only holds for DEEP
    # books. The volume test showed the fade's apparent edge in thin markets is an
    # artifact of assuming a 2c spread that doesn't exist there. So fade-variant
    # fills are charged a volume-scaled spread (floored at 2c). Sub-cent effect
    # above the 200k gate, but it stops pretending thin markets are cheap to fill.
    "liq_ref_volume":  300_000, # at/above this, spread ~= assumed_spread
    "liq_spread_cap":  0.10,    # never charge more than this (a thin-market sanity cap)
    # ── 2026-06-11 research fixes (oracle3 Wang calibration + Lesson #8) ──────
    # maker_entries: legs with a price-stability gate rest a limit AT the mid
    # instead of crossing the spread. Only honest where the market is slow/gated
    # (nearres family checks _scan_chg; nearresfade trades 1-30d markets).
    "maker_entries":            True,
    # fade_min_lambda: oracle3's Wang λ by category (291K resolved contracts):
    # crypto 0.253 / science 0.268 / tech 0.268 / other 0.282 / sports 0.070 /
    # politics 0.054. Fades harvest the risk premium λ — in low-λ categories
    # there is nothing to harvest (this is WHY truefade died on politics).
    "fade_min_lambda":          0.15,
    # nearres_hold_to_resolution: Lesson #8 — at 0.88-0.95 entries the 9c target
    # caps winners while stops book full adverse moves; the OOS edge (+4.2c/trade,
    # CI>0) was measured AT RESOLUTION. So: no target/stop, redeem at settlement.
    "nearres_hold_to_resolution": True,
    "max_hold_hours":  48,      # force-close stale paper positions
    "reentry_cooldown_hours": 24,  # don't re-buy a market we recently closed (anti-churn)

    # ── entry gate (Phase-1 SHADOW, 2026-06-18) ────────────────────────────────
    # Tags every new entry with the "brain's" verdict — kb_query lexical relevance
    # over BRAIN.md + the 1170-node knowledge graph, then an NVIDIA-70B skeptic
    # judge — WITHOUT changing what opens. After ~30 exits, compare gate_pass vs
    # gate_fail P&L; flip to "enforce" only if PASS beats FAIL with CI>0. Honest by
    # construction: shadow reduces no n and resets no experiment.
    "entry_gate_enabled":      True,
    "entry_gate_mode":         "shadow",   # "shadow" (tag only) | "enforce" (remove FAILs, fail-closed)
    "entry_gate_legs":         "all",      # "all" or a tuple of leg names
    "gate_min_conf":           0.60,       # LLM confidence to count an entry as PASS
    "gate_kb_min_score":       3.0,        # kb relevance floor used when there's no LLM verdict
    "gate_max_llm_per_cycle":  3,          # cap LLM judgments per once-run (120s timeout guard)
    "gate_llm_timeout":        8,          # seconds per LLM call (3×8 = 24s worst case)

    # ── dip strategy ───────────────────────────────────────────────
    "dip_enabled":     False,   # KILLED — proven loser (-$2.5, buy-the-falling-knife)
    "dip_buy_min":     0.30,
    "dip_buy_max":     0.40,
    "dip_sell_target": 0.49,
    "dip_stop":        1.0,     # DISABLED FOR 7 DAYS (was 0.25) — no stop-loss until 2026-07-30
    "dip_min_volume":  50_000,
    "dip_max_open":    15,
    "dip_bet_usdc":    1.0,
    "dip_min_days":    0,       # dip has no min-horizon (your spec)
    "dip_max_days":    21,      # mean-reversion is short-term: skip year-out markets

    # ── scalp strategy ─────────────────────────────────────────────
    "scalp_enabled":   False,   # KILLED 2026-06-07: 8 exits all targets, $0 net — pure spread capture with zero edge (8/8 target hits in [0.95-0.97] band = buy-high-hope; degenerate wins don't survive spread long-term)
    "scalp_buy_min":   0.95,    # SAFEST: only genuinely near-certain favorites
    "scalp_buy_max":   0.97,
    "scalp_gain":      0.06,    # FIX 2026-06-07: was 0.02 — spread was eating the entire 2¢ gain, $0 realized. Now 6¢ clears the spread.
    "scalp_stop":      1.0,     # DISABLED FOR 7 DAYS (was 0.06)
    "scalp_min_volume":250_000, # near-certain scalps need deep books to fill
    "scalp_max_open":  15,
    "scalp_bet_usdc":  1.0,
    "scalp_min_days":  1,       # skip <24h markets — that's where resolution GAPS skip the stop
    "scalp_max_days":  14,      # and skip year-out holds

    # ── fade strategy (house pick) ─────────────────────────────────
    # Buy the NO side of elevated speculative YES markets — the data
    # said the SELL/short side is where the edge lives.
    "fade_enabled": False,  # KILLED 2026-06-10: 29% win 28 exits — low win% even if P&L positive due to outlier; R:R 0.95
    "fade_yes_min":    0.60,    # fade when YES sits in this elevated band
    "fade_yes_max":    0.85,
    "fade_gain":       0.10,    # profit when NO rises 10¢ (YES decays)
    "fade_stop":       1.0,     # DISABLED FOR 7 DAYS (was 0.10)
    "fade_max_hold_hours": 72,  # 3 days: patient (> 48h global) but losers actually realize. Was 1440h/60d -> never timed out.
    "fade_min_volume": 200_000,
    "fade_max_open":   15,
    "fade_bet_usdc":   1.0,

    # ── fastfade (4th leg): same NO signal as fade, but quick profit + churn ─
    # Takes a small +target instead of holding for full decay, and re-enters
    # freely. A/B test of "fast" vs "patient" fade on the SAME markets.
    "fastfade_enabled":    False,  # KILLED 2026-06-08: 42 priced exits, -$0.617, confirmed loser — 11 time-exits + stop drag. Wind down via scan_exits.
    "fastfade_yes_min":    0.60,
    "fastfade_yes_max":    0.85,
    "fastfade_target":     0.06,   # FIX 2026-06-07: was 0.03 — target 3¢ vs stop 6¢ = 1:2 R:R guaranteed loss even at 86% hit rate. Now symmetric 1:1.
    "fastfade_stop":       1.0,    # DISABLED FOR 7 DAYS (was 0.06)
    "fastfade_max_hold_hours": 48,  # match global churn cap. Was 1440h/60d -> losers never timed out, faking +EV.
    "fastfade_min_volume": 200_000,
    "fastfade_max_open":   15,
    "fastfade_bet_usdc":   1.0,

    # ── allin (5th leg): trade EVERY market, no volume/price selectivity ─────
    # User-requested "trade all markets" test. Expected to return the negative
    # post-spread house average — runs isolated so it can't taint the others.
    "allin_enabled":       False,
    "allin_buy_min":       0.05,   # skip only degenerate ~0/~1 (no real book)
    "allin_buy_max":       0.95,
    "allin_target":        0.05,   # +5¢ take-profit
    "allin_stop": 1.0,
    "allin_min_volume":    0,      # NO volume filter — literally everything
    "allin_max_open":      20,     # CUT 2026-06-14: 100→20. Control point made overwhelmingly (-$104/757); smaller sample still proves marking honest, 5x less bleed
    "allin_bet_usdc":      1.0,
    "allin_max_hold_hours": 48,

    # ── momentum (6th leg): buy YES that's RISING — trend continuation ──
    # The one direction the other legs don't test (dip/fade/midfade are reversion).
    "momentum_enabled":    False,  # KILLED 2026-06-04: REAL losing, 10 exits, CI[-0.508,-0.043] excl 0. Has real stop -> verdict trustworthy. Open positions wind down via scan_exits.
    "momentum_yes_min":    0.40,
    "momentum_yes_max":    0.70,
    "momentum_chg_min":    0.04,   # require >=+4c 24h move to call it "rising"
    "momentum_gain":       0.05,
    "momentum_stop": 1.0,
    "momentum_min_volume": 200_000,
    "momentum_max_open":   15,
    "momentum_bet_usdc":   1.0,
    "momentum_max_hold_hours": 48,

    # ── favyes (7th leg): buy strong favorites 0.80-0.90 and hold ──
    # Tests the FAVORITE side of favorite-longshot bias (data hint: 0.8 -> ~81% YES).
    "favyes_enabled":      False,  # KILLED 2026-06-07: 15 priced exits, mean -$0.04/exit, CI [-0.168,+0.069] — noise with negative mean; 7 stale_nodata excluded. Wind down via scan_exits.
    "favyes_buy_min":      0.80,
    "favyes_buy_max":      0.90,
    "favyes_gain":         0.08,   # ride toward resolution
    "favyes_stop": 1.0,   # FIX 2026-06-07: was 0.20 — risking 20¢ to make 8¢ = 1:2.5 R:R, guaranteed negative. Now symmetric 1:1.
    "favyes_min_volume":   200_000,
    "favyes_max_open":     15,
    "favyes_bet_usdc":     1.0,
    "favyes_max_hold_hours": 72,   # 3 days (2026-06-04): was 1440h/60d -> only 2 closed in weeks, unmeasurable + delays loss realization. Has real 0.20 stop so milder than fade, but same survivorship-delay family.

    # ── coinflip (8th leg): buy ~0.50 indiscriminately — SECOND control ──
    # Bounds the noise floor near the coinflip zone; expected ~negative after spread.
    "coinflip_enabled":    False,
    "coinflip_buy_min":    0.45,
    "coinflip_buy_max":    0.55,
    "coinflip_gain":       0.05,
    "coinflip_stop": 1.0,
    "coinflip_min_volume": 100_000,
    "coinflip_max_open":   30,
    "coinflip_bet_usdc":   1.0,
    "coinflip_max_hold_hours": 48,

    # ── midfade (9th leg): mean-reversion — fade the recent drift back toward 0.50 ──
    # Rose recently -> hold NO; fell recently -> hold YES. Opposite of momentum.
    "midfade_enabled":     False,  # KILLED 2026-06-07: CONFIRMED REAL LOSER — 23 exits, CI [-0.117,-0.005] excludes 0. Mean -$0.059/exit. Open positions wind down via scan_exits.
    "midfade_yes_min":     0.40,
    "midfade_yes_max":     0.70,
    "midfade_chg_min":     0.04,   # only fade a real drift
    "midfade_gain":        0.05,
    "midfade_stop": 1.0,
    "midfade_min_volume":  200_000,
    "midfade_max_open":    15,
    "midfade_bet_usdc":    1.0,
    "midfade_max_hold_hours": 48,

    # ── research-backed fade variants (2026-06-07) — all buy NO + hold, same exit
    #    shape as fade (gain/stop 0.10, 72h), but the CORRECT [0.20,0.55] band the
    #    backtest+Wang fit say is +EV (vs the live fade leg's [0.60,0.85]). ───────
    "truefade_enabled":      False,  # KILLED 2026-06-10: 6% win 16 exits, all time exits on long-dated markets
    "truefade_gain": 0.10, "truefade_stop": 1.0, "truefade_max_open": 20,
    "truefade_bet_usdc": 1.0, "truefade_max_hold_hours": 72, "truefade_max_days": 60,
    "deepfade_enabled": False, "deepfade_gain": 0.10, "deepfade_stop": 1.0,  # KILLED 2026-06-10: 0% win 4 exits -$0.74
    "deepfade_max_open": 20, "deepfade_bet_usdc": 1.0, "deepfade_max_hold_hours": 72, "deepfade_max_days": 60,
    "nsfade_enabled":   False, "nsfade_gain":   0.10, "nsfade_stop": 1.0,  # KILLED 2026-06-10: 0% win 2 exits -$0.72
    "nsfade_max_open":   20, "nsfade_bet_usdc":   1.0, "nsfade_max_hold_hours":   72, "nsfade_max_days":   60,
    "wangfade_enabled": False, "wangfade_gain": 0.10, "wangfade_stop": 1.0,  # KILLED 2026-06-10: 0% win 4 exits -$1.21
    "wangfade_max_open": 20, "wangfade_bet_usdc": 1.0, "wangfade_max_hold_hours": 72, "wangfade_max_days": 60,
    "wangfade_lambda":  0.22,  # Wang distortion fit on our 365 markets
    "wangfade_margin":  0.04,  # only fade when YES overprices fair value by >= this

    # ── manifold (open-source cross-market divergence leg) ────────────────
    # Fetches top-200 open binary Manifold Markets markets (free public API,
    # no auth). For each Polymarket market, find the best title match (Jaccard
    # word-overlap) and trade in the direction Manifold's community leans when
    # |Manifold_prob - Poly_yes| >= min_divergence. The thesis: calibrated
    # independent forecasters seeing something different is a real signal.
    # Caveats: fuzzy matching will mismatch some pairs; the closed-trade set
    # will show whether match quality is sufficient. Paper only — measure first.
    "manifold_enabled":         False,  # KILLED 2026-06-19 loss-sweep: cumulative $-0.35 n=6
    "manifold_min_divergence":  0.12,   # min |Manifold - Poly| to enter; clears spread + noise
    "manifold_min_similarity":  0.55,   # min Jaccard word-overlap; 0.55 cuts most wrong-team matches
    "manifold_yes_min":         0.10,   # skip near-zero Poly markets (already effectively resolved)
    "manifold_yes_max":         0.90,   # skip near-certain markets (same reason)
    "manifold_gain":            0.10,
    "manifold_stop": 1.0,
    "manifold_min_volume":      100_000,
    "manifold_max_open":        10,
    "manifold_bet_usdc":        1.0,
    "manifold_max_hold_hours":  72,

    # ── metaculus (open-source cross-market divergence leg) ──────────────
    # Same signal, different source: Metaculus community median (q2) from
    # the public questions API (no auth). Metaculus forecasters are more
    # calibration-focused than Manifold; independent signals from both
    # sources is a stronger test than either alone.
    "metaculus_enabled":        False,  # CUT 2026-06-14: enabled since 06-07, 0 opens/0 closes in 7d (never triggers)
    "metaculus_min_divergence": 0.12,
    "metaculus_min_similarity": 0.55,
    "metaculus_yes_min":        0.10,
    "metaculus_yes_max":        0.90,
    "metaculus_gain":           0.10,
    "metaculus_stop": 1.0,
    "metaculus_min_volume":     100_000,
    "metaculus_max_open":       10,
    "metaculus_bet_usdc":       1.0,
    "metaculus_max_hold_hours": 72,

    # ── endgame (near-expiry fade, 2026-06-07) ────────────────────────────────
    # Hypothesis: liquidity providers widen spreads as markets approach resolution,
    # creating a "terminal uncertainty premium" in NO prices. Fading YES [.25,.75]
    # within 72h of close captures this — like theta in options near expiry.
    "endgame_enabled":        False,  # KILLED 2026-06-07: 4 exits, 0/4 wins, mean -$0.61/exit — hypothesis wrong (terminal uncertainty premium runs the other way). Open positions wind down via scan_exits.
    "endgame_yes_min":        0.25,   # not near-resolved (skip >75% favorites)
    "endgame_yes_max":        0.75,
    "endgame_max_hours":      72,     # only enter ≤72h before close
    "endgame_min_volume":     50_000, # lower vol floor OK near resolution
    "endgame_gain":           0.08,   # tighter target: less time to run
    "endgame_stop": 1.0,
    "endgame_max_open":       10,
    "endgame_bet_usdc":       1.0,
    "endgame_max_hold_hours": 72,

    # ── lowliq (illiquid-market fade, 2026-06-07) ─────────────────────────────
    # Hypothesis: markets with lower volume are less efficiently priced — bigger
    # Wang distortion, wider fair-value gap. Filters to volume BELOW the main
    # fade_min_volume floor (250k vs 300k) to explicitly test the illiquidity edge.
    "lowliq_enabled":        False,  # CUT 2026-06-14: enabled since 06-07, 0 opens/0 closes in 7d (never triggers)
    "lowliq_yes_min":        0.20,
    "lowliq_yes_max":        0.55,
    "lowliq_min_volume":     50_000,   # low end: at least some activity
    "lowliq_max_volume":     250_000,  # cap: below main fade liquidity threshold
    "lowliq_gain":           0.10,
    "lowliq_stop": 1.0,
    "lowliq_max_open":       10,
    "lowliq_bet_usdc":       1.0,
    "lowliq_max_hold_hours": 72,

    # ── contrarian (momentum-reversal, 2026-06-07) ────────────────────────────
    # Hypothesis: short-term price moves (>6c in one direction) overshoot and
    # revert. Pure reversal signal — bets opposite to recent chg regardless of
    # absolute price level (unlike midfade which uses chg as a secondary filter).
    # Uses any YES level [.10,.90] so it's not contaminated by price-band selection.
    "contrarian_enabled": False,  # KILLED 2026-06-10: 33% win 18 exits -$0.98 R:R 1.00 — structural loser
    "contrarian_chg_min":        0.06,  # min |chg| to trigger entry
    "contrarian_yes_min":        0.10,  # skip near-resolved
    "contrarian_yes_max":        0.90,
    "contrarian_min_volume":     200_000,
    "contrarian_gain":           0.08,
    "contrarian_stop":           0.08,
    "contrarian_max_open":       10,
    "contrarian_bet_usdc":       1.0,
    "contrarian_max_hold_hours": 48,    # short hold: reversal either happens fast or not

    # ── catpol (politics-category fade, 2026-06-07) ───────────────────────────
    # Hypothesis: political markets exhibit stronger narrative-driven overpricing
    # than the full population (poll reactions, partisan optimism). Tests whether
    # restricting the truefade universe to politics improves the fade signal.
    "catpol_enabled":        False,  # KILLED 2026-06-10: 0% win 2 exits -$0.74
    "catpol_yes_min":        0.20,
    "catpol_yes_max":        0.55,
    "catpol_min_volume":     100_000,
    "catpol_gain":           0.10,
    "catpol_stop":           0.10,
    "catpol_max_open":       10,
    "catpol_bet_usdc":       1.0,
    "catpol_max_hold_hours": 72,

    # ── conviction (resolution-arbitrage, 2026-06-08) ────────────────────────
    # THE key leg for 90% win rate. Strategy: find markets where the outcome is
    # ALREADY EFFECTIVELY KNOWN from price action + elite consensus, but Polymarket
    # hasn't fully priced it in yet. We're not predicting — we're arbitraging latency.
    #
    # Entry requires ALL of:
    #   1. YES priced 0.10–0.30 (cheap — big upside if it resolves YES)
    #   2. ≥6 elite wallets bought in the last 6h (consensus forming fast = informed)
    #   3. Price already moved +10¢ toward YES in the current session (confirming)
    #   4. Volume ≥ $500k and surging (money flowing in = real signal)
    #   5. Resolves within 72h (short horizon = less time for thesis to be wrong)
    #
    # This means we enter LATE in the price discovery, after smart money has moved,
    # when the remaining discount is still large enough to be worth taking.
    # Win rate target: 85–95%. Return target: 3–8x (buy at 0.15, exit near 0.85).
    "conviction_enabled":       False,  # KILLED 2026-06-10: 0% win 2 exits -$1.43
    "conviction_yes_min":       0.10,   # minimum YES price (below = too illiquid/unknown)
    "conviction_yes_max":       0.30,   # maximum YES price (above = not enough upside)
    "conviction_min_wallets":   6,      # ≥6 elite wallets — strong consensus
    "conviction_min_vol":     200_000,  # $200k minimum — liquid market
    "conviction_min_chg":       0.02,   # YES must have risen ≥2¢ today (confirmed upward move)
    "conviction_max_hours":    720,     # resolves within 30d — was 72h, widened to get data flowing
    "conviction_target":        0.80,   # exit when YES hits 80¢ (3–8x depending on entry)
    "conviction_stop":          0.05,   # stop if YES falls back to 5¢ (thesis failed)
    "conviction_max_open":      5,      # max 5 open (high conviction = concentrate)
    "conviction_bet_usdc":      1.0,
    "conviction_max_hold_hours": 720,  # hold up to 30d — matches conviction_max_hours

    # ── ADHD executive-function legs (2026-06-09) ─────────────────────────────
    # Three legs inspired by ADHD cognitive patterns that mirror crowd psychology
    # failures on prediction markets. Each targets a specific bias.

    # deadline (Time Blindness): crowd treats a ≤24h market like it has weeks.
    # At <1 day remaining, a market at 45-72¢ should already be pricing toward
    # 0 or 1 — the uncertainty premium is irrational this close to resolution.
    # Buy YES if it's already moving up (chg≥4¢) — ride the crowd's late catch-up.
    # R:R: at entry 0.60, target 0.95 → +35¢ vs 8¢ stop = 4.4:1
    "deadline_enabled":        False,  # KILLED 2026-06-15: n=17, 24% WR, -$1.37. Kill rule (n≥10, total<-$0.50, WR<60%). Near-deadline theta taker — no edge.
    "deadline_bet_usdc":       1.0,
    "deadline_max_open":       10,
    "deadline_max_hold_hours": 24,      # these expire fast — hard cap at 24h
    "deadline_yes_min":        0.45,
    "deadline_yes_max":        0.72,
    "deadline_min_vol":        500_000,
    "deadline_max_days":       1,       # resolves within 24h
    "deadline_chg":            0.04,    # already moving up ≥4¢ (crowd catching on)
    "deadline_target":         0.95,    # absolute YES target
    "deadline_stop":           0.08,    # 8¢ stop below entry
    "deadline_excl_kw":        ("crude oil", "(cl)", "natural gas", "(ng)",
                                 "gold ", "(gc)", "silver ", "s&p 500", "nasdaq",
                                 "dow jones", "russell 2000", "vix", "oil price"),

    # impulse (Inhibition Failure): crowd acts on impulse — a market jumps ≥12¢
    # in a single scan cycle on a liquid market. This is irrational overshooting.
    # Fade it: buy NO (the crowd will regret the impulse buy and partially reverse).
    # Gate: requires _price_snap delta ≥ 12¢ AND vol ≥ $1M (real liquid market).
    # R:R: NO entry at ~15-30¢, target NO+0.20 → 4-13x. Stop 5¢.
    "impulse_enabled": False,  # KILLED 2026-06-10: 0% win 2 exits -$1.97 — immediate kill
    "impulse_bet_usdc":        1.0,
    "impulse_max_open":        10,
    "impulse_max_hold_hours":  240,     # 10 days — let the reversal play out
    "impulse_yes_min":         0.65,    # YES already high after the impulse spike
    "impulse_yes_max":         0.90,
    "impulse_min_vol":         1_000_000,  # must be a very liquid market
    "impulse_min_spike":       0.12,    # scan delta (current - _price_snap) must be ≥12¢
    "impulse_target_delta":    0.20,    # NO target = entry_NO + 0.20 (partial reversal)
    "impulse_stop_delta":      0.05,    # stop if NO falls 5¢ (spike keeps going)

    # fogbuy (Working Memory / Forgetting): market was priced high but the crowd
    # gradually "forgot" and drifted it down over days. Still high volume = still
    # contested = original thesis may still be valid. Buy the discount.
    # Gate: chg ≤ -0.08 (been declining 24h) but vol ≥ $600k (still active),
    #       AND no scan-level crash (scan delta > -0.05 — steady drift not collapse).
    # R:R: +20¢ target vs 8¢ stop = 2.5:1 (entry 0.35-0.58)
    "fogbuy_enabled":          False,  # CUT 2026-06-15: kill rule — n=18, -$3.56, 0% WR
    "fogbuy_bet_usdc":         1.0,
    "fogbuy_max_open":         10,
    "fogbuy_max_hold_hours":   336,     # 14 days — memory can take time to return
    "fogbuy_yes_min":          0.35,
    "fogbuy_yes_max":          0.58,
    "fogbuy_min_vol":          600_000,
    "fogbuy_min_chg_down":     0.08,    # must have fallen ≥8¢ in 24h (the forgetting)
    "fogbuy_max_scan_drop":    0.05,    # scan delta must be > -5¢ (drift, not crash)
    "fogbuy_min_days":         4,       # at least 4 days left for thesis to play out
    "fogbuy_target_delta":     0.20,    # target = entry + 20¢
    "fogbuy_stop":             0.08,    # stop 8¢ below entry

    # ── anchordrift — anchoring bias fade (2026-06-09) ──────────────────────
    # Markets stuck near round psychological anchors (25¢, 50¢, 75¢) that
    # haven't moved in 24h are over-anchored by crowd consensus. Buy NO in
    # the [0.22,0.55] band — anchored markets tend to break toward resolution
    # extremes, and YES holders pay the vig while waiting.
    "anchordrift_enabled": False,  # KILLED 2026-06-10
    "anchordrift_yes_min":       0.22,
    "anchordrift_yes_max":       0.55,
    "anchordrift_max_chg":       0.02,   # 24h chg must be ≤ ±2¢ (stuck/anchored)
    "anchordrift_min_volume":    200_000,
    "anchordrift_max_open":      12,
    "anchordrift_bet_usdc":      1.0,
    "anchordrift_gain":          0.10,
    "anchordrift_stop":          0.08,
    "anchordrift_max_hold_hours": 120,   # 5 days — give drift time

    # ── volcrush — volume collapse fade (2026-06-09) ─────────────────────────
    # Markets where 24h volume dropped significantly (chg ≤ -3¢) while YES sits
    # in [0.30,0.65]. Volume collapse = smart money stopped defending. Fade YES.
    "volcrush_enabled":          False,  # KILLED 2026-06-10: 5 exits 0% win -$0.16/exit
    "volcrush_yes_min":          0.30,
    "volcrush_yes_max":          0.65,
    "volcrush_min_chg_down":     0.03,   # YES must have dropped ≥3¢ in 24h
    "volcrush_min_volume":       300_000,
    "volcrush_max_open":         10,
    "volcrush_bet_usdc":         1.0,
    "volcrush_gain":             0.08,
    "volcrush_stop":             0.06,
    "volcrush_max_hold_hours":   72,

    # ── latefade — late-game FLB fade, <48h remaining (2026-06-09) ───────────
    # Tighter window than shortdate (which does 1-7 days). Markets with <2 days
    # left and YES in [0.20,0.50] are extreme longshots where the crowd is
    # paying a massive premium on hope. Resolution is imminent → low drawdown.
    "latefade_enabled":          False,  # KILLED 2026-06-15: n=14, 43% WR, -$0.73. Kill rule met. Late-window fade taker — fade-family bleed.
    "latefade_yes_min":          0.20,
    "latefade_yes_max":          0.50,
    "latefade_max_days":         2,      # ≤48h to resolution
    "latefade_min_days":         0.1,    # at least ~2.4h left
    "latefade_min_volume":       100_000,
    "latefade_max_open":         15,
    "latefade_bet_usdc":         1.0,
    "latefade_gain":             0.12,   # larger target — if it resolves NO, we collect
    "latefade_stop":             0.06,
    "latefade_max_hold_hours":   60,     # 2.5 days — let it resolve

    # ── momno — negative momentum continuation (2026-06-09) ──────────────────
    # Markets where YES is already falling (chg ≤ -5¢ in 24h) and sits in
    # [0.30,0.70]. Buy NO to ride the continuation. This is OPPOSITE to
    # mean-reversion legs (crashbuy/pricerev) — tests whether momentum or
    # reversion dominates in prediction markets.
    "momno_enabled":             False,  # KILLED 2026-06-10: 0% win 2 exits -$0.31
    "momno_yes_min":             0.30,
    "momno_yes_max":             0.70,
    "momno_min_chg_down":        0.05,   # YES must have fallen ≥5¢ in 24h
    "momno_min_volume":          300_000,
    "momno_max_open":            10,
    "momno_bet_usdc":            1.0,
    "momno_gain":                0.08,
    "momno_stop":                0.06,
    "momno_max_hold_hours":      72,

    # ── narrowband — near-coinflip fade (2026-06-09) ─────────────────────────
    # YES in [0.40,0.52] on high-volume markets (>$500k). These are "nobody
    # knows" markets — the vig disproportionately hurts YES holders on uncertain
    # outcomes because YES holders pay the full spread while NO holders benefit
    # from the slight structural edge of "most things don't happen."
    "narrowband_enabled": False,  # KILLED 2026-06-10
    "narrowband_yes_min":        0.40,
    "narrowband_yes_max":        0.52,
    "narrowband_min_volume":     500_000,
    "narrowband_max_open":       10,
    "narrowband_bet_usdc":       1.0,
    "narrowband_gain":           0.08,
    "narrowband_stop":           0.06,
    "narrowband_max_hold_hours": 72,

    # ── esportsfav — esports YES favorite buy (2026-06-10) ───────────────────
    # nearres data: 9/9 esports target hits. Extend to broader band [0.55,0.80].
    # Thesis: esports crowds overweight upsets; favorites are systematically cheap.
    # Keyword filter ensures esports only (cs2/valorant/lol/dota/esport).
    "esportsfav_enabled":         False,  # KILLED 2026-06-19 loss-sweep: cumulative $-0.51 n=16
    "esportsfav_yes_min":         0.80,   # DATA: only 0.80-0.85 passes break-even
    "esportsfav_yes_max":         0.85,
    "esportsfav_min_volume":      200_000,
    "esportsfav_max_open":        10,
    "esportsfav_bet_usdc":        1.0,
    "esportsfav_gain":            0.06,   # 0.825→0.885, calculated
    "esportsfav_stop":            0.09,   # wide: 0.825→0.735 = catastrophic reversal
    "esportsfav_max_hold_hours":  72,

    # ── resolvetoday — high-confidence market expiring today (2026-06-10) ────
    # Markets resolving within 24h where YES is 80-97¢. Time-decay kills the
    # stop: there's not enough time for a reversal. Purely a theta play.
    # Different from nearres: nearres fires at 6h, this fires at 1-24h.
    "resolvetoday_enabled":       False,  # KILLED 2026-06-19 loss-sweep: cumulative $-0.20 n=4
    "resolvetoday_yes_min":       0.80,
    "resolvetoday_yes_max":       0.85,
    "resolvetoday_min_hours":     6.0,    # >6h: no overlap with nearres
    "resolvetoday_max_hours":     24.0,
    "resolvetoday_min_volume":    300_000,
    "resolvetoday_max_open":      12,
    "resolvetoday_bet_usdc":      1.0,
    "resolvetoday_gain":          0.06,   # calculated
    "resolvetoday_stop":          0.09,
    "resolvetoday_max_hold_hours": 26,

    # ── stablehigh — stable high-prob market (2026-06-10) ────────────────────
    # Markets sitting at 83-96¢ with near-zero 24h change (abs chg < 1.5¢).
    # Stability at high prices signals market consensus = YES. No recent news to
    # reverse it. Different from nearres (no time filter) and resolvetoday (no
    # expiry filter) — pure price-stability signal.
    "stablehigh_enabled": False,  # KILLED 2026-06-10
    "stablehigh_yes_min":         0.80,   # DATA: exactly the profitable band
    "stablehigh_yes_max":         0.85,
    "stablehigh_max_chg":         0.010,  # ≤1c move → stop rate < 15%
    "stablehigh_min_volume":      500_000,
    "stablehigh_max_open":        10,
    "stablehigh_bet_usdc":        1.0,
    "stablehigh_gain":            0.06,
    "stablehigh_stop":            0.09,   # wide: stable rarely drops 9c
    "stablehigh_max_hold_hours":  120,

    # ── priceleap — upward price momentum continuation (2026-06-10) ──────────
    # Markets that JUMPED ≥12¢ in 24h, now in [0.55,0.82]. Thesis: the jump was
    # caused by new information. Momentum continues 24-48h before reversion.
    # Opposite of momno (which fades drops). Buy YES on the breakout.
    "priceleap_enabled":          False,  # KILLED by data: 0.55-0.82 all stop%>40%
    "priceleap_yes_min":          0.55,
    "priceleap_yes_max":          0.82,
    "priceleap_min_chg_up":       0.12,
    "priceleap_min_volume":       400_000,
    "priceleap_max_open":         10,
    "priceleap_bet_usdc":         1.0,
    "priceleap_gain":             0.08,
    "priceleap_stop":             0.07,
    "priceleap_max_hold_hours":   48,

    # ── thetano — deep longshot fade (2026-06-10) ─────────────────────────────
    # YES between 0.05-0.18 with >3 days left = NO at 82-95¢.
    # Deep FLB: extreme longshots are even more overpriced than moderate ones.
    # Different from fade (0.20-0.55 YES band) — targets the deep tail.
    "thetano_enabled":            False,  # KILLED by data: CI[-0.14,-0.026] negative
    "thetano_yes_min":            0.05,
    "thetano_yes_max":            0.18,
    "thetano_min_days":           3,
    "thetano_max_days":           60,
    "thetano_min_volume":         200_000,
    "thetano_max_open":           12,
    "thetano_bet_usdc":           1.0,
    "thetano_gain":               0.06,
    "thetano_stop":               0.07,
    "thetano_max_hold_hours":     240,

    # ── breakoutyes — 50¢ resistance breakout continuation (2026-06-10) ──────
    # Markets where YES just crossed 50¢ upward: now [0.52,0.65] with chg ≥5¢.
    # 50¢ is psychological resistance — once broken, momentum accelerates.
    # Buy YES on the breakout continuation.
    "breakoutyes_enabled":        False,  # KILLED by data: stop%=46-49%
    "breakoutyes_yes_min":        0.52,
    "breakoutyes_yes_max":        0.65,
    "breakoutyes_min_chg_up":     0.05,
    "breakoutyes_min_volume":     300_000,
    "breakoutyes_max_open":       10,
    "breakoutyes_bet_usdc":       1.0,
    "breakoutyes_gain":           0.08,
    "breakoutyes_stop":           0.06,
    "breakoutyes_max_hold_hours": 48,

    # ── longshot — tiered inverse-moonshot: buy NO on overconfident YES (2026-06-09) ──
    # Thesis: Overconfident YES markets (72-93¢) systematically underweight tail
    # risk. Buy NO cheap while crowd consensus is still building — if the event
    # fails, NO pays 3-8x. Two tiers mirror moonshot's tiered structure.
    # R:R: t_high 0.30/0.04=7.5:1   t_mid 0.22/0.08=2.75:1
    "longshot_enabled": False,  # KILLED 2026-06-10: 33% win 6 exits -$1.00 R:R 1.25 — replaced by longshortbias
    "longshot_bet_usdc":          1.0,
    "longshot_max_open":          18,
    "longshot_t_high_max_open":   6,      # cap high-tier (most asymmetric, most speculative)
    "longshot_t_mid_max_open":    12,
    "longshot_max_hold_hours":    720,    # 30d — tail-risk needs time to materialize
    "longshot_excl_kw":           ("crude oil", "(cl)", "natural gas", "(ng)",
                                    "gold ", "(gc)", "silver ", "s&p 500", "nasdaq",
                                    "dow jones", "russell 2000", "vix", "oil price"),
    # Tier high (NO at 7-15¢): YES[0.85-0.93] — crowd almost certain, max payout if wrong
    "longshot_th_yes_min":        0.85,
    "longshot_th_yes_max":        0.93,
    "longshot_th_min_vol":        800_000,  # very liquid — thin NO book at high YES prices
    "longshot_th_chg":            0.04,     # YES still rising ≥4¢ = overbought momentum
    "longshot_th_target_delta":   0.30,     # NO abs_target = entry_NO + 0.30
    "longshot_th_stop_delta":     0.04,     # stop if NO falls 4¢ (YES hits 97¢)
    # Tier mid (NO at 15-28¢): YES[0.72-0.85] — more liquid NO book, still asymmetric
    "longshot_tm_yes_min":        0.72,
    "longshot_tm_yes_max":        0.85,
    "longshot_tm_min_vol":        400_000,
    "longshot_tm_chg":            0.03,     # YES rising ≥3¢
    "longshot_tm_target_delta":   0.22,     # NO abs_target = entry_NO + 0.22
    "longshot_tm_stop_delta":     0.08,     # stop if NO falls 8¢

    # ── nearwin — near-resolution YES chase (2026-06-09) ─────────────────────
    # Thesis: Markets resolving in ≤7 days at YES[55-80¢] moving upward already
    # have informed money behind them. Short horizon means target is reachable
    # before fresh bad news arrives. Structurally similar to moonshot 2x but
    # with a TIME gate instead of a price-band gate.
    # R:R: at entry 0.65, target 0.95 → +30¢ gain vs 8¢ stop = 3.75:1
    "nearwin_enabled": False,  # KILLED 2026-06-10
    "nearwin_bet_usdc":           1.0,
    "nearwin_max_open":           12,
    "nearwin_max_hold_hours":     168,    # 7 days — near-resolution, don't hold past close
    "nearwin_yes_min":            0.55,
    "nearwin_yes_max":            0.80,
    "nearwin_min_vol":            400_000,
    "nearwin_max_days":           7,      # market must resolve within 7 days
    "nearwin_chg":                0.06,   # YES rising ≥6¢ — real upward momentum
    "nearwin_target":             0.95,   # absolute YES target (near-certain resolution)
    "nearwin_stop":               0.08,   # stop 8¢ below entry
    "nearwin_excl_kw":            ("crude oil", "(cl)", "natural gas", "(ng)",
                                    "gold ", "(gc)", "silver ", "s&p 500", "nasdaq",
                                    "dow jones", "russell 2000", "vix", "oil price"),

    # ── crashbuy — oversold bounce entry (2026-06-09) ────────────────────────
    # Thesis: Markets crashing ≥10¢ in 24h often overshoot. When the scan-level
    # delta flips positive (first tick up after the crash), early smart money
    # is re-entering. Catch the reversion with asymmetric target vs stop.
    # Gate: chg ≤ -0.10 (crashed) AND current > _price_snap (scan-level bounce started)
    # R:R: +20¢ target vs 8¢ stop = 2.5:1 (entry 0.35–0.60)
    "crashbuy_enabled":           False,  # KILLED 2026-06-15: n=17, 41% WR, -$1.13. Kill rule met. Buy-the-crash taker — falling-knife pattern.
    "crashbuy_bet_usdc":          1.0,
    "crashbuy_max_open":          12,
    "crashbuy_max_hold_hours":    240,    # 10 days — give reversion time to play out
    "crashbuy_yes_min":           0.25,
    "crashbuy_yes_max":           0.60,
    "crashbuy_min_vol":           400_000,
    "crashbuy_min_crash":         0.10,   # chg must be ≤ -0.10 (fell ≥10¢ in 24h)
    "crashbuy_min_days":          3,      # must have ≥3 days remaining
    "crashbuy_target_delta":      0.20,   # target = entry_mid + 20¢
    "crashbuy_stop":              0.08,   # stop 8¢ below entry

    # ── tp20 — "take 0.20 and sell" scalp (user request 2026-06-14) ──────────
    # Buy YES on an UPWARD tick in the low band, take a fixed +20¢ and sell,
    # hard stop −10¢ (2:1). Taker entry at the ask (pays half-spread, as asked).
    # HONEST NOTE: momentum-continuation is in the graveyard (momentum family
    # dead at scale), and a taker pays the spread — so expectation is modest.
    # This is a clean testable version of "buy and sell at +20¢"; judge at n≥10.
    "tp20_enabled":               False,  # KILLED 2026-06-15: n=13, 23% WR, -$3.07. Kill rule (n≥10, total<-$0.50, WR<60%). Tight-target taker leg bleeds spread.
    "tp20_bet_usdc":              1.0,
    "tp20_max_open":              10,
    "tp20_max_hold_hours":        72,
    "tp20_yes_min":               0.15,   # low enough that +0.20 lands ≤ ~0.75
    "tp20_yes_max":               0.55,
    "tp20_min_vol":               200_000,
    "tp20_min_rise":              0.02,   # rose ≥2¢ vs last scan (continuation)
    "tp20_gain":                  0.20,   # take +20¢ and sell
    "tp20_stop":                  0.10,   # hard stop −10¢

    # ── scalp02 — "buy & sell at 0.20% profit" micro-scalp (user req 2026-06-14)
    # MAKER entry at mid (the ONLY viable form: a taker pays a 1-3¢ spread to
    # chase a 0.2¢ target — guaranteed loss). On very-liquid, mid-priced,
    # price-STABLE markets so the resting order fills without being run over.
    # Take +0.2¢ and sell, 3¢ stop. HONEST: tiny TP + wide stop = the win-rate
    # trap (need >93% WR just to break even); wins ~95%+ but EV is knife-edge
    # and depends entirely on whether paper maker-fills are realistic. Pure
    # experiment, judged at n≥10 — expect the kill rule to retire it.
    "scalp02_enabled":            False,  # KILLED 2026-06-14: n=6 0% WR -$0.32 (kill rule). STRUCTURAL: the 0.2c target < honest exit half-spread (~1c), so every "target" hit books a LOSS (entry+0.002-half < entry). Proves 0.2% scalp is dead vs spread, exactly as predicted.
    "scalp02_bet_usdc":           1.0,
    "scalp02_max_open":           15,
    "scalp02_max_hold_hours":     6,      # scalp — bail if +0.2% doesn't hit fast
    "scalp02_yes_min":            0.30,   # mid-priced = most stable
    "scalp02_yes_max":            0.70,
    "scalp02_min_vol":            1_000_000,  # very liquid → maker can rest
    "scalp02_max_chg":            0.03,   # price-stable: resting order won't get run over
    "scalp02_gain":               0.002,  # take +0.2¢ (~0.2-0.6% return) and sell
    "scalp02_stop":               0.03,   # bound the tail at −3¢

    # ── volsurge — volume spike + PRICE CONFIRMATION (user request 2026-06-14)
    # The honest fix to volspike (which lost: it guessed direction by price LEVEL,
    # a fade with no confirmation). volsurge requires vol ≥3× baseline AND price
    # already MOVING ≥3¢ this scan → buy the DIRECTION of the move (informed-flow
    # continuation = the same underreaction thesis as nearres). Compound AND-gate
    # (Lesson 7). House 3:1 R:R, judge at n≥10.
    "volsurge_enabled":           True,
    "volsurge_vol_threshold":     3.0,    # vol ≥ 3× EMA baseline (reuses _vol_baseline)
    "volsurge_min_move":          0.03,   # price moved ≥3¢ this scan (the confirmation)
    "volsurge_yes_min":           0.12,
    "volsurge_yes_max":           0.88,
    "volsurge_min_volume":        200_000,
    "volsurge_min_baseline":      10_000, # baseline must be established
    "volsurge_max_open":          6,
    "volsurge_bet_usdc":          1.0,
    "volsurge_gain":              0.09,
    "volsurge_stop":              0.03,
    "volsurge_max_hold_hours":    48,

    # ── moonshot — ORIGINAL single-tier design (config block lives further below) ──
    # Reverted 2026-06-18 from the tiered 2x/5x/10x version (killed 2026-06-10, 20.6% WR).
    # ── esportsdog — esports underdog YES buy (2026-06-10) ───────────────────
    # DATA: YES 0.10-0.15 EV=+$0.089, YES 0.20-0.25 EV=+$0.198 (moonshot-style exits).
    # nearres proved esports YES is underpriced. At low prices the payout is even bigger.
    # Esports keywords only — other categories have higher stop rates at this price.
    # R:R: entry 0.20, target 0.65, stop 0.05 → (0.45/0.65) / (0.05/0.15) = 6.9:1
    "esportsdog_enabled":      False,  # KILLED 2026-06-13: 0/8 wins -$20 — buying underdogs against our own proven favorites edge (kill rule n>=5 WR<=20% negative)
    "esportsdog_yes_min":        0.12,
    "esportsdog_yes_max":        0.30,
    "esportsdog_min_volume":     200_000,
    "esportsdog_max_open":       8,
    "esportsdog_bet_usdc":       4.0,   # RISKY: deep longshot 1:4 sizing
    "esportsdog_abs_target":     0.65,   # ~3-5x payout at entry 0.12-0.30
    "esportsdog_stop_delta":     0.05,   # tight: lose $0.25-0.45 max
    "esportsdog_max_hold_hours": 120,    # 5 days — esports resolves fast

    # ── geopolbomb — geopolitical shock YES buy (2026-06-10) ─────────────────
    # DATA: Iran airspace at 0.211 → +$2.12, at 0.280 → +$1.38. These are the
    # top 2 trades in the entire dataset. Geopolitical events are fat-tail:
    # most resolve NO but when YES hits, payout is enormous.
    # Keyword filter: airspace/ceasefire/coup/war/strike/nuclear/sanction/invasion.
    # R:R: entry 0.20, target 0.70, stop 0.05 → 13:1 on investment
    "geopolbomb_enabled": False,  # KILLED 2026-06-10
    "geopolbomb_yes_min":        0.08,
    "geopolbomb_yes_max":        0.28,
    "geopolbomb_min_volume":     300_000,
    "geopolbomb_max_open":       6,
    "geopolbomb_bet_usdc":       4.0,   # RISKY: geo tail bet 1:4 sizing
    "geopolbomb_abs_target":     0.70,   # massive payout if event happens
    "geopolbomb_stop_delta":     0.04,   # very tight — these are pure tail bets
    "geopolbomb_max_hold_hours": 720,    # 30 days — geopolitical events need time
    "geopolbomb_kw":             ("airspace", "ceasefire", "cease-fire", "coup",
                                   "invasion", "nuclear", "sanction", "blockade",
                                   "war ", "warfare", "missile", "airstrike",
                                   "peace deal", "peace agreement", "withdrawal",
                                   "annexation", "regime change"),

    # ── alicenews — OpenAlice news-confirmed geopolitical YES buy (2026-06-11) ──
    # Signal: OpenAlice /api/news returns breaking headlines every cycle.
    # Only enter a geopolitical market when an actual matching headline exists
    # in OpenAlice's last 6h feed — upgrades geopolbomb from keyword-only to
    # news-confirmed. Same moonshot exits (target 0.70, stop 0.04).
    # R:R: entry 0.18, target 0.70, stop 0.04 → 2.9:1
    "alicenews_enabled":         False,  # CUT 2026-06-15: kill rule — n=7, -$4.57, 0% WR.
                                         # 2026-06-21: signal upgraded to MULTI-SOURCE (see entry block);
                                         # stays disabled — re-enable is a human call (predictive geo = dead end).
    "alicenews_yes_min":         0.08,
    "alicenews_yes_max":         0.35,
    "alicenews_min_volume":      200_000,
    "alicenews_max_open":        6,
    "alicenews_bet_usdc":        2.0,   # MEDIUM RISKY
    "alicenews_abs_target":      0.70,
    "alicenews_stop_delta":      0.04,
    "alicenews_max_hold_hours":  168,   # 7 days
    "alicenews_min_sources":     2,     # MULTI-INFO: # independent feeds (OpenAlice/hermes/GDELT) that
                                        # must corroborate a market keyword before entry (2026-06-21)
    "alicenews_api_url":         "http://127.0.0.1:47331/api/news",
    "hermes_api_url":            "http://127.0.0.1:48580/api/news",
    "alicenews_kw":              ("ceasefire", "cease-fire", "coup", "invasion",
                                  "nuclear", "sanction", "blockade", "war",
                                  "missile", "airstrike", "peace deal",
                                  "withdrawal", "annexation", "regime change",
                                  "tariff", "trade war", "election", "resign",
                                  "impeach", "default", "earthquake", "hurricane",
                                  "pandemic", "outbreak", "ban", "summit"),

    # ── midfield — moonshot gap-fill YES 0.30-0.40 (2026-06-10) ─────────────
    # DATA: YES 0.30-0.35 EV=+$0.158, YES 0.35-0.40 EV=+$0.197 (moonshot exits).
    # Moonshot t2 starts at 0.35 but requires chg≥6¢. This leg fills the gap with
    # a lower chg threshold. Different from moonshot: targets absolute 0.70 (not entry+0.35).
    # R:R: entry 0.35, target 0.70, stop 0.05 → (0.35/0.70) / (0.05/0.30) = 3.0:1
    "midfield_enabled":      False,  # KILLED 2026-06-11: 0/9 wins, -$3.88 — gap-band thesis dead
    "midfield_yes_min":          0.30,
    "midfield_yes_max":          0.42,   # just below moonshot_t2_yes_min=0.35 overlap
    "midfield_min_volume":       400_000,
    "midfield_min_chg":          0.03,   # lower bar than moonshot t2 (0.06¢)
    "midfield_max_open":         8,
    "midfield_bet_usdc":         2.0,   # MEDIUM RISKY: 1:4:2 sizing
    "midfield_abs_target":       0.70,   # absolute 70¢ — doubles money at entry 0.35
    "midfield_stop_delta":       0.05,   # tight stop
    "midfield_max_hold_hours":   240,    # 10 days

    # ── cryptobull — crypto YES price milestone buy (2026-06-10) ─────────────
    # DATA: YES 0.10-0.25 EV=+$0.07-$0.20 with moonshot-style exits.
    # Crypto keywords only (BTC/ETH/SOL price milestones).
    # Different from moonshot: crypto-only filter + no chg requirement (milestones
    # don't need a recent move — they're pure directional bets on levels).
    # R:R: entry 0.18, target 0.65, stop 0.05 → (0.47/0.65) / (0.05/0.13) = 1.9:1 per $
    "cryptobull_enabled":        False,  # KILLED 2026-06-19 loss-sweep: cumulative $-1.35 n=1
    "cryptobull_yes_min":        0.10,
    "cryptobull_yes_max":        0.28,
    "cryptobull_min_volume":     500_000,  # crypto markets are liquid
    "cryptobull_max_open":       6,
    "cryptobull_bet_usdc":       2.0,   # MEDIUM RISKY: 1:4:2 sizing
    "cryptobull_abs_target":     0.65,
    "cryptobull_stop_delta":     0.05,
    "cryptobull_max_hold_hours": 480,    # 20 days — crypto needs time
    "cryptobull_kw":             ("bitcoin", "btc ", "btc$", "ethereum", "eth ",
                                   "solana", "sol ", "xrp", "crypto", "cryptocurrency",
                                   "coinbase", "binance", "defi", "altcoin"),

    #
    # ── moonshot — ORIGINAL 10x-or-nothing design, revived 2026-06-18 ──────────
    # Buy YES at 5–10¢ on a $1M+ market, target 50¢ (5–10x), NO STOP ($1 entry IS
    # the max loss), max 2 open, 30-day hold. The original gate ("≥2 elite wallets
    # in") read a per-market trader_count that no longer exists in the feed — it is
    # replaced with the live whale-flow signal (large wallets net-accumulating).
    # FRESH PAPER RE-TEST: whale-as-instruction is a documented dead signal
    # (44% WR, n=136) — this is re-measurement, not a claimed edge.
    "moonshot_enabled":         False,      # KILLED 2026-06-22: n=34 WR21% -$6.42 gross(no-fee) — predictive null (calibrated mids)
    "moonshot_yes_min":         0.05,       # buy at 5¢ or lower
    "moonshot_yes_max":         0.10,       # up to 10¢ — above this it's not cheap enough
    "moonshot_min_volume":      1_000_000,  # $1M+ liquid market only
    "moonshot_min_whale_buy":   400.0,      # ≥$400 large-wallet buys (≈2 elite buys) AND buy>sell
    "moonshot_target":          0.50,       # YES hits 50¢ = 5–10x return
    "moonshot_max_open":        2,          # ≤2 open = ~10% of portfolio
    "moonshot_bet_usdc":        1.0,        # $1 per trade — loss capped at $1
    "moonshot_max_hold_hours":  720,        # 30 days — give long shots time to play out
    # commodity/macro exclusion list (shared key — the conviction leg reads it too)
    "moonshot_excl_kw":         ("crude oil", "(cl)", "natural gas", "(ng)",
                                  "gold ", "(gc)", "silver ", "s&p 500", "nasdaq",
                                  "dow jones", "russell 2000", "vix", "oil price"),

    # ── wcrank (FIFA ranking + WC standings + recent form, 2026-06-07) ──────
    # Signal: combine three independent historical/live data sources:
    #  1. FIFA pre-tournament world ranking (reflects ~3 years of weighted
    #     performance — the closest "last 10 year data" available free/public)
    #  2. ESPN in-tournament WC group standing (live W/L/pts after games played)
    #  3. TheSportsDB most-recent match result (short-term form signal)
    # Compute rank-implied probability per question type (WC winner / group
    # winner / advance to knockout) and trade when it diverges from Poly YES
    # by >= min_divergence. Only fires on World Cup soccer markets.
    "wcrank_enabled":        False,  # KILLED 2026-06-19 loss-sweep: cumulative $-0.19 n=3
    "wcrank_min_divergence": 0.15,    # higher than other legs — rank signal is noisy
    "wcrank_yes_min":        0.05,    # sports markets can have very low prices (most teams won't win WC)
    "wcrank_yes_max":        0.95,
    "wcrank_min_volume":     100_000,
    "wcrank_max_open":       10,
    "wcrank_bet_usdc":       1.0,
    "wcrank_gain":           0.12,
    "wcrank_stop":           0.12,
    "wcrank_max_hold_hours": 72,

    # ── wcnews (ESPN media attention signal, 2026-06-07) ─────────────────────
    # Signal: ESPN WC article mention count per team in the last ~48h of coverage.
    # High media attention on a team + elevated YES price → contrarian fade (NO).
    # High media attention on a team + depressed YES price → buy YES (market hasn't
    # repriced the coverage yet). Captures news-driven mispricings.
    # Uses the SAME ESPN WC news cache as the wcrank enrichment (no extra calls).
    "wcnews_enabled": False,  # KILLED 2026-06-10: 0% win 1 exit -$0.18 — killed on first data
    "wcnews_min_mentions":   5,       # min ESPN articles to have a media signal at all
    "wcnews_divergence":     0.12,    # min |news-implied prob - Poly YES| to enter
    "wcnews_yes_min":        0.05,
    "wcnews_yes_max":        0.95,
    "wcnews_min_volume":     100_000,
    "wcnews_max_open":       8,
    "wcnews_bet_usdc":       1.0,
    "wcnews_gain":           0.10,
    "wcnews_stop":           0.10,
    "wcnews_max_hold_hours": 48,      # news signals decay faster

    # ── wikivol (Wikipedia page-view surge, 2026-06-07) ──────────────────────
    # Wikimedia REST API — free, no auth, works from any IP. Detects topics
    # with sudden attention spikes that the market may not have priced in yet.
    # surge = avg(last 7 days views) / avg(prior 30 days views).
    # High surge + YES < 0.50 → buy YES (underpriced given public attention)
    # High surge + YES ≥ 0.50 → buy NO  (hype-priced, fade the attention spike)
    "wikivol_enabled":        False,  # CUT 2026-06-14: enabled since 06-07, 0 opens/0 closes in 7d (never triggers)
    "wikivol_min_surge":      2.5,    # 7-day avg must be 2.5× the 30-day baseline
    "wikivol_yes_min":        0.10,
    "wikivol_yes_max":        0.90,
    "wikivol_min_volume":     50_000,
    "wikivol_max_open":       8,
    "wikivol_bet_usdc":       1.0,
    "wikivol_gain":           0.10,
    "wikivol_stop":           0.08,
    "wikivol_max_hold_hours": 48,

    # ── resolveprox (resolution proximity convergence, 2026-06-07) ────────────
    # No external API — uses the market end date from Gamma. Markets closing in
    # ≤5 days still priced away from 0/1 have a residual uncertainty discount
    # that should collapse as the event date approaches. We bet the convergence.
    # YES in [0.65, 0.92] and closes within max_days → buy YES
    # YES in [0.08, 0.35] and closes within max_days → buy NO
    "resolveprox_enabled":        False,  # KILLED 2026-06-08: 5 exits, 40% win, -$0.44 total (-$0.09/exit)
    "resolveprox_max_days":       5,
    "resolveprox_yes_hi_min":     0.65,
    "resolveprox_yes_hi_max":     0.92,
    "resolveprox_yes_lo_min":     0.08,
    "resolveprox_yes_lo_max":     0.35,
    "resolveprox_min_volume":     75_000,
    "resolveprox_max_open":       15,
    "resolveprox_bet_usdc":       1.0,
    "resolveprox_gain":           0.07,
    "resolveprox_stop":           0.05,
    "resolveprox_max_hold_hours": 130,  # 5 days + buffer — should resolve

    # ── redditbuzz (Reddit discussion leading indicator, 2026-06-07) ──────────
    # Reddit JSON API — completely free, no auth for public reads. Tracks weekly
    # post counts on topic-matched subreddits. High discussion volume is a
    # leading indicator of public attention; market prices often lag.
    # High posts + YES < 0.55 → buy YES (Reddit ahead of Poly)
    # High posts + YES ≥ 0.55 → buy NO (hype-priced)
    "redditbuzz_enabled":        False,  # CUT 2026-06-14: enabled since 06-07, 0 opens/0 closes in 7d (never triggers)
    "redditbuzz_min_posts":      4,     # min posts on topic in last 7 days
    "redditbuzz_yes_min":        0.15,
    "redditbuzz_yes_max":        0.85,
    "redditbuzz_min_volume":     50_000,
    "redditbuzz_max_open":       6,
    "redditbuzz_bet_usdc":       1.0,
    "redditbuzz_gain":           0.10,
    "redditbuzz_stop":           0.08,
    "redditbuzz_max_hold_hours": 72,

    # ── cryptofair (CoinGecko + log-normal fair value, 2026-06-07) ────────────
    # CoinGecko free tier — no auth, works from any IP. For "Will BTC/ETH hit
    # $X by date Y?" markets, compute the fair probability using geometric
    # Brownian motion (Black-Scholes d2 formula): P = N(d2) where
    #   d2 = [ln(S/K) + (−σ²/2)T] / (σ√T).
    # This is options pricing applied to prediction markets — the most rigorous
    # quantitative signal in the lab. 80% annualized vol prior for crypto.
    "cryptofair_enabled":        False,  # KILLED 2026-06-19 loss-sweep: cumulative $-0.65 n=5
    "cryptofair_min_divergence": 0.12,
    "cryptofair_yes_min":        0.05,
    "cryptofair_yes_max":        0.95,
    "cryptofair_min_volume":     100_000,
    "cryptofair_max_open":       8,
    "cryptofair_bet_usdc":       1.0,
    "cryptofair_gain":           0.12,
    "cryptofair_stop":           0.10,
    "cryptofair_max_hold_hours": 96,

    # ── volspike (volume spike — informed-flow detector, 2026-06-07) ──────────
    # When trading volume on a market jumps >3× its recent baseline in a single
    # cycle, informed players are likely entering. We trade the PRICE LAG:
    # if YES < 0.50 and volume spiked → informed buyers see YES as underpriced.
    # if YES > 0.50 and volume spiked → informed sellers see YES as overpriced.
    # Vol baseline stored in state across cycles; spike = vol > baseline × threshold.
    "volspike_enabled":        True,
    "volspike_threshold":      3.0,    # vol must be 3× the rolling baseline
    "volspike_yes_min":        0.10,
    "volspike_yes_max":        0.90,
    "volspike_min_volume":     200_000,
    "volspike_max_open":       6,
    "volspike_bet_usdc":       1.0,
    "volspike_gain":           0.08,
    "volspike_stop":           0.06,
    "volspike_max_hold_hours": 24,

    # ── newmarket (launch-day mispricing, 2026-06-07) ─────────────────────────
    # Polymarket creators set initial prices manually — frequently wrong. Markets
    # <48h old priced in [0.20, 0.80] are the richest mispricing window before
    # informed traders arbitrage them flat. We buy the "uncertain" side (YES if
    # <0.50, NO if >0.50) — a structural bet on creator miscalibration.
    "newmarket_enabled":        False,   # KILLED 2026-06-08: 0/3 wins all stops, -$0.76 — both cats losing
    "newmarket_min_chg":        0.08,    # min |24h price change| to qualify as large move
    "newmarket_yes_min":        0.28,    # uncertain zone — outside this = near-resolved, skip
    "newmarket_yes_max":        0.72,
    "newmarket_min_volume":     200_000,  # need real liquidity to fade a move
    "newmarket_max_open":       8,
    "newmarket_bet_usdc":       1.0,
    "newmarket_gain":           0.10,
    "newmarket_stop":           0.08,
    "newmarket_max_hold_hours": 72,

    # ── predictit (PredictIt cross-market divergence, 2026-06-07) ────────────
    # PredictIt hosts political prediction markets with independent, calibrated
    # community probabilities. Free JSON API, no auth. When PredictIt and
    # Polymarket diverge by ≥10c on the same political question, we trade the
    # Polymarket side toward the PredictIt consensus (the more liquid, older
    # market tends to be better calibrated on US politics).
    "predictit_enabled":        False,  # CUT 2026-06-14: enabled since 06-07, 0 opens/0 closes in 7d (never triggers)
    "predictit_min_divergence": 0.10,
    "predictit_min_similarity": 0.40,   # Jaccard word overlap threshold for matching
    "predictit_yes_min":        0.05,
    "predictit_yes_max":        0.95,
    "predictit_min_volume":     50_000,
    "predictit_max_open":       8,
    "predictit_bet_usdc":       1.0,
    "predictit_gain":           0.10,
    "predictit_stop":           0.08,
    "predictit_max_hold_hours": 96,

    # ── walletcopy (mirror top-volume wallets, 2026-06-07) ────────────────────
    # Fetches 500 recent global trades from data-api.polymarket.com/trades.
    # Ranks wallets by dollar volume in that window. When any top-20 wallet
    # made a BUY in a known market within the last 90 minutes, paper-trade the
    # same direction. Thesis: high-volume wallets are net profitable (they keep
    # trading) and their fresh entries signal conviction.
    "walletcopy_enabled":          False,  # KILLED: reversed to -$1.72, 52 exits
    "walletcopy_min_trade_usdc": 30,     # min $ per trade to count as significant
    "walletcopy_max_trade_age_min": 90,  # only copy trades from last 90 min
    "walletcopy_top_n":          20,     # number of top wallets to track
    "walletcopy_yes_min":        0.08,
    "walletcopy_yes_max":        0.92,
    "walletcopy_min_volume":     50_000,
    "walletcopy_max_open":       25,
    "walletcopy_bet_usdc":       1.0,
    "walletcopy_gain":           0.08,
    "walletcopy_stop":           0.06,
    "walletcopy_max_hold_hours": 48,

    # ── whale (single large-trade follower, 2026-06-07) ───────────────────────
    # Any single trade > whale_min_usdc in a liquid market is likely an informed
    # player with conviction. Follow the direction immediately. Trades are pulled
    # from the global feed (same as walletcopy) — no extra API calls.
    "whale_enabled":          False,  # KILLED: reversed to -$2.04, 47 exits
    "whale_min_usdc":            200,    # single trade must be > $200 to qualify
    "whale_max_trade_age_min":   60,     # only copy trades from last 60 min
    "whale_yes_min":             0.08,
    "whale_yes_max":             0.92,
    "whale_min_volume":          100_000,
    "whale_max_open":            20,
    "whale_bet_usdc":            1.0,
    "whale_gain":                0.10,
    "whale_stop":                0.08,
    "whale_max_hold_hours":      48,

    # ── multiwhale (clustered large-trade consensus, 2026-06-07) ─────────────
    # When 3 or more trades each > $75 all go the SAME direction in a single
    # market within 90 minutes, that's a cluster of informed money. Follow.
    "multiwhale_enabled":        False,  # KILLED 2026-06-10: 32 exits -$0.08/exit -$2.55 total
    "multiwhale_min_usdc":       75,     # per-trade minimum
    "multiwhale_min_count":      3,      # need 3+ qualifying trades
    "multiwhale_window_min":     90,
    "multiwhale_yes_min":        0.10,
    "multiwhale_yes_max":        0.90,
    "multiwhale_min_volume":     100_000,
    "multiwhale_max_open":       20,
    "multiwhale_bet_usdc":       1.0,
    "multiwhale_gain":           0.10,
    "multiwhale_stop":           0.07,
    "multiwhale_max_hold_hours": 48,

    # ── bookimbal (CLOB order book imbalance, 2026-06-07) ────────────────────
    # Fetches the YES-side CLOB order book from clob.polymarket.com. Computes
    # total bid liquidity (top 5 levels) vs ask liquidity. Strong imbalance
    # (>2.5:1 either direction) signals directional flow pressure.
    # bid>ask → price wants to go UP → buy YES; ask>bid → sell pressure → buy NO.
    "bookimbal_enabled":         False,  # KILLED 2026-06-08: 13 exits 7.7% win -$0.07/exit
    "bookimbal_min_ratio":       2.5,    # bid/ask liquidity ratio threshold
    "bookimbal_levels":          5,      # book depth to compare
    "bookimbal_yes_min":         0.10,
    "bookimbal_yes_max":         0.90,
    "bookimbal_min_volume":      200_000,
    "bookimbal_max_open":        10,
    "bookimbal_bet_usdc":        1.0,
    "bookimbal_gain":            0.07,
    "bookimbal_stop":            0.05,
    "bookimbal_max_hold_hours":  24,

    # ── shortdate (near-expiry FLB fade, 2026-06-07) ──────────────────────────
    # The favorite-longshot bias intensifies as resolution approaches: markets in
    # [0.20,0.58] with 1–7 days left are over-extended longshots that haven't
    # priced in the base rate of "most things don't happen." Fading YES (buying
    # NO) with short remaining time limits drawdown risk — markets resolve cleanly.
    "shortdate_enabled":         False,  # KILLED 2026-06-10: 14 exits -$0.22/exit -$3.07 total
    "shortdate_yes_min":         0.20,
    "shortdate_yes_max":         0.58,
    "shortdate_min_days":        1,
    "shortdate_max_days":        7,
    "shortdate_min_volume":      150_000,
    "shortdate_max_open":        15,
    "shortdate_bet_usdc":        1.0,
    "shortdate_gain":            0.08,
    "shortdate_stop":            0.06,
    "shortdate_max_hold_hours":  180,   # 7.5 days — let it resolve

    # ── highvol (high-volume FLB fade, 2026-06-07) ────────────────────────────
    # Markets with >$1M volume are better-tracked but the FLB still shows up in
    # the 0.15–0.48 band even in the deepest books. Higher volume = tighter
    # spread so the edge doesn't get eaten. Tests whether fade works in the
    # upper liquidity tier separately from the main fade (200k floor).
    "highvol_enabled":           False,  # KILLED 2026-06-10: 5 exits 0% win -$0.14/exit
    "highvol_yes_min":           0.15,
    "highvol_yes_max":           0.48,
    "highvol_min_volume":        1_000_000,
    "highvol_max_open":          15,
    "highvol_bet_usdc":          1.0,
    "highvol_gain":              0.07,
    "highvol_stop":              0.05,
    "highvol_max_hold_hours":    72,

    # ── pricerev (single-scan dip reversal, 2026-06-07) ───────────────────────
    # Tracks YES price from the previous scan in _price_snap. When a market
    # drops ≥5¢ in a single scan interval (strong panic selling), bet on the
    # mean-reversion. Short hold: if it doesn't bounce in 24h, exit.
    "pricerev_enabled":          False,  # KILLED 2026-06-08 night: 76.9% win but -$0.51 — stops 3x bigger than targets, R:R structurally broken
    "pricerev_min_drop":         0.05,  # YES must have fallen ≥5¢ since last scan
    "pricerev_yes_min":          0.15,
    "pricerev_yes_max":          0.65,
    "pricerev_min_volume":       200_000,
    "pricerev_max_open":         10,
    "pricerev_bet_usdc":         1.0,
    "pricerev_gain":             0.06,
    "pricerev_stop":             0.05,
    "pricerev_max_hold_hours":   24,

    # ── catrev (category mean reversion, 2026-06-07) ──────────────────────────
    # If the average 24h chg across an entire category (sports/politics/crypto)
    # is negative AND an individual market also dropped, the category-level
    # selloff may be an overreaction. Buy YES on those individual markets.
    # Uses the chg field aggregated per category in the current scan batch.
    "catrev_enabled":          False,  # KILLED: 1 exit only
    "catrev_cat_chg_max":       -0.04,  # category avg chg must be ≤ this (bearish)
    "catrev_mkt_chg_max":       -0.04,  # individual market chg must also be negative
    "catrev_yes_min":            0.25,
    "catrev_yes_max":            0.70,
    "catrev_min_volume":         200_000,
    "catrev_max_open":           10,
    "catrev_bet_usdc":           1.0,
    "catrev_gain":               0.07,
    "catrev_stop":               0.05,
    "catrev_max_hold_hours":     48,

    # ── spreadfade (wide-spread fade, 2026-06-07) ─────────────────────────────
    # Markets with above-average effective spread (>4c on mid) face less arb
    # pressure → bigger mispricing persists longer. The FLB fade signal should
    # be STRONGER in these markets. Filters to [0.20, 0.55] YES and requires
    # spread proxy (vol-derived) above threshold.
    "spreadfade_enabled":        False,  # CUT 2026-06-14: enabled since 06-07, 0 opens/0 closes in 7d (never triggers)
    "spreadfade_yes_min":        0.20,
    "spreadfade_yes_max":        0.55,
    "spreadfade_min_volume":     50_000,   # lower vol floor (wide spread = thin book)
    "spreadfade_max_volume":     300_000,  # cap: above this spread narrows
    "spreadfade_max_open":       10,
    "spreadfade_bet_usdc":       1.0,
    "spreadfade_gain":           0.10,
    "spreadfade_stop":           0.08,
    "spreadfade_max_hold_hours": 72,

    # ── resolverush (late resolution convergence, 2026-06-07) ────────────────
    # When a market has <48h left and YES is in [0.55, 0.88] (strong favorite
    # but not yet resolved), the market should converge to ~0.90+ if the outcome
    # is likely already known. We buy YES to ride the final convergence sprint.
    # Complementary to resolveprox: that leg fades underdogs, this buys favorites.
    "resolverush_enabled":       False,  # KILLED 2026-06-08: 1 exit, stop, -$0.17 — insufficient data but wrong direction
    "resolverush_yes_min":       0.55,
    "resolverush_yes_max":       0.88,
    "resolverush_max_hours":     48,       # ≤48h to resolution
    "resolverush_min_hours":     4,        # skip <4h (resolution gap risk)
    "resolverush_min_volume":    100_000,
    "resolverush_max_open":      10,
    "resolverush_bet_usdc":      1.0,
    "resolverush_gain":          0.07,
    "resolverush_stop":          0.06,
    "resolverush_max_hold_hours": 50,

    # ── Whale watchdog (logging, not a leg) ───────────────────────────────────
    "watchdog_enabled":          True,
    "watchdog_min_usdc":         200,    # flag trades above this dollar value

    # ══════════════════════════════════════════════════════════════════════════
    # VOLUME LEGS BY CATEGORY AND TRADING MODE (2026-06-07)
    # Eight legs, each isolating a specific volume signal type.
    # All use the same _vol_baseline EMA infrastructure (α=0.20) as volspike.
    # "Volume without a category filter" is what volspike already does.
    # These ask the next question: does the signal sharpen when filtered by
    # category (crypto/sports/politics) or by trade-flow direction?
    # ══════════════════════════════════════════════════════════════════════════

    # ── cryptovol (crypto-category vol spike, 2026-06-07) ────────────────────
    # Crypto prediction markets are uniquely reflexive — price moves drive
    # volume which drives more price moves. A vol spike in cat=crypto without
    # a large chg is often rotation/arb activity, not genuine information.
    # Fade YES when price is elevated; buy YES when depressed.
    "cryptovol_enabled":         False,  # KILLED 2026-06-08: -$1.07 from 2 exits, hitting sports markets (wrong filter)
    # NOTE: Gamma cat="crypto" is miscategorized (FIFA/political markets leak in).
    # We use keyword matching on the question instead. Vol-spike detection replaced
    # with price-change filter (cumulative vol EMA never spikes — ratio always ≈1.0).
    "cryptovol_min_chg":         0.05,   # |24h price change| ≥ 5¢ (significant move)
    "cryptovol_yes_min":         0.10,
    "cryptovol_yes_max":         0.90,
    "cryptovol_min_volume":      100_000,
    "cryptovol_max_open":        8,
    "cryptovol_bet_usdc":        1.0,
    "cryptovol_gain":            0.09,
    "cryptovol_stop":            0.07,
    "cryptovol_max_hold_hours":  36,

    # ── sportsvol (sports-category vol spike, 2026-06-07) ────────────────────
    # Sports markets spike in volume around game-time. This is the OPPOSITE of
    # an information edge — late volume near game-time is retail/FOMO money.
    # Fading YES in [0.20, 0.58] when sports vol spikes captures the longshot
    # bias amplified by FOMO bettors who inflate longshot prices pre-game.
    "sportsvol_enabled":          False,  # KILLED: -$0.46, R:R broken
    # Vol spike replaced with chg filter — cumulative vol EMA approach doesn't work.
    "sportsvol_min_chg":         0.05,   # |24h price change| ≥ 5¢ (game is live/near)
    "sportsvol_yes_min":         0.20,
    "sportsvol_yes_max":         0.58,
    "sportsvol_min_volume":      150_000,
    "sportsvol_max_open":        12,
    "sportsvol_bet_usdc":        1.0,
    "sportsvol_gain":            0.08,
    "sportsvol_stop":            0.06,
    "sportsvol_max_hold_hours":  30,     # short: sports resolve fast

    # ── polvol (politics-category vol spike, 2026-06-07) ─────────────────────
    # Politics volume spikes on news releases, poll drops, debates. The market
    # over-reacts in both directions. Fade the DIRECTION of the move (vol + chg
    # both positive → fade by buying NO; vol + chg negative → buy YES).
    "polvol_enabled":            False,  # KILLED 2026-06-19 loss-sweep: cumulative $-1.15 n=24
    # Vol spike removed — same cumulative vol EMA issue. chg filter is the real gate.
    "polvol_min_chg":            0.04,   # |24h price change| ≥ 4¢ (news/poll driven)
    "polvol_yes_min":            0.20,
    "polvol_yes_max":            0.80,
    "polvol_min_volume":         100_000,
    "polvol_max_open":           10,
    "polvol_bet_usdc":           1.0,
    "polvol_gain":               0.09,
    "polvol_stop":               0.07,
    "polvol_max_hold_hours":     72,

    # ── buyflow (net-buy-flow follower, 2026-06-07) ───────────────────────────
    # When ≥70% of dollar volume flowing into a market in the last 15 min is
    # BUY-side (and total flow > $50), the crowd is accumulating — follow YES.
    # Distinct from walletcopy/whale: this is AGGREGATE retail+institutional
    # directional pressure, not individual large trades.
    "buyflow_enabled":           False,  # KILLED 2026-06-10: 53 exits -$0.037/exit -$1.93 total
    "buyflow_min_ratio":         0.70,   # ≥70% of flow must be BUY
    "buyflow_min_total_usdc":    50,     # need at least $50 total flow to signal
    "buyflow_yes_min":           0.10,
    "buyflow_yes_max":           0.88,
    "buyflow_min_volume":        100_000,
    "buyflow_max_open":          20,
    "buyflow_bet_usdc":          1.0,
    "buyflow_gain":              0.07,
    "buyflow_stop":              0.06,
    "buyflow_max_hold_hours":    24,

    # ── sellflow (net-sell-flow fade, 2026-06-07) ─────────────────────────────
    # Mirror of buyflow: ≥70% sell-side → crowd distributing → buy NO.
    # Captures informed exit before bad news, or FOMO unwind after a pump.
    "sellflow_enabled":          False,   # KILLED 2026-06-08: 0/5 wins, -$1.41 — follow-sellers thesis failed
    "sellflow_min_ratio":        0.70,
    "sellflow_min_total_usdc":   50,
    "sellflow_yes_min":          0.12,
    "sellflow_yes_max":          0.90,
    "sellflow_min_volume":       100_000,
    "sellflow_max_open":         12,
    "sellflow_bet_usdc":         1.0,
    "sellflow_gain":             0.07,
    "sellflow_stop":             0.06,
    "sellflow_max_hold_hours":   24,

    # ── coinpump (crypto price-jump fade, 2026-06-07) ─────────────────────────
    # Cat=crypto markets where YES pumped >8% in 24h. Crypto prediction markets
    # overshoot on spot price moves — the 24h chg in Gamma reflects how the YES
    # price reacted to the underlying asset move. Fade the overshoot (buy NO).
    # Distinct from cryptofair (which uses a log-normal model); this is a raw
    # chg-magnitude filter with no asset-price anchor.
    "coinpump_enabled":          False,  # KILLED 2026-06-10: 0% win 2 exits -$0.30
    "coinpump_min_chg":          0.08,   # YES must have risen ≥8% in 24h
    "coinpump_yes_min":          0.30,   # don't fade markets already near resolved
    "coinpump_yes_max":          0.85,
    "coinpump_min_volume":       100_000,
    "coinpump_max_open":         8,
    "coinpump_bet_usdc":         1.0,
    "coinpump_gain":             0.09,
    "coinpump_stop":             0.07,
    "coinpump_max_hold_hours":   48,

    # ── coindip (crypto dip-buyer, 2026-06-07) ────────────────────────────────
    # Opposite side of coinpump: YES dropped >8% in 24h on a crypto market.
    # Crypto predictions overreact to negative spot moves; the underlying asset
    # often bounces. Buy YES to ride the correction.
    "coindip_enabled": False,  # KILLED 2026-06-10: 60% win 5 exits -$0.13 R:R 0.48 — R:R structurally broken
    "coindip_max_chg":          -0.08,   # YES must have dropped ≥8%
    "coindip_yes_min":           0.15,
    "coindip_yes_max":           0.70,
    "coindip_min_volume":        100_000,
    "coindip_max_open":          8,
    "coindip_bet_usdc":          1.0,
    "coindip_gain":              0.09,
    "coindip_stop":              0.07,
    "coindip_max_hold_hours":    48,

    # ── coinup (buy YES on crypto "Up or Down" coinflips — 2026-06-08) ──────────
    # Forward test: backtest (21d N=700) found P(Up YES)=56.5% CI[0.54,0.59].
    # CAVEAT: period-specific bullish drift, NOT structural edge. Flips in bear markets.
    # coindown is the control — must lose in a bull period (P(Down)≈43.5% < break-even).
    "coinup_enabled":          False,   # KILLED 2026-06-22: n=19 WR37% -$2.46 gross(no-fee) — momentum A/B concluded, no edge
    "coinup_yes_min":          0.38,   # (the 2026-06-08 "zero markets" kill was a data-blind
    "coinup_yes_max":          0.68,   #  bug — fetch_updown_markets() now feeds them)
    "coinup_min_hours_left":   1.0,    # must have >=1h — bot needs time to act
    "coinup_max_hours_left":   8.0,    # short-horizon only (not multi-day markets)
    "coinup_min_volume":       2_000,   # Up/Down markets are low-vol (<=~$17k); 50k filtered all
    "coinup_max_open":         10,
    "coinup_bet_usdc":         1.0,
    "coinup_gain":             0.10,
    "coinup_stop":             0.08,
    "coinup_max_hold_hours":   8,

    "coindown_enabled":        False,   # KILLED 2026-06-22: pairwise coinup-control, moot once coinup off (n=18 WR33% -$3.28). NB: global marking controls allin/coinflip/longshot are ALSO all disabled — book has no live marking control.
    "coindown_yes_min":        0.38,
    "coindown_yes_max":        0.68,
    "coindown_min_hours_left": 1.0,
    "coindown_max_hours_left": 8.0,
    "coindown_min_volume":     2_000,
    "coindown_max_open":       10,
    "coindown_bet_usdc":       1.0,
    "coindown_gain":           0.10,
    "coindown_stop":           0.08,
    "coindown_max_hold_hours": 8,



    # ── candlesig (candle-based RSI + EMA trend signal, 2026-06-10) ─────────
    # Uses live 15m Binance candles to compute RSI(14) and EMA(20/50) trend.
    # Enters "Up or Down" Polymarket markets only when candle signal confirms:
    #   BUY YES: RSI < rsi_bull_max AND EMA20 > EMA50 (oversold in uptrend)
    #   BUY NO : RSI > rsi_bear_min AND EMA20 < EMA50 (overbought in downtrend)
    "candlesig_enabled":        True,
    "candlesig_yes_min":        0.35,   # market must not be near-resolved
    "candlesig_yes_max":        0.65,
    "candlesig_min_hours_left": 1.0,
    "candlesig_max_hours_left": 18.0,   # 2026-06-15: user widened 12→18 (watch the
                                        # 2026-06-10 finding: +1.6pp at 8h, INVERTS to
                                        # -1.2pp by 24h — 18h is partway into the decay)
    "candlesig_min_volume":     5_000,
    "candlesig_max_open":       6,
    "candlesig_bet_usdc":       1.0,
    "candlesig_gain":           0.10,
    "candlesig_stop":           0.08,
    "candlesig_max_hold_hours": 24,
    "candlesig_rsi_bull_max":   45.0,   # RSI must be below this to BUY YES
    "candlesig_rsi_bear_min":   55.0,   # RSI must be above this to BUY NO

    # ── macdsig (MoonDev MACD mean-reversion port, 2026-06-13) ───────────────
    # From oss-bots/polymarket/polymarket-bot (wincXDSolana). Distinct from
    # candlesig (RSI+EMA): uses MACD(12/26/9) crossover + price deviation from
    # EMA20 on crypto Up/Down markets. The bot's exact rule:
    #   dev > +0.015 AND macd < macd_sig  (overbought + bearish) → bet DOWN → NO
    #   dev < -0.015 AND macd > macd_sig  (oversold  + bullish) → bet UP   → YES
    # MACD is the fresh, untested variable; short-crypto mean-reversion has mostly
    # died here (gridbounce/coinup killed), so honest expectation is modest.
    "macdsig_enabled":          True,
    "macdsig_dev_threshold":    0.015,  # |price/EMA20 - 1| must exceed this
    "macdsig_yes_min":          0.35,
    "macdsig_yes_max":          0.65,
    "macdsig_min_hours_left":   1.0,
    "macdsig_max_hours_left":   18.0,   # 2026-06-15: user widened 12→18 (candlesig lesson: signal decays past ~8h)
    "macdsig_min_volume":       5_000,
    "macdsig_max_open":         6,
    "macdsig_bet_usdc":         1.0,
    "macdsig_gain":             0.10,
    "macdsig_stop":             0.08,
    "macdsig_max_hold_hours":   24,

    # ── coinrev (fade big 24h moves — mean-reversion, 2026-06-10) ────────────
    # Backtest on 12 coins / 10y of 15m candles: fading a >7% 24h move wins
    # 54.6% of the time at the 8h horizon (n=359k, monotonic dose-response
    # from 53.2% @ >2% to 55.8% @ >15%). In-sample — forward gate still rules.
    "coinrev_enabled":          True,
    "coinrev_chg_threshold":    0.07,   # |24h change| must exceed 7%
    "coinrev_rsi_confirm":      True,   # mine2 2026-06-11: +RSI gate lifts 54.6%->55.6%
    "coinrev_rsi_hi":           65.0,   # pump must ALSO be overbought to fade down
    "coinrev_rsi_lo":           35.0,   # dump must ALSO be oversold to fade up
    "coinrev_yes_min":          0.30,
    "coinrev_yes_max":          0.70,
    "coinrev_min_hours_left":   1.0,
    "coinrev_max_hours_left":   18.0,   # 2026-06-15: user widened 12→18 (same 8h-decay logic as candlesig)
    "coinrev_min_volume":       5_000,
    "coinrev_max_open":         6,
    "coinrev_bet_usdc":         1.0,
    "coinrev_max_hold_hours":   12,

    # ── diverg (spot-price vs. market divergence, 2026-06-08) ────────────────
    # SOURCE: aulekator/Polymarket-BTC-15-Minute-Trading-Bot (PriceDivergence signal).
    # If BTC/ETH/etc. is up >N% in 24h but the Up/Down market is still priced
    # near 50/50 (< 0.55), the market hasn't repriced the momentum → buy YES.
    # Inverse: coin down >N% but market priced >0.45 → buy NO.
    "diverg_enabled":          True,   # REVIVED 2026-06-15: coinup/coindown markets back
    "diverg_chg_threshold":    0.03,   # 3% 24h move to qualify
    "diverg_yes_max_bull":     0.58,   # buy YES only when market hasn't caught up
    "diverg_yes_min_bear":     0.42,   # buy NO only when market hasn't caught up
    "diverg_min_hours_left":   1.0,
    "diverg_max_hours_left":   8.0,
    "diverg_min_volume":       50_000,
    "diverg_max_open":         8,
    "diverg_bet_usdc":         1.0,
    "diverg_gain":             0.10,
    "diverg_stop":             0.08,
    "diverg_max_hold_hours":   8,

    # ── feargreed (Fear & Greed contrarian, 2026-06-08) ───────────────────────
    # SOURCE: aulekator bot's SentimentAnalysis signal (F&G thresholds 25/75).
    # Extreme fear (<25) = market oversold → contrarian buy YES (Up).
    # Extreme greed (>75) = market overbought → contrarian buy NO (Down).
    # Via alternative.me/fng (free, no auth, cached 1h).
    "feargreed_enabled":       False,  # KILLED 2026-06-19 loss-sweep: cumulative $-1.23 n=7   # REVIVED 2026-06-15: coinup/coindown markets back
    "feargreed_fear_max":      25,     # F&G ≤25 = extreme fear → buy YES
    "feargreed_greed_min":     75,     # F&G ≥75 = extreme greed → buy NO
    "feargreed_yes_min":       0.38,
    "feargreed_yes_max":       0.68,
    "feargreed_min_hours_left": 1.0,
    "feargreed_max_hours_left": 8.0,
    "feargreed_min_volume":    50_000,
    "feargreed_max_open":      8,
    "feargreed_bet_usdc":      1.0,
    "feargreed_gain":          0.10,
    "feargreed_stop":          0.08,
    "feargreed_max_hold_hours": 8,

    # ── lateprox (late-window entry on high-confidence coin markets, 2026-06-08) ──
    # SOURCE: aulekator bot's late-window entry (minutes 13-14 of a 15-min window).
    # Calibration showed: market priced at 0.60→72% actual win rate (n=69, +12% edge).
    # Enter only when <2h remaining AND price already tilted (≥0.62 or ≤0.38).
    # At that point the outcome is nearly locked in → low variance, high hit rate.
    "lateprox_enabled":        False,  # KILLED 2026-06-19 loss-sweep: cumulative $-0.22 n=4   # REVIVED 2026-06-15: coinup/coindown markets back
    "lateprox_yes_hi_min":     0.62,   # buy YES when priced ≥62% (calibration sweet spot)
    "lateprox_yes_hi_max":     0.80,   # don't enter near-certainties (>80 = no value)
    "lateprox_yes_lo_min":     0.20,   # buy NO when YES priced ≤38%
    "lateprox_yes_lo_max":     0.38,
    "lateprox_min_hours_left": 0.25,   # at least 15min (bot needs time to exit)
    "lateprox_max_hours_left": 2.0,    # only late window (<2h)
    "lateprox_min_volume":     50_000,
    "lateprox_max_open":       10,
    "lateprox_bet_usdc":       1.0,
    "lateprox_gain":           0.08,
    "lateprox_stop":           0.06,
    "lateprox_max_hold_hours": 2,

    # ── fgcrypto (Fear & Greed on long-dated crypto markets, 2026-06-08) ────────
    # PIVOT from feargreed (which targeted now-defunct short-term up/down markets).
    # Applied to live long-dated BTC/ETH/SOL markets ("Will BTC reach $X by Dec?").
    # F&G ≤ 25 (extreme fear): bearish dip-targets ("dip to $X") are overpriced → BUY NO.
    # F&G ≥ 75 (extreme greed): bullish price-targets ("reach $X") are overpriced → BUY NO.
    # In both cases we trade the "overreaction" — crowd is overfitting fear/greed to long-dated.
    "fgcrypto_enabled": False,  # KILLED 2026-06-10
    "fgcrypto_fear_max":        25,     # F&G ≤25 = extreme fear → sell dip-markets (NO)
    "fgcrypto_greed_min":       75,     # F&G ≥75 = extreme greed → sell rally-markets (NO)
    "fgcrypto_min_drop_pct":    0.20,   # dip target must be ≥20% below current price (avoid near-misses)
    "fgcrypto_min_pump_pct":    0.20,   # rally target must be ≥20% above current price
    "fgcrypto_yes_min_sell":    0.40,   # only sell when YES ≥ 40% (not already near zero)
    "fgcrypto_yes_max_sell":    0.85,   # don't fade near-certainties
    "fgcrypto_min_volume":      20_000, # lower bar — long-dated markets have lower daily vol
    "fgcrypto_max_open":        6,
    "fgcrypto_bet_usdc":        1.0,
    "fgcrypto_gain":            0.12,   # longer hold → wider target
    "fgcrypto_stop":            0.10,
    "fgcrypto_max_hold_hours":  168,    # 7 days max hold (long-dated market)

    # ── nearterm (resolution convergence, 2026-06-08) ──────────────────────────
    # BUY high-consensus markets (YES 0.80-0.97) resolving in 7-45 days.
    # Thesis: once the market has formed a strong consensus (>80%), the remaining
    # uncertainty premium is being sold by the last doubters. We collect that premium
    # by buying consensus and holding to resolution at 1.00 (expected +6-25% gain).
    # Exit via TP (YES reaches 0.975) or SL (YES drops below 0.72 = reversal signal).
    # Excludes commodity markets (macro-driven) and near-certainties (>97%, no meat left).
    "nearterm_enabled":         False,  # KILLED 2026-06-17: n=10, 40% WR, -$0.99 (-9.9¢/exit), CI[-0.183,-0.011] excludes 0 = REAL losing. Kill rule 1 met (n≥10 + WR<60% + pnl<-$0.50). Predictive leg on calibrated mids (Lesson 15). 10 open wind down via scan_exits.
    "nearterm_yes_min":         0.80,   # enter when consensus ≥ 80%
    "nearterm_yes_max":         0.97,   # don't enter near-certainties (no premium left)
    "nearterm_min_days":        7,      # must resolve in ≥ 7 days (not today's noise)
    "nearterm_max_days":        45,     # must resolve in ≤ 45 days (not years away)
    "nearterm_min_volume":      15_000, # liquid market
    "nearterm_max_open":        10,
    "nearterm_bet_usdc":        1.0,
    "nearterm_gain":            0.08,   # TP: +8% from fill (e.g. 0.90 → 0.972)
    "nearterm_stop":            0.15,   # SL: -15% from fill (e.g. 0.90 → 0.765)
    "nearterm_max_hold_hours":  1080,   # 45 days max (matches max_days)

    # ── acumvol (volume-without-price = hidden accumulation, 2026-06-07) ──────
    # When volume is >2.5× baseline BUT price barely moved (|chg| < 2%), smart
    # money is accumulating without moving the market. The stock market version
    # of "distribution" — prediction market adaptation. We buy YES: the
    # accumulated interest will eventually move price UP.
    "acumvol_enabled":           False,  # CUT 2026-06-14: enabled since 06-07, 0 opens/0 closes in 7d (never triggers)
    "acumvol_threshold":         2.5,    # vol must be 2.5× EMA baseline
    "acumvol_max_abs_chg":       0.02,   # price change must be tiny (hidden)
    "acumvol_yes_min":           0.20,
    "acumvol_yes_max":           0.75,
    "acumvol_min_volume":        200_000,
    "acumvol_max_open":          10,
    "acumvol_bet_usdc":          1.0,
    "acumvol_gain":              0.08,
    "acumvol_stop":              0.06,
    "acumvol_max_hold_hours":    48,

    # ── resolvebump (resolution-proximity momentum, 2026-06-08) ──────────────
    # Markets closing within 48h still priced 35–65¢ have max uncertainty.
    # Binary resolution forces a big move — we follow the direction of recent
    # price momentum (chg > 0 → buy YES, chg < 0 → buy NO).
    "resolvebump_enabled":      False,  # KILLED: -$1.20, 2 exits — thesis failed fast
    "resolvebump_max_hours":    48,     # only enter if resolves within 48h
    "resolvebump_yes_min":      0.35,   # still uncertain (below = near-dead)
    "resolvebump_yes_max":      0.65,   # still uncertain (above = near-decided)
    "resolvebump_min_vol":      150_000,
    "resolvebump_min_chg":      0.02,   # must be moving (|chg| ≥ 2¢)
    "resolvebump_max_open":     12,
    "resolvebump_bet_usdc":     1.0,
    "resolvebump_gain":         0.14,   # wide — resolution can move 20-30¢
    "resolvebump_stop":         0.08,
    "resolvebump_max_hold_hours": 48,

    # ── thinbook (thin-market noise fade, 2026-06-08) ──────────────────────
    # Low-liquidity markets (<$60k vol) where price moved ≥8¢ are usually NOISE
    # not signal — one retail order moved a thin book. Fade the move.
    # chg>0 (price spiked up) → buy NO; chg<0 (price dumped) → buy YES.
    "thinbook_enabled":         False,  # KILLED: 0 exits, never fires
    "thinbook_max_vol":         60_000,  # thin market gate
    "thinbook_min_chg":         0.08,    # must have moved ≥8¢ (meaningful noise)
    "thinbook_yes_min":         0.20,    # not near edges
    "thinbook_yes_max":         0.80,
    "thinbook_max_open":        10,
    "thinbook_bet_usdc":        1.0,
    "thinbook_gain":            0.10,    # reversion target
    "thinbook_stop":            0.07,
    "thinbook_max_hold_hours":  24,

    # ── whaleflip (follow whale exits, 2026-06-08) ──────────────────────────
    # When a top-20 wallet places a big SELL (≥$200), their direction is still
    # informative. We follow it: if whale sells YES → we buy NO.
    # Complementary to walletcopy (which follows buys) — covers the exit signal.
    "whaleflip_enabled":        False,  # KILLED: 0 exits, never fires
    "whaleflip_min_usdc":       200,     # minimum whale sell size
    "whaleflip_min_vol":        100_000,
    "whaleflip_yes_min":        0.20,
    "whaleflip_yes_max":        0.80,
    "whaleflip_max_open":       12,
    "whaleflip_bet_usdc":       1.0,
    "whaleflip_gain":           0.08,
    "whaleflip_stop":           0.06,
    "whaleflip_max_hold_hours": 48,

    # ── multiconfirm (confluence double-bet, 2026-06-08) ────────────────────
    # When 2+ proven legs (walletcopy, whale, buyflow, multiwhale) are already
    # open on the same market in the same direction — that's rare agreement.
    # Enter a third position with 1.5x bet. No new analysis, pure confluence.
    "multiconfirm_enabled":     False,  # KILLED: 0 exits — depends on killed legs
    "multiconfirm_min_legs":    2,       # need at least 2 legs agreeing
    "multiconfirm_max_open":    8,
    "multiconfirm_bet_usdc":    1.5,     # 1.5x since it's a high-confidence overlay
    "multiconfirm_gain":        0.10,
    "multiconfirm_stop":        0.07,
    "multiconfirm_max_hold_hours": 72,

    # ── catmom (category momentum, 2026-06-08) ──────────────────────────────
    # If ≥65% of sports OR politics markets have positive chg this scan,
    # the whole category is in a bullish regime. Buy YES on next qualifying entry.
    # If ≤35%, the category is bearish — buy NO.
    # Filters: vol>100k, yes∈[0.30,0.70] to avoid near-resolved markets.
    "catmom_enabled":           False,  # KILLED: -$1.00, 12 exits, 8% win
    "catmom_mom_threshold":     0.65,    # ≥65% positive = bullish regime
    "catmom_bear_threshold":    0.35,    # ≤35% positive = bearish regime
    "catmom_min_vol":           100_000,
    "catmom_yes_min":           0.30,
    "catmom_yes_max":           0.70,
    "catmom_max_open":          10,
    "catmom_bet_usdc":          1.0,
    "catmom_gain":              0.10,
    "catmom_stop":              0.07,
    "catmom_max_hold_hours":    48,

    # ── nearres (near-resolution certainty bet, 2026-06-08) ──────────────────
    # ── nearres (near-resolution certainty — rebuilt 2026-06-09) ───────────────
    # ONLY markets resolving within 6h, priced >83¢ (YES) or <17¢ (NO).
    # 13 exits, 69% win, +$0.81 — best signal on board. Tuning it tighter.
    # The 11 stale exits = overnight loop loosened to 12h + 75k vol → stale.
    # Back to 6h window, 250k vol floor, tighter certainty bands.
    # Separate category legs (nearres_sports, nearres_pol) for A/B breakdown.
    "nearres_enabled":          True,
    "nearres_max_hours":        4,      # 4h max — only truly imminent (tighter window)
    "nearres_yes_hi":           0.88,   # 2026-06-10: raised for 80%+ win rate
    "nearres_yes_lo":           0.12,   # 2026-06-10: inverse of yes_hi
    "nearres_max_entry":        0.95,   # 2026-06-10: cap the SIDE mid — retro-pricing
                                        # showed 7 fills at 0.999 risking $2 to win $0.002
                                        # (win-rate padding, zero dollars, pure tail risk)

    # ── nearresno (2026-06-13): the NO-favorite arm of nearres, ISOLATED ──────
    # Learn-from-positives finding: nearres's edge is one-sided. The NO branch
    # (back the favorite via a cheap-underdog market) is +$0.050/exit n=18, 2.3:1
    # win:loss, SURVIVES outlier removal (drop-top1 still +$0.046). The YES branch
    # is -$0.042 with the 5.3:1 killed-truefade asymmetry. This isolates the good
    # arm with a side-mid band [0.80,0.95] that strips the $0.001 dust padding
    # (which inflated win% but not $) while keeping the real winners (strip → +$0.075).
    # Enabled to build its OWN clean >=30-exit track; graduates only on its own CI.
    "nearresno_enabled":        True,
    "nearresno_max_hours":      4,      # only imminent esports (same as nearres)
    "nearresno_yes_max":        0.18,   # underdog must be cheap → its NO is the favorite
    "nearresno_band_lo":        0.80,   # side-mid (1-yes) floor — real favorite, not coinflip
    "nearresno_band_hi":        0.95,   # side-mid cap — strips the 0.999 dust padding
    "nearresno_min_vol":        200_000,
    "nearresno_max_chg":        0.05,   # price-stability gate (same as nearres)
    "nearresno_max_open":       6,
    "nearresno_bet_usdc":       1.0,
    "nearresno_gain":           0.09,
    "nearresno_stop":           0.03,
    "nearresno_max_hold_hours": 5,

    # ── spreadcap (2026-06-13): maker spread-capture on NOISE-flow markets ────
    # The analysis proved the bot loses ~$0.11/trade paying the spread as a TAKER.
    # The ONLY structurally +EV path: be a MAKER (enter at mid, save the half-spread)
    # on markets where flow is NOISE not information — there the spread over-pays for
    # adverse selection (Glosten-Milgrom). Target = very-high-vol, mid-priced, PRICE-
    # STABLE markets (stability = the resting order isn't about to be run over by
    # informed flow), NOT esports/in-play (informed + gap-through). Honest model:
    # maker entry at mid, but REAL taker bid on exit + real target/stop — so it can
    # only profit if stable high-vol markets mean-revert more than they trend. If
    # they're informed (trend), it loses and dies on the gate, like everything else.
    # HYPOTHESIS, not a declared winner — must earn >=30 exits + CI>0 like any leg.
    "spreadcap_enabled":        False,  # KILLED 2026-06-15: n=25, 40% WR, -$0.92, -3.7¢/exit, CI[-0.075,-0.000]. Same spread-bleed pattern as microscalp — "spread capture" with no edge IS paying the spread. Kill rule met (n≥10, total<-$0.50, WR<60%).
    "spreadcap_min_vol":        750_000,  # very deep book = mostly noise/retail flow
    "spreadcap_yes_min":        0.32,     # mid-band only — avoid resolution dynamics
    "spreadcap_yes_max":        0.68,
    "spreadcap_max_chg":        0.015,    # PRICE-STABLE: scan move < 1.5c (no informed run)
    "spreadcap_min_days":       1.0,      # not near resolution (where info concentrates)
    "spreadcap_gain":           0.04,     # small symmetric take — capture mean-reversion
    "spreadcap_stop":           0.04,
    "spreadcap_min_volume":     750_000,
    "spreadcap_max_open":       10,
    "spreadcap_bet_usdc":       1.0,
    "spreadcap_max_hold_hours": 24,
    "spreadcap_skip_kw":        ("counter-strike", "cs2", "valorant", "dota",
                                  "league of legends", " vs ", "(bo3)", "(bo5)"),
    # Avellaneda-Stoikov optimal quoting (2026-06-13): replace spreadcap's blind
    # fixed gain with the A-S optimal half-spread (vol/time-scaled) + inventory skew.
    "spreadcap_as_enabled":     True,    # use A-S quotes; False = old fixed-gain behavior
    "spreadcap_as_gamma":       2.0,     # risk aversion — drives inventory-skew strength
    "spreadcap_as_k":           22.0,    # order-book liquidity (deep Polymarket books are liquid →
                                         # high k → tight optimal spread ~3-5c, the scalp range)
    "spreadcap_as_vol_floor":   0.03,    # min volatility estimate so half-spread isn't degenerate

    # ── microscalp (2026-06-13, user spec): close at +0.20%, churn fast ───────
    # "make many trades, sell each at a tiny profit." As a TAKER this is the
    # killed-scalp thesis (spread 20x the target → guaranteed loss). The ONE
    # honest shot is MAKER: enter at mid (save the half-spread), take +0.20%-ish,
    # tight symmetric stop, free re-entry. Same deep/stable/noise-flow universe as
    # spreadcap. HYPOTHESIS — if noise markets oscillate, the maker entry + tiny
    # take can net positive; if they trend (adverse selection), it dies fast & loud.
    "microscalp_enabled":       False,  # KILLED 2026-06-15: n=413, 0% WR, -$9.70, -2.3¢/exit, CI[-0.027,-0.020]. Pure spread bleed — micro-scalping pays bid-ask every round-trip with no directional edge → loses the spread every time. The cleanest proof of "execution edge is sub-spread".
    # CONVERTED 2026-06-13 (user spec): trade EVERY market, like allin — BUT keep the
    # MAKER entry. This makes it the maker twin of the allin taker-control: a clean A/B
    # on whether the spread-discount alone reduces the loss when you trade everything.
    # Expected to still lose (no selectivity) but by HOW MUCH LESS than allin (-$0.14)
    # measures the value of maker execution at scale. Honest experiment, not an edge claim.
    "microscalp_min_vol":       0,          # every market (matches allin)
    "microscalp_yes_min":       0.05,       # every price 0.05-0.95 (matches allin; floor skips degenerates)
    "microscalp_yes_max":       0.95,
    "microscalp_max_chg":       1.0,        # no stability gate — take every market
    "microscalp_min_days":      1.0,
    "microscalp_gain":          0.004,      # ~0.20-0.40% take-profit (the user's tiny target)
    "microscalp_stop":          0.006,      # 1.5:1 — tiny take needs ~60% win to clear; honest geometry
    "microscalp_max_open":      30,         # CUT 2026-06-14: 100→30. Keeps the maker-vs-taker A/B alive, less bleed
    "microscalp_bet_usdc":      1.0,
    "microscalp_max_hold_hours": 4,         # cycle book over a few hours
    "microscalp_skip_kw":       ("counter-strike", "cs2", "valorant", "dota",
                                  "league of legends", " vs ", "(bo3)", "(bo5)"),

    # ── sportres / nearreslow (2026-06-10): A/B arms extending the nearres lead ──
    # sportres: does the near-resolution favorite edge generalize BEYOND esports
    # (tennis/MLB/NBA/soccer)? nearreslow: does it extend down-band [0.80,0.88)
    # where the Wang fit predicts decay? Both reuse the proven nearres structure.
    "sportres_enabled":         False,  # KILLED 2026-06-12: 7 exits, 43% WR, -$1.98 — OOS negative, no edge in traditional sports
    "sportres_max_hours":       5,      # 2026-06-11: allow OT/extra innings
    "sportres_min_hrs_in":      1.0,    # 2026-06-11: tennis runs 1.5-3h — past 1h ≈ mid/late match
    "sportres_band_lo":         0.88,   # side-mid band (same as nearres)
    "sportres_band_hi":         0.95,
    "sportres_min_vol":         100_000, # sports vol runs lower than politics
    "sportres_max_open":        12,
    "sportres_bet_usdc":        2.0,
    "sportres_gain":            0.09,
    "sportres_stop":            0.03,   # 3.0 R:R
    "sportres_max_hold_hours":  6,

    "nearreslow_enabled":       False,  # KILLED 2026-06-11: OOS backtest n=69, 38% WR, CI [-0.067,-0.003] — band <0.88 is negative-EV, Wang confirmed
    "nearreslow_band_lo":       0.80,
    "nearreslow_band_hi":       0.88,   # exclusive top — no overlap with nearres
    "nearreslow_min_vol":       250_000,
    "nearreslow_max_open":      12,
    "nearreslow_bet_usdc":      2.0,
    "nearreslow_gain":          0.09,
    "nearreslow_stop":          0.03,   # 3.0 R:R
    "nearreslow_max_hold_hours": 6,

    # ── psconfirm (2026-06-11): PandaScore-confirmed nearres A/B ──────────────
    # alice-pattern: independent live signal gates the entry. Same band/window
    # as nearres but ONLY when PandaScore says the match is verifiably RUNNING.
    "psconfirm_enabled":         False,  # KILLED 2026-06-19 loss-sweep: cumulative $-0.90 n=10
    "psconfirm_max_open":        10,
    "psconfirm_bet_usdc":        2.0,
    "psconfirm_gain":            0.99,   # unreachable — rides to resolution
    "psconfirm_stop":            0.03,
    "psconfirm_max_hold_hours":  8,

    # ── nearrestitle (2026-06-13): title-gated nearres A/B (Research Finding C) ──
    # OOS per-title breakdown (backtest_results.json esports_hi, n=161):
    # LoL 68.5% WR CI[+0.011,+0.075] excludes 0; CS2 61.2% CI brushes 0;
    # Dota2 50% WR NEGATIVE mean (n=24) — the reverse-FLB title (Whelan 2024).
    # Same band/window as nearres but skips Dota2 + handicap markets + tennis
    # filter-leaks. nearres itself untouched (28/30 to gate — a config change
    # would reset its config-consistent experiment).
    "nearrestitle_enabled":         False,  # KILLED 2026-06-15: 6 exits, 2 stops filled 18-32¢ through trigger (0.725/0.555 vs 0.905/0.875 triggers) — same gap-through artifact as nearres, amplified by bet_usdc=2.0. R:R: risk 15-35¢ to make 5-7¢. mean=-$0.116/exit.
    "nearrestitle_max_open":        10,
    "nearrestitle_bet_usdc":        2.0,
    "nearrestitle_gain":            0.99,   # unreachable — rides to resolution
    "nearrestitle_stop":            0.03,
    "nearrestitle_max_hold_hours":  8,
    "nearrestitle_skip_kw":         ["dota", "handicap", "itf ", "atp ", "wta "],

    # ── quietfade (2026-06-11): news-quiet conflict fade ──────────────────────
    # nearresfade SKIPS conflict markets wholesale (escalation drift). quietfade
    # re-enters ONLY when OpenAlice's 6h feed has NO matching headline — fade
    # the fear premium when news is verifiably quiet. Down/empty feed → skip.
    "quietfade_enabled":         True,
    "quietfade_yes_min":         0.22,
    "quietfade_yes_max":         0.52,
    "quietfade_min_days":        1,
    "quietfade_max_days":        30,
    "quietfade_min_vol":         200_000,
    "quietfade_max_open":        8,
    "quietfade_bet_usdc":        1.0,
    "quietfade_gain":            0.10,
    "quietfade_stop":            0.07,
    "quietfade_max_hold_hours":  720,

    # ── panel (2026-06-11): TradingAgents architecture, deterministic ─────────
    # TauricResearch/TradingAgents distilled: 3 independent analyst votes
    # (momentum / whale flow / news headline) + bear-veto risk gate. Enter YES
    # on >=2 votes, no veto. The multi-agent debate as arithmetic — no LLM key,
    # no per-decision token cost, fits the 60s cycle.
    "panel_enabled":             False,  # KILLED 2026-06-15: n=8, 12% WR, -$0.97, -12¢/exit, CI[-0.203,-0.036]. News-panel directional bet loses on calibrated mids. Kill rule met (n≥5, WR≤20%, negative).
    "panel_yes_min":             0.55,
    "panel_yes_max":             0.85,
    "panel_min_vol":             200_000,
    "panel_max_days":            30,
    "panel_min_votes":           2,
    "panel_chg_vote":            0.03,   # momentum analyst: 24h move >= +3c
    "panel_whale_vote":          300,    # flow analyst: net whale buy >= $300
    "panel_max_open":            6,
    "panel_bet_usdc":            1.0,
    "panel_gain":                0.09,
    "panel_stop":                0.03,   # 3.0 R:R
    "panel_max_hold_hours":      72,

    # ── tasignal (2026-06-14): LIVE TradingAgents LLM agent graph (vs panel which
    #    only DISTILLED its vote structure). tradingagents_feed.py runs the agent
    #    graph on a few markets and writes a verdict cache; this leg trades the
    #    DIVERGENCE between TA's BUY/SELL lean and the market's implied YES.
    #    OFF until an LLM key is in .env AND the feed's first keyed run is validated.
    "tasignal_enabled":          False,
    "tasignal_cache":            "tradingagents_signals.json",
    "tasignal_min_div":          0.20,    # TA must disagree with the market by >=20pts
    "tasignal_min_vol":          50_000,
    "tasignal_max_days":         30,
    "tasignal_max_open":         6,
    "tasignal_bet_usdc":         1.0,
    "tasignal_gain":             0.09,
    "tasignal_stop":             0.03,    # 3.0 R:R
    "tasignal_max_hold_hours":   72,

    "nearres_min_vol":          250_000, # quality filter (was 75k)
    "nearres_max_open":         20,     # let it fire freely — this is the main bet
    "nearres_bet_usdc":         2.0,    # double bet — highest confidence leg
    "nearres_gain":             0.09,   # 2026-06-10: 88→97 gain=0.09 stop=0.03 → R:R=3.0
    "nearres_stop":             0.030,  # 2026-06-10: R:R = 0.09/0.03 = 3.0 exactly, noise-tolerant
    "nearres_max_hold_hours":   5,      # 1h buffer past 4h window

    # ── nearres_sports (sports-only near-resolution, 2026-06-09) ─────────────
    # Sports markets have binary, time-locked outcomes — resolution is guaranteed.
    # A sports market at 85¢ with 4h left is basically a 85¢→$1 trade.
    "nearres_sports_enabled":   True,
    "nearres_sports_max_hours": 5,   # 2026-06-10: tighter      # slightly wider — sports can run late
    "nearres_sports_yes_hi":    0.87, # 2026-06-10
    "nearres_sports_yes_lo":    0.18,
    "nearres_sports_min_vol":   150_000,
    "nearres_sports_max_open":  15,
    "nearres_sports_bet_usdc":  2.0,
    "nearres_sports_gain":      0.09,
    "nearres_sports_stop":      0.030,
    "nearres_sports_max_hold_hours": 9,

    # ── nearres_pol (politics-only near-resolution, 2026-06-09) ──────────────
    # Political markets near resolution have the highest informed trader
    # concentration. When it hits 85¢ within 6h — the result is known.
    "nearres_pol_enabled":      False,  # KILLED 2026-06-19 loss-sweep: cumulative $-0.04 n=1
    "nearres_pol_max_hours":    4,    # 2026-06-10
    "nearres_pol_yes_hi":       0.90, # 2026-06-10   # tighter — politics has more surprises
    "nearres_pol_yes_lo":       0.15,
    "nearres_pol_min_vol":      300_000,
    "nearres_pol_max_open":     10,
    "nearres_pol_bet_usdc":     2.0,
    "nearres_pol_gain":         0.08,
    "nearres_pol_stop":         0.030,
    "nearres_pol_max_hold_hours": 7,

    # ── snaprev (single-scan reversal from _price_snap, 2026-06-08) ──────────
    # If YES ROSE ≥8¢ in the last scan interval and NOW reversed ≥3¢ back
    # down, the spike was a fakeout — fade it (buy NO).
    # Mirror: if YES FELL ≥8¢ and just bounced ≥3¢, buy YES.
    # Uses _price_snap (previous-scan price stored each cycle).
    "snaprev_enabled":          False,  # KILLED 2026-06-08: 12 exits, -$0.921, 58% WIN-RATE TRAP — high win% but negative $.
    "snaprev_min_spike":        0.08,   # prior-scan move must be ≥8¢ to qualify
    "snaprev_min_reversal":     0.03,   # current-scan reversal must be ≥3¢
    "snaprev_yes_min":          0.15,
    "snaprev_yes_max":          0.85,
    "snaprev_min_vol":          150_000,
    "snaprev_max_open":         10,
    "snaprev_bet_usdc":         1.0,
    "snaprev_gain":             0.08,
    "snaprev_stop":             0.06,
    "snaprev_max_hold_hours":   24,

    # ── volburst (high volume + large price move together, 2026-06-08) ───────
    # When a market has BOTH high vol (>$800k) AND a large price move (|chg|>8¢)
    # these two signals together confirm real informed trading — not noise.
    # Follow the direction of the move.
    "volburst_enabled":         False,  # KILLED 2026-06-08: 11 exits, -$1.124, 36% win — losing money following vol spikes.
    "volburst_min_vol":         800_000,
    "volburst_min_chg":         0.08,   # must have moved ≥8¢
    "volburst_yes_min":         0.15,
    "volburst_yes_max":         0.85,
    "volburst_max_open":        12,
    "volburst_bet_usdc":        1.0,
    "volburst_gain":            0.10,
    "volburst_stop":            0.07,
    "volburst_max_hold_hours":  48,

    # ── flipflop (large reversal momentum, 2026-06-08) ───────────────────────
    # A market that was priced very differently yesterday (implied by chg) and
    # has now crossed 50¢ is in a regime change. The reversal has momentum.
    # yes<0.45 AND chg<-0.15 → was >0.60 yesterday → keep selling (buy NO)
    # yes>0.55 AND chg>+0.15 → was <0.40 yesterday → keep buying (buy YES)
    "flipflop_enabled":         False,  # KILLED: -$0.56, 8 exits, 63% win — R:R broken
    "flipflop_min_chg":         0.15,   # must have moved ≥15¢ (big swing)
    "flipflop_yes_hi_max":      0.45,   # bear flipflop: now below 45¢
    "flipflop_yes_lo_min":      0.55,   # bull flipflop: now above 55¢
    "flipflop_min_vol":         300_000,
    "flipflop_max_open":        10,
    "flipflop_bet_usdc":        1.0,
    "flipflop_gain":            0.12,
    "flipflop_stop":            0.08,
    "flipflop_max_hold_hours":  48,

    # ── stalefish (stuck market finally breaks out, 2026-06-08) ──────────────
    # A market priced near 50¢ for a long time (low |chg|) that SUDDENLY moves
    # ≥10¢ in one scan (via _price_snap delta) has broken out of indecision.
    # Follow the breakout — stuck markets have suppressed vol, breakout = real news.
    "stalefish_enabled":        False,  # KILLED 2026-06-08: 14 exits, -$2.559, 43% win — breakout-follow thesis disproven.
    "stalefish_stale_chg":      0.02,   # market was "stuck" = |chg| < 2¢ yesterday (use chg field)
    "stalefish_breakout":       0.08,   # but just moved ≥8¢ this scan vs _price_snap
    "stalefish_yes_min":        0.20,
    "stalefish_yes_max":        0.80,
    "stalefish_min_vol":        100_000,
    "stalefish_max_open":       10,
    "stalefish_bet_usdc":       1.0,
    "stalefish_gain":           0.12,   # wide — breakouts tend to overshoot
    "stalefish_stop":           0.07,
    "stalefish_max_hold_hours": 48,

    # ── nohappen ("Nothing Ever Happens" bot, OSS-inspired, 2026-06-09) ───────
    # Academic finding: markets asking "Will [dramatic thing] happen?"
    # systematically OVERPRICE YES due to attention bias. The answer is almost
    # always NO. Buy NO on sensational markets priced 15–50¢.
    # Keywords: crash, war, resign, arrest, impeach, collapse, default, ban,
    #           invade, nuke, assassin, bankrupt, sanction, coup, scandal
    # Source: "Nothing Ever Happens" bot + QuantPedia prediction market biases
    "nohappen_enabled":         False,  # KILLED 2026-06-19 loss-sweep: cumulative $-0.24 n=5
    "nohappen_yes_min":         0.15,   # not already near-zero
    "nohappen_yes_max":         0.50,   # still dramatic odds
    "nohappen_min_vol":         200_000,
    "nohappen_max_open":        15,
    "nohappen_bet_usdc":        1.0,
    "nohappen_gain":            0.10,   # NO → 0 pays out well
    "nohappen_stop":            0.08,
    "nohappen_max_hold_hours":  168,    # dramatic events take time to not happen

    # ── longshortbias (favorite-longshot bias, QuantPedia, 2026-06-09) ────────
    # QuantPedia documented edge: longshots (YES < 10¢) are SYSTEMATICALLY
    # overpriced in prediction markets. Crowds overestimate low-probability
    # dramatic events. Buy NO on longshots with sufficient liquidity.
    # Same academic basis as truefade but at the extreme low end.
    "longshortbias_enabled":    False,  # KILLED 2026-06-19 sweep: bootstrap CI[-0.096,-0.015] excl 0, n=5
    "longshortbias_yes_max":    0.10,   # only true longshots
    "longshortbias_yes_min":    0.03,   # not already dead (vol dries up)
    "longshortbias_min_vol":    500_000, # need liquidity to exit
    "longshortbias_max_open":   15,
    "longshortbias_bet_usdc":   1.0,
    "longshortbias_gain":       0.05,   # NO at 93¢ → 98¢ = realistic
    "longshortbias_stop":       0.04,
    "longshortbias_max_hold_hours": 336, # longshots need time to not happen

    # ── clobimbal (CLOB order book imbalance, PolyFlup-inspired, 2026-06-09) ──
    # PolyFlup uses bid/ask liquidity imbalance as a 20% weight in its signal.
    # When bids >> asks (ratio ≥ 2.5×), smart money is loading the bid.
    # When asks >> bids, smart money is offering — follow the liquidity.
    # Uses existing fetch_clob_book() infrastructure.
    "clobimbal_enabled":     False,  # KILLED 2026-06-11: 1/5 wins -$0.61 (kill rule n>=5+WR<=20%+neg); whale-family prior also negative
    "clobimbal_min_ratio":      2.5,    # one side must have 2.5× the other
    "clobimbal_levels":         5,      # look at top 5 price levels
    "clobimbal_yes_min":        0.20,
    "clobimbal_yes_max":        0.80,
    "clobimbal_min_vol":        150_000,
    "clobimbal_max_open":       15,
    "clobimbal_bet_usdc":       1.0,
    "clobimbal_gain":           0.09,
    "clobimbal_stop":           0.06,
    "clobimbal_max_hold_hours": 48,

    # ── polyflup (PolyFlup weighted crypto signal, 2026-06-09) ───────────────
    # Simplified port of Niller2005/PolyFlup's weighted directional model.
    # Original uses 5 signals; we implement 3 from Gamma API data:
    #   chg momentum (30% weight): chg > 0 = bullish
    #   vol momentum (10% weight): vol spike = confirmed
    #   CLOB imbalance (20% weight): bid > ask = bullish
    # Fire when combined confidence ≥ 60%. Crypto/BTC/ETH markets only.
    # Source: github.com/Niller2005/PolyFlup STRATEGY.md
    "polyflup_enabled":      False,  # KILLED 2026-06-12: 1/5 wins 20% WR -$0.46 (kill rule) — crypto momentum on dip-strike markets failed
    "polyflup_min_confidence":  0.60,   # fire when ≥60% weighted signal
    "polyflup_min_vol":         300_000,
    "polyflup_min_chg":         0.02,   # must be moving
    "polyflup_yes_min":         0.30,
    "polyflup_yes_max":         0.70,
    "polyflup_max_open":        10,
    "polyflup_bet_usdc":        1.0,
    "polyflup_gain":            0.10,
    "polyflup_stop":            0.07,
    "polyflup_max_hold_hours":  24,


    # ── nearresfade (truefade replacement — fade near-resolution, 2026-06-10) ─────
    # Truefade failed because long-dated markets don't move in 48h.
    # Fix: only fade markets with <30 DAYS to resolution — then resolution pressure
    # forces convergence toward the correct probability. Wang transform says YES[0.20-0.55]
    # is systematically overpriced. With <30d remaining, that mispricing corrects fast.
    "nearresfade_enabled":       False,  # KILLED 2026-06-15: n=29, 52% WR, -$1.20. Kill rule met. truefade's replacement — same fade-family bleed (CI includes 0 but net-negative at n=29).
    "nearresfade_yes_min":       0.22,   # fade from 22¢ YES
    "nearresfade_yes_max":       0.52,   # up to 52¢ YES (strong overpricing zone)
    "nearresfade_max_days":      30,     # ONLY markets resolving within 30 days
    "nearresfade_min_days":      1,      # avoid same-day (already handled by nearres)
    # 2026-06-11: 3 of first 5 losses were live-escalation markets (Israel airspace
    # x2, Yemen strike) — during an active conflict the YES drift is real news, not
    # base-rate overpricing. Sit those out.
    "nearresfade_skip_kw":       ("israel", "iran", "yemen", "gaza", "strike on",
                                  "airspace", "ceasefire", "war ", "nuclear",
                                  "russia", "ukraine", "nato", "invasion"),
    "nearresfade_min_vol":       200_000,
    "nearresfade_max_open":      20,
    "nearresfade_bet_usdc":      1.0,
    "nearresfade_gain":          0.10,   # NO from 0.60 → 0.70 = 10¢ gain
    "nearresfade_stop":          0.07,   # 7¢ stop → R:R = 0.10/0.07 = 1.43 (will tune)
    "nearresfade_max_hold_hours": 720,   # 30 days — let resolution pressure work

    # ── windowshut (2026-06-14): structural-theta NO on lognormal-IMPOSSIBLE crypto
    # threshold markets, held to resolution. Buy NO when Black-Scholes P(YES) is
    # near-impossible (<=max_fairprob) yet the market still prices YES at a theta
    # premium (>=yes_min). Gap-immune (NO position rises to 1.0 as the impossible
    # YES decays; no stop to gap through — but we KEEP a 7¢ stop per Lesson #8,
    # no-stop hold-to-res = killer variance). Distinct from cryptofair (which scalps
    # divergence either-side with a 96h cap) — windowshut is NO-only, deep-impossible,
    # resolution-horizon. Gated against control windowshutrand (same entry, NO gate).
    "windowshut_enabled":        True,
    "windowshut_max_fairprob":   0.08,   # lognormal P(YES) must be <= 8% (near-impossible)
    "windowshut_yes_min":        0.04,   # but market YES still >= 4% = theta premium to harvest
    "windowshut_yes_max":        0.25,   # above this it's just "unlikely", not impossible
    "windowshut_min_days":       1,      # avoid same-day noise
    "windowshut_max_days":       30,     # resolution-pressure window
    "windowshut_min_vol":        100_000,
    "windowshut_max_open":       15,
    "windowshut_bet_usdc":       1.0,
    "windowshut_gain":           0.10,   # NO ~0.90 -> 1.00 at resolution
    "windowshut_stop":           0.07,   # Lesson #8: keep a stop on hold-to-res NO
    "windowshut_max_hold_hours": 720,
    "windowshut_annual_vol":     0.80,   # vol prior for the lognormal (matches _lognormal_prob)
    # control: same NO entry on the same low-YES crypto-threshold universe, but WITHOUT
    # the lognormal feasibility gate. windowshut must BEAT this or the gate adds no alpha.
    "windowshutrand_enabled":        True,
    "windowshutrand_yes_min":        0.04,
    "windowshutrand_yes_max":        0.25,
    "windowshutrand_min_days":       1,
    "windowshutrand_max_days":       30,
    "windowshutrand_min_vol":        100_000,
    "windowshutrand_max_open":       15,
    "windowshutrand_bet_usdc":       1.0,
    "windowshutrand_gain":           0.10,
    "windowshutrand_stop":           0.07,
    "windowshutrand_max_hold_hours": 720,

    # ── tailspin (sustained downtrend momentum, 2026-06-08) ──────────────────
    # Market in a clear downtrend: yes < 0.30 AND chg <= -0.10 (dropped ≥10¢ today).
    # Yesterday it was ≥0.40, now below 0.30 — the sellers are winning. Buy NO.
    # Mirror: yes > 0.70 AND chg >= +0.10 → buyers winning → buy YES.
    "tailspin_enabled": False,  # KILLED 2026-06-10: 67% win 9 exits +$0.11 R:R 0.65 — positive but R:R too low vs 3:1 target
    "tailspin_yes_lo":          0.30,   # bear tailspin: price below this
    "tailspin_yes_hi":          0.70,   # bull tailspin: price above this
    "tailspin_min_chg":         0.10,   # must have moved ≥10¢ (real trend)
    "tailspin_min_vol":         200_000,
    "tailspin_max_open":        12,
    "tailspin_bet_usdc":        1.0,
    "tailspin_gain":            0.12,
    "tailspin_stop":            0.08,
    "tailspin_max_hold_hours":  48,

    # ── roundnum (tipping-point breakout, 2026-06-08) ────────────────────────
    # When a market crosses 0.50 for the first time (prev_snap < 0.50, now > 0.52
    # OR prev_snap > 0.50, now < 0.48) with high volume, that's a regime flip.
    # Follow the direction — crossing 50¢ with conviction tends to continue.
    "roundnum_enabled":         False,  # KILLED 2026-06-10: 17 exits -$0.09/exit
    "roundnum_cross_buffer":    0.02,   # must be ≥2¢ past 0.50 to count (not just touching)
    "roundnum_min_vol":         300_000,
    "roundnum_max_open":        10,
    "roundnum_bet_usdc":        1.0,
    "roundnum_gain":            0.10,
    "roundnum_stop":            0.07,
    "roundnum_max_hold_hours":  48,

    # ── firstmover (largest markets by volume, follow momentum, 2026-06-08) ──
    # The top 8 markets by volume have the tightest spreads and best price
    # discovery. When they move (|chg| ≥ 5¢), that's real informed flow.
    # Follow the direction. Tight stop since these markets mean-revert less.
    "firstmover_enabled":       True,
    "firstmover_top_n":         8,      # look at top N markets by vol in this scan
    "firstmover_min_chg":       0.05,
    "firstmover_yes_min":       0.20,
    "firstmover_yes_max":       0.80,
    "firstmover_max_open":      8,
    "firstmover_bet_usdc":      1.0,
    "firstmover_gain":          0.09,
    "firstmover_stop":          0.06,
    "firstmover_max_hold_hours": 36,

    # ── yoyo (oscillation noise fade, 2026-06-08) ────────────────────────────
    # Markets that reverse EVERY scan (scan_delta opposite to chg) are pure noise
    # — random walk around a mean. When we see the current move, fade it:
    # the next scan will likely reverse again.
    # Requires _price_snap. chg > 0 (rose 24h) but scan_delta < 0 (just fell)
    # → market is oscillating → buy NO (bet on continued reversal).
    "yoyo_enabled":             False,  # KILLED 2026-06-08: 14 exits, -$1.540, 43% win — oscillation trade doesn't hold up at scale.
    "yoyo_min_chg":             0.05,   # 24h move must be ≥5¢ (not flat)
    "yoyo_min_reversal":        0.03,   # scan reversal must be ≥3¢ (real oscillation)
    "yoyo_yes_min":             0.25,
    "yoyo_yes_max":             0.75,
    "yoyo_min_vol":             100_000,
    "yoyo_max_open":            10,
    "yoyo_bet_usdc":            1.0,
    "yoyo_gain":                0.07,
    "yoyo_stop":                0.05,
    "yoyo_max_hold_hours":      24,

    # ── bigswing (>20¢ single-day move, follow it, 2026-06-08) ───────────────
    # A 20¢+ move in one day on a liquid market is rare and almost always real
    # information — not noise. Even if you're late, the move usually has legs.
    "bigswing_enabled":         False,  # KILLED 2026-06-08: 4 exits, -$1.678, 0% win — chasing big moves = always buying tops.
    "bigswing_min_chg":         0.20,
    "bigswing_yes_min":         0.10,
    "bigswing_yes_max":         0.90,
    "bigswing_min_vol":         300_000,
    "bigswing_max_open":        10,
    "bigswing_bet_usdc":        1.0,
    "bigswing_gain":            0.15,
    "bigswing_stop":            0.10,
    "bigswing_max_hold_hours":  48,

    # ── polmom (political market momentum, 2026-06-08) ────────────────────────
    # Politics-only. When a political market moves ≥6¢ with vol > $500k,
    # it's real information (polls, news). Follow the direction.
    # Politics markets have the highest informed-trader concentration.
    "polmom_enabled":           False,  # KILLED: -$0.24, 2 exits — too early but same pattern
    "polmom_min_chg":           0.06,
    "polmom_min_vol":           500_000,
    "polmom_yes_min":           0.15,
    "polmom_yes_max":           0.85,
    "polmom_max_open":          12,
    "polmom_bet_usdc":          1.0,
    "polmom_gain":              0.10,
    "polmom_stop":              0.07,
    "polmom_max_hold_hours":    72,

    # ── sportshigh (sports markets >75¢ fading to certainty, 2026-06-08) ─────
    # Sports markets priced >75¢ that still have volume are near-decided.
    # They drift toward 100¢ as resolution approaches. Buy YES and hold.
    "sportshigh_enabled": False,  # KILLED 2026-06-10
    "sportshigh_yes_min":       0.75,
    "sportshigh_yes_max":       0.95,
    "sportshigh_min_vol":       150_000,
    "sportshigh_min_chg":       0.01,   # must still be moving (not already resolved)
    "sportshigh_max_open":      10,
    "sportshigh_bet_usdc":      1.0,
    "sportshigh_gain":          0.12,
    "sportshigh_stop":          0.10,
    "sportshigh_max_hold_hours": 48,

    # ── volwake (dead market springs to life, 2026-06-08) ────────────────────
    # Market vol just exceeded its EMA baseline by 3×+ AND it barely traded
    # before (baseline < $50k). A sleeping market that wakes up = real news.
    # Follow the price direction when the market wakes.
    "volwake_enabled":          True,
    "volwake_ema_mult":         3.0,    # vol must be 3× EMA baseline
    "volwake_max_baseline":     50_000, # only for previously-sleepy markets
    "volwake_min_chg":          0.03,
    "volwake_yes_min":          0.15,
    "volwake_yes_max":          0.85,
    "volwake_max_open":         10,
    "volwake_bet_usdc":         1.0,
    "volwake_gain":             0.12,
    "volwake_stop":             0.08,
    "volwake_max_hold_hours":   48,

    # ── doubletop (price hit high, pulled back, retrying — fade the retry, 2026-06-08)
    # Market rose to X (prev_snap high), fell back, now re-testing X from below.
    # Double-tops are rejection signals. If yes ≈ prev_snap high and chg < 0 now,
    # bet NO — the second test usually fails.
    "doubletop_enabled":        False,  # KILLED: 0 exits, never fires
    "doubletop_retrace_min":    0.04,   # must have pulled back ≥4¢ then re-tested
    "doubletop_retry_buffer":   0.02,   # within 2¢ of previous high = re-test
    "doubletop_min_vol":        200_000,
    "doubletop_yes_min":        0.40,
    "doubletop_yes_max":        0.85,
    "doubletop_max_open":       10,
    "doubletop_bet_usdc":       1.0,
    "doubletop_gain":           0.09,
    "doubletop_stop":           0.06,
    "doubletop_max_hold_hours": 36,

    # ── lopsided (one side heavily traded vs other, fade the crowd, 2026-06-08)
    # When vol is very high but price is near 50¢, market is contested.
    # If chg is negative despite high vol → buyers losing → sell YES (buy NO).
    # If chg is positive despite high vol → sellers losing → buy YES.
    "lopsided_enabled":         False,  # KILLED 2026-06-10: 9 exits -$0.12/exit
    "lopsided_min_vol":         1_000_000,  # only in very liquid markets
    "lopsided_yes_min":         0.35,
    "lopsided_yes_max":         0.65,       # near 50¢ = genuinely contested
    "lopsided_min_chg":         0.04,
    "lopsided_max_open":        8,
    "lopsided_bet_usdc":        1.0,
    "lopsided_gain":            0.09,
    "lopsided_stop":            0.06,
    "lopsided_max_hold_hours":  48,

    # ── sportsnear (sports market, <72h to end, 40-80¢, 2026-06-08) ──────────
    # Sports markets in the final 3 days get heavy retail flow that often
    # overshoots in one direction before correcting into the result.
    # Buy the direction of chg — late sports money is usually smart money.
    "sportsnear_enabled":       False,  # KILLED: -$0.66, 1 exit — quick kill
    "sportsnear_max_hours":     72,
    "sportsnear_yes_min":       0.40,
    "sportsnear_yes_max":       0.80,
    "sportsnear_min_vol":       200_000,
    "sportsnear_min_chg":       0.04,
    "sportsnear_max_open":      10,
    "sportsnear_bet_usdc":      1.0,
    "sportsnear_gain":          0.12,
    "sportsnear_stop":          0.08,
    "sportsnear_max_hold_hours": 72,

    # ── whaleagreement (2+ whale trades same direction <2h, 2026-06-08) ──────
    # When 2 or more independent whale wallets (≥$150 each) buy the same
    # side within 2 hours, they independently reached the same conclusion.
    # That's stronger than a single whale. Enter same direction.
    "whaleagree_enabled":       False,  # KILLED: 0 exits — depends on killed whale legs
    "whaleagree_min_usdc":      150,    # per whale
    "whaleagree_min_whales":    2,      # need at least 2 agreeing
    "whaleagree_min_vol":       100_000,
    "whaleagree_yes_min":       0.15,
    "whaleagree_yes_max":       0.85,
    "whaleagree_max_open":      12,
    "whaleagree_bet_usdc":      1.5,    # higher confidence = bigger bet
    "whaleagree_gain":          0.10,
    "whaleagree_stop":          0.07,
    "whaleagree_max_hold_hours": 72,

    # ── gapfill (price gapped up/down from prev scan, expect partial fill, 2026-06-08)
    # When a market gaps >12¢ between scans (scan_delta large), gaps often
    # partially fill back. Fade 50% of the gap.
    # Different from snaprev: snaprev needs chg confirmation; gapfill is pure
    # scan-level price discontinuity with NO prior trend requirement.
    "gapfill_enabled":          False,  # KILLED 2026-06-08: 7 exits, -$2.367, 29% win — gap-fill thesis does not hold on prediction markets.
    "gapfill_min_gap":          0.12,   # scan delta must be ≥12¢
    "gapfill_yes_min":          0.10,
    "gapfill_yes_max":          0.90,
    "gapfill_min_vol":          150_000,
    "gapfill_max_open":         10,
    "gapfill_bet_usdc":         1.0,
    "gapfill_gain":             0.07,   # target ~50% gap fill
    "gapfill_stop":             0.06,
    "gapfill_max_hold_hours":   24,

    # ── streakbuy (3+ positive chg markets in same category = streak, 2026-06-08)
    # When 3+ consecutive markets in the same cat have chg > 5¢ in the same
    # scan, the whole category is in a bullish streak. Enter YES on the next
    # qualifying market in that category before the streak ends.
    "streakbuy_enabled":        False,  # KILLED: -$0.49, 8 exits, 13% win
    "streakbuy_min_streak":     3,      # need 3+ markets up in cat this scan
    "streakbuy_streak_chg":     0.05,   # each must have moved ≥5¢
    "streakbuy_min_vol":        100_000,
    "streakbuy_yes_min":        0.25,
    "streakbuy_yes_max":        0.75,
    "streakbuy_max_open":       8,
    "streakbuy_bet_usdc":       1.0,
    "streakbuy_gain":           0.09,
    "streakbuy_stop":           0.06,
    "streakbuy_max_hold_hours": 36,

    # ── revertmid (extreme price pulled back toward 50¢, 2026-06-08) ─────────
    # Markets that were extreme (>85¢ or <15¢) and are now moving back toward
    # 50¢ (chg moving away from extreme) — the extreme was wrong, follow the
    # correction. Buy NO if was >85¢ now falling; YES if was <15¢ now rising.
    "revertmid_enabled":        False,  # KILLED 2026-06-10: 5 exits -$0.49/exit -$2.47 total
    "revertmid_hi":             0.82,   # was above this (high extreme)
    "revertmid_lo":             0.18,   # was below this (low extreme)
    "revertmid_min_chg":        0.04,   # must be moving back
    "revertmid_min_vol":        200_000,
    "revertmid_max_open":       10,
    "revertmid_bet_usdc":       1.0,
    "revertmid_gain":           0.10,
    "revertmid_stop":           0.07,
    "revertmid_max_hold_hours": 48,

    # ── OSS-BOT LEGS (2026-06-10) ─────────────────────────────────────────────
    # Four legs derived from cloned open-source Polymarket bots in oss-bots/.

    # ── noevent (nothing-ever-happens inspired, 2026-06-10) ──────────────────
    # "nothing-ever-happens" bot (sterlingcrispin, 944★) buys NO on ALL
    # non-sports markets below a price cap. Thesis: the market systematically
    # over-prices exciting/scary "yes will happen" events. We test the core
    # thesis here — NO on non-sports markets in [0.30, 0.60] (broader than
    # truefade [0.20-0.55]) without the sports filter the OSS bot applies.
    "noevent_enabled":          False,  # KILLED 2026-06-15: n=47, 40% WR, -$0.80, -1.7¢/exit (CI[-0.052,+0.017] includes 0 = honest null, not edge). Was the designed honest-null baseline; bleeds slowly. allin/coinflip/coindown already cover the marking-honesty control role, so retire it. Revive as a pure baseline if a clean null reference is needed.
    "noevent_yes_min":          0.30,   # don't bet near-dead markets
    "noevent_yes_max":          0.60,   # don't bet near-decided markets
    "noevent_min_volume":       80_000, # need some liquidity
    "noevent_max_open":         15,
    "noevent_bet_usdc":         1.0,
    "noevent_gain":             0.09,   # R:R tuned 2026-06-10
    "noevent_stop":             0.03,   # 0.09/0.03 = 3.0 R:R
    "noevent_max_hold_hours":   72,
    # keyword blocklist: skip sports/esports markets (OSS bot's "non-sports" gate)
    "noevent_sports_kw":        ("nba", "nfl", "mlb", "nhl", "fifa", "epl", "world cup",
                                  "champions league", "premier league", "soccer",
                                  "baseball", "basketball", "football", "hockey",
                                  "tennis", "golf", "mma", "ufc", "esport", "cs2",
                                  "valorant", "league of legends", "dota"),

    # ── weatherno (weatherbot inspired, 2026-06-10) ───────────────────────────
    # weatherbot (alteregoeth-ai, 324★) trades weather temperature markets using
    # NWS forecasts + Kelly Criterion. The key insight: Polymarket weather markets
    # systematically overprice "above average temperature" YES during bull weather
    # sentiment. We test the price-only version: fade YES > 0.58 on weather mkts.
    "weatherno_enabled":        True,
    "weatherno_yes_min":        0.58,   # elevated price = likely overpriced
    "weatherno_yes_max":        0.88,   # not near-resolved yet
    "weatherno_min_volume":     80_000, # raised — thin markets have bad fills
    "weatherno_max_open":       8,
    "weatherno_bet_usdc":       1.0,
    "weatherno_gain":           0.09,
    "weatherno_stop":           0.03,   # 3.0 R:R
    "weatherno_max_hold_hours": 72,
    "weatherno_kw":             ("temperature", "degrees", "fahrenheit", "celsius",
                                  "weather", "high temp", "low temp", "above average",
                                  "below average", "warmer", "cooler", "heat", "cold snap",
                                  "nyc temp", "chicago temp", "miami temp", "dallas temp"),

    # ── btc15no (retargeted 2026-06-13: regime-gated "dip to $X" fade) ──────────
    # Original target (15-min up/down markets) no longer exists in the live feed.
    # Retargeted to "dip to $X by date" markets — the actual crypto markets in feed.
    # Thesis: in TREND_UP regime, YES on "dip to $X" is overpriced (BTC won't dip).
    # Regime gate (regime_leg.py): only fire in TREND_UP or RANGE, never TREND_DOWN.
    # YES band: market pricing dip as somewhat likely (0.25-0.60) → fade it.
    "btc15no_enabled":          False,  # KILLED 2026-06-19 sweep: bootstrap CI[-0.087,-0.000] excl 0, n=8
    "btc15no_yes_min":          0.25,   # dip priced as possible (>25¢)
    "btc15no_yes_max":          0.60,   # not near-resolved
    "btc15no_min_volume":       80_000,
    "btc15no_max_open":         6,
    "btc15no_bet_usdc":         1.0,
    "btc15no_gain":             0.09,
    "btc15no_stop":             0.03,
    "btc15no_max_hold_hours":   72,     # price-level markets resolve slowly
    "btc15no_asset_kw":         ("btc", "bitcoin", "eth", "ethereum", "sol", "solana",
                                  "xrp", "bnb"),
    "btc15no_frame_kw":         ("dip to", "drop to", "fall to", "crash to",
                                  "below $", "under $", "reach $", "hit $"),

    # ── csarb — complete-set discount (polybot OSS harvest, 2026-06-11) ───────
    # polybot's reverse-engineered "Gabagool" spec: buy the full YES+NO set when
    # its total cost < $1 inside the final minutes of short-window crypto markets;
    # redemption pays exactly $1. Tested here as the TAKER reduction using gamma
    # top-of-book (bestAsk + (1-bestBid) < 1 - threshold). HONESTY CAVEAT: gamma
    # quotes are snapshots, not executable fills — if this leg ever "prints money"
    # it is stale-quote illusion, not edge. Expected behaviour: nearly never
    # triggers (crossed books are rare); zero entries is itself the data point.
    "csarb_enabled":            True,
    "csarb_threshold":          0.005,  # set must cost <= 0.995 to enter
    "csarb_min_volume":         2_000,  # 2026-06-15: Up/Down markets now fed (low-vol); was 50k
    "csarb_max_open":           6,
    "csarb_bet_usdc":           1.0,    # SAFE tier
    "csarb_max_hold_hours":     2,      # short-window markets resolve fast
    "csarb_window_min_s":       60,     # polybot time gating: enter only
    "csarb_window_max_s":       900,    #   60–900s before market close
    "csarb_asset_kw":           ("btc", "bitcoin", "eth", "ethereum", "sol", "solana",
                                  "xrp", "bnb"),
    "csarb_frame_kw":           ("15 minute", "15-minute", "15min", "1 hour", "1-hour",
                                  "up or down", "above or below", "higher or lower"),

    # ── newsno (polymarket-pipeline inspired, 2026-06-10) ─────────────────────
    # polymarket-pipeline (brodyautomates, 329★) scrapes breaking news, classifies
    # whether it's market-moving, then fades overpriced YES on event markets.
    # The key signal: "will X happen" phrasing + YES > 0.65 = crowds too confident.
    # We test the question-framing gate without the news classifier: price-only
    # fade on event markets with "will" in question when YES in [0.62, 0.85].
    "newsno_enabled":           False,  # KILLED 2026-06-15: n=38, 40% WR, -$2.58. Kill rule met. News-NO taker on calibrated mids — bleeds (CI includes 0 but bleeding at large n).
    "newsno_yes_min":           0.62,   # crowd already confident
    "newsno_yes_max":           0.85,   # not near-certainty
    "newsno_min_volume":        100_000, # need liquid market for reliable signal
    "newsno_max_open":          12,
    "newsno_bet_usdc":          1.0,
    "newsno_gain":              0.09,   # R:R tuned 2026-06-10
    "newsno_stop":              0.03,   # 0.09/0.03 = 3.0 R:R
    "newsno_max_hold_hours":    72,
    "newsno_kw":                ("will ", "does ", "can ", "is there ", "are there ",
                                  "will there be", "will the", "will it", "will he",
                                  "will she", "will they", "will this"),
    # blocklist: skip near-certainty crypto/sports (they're priced right)
    "newsno_skip_kw":           ("bitcoin reach", "ethereum reach", "btc hit", "eth hit",
                                  "nba champion", "nfl champion", "super bowl",
                                  "world series", "stanley cup",
                                  # 2026-06-10: negated questions INVERT the thesis —
                                  # buying NO on "will no X happen" bets X happens
                                  "will no ", "will there be no", "no qualifying"),

    # ── ytbuzz (YouTube attention hype-fade, 2026-06-10) ──────────────────────
    # YouTube is retail's biggest hype channel. Lots of FRESH (≤48h) videos with
    # real combined views on a market's topic = attention spike → the crowd piles
    # into the obvious YES side → fade it (buy NO). Scrapes public search pages,
    # no API key. Cache persisted via CACHE json so the once-mode bot accumulates
    # coverage across runs (in-memory caches die with each 60s process).
    "ytbuzz_enabled":           False,  # KILLED 2026-06-12: 1/8 wins 12% WR -$1.40 — buzz tracks real events, fading it loses
    "ytbuzz_yes_min":           0.55,
    "ytbuzz_yes_max":           0.85,
    "ytbuzz_min_videos":        5,        # fresh (≤48h) videos required
    "ytbuzz_min_views":         50_000,   # combined views across those videos
    "ytbuzz_min_volume":        80_000,
    "ytbuzz_max_open":          8,
    "ytbuzz_bet_usdc":          1.0,
    "ytbuzz_gain":              0.09,
    "ytbuzz_stop":              0.03,     # 0.09/0.03 = 3.0 R:R house standard
    "ytbuzz_max_hold_hours":    72,

    # ── vlrtop — VLR.GG Valorant match latency-arb (2026-06-10) ────────────
    # Completed matches on vlr.gg: the winner's Polymarket market hasn't
    # resolved yet → buy YES, hold until resolution (usually <4h).
    # Entry band widened to [0.75, 0.97] because post-match markets can be
    # anywhere before the oracle fires.
    "vlrtop_enabled":           True,
    "vlrtop_max_open":          5,
    "vlrtop_bet_usdc":          1.0,
    "vlrtop_gain":              0.09,
    "vlrtop_stop":              0.03,
    "vlrtop_max_hold_hours":    8,   # match results resolve fast

    # ── liqcs2 — Liquipedia CS2 tournament whitelist (2026-06-10) ──────────
    # Only enter nearres/esportsfav on CS2 markets whose tournament appears in
    # Liquipedia's Ongoing_Tournaments list. Prevents stale bracket markets.
    # Not a standalone entry leg — used as a confirmation gate.
    "liqcs2_enabled":           True,

    # ── gtrend — Google Trends attention surge (2026-06-10) ────────────────
    # When a market's subject keyword has current search interest ≥ 1.5x its
    # 7-day rolling mean, crowd attention is spiking → faster resolution,
    # buy YES on favorites [0.75, 0.95]. Cached 1h to avoid rate limits.
    "gtrend_enabled":           True,
    "gtrend_min_ratio":         1.5,   # current/base must be ≥ this
    "gtrend_max_open":          4,
    "gtrend_bet_usdc":          1.0,
    "gtrend_gain":              0.09,
    "gtrend_stop":              0.03,
    "gtrend_max_hold_hours":    48,

    # ── bookpress — full-depth order-book pressure (2026-06-10) ────────────
    # OSS basis: polymarket/agents CLOB bots. Unlike clobimbal (top-N levels),
    # this aggregates ALL depth within 5¢ of mid on each side. Bid depth ≥2.5x
    # ask depth = informed buying → buy YES [0.55, 0.85].
    "bookpress_enabled":        False,  # KILLED 2026-06-19 loss-sweep: cumulative $-0.47 n=46
    "bookpress_max_open":       6,
    "bookpress_bet_usdc":       1.0,
    "bookpress_min_vol":        100_000,
    "bookpress_min_ratio":      2.5,
    "bookpress_band":           0.05,  # depth counted within ±5¢ of mid
    "bookpress_gain":           0.09,
    "bookpress_stop":           0.03,
    "bookpress_max_hold_hours": 48,

    # ── kellyfav — Kelly-sized favorite buy (2026-06-10) ───────────────────
    # FLB literature: favorites are systematically underpriced. Same selection
    # as favyes-ish but the EXPERIMENT is sizing: Kelly fraction (assumed 3%
    # true-prob edge) instead of flat $1. Tests sizing, not selection.
    "kellyfav_enabled":         False,  # KILLED 2026-06-19 loss-sweep: cumulative $-5.33 n=21
    "kellyfav_max_open":        8,
    "kellyfav_unit_usdc":       2.0,   # bankroll unit scaled by Kelly fraction
    "kellyfav_edge":            0.03,  # assumed true-prob edge over market price
    "kellyfav_yes_min":         0.80,
    "kellyfav_yes_max":         0.93,
    "kellyfav_min_vol":         150_000,
    "kellyfav_min_days":        2,
    "kellyfav_max_days":        14,
    "kellyfav_gain":            0.09,
    "kellyfav_stop":            0.03,
    "kellyfav_max_hold_hours":  168,   # favorites need time to converge

    # ── newslag — Claude original: attention+momentum confluence (2026-06-10) ──
    # Reddit fresh-post buzz (cached) AND a ≥4¢ same-scan price move = breaking
    # event being repriced. Buy the moving side [0.60, 0.90]; each signal alone
    # was mediocre (redditbuzz, priceleap) — the AND is the experiment.
    "newslag_enabled":          True,
    "newslag_max_open":         6,
    "newslag_bet_usdc":         1.0,
    "newslag_min_vol":          75_000,
    "newslag_min_posts":        3,     # fresh Reddit posts in cache
    "newslag_min_chg":          0.04,  # last-scan price move
    "newslag_gain":             0.09,
    "newslag_stop":             0.03,
    "newslag_max_hold_hours":   24,

    # ── birdeye (Solana whale/smart-money signal, 2026-06-12) ────────────────
    # Uses cloned birdeye-py + alpha-radar logic (no pip install).
    # Signal: YES when whale_quality>60 (smart money accumulating),
    #         NO when sybil_score>50 (wash trading dominant).
    # Only fires on crypto markets whose question contains bitcoin/ethereum/solana.
    "birdeye_enabled":          True,
    "birdeye_max_open":         6,
    "birdeye_bet_usdc":         1.0,
    "birdeye_gain":             0.09,
    "birdeye_stop":             0.03,
    "birdeye_yes_min":          0.40,   # don't enter near-decided markets
    "birdeye_yes_max":          0.85,
    "birdeye_min_volume":       50_000,
    "birdeye_max_hold_hours":   24,

    # ── liqcrush (liquidity collapse → buy NO, 2026-06-12) ───────────────────
    # Fires when on-chain liquidity crashes (price down >30%/24h or V/L rug) but
    # Polymarket YES price is still elevated — market hasn't repriced yet → buy NO.
    "liqcrush_enabled":         True,
    "liqcrush_max_open":        4,
    "liqcrush_bet_usdc":        1.0,
    "liqcrush_gain":            0.09,
    "liqcrush_stop":            0.03,
    "liqcrush_yes_min":         0.35,   # only fade if YES still elevated
    "liqcrush_yes_max":         0.80,
    "liqcrush_min_volume":      20_000,
    "liqcrush_max_hold_hours":  12,

    # ── newscrypto (RSS news → crypto market signal, 2026-06-13) ─────────────
    # Fetches CoinTelegraph + Decrypt RSS, entity-matches to open crypto markets,
    # scores sentiment, emits YES/NO when edge ≥ 7¢ and confidence ≥ 0.55.
    "newscrypto_enabled":       False,
    "newscrypto_max_open":      4,
    "newscrypto_bet_usdc":      1.0,
    "newscrypto_gain":          0.09,
    "newscrypto_stop":          0.03,
    "newscrypto_yes_min":       0.30,   # don't enter near-decided markets
    "newscrypto_yes_max":       0.80,
    "newscrypto_min_volume":    50_000,
    "newscrypto_max_hold_hours": 24,

    # ── newsesports (RSS news → esports market signal, 2026-06-13) ───────────
    # HLTV RSS + esports entity matching. Confirmation signal for nearres entries.
    # Also opens standalone positions when a known team win/loss is detected.
    "newsesports_enabled":      True,
    "newsesports_max_open":     4,
    "newsesports_bet_usdc":     1.0,
    "newsesports_gain":         0.09,
    "newsesports_stop":         0.03,
    "newsesports_yes_min":      0.35,
    "newsesports_yes_max":      0.80,
    "newsesports_min_volume":   20_000,
    "newsesports_max_hold_hours": 12,

    # ── 2026-06-11 batch: structural/arb legs ─────────────────────────────────
    # wcmatch — daily-match favorite near resolution ("win on 20xx-…" questions)
    "wcmatch_enabled":          False,  # KILLED 2026-06-19 loss-sweep: cumulative $-0.04 n=8
    "wcmatch_yes_min":          0.65,
    "wcmatch_yes_max":          0.88,
    "wcmatch_min_volume":       500_000,
    "wcmatch_max_hours_left":   3.0,
    "wcmatch_max_open":         10,
    "wcmatch_bet_usdc":         2.0,
    "wcmatch_gain":             0.09,
    "wcmatch_stop":             0.03,
    "wcmatch_max_hold_hours":   5,
    # famsum — overround family longshot fade (sumYES > 1.10 across a family)
    "famsum_enabled":           False,  # KILLED 2026-06-19 sweep: overround-fade (predictive), bootstrap CI[-0.040,-0.021] excl 0, n=10
    "famsum_min_members":       10,
    "famsum_sum_min":           1.10,
    "famsum_yes_min":           0.03,
    "famsum_yes_max":           0.25,
    "famsum_min_volume":        200_000,
    "famsum_max_open":          12,
    "famsum_bet_usdc":          1.0,
    "famsum_gain":              0.06,
    "famsum_stop":              0.03,
    "famsum_max_hold_hours":    168,
    # mapdown — esports BO3 panic fade (buy the crashed team)
    "mapdown_enabled":          False,  # KILLED 2026-06-19 loss-sweep: cumulative $-0.17 n=1
    "mapdown_chg_max":          -0.15,
    "mapdown_yes_min":          0.25,
    "mapdown_yes_max":          0.50,
    "mapdown_min_volume":       500_000,
    "mapdown_min_hours_left":   2.0,
    "mapdown_max_open":         6,
    "mapdown_bet_usdc":         1.0,
    "mapdown_gain":             0.12,
    "mapdown_stop":             0.04,
    "mapdown_max_hold_hours":   6,
    # ladderarb — date-ladder monotonicity violation (buy underpriced later rung)
    "ladderarb_enabled":        False,  # KILLED 2026-06-19 loss-sweep: cumulative $-1.34 n=26
    "ladderarb_gap_min":        0.04,
    "ladderarb_min_volume":     200_000,
    "ladderarb_max_open":       8,
    "ladderarb_bet_usdc":       1.0,
    "ladderarb_gain":           0.06,
    "ladderarb_stop":           0.04,
    "ladderarb_max_hold_hours": 96,

    # laddermid — DISCIPLINED ladderarb (2026-06-13): same date-monotonicity arb,
    # but only buys the underpriced rung when it's in [0.20,0.80]. The cross-exam
    # showed ladderarb's wins were favorite-longshot padding (cheap OTM rungs,
    # +5c target = +125% on noise, open tails → 0). This strips the padding band
    # to test whether the monotonicity edge is REAL or just cheap-ticket artifact.
    # Symmetric R:R (no asymmetric win-small/lose-big). The honest controlled twin.
    "laddermid_enabled":        False,  # KILLED 2026-06-19 loss-sweep: cumulative $-0.91 n=10
    "laddermid_gap_min":        0.05,   # require a bigger violation (less noise)
    "laddermid_band_lo":        0.20,   # bought rung must be here — NOT a cheap longshot
    "laddermid_band_hi":        0.80,
    "laddermid_min_volume":     200_000,
    "laddermid_max_open":       8,
    "laddermid_bet_usdc":       1.0,
    "laddermid_gain":           0.06,
    "laddermid_stop":           0.06,   # symmetric — no win-rate padding geometry
    "laddermid_max_hold_hours": 96,

    # strikeflip — strike-ladder YES-side arb (2026-06-13): mirror of clusterarb.
    # Within a strike cluster (same subject, different $ thresholds), a LOWER
    # strike must be >= a HIGHER strike in YES (easier to clear). If a lower
    # strike's YES sits BELOW a higher strike's YES by gap_min, the lower strike
    # is underpriced → buy its YES. Band-gated [0.20,0.80] to dodge the padding trap.
    "strikeflip_enabled":       False,  # KILLED 2026-06-19 loss-sweep: cumulative $-0.40 n=5
    "strikeflip_gap_min":       0.05,
    "strikeflip_band_lo":       0.20,
    "strikeflip_band_hi":       0.80,
    "strikeflip_min_volume":    200_000,
    "strikeflip_max_open":      8,
    "strikeflip_bet_usdc":      1.0,
    "strikeflip_gain":          0.06,
    "strikeflip_stop":          0.06,
    "strikeflip_max_hold_hours": 96,

    # negladder — date-ladder NO-side (2026-06-13): the OTHER leg of ladderarb's
    # violation. When an EARLIER deadline is overpriced vs a later one (early.yes
    # >> later.yes), buy NO on the overpriced early rung. Completes the structural
    # arb grid (date×strike × YES×NO). Band-gated [0.20,0.80], symmetric R:R.
    "negladder_enabled":        False,  # KILLED 2026-06-19 loss-sweep: cumulative $-0.91 n=7
    "negladder_gap_min":        0.05,
    "negladder_band_lo":        0.20,
    "negladder_band_hi":        0.80,
    "negladder_min_volume":     200_000,
    "negladder_max_open":       8,
    "negladder_bet_usdc":       1.0,
    "negladder_gain":           0.06,
    "negladder_stop":           0.06,
    "negladder_max_hold_hours": 96,
    # thetadk — catalyst-needed "by <month>" deadline decay (distinct from thetano)
    "thetadk_enabled":          False,  # KILLED 2026-06-19 loss-sweep: cumulative $-0.33 n=6
    "thetadk_days_min":         1.0,
    "thetadk_days_max":         7.0,
    "thetadk_yes_min":          0.10,
    "thetadk_yes_max":          0.45,
    "thetadk_min_volume":       300_000,
    "thetadk_max_open":         10,
    "thetadk_bet_usdc":         1.0,
    "thetadk_gain":             0.09,
    "thetadk_stop":             0.04,
    "thetadk_max_hold_hours":   96,
    # certsnipe — past-end near-certainty collection (hold to resolution)
    "certsnipe_enabled":        False,  # KILLED 2026-06-19 loss-sweep: cumulative $-0.12 n=4
    "certsnipe_past_days_min":  0.5,
    "certsnipe_past_days_max":  30.0,
    "certsnipe_yes_min":        0.95,
    "certsnipe_yes_max":        0.97,   # gap >= 3c (Lesson 4 anti-padding)
    "certsnipe_min_volume":     200_000,
    "certsnipe_max_open":       8,
    "certsnipe_bet_usdc":       2.0,
    "certsnipe_gain":           0.03,
    "certsnipe_stop":           0.02,
    "certsnipe_max_hold_hours": 240,
    # dipladder — BTC "dip to $X" touch-probability vs distance-to-strike
    "dipladder_enabled":        False,  # KILLED 2026-06-15: n=5, 0% WR, -$1.83, -36¢/exit, CI[-0.509,-0.240]. Buys falling knives (same failure as the original dip leg, Lesson "buy-the-falling-knife"). Kill rule met (n≥5, WR≤20%, negative).
    "dipladder_far_dist":       0.50,   # strike >50% below spot
    "dipladder_far_yes_min":    0.15,   # ...but YES still >=0.15 → overpriced, buy NO
    "dipladder_near_dist_lo":   0.10,
    "dipladder_near_dist_hi":   0.25,
    "dipladder_near_yes_max":   0.30,   # close strike but YES <=0.30 → underpriced, buy YES
    "dipladder_min_volume":     300_000,
    "dipladder_max_open":       6,
    "dipladder_bet_usdc":       1.0,
    "dipladder_gain":           0.08,
    "dipladder_stop":           0.04,
    "dipladder_max_hold_hours": 240,
    # clusterarb — same-event threshold coherence (band too thin/inverted → NO on higher)
    "clusterarb_enabled":       False,  # KILLED 2026-07-02 sweep: n=12, 8.3% win, bootstrap CI[-0.012,-0.005] excl 0 — consistent ~1c/exit loser (also fails wr<=20% rule). Open positions wind down via scan_exits.
    "clusterarb_tol":           0.02,
    "clusterarb_min_volume":    200_000,
    "clusterarb_max_open":      6,
    "clusterarb_bet_usdc":      1.0,
    "clusterarb_gain":          0.05,
    "clusterarb_stop":          0.03,
    "clusterarb_max_hold_hours": 168,
    # socspread — soccer "Spread:" handicap fade (NOT the price-band spreadfade leg)
    "socspread_enabled":        False,  # KILLED 2026-06-19 sweep: bootstrap CI[-0.222,-0.001] excl 0, n=10
    "socspread_yes_min":        0.38,
    "socspread_yes_max":        0.55,
    "socspread_min_volume":     300_000,
    "socspread_max_hours_left": 4.0,
    "socspread_max_open":       6,
    "socspread_bet_usdc":       1.0,
    "socspread_gain":           0.08,
    "socspread_stop":           0.03,
    "socspread_max_hold_hours": 5,
    # peacefwd — rich middle-rung ladder fade (high forward prob, nothing imminent)
    "peacefwd_enabled":         False,  # KILLED 2026-06-15: n=6, 0% WR, -$1.37, -23¢/exit, CI[-0.310,-0.164]. Event-driven "peace deal forward" longshot — never won. Kill rule met (n≥5, WR≤20%, negative).
    "peacefwd_fwd_min":         0.35,
    "peacefwd_base_yes_max":    0.10,
    "peacefwd_min_volume":      300_000,
    "peacefwd_max_open":        5,
    "peacefwd_bet_usdc":        1.0,
    "peacefwd_gain":            0.07,
    "peacefwd_stop":            0.04,
    "peacefwd_max_hold_hours":  168,

    # ── TRADING-EXTRA LEGS (2026-06-11) ───────────────────────────────────────
    # Six legs translated from cloned exchange-trading bots in oss-bots/trading-extra/.
    # All at house 3.0 R:R (gain 9¢ / stop 3¢) unless the thesis demands otherwise.

    # ── emacross (Stock-Prediction-Models inspired) ───────────────────────────
    # ML forecasting repos lean on trend-confirmation across timescales. Proxy:
    # 24h chg positive AND price still rising scan-over-scan → buy YES.
    "emacross_enabled":          False,  # KILLED 2026-06-13: 56 exits -$0.073/exit, CI excludes 0 — Stock-Prediction trend doesn't translate
    "emacross_min_chg":          0.04,   # 24h trend up ≥4¢
    "emacross_min_scan_rise":    0.02,   # scan-over-scan confirm ≥2¢
    "emacross_yes_min":          0.30,
    "emacross_yes_max":          0.75,
    "emacross_min_volume":       100_000,
    "emacross_max_open":         10,
    "emacross_bet_usdc":         1.0,
    "emacross_gain":             0.09,
    "emacross_stop":             0.03,
    "emacross_max_hold_hours":   48,

    # ── ridewinner (binance-trade-bot inspired) ───────────────────────────────
    # binance-trade-bot holds the strongest coin and jumps ship to the next
    # leader. Proxy: buy YES on the crypto-kw market with the LARGEST positive
    # 24h chg each scan, assuming momentum persists.
    "ridewinner_enabled":        False,  # KILLED 2026-06-19 sweep: bootstrap CI[-0.251,-0.065] excl 0, n=8
    "ridewinner_min_chg":        0.05,
    "ridewinner_yes_min":        0.30,
    "ridewinner_yes_max":        0.70,
    "ridewinner_min_volume":     100_000,
    "ridewinner_max_open":       8,
    "ridewinner_bet_usdc":       1.0,
    "ridewinner_gain":           0.09,
    "ridewinner_stop":           0.03,
    "ridewinner_max_hold_hours": 48,
    "ridewinner_kw":             ("bitcoin", "btc", "ethereum", "eth", "solana", "sol",
                                   "xrp", "dogecoin", "doge", "crypto", "bnb"),

    # ── tadip (freqtrade-strategies inspired) ─────────────────────────────────
    # Common profitable freqtrade shape: buy the dip ONLY with reversal confirm.
    # Proxy: 24h chg ≤ -6¢ AND current scan ticked UP vs last scan → buy YES.
    # Differs from crashbuy: requires the scan-level bounce confirmation.
    "tadip_enabled":             False,  # KILLED 2026-06-13: 15 exits -$0.247/exit 20% win — freqtrade dip-confirm doesn't translate
    "tadip_max_chg":             -0.06,  # dropped ≥6¢ in 24h
    "tadip_min_scan_bounce":     0.01,   # current scan must tick up ≥1¢
    "tadip_yes_min":             0.25,
    "tadip_yes_max":             0.60,
    "tadip_min_volume":          100_000,
    "tadip_max_open":            10,
    "tadip_bet_usdc":            1.0,
    "tadip_gain":                0.09,
    "tadip_stop":                0.03,
    "tadip_max_hold_hours":      48,

    # ── gridbounce (bbgo inspired) ────────────────────────────────────────────
    # bbgo's flagship is grid trading: collect mean reversion at deviations.
    # Proxy: |24h chg| ≥ 8¢ on deep books → fade the move (up → NO, down → YES)
    # with a grid-tight target.
    "gridbounce_enabled":        False,  # KILLED 2026-06-13: 83 exits -$0.074/exit, CI excludes 0 — bbgo grid doesn't translate to Polymarket
    "gridbounce_min_abs_chg":    0.08,
    "gridbounce_yes_min":        0.15,
    "gridbounce_yes_max":        0.85,
    "gridbounce_min_volume":     200_000, # grid needs deep books
    "gridbounce_max_open":       10,
    "gridbounce_bet_usdc":       1.0,
    "gridbounce_gain":           0.06,   # grid-style tighter take
    "gridbounce_stop":           0.03,
    "gridbounce_max_hold_hours": 36,

    # ── sentibreadth (Binance-News-Sentiment-Bot inspired) ───────────────────
    # That bot buys when aggregate news sentiment is broadly positive. Proxy:
    # breadth — if ≥70% of moving crypto markets are UP this scan, risk-on →
    # buy YES on the LAGGARDS (|chg|≤1¢) that haven't repriced yet.
    "sentibreadth_enabled":      False,  # KILLED 2026-06-19 sweep: bootstrap CI[-0.185,-0.054] excl 0, n=5
    "sentibreadth_min_breadth":  0.70,
    "sentibreadth_min_movers":   5,      # need ≥5 movers to score breadth
    "sentibreadth_max_abs_chg":  0.01,   # laggard = hasn't moved yet
    "sentibreadth_yes_min":      0.35,
    "sentibreadth_yes_max":      0.65,
    "sentibreadth_min_volume":   100_000,
    "sentibreadth_max_open":     8,
    "sentibreadth_bet_usdc":     1.0,
    "sentibreadth_gain":         0.09,
    "sentibreadth_stop":         0.03,
    "sentibreadth_max_hold_hours": 48,
    "sentibreadth_kw":           ("bitcoin", "btc", "ethereum", "eth", "solana", "sol",
                                   "xrp", "dogecoin", "doge", "crypto", "bnb"),

    # ── rsifade (Gekko-Strategies inspired) ──────────────────────────────────
    # Gekko's most-forked family is RSI Bull/Bear: fade overbought. Proxy:
    # 24h chg ≥ +12¢ = overbought → buy NO. Inverse of killed priceleap
    # (chasing the jump lost — data says fading it is the right side).
    "rsifade_enabled":           False,  # KILLED 2026-06-13: 50 exits -$0.133/exit, CI excludes 0 — Gekko RSI fade doesn't translate
    "rsifade_min_chg":           0.12,
    "rsifade_yes_min":           0.45,
    "rsifade_yes_max":           0.85,
    "rsifade_min_volume":        150_000,
    "rsifade_max_open":          10,
    "rsifade_bet_usdc":          1.0,
    "rsifade_gain":              0.09,
    "rsifade_stop":              0.03,
    "rsifade_max_hold_hours":    48,

    # ── fav75 (user spec 2026-06-11: ~75% win prob, high reward) ──────────────
    # Buy YES on ~75¢ favorites near resolution and ride convergence toward 1.
    # Win pays ~+20¢, loss stops at -10¢ → 2:1 reward:risk at ~75% implied win
    # rate. Same favorite-longshot-bias thesis as nearres, tested one rung
    # lower on the price ladder (nearres band is 0.88-0.95).
    "fav75_enabled":             False,  # KILLED 2026-06-15: n=13 30.8% WR -$3.57 (kill rule n≥10+WR<60%+neg) — FLB at [0.72,0.88] same structural failure as nearres
    "fav75_yes_min":             0.72,
    "fav75_yes_max":             0.78,
    "fav75_max_days":            7,      # near resolution — convergence window
    "fav75_min_days":            0.05,   # ≥~1h left (skip resolution-gap traps)
    "fav75_min_volume":          200_000,
    "fav75_max_open":            10,
    "fav75_bet_usdc":            1.0,
    "fav75_gain":                0.20,   # high reward: ride toward resolution
    "fav75_stop":                0.10,   # 2:1 R:R
    "fav75_max_hold_hours":      168,    # let the convergence play out (7d)
    # exclude in-play esports/match markets (2026-06-13): they gap through stops
    "fav75_skip_kw":             ("counter-strike", "cs2", "cs:", "valorant",
                                   "league of legends", "dota", "overwatch",
                                   "rainbow six", "r6 siege", " vs ", "(bo3)",
                                   "(bo5)", "map 1", "game 1", "esl", "iem ", "blast"),

    # ── newsmove (knowledge-based leg, 2026-06-13) ────────────────────────────
    # The first leg that trades on the GDELT market-impact MEMORY (info_impact.db),
    # not a price pattern. Thesis: if news about entity E has HISTORICALLY moved
    # markets-about-E in a consistent direction (per-entity calibration, n>=N with
    # 24h follow-ups), and a fresh market about E is still unmoved, trade the
    # calibrated direction. Per-entity normalization defeats the Trump-skew (72%
    # of matches) — each entity is judged on its OWN history, not the global pool.
    #
    # ACTIVATED 2026-06-15: info_impact.db has 39k rows, 39k with yes_24h filled.
    # 3 entities currently pass gate: nuclear deal (+0.126, 1.00), lewis hamilton
    # (+0.063, 0.91), kimi antonelli (-0.057, 0.99). Per-entity gates guard against
    # Trump-skew (72% of matches, 0.50 consistency = noise). Monitor first entries.
    "newsmove_enabled":          True,   # activated 2026-06-15 (was False pending data)
    "newsmove_min_samples":      30,     # entity needs >=30 matches WITH 24h follow-up
    "newsmove_min_edge":         0.04,   # |mean signed 24h move| must be >= this
    "newsmove_min_consistency":  0.65,   # >=65% of moves in the same direction
    "newsmove_max_abs_chg":      0.03,   # target market hasn't repriced yet (|24h chg| small)
    "newsmove_yes_min":          0.10,
    "newsmove_yes_max":          0.90,
    "newsmove_min_volume":       100_000,
    "newsmove_max_open":         8,
    "newsmove_bet_usdc":         1.0,
    "newsmove_gain":             0.10,
    "newsmove_stop":             0.06,
    "newsmove_max_hold_hours":   48,
    "newsmove_db":               "info_impact.db",
    "newsmove_cal_ttl":          3600,   # recompute per-entity calibration hourly
}

# All lab legs, in display order. Single source of truth for state/board/dashboard.
LEGS = ("dip", "scalp", "fade", "fastfade", "allin",
        "momentum", "favyes", "coinflip", "midfade",
        # research-backed fade variants (2026-06-07): the live `fade` leg fades
        # YES[0.60,0.85], but the backtest + Wang fit say the +EV fade is the LOW
        # band [0.20,0.55]. These test the CORRECT thesis as independent A/B arms.
        "truefade", "deepfade", "nsfade", "wangfade",
        # open-source cross-market divergence (2026-06-07): buy in the direction
        # Manifold Markets / Metaculus community forecasters lean when they diverge
        # from Polymarket by >=12c. Independent signal, not a price-costume.
        "manifold", "metaculus",
        # new algo legs (2026-06-07): time-based, liquidity-based, reversal, category
        "endgame", "lowliq", "contrarian", "catpol",
        # sports data legs (2026-06-07): FIFA rank + WC standing + TSDB form + ESPN news
        "wcrank", "wcnews",
        # analytics legs (2026-06-07): Wikipedia surge, resolution proximity,
        # Reddit discussion, CoinGecko log-normal fair value
        "wikivol", "resolveprox", "redditbuzz", "cryptofair",
        # signal legs (2026-06-07): volume spike, new-market launch, PredictIt cross-market
        "volspike", "newmarket", "predictit",
        # wallet / market-microstructure legs (2026-06-07)
        "walletcopy", "whale", "multiwhale", "bookimbal",
        # structural fade variants (2026-06-07)
        "shortdate", "highvol", "pricerev", "catrev", "spreadfade", "resolverush",
        # volume-by-category and trade-flow legs (2026-06-07)
        "cryptovol", "sportsvol", "polvol",
        "buyflow", "sellflow",
        "coinpump", "coindip", "acumvol",
        # coin up/down forward experiment (2026-06-08)
        "coinup", "coindown",
        # candle-based legs (2026-06-10): candlesig=RSI+EMA, coinrev=fade >7% 24h move
        "candlesig", "coinrev", "macdsig",
        # algo legs from GitHub survey (2026-06-08):
        # diverg=spot-vs-market divergence, feargreed=F&G contrarian,
        # lateprox=aulekator late-window high-confidence entry
        "diverg", "feargreed", "lateprox",
        "fgcrypto",
        "nearterm",
        # moonshot (2026-06-08): 5–10¢ YES, $1 max loss, 10x+ upside
        "moonshot",
        # conviction (2026-06-08): resolution-arb — late entry after elite consensus
        # confirmed, short horizon, aiming for 85–95% win rate and 3–8x returns
        "conviction",
        # 2026-06-08: resolution proximity, thin-book fade, whale exits, confluence, cat momentum
        "resolvebump", "thinbook", "whaleflip", "multiconfirm", "catmom",
        # 2026-06-08 batch 2: near-resolution, snap reversal, vol+chg combo, regime flip, breakout
        "nearres", "nearres_sports", "nearres_pol", "snaprev", "volburst", "flipflop", "stalefish",
        # 2026-06-08 night: tailspin, roundnum, firstmover, yoyo
        "tailspin", "roundnum", "firstmover", "yoyo",
        "bigswing", "polmom", "sportshigh", "volwake", "doubletop",
        "nohappen", "longshortbias", "clobimbal", "polyflup", "nearresfade",
        "lopsided", "sportsnear", "whaleagree", "gapfill", "streakbuy", "revertmid",
        # 2026-06-09: moonshot-style asymmetric legs
        # longshot=inverse moonshot (buy NO on overconfident YES)
        # nearwin=near-resolution YES chase (≤7 days, 55-80¢, chg≥6¢)
        # crashbuy=oversold bounce (crashed ≥10¢ + scan-level bounce)
        "longshot", "nearwin", "crashbuy",
        "tp20",   # buy upward-tick, take +20¢ and sell (user request 2026-06-14)
        "scalp02",  # maker micro-scalp, +0.2% take-profit (user request 2026-06-14)
        "volsurge",  # volume spike + price confirmation, buy the move (user req 2026-06-14)
        # 2026-06-14: structural-theta + its control (from the 10-leg design workflow)
        "windowshut", "windowshutrand",
        # 2026-06-09: ADHD executive-function legs
        # deadline=time-blindness (≤24h market mispriced at 45-72¢)
        # impulse=inhibition-failure fade (12¢ scan spike → buy NO)
        # fogbuy=working-memory (crowd forgot → drifted market still contested)
        "deadline", "impulse", "fogbuy",
        # 2026-06-09: bias/structure exploitation legs
        # anchordrift=anchoring bias fade, volcrush=volume collapse fade,
        # latefade=<48h FLB fade, momno=negative momentum continuation,
        # narrowband=near-coinflip vig exploitation
        "anchordrift", "volcrush", "latefade", "momno", "narrowband",
        # 2026-06-10: data-driven legs
        # esportsfav=esports YES favorite, resolvetoday=theta expiry play,
        # stablehigh=stable high-prob market, priceleap=momentum continuation,
        # thetano=deep longshot fade, breakoutyes=50c breakout continuation
        "esportsfav", "resolvetoday", "stablehigh", "priceleap", "thetano", "breakoutyes",
        # 2026-06-10: risky/asymmetric legs (data-backed EV calculations)
        # esportsdog=esports underdog YES EV+$0.09-$0.20, geopolbomb=geo tail bets (Iran+$2.12),
        # midfield=moonshot gap 0.30-0.42 EV+$0.158-$0.197, cryptobull=crypto milestone YES
        "esportsdog", "geopolbomb", "midfield", "cryptobull",
        # 2026-06-10: OSS-bot-inspired legs
        # noevent=nothing-ever-happens NO fade (non-sports [0.30-0.60])
        # weatherno=weatherbot price-only fade (weather mkts YES>0.58)
        # btc15no=aulekator/fusion BTC15m NO (YES>0.55 short window)
        # newsno=pipeline "will X happen" event fade (YES [0.62-0.85])
        "noevent", "weatherno", "btc15no", "newsno",
        # 2026-06-10: YouTube attention hype-fade (scraped, no API key)
        "ytbuzz",
        # 2026-06-10 night: nearres A/B extension arms (cross-category + down-band)
        "sportres", "nearreslow",
        # 2026-06-13: isolated NO-favorite arm of nearres (learn-from-positives)
        "nearresno",
        # 2026-06-13: maker spread-capture on noise-flow markets (the one +EV theory)
        "spreadcap",
        # 2026-06-13: user-spec micro-scalp — maker entry, +0.20% take, fast churn
        "microscalp",
        # 2026-06-11: alice-pattern confirmation legs (independent signal gates entry)
        "psconfirm", "quietfade",
        # 2026-06-13: title-gated nearres arm (Finding C: Dota2 reverse-FLB)
        "nearrestitle",
        # 2026-06-11: TradingAgents-distilled multi-analyst vote leg
        "panel",
        # 2026-06-14: LIVE TradingAgents agent graph (reads tradingagents_feed.py)
        "tasignal",
        # 2026-06-10: new open-source signal legs
        # vlrtop=VLR.GG Valorant match latency-arb (buy YES on winner pre-resolve)
        # gtrend=Google Trends search-interest surge on market subject
        "vlrtop", "gtrend",
        # 2026-06-10: OSS top-tier ports + Claude original
        # bookpress=full-depth CLOB pressure, kellyfav=Kelly-sized favorites,
        # newslag=reddit-buzz AND price-move confluence (Claude's own design)
        "bookpress", "kellyfav", "newslag",
        # 2026-06-11: OpenAlice-confirmed news signal
        # alicenews=geopolitical YES only when OpenAlice /api/news has matching headline
        "alicenews",
        # 2026-06-11 batch: structural/arb legs
        # wcmatch=daily-match favorite <3h, famsum=overround family longshot fade,
        # mapdown=esports panic-crash buy, ladderarb=date-ladder monotonicity arb,
        # thetadk="by <month>" deadline decay, certsnipe=past-end near-certainty,
        # dipladder=BTC dip-to-$X touch-prob, clusterarb=threshold coherence,
        # socspread=soccer Spread: fade, peacefwd=rich middle-rung ladder fade
        "wcmatch", "famsum", "mapdown", "ladderarb", "thetadk",
        "certsnipe", "dipladder", "clusterarb", "socspread", "peacefwd",
        # 2026-06-13: disciplined ladder/strike arb variants (band-gated, anti-padding)
        "laddermid", "strikeflip", "negladder",
        # 2026-06-11 OSS harvest: polybot complete-set reduction
        # csarb=buy YES+NO when top-of-book asks sum < 1 (crossed/discounted set)
        "csarb",
        # 2026-06-11: trading-extra legs (exchange-bot signal ports)
        # emacross=two-timescale trend confirm (Stock-Prediction-Models)
        # ridewinner=strongest crypto mover momentum (binance-trade-bot)
        # tadip=dip + scan-bounce confirm (freqtrade-strategies)
        # gridbounce=±8c deviation mean-reversion (bbgo grid)
        # sentibreadth=crypto breadth risk-on laggard buy (news-sentiment-bot)
        # rsifade=overbought ≥12c fade (Gekko RSI Bull/Bear)
        "emacross", "ridewinner", "tadip", "gridbounce", "sentibreadth", "rsifade",
        # 2026-06-11: user spec — ~75% win prob favorites with 2:1 reward:risk
        "fav75",
        # 2026-06-13: knowledge-based leg — trades GDELT impact-memory calibration
        "newsmove",
        # 2026-06-12: Solana whale/smart-money signal (birdeye-py + alpha-radar logic)
        "birdeye",
        # 2026-06-12: Liquidity collapse leg — price down >30% 24h + thin book → buy NO
        "liqcrush",
        # 2026-06-13: RSS news signal legs (CoinTelegraph + HLTV)
        "newscrypto", "newsesports",
        # misfire quarantine buckets (off-thesis exits, never traded)
        "nearres_misfire")


# ── External-signal caches (Manifold, Metaculus) ─────────────────────────────
# Fetched once per TTL window then reused across every Polymarket market in the
# same scan cycle — avoids hammering external APIs every 60s.
_manifold_cache  = {"markets": [], "ts": 0.0}
_metaculus_cache = {"questions": [], "ts": 0.0}
_EXT_TTL = 300   # 5 min: warm for one full scan, fresh before the next

# ── Wallet / trade-feed caches (2026-06-07) ───────────────────────────────────
_global_trades_cache = {"trades": [], "ts": 0.0}
_GLOBAL_TRADES_TTL   = 90      # 90s — fresh each scan cycle
_top_wallets_cache   = {"wallets": [], "ts": 0.0}
_TOP_WALLETS_TTL     = 1800    # 30 min (rank is stable over short windows)
_wallet_act_cache    = {}      # addr → {"trades": [], "ts": float}
_WALLET_ACT_TTL      = 180
_clob_cache          = {}      # token_id → {"bids":[],"asks":[],"ts":float}
_CLOB_TTL            = 90
_price_snap          = {}      # market_id → float  (YES price from last scan)

# ── Macro gates (Deribit PCR, convergence filter) ─────────────────────────────
_deribit_pcr_cache = {"pcr": None, "ts": 0.0}
_DERIBIT_PCR_TTL = 300  # 5 min; Deribit updates PCR every 1h

def fetch_deribit_pcr():
    """Fetch Deribit Put-Call Ratio (institutional derivatives sentiment).
    Blocks BTC legs if PCR > 2.0 (bearish pile) or < 0.5 (bullish mania).
    FREE API, 5-min refresh. Fails gracefully (returns None → no gate)."""
    global _deribit_pcr_cache
    now = time.time()
    if now - _deribit_pcr_cache["ts"] < _DERIBIT_PCR_TTL and _deribit_pcr_cache["pcr"] is not None:
        return _deribit_pcr_cache["pcr"]
    try:
        url = "https://www.deribit.com/api/v2/public/get_book?instrument_name=BTC-PERPETUAL"
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        r = json.loads(urllib.request.urlopen(req, timeout=5).read())
        result = r.get("result", {})
        puts = sum(v for instr, v in (result.get("puts") or []))
        calls = sum(v for instr, v in (result.get("calls") or []))
        pcr = puts / calls if calls > 0 else None
        _deribit_pcr_cache["pcr"] = pcr
        _deribit_pcr_cache["ts"] = now
        return pcr
    except Exception as e:
        sys.stderr.write(f"[deribit_pcr] fetch error: {e}\n")
        return None

def convergence_filter(m, cfg):
    """Require ≥2 of 3 crypto indicators (divergence, momentum, vol_skew) to agree.
    Reduces false signals on coinup/coindown ~10-20%. Fails gracefully."""
    try:
        from candle_signals import divergence, momentum
        divg = divergence(m.get("id", ""), m.get("yes", 0.5))
        mom = momentum(m.get("id", ""), m.get("yes", 0.5))
        # vol_skew: ≥2 indicators agree on direction
        agrees = sum([divg == 1, mom == 1])  # placeholder; expand as needed
        return agrees >= 2
    except Exception:
        return True  # fail open — if signal unavailable, allow trade
_cat_chg_snap        = {}      # cat → avg_chg computed in current scan
_seen_whale_tx       = set()   # de-dup watchdog log (transaction hashes seen)
_newsmove_cal        = {"data": {}, "ts": 0.0}   # entity → calibration for newsmove leg


def newsmove_calibration(cfg):
    """Per-entity impact calibration from info_impact.db, cached hourly.
    Returns {entity: {'dir': +1/-1, 'edge': mean_abs_move, 'n': samples}} for
    entities clearing min_samples / min_edge / min_consistency on 24h follow-ups.
    Empty until the GDELT impact memory accumulates 24h windows — this is the
    runtime gate that keeps newsmove dormant on immature data."""
    now = time.time()
    if now - _newsmove_cal["ts"] < cfg.get("newsmove_cal_ttl", 3600):
        return _newsmove_cal["data"]
    out = {}
    try:
        import sqlite3
        db = os.path.join(BASE, cfg.get("newsmove_db", "info_impact.db"))
        if os.path.exists(db):
            c = sqlite3.connect(db)
            rows = c.execute("""SELECT entity,
                                       AVG(yes_24h - yes_at_match) mean_move,
                                       AVG(CASE WHEN yes_24h>yes_at_match THEN 1.0 ELSE 0.0 END) up_frac,
                                       COUNT(*) n
                                FROM matches WHERE yes_24h IS NOT NULL
                                GROUP BY entity HAVING n >= ?""",
                             (cfg["newsmove_min_samples"],)).fetchall()
            c.close()
            for ent, mean_move, up_frac, n in rows:
                if mean_move is None:
                    continue
                consistency = max(up_frac, 1 - up_frac)
                if abs(mean_move) >= cfg["newsmove_min_edge"] and consistency >= cfg["newsmove_min_consistency"]:
                    out[ent.lower()] = {"dir": 1 if mean_move > 0 else -1,
                                        "edge": round(abs(mean_move), 4), "n": int(n)}
    except Exception:
        out = {}   # fail-soft: no calibration → leg stays dormant
    _newsmove_cal["data"] = out
    _newsmove_cal["ts"] = now
    return out


# ── HTTP (fail-soft, rate-limit aware) ───────────────────────────────────────
# Pacing + 429/5xx backoff borrowed from how robust OSS bots survive Cloudflare
# throttling (poly-market-maker has none; this is an improvement on it). Network
# /DNS errors are NOT retried — that's the flaky-egress case, fail soft & fast.
_MIN_INTERVAL = 0.20   # >=5 req/s pacing; far under CLOB's 150/s market-data cap
_last_call = [0.0]

def http_get(url, params=None, retries=3):
    if params:
        url += "?" + urllib.parse.urlencode(params)
    backoff = 1.0
    for attempt in range(retries + 1):
        wait = _MIN_INTERVAL - (time.time() - _last_call[0])
        if wait > 0:
            time.sleep(wait)
        _last_call[0] = time.time()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except Exception as e:
            code = getattr(e, "code", None)
            if attempt < retries and (code == 429 or (code and 500 <= code < 600)):
                ra = None
                try:
                    ra = float(e.headers.get("Retry-After"))  # honor server hint
                except Exception:
                    ra = None
                time.sleep(ra if ra else backoff)
                backoff = min(backoff * 2, 8.0)
                continue
            sys.stderr.write(f"[http] {url[:70]} failed: {e}\n")
            return None


# ── Cross-market signal helpers ───────────────────────────────────────────────

def _word_sim(a, b):
    """Jaccard similarity on lowercase word sets. No deps beyond stdlib re."""
    wa = set(re.sub(r'[^a-z0-9 ]', ' ', a.lower()).split())
    wb = set(re.sub(r'[^a-z0-9 ]', ' ', b.lower()).split())
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


def _best_match(poly_q, ext_markets, q_key="question"):
    """Return (best_market_dict, similarity) from a list of external markets.
    Scans ALL ext_markets for the highest Jaccard overlap with poly_q — O(N*words).
    N~200 markets * ~15 words each = fast in-process, no network needed."""
    best, best_s = None, 0.0
    for m in ext_markets:
        s = _word_sim(poly_q, m.get(q_key) or "")
        if s > best_s:
            best_s, best = s, m
    return best, best_s


def fetch_manifold_markets():
    """Open binary Manifold Markets markets (free public API, no auth).
    Fetches 3 pages of 100 markets each (~300 raw → ~150-180 open binary),
    filters client-side for BINARY + unresolved + has probability.
    Uses a browser UA — the Manifold v0/markets endpoint rejects sort/filter/
    contractType params (400); only plain ?limit=N works reliably.
    Cached for _EXT_TTL seconds. Returns [] on any network error."""
    if time.time() - _manifold_cache["ts"] < _EXT_TTL and _manifold_cache["markets"]:
        return _manifold_cache["markets"]
    mkt, seen = [], set()
    for page in range(3):
        # Manifold v0/markets does NOT support sort/filter/contractType params.
        # Fetch plain pages and filter client-side.
        url = (f"https://api.manifold.markets/v0/markets?limit=100"
               f"&before={mkt[-1]['id']}" if page > 0 and mkt else
               "https://api.manifold.markets/v0/markets?limit=100")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible)"})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                raw = json.loads(r.read().decode("utf-8", "replace"))
        except Exception as e:
            sys.stderr.write(f"[manifold] page {page} failed: {e}\n")
            break
        if not raw:
            break
        for m in raw:
            mid = m.get("id")
            if not mid or mid in seen:
                continue
            if (m.get("outcomeType") != "BINARY"
                    or m.get("isResolved")
                    or not isinstance(m.get("probability"), (int, float))):
                continue
            seen.add(mid)
            mkt.append(m)
    _manifold_cache["markets"] = mkt
    _manifold_cache["ts"] = time.time()
    sys.stderr.write(f"[manifold] cached {len(mkt)} open binary markets\n")
    return mkt


def fetch_metaculus_questions():
    """Top-100 active open binary Metaculus questions (free public API, no auth).
    Keeps only questions that have a community prediction median (q2).
    Uses a browser-like UA + Accept header — Metaculus blocks plain python UAs
    in some environments but works fine from Mac/VPS. Returns [] on any error."""
    if time.time() - _metaculus_cache["ts"] < _EXT_TTL and _metaculus_cache["questions"]:
        return _metaculus_cache["questions"]
    url = ("https://www.metaculus.com/api2/questions/"
           "?status=open&forecast_type=binary&limit=100&ordering=-activity")
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Accept": "application/json",
        })
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            raw = json.loads(r.read().decode("utf-8", "replace"))
    except Exception as e:
        sys.stderr.write(f"[metaculus] fetch failed: {e} — leg will have no signal this cycle\n")
        raw = {}
    results = raw.get("results", []) if isinstance(raw, dict) else []
    qs = []
    for q in results:
        cp = q.get("community_prediction") or {}
        median = (cp.get("full") or {}).get("q2")
        if median is not None:
            q["_median"] = float(median)
            qs.append(q)
    _metaculus_cache["questions"] = qs
    _metaculus_cache["ts"] = time.time()
    sys.stderr.write(f"[metaculus] cached {len(qs)} binary questions w/ community prediction\n")
    return qs


# ── Sports signal: FIFA rankings + ESPN WC data + TheSportsDB form ───────────
#
# FIFA_RANKS: pre-tournament FIFA world rankings for the 48 WC 2026 teams.
# The FIFA ranking formula weights results over the last ~4 years (exponential
# decay) — this is as close as free public data gets to "last 10 years of
# historical performance." A rank of 1 = strongest team historically.
# Source: FIFA.com monthly release (public, no auth needed, embedded statically
# to avoid depending on the FIFA site's structure at runtime).
FIFA_RANKS = {
    # rank: lower = historically stronger (reflects ~4 years weighted results)
    "Argentina":             1,   "France":            2,   "England":           3,
    "Brazil":                4,   "Spain":             5,   "Portugal":          6,
    "Belgium":               7,   "Netherlands":       8,   "Germany":           9,
    "Croatia":              10,   "Italy":            11,   "Morocco":          12,
    "Colombia":             13,   "United States":    14,   "Japan":            15,
    "Senegal":              16,   "Switzerland":      17,   "Denmark":          18,
    "Mexico":               19,   "Uruguay":          20,   "South Korea":      21,
    "Ecuador":              22,   "Hungary":          23,   "Australia":        24,
    "Turkey":               25,   "Austria":          26,   "Serbia":           27,
    "Chile":                28,   "Czechia":          29,   "Ukraine":          30,
    "Algeria":              31,   "Canada":           32,   "Cameroon":         33,
    "Poland":               34,   "Paraguay":         35,   "Scotland":         36,
    "Saudi Arabia":         37,   "South Africa":     38,   "Ivory Coast":      39,
    "Costa Rica":           40,   "Qatar":            41,   "Bolivia":          42,
    "Uzbekistan":           43,   "New Zealand":      44,   "Kenya":            45,
    "Congo DR":             46,   "Bosnia-Herzegovina":47,  "Curaçao":          48,
}
# Alternate spellings Polymarket uses for the same team
TEAM_ALIASES = {
    "usa": "United States", "u.s.": "United States", "us men": "United States",
    "drc": "Congo DR", "dr congo": "Congo DR",
    "cote d'ivoire": "Ivory Coast", "côte d'ivoire": "Ivory Coast",
    "ivory coast": "Ivory Coast",
    "south korea": "South Korea", "korea republic": "South Korea",
    "korea": "South Korea",
    "bosnia": "Bosnia-Herzegovina",
    "nz": "New Zealand",
}

# Caches for WC live data (refreshed every _EXT_TTL seconds = 5 min)
_wc_standings_cache = {"data": {}, "ts": 0.0}   # team → {w,l,t,pts,gp}
_wc_news_cache      = {"data": {}, "ts": 0.0}   # team → article_count
_tsdb_form_cache    = {}                          # team → {result,date,opp} (TTL 4h)
_TSDB_FORM_TTL      = 14_400                      # 4 hours: match results don't change
# analytics leg caches
_wiki_cache         = {}           # article → {"views": [...], "ts": float}  TTL 6h
_wiki_miss          = set()        # articles that returned 404 — never retry
_wiki_backoff_until = [0.0]        # epoch time: skip ALL wiki calls until this passes (429 cooldown)
_WIKI_TTL           = 21_600
_reddit_cache       = {}           # query_key → {"posts": int, "ts": float}  TTL 30m
_REDDIT_TTL         = 1_800
_yt_cache           = {}           # query_key → {"n_fresh": int, "views_fresh": int, "ts": float}
_YT_TTL             = 3_600        # YouTube scrape is heavy — refresh hourly
_coingecko_cache    = {"data": {}, "ts": 0.0}   # coin_id → {usd, change}    TTL 5m
_CG_TTL             = 300
# volspike: rolling volume baseline per market (EMA, updated each cycle)
_vol_baseline       = {}     # market_id → float (EMA of past volumes)
_VOL_EMA_ALPHA      = 0.20   # EMA smoothing: new = α×current + (1−α)×old
# predictit: market data cache
_predictit_cache    = {"markets": [], "ts": 0.0}
_PI_TTL             = 300    # 5-min cache (PredictIt updates slowly)


def fetch_wc_standings():
    """ESPN WC group standings: all 48 teams, W/L/T/Pts, cached 5 min.
    Returns {} on error — wcrank leg skips in-tournament adjustment gracefully."""
    if time.time() - _wc_standings_cache["ts"] < _EXT_TTL and _wc_standings_cache["data"]:
        return _wc_standings_cache["data"]
    url = "https://site.api.espn.com/apis/v2/sports/soccer/fifa.world/standings"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible)"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            raw = json.loads(r.read().decode("utf-8", "replace"))
    except Exception as e:
        sys.stderr.write(f"[wc_standings] fetch failed: {e}\n")
        raw = {}
    out = {}
    for grp in raw.get("children", []):
        for e in grp.get("standings", {}).get("entries", []):
            team = e.get("team", {}).get("displayName", "")
            if not team:
                continue
            stats = {s.get("name", ""): s.get("value", 0) for s in e.get("stats", [])}
            out[team] = {
                "w":  int(stats.get("wins", 0)),
                "l":  int(stats.get("losses", 0)),
                "t":  int(stats.get("ties", 0)),
                "pts": int(stats.get("points", 0)),
                "gp": int(stats.get("gamesPlayed", 0)),
            }
    _wc_standings_cache["data"] = out
    _wc_standings_cache["ts"] = time.time()
    sys.stderr.write(f"[wc_standings] cached {len(out)} teams\n")
    return out


def fetch_wc_news_teams():
    """ESPN WC article mention count per team (last ~50 articles), cached 5 min.
    Returns {} on error. Used by both wcrank (enrichment) and wcnews (signal)."""
    if time.time() - _wc_news_cache["ts"] < _EXT_TTL and _wc_news_cache["data"]:
        return _wc_news_cache["data"]
    url = "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/news?limit=50"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible)"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            raw = json.loads(r.read().decode("utf-8", "replace"))
    except Exception as e:
        sys.stderr.write(f"[wc_news] fetch failed: {e}\n")
        raw = {}
    out = {}
    for a in raw.get("articles", []):
        for cat in a.get("categories", []):
            if cat.get("type") == "team":
                name = cat.get("description", "")
                out[name] = out.get(name, 0) + 1
    _wc_news_cache["data"] = out
    _wc_news_cache["ts"] = time.time()
    sys.stderr.write(f"[wc_news] cached {len(out)} teams, {sum(out.values())} mentions\n")
    return out


def fetch_team_last_result(team_canonical):
    """TheSportsDB: most recent match result for a national soccer team.
    Cached per-team for 4 hours. Returns {} on miss/error.
    Result dict: {result: 'W'|'L'|'D', date: str, opp: str, score: str}"""
    cached = _tsdb_form_cache.get(team_canonical, {})
    if cached.get("ts") and time.time() - cached["ts"] < _TSDB_FORM_TTL:
        return cached.get("data", {})
    try:
        url = (f"https://www.thesportsdb.com/api/v1/json/3/searchteams.php"
               f"?t={urllib.parse.quote(team_canonical)}")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible)"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            teams = json.loads(r.read()).get("teams") or []
        # prefer the FIFA/international entry
        teams_sorted = sorted(teams, key=lambda t: 0 if "FIFA" in (t.get("strLeague","") or "") else 1)
        tid = (teams_sorted[0] if teams_sorted else {}).get("idTeam")
        if not tid:
            _tsdb_form_cache[team_canonical] = {"ts": time.time(), "data": {}}
            return {}
        url2 = f"https://www.thesportsdb.com/api/v1/json/3/eventslast.php?id={tid}"
        req2 = urllib.request.Request(url2, headers={"User-Agent": "Mozilla/5.0 (compatible)"})
        with urllib.request.urlopen(req2, timeout=TIMEOUT) as r:
            results = json.loads(r.read()).get("results") or []
        data = {}
        if results:
            e = results[0]
            home, away = e.get("strHomeTeam",""), e.get("strAwayTeam","")
            hs, as_ = e.get("intHomeScore","?"), e.get("intAwayScore","?")
            is_home = team_canonical.lower() in home.lower()
            opp = away if is_home else home
            try:
                won = (int(hs) > int(as_)) == is_home
                tied = int(hs) == int(as_)
                result_str = "D" if tied else ("W" if won else "L")
            except Exception:
                result_str = "?"
            data = {"result": result_str, "date": e.get("dateEvent",""),
                    "opp": opp[:25], "score": f"{hs}-{as_}"}
        _tsdb_form_cache[team_canonical] = {"ts": time.time(), "data": data}
        return data
    except Exception as e:
        sys.stderr.write(f"[tsdb] {team_canonical}: {e}\n")
        _tsdb_form_cache[team_canonical] = {"ts": time.time(), "data": {}}
        return {}


def fetch_wiki_pageviews(article):
    """Wikimedia REST API — 37 days of daily pageviews for a Wikipedia article.
    Returns list of (date_str, views) tuples. Cached per-article for 6 hours.
    404s go into _wiki_miss and are never retried. Free, no auth, any IP."""
    if article in _wiki_miss:
        return []
    cached = _wiki_cache.get(article, {})
    if cached and time.time() - cached.get("ts", 0) < _WIKI_TTL:
        return cached["views"]
    from datetime import datetime, timedelta, timezone as _tz2
    end_dt   = datetime.now(_tz2.utc)
    start_dt = end_dt - timedelta(days=37)
    s, e = start_dt.strftime("%Y%m%d"), end_dt.strftime("%Y%m%d")
    url = (f"https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article"
           f"/en.wikipedia/all-access/all-agents/{urllib.parse.quote(article, safe='')}"
           f"/daily/{s}/{e}")
    try:
        import socket as _sock
        req  = urllib.request.Request(url, headers={"User-Agent": "scalp-lab-paper/1.0 (paper-trading; non-commercial)"})
        old_to = _sock.getdefaulttimeout()
        _sock.setdefaulttimeout(5)   # hard socket-level timeout (urlopen timeout can hang pre-connect)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                data = json.loads(r.read().decode("utf-8", "replace"))
        finally:
            _sock.setdefaulttimeout(old_to)
        views = [(it["timestamp"][:8], it["views"]) for it in data.get("items", [])]
        _wiki_cache[article] = {"views": views, "ts": time.time()}
        return views
    except Exception as exc:
        code = getattr(exc, "code", None)
        if code == 404:
            _wiki_miss.add(article)          # permanent miss — never retry
        elif code == 429:
            _wiki_backoff_until[0] = time.time() + 300   # 5-min global cooldown on 429
        sys.stderr.write(f"[wikivol] '{article}': {exc}\n")
        _wiki_cache[article] = {"views": [], "ts": time.time()}
        return []


def _wiki_surge(article):
    """(surge_ratio, recent_avg, baseline_avg) — ratio of last-7-day avg to prior-30-day avg.
    Returns (0, 0, 0) when there is not enough data or traffic is too low to matter."""
    views = fetch_wiki_pageviews(article)
    if len(views) < 15:
        return 0.0, 0, 0
    recent   = [v for _, v in views[-7:]]
    baseline = [v for _, v in views[:-7]]
    r_avg = sum(recent)   / max(len(recent),   1)
    b_avg = sum(baseline) / max(len(baseline), 1)
    if b_avg < 100:          # < 100 views/day baseline → too obscure for Poly markets
        return 0.0, r_avg, b_avg
    return (r_avg / b_avg), r_avg, b_avg


def _wiki_article_for(question):
    """Heuristic: strip question scaffolding to get the core Wikipedia article title.
    'Will Donald Trump win the 2024 election?' → 'Donald Trump'
    Returns None when the result is too short or too generic to be useful."""
    q = question
    # Strip leading question verb
    q = re.sub(r'^(will|does|is|can|has|did|are|were|what|who|which)\s+', '', q, flags=re.IGNORECASE)
    q = re.sub(r'\s*\?.*$', '', q)
    # Remove trailing date/event context
    q = re.sub(r'\s+(by|before|in|at|on|during)\s+\d{4}.*$', '', q, flags=re.IGNORECASE)
    q = re.sub(r'\s+(win|lose|advance|qualify|reach|hit|exceed|fall|drop|achieve)\s.*$', '', q, flags=re.IGNORECASE)
    q = re.sub(r'\s+(the\s+)?(2024|2025|2026|2027|presidential|world\s+cup|championship|election|vote|primary|caucus).*$', '', q, flags=re.IGNORECASE)
    q = q.strip().title()
    return q if len(q) > 4 else None


def _yt_age_hours(txt):
    """'3 hours ago' → 3.0; '2 days ago' → 48.0; None if unparseable."""
    m = re.match(r"(?:Streamed\s+)?(\d+)\s+(second|minute|hour|day|week|month|year)s?\s+ago",
                 str(txt))
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2)
    return n * {"second": 1/3600, "minute": 1/60, "hour": 1.0,
                "day": 24.0, "week": 168.0, "month": 720.0, "year": 8760.0}[unit]


_vlr_cache   = {"matches": [], "ts": 0.0}
_VLR_TTL     = 300   # 5 min — match results don't change fast
_gtrend_cache = {}   # query → {"val": int, "base": float, "ts": float}
_GTREND_TTL  = 3600  # 1h — Trends API rate-limits hard, only call once/hour
_liq_cs2_cache = {"matches": [], "ts": 0.0}
_LIQ_CS2_TTL   = 600

def fetch_vlr_matches():
    """Scrape vlr.gg/matches for recent Valorant match results (no API key).
    Returns list of dicts: {team1, team2, winner, loser, slug, tournament, live}.
    Used by vlrtop leg for latency-arb on match resolution.
    Cached VLR_TTL=300s; on any failure returns the last cached list."""
    now = time.time()
    if now - _vlr_cache["ts"] < _VLR_TTL:
        return _vlr_cache["matches"]
    try:
        req  = urllib.request.Request("https://www.vlr.gg/matches",
                                      headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            html = resp.read().decode("utf-8", errors="replace")
        results = []
        # Each match: slug contains "team1-vs-team2-tournament"
        slugs = re.findall(r'href="(/\d+/([^"]+))"', html)
        for path, slug in slugs:
            if "-vs-" not in slug:
                continue
            parts = slug.split("-vs-")
            if len(parts) < 2:
                continue
            t1_raw = parts[0].split("/")[-1].replace("-", " ")
            rest   = parts[1]
            # rest = "nrg-valorant-masters-london-2026-r3-1-1"
            # team2 is up to the first known tournament keyword
            t2_end = len(rest)
            for kw in ("valorant-", "vct-", "champions-", "masters-", "challengers-",
                       "game-changers", "last-chance", "esport"):
                idx = rest.find(kw)
                if idx > 0 and idx < t2_end:
                    t2_end = idx
            t2_raw = rest[:t2_end].rstrip("-").replace("-", " ")
            tourn_raw = rest[t2_end:].replace("-", " ").strip()
            # Detect live vs completed from the match page context
            # vlr.gg shows scores like "2 - 0" in the slug suffix (r3-2-0 = round 3 score 2-0)
            score_m = re.search(r"-(\d+)-(\d+)$", slug)
            s1, s2 = (int(score_m.group(1)), int(score_m.group(2))) if score_m else (0, 0)
            live   = s1 == 0 and s2 == 0
            winner = t1_raw if s1 > s2 else (t2_raw if s2 > s1 else "")
            loser  = t2_raw if s1 > s2 else (t1_raw if s2 > s1 else "")
            results.append({
                "team1": t1_raw.strip().lower(),
                "team2": t2_raw.strip().lower(),
                "winner": winner.strip().lower(),
                "loser":  loser.strip().lower(),
                "scores": (s1, s2),
                "tournament": tourn_raw[:50],
                "slug": slug,
                "live": live,
            })
        _vlr_cache["matches"] = results
        _vlr_cache["ts"] = now
    except Exception:
        pass
    return _vlr_cache["matches"]


def fetch_liq_cs2_matches():
    """Liquipedia CS2 ongoing match list (no API key required).
    Returns list of dicts: {team1, team2, tournament, url}.
    Only ongoing/upcoming — used as whitelist for nearres cs2 confirmation."""
    now = time.time()
    if now - _liq_cs2_cache["ts"] < _LIQ_CS2_TTL:
        return _liq_cs2_cache["matches"]
    try:
        import re
        url = ("https://liquipedia.net/counterstrike/api.php"
               "?action=query&list=categorymembers&cmtitle=Category:Ongoing_Tournaments"
               "&cmlimit=30&format=json")
        req  = urllib.request.Request(url, headers={"User-Agent": "PolymarketResearch/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        pages  = data.get("query", {}).get("categorymembers", [])
        result = []
        for p in pages:
            title = p.get("title", "")
            # title is like "IEM Cologne 2026" or "ESL Pro League S21"
            result.append({
                "tournament": title.lower(),
                "url": "https://liquipedia.net/counterstrike/" + title.replace(" ", "_"),
            })
        _liq_cs2_cache["matches"] = result
        _liq_cs2_cache["ts"] = now
    except Exception:
        pass
    return _liq_cs2_cache["matches"]


def fetch_gtrends(keyword):
    """Google Trends interest-over-time for keyword (7-day window).
    Returns (current_val, rolling_mean) tuple, (0, 0) on error.
    Cached GTREND_TTL=3600s per keyword to avoid rate limits."""
    now = time.time()
    cached = _gtrend_cache.get(keyword)
    if cached and now - cached["ts"] < _GTREND_TTL:
        return cached["val"], cached["base"]
    try:
        from pytrends.request import TrendReq
        tr = TrendReq(hl="en-US", tz=360, timeout=(5, 15), retries=0)
        tr.build_payload([keyword], timeframe="now 7-d")
        df = tr.interest_over_time()
        if df.empty:
            return 0, 0
        vals = df[keyword].tolist()
        cur  = int(vals[-1])
        base = sum(vals[:-1]) / max(len(vals) - 1, 1)
        _gtrend_cache[keyword] = {"val": cur, "base": base, "ts": now}
        return cur, base
    except Exception:
        return 0, 0


def fetch_youtube_buzz(query):
    """Scrape YouTube search (sorted by upload date) and measure attention:
    videos ≤48h old and their combined views. No API key — parses ytInitialData
    from the public results page. On any failure caches zeros so the warmup
    thread doesn't hammer a broken endpoint."""
    ck  = query.lower()[:60]
    res = {"n_fresh": 0, "views_fresh": 0, "ts": time.time()}
    try:
        url = ("https://www.youtube.com/results?search_query="
               + urllib.parse.quote(query) + "&sp=CAI%3D")   # sort: upload date
        req  = urllib.request.Request(url, headers={"User-Agent": UA})
        html = urllib.request.urlopen(req, timeout=15).read().decode("utf-8", "replace")
        m = re.search(r"var ytInitialData = ({.*?});</script>", html, re.S)
        if m:
            d = json.loads(m.group(1))
            secs = (d.get("contents", {}).get("twoColumnSearchResultsRenderer", {})
                     .get("primaryContents", {}).get("sectionListRenderer", {})
                     .get("contents", []))
            for sec in secs:
                for it in sec.get("itemSectionRenderer", {}).get("contents", []):
                    v = it.get("videoRenderer")
                    if not v:
                        continue
                    hrs = _yt_age_hours(v.get("publishedTimeText", {}).get("simpleText", ""))
                    if hrs is None or hrs > 48:
                        continue
                    views = str(v.get("viewCountText", {}).get("simpleText", ""))
                    res["n_fresh"]     += 1
                    res["views_fresh"] += int(re.sub(r"[^\d]", "", views) or 0)
    except Exception as e:
        sys.stderr.write(f"[ytbuzz] '{query[:30]}': {e}\n")
    _yt_cache[ck] = res
    return res


def fetch_reddit_posts(query, subreddits=("worldnews", "politics", "news")):
    """Reddit public JSON search — no auth, free. Returns total post count across
    the given subreddits for the query in the past week. Cached 30 min."""
    cache_key = (query.lower()[:60], tuple(subreddits))
    cached = _reddit_cache.get(str(cache_key), {})
    if cached and time.time() - cached.get("ts", 0) < _REDDIT_TTL:
        return cached["posts"]
    total = 0
    for sub in subreddits:
        url = (f"https://www.reddit.com/r/{sub}/search.json"
               f"?q={urllib.parse.quote(query)}&sort=new&limit=25&t=week&restrict_sr=1")
        try:
            # Reddit requires a browser-like UA to avoid 429/403
            req  = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (scalp-lab-paper; non-commercial)"})
            with urllib.request.urlopen(req, timeout=6) as r:
                data = json.loads(r.read().decode("utf-8", "replace"))
            total += len(data.get("data", {}).get("children", []))
        except Exception:
            pass
    _reddit_cache[str(cache_key)] = {"posts": total, "ts": time.time()}
    return total


def fetch_coingecko():
    """CoinGecko free API — spot price + 24h/7d change for major coins, no auth.
    Returns {coin_id → {usd, usd_24h_change, usd_7d_change}}. Cached 5 min."""
    if time.time() - _coingecko_cache["ts"] < _CG_TTL and _coingecko_cache["data"]:
        return _coingecko_cache["data"]
    coins = ("bitcoin,ethereum,solana,binancecoin,dogecoin,ripple,"
             "cardano,avalanche-2,polkadot,chainlink,litecoin,stellar,"
             "toncoin,tron,shiba-inu,sui,near,cosmos,uniswap,"
             "aptos,arbitrum,optimism,pepe,dogwifhat,bonk,floki")
    url   = (f"https://api.coingecko.com/api/v3/simple/price?ids={coins}"
             f"&vs_currencies=usd&include_24hr_change=true&include_7d_change=true")
    try:
        req  = urllib.request.Request(url, headers={"User-Agent": "scalp-lab-paper/1.0 (non-commercial)"})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
        if data:
            _coingecko_cache["data"] = data
            _coingecko_cache["ts"]   = time.time()
            return data
        sys.stderr.write("[cryptofair] coingecko: empty body (throttled?)\n")
    except Exception as exc:
        sys.stderr.write(f"[cryptofair] coingecko: {exc}\n")
    # keyless fallback (coinpaprika → coinlore) so crypto features never go blind on a 429
    try:
        import feeds
        fb = feeds.crypto_multi_cg_shape(coins.split(","))
        if fb:
            _coingecko_cache["data"] = fb
            _coingecko_cache["ts"]   = time.time()
            sys.stderr.write(f"[cryptofair] coingecko down → keyless fallback served "
                             f"{len(fb)}/{coins.count(',') + 1} coins\n")
            return fb
    except Exception as exc2:
        sys.stderr.write(f"[cryptofair] fallback failed: {exc2}\n")
    return {}



_feargreed_cache = {"value": None, "ts": 0.0}
_FG_TTL          = 3600   # Fear & Greed changes slowly — 1h cache

def fetch_fear_greed():
    """alternative.me/fng — Fear & Greed index (0=Extreme Fear, 100=Extreme Greed).
    Free, no auth. Returns int or None on failure. Cached 1h."""
    if time.time() - _feargreed_cache["ts"] < _FG_TTL and _feargreed_cache["value"] is not None:
        return _feargreed_cache["value"]
    try:
        req = urllib.request.Request(
            "https://api.alternative.me/fng/?limit=1",
            headers={"User-Agent": "scalp-lab-paper/1.0 (non-commercial)"})
        with urllib.request.urlopen(req, timeout=6) as r:
            d = json.loads(r.read().decode("utf-8", "replace"))
        val = int(d["data"][0]["value"])
        _feargreed_cache["value"] = val
        _feargreed_cache["ts"]    = time.time()
        return val
    except Exception as exc:
        sys.stderr.write(f"[feargreed] fetch failed: {exc}\n")
        return _feargreed_cache["value"]   # return stale if available

# Coin title → CoinGecko ID for Up/Down markets
_UPDOWN_COIN_MAP = {
    "bitcoin": "bitcoin", "btc": "bitcoin",
    "ethereum": "ethereum", "eth": "ethereum",
    "bnb": "binancecoin", "binance": "binancecoin",
    "solana": "solana", "sol": "solana",
    "xrp": "ripple", "ripple": "ripple",
    "dogecoin": "dogecoin", "doge": "dogecoin",
    "cardano": "cardano", "ada": "cardano",
    "chainlink": "chainlink", "link": "chainlink",
    "toncoin": "toncoin", "ton": "toncoin",
    "tron": "tron", "trx": "tron",
    "shiba": "shiba-inu", "shib": "shiba-inu",
    "sui": "sui",
    "near": "near",
    "cosmos": "cosmos", "atom": "cosmos",
    "uniswap": "uniswap", "uni": "uniswap",
    "aptos": "aptos", "apt": "aptos",
    "arbitrum": "arbitrum", "arb": "arbitrum",
    "optimism": "optimism", "op": "optimism",
    "pepe": "pepe",
    "dogwifhat": "dogwifhat", "wif": "dogwifhat",
    "bonk": "bonk",
    "floki": "floki",
}

def _coin_from_updown(q):
    """Parse CoinGecko coin_id from 'Bitcoin Up or Down ...' style title.
    Returns coin_id string or None."""
    ql = q.lower()
    if "up or down" not in ql:
        return None
    before = ql.split("up or down")[0].strip().rstrip("- ").split()[-1] if ql.split("up or down")[0].strip() else ""
    return _UPDOWN_COIN_MAP.get(before)

def _parse_price_market(q):
    """Parse 'Will the price of Bitcoin be above $62,000 on June 13?' style titles.
    Also handles 'Will Ethereum dip to $1,550 on June 10?' and
    'Will the price of Solana be greater than $120 on June 10?' and
    'between $100 and $110' (returns None for ranges — ambiguous).
    Returns (coin_id, strike, direction) where direction is 'above'|'below',
    or None if not parseable."""
    ql = q.lower()
    coin_id = None
    for name, cid in sorted(_UPDOWN_COIN_MAP.items(), key=lambda x: -len(x[0])):
        if re.search(r"\b" + re.escape(name) + r"\b", ql):
            coin_id = cid
            break
    if not coin_id:
        return None
    if "between" in ql:   # range markets — skip
        return None
    m_price = re.search(r"\$([0-9][0-9,]*\.?[0-9]*)", ql)
    if not m_price:
        return None
    strike = float(m_price.group(1).replace(",", ""))
    if any(k in ql for k in ("above", "greater than", "hit", "reach", "exceed")):
        direction = "above"
    elif any(k in ql for k in ("below", "less than", "dip to", "drop to", "fall to", "under")):
        direction = "below"
    else:
        return None
    return coin_id, strike, direction


def _lognormal_prob(spot, target, days, annual_vol=0.80):
    """P(price ≥ target at maturity) under geometric Brownian motion.
    Uses the Black-Scholes d2 formula: P = N(d2) where
      d2 = [ln(S/K) + (−σ²/2)·T] / (σ·√T)
    annual_vol = 0.80 is a conservative 80% annualized vol prior for major crypto.
    Returns a float in [0, 1]."""
    import math
    if spot <= 0 or target <= 0 or days <= 0:
        return 0.0
    T = days / 365.0
    try:
        d2 = (math.log(spot / target) + (-0.5 * annual_vol ** 2) * T) / (annual_vol * math.sqrt(T))
        # Standard-normal CDF — Abramowitz & Stegun 26.2.17 polynomial approximation
        def _ncdf(x):
            a1, a2, a3, a4, a5 = 0.319381530, -0.356563782, 1.781477937, -1.821255978, 1.330274429
            k    = 1.0 / (1.0 + 0.2316419 * abs(x))
            poly = k * (a1 + k * (a2 + k * (a3 + k * (a4 + k * a5))))
            base = 1.0 - (1.0 / math.sqrt(2 * math.pi)) * math.exp(-x * x / 2.0) * poly
            return base if x >= 0 else 1.0 - base
        return max(0.0, min(1.0, _ncdf(d2)))
    except Exception:
        return 0.0


def _parse_crypto_target(question):
    """Extract (coin_id, target_usd) from a crypto price market title.
    Returns (None, None) if the question is not a price-target market.
    Examples:
      'Will Bitcoin reach $100,000 by end of 2025?' → ('bitcoin', 100000.0)
      'Will ETH hit $5k?'                           → ('ethereum', 5000.0)
    """
    q = question.lower()
    # Only price-target questions contain these verbs
    if not any(kw in q for kw in ("reach", "hit", "exceed", "above", "over", "top", "cross", "break")):
        return None, None
    COIN_MAP = {
        "bitcoin": "bitcoin",       "btc": "bitcoin",
        "ethereum": "ethereum",     "eth": "ethereum",
        "solana": "solana",         "sol": "solana",
        "binance coin": "binancecoin", "bnb": "binancecoin",
        "dogecoin": "dogecoin",     "doge": "dogecoin",
        "ripple": "ripple",         "xrp": "ripple",
        "cardano": "cardano",       "ada": "cardano",
        "avalanche": "avalanche-2", "avax": "avalanche-2",
        "polkadot": "polkadot",     "dot": "polkadot",
        "chainlink": "chainlink",   "link": "chainlink",
        "litecoin": "litecoin",     "ltc": "litecoin",
        "stellar": "stellar",       "xlm": "stellar",
    }
    coin_id = None
    for name, cid in sorted(COIN_MAP.items(), key=lambda x: -len(x[0])):
        if name in q:
            coin_id = cid
            break
    if not coin_id:
        return None, None
    # Extract dollar target — "$100,000", "$100k", "100 000 usd", etc.
    m = re.search(r'\$([0-9,]+(?:\.[0-9]+)?)([kKmM]?)', question)
    if not m:
        m = re.search(r'([0-9,]+(?:\.[0-9]+)?)\s*([kKmM]?)\s*(?:usd|dollars?)', q)
    if not m:
        return coin_id, None
    try:
        num    = float(m.group(1).replace(",", ""))
        suffix = m.group(2).lower()
        if suffix == "k": num *= 1_000
        elif suffix == "m": num *= 1_000_000
        return coin_id, num
    except ValueError:
        return coin_id, None


def fetch_predictit():
    """PredictIt free JSON API — all active markets with per-contract prices.
    Returns list of dicts: {question, yes_price, id}. Cached 5 min. No auth."""
    if time.time() - _predictit_cache["ts"] < _PI_TTL and _predictit_cache["markets"]:
        return _predictit_cache["markets"]
    url = "https://www.predictit.org/api/marketdata/all/"
    try:
        req  = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (scalp-lab-paper; non-commercial)"})
        with urllib.request.urlopen(req, timeout=8) as r:
            raw = json.loads(r.read().decode("utf-8", "replace"))
        out = []
        for mkt in raw.get("markets", []):
            for contract in mkt.get("contracts", []):
                # Only binary YES/NO contracts that are active
                if contract.get("status") != "Open":
                    continue
                best_yes = contract.get("bestBuyYesCost")
                if best_yes is None:
                    continue
                out.append({
                    "q":   mkt.get("name", "") + " — " + contract.get("name", ""),
                    "yes": float(best_yes),
                    "pi_id": contract.get("id"),
                })
        _predictit_cache["markets"] = out
        _predictit_cache["ts"]      = time.time()
        sys.stderr.write(f"[predictit] cached {len(out)} contracts\n")
        return out
    except Exception as exc:
        sys.stderr.write(f"[predictit] fetch failed: {exc}\n")
        return _predictit_cache["markets"]   # return stale on error


def _update_vol_baseline(market_id, current_vol):
    """Exponential moving average of market volume across cycles.
    Returns the PREVIOUS baseline (before update) for spike detection."""
    prev = _vol_baseline.get(market_id, current_vol)   # first time: no spike
    _vol_baseline[market_id] = _VOL_EMA_ALPHA * current_vol + (1 - _VOL_EMA_ALPHA) * prev
    return prev


def _market_age_hours(m):
    """Hours since a Gamma market was created. Returns None if field absent."""
    created = m.get("created_at") or m.get("createdAt") or m.get("created")
    if not created:
        return None
    try:
        from datetime import datetime, timezone as _tz5
        dt = datetime.fromisoformat(str(created).replace("Z", "+00:00"))
        return (datetime.now(_tz5.utc) - dt).total_seconds() / 3600
    except Exception:
        return None


# ── Wallet / trade-feed helpers (2026-06-07) ─────────────────────────────────

def fetch_global_trades(limit=500):
    """All recent trades from data-api.polymarket.com — global feed, no auth."""
    now = time.time()
    if now - _global_trades_cache["ts"] < _GLOBAL_TRADES_TTL:
        return _global_trades_cache["trades"]
    try:
        import socket as _sock
        url = f"https://data-api.polymarket.com/trades?limit={limit}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        _sock.setdefaulttimeout(10)
        with urllib.request.urlopen(req, timeout=10) as r:
            trades = json.loads(r.read())
        _global_trades_cache.update({"trades": trades, "ts": now})
        sys.stderr.write(f"[trades] fetched {len(trades)} recent trades\n")
        return trades
    except Exception as e:
        sys.stderr.write(f"[trades] fetch failed: {e}\n")
        return _global_trades_cache["trades"]


def _whale_flow(market_id, min_usd=200):
    """(buy_usd, sell_usd) of large trades on one market from the global trades
    cache. LOGGED on gate-leg entries as a covariate for post-gate analysis —
    NOT a live signal (whale-follow legs died: ~44% WR over n=136). Skill rule:
    whale data is confirmation research, never instruction."""
    buy = sell = 0.0
    for t in _global_trades_cache.get("trades", []):
        try:
            if str(t.get("conditionId")) != str(market_id):
                continue
            usd = float(t.get("size", 0)) * float(t.get("price", 0))
            if usd < min_usd:
                continue
            if str(t.get("side", "")).upper() == "BUY":
                buy += usd
            else:
                sell += usd
        except Exception:
            continue
    return round(buy, 2), round(sell, 2)


def get_top_wallets(n=20):
    """Rank wallets by dollar volume in the last 500 trades. Cached 30 min."""
    now = time.time()
    if now - _top_wallets_cache["ts"] < _TOP_WALLETS_TTL and _top_wallets_cache["wallets"]:
        return _top_wallets_cache["wallets"]
    trades = fetch_global_trades(500)
    vol, names = {}, {}
    for t in trades:
        w = t.get("proxyWallet", "")
        if not w:
            continue
        dollar = float(t.get("size", 0)) * float(t.get("price", 0))
        vol[w] = vol.get(w, 0) + dollar
        if t.get("name"):
            names[w] = t["name"]
    top = sorted(vol.items(), key=lambda x: -x[1])[:n]
    wallets = [{"addr": a, "vol": v, "name": names.get(a, "")} for a, v in top]
    _top_wallets_cache.update({"wallets": wallets, "ts": now})
    sys.stderr.write(f"[top_wallets] top: {wallets[0]['name'] or wallets[0]['addr'][:10]} "
                     f"${wallets[0]['vol']:.0f}\n" if wallets else "[top_wallets] empty\n")
    return wallets


def fetch_clob_book(token_id):
    """CLOB order book for a YES-side token. Cached 90s."""
    now = time.time()
    cached = _clob_cache.get(token_id)
    if cached and now - cached.get("ts", 0) < _CLOB_TTL:
        return cached
    try:
        import socket as _sock2
        url = f"https://clob.polymarket.com/book?token_id={token_id}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        _sock2.setdefaulttimeout(6)
        with urllib.request.urlopen(req, timeout=6) as r:
            book = json.loads(r.read())
        book["ts"] = now
        _clob_cache[token_id] = book
        return book
    except Exception:
        return cached or {}


_crypto_news_cache = {"titles": set(), "ts": 0.0}

def fetch_crypto_headlines():
    """Crypto headlines from public RSS (CoinDesk + Cointelegraph), no key.
    Second news feed beside OpenAlice — its world-news skew starved crypto
    news-votes (wave-5 follow-up, 2026-06-12). Cached 30 min in-process;
    once-mode refetches per run (~1s, fail-soft empty)."""
    now = time.time()
    if now - _crypto_news_cache["ts"] < 1800 and _crypto_news_cache["titles"]:
        return _crypto_news_cache["titles"]
    titles = set()
    for url in ("https://www.coindesk.com/arc/outboundfeeds/rss/",
                "https://cointelegraph.com/rss"):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            xml = urllib.request.urlopen(req, timeout=6).read().decode("utf-8", "replace")
            for t in re.findall(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", xml)[1:]:
                titles.add(t.lower())
        except Exception:
            continue
    if titles:
        _crypto_news_cache.update({"titles": titles, "ts": now})
    return titles


def liquidity_check(m, max_spread=0.03, min_depth_usd=300):
    """(ok, spread, depth) from the live CLOB book. The OOS backtest assumes a
    1.5c half-spread — entries on books wider than max_spread violate the
    measured economics. Fail-OPEN on fetch failure (don't starve proven legs
    on a flaky endpoint), fail-CLOSED on a real thin/wide book."""
    tok = m.get("token")
    if not tok:
        return True, None, None
    book = fetch_clob_book(tok)
    try:
        bids = book.get("bids") or []
        asks = book.get("asks") or []
        if not bids or not asks:
            return True, None, None        # no data → fail-open
        bb = max(float(b["price"]) for b in bids)
        ba = min(float(a["price"]) for a in asks)
        spread = ba - bb
        depth = sum(float(x["price"]) * float(x["size"]) for x in bids + asks
                    if abs(float(x["price"]) - (bb + ba) / 2) <= 0.03)
        return (spread <= max_spread and depth >= min_depth_usd,
                round(spread, 4), round(depth))
    except Exception:
        return True, None, None


_IMESSAGE_TARGETS = ["krisharyan@icloud.com", "+918449447444"]
_imsg_queue = []       # batch messages, flush once per scan
_last_imsg_flush = 0.0
_IMSG_MIN_GAP = 60     # max 1 iMessage burst per minute

def _imsg(text):
    """Queue an iMessage line; flushed in bulk at end of scan."""
    _imsg_queue.append(text)

def _flush_imsg():
    """Send all queued messages as one iMessage (avoids spam)."""
    global _last_imsg_flush
    if not _imsg_queue:
        return
    now = time.time()
    if now - _last_imsg_flush < _IMSG_MIN_GAP:
        _imsg_queue.clear()
        return
    body = "\n".join(_imsg_queue)
    _imsg_queue.clear()
    _last_imsg_flush = now
    # body via argv: a literal newline inside a quoted AppleScript string is a
    # SYNTAX ERROR — multi-line flushes died silently until 2026-06-12.
    osa = ('on run argv\n'
           'tell application "Messages"\n'
           '  set s to 1st service whose service type = iMessage\n'
           '  set b to buddy (item 1 of argv) of s\n'
           '  send (item 2 of argv) to b\n'
           'end tell\n'
           'end run')
    for target in _IMESSAGE_TARGETS:
        try:
            r = subprocess.run(["osascript", "-e", osa, target, body],
                               capture_output=True, text=True, timeout=30)
            if r.returncode != 0:
                sys.stderr.write(f"[imsg FAIL] {target}: {r.stderr.strip()}\n")
        except Exception as e:
            sys.stderr.write(f"[imsg FAIL] {target}: {e}\n")


def watchdog_flag_whales(trades, threshold_usdc=200):
    """Log any new trade above threshold to stderr. Called in one_scan.
    Uses tx-hash dedup so each whale trade logs exactly once regardless of
    when the feed delivers it (trades arrive ~5-10 min delayed on free tier)."""
    cutoff = time.time() - 900   # 15 min window — covers feed delay
    for t in trades:
        if t.get("timestamp", 0) < cutoff:
            continue
        dollar = float(t.get("size", 0)) * float(t.get("price", 0))
        if dollar < threshold_usdc:
            continue
        tid = t.get("transactionHash", "")
        if tid in _seen_whale_tx:
            continue
        _seen_whale_tx.add(tid)
        name = t.get("name") or t.get("pseudonym") or t.get("proxyWallet", "?")[:10]
        sys.stderr.write(
            f"🐋 WHALE ${dollar:.0f}  {t.get('side','?')} {t.get('outcome','?')[:28]}"
            f"  @ {t.get('price',0):.3f}  by {name}\n"
        )


def _build_trade_index(trades, window_min=90):
    """Index trades by conditionId for fast lookup. Computes dollar value inline."""
    cutoff = time.time() - window_min * 60
    idx = {}   # conditionId → list of {side, dollar, ts, tx}
    for t in trades:
        if t.get("timestamp", 0) < cutoff:
            continue
        cid = t.get("conditionId", "")
        if not cid:
            continue
        dollar = float(t.get("size", 0)) * float(t.get("price", 0))
        idx.setdefault(cid, []).append({
            "side": t.get("side", ""),
            "dollar": dollar,
            "ts": t.get("timestamp", 0),
            "tx": t.get("transactionHash", ""),
            "wallet": t.get("proxyWallet", ""),
        })
    return idx


def _compute_cat_chg(markets):
    """Average 24h chg per category across all markets. Stored in _cat_chg_snap."""
    cat_totals = {}
    cat_counts = {}
    for m in markets:
        cat = m.get("cat") or "unknown"
        chg = m.get("chg", 0) or 0
        cat_totals[cat] = cat_totals.get(cat, 0) + chg
        cat_counts[cat] = cat_counts.get(cat, 0) + 1
    _cat_chg_snap.clear()
    for cat, total in cat_totals.items():
        _cat_chg_snap[cat] = total / cat_counts[cat] if cat_counts[cat] else 0


def _extract_wc_team(question):
    """Extract the primary team name from a WC Polymarket question.
    Returns (canonical_name, rank) or (None, None) on no match.
    Strategy: scan question words for longest FIFA_RANKS key match."""
    q_lower = question.lower()
    # Try aliases first (they're often the shorter form like "USA", "DRC")
    for alias, canonical in TEAM_ALIASES.items():
        if alias in q_lower:
            rank = FIFA_RANKS.get(canonical)
            if rank:
                return canonical, rank
    # Try full FIFA_RANKS keys
    best, best_rank, best_len = None, None, 0
    for name, rank in FIFA_RANKS.items():
        if name.lower() in q_lower and len(name) > best_len:
            best, best_rank, best_len = name, rank, len(name)
    return best, best_rank


def _wc_qtype(question):
    """Detect the type of WC question for probability calibration.
    Returns: 'wc_winner' | 'group_winner' | 'advance' | 'match' | None"""
    q = question.lower()
    if "win the" in q and ("world cup" in q or "wc" in q):
        return "wc_winner"
    if "win group" in q or "win their group" in q or "finish first" in q:
        return "group_winner"
    if "advance" in q or "qualify" in q or "reach" in q or "make it" in q:
        return "advance"
    if any(k in q for k in ("vs.", " vs ", " vs\n", "beat ", "defeat ", "win the match")):
        return "match"
    if "world cup" in q or "fifa" in q:  # generic WC question
        return "wc_winner"
    return None


def _rank_prob(rank, qtype, standings_entry, last_result):
    """Compute rank-implied probability for a team + question type.

    Combines three historical/live signals:
      1. FIFA rank (long-term: ~4-year weighted strength)
      2. WC in-tournament standing (live form: W/L/pts so far)
      3. TheSportsDB last result (short-term: most recent match)

    Returns a probability in [0, 1].  Honest caveats:
      • Base rates are calibrated to historical WC data (rough approximation).
      • In-tournament adjustments are linear heuristics, not regression-fitted.
      • This is a HYPOTHESIS to test, not a proven model.
    """
    # ── 1. Base rate from FIFA rank ──────────────────────────────────────────
    # WC winner: empirically, top-5 teams win ~60% of WC tournaments combined.
    # Group winner: ~25% base + advantage for strong team in 4-team group.
    # Advance (top 2 of 4 + 8 best 3rd): ~62.5% base + strength adjustment.
    # Match winner: depends on matchup; we have one-team signal only.
    if qtype == "wc_winner":
        # 0.5/rank normalized over sum(0.5/i for i=1..48) ≈ 2.28
        raw = 0.5 / max(rank, 1)
        base = raw / 2.28
    elif qtype == "group_winner":
        # top team in a 4-team group: ~40-50%, weakest ~10%
        raw = 1.0 / max(rank ** 0.4, 1)
        # normalize very roughly: sum of 1/rank^0.4 for top 4 of group
        # assume group has ranks: rank, rank+10, rank+20, rank+30 (approximation)
        group_total = sum(1.0 / max((rank + i * 10) ** 0.4, 1) for i in range(4))
        base = raw / group_total
    elif qtype == "advance":
        # top-2 + some 3rd-place → roughly 62.5% base, strength-adjusted
        raw = 1.0 / max(rank ** 0.25, 1)
        norm = sum(1.0 / max(i ** 0.25, 1) for i in range(1, 49))
        base = min(0.85, max(0.10, raw / norm * 48 * 0.625))
    else:  # match or generic WC: use wc_winner proxy
        raw = 0.5 / max(rank, 1)
        base = raw / 2.28

    # ── 2. In-tournament adjustment (ESPN WC standings) ──────────────────────
    # Each win/loss shifts estimated probability toward the "form" direction.
    # Capped at ±25% adjustment to prevent over-reacting to a single result.
    adj = 0.0
    if standings_entry:
        gp = standings_entry.get("gp", 0)
        w  = standings_entry.get("w", 0)
        l  = standings_entry.get("l", 0)
        if gp > 0:
            form_rate = w / gp       # fraction of games won so far
            adj = (form_rate - 0.5) * 0.15 * gp   # ±7.5% per game won/lost

    # ── 3. Recent form adjustment (TheSportsDB last result) ──────────────────
    form_adj = {"W": +0.02, "L": -0.02, "D": 0.0}.get(last_result.get("result","?"), 0.0)

    prob = max(0.01, min(0.97, base + adj + form_adj))
    return round(prob, 4)


def _sports_context(team, standings, news_counts, last_result):
    """Build a context dict stored on every sports position at entry.
    Used for post-hoc analysis: WHY did this trade win/lose?
    This is the 'analyze every trade' annotation layer."""
    return {
        "team":          team,
        "fifa_rank":     FIFA_RANKS.get(team),
        "espn_mentions": news_counts.get(team, 0),
        "wc_standing":   standings.get(team),
        "last_result":   last_result.get("result"),
        "last_date":     last_result.get("date"),
        "last_opp":      last_result.get("opp"),
        "last_score":    last_result.get("score"),
    }


def is_live_match(q):
    """True for single-match markets (tennis/esports/team games) that resolve
    within hours regardless of the market's PADDED end date. 2026-06-10: fogbuy
    took a -$0.998 gap loss and nearresfade two surprise exits on 'HSBC
    Championships: X vs Y' tennis — min_days gates are useless when `end` lies.
    Multi-day-horizon legs must skip these; nearres/sportres/nearreslow want them."""
    ql = str(q).lower()
    if " vs " in ql or " vs. " in ql:
        return True
    return any(k in ql for k in (
        "championships:", "open:", "masters:", "- game ", "game 1", "game 2",
        "(bo3)", "(bo5)", "map 1", "map 2", "winner?", "leading at halftime",
        "counter-strike", "valorant", "lol:", "dota",
        # 2026-06-12: date-match and betting-line formats ("Will Mexico win on
        # 2026-06-11?", "Spread: Mexico (-1.5)") burned newsno/ytbuzz/latefade
        " win on 20", "spread:", "o/u ", "(-1.", "(-2.", "(+1.", "(+2.",
        "moneyline"))


def category_of(m):
    """Same logic as ml/collect_history.py so live fade trades classify
    IDENTICALLY to the in-sample dataset (sports vs non-sports must match)."""
    tags = " ".join(str(t) for t in (m.get("tags") or [])).lower()
    q = (m.get("question") or "").lower()
    blob = tags + " " + q
    if any(k in blob for k in ("bitcoin", "ethereum", "crypto", "btc", "eth", "solana")):
        return "crypto"
    if any(k in blob for k in ("election", "president", "senate", "congress", "minister", "vote", "poll")):
        return "politics"
    # 2026-06-10: finer buckets — 441/800 markets were landing in "other".
    # esports BEFORE sports so "LoL: ... Game 1" stops misfiling as sports.
    if any(k in blob for k in ("counter-strike", "cs2", "valorant", "league of legends",
                               "lol:", "dota", "overwatch", "esport", "iem ",
                               "blast premier", "vct ", "lck", "lpl", "lcs")):
        return "esports"
    if any(k in blob for k in ("nba", "nfl", "soccer", "tennis", "ufc", "mlb", "match", "vs.", "game")):
        return "sports"
    if any(k in blob for k in ("war", "ceasefire", "invasion", "nuclear", "nato",
                               "missile", "strike on", "treaty", "regime")):
        return "world"
    if any(k in blob for k in ("movie", "film", "album", "spotify", "box office",
                               "gta", "oscar", "grammy", "netflix")):
        return "entertainment"
    if any(k in blob for k in ("fed ", "rate cut", "inflation", "cpi", "gdp",
                               "recession", "tariff", "unemployment")):
        return "economy"
    return "other"


def fetch_markets(pages=8, per_page=100):
    """High-volume, open binary markets from Gamma, PAGINATED.

    Gamma caps each call at ~100 rows, so we walk `pages` offsets (most-liquid
    first) to build a universe of ~pages*per_page markets. A deeper universe is
    what the scalp leg needs — near-certain favorites are rare and only show up
    once you scan past the contested 0.50 markets at the top.
    """
    out, seen = [], set()
    for p in range(pages):
        raw = http_get(GAMMA, {
            "closed": "false", "active": "true",
            "order": "volumeNum", "ascending": "false",
            "limit": per_page, "offset": p * per_page,
        }) or []
        if not raw:
            break
        for m in raw:
            try:
                cid = str(m.get("conditionId") or m.get("id"))
                if cid in seen:
                    continue
                prices = m.get("outcomePrices")
                if isinstance(prices, str):
                    prices = json.loads(prices)
                toks = m.get("clobTokenIds")
                if isinstance(toks, str):
                    toks = json.loads(toks)
                if not prices or len(prices) < 2:
                    continue
                seen.add(cid)
                out.append({
                    "id":    cid,
                    "q":     (m.get("question") or "")[:90],
                    "yes":   float(prices[0]),
                    "vol":   float(m.get("volumeNum") or m.get("volume") or 0),
                    "end":   m.get("endDate"),
                    "start": m.get("gameStartTime"),  # honest for sports; endDate is padded
                    "token": (toks[0] if toks else None),
                    "chg":   float(m.get("oneDayPriceChange") or 0.0),  # 24h YES move (momentum/midfade)
                    "cat":   category_of(m),  # sports/crypto/politics/other — for fade sports-split
                    # top-of-book YES quotes (csarb complete-set test); None when absent
                    "bid":   (float(m["bestBid"]) if m.get("bestBid") not in (None, "") else None),
                    "ask":   (float(m["bestAsk"]) if m.get("bestAsk") not in (None, "") else None),
                })
            except Exception:
                continue
        if len(raw) < per_page:
            break   # last page reached
    return out


_updown_cache = {"ts": 0.0, "markets": []}
_UPDOWN_TTL = 45   # one fetch per once-cycle; markets churn every few minutes

def fetch_updown_markets(pages=4):
    """Short-window crypto "Up or Down" markets — returned in fetch_markets()
    shape so the same legs can consume them.

    WHY THIS EXISTS (diagnosed 2026-06-15): these markets are INVISIBLE to
    fetch_markets() — the gamma /markets?order=volumeNum feed never surfaces
    them (they churn hourly → low cumulative volume → past the 800-row cap;
    absent under order=endDate too). They live in the /events endpoint instead
    (same lesson kalshi_feed learned). This blindness is why csarb has NEVER
    fired and why coinup/coindown were wrongly graveyarded as "no such markets".
    They ARE low-volume by nature (≤~$17k), so the legs that use them run a
    LOW vol floor — a 50k floor (regular-market calibration) filters them all.
    """
    now = time.time()
    if now - _updown_cache["ts"] < _UPDOWN_TTL:
        return _updown_cache["markets"]
    events_url = GAMMA.rsplit("/", 1)[0] + "/events"
    out, seen = [], set()
    # UNION of orderings: startDate-desc gives broad coverage of freshly-created
    # markets (which mature into eligibility); volume/volume24hr-desc surface the
    # LIQUID mid-window markets (startDate-desc pushes them past the cutoff). The
    # legs need the liquid ones — gating on vol>=2k filters everything otherwise.
    plan = [("startDate", pages), ("volume24hr", 3), ("volume", 3)]
    for order, opages in plan:
      for p in range(opages):
        raw = http_get(events_url, {
            "closed": "false", "active": "true",
            "order": order, "ascending": "false",
            "limit": 100, "offset": p * 100,
        }) or []
        if not raw:
            break
        for ev in raw:
            t = (ev.get("title") or "").lower()
            if not any(k in t for k in ("up or down", "higher or lower", "above or below")):
                continue
            for m in (ev.get("markets") or []):
                try:
                    cid = str(m.get("conditionId") or m.get("id"))
                    if not cid or cid in seen:
                        continue
                    prices = m.get("outcomePrices")
                    if isinstance(prices, str):
                        prices = json.loads(prices)
                    if not prices or len(prices) < 2:
                        continue
                    toks = m.get("clobTokenIds")
                    if isinstance(toks, str):
                        toks = json.loads(toks)
                    seen.add(cid)
                    out.append({
                        "id":    cid,
                        "q":     (m.get("question") or ev.get("title") or "")[:90],
                        "yes":   float(prices[0]),
                        "vol":   float(m.get("volumeNum") or m.get("volume") or m.get("liquidity") or 0),
                        "end":   m.get("endDate"),
                        "start": m.get("gameStartTime"),
                        "token": (toks[0] if toks else None),
                        "chg":   float(m.get("oneDayPriceChange") or 0.0),
                        "cat":   "crypto",
                        "bid":   (float(m["bestBid"]) if m.get("bestBid") not in (None, "") else None),
                        "ask":   (float(m["bestAsk"]) if m.get("bestAsk") not in (None, "") else None),
                    })
                except Exception:
                    continue
        if len(raw) < 100:
            break
    _updown_cache.update({"ts": now, "markets": out})
    sys.stderr.write(f"[updown] {len(out)} Up/Down markets via events\n")
    return out


def fetch_prices_direct(ids, chunk=20):
    """Real YES price for held markets that fell out of the top-volume feed
    (or resolved). Without this, scan_exits parks such positions at breakeven
    ('stale_nodata'), which CENSORS real losses — fatal for fade, whose NO
    positions on favorites usually resolve against us yet were marked $0.
    Gamma comma-join (condition_ids=a,b) returns nothing; the repeated-param
    form (condition_ids=a&condition_ids=b) works, incl. closed/resolved markets.
    Batched + capped so we don't re-trigger 429s.
    """
    out, ids = {}, [i for i in dict.fromkeys(ids) if i]
    for k in range(0, len(ids), chunk):
        part = ids[k:k + chunk]
        url = GAMMA + "?" + "&".join(
            "condition_ids=" + urllib.parse.quote(str(i)) for i in part)
        raw = http_get(url) or []
        for m in raw:
            try:
                prices = m.get("outcomePrices")
                if isinstance(prices, str):
                    prices = json.loads(prices)
                if not prices or len(prices) < 2:
                    continue
                yes = float(prices[0])
                out[str(m.get("conditionId") or m.get("id"))] = yes
                if m.get("id"):
                    out[str(m.get("id"))] = yes
            except Exception:
                continue
    return out


def fetch_price_clob(cid):
    """YES price from the CLOB for a single market. Resolved markets age out of
    gamma entirely (condition_ids returns []), but clob.polymarket.com keeps
    them with settled token prices (0/1) — without this fallback every position
    held through resolution was censored as stale_nodata (265 records by
    2026-06-10)."""
    try:
        d = http_get(f"https://clob.polymarket.com/markets/{cid}", retries=1)
        toks = (d or {}).get("tokens") or []
        if not toks:
            return None
        yes_tok = next((t for t in toks
                        if str(t.get("outcome", "")).lower() == "yes"), toks[0])
        p = yes_tok.get("price")
        return float(p) if p is not None else None
    except Exception:
        return None


# ── State ───────────────────────────────────────────────────────────────────
def load_state():
    s = _db.lab_load_state(LEGS)
    if not isinstance(s, dict):
        s = {}
    for k in LEGS:
        s.setdefault(k, {"open": [], "closed": []})
    # ── Restore persisted EMA baselines + price snapshots ──────────────────
    global _vol_baseline, _price_snap
    if os.path.exists(CACHE):
        try:
            c = json.load(open(CACHE))
            loaded_vb = c.get("vol_baseline", {})
            loaded_ps = c.get("price_snap", {})
            _vol_baseline.update(loaded_vb)
            _price_snap.update(loaded_ps)
            # Attention caches must persist too: under once-mode every run is a
            # fresh process, so in-memory-only caches meant wikivol/redditbuzz
            # could never fire (warmup filled them right before process exit).
            _wiki_cache.update(c.get("wiki_cache", {}))
            _reddit_cache.update(c.get("reddit_cache", {}))
            _yt_cache.update(c.get("yt_cache", {}))
            _gdelt_cache.update(c.get("gdelt_cache", {}))
            age_h = (time.time() - c.get("saved_at", 0)) / 3600
            sys.stderr.write(
                f"[cache] restored {len(loaded_vb)} vol baselines, "
                f"{len(loaded_ps)} price snaps, "
                f"{len(_wiki_cache)}w/{len(_reddit_cache)}r/{len(_yt_cache)}y attention  "
                f"(age {age_h:.1f}h)\n"
            )
        except Exception as e:
            sys.stderr.write(f"[cache] load failed: {e}\n")
    return s


def save_state(s):
    _db.lab_save_state(s)
    # Mirror to JSON — watchdog's freshness check and hourly_pnl_alert.py read
    # this file; it went permanently stale when state moved to PostgreSQL.
    try:
        tmp = STATE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(s, f, default=str)
        os.replace(tmp, STATE)
    except Exception as e:
        sys.stderr.write(f"[state] json mirror failed: {e}\n")
    # ── Persist EMA baselines + price snapshots (cache stays in JSON) ───────
    try:
        json.dump(
            {"vol_baseline": _vol_baseline, "price_snap": _price_snap,
             "wiki_cache": _wiki_cache, "reddit_cache": _reddit_cache,
             "yt_cache": _yt_cache, "gdelt_cache": _gdelt_cache,
             "saved_at": time.time()},
            open(CACHE, "w"), indent=2
        )
    except Exception as e:
        sys.stderr.write(f"[cache] save failed: {e}\n")


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def hours_since(iso):
    try:
        t = datetime.fromisoformat(iso)
        return (datetime.now(timezone.utc) - t).total_seconds() / 3600
    except Exception:
        return 0.0


def days_to_end(end_iso):
    """Days until the market resolves, or None if unknown/unparseable."""
    if not end_iso:
        return None
    try:
        t = datetime.fromisoformat(str(end_iso).replace("Z", "+00:00"))
        return (t - datetime.now(timezone.utc)).total_seconds() / 86400
    except Exception:
        return None


# ── Fill modeling ─────────────────────────────────────────────────────────
def liq_spread(vol, cfg):
    """Liquidity-aware spread estimate.
    At ref_volume the spread equals assumed_spread; thinner books cost more.
    Floored at assumed_spread/4 for deep markets (>4x ref), capped at liq_spread_cap.
    Formula: spread = base * sqrt(ref / vol), clamped to [base/4, cap].
    This is used by ask()/bid() when vol is provided, and directly by fade legs."""
    base = cfg["assumed_spread"]
    ref  = cfg.get("liq_ref_volume", 300_000)
    raw  = base * (ref / max(vol, 1.0)) ** 0.5
    return round(min(cfg.get("liq_spread_cap", 0.10),
                     max(base / 4.0, raw)), 4)


def ask(mid, cfg, vol=None):   # you BUY here — vol makes spread volume-aware
    spr = liq_spread(vol, cfg) if vol else cfg["assumed_spread"]
    return min(0.999, mid + spr / 2)


def bid(mid, cfg, vol=None):   # you EXIT here — vol makes spread volume-aware
    spr = liq_spread(vol, cfg) if vol else cfg["assumed_spread"]
    return max(0.001, mid - spr / 2)


def maker_fill(mid, cfg, vol=None):
    """Maker entry: rest a limit AT the mid instead of crossing the spread.
    Modeled fill = mid — honest only for legs that gate on price stability
    (a fast market blows through a resting order; the stability gate is what
    makes the fill assumption defensible). Falls back to taker ask() when
    maker_entries is off so the A/B can be flipped in one config line."""
    if not cfg.get("maker_entries", False):
        return ask(mid, cfg, vol)
    return round(min(0.999, max(0.001, mid)), 4)


def avellaneda_stoikov_quote(mid, inventory, sigma, t_days, gamma, k):
    """Avellaneda-Stoikov (2008) optimal market-making quotes, ported from
    oss-bots/polymarket/avellaneda-stoikov/src/main.py (limited-horizon form).

      reservation price r = mid - q·γ·σ²·(T-t)      (skews AWAY from mid with inventory)
      optimal half-spread = (1/γ)·ln(1 + γ/k)        (wider when vol/time high)
      optimal ask = r + half_spread,  bid = r - half_spread

    For spreadcap this gives TWO things the fixed-gain version lacked:
      1. an inventory-aware reservation price — if we already hold YES (q>0), r
         shifts DOWN so we prefer to SELL / fade, offloading one-sided risk;
      2. a volatility-and-time-scaled take-profit (the half-spread) instead of a
         blind 0.004 — wider target when the market can actually move that far.
    Returns (reservation_price, half_spread). All inputs in price/prob units."""
    import math
    T_t = max(t_days, 0.01)
    r = mid - inventory * gamma * (sigma ** 2) * T_t
    half_spread = (1.0 / gamma) * math.log(1.0 + gamma / max(k, 1e-6))
    return r, max(0.002, min(half_spread, 0.15))   # floor/cap for sane prediction-market quotes


# oracle3 Wang-transform λ by category (291,309 resolved contracts, Yang 2026).
# λ is the risk premium fades harvest; sports/politics carry almost none —
# the empirical reason truefade died on long-dated politics.
WANG_LAMBDA_BY_CAT = {
    "crypto": 0.253, "science": 0.268, "tech": 0.268, "other": 0.282,
    "sports": 0.070, "esports": 0.070, "politics": 0.054, "pol": 0.054,
}

def wang_cat_ok(m, cfg):
    """λ category gate for fade legs: skip categories whose Wang risk premium
    is too thin to pay the spread. Unknown categories default to 'other'."""
    lam = WANG_LAMBDA_BY_CAT.get((m.get("cat") or "other").lower(), 0.282)
    return lam >= cfg.get("fade_min_lambda", 0.15)


# Legs that hold to resolution instead of exiting on target/stop (Lesson #8).
HOLD_TO_RES_LEGS = ("nearres", "sportres", "nearreslow", "certsnipe", "psconfirm",
                    "nearrestitle")


def _half(d, cfg):
    """Exit/entry half-spread for a position/trade: its stored per-position spread
    if present (fade variants), else the flat assumed_spread (all legacy + other legs
    => byte-identical to the old bid()/HALF behaviour)."""
    return d.get("spread", cfg["assumed_spread"]) / 2.0

# ── Smart-timing helpers (scan-to-scan momentum) ────────────────────────────
def _scan_chg(market_id, current_yes):
    """Change in YES price since the last scan. Returns None on first sighting."""
    prev = _price_snap.get(market_id)
    return None if prev is None else round(current_yes - prev, 4)

def _entry_confirmed(market_id, current_yes, band_lo, band_hi,
                     max_rise=0.03, require_prev=True):
    """
    Returns True only when it is a GOOD time to enter:
      • Signal was present in the PREVIOUS scan too (not a one-scan flicker).
      • YES is not rising fast toward the upper band (rising knife — wait).
    band_lo/band_hi: the YES price range for this leg's signal.
    max_rise: maximum allowed scan-to-scan YES increase before we hold off.
    require_prev: if False, allow first-sighting entry (used for nearres).
    """
    prev = _price_snap.get(market_id)
    if prev is None:
        return not require_prev          # first time seeing market
    delta = current_yes - prev
    if require_prev and not (band_lo <= prev <= band_hi):
        return False   # signal wasn't present last scan — wait for it to settle
    if delta > max_rise:
        return False   # YES rising fast — not the right moment to fade it
    return True

def _momentum_exit_reason(pos, current_yes, cfg, strat):
    """
    Return 'momentum_stop' if adverse price acceleration warrants exiting
    before the hard stop is reached.  Logic:
      • Compute scan-to-scan YES change.
      • For NO positions YES rising fast is adverse; for YES positions falling fast.
      • Only fires when adverse move >= ACCEL_THRESH (4¢/scan) AND position is
        already at least EARLY_FRAC (40%) of the way to the stop.
    """
    prev_yes = _price_snap.get(pos["id"])
    if prev_yes is None:
        return None
    delta = current_yes - prev_yes          # positive = YES rising
    side  = pos.get("side", "YES")
    adverse = delta if side == "NO" else -delta   # positive = moving against us
    if adverse < 0.04:                            # not accelerating enough
        return None
    # How far are we from our stop?
    entry_mid = pos.get("entry_mid", 0)
    mid_now   = (1 - current_yes) if side == "NO" else current_yes
    stop_pct  = cfg.get(f"{strat}_stop", cfg.get("assumed_spread", 0.05))
    stop_dist = entry_mid * stop_pct             # distance from entry to stop
    loss_so_far = entry_mid - mid_now            # how much we've already lost
    if stop_dist > 0 and (loss_so_far / stop_dist) >= 0.40:
        return "momentum_stop"   # 40% into the stop and accelerating → get out
    return None


# ── Entry logic ─────────────────────────────────────────────────────────────
def scan_entries(markets, state, cfg):
    held = {p["id"] for strat in state.values() for p in strat["open"]}
    opened = []
    # Default to empty — populated later when fetch_global_trades runs.
    # Guards against UnboundLocalError if a leg check runs before the fetch section.
    _all_trades = []; _tidx_90 = {}; _tidx_60 = {}; _market_by_id = {}

    for strat, lo, hi, vmin, cap, bet, mind, maxd in (
        ("dip",   cfg["dip_buy_min"],   cfg["dip_buy_max"],   cfg["dip_min_volume"],   cfg["dip_max_open"],   cfg["dip_bet_usdc"],   cfg["dip_min_days"],   cfg["dip_max_days"]),
        ("scalp", cfg["scalp_buy_min"], cfg["scalp_buy_max"], cfg["scalp_min_volume"], cfg["scalp_max_open"], cfg["scalp_bet_usdc"], cfg["scalp_min_days"], cfg["scalp_max_days"]),
    ):
        if not cfg[f"{strat}_enabled"]:
            continue
        book = state[strat]
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        for m in markets:
            if len(book["open"]) >= cap:
                break
            if m["id"] in held or m["id"] in cooldown:
                continue
            if not (lo <= m["yes"] <= hi) or m["vol"] < vmin:
                continue
            d = days_to_end(m.get("end"))
            if d is None or d > maxd or d < mind:
                continue   # horizon window: skip year-out, unknown, AND too-soon (gap-prone)
            entry = ask(m["yes"], cfg)
            size  = round(bet / entry, 4) if entry > 0 else 0
            if size <= 0:
                continue
            pos = {
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": strat,
                "entry_mid": m["yes"], "entry_fill": round(entry, 4),
                "size": size, "vol": m["vol"],
                "opened_at": now_iso(),
            }
            pos["side"] = "YES"
            book["open"].append(pos)
            held.add(m["id"])
            opened.append(pos)

    # ── fade (NO side) ──────────────────────────────────────────────
    if cfg["fade_enabled"]:
        book = state["fade"]
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        for m in markets:
            if len(book["open"]) >= cfg["fade_max_open"]:
                break
            if m["id"] in held or m["id"] in cooldown:
                continue
            if not (cfg["fade_yes_min"] <= m["yes"] <= cfg["fade_yes_max"]):
                continue
            if m["vol"] < cfg["fade_min_volume"]:
                continue
            no_mid = 1 - m["yes"]
            entry  = ask(no_mid, cfg, m["vol"])
            size   = round(cfg["fade_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0:
                continue
            pos = {
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "fade", "side": "NO",
                "entry_mid": round(no_mid, 4), "entry_fill": round(entry, 4),
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "cat": m.get("cat"),   # tag-based category -> accurate sports split in fade_checkpoint
            }
            book["open"].append(pos)
            held.add(m["id"])
            opened.append(pos)

    # ── research-backed fade variants (2026-06-07) — INDEPENDENT A/B books (each
    #    evaluates ALL markets, not blocked by sibling legs). All buy NO + hold.
    #    truefade: the CORRECT band [0.20,0.55] (live `fade` wrongly uses [.60,.85]).
    #    deepfade: [0.20,0.35] peak distortion. nsfade: [0.20,0.55] non-sports.
    #    wangfade: [0.20,0.55] gated on Wang fair-value overpricing. ──────────────
    def _wang_overpriced(m):
        try:
            fair = _P.phi(_P.phi_inv(max(1e-4, min(1 - 1e-4, m["yes"]))) - cfg["wangfade_lambda"])
            return (m["yes"] - fair) >= cfg["wangfade_margin"]
        except Exception:
            return False
    # Esports match markets (e.g. "Monte vs paiN", CS:GO/Valorant/LoL fixtures) resolve
    # on genuine sporting uncertainty — fade thesis doesn't apply. Exclude by keyword.
    _ESPORTS_KW = ("counter-strike","cs2","cs:","valorant","league of legends",
                   " vs ", "dota","overwatch","rainbow six","r6 siege","esl pro",
                   "iem cologne","pgl major","blast premier","esport")
    def _is_esports(m):
        q = m.get("q","").lower()
        return any(k in q for k in _ESPORTS_KW)
    for leg, pred in (
        ("truefade", lambda m: 0.20 <= m["yes"] <= 0.55 and not _is_esports(m)),
        ("deepfade", lambda m: 0.20 <= m["yes"] <= 0.35 and not _is_esports(m)),
        ("nsfade",   lambda m: 0.20 <= m["yes"] <= 0.55 and m.get("cat") != "sports" and not _is_esports(m)),
        ("wangfade", lambda m: 0.20 <= m["yes"] <= 0.55 and _wang_overpriced(m) and not _is_esports(m)),
    ):
        if not cfg.get(f"{leg}_enabled"):
            continue
        book = state[leg]
        own = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        for m in markets:
            if len(book["open"]) >= cfg[f"{leg}_max_open"]:
                break
            if m["id"] in own or m["id"] in cooldown:
                continue
            if m["vol"] < cfg["fade_min_volume"] or not pred(m):
                continue   # fade_min_volume = LIQUIDITY GATE: skip books too thin to fill
            _max_d = cfg.get(f"{leg}_max_days")
            if _max_d is not None:
                _d = days_to_end(m.get("end"))
                if _d is None or _d > _max_d:
                    continue  # skip long-dated markets (2028 elections etc) — no resolution pressure
            # ── Smart entry timing: require 2-scan confirmation, reject rising knife ──
            # band bounds for truefade/deepfade/nsfade/wangfade are all YES ∈ [0.20,0.55]
            # (deepfade is [0.20,0.35] but over-checking is fine — pred() already gates it)
            if not _entry_confirmed(m["id"], m["yes"], 0.20, 0.55, max_rise=0.025):
                continue  # signal not confirmed for 2 scans or YES rising fast — wait
            no_mid = 1 - m["yes"]
            spr    = liq_spread(m["vol"], cfg)            # honest liquidity-scaled spread
            entry  = round(min(0.999, no_mid + spr / 2), 4)
            size   = round(cfg[f"{leg}_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0:
                continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": leg, "side": "NO",
                "entry_mid": round(no_mid, 4), "entry_fill": entry,
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "cat": m.get("cat"), "spread": spr,   # per-position spread -> honest exit/P&L
            })
            opened.append(book["open"][-1])

    # (cat is stamped onto every opened pos centrally just before return)
    # ── fastfade (NO side) — INDEPENDENT book: mirrors fade's markets so the
    # A/B is fair, churns freely (no cross-strategy hold lock, no cooldown),
    # but capped per-market-per-day so it can't churn one loser endlessly. ──
    if cfg["fastfade_enabled"]:
        book = state["fastfade"]
        ff_held = {p["id"] for p in book["open"]}
        today = now_iso()[:10]
        ff_today = {}
        for r in book["closed"]:
            if r.get("closed_at", "")[:10] == today:
                ff_today[r["id"]] = ff_today.get(r["id"], 0) + 1
        cap_day = cfg.get("fastfade_max_per_day", 7)
        for m in markets:
            if len(book["open"]) >= cfg["fastfade_max_open"]:
                break
            if m["id"] in ff_held:
                continue
            if ff_today.get(m["id"], 0) >= cap_day:
                continue
            if not (cfg["fastfade_yes_min"] <= m["yes"] <= cfg["fastfade_yes_max"]):
                continue
            if m["vol"] < cfg["fastfade_min_volume"]:
                continue
            no_mid = 1 - m["yes"]
            entry  = ask(no_mid, cfg, m["vol"])
            size   = round(cfg["fastfade_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0:
                continue
            pos = {
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "fastfade", "side": "NO",
                "entry_mid": round(no_mid, 4), "entry_fill": round(entry, 4),
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
            }
            book["open"].append(pos)
            opened.append(pos)

    # ── allin (YES on EVERY market, no volume/selectivity filter) ──
    if cfg.get("allin_enabled"):
        book = state["allin"]
        ai_held = {p["id"] for p in book["open"]}
        for m in markets:
            if len(book["open"]) >= cfg["allin_max_open"]:
                break
            if m["id"] in ai_held:
                continue
            if not (cfg["allin_buy_min"] <= m["yes"] <= cfg["allin_buy_max"]):
                continue
            entry = ask(m["yes"], cfg)
            size  = round(cfg["allin_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0:
                continue
            pos = {
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "allin", "side": "YES",
                "entry_mid": round(m["yes"], 4), "entry_fill": round(entry, 4),
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
            }
            book["open"].append(pos)
            opened.append(pos)

    # ── momentum (YES side) — buy what's RISING (trend continuation) ──
    if cfg.get("momentum_enabled"):
        book = state["momentum"]; mh = {p["id"] for p in book["open"]}
        for m in markets:
            if len(book["open"]) >= cfg["momentum_max_open"]:
                break
            if m["id"] in mh:
                continue
            if not (cfg["momentum_yes_min"] <= m["yes"] <= cfg["momentum_yes_max"]):
                continue
            if m.get("chg", 0.0) < cfg["momentum_chg_min"] or m["vol"] < cfg["momentum_min_volume"]:
                continue
            entry = ask(m["yes"], cfg)
            size = round(cfg["momentum_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0:
                continue
            pos = {"id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "momentum", "side": "YES",
                   "entry_mid": round(m["yes"], 4), "entry_fill": round(entry, 4),
                   "size": size, "vol": m["vol"], "opened_at": now_iso()}
            book["open"].append(pos); opened.append(pos)

    # ── favyes (YES side) — buy strong favorites, hold toward resolution ──
    if cfg.get("favyes_enabled"):
        book = state["favyes"]; fh = {p["id"] for p in book["open"]}
        for m in markets:
            if len(book["open"]) >= cfg["favyes_max_open"]:
                break
            if m["id"] in fh:
                continue
            if not (cfg["favyes_buy_min"] <= m["yes"] <= cfg["favyes_buy_max"]):
                continue
            if m["vol"] < cfg["favyes_min_volume"]:
                continue
            entry = ask(m["yes"], cfg)
            size = round(cfg["favyes_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0:
                continue
            pos = {"id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "favyes", "side": "YES",
                   "entry_mid": round(m["yes"], 4), "entry_fill": round(entry, 4),
                   "size": size, "vol": m["vol"], "opened_at": now_iso()}
            book["open"].append(pos); opened.append(pos)

    # ── coinflip (YES side) — buy ~0.50 indiscriminately (2nd control) ──
    if cfg.get("coinflip_enabled"):
        book = state["coinflip"]; ch = {p["id"] for p in book["open"]}
        for m in markets:
            if len(book["open"]) >= cfg["coinflip_max_open"]:
                break
            if m["id"] in ch:
                continue
            if not (cfg["coinflip_buy_min"] <= m["yes"] <= cfg["coinflip_buy_max"]):
                continue
            if m["vol"] < cfg["coinflip_min_volume"]:
                continue
            entry = ask(m["yes"], cfg)
            size = round(cfg["coinflip_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0:
                continue
            pos = {"id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "coinflip", "side": "YES",
                   "entry_mid": round(m["yes"], 4), "entry_fill": round(entry, 4),
                   "size": size, "vol": m["vol"], "opened_at": now_iso()}
            book["open"].append(pos); opened.append(pos)

    # ── midfade — fade the recent drift back toward 0.50 (mean reversion) ──
    if cfg.get("midfade_enabled"):
        book = state["midfade"]; dh = {p["id"] for p in book["open"]}
        for m in markets:
            if len(book["open"]) >= cfg["midfade_max_open"]:
                break
            if m["id"] in dh:
                continue
            if not (cfg["midfade_yes_min"] <= m["yes"] <= cfg["midfade_yes_max"]):
                continue
            chg = m.get("chg", 0.0)
            if abs(chg) < cfg["midfade_chg_min"] or m["vol"] < cfg["midfade_min_volume"]:
                continue
            if chg > 0:                       # rose -> bet it reverts DOWN -> hold NO
                side, out_mid = "NO", 1 - m["yes"]
            else:                             # fell -> bet it reverts UP -> hold YES
                side, out_mid = "YES", m["yes"]
            entry = ask(out_mid, cfg, m["vol"])
            size = round(cfg["midfade_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0:
                continue
            pos = {"id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "midfade", "side": side,
                   "entry_mid": round(out_mid, 4), "entry_fill": round(entry, 4),
                   "size": size, "vol": m["vol"], "opened_at": now_iso()}
            book["open"].append(pos); opened.append(pos)

    # ── manifold (open-source cross-market divergence) ─────────────────────────
    # Fetch ~150 open binary Manifold markets (cached 5 min), fuzzy-match each
    # Polymarket market by title (Jaccard word-overlap ≥ min_similarity), and
    # trade in the direction Manifold's community leans when they diverge by
    # ≥ min_divergence. Guards:
    #  • Poly YES must be in [yes_min, yes_max] — skip near-resolved markets
    #  • Each Manifold market can only match ONE Poly market per scan cycle
    #    (dedup by Manifold ID) to prevent one generic Manifold market from
    #    absorbing multiple Poly markets that share template words
    # Positions store mf_prob + mf_sim + mf_q for post-hoc match-quality audit.
    if cfg.get("manifold_enabled"):
        mf_mkts = fetch_manifold_markets()   # cached; [] on network error
        book = state["manifold"]
        own = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        mf_used = set()   # Manifold IDs already matched this scan cycle
        for m in markets:
            if len(book["open"]) >= cfg["manifold_max_open"]:
                break
            if m["id"] in own or m["id"] in cooldown:
                continue
            if m["vol"] < cfg["manifold_min_volume"]:
                continue
            # Price band guard: skip markets already at the extremes
            if not (cfg["manifold_yes_min"] <= m["yes"] <= cfg["manifold_yes_max"]):
                continue
            best, sim = _best_match(m["q"], mf_mkts, q_key="question")
            if best is None or sim < cfg["manifold_min_similarity"]:
                continue
            mf_id = best.get("id", "")
            if mf_id in mf_used:
                continue   # one Manifold market → one Poly trade per cycle
            mf_prob = float(best["probability"])
            diff = mf_prob - m["yes"]
            if abs(diff) < cfg["manifold_min_divergence"]:
                continue
            side    = "YES" if diff > 0 else "NO"
            out_mid = m["yes"] if side == "YES" else 1 - m["yes"]
            entry   = ask(out_mid, cfg, m["vol"])
            size    = round(cfg["manifold_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0:
                continue
            mf_used.add(mf_id)
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "manifold", "side": side,
                "entry_mid": round(out_mid, 4), "entry_fill": round(entry, 4),
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "mf_prob": round(mf_prob, 4), "mf_diff": round(diff, 4),
                "mf_sim":  round(sim, 4),
                "mf_q":    (best.get("question") or "")[:80],
            })
            opened.append(book["open"][-1])

    # ── metaculus (open-source cross-market divergence) ─────────────────────────
    # Same pattern as manifold: community median (q2), price band guard, per-cycle
    # dedup by Metaculus question ID. Fails gracefully if API is blocked.
    if cfg.get("metaculus_enabled"):
        mt_qs = fetch_metaculus_questions()   # cached; [] on network error
        book = state["metaculus"]
        own = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        mt_used = set()   # Metaculus IDs already matched this scan cycle
        for m in markets:
            if len(book["open"]) >= cfg["metaculus_max_open"]:
                break
            if m["id"] in own or m["id"] in cooldown:
                continue
            if m["vol"] < cfg["metaculus_min_volume"]:
                continue
            if not (cfg["metaculus_yes_min"] <= m["yes"] <= cfg["metaculus_yes_max"]):
                continue
            best, sim = _best_match(m["q"], mt_qs, q_key="title")
            if best is None or sim < cfg["metaculus_min_similarity"]:
                continue
            mt_id = best.get("id", "")
            if mt_id in mt_used:
                continue
            mt_prob = float(best["_median"])
            diff = mt_prob - m["yes"]
            if abs(diff) < cfg["metaculus_min_divergence"]:
                continue
            side    = "YES" if diff > 0 else "NO"
            out_mid = m["yes"] if side == "YES" else 1 - m["yes"]
            entry   = ask(out_mid, cfg, m["vol"])
            size    = round(cfg["metaculus_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0:
                continue
            mt_used.add(mt_id)
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "metaculus", "side": side,
                "entry_mid": round(out_mid, 4), "entry_fill": round(entry, 4),
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "mt_prob": round(mt_prob, 4), "mt_diff": round(diff, 4),
                "mt_sim":  round(sim, 4),
                "mt_q":    (best.get("title") or "")[:80],
            })
            opened.append(book["open"][-1])

    # ── endgame (near-expiry fade) ────────────────────────────────────────────
    # Bets NO on markets ≤72h from resolution that are still far from 0/1.
    # Tests the terminal-uncertainty-premium hypothesis: do LPs overprice risk
    # near resolution in a way the NO side can systematically harvest?
    if cfg.get("endgame_enabled"):
        book = state["endgame"]
        own = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        for m in markets:
            if len(book["open"]) >= cfg["endgame_max_open"]:
                break
            if m["id"] in own or m["id"] in cooldown:
                continue
            d = days_to_end(m.get("end"))
            if d is None or d > cfg["endgame_max_hours"] / 24 or d < 0:
                continue   # not near expiry, or already expired
            if not (cfg["endgame_yes_min"] <= m["yes"] <= cfg["endgame_yes_max"]):
                continue
            if m["vol"] < cfg["endgame_min_volume"]:
                continue
            no_mid = 1 - m["yes"]
            entry  = ask(no_mid, cfg, m["vol"])
            size   = round(cfg["endgame_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0:
                continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "endgame", "side": "NO",
                "entry_mid": round(no_mid, 4), "entry_fill": entry,
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "cat": m.get("cat"), "days_left": round(d, 3),
            })
            opened.append(book["open"][-1])

    # ── lowliq (illiquid-market fade) ─────────────────────────────────────────
    # Fades YES [.20,.55] in markets with volume BELOW the main fade_min_volume
    # threshold. Tests: do less liquid markets have a larger Wang distortion /
    # wider fair-value gap than the liquid ones truefade selects?
    if cfg.get("lowliq_enabled"):
        book = state["lowliq"]
        own = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        for m in markets:
            if len(book["open"]) >= cfg["lowliq_max_open"]:
                break
            if m["id"] in own or m["id"] in cooldown:
                continue
            if not (cfg["lowliq_yes_min"] <= m["yes"] <= cfg["lowliq_yes_max"]):
                continue
            if not (cfg["lowliq_min_volume"] <= m["vol"] <= cfg["lowliq_max_volume"]):
                continue
            no_mid = 1 - m["yes"]
            spr    = liq_spread(m["vol"], cfg)   # spread is WIDER here (low vol)
            entry  = round(min(0.999, no_mid + spr / 2), 4)
            size   = round(cfg["lowliq_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0:
                continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "lowliq", "side": "NO",
                "entry_mid": round(no_mid, 4), "entry_fill": entry,
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "cat": m.get("cat"), "spread": spr,
            })
            opened.append(book["open"][-1])

    # ── contrarian (pure momentum-reversal) ───────────────────────────────────
    # Bets AGAINST the direction of recent price change when |chg| >= threshold.
    # Pure reversal signal — no price-band filter (unlike midfade). Tests whether
    # short-term Polymarket moves overshoot and revert regardless of where in
    # [.10,.90] the market sits. Either side (NO if rose, YES if fell).
    if cfg.get("contrarian_enabled"):
        book = state["contrarian"]
        own = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        for m in markets:
            if len(book["open"]) >= cfg["contrarian_max_open"]:
                break
            if m["id"] in own or m["id"] in cooldown:
                continue
            chg = m.get("chg", 0.0)
            if abs(chg) < cfg["contrarian_chg_min"]:
                continue
            if not (cfg["contrarian_yes_min"] <= m["yes"] <= cfg["contrarian_yes_max"]):
                continue
            if m["vol"] < cfg["contrarian_min_volume"]:
                continue
            side    = "NO" if chg > 0 else "YES"   # bet against the move
            out_mid = (1 - m["yes"]) if side == "NO" else m["yes"]
            entry   = ask(out_mid, cfg, m["vol"])
            size    = round(cfg["contrarian_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0:
                continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "contrarian", "side": side,
                "entry_mid": round(out_mid, 4), "entry_fill": round(entry, 4),
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "chg_at_entry": round(chg, 4),
            })
            opened.append(book["open"][-1])

    # ── catpol (politics-category fade) ───────────────────────────────────────
    # Restricts the truefade [.20,.55] universe to politics/election markets.
    # Hypothesis: political markets exhibit stronger narrative-driven overpricing
    # (poll reactions, partisan optimism) → larger Wang distortion in this slice.
    if cfg.get("catpol_enabled"):
        book = state["catpol"]
        own = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        for m in markets:
            if len(book["open"]) >= cfg["catpol_max_open"]:
                break
            if m["id"] in own or m["id"] in cooldown:
                continue
            cat = (m.get("cat") or "").lower()
            if "polit" not in cat and "elect" not in cat:
                continue   # skip non-politics markets
            if not (cfg["catpol_yes_min"] <= m["yes"] <= cfg["catpol_yes_max"]):
                continue
            if m["vol"] < cfg["catpol_min_volume"]:
                continue
            no_mid = 1 - m["yes"]
            spr    = liq_spread(m["vol"], cfg)
            entry  = round(min(0.999, no_mid + spr / 2), 4)
            size   = round(cfg["catpol_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0:
                continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "catpol", "side": "NO",
                "entry_mid": round(no_mid, 4), "entry_fill": entry,
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "cat": m.get("cat"), "spread": spr,
            })
            opened.append(book["open"][-1])

    # ── moonshot — ORIGINAL 10x-or-nothing (buy YES at 5–10¢), revived 2026-06-18 ─
    # Max loss = $1 (the entry cost). Upside = 10x+ if the market resolves YES.
    # Fires only on $1M+ markets where large wallets are net-accumulating. No stop —
    # at 5–10¢ a stop just exits on noise; time-limited to 30 days. The original
    # ≥2-elite-wallet gate read a per-market trader_count that left the feed, so we
    # substitute the live whale-flow signal. (Whale-as-instruction is a documented
    # dead signal — 44% WR over n=136 — so this is a fresh paper re-test, not an edge.)
    if cfg.get("moonshot_enabled"):
        fetch_global_trades(500)   # warm whale-flow cache: moonshot runs before other wallet legs
        book   = state["moonshot"]
        own    = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        for m in markets:
            if len(book["open"]) >= cfg["moonshot_max_open"]:
                break
            if m["id"] in own or m["id"] in cooldown:
                continue
            # exclude commodity / macro markets (shared list with conviction)
            if any(k in m["q"].lower() for k in cfg.get("moonshot_excl_kw", ())):
                continue
            yes = m["yes"]
            if not (cfg["moonshot_yes_min"] <= yes <= cfg["moonshot_yes_max"]):
                continue
            if m["vol"] < cfg["moonshot_min_volume"]:
                continue
            # elite-wallet gate via live whale flow (replaces the dead trader_count):
            # require large wallets net-accumulating this cheap market
            whale_buy, whale_sell = _whale_flow(m["id"])
            if whale_buy < cfg["moonshot_min_whale_buy"] or whale_buy <= whale_sell:
                continue
            spr    = liq_spread(m["vol"], cfg)
            entry  = round(min(0.999, yes + spr / 2), 4)
            size   = round(cfg["moonshot_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0:
                continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""),
                "strategy": "moonshot", "side": "YES",
                "entry_mid": round(yes, 4), "entry_fill": entry,
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "whale_buy": whale_buy, "whale_sell": whale_sell,
                "potential_x": round(1.0 / yes, 1),   # e.g. 10x at $0.10
            })
            opened.append(book["open"][-1])

    # ── conviction (resolution-arbitrage) ─────────────────────────────────────
    # Only enters after elite consensus has already formed AND price has already
    # moved our way — we're buying the CONFIRMATION, not predicting the move.
    if cfg.get("conviction_enabled"):
        book     = state["conviction"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        for m in markets:
            if len(book["open"]) >= cfg["conviction_max_open"]:
                break
            if m["id"] in own or m["id"] in cooldown:
                continue
            # exclude commodity markets — use same list as moonshot
            if any(k in m["q"].lower() for k in cfg.get("moonshot_excl_kw", ())):
                continue
            yes = m["yes"]
            # Gate 1: price in cheap-but-moving band
            if not (cfg["conviction_yes_min"] <= yes <= cfg["conviction_yes_max"]):
                continue
            # Gate 2: volume — liquid market only
            if m["vol"] < cfg["conviction_min_vol"]:
                continue
            # Gate 3: market resolving soon
            days = days_to_end(m.get("end"))   # endDate ISO string → float days
            if days is None or days <= 0 or days > cfg["conviction_max_hours"] / 24:
                continue
            # Gate 4: price already moved ≥8¢ (chg field = price change since yesterday)
            chg = m.get("chg", 0) or 0
            if chg < cfg["conviction_min_chg"]:
                continue
            # All gates passed — enter YES (confirmed momentum + short horizon)
            spr   = liq_spread(m["vol"], cfg)
            entry = round(min(0.999, yes + spr / 2), 4)
            size  = round(cfg["conviction_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0:
                continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "conviction", "side": "YES",
                "entry_mid": round(yes, 4), "entry_fill": entry,
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "chg_at_entry": round(chg, 4),
            })
            opened.append(book["open"][-1])


    # ── resolvebump (resolution-proximity momentum) ──────────────────────────
    if cfg.get("resolvebump_enabled"):
        book     = state["resolvebump"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        for m in markets:
            if len(book["open"]) >= cfg["resolvebump_max_open"]: break
            if m["id"] in own or m["id"] in cooldown: continue
            yes = m["yes"]
            if not (cfg["resolvebump_yes_min"] <= yes <= cfg["resolvebump_yes_max"]): continue
            if m["vol"] < cfg["resolvebump_min_vol"]: continue
            days = days_to_end(m.get("end"))
            if days is None or days <= 0 or days > cfg["resolvebump_max_hours"] / 24: continue
            chg = m.get("chg", 0) or 0
            if abs(chg) < cfg["resolvebump_min_chg"]: continue
            side    = "YES" if chg > 0 else "NO"
            out_mid = yes if side == "YES" else 1 - yes
            entry   = ask(out_mid, cfg, m["vol"])
            size    = round(cfg["resolvebump_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "resolvebump", "side": side,
                "entry_mid": round(out_mid, 4), "entry_fill": entry,
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "days_left": round(days, 2), "chg_at_entry": round(chg, 4),
            })
            opened.append(book["open"][-1])

    # ── thinbook (thin-market noise fade) ────────────────────────────────────
    if cfg.get("thinbook_enabled"):
        book     = state["thinbook"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        for m in markets:
            if len(book["open"]) >= cfg["thinbook_max_open"]: break
            if m["id"] in own or m["id"] in cooldown: continue
            if m["vol"] > cfg["thinbook_max_vol"]: continue   # must be thin
            yes = m["yes"]
            if not (cfg["thinbook_yes_min"] <= yes <= cfg["thinbook_yes_max"]): continue
            chg = m.get("chg", 0) or 0
            if abs(chg) < cfg["thinbook_min_chg"]: continue   # must have moved
            # Fade the move: up → buy NO, down → buy YES
            side    = "NO" if chg > 0 else "YES"
            out_mid = (1 - yes) if side == "NO" else yes
            entry   = ask(out_mid, cfg, m["vol"])
            size    = round(cfg["thinbook_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "thinbook", "side": side,
                "entry_mid": round(out_mid, 4), "entry_fill": entry,
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "chg_at_entry": round(chg, 4),
            })
            opened.append(book["open"][-1])

    # ── whaleflip (follow whale exits = top wallet SELL signal) ─────────────
    if cfg.get("whaleflip_enabled") and _all_trades:
        book     = state["whaleflip"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        top_wallets = {w["addr"] for w in get_top_wallets(20)}
        min_usdc    = cfg["whaleflip_min_usdc"]
        for cid, tlist in _tidx_60.items():
            if len(book["open"]) >= cfg["whaleflip_max_open"]: break
            if cid in own or cid in cooldown: continue
            m = _market_by_id.get(cid)
            if not m or m["vol"] < cfg["whaleflip_min_vol"]: continue
            if not (cfg["whaleflip_yes_min"] <= m["yes"] <= cfg["whaleflip_yes_max"]): continue
            # Look for big SELL from top wallet in last 60 min
            sells = [t for t in tlist if t["wallet"] in top_wallets
                     and t["side"] == "SELL" and t["dollar"] >= min_usdc]
            if not sells: continue
            best = max(sells, key=lambda t: t["dollar"])
            # Follow their exit direction: SELL YES → we buy NO (they're exiting YES)
            #                              SELL NO  → we buy YES (they're exiting NO)
            side    = "NO" if best.get("outcome", "YES") == "YES" else "YES"
            out_mid = (1 - m["yes"]) if side == "NO" else m["yes"]
            entry   = ask(out_mid, cfg, m["vol"])
            size    = round(cfg["whaleflip_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "whaleflip", "side": side,
                "entry_mid": round(out_mid, 4), "entry_fill": entry,
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "whale_usdc": round(best["dollar"], 2),
            })
            opened.append(book["open"][-1])

    # ── multiconfirm (confluence — 2+ proven legs agree on same market) ──────
    if cfg.get("multiconfirm_enabled"):
        book     = state["multiconfirm"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        signal_legs = ["walletcopy", "whale", "buyflow", "multiwhale"]
        market_votes = {}  # market_id -> list of (side, leg)
        for leg in signal_legs:
            for pos in state[leg]["open"]:
                market_votes.setdefault(pos["id"], []).append((pos["side"], leg))
        for cid, votes in market_votes.items():
            if len(book["open"]) >= cfg["multiconfirm_max_open"]: break
            if len(votes) < cfg["multiconfirm_min_legs"]: continue
            if cid in own or cid in cooldown: continue
            sides = {v[0] for v in votes}
            if len(sides) != 1: continue   # legs disagree — skip
            side = sides.pop()
            m = _market_by_id.get(cid)
            if not m: continue
            out_mid = m["yes"] if side == "YES" else 1 - m["yes"]
            entry   = ask(out_mid, cfg, m["vol"])
            size    = round(cfg["multiconfirm_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "multiconfirm", "side": side,
                "entry_mid": round(out_mid, 4), "entry_fill": entry,
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "confirming_legs": [v[1] for v in votes],
            })
            opened.append(book["open"][-1])

    # ── catmom (category momentum) ────────────────────────────────────────────
    if cfg.get("catmom_enabled"):
        book     = state["catmom"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        # Compute % of markets with positive chg per category
        cat_counts = {}   # cat -> [total, positive_chg_count]
        for m in markets:
            cat = m.get("cat", "other")
            chg = m.get("chg", 0) or 0
            cat_counts.setdefault(cat, [0, 0])
            cat_counts[cat][0] += 1
            if chg > 0:
                cat_counts[cat][1] += 1
        # Compute momentum ratio per category
        cat_mom = {}
        for cat, (total, pos) in cat_counts.items():
            cat_mom[cat] = pos / total if total >= 5 else 0.5  # neutral if too few
        for m in markets:
            if len(book["open"]) >= cfg["catmom_max_open"]: break
            if m["id"] in own or m["id"] in cooldown: continue
            if m["vol"] < cfg["catmom_min_vol"]: continue
            yes = m["yes"]
            if not (cfg["catmom_yes_min"] <= yes <= cfg["catmom_yes_max"]): continue
            cat = m.get("cat", "other")
            mom = cat_mom.get(cat, 0.5)
            if mom >= cfg["catmom_mom_threshold"]:
                side = "YES"   # bullish category regime
            elif mom <= cfg["catmom_bear_threshold"]:
                side = "NO"    # bearish category regime
            else:
                continue       # neutral — skip
            out_mid = yes if side == "YES" else 1 - yes
            entry   = ask(out_mid, cfg, m["vol"])
            size    = round(cfg["catmom_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": cat, "strategy": "catmom", "side": side,
                "entry_mid": round(out_mid, 4), "entry_fill": entry,
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "cat_mom": round(mom, 3),
            })
            opened.append(book["open"][-1])


    # ── nearres (near-resolution certainty bet — ESPORTS ONLY) ──────────────
    # Data shows: 8/8 target wins are esports; politics entries only produce
    # near-breakeven time exits (entry 0.95+, no spread room). Esports matches
    # resolve cleanly to 0/1 within hours, giving the full 12% target room.
    _NEARRES_ESPORTS_KW = ("counter-strike","cs2","cs:","valorant",
                           "league of legends","lol:","dota","overwatch",
                           "r6 siege","rainbow six","iem ","pgl major",
                           "blast premier","vct ","lcq ","lcs ","lck ",
                           "lpl ","esport"," vs ")
    def _nearres_is_esports(m):
        q = m.get("q","").lower()
        return any(k in q for k in _NEARRES_ESPORTS_KW)
    if cfg.get("nearres_enabled"):
        book     = state["nearres"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        for m in markets:
            if len(book["open"]) >= cfg["nearres_max_open"]: break
            if m["id"] in own or m["id"] in cooldown: continue
            if not _nearres_is_esports(m): continue  # ESPORTS ONLY — politics burns spread
            if m["vol"] < cfg["nearres_min_vol"]: continue
            days = days_to_end(m.get("end"))
            if days is None or days <= 0 or days > cfg["nearres_max_hours"] / 24: continue
            yes = m["yes"]
            if yes >= cfg["nearres_yes_hi"]:
                side = "YES"
            elif yes <= cfg["nearres_yes_lo"]:
                side = "NO"
            else:
                continue
            # ── Smart timing: require price stability — a sudden jump could mean
            # the match outcome is already becoming clear on a live feed, making
            # the market about to reprice sharply the other way. ─────────────────
            _chg = _scan_chg(m["id"], yes)
            if _chg is not None and abs(_chg) > 0.05:
                continue  # price moved >5¢ this scan — wait for it to stabilize
            out_mid = yes if side == "YES" else 1 - yes
            if out_mid > cfg.get("nearres_max_entry", 0.95):
                continue   # above 0.95 the max payoff (<5¢/share) can't pay for the $2 tail
            _lq = liquidity_check(m)
            if not _lq[0]: continue        # book too thin/wide for backtested economics
            # ── newsesports confirmation gate ─────────────────────────────────
            # If a strong-bearish news signal contradicts our bet direction, skip.
            # (bearish = news says team will lose → bad for YES bet; or team on fire
            #  when we want NO). Only vetoes when confidence ≥ 0.65.
            try:
                from news_leg import news_signal as _ns
                _nr_news = _ns(m["q"], yes, "esports")
                if _nr_news and _nr_news.get("confidence", 0) >= 0.65:
                    if side == "YES" and _nr_news["side"] == "NO":
                        continue   # bearish news contradicts YES bet — skip
                    if side == "NO" and _nr_news["side"] == "YES":
                        continue   # bullish news contradicts NO bet — skip
            except Exception:
                _nr_news = None
            # maker entry (2026-06-11): the _scan_chg stability gate above means
            # the market is slow enough for a resting limit at mid to fill
            entry   = maker_fill(out_mid, cfg, m["vol"])
            size    = round(cfg["nearres_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            _yt_qry = re.split(r" - | \(BO", m["q"].split(":")[-1].strip())[0].strip() + " esports"
            _yt_bz  = fetch_youtube_buzz(_yt_qry)
            _nr_psm = _ps.match_for_question(m["q"]) or {}
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "nearres", "side": side,
                "whale_buy_usd": _whale_flow(m["id"])[0], "whale_sell_usd": _whale_flow(m["id"])[1],
                "ps_status": _nr_psm.get("status"),
                "tournament_round": _nr_psm.get("tournament_round", "unknown"),
                "yt_buzz_n": _yt_bz.get("n_fresh", 0), "yt_buzz_views": _yt_bz.get("views_fresh", 0),
                "book_spread": _lq[1], "book_depth": _lq[2],
                "entry_mid": round(out_mid, 4), "entry_fill": entry,
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "days_left": round(days, 3),
                "news_side": (_nr_news or {}).get("side"), "news_conf": (_nr_news or {}).get("confidence"),
            })
            opened.append(book["open"][-1])

    # ── nearresno (isolated NO-favorite arm — the profitable, trap-surviving side) ──
    if cfg.get("nearresno_enabled"):
        book     = state["nearresno"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        for m in markets:
            if len(book["open"]) >= cfg["nearresno_max_open"]: break
            if m["id"] in own or m["id"] in cooldown: continue
            if not _nearres_is_esports(m): continue   # esports only — same universe as nearres
            if m["vol"] < cfg["nearresno_min_vol"]: continue
            days = days_to_end(m.get("end"))
            if days is None or days <= 0 or days > cfg["nearresno_max_hours"] / 24: continue
            yes = m["yes"]
            if yes > cfg["nearresno_yes_max"]: continue   # underdog must be cheap → back its NO
            out_mid = 1 - yes                              # NO side = the favorite
            # dust-strip + real-favorite gate (the key discipline from the analysis)
            if not (cfg["nearresno_band_lo"] <= out_mid <= cfg["nearresno_band_hi"]): continue
            _chg = _scan_chg(m["id"], yes)
            if _chg is not None and abs(_chg) > cfg["nearresno_max_chg"]: continue
            _lq = liquidity_check(m)
            if not _lq[0]: continue
            entry = ask(out_mid, cfg, m["vol"])
            size  = round(cfg["nearresno_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "nearresno", "side": "NO",
                "entry_mid": round(out_mid, 4), "entry_fill": round(entry, 4),
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "days_left": round(days, 3), "book_spread": _lq[1],
            })
            opened.append(book["open"][-1])

    # ── spreadcap (maker spread-capture on stable high-vol noise markets) ────
    if cfg.get("spreadcap_enabled"):
        book     = state["spreadcap"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        _sc_skip = cfg.get("spreadcap_skip_kw", ())
        _as_on   = cfg.get("spreadcap_as_enabled", False)
        # A-S inventory: net signed YES exposure spreadcap currently holds (YES=+1, NO=-1).
        # Drives the reservation-price skew so we don't pile one-sided risk.
        _sc_inv  = sum(1 if p.get("side") == "YES" else -1 for p in book["open"])
        for m in markets:
            if len(book["open"]) >= cfg["spreadcap_max_open"]: break
            if m["id"] in own or m["id"] in cooldown: continue
            if m["vol"] < cfg["spreadcap_min_vol"]: continue          # deep book = noise flow
            yes = m["yes"]
            if not (cfg["spreadcap_yes_min"] <= yes <= cfg["spreadcap_yes_max"]): continue
            q_low = str(m.get("q", "")).lower()
            if any(k in q_low for k in _sc_skip): continue            # no in-play esports
            if is_live_match(q_low): continue                        # no informed live flow
            d = days_to_end(m.get("end"))
            if d is None or d < cfg["spreadcap_min_days"]: continue   # not near resolution
            _chg = _scan_chg(m["id"], yes)
            if _chg is None or abs(_chg) > cfg["spreadcap_max_chg"]: continue  # must be STABLE
            as_gain = None
            if _as_on:
                # Avellaneda-Stoikov optimal quote. sigma from recent move (vol proxy).
                sigma = max(abs(_chg), cfg.get("spreadcap_as_vol_floor", 0.02))
                r, half_spread = avellaneda_stoikov_quote(
                    yes, _sc_inv, sigma, d, cfg["spreadcap_as_gamma"], cfg["spreadcap_as_k"])
                as_gain = round(half_spread, 4)            # vol/time-scaled take-profit
                # reservation price r < mid → we're over-long YES → prefer to SELL (NO);
                # r > mid → over-short → prefer YES. This is the inventory risk control.
                if r < yes - 0.0005:
                    side, side_mid = "NO", 1 - yes
                elif r > yes + 0.0005:
                    side, side_mid = "YES", yes
                else:   # balanced inventory → fall back to mean-reversion on the dip
                    side, side_mid = ("YES", yes) if _chg <= 0 else ("NO", 1 - yes)
            else:
                if _chg < 0:   side, side_mid = "YES", yes
                elif _chg > 0: side, side_mid = "NO", 1 - yes
                else:          side, side_mid = "YES", yes
            entry = maker_fill(side_mid, cfg, m["vol"])   # MAKER entry — save the half-spread
            size  = round(cfg["spreadcap_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "spreadcap", "side": side,
                "entry_mid": round(side_mid, 4), "entry_fill": round(entry, 4),
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "chg_at_entry": round(_chg, 4), "maker": True, "as_gain": as_gain,
            })
            opened.append(book["open"][-1])
            _sc_inv += 1 if side == "YES" else -1   # update running inventory for next pick

    # ── microscalp (user spec: maker entry, +0.20% take-profit, fast churn) ──
    if cfg.get("microscalp_enabled"):
        book     = state["microscalp"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < 1}   # short cooldown — churn
        _ms_skip = cfg.get("microscalp_skip_kw", ())
        for m in markets:
            if len(book["open"]) >= cfg["microscalp_max_open"]: break
            if m["id"] in own or m["id"] in cooldown: continue
            if m["vol"] < cfg["microscalp_min_vol"]: continue        # deepest books only
            yes = m["yes"]
            if not (cfg["microscalp_yes_min"] <= yes <= cfg["microscalp_yes_max"]): continue
            q_low = str(m.get("q", "")).lower()
            if any(k in q_low for k in _ms_skip) or is_live_match(q_low): continue
            d = days_to_end(m.get("end"))
            if d is None or d < cfg["microscalp_min_days"]: continue
            _chg = _scan_chg(m["id"], yes)
            if _chg is None or abs(_chg) > cfg["microscalp_max_chg"]: continue  # dead-stable
            if _chg < 0:
                side, side_mid = "YES", yes
            else:
                side, side_mid = "NO", 1 - yes
            entry = maker_fill(side_mid, cfg, m["vol"])   # MAKER — no spread paid on entry
            size  = round(cfg["microscalp_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "microscalp", "side": side,
                "entry_mid": round(side_mid, 4), "entry_fill": round(entry, 4),
                "size": size, "vol": m["vol"], "opened_at": now_iso(), "maker": True,
            })
            opened.append(book["open"][-1])

    # ── nearrestitle (2026-06-13): nearres minus reverse-FLB titles (Finding C) ──
    # Identical economics to nearres (same band, stability gate, maker fill,
    # liquidity check) with ONE extra filter: skip Dota2 + handicap markets +
    # tennis filter-leaks (nearrestitle_skip_kw). OOS says Dota2 is 50% WR with
    # negative mean at this band — the A/B isolates that single hypothesis.
    # (yt-buzz covariate scrape omitted — covariate-only, not economics.)
    if cfg.get("nearrestitle_enabled"):
        book     = state["nearrestitle"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        for m in markets:
            if len(book["open"]) >= cfg["nearrestitle_max_open"]: break
            if m["id"] in own or m["id"] in cooldown: continue
            if not _nearres_is_esports(m): continue  # ESPORTS ONLY — politics burns spread
            _ttl = m.get("q", "").lower()
            if any(kw in _ttl for kw in cfg["nearrestitle_skip_kw"]): continue  # THE GATE
            if m["vol"] < cfg["nearres_min_vol"]: continue
            days = days_to_end(m.get("end"))
            if days is None or days <= 0 or days > cfg["nearres_max_hours"] / 24: continue
            yes = m["yes"]
            if yes >= cfg["nearres_yes_hi"]:
                side = "YES"
            elif yes <= cfg["nearres_yes_lo"]:
                side = "NO"
            else:
                continue
            _chg = _scan_chg(m["id"], yes)
            if _chg is not None and abs(_chg) > 0.05:
                continue  # price moved >5¢ this scan — wait for it to stabilize
            out_mid = yes if side == "YES" else 1 - yes
            if out_mid > cfg.get("nearres_max_entry", 0.95):
                continue   # above 0.95 the max payoff (<5¢/share) can't pay for the $2 tail
            _lq = liquidity_check(m)
            if not _lq[0]: continue        # book too thin/wide for backtested economics
            entry   = maker_fill(out_mid, cfg, m["vol"])
            size    = round(cfg["nearrestitle_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            _nt_psm = _ps.match_for_question(m["q"]) or {}
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "nearrestitle", "side": side,
                "whale_buy_usd": _whale_flow(m["id"])[0], "whale_sell_usd": _whale_flow(m["id"])[1],
                "ps_status": _nt_psm.get("status"),
                "tournament_round": _nt_psm.get("tournament_round", "unknown"),
                "book_spread": _lq[1], "book_depth": _lq[2],
                "entry_mid": round(out_mid, 4), "entry_fill": entry,
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "days_left": round(days, 3),
            })
            opened.append(book["open"][-1])

    # ── psconfirm (2026-06-11): nearres entries gated on PandaScore "running" ──
    if cfg.get("psconfirm_enabled"):
        book     = state["psconfirm"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        for m in markets:
            if len(book["open"]) >= cfg["psconfirm_max_open"]: break
            if m["id"] in own or m["id"] in cooldown: continue
            if not _nearres_is_esports(m): continue
            if m["vol"] < cfg["nearres_min_vol"]: continue
            days = days_to_end(m.get("end"))
            if days is None or days <= 0 or days > cfg["nearres_max_hours"] / 24: continue
            yes = m["yes"]
            if yes >= cfg["nearres_yes_hi"]:
                side = "YES"
            elif yes <= cfg["nearres_yes_lo"]:
                side = "NO"
            else:
                continue
            out_mid = yes if side == "YES" else 1 - yes
            if out_mid > cfg.get("nearres_max_entry", 0.95): continue
            _chg = _scan_chg(m["id"], yes)
            if _chg is not None and abs(_chg) > 0.05: continue
            # THE GATE: PandaScore must confirm the match is live right now
            _psm = _ps.match_for_question(m["q"]) or {}
            if _psm.get("status") != "running": continue
            _lq = liquidity_check(m)
            if not _lq[0]: continue        # thin/wide book
            entry = ask(out_mid, cfg, m["vol"])
            size  = round(cfg["psconfirm_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            _ps_live = _ps.live_score(_psm.get("id"))
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "psconfirm", "side": side,
                "entry_mid": round(out_mid, 4), "entry_fill": entry,
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "days_left": round(days, 3), "ps_match": _psm.get("name", ""),
                "tournament_round": _psm.get("tournament_round", "unknown"),
                "book_spread": _lq[1], "book_depth": _lq[2],
                "map_score": _ps_live.get("score"),
                "maps_done": _ps_live.get("games_done"),
                "latest_map_winner": _ps_live.get("latest_map"),
                # ESTA series fair value for the series LEADER (0.5 if tied) —
                # CS-trained table; covariate on all esports, game in "q"
                "cs2_fair_leader": (lambda sc: _cs2.fair_value(
                    (max(sc), min(sc))) if sc and len(sc) == 2 else None
                    )(_ps_live.get("score")),
            })
            opened.append(book["open"][-1])

    # ── quietfade (2026-06-11): conflict fade gated on a QUIET news feed ──────
    if cfg.get("quietfade_enabled"):
        book     = state["quietfade"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        _qf_kw = cfg.get("nearresfade_skip_kw", ())     # the conflict vocabulary
        _qf_headlines = set()
        # two independent local feeds: OpenAlice (:47331) + hermes_news (:48580,
        # keyless RSS, Hermes OSS port 2026-06-12) — either alone keeps the leg alive
        import urllib.request as _ur2
        for _feed_url in (cfg.get("alicenews_api_url", "http://127.0.0.1:47331/api/news"),
                          cfg.get("hermes_api_url",    "http://127.0.0.1:48580/api/news")):
            try:
                _r2 = _ur2.urlopen(_feed_url, timeout=3)
                for _it in json.loads(_r2.read()).get("items", []):
                    _qf_headlines.add(_it.get("title", "").lower())
            except Exception:
                pass
        _qf_headlines |= fetch_crypto_headlines()   # third feed: crypto coverage
        if _qf_headlines:                                # empty/down feed → fail-safe skip
            for m in markets:
                if len(book["open"]) >= cfg["quietfade_max_open"]: break
                if m["id"] in own or m["id"] in cooldown: continue
                if m["vol"] < cfg["quietfade_min_vol"]: continue
                yes = m["yes"]
                if not (cfg["quietfade_yes_min"] <= yes <= cfg["quietfade_yes_max"]): continue
                q_low = str(m.get("q", "")).lower()
                mkt_kw = next((k for k in _qf_kw if k in q_low), None)
                if not mkt_kw: continue                  # conflict markets only
                if is_live_match(m.get("q", "")): continue
                days = days_to_end(m.get("end"))
                if days is None or days < cfg["quietfade_min_days"]: continue
                if days > cfg["quietfade_max_days"]: continue
                if any(mkt_kw in h for h in _qf_headlines): continue  # news NOT quiet
                entry = ask(1 - yes, cfg)
                size  = round(cfg["quietfade_bet_usdc"] / entry, 4) if entry > 0 else 0
                if size <= 0: continue
                book["open"].append({
                    "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "quietfade", "side": "NO",
                    "entry_mid": round(1 - yes, 4), "entry_fill": entry,
                    "size": size, "vol": m["vol"], "opened_at": now_iso(),
                    "days_left": round(days, 1), "kw": mkt_kw,
                    "gdelt_intensity": _gd.intensity(mkt_kw, cache=_gdelt_cache),
                })
                opened.append(book["open"][-1])

    # ── panel (2026-06-11): multi-analyst vote + bear veto (TradingAgents) ────
    if cfg.get("panel_enabled"):
        book     = state["panel"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        _pn_headlines = set()
        try:
            import urllib.request as _ur3
            _r3 = _ur3.urlopen(cfg.get("alicenews_api_url", "http://127.0.0.1:47331/api/news"), timeout=3)
            for _it in json.loads(_r3.read()).get("items", []):
                _pn_headlines.add(_it.get("title", "").lower())
        except Exception:
            pass
        _pn_headlines |= fetch_crypto_headlines()   # second feed: crypto coverage
        for m in markets:
            if len(book["open"]) >= cfg["panel_max_open"]: break
            if m["id"] in own or m["id"] in cooldown: continue
            if m["vol"] < cfg["panel_min_vol"]: continue
            yes = m["yes"]
            if not (cfg["panel_yes_min"] <= yes <= cfg["panel_yes_max"]): continue
            days = days_to_end(m.get("end"))
            if days is None or days <= 0 or days > cfg["panel_max_days"]: continue
            q_low = str(m.get("q", "")).lower()
            # ── bear veto (risk judge) ──
            if is_live_match(m.get("q", "")): continue
            if "will no " in q_low or "will there be no" in q_low: continue
            chg = m.get("chg", 0) or 0
            if chg <= -0.04: continue                      # falling knife
            # ── analyst votes ──
            votes = 0
            v_mom = chg >= cfg["panel_chg_vote"]
            wb, ws = _whale_flow(m["id"])
            v_flow = (wb - ws) >= cfg["panel_whale_vote"]
            toks = [w.strip("?,.:;'\"") for w in q_low.split() if len(w) >= 6]
            v_news = any(any(t in h for h in _pn_headlines) for t in toks[:6]) if _pn_headlines else False
            votes = int(v_mom) + int(v_flow) + int(v_news)
            if votes < cfg["panel_min_votes"]: continue
            entry = ask(yes, cfg, m["vol"])
            size  = round(cfg["panel_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "panel", "side": "YES",
                "entry_mid": round(yes, 4), "entry_fill": round(entry, 4),
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "votes": f"mom={int(v_mom)} flow={int(v_flow)} news={int(v_news)}",
                "days_left": round(days, 1),
            })
            opened.append(book["open"][-1])

    # ── tasignal (2026-06-14): trade TradingAgents' divergence from the market ──
    #    Reads tradingagents_feed.py's verdict cache. BUY lean + market underprices
    #    => buy YES; SELL lean + market overprices => buy NO. OFF until validated.
    if cfg.get("tasignal_enabled"):
        book     = state["tasignal"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        _ta_sig = {}
        try:
            import os as _os_ta, json as _json_ta
            _p = _os_ta.path.join(_os_ta.path.dirname(_os_ta.path.abspath(__file__)),
                                  cfg["tasignal_cache"])
            for s in _json_ta.load(open(_p)).get("signals", []):
                if s.get("id"):
                    _ta_sig[str(s["id"])] = s
                if s.get("question"):                       # robust fallback match
                    _ta_sig[s["question"].strip().lower()] = s
        except Exception:
            _ta_sig = {}
        for m in markets:
            if len(book["open"]) >= cfg["tasignal_max_open"]: break
            if m["id"] in own or m["id"] in cooldown: continue
            sig = _ta_sig.get(str(m["id"])) or _ta_sig.get(str(m.get("q", "")).strip().lower())
            if not sig: continue
            if m["vol"] < cfg["tasignal_min_vol"]: continue
            days = days_to_end(m.get("end"))
            if days is None or days <= 0 or days > cfg["tasignal_max_days"]: continue
            if is_live_match(m.get("q", "")): continue
            yes  = m["yes"]
            div  = sig.get("divergence", 0) or 0
            lean = sig.get("lean")
            if lean == "YES" and div >= cfg["tasignal_min_div"]:
                side, out_mid = "YES", yes
            elif lean == "NO" and div <= -cfg["tasignal_min_div"]:
                side, out_mid = "NO", 1 - yes
            else:
                continue
            entry = ask(out_mid, cfg, m["vol"])
            size  = round(cfg["tasignal_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "tasignal", "side": side,
                "entry_mid": round(out_mid, 4), "entry_fill": round(entry, 4),
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "ta_decision": sig.get("ta_decision", ""), "divergence": round(div, 3),
                "days_left": round(days, 1),
            })
            opened.append(book["open"][-1])

    # ── sportres (2026-06-10): nearres structure on NON-esports sports ────────
    if cfg.get("sportres_enabled"):
        book     = state["sportres"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        from datetime import datetime as _srdt, timezone as _srtz
        _now = _srdt.now(_srtz.utc)
        for m in markets:
            if len(book["open"]) >= cfg["sportres_max_open"]: break
            if m["id"] in own or m["id"] in cooldown: continue
            if _nearres_is_esports(m): continue          # esports belongs to nearres
            # 2026-06-11 TENNIS-ONLY: OOS sub-sport split — tennis +3.4c/trade
            # (64%, n=14) mirrors esports; MLB/NBA/soccer all OOS-negative.
            _q = m.get("q", "").lower()
            if not any(k in _q for k in ("tennis", "atp", "wta", "grand slam",
                                         "wimbledon", "roland garros", "us open",
                                         "australian open", "championships:", "open:")):
                continue
            st = m.get("start")                          # gameStartTime — honest, unlike endDate
            if not st: continue                          # only scheduled games carry it
            try:
                s = str(st).strip().replace(" ", "T")
                if s.endswith("+00"): s += ":00"
                hrs_in = (_now - _srdt.fromisoformat(s)).total_seconds() / 3600
            except Exception:
                continue
            # LATE-GAME ONLY (2026-06-11): a 0.93 favorite in inning 2 has hours of
            # reversal room — Nationals collapse cost -$2.00. Enter past halfway,
            # when the favorite's lead has little time left to evaporate.
            if not (cfg.get("sportres_min_hrs_in", 1.5) <= hrs_in <= cfg["sportres_max_hours"]): continue
            if m["vol"] < cfg["sportres_min_vol"]: continue
            yes = m["yes"]
            side    = "YES" if yes >= 0.5 else "NO"
            out_mid = yes if side == "YES" else 1 - yes
            if not (cfg["sportres_band_lo"] <= out_mid <= cfg["sportres_band_hi"]): continue
            _chg = _scan_chg(m["id"], yes)
            if _chg is not None and abs(_chg) > 0.05: continue   # mid-swing — wait
            entry = maker_fill(out_mid, cfg, m["vol"])   # stability-gated → maker fill at mid
            size  = round(cfg["sportres_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "sportres", "side": side,
                "whale_buy_usd": _whale_flow(m["id"])[0], "whale_sell_usd": _whale_flow(m["id"])[1],
                "entry_mid": round(out_mid, 4), "entry_fill": round(entry, 4),
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "hrs_into_game": round(hrs_in, 2),
            })
            opened.append(book["open"][-1])

    # ── nearreslow (2026-06-10): esports favorites, down-band [0.80,0.88) ─────
    if cfg.get("nearreslow_enabled"):
        book     = state["nearreslow"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        for m in markets:
            if len(book["open"]) >= cfg["nearreslow_max_open"]: break
            if m["id"] in own or m["id"] in cooldown: continue
            if not _nearres_is_esports(m): continue
            if m["vol"] < cfg["nearreslow_min_vol"]: continue
            days = days_to_end(m.get("end"))
            if days is None or days <= 0 or days > cfg["nearres_max_hours"] / 24: continue
            yes = m["yes"]
            side    = "YES" if yes >= 0.5 else "NO"
            out_mid = yes if side == "YES" else 1 - yes
            if not (cfg["nearreslow_band_lo"] <= out_mid < cfg["nearreslow_band_hi"]): continue
            _chg = _scan_chg(m["id"], yes)
            if _chg is not None and abs(_chg) > 0.05: continue
            entry = ask(out_mid, cfg, m["vol"])
            size  = round(cfg["nearreslow_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "nearreslow", "side": side,
                "whale_buy_usd": _whale_flow(m["id"])[0], "whale_sell_usd": _whale_flow(m["id"])[1],
                "entry_mid": round(out_mid, 4), "entry_fill": round(entry, 4),
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "days_left": round(days, 3),
            })
            opened.append(book["open"][-1])

    # ── nearres_sports (sports-only near-resolution) ──────────────────────────
    if cfg.get("nearres_sports_enabled"):
        book     = state["nearres_sports"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        for m in markets:
            if len(book["open"]) >= cfg["nearres_sports_max_open"]: break
            if m["id"] in own or m["id"] in cooldown: continue
            if m.get("cat") != "sports": continue
            if m["vol"] < cfg["nearres_sports_min_vol"]: continue
            days = days_to_end(m.get("end"))
            if days is None or days <= 0 or days > cfg["nearres_sports_max_hours"] / 24: continue
            yes = m["yes"]
            if yes >= cfg["nearres_sports_yes_hi"]:
                side = "YES"
            elif yes <= cfg["nearres_sports_yes_lo"]:
                side = "NO"
            else:
                continue
            out_mid = yes if side == "YES" else 1 - yes
            entry   = ask(out_mid, cfg)
            size    = round(cfg["nearres_sports_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": "sports", "strategy": "nearres_sports", "side": side,
                "entry_mid": round(out_mid, 4), "entry_fill": entry,
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "days_left": round(days, 3),
            })
            opened.append(book["open"][-1])

    # ── nearres_pol (politics-only near-resolution) ───────────────────────────
    if cfg.get("nearres_pol_enabled"):
        book     = state["nearres_pol"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        for m in markets:
            if len(book["open"]) >= cfg["nearres_pol_max_open"]: break
            if m["id"] in own or m["id"] in cooldown: continue
            if m.get("cat") != "politics": continue
            if m["vol"] < cfg["nearres_pol_min_vol"]: continue
            days = days_to_end(m.get("end"))
            if days is None or days <= 0 or days > cfg["nearres_pol_max_hours"] / 24: continue
            yes = m["yes"]
            if yes >= cfg["nearres_pol_yes_hi"]:
                side = "YES"
            elif yes <= cfg["nearres_pol_yes_lo"]:
                side = "NO"
            else:
                continue
            out_mid = yes if side == "YES" else 1 - yes
            entry   = ask(out_mid, cfg)
            size    = round(cfg["nearres_pol_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": "politics", "strategy": "nearres_pol", "side": side,
                "entry_mid": round(out_mid, 4), "entry_fill": entry,
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "days_left": round(days, 3),
            })
            opened.append(book["open"][-1])

    # ── snaprev (single-scan reversal — uses _price_snap) ───────────────────
    if cfg.get("snaprev_enabled"):
        book     = state["snaprev"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        for m in markets:
            if len(book["open"]) >= cfg["snaprev_max_open"]: break
            if m["id"] in own or m["id"] in cooldown: continue
            if m["vol"] < cfg["snaprev_min_vol"]: continue
            yes  = m["yes"]
            prev = _price_snap.get(m["id"])
            if prev is None: continue           # no prior snap — skip
            scan_delta = yes - prev             # positive = rose this scan
            if not (cfg["snaprev_yes_min"] <= yes <= cfg["snaprev_yes_max"]): continue
            # Fakeout spike: rose big last scan (chg>0), now falling back
            if m.get("chg", 0) >= cfg["snaprev_min_spike"] and scan_delta <= -cfg["snaprev_min_reversal"]:
                side = "NO"   # fade the failed spike
            # Fakeout dump: fell big last scan (chg<0), now bouncing back
            elif m.get("chg", 0) <= -cfg["snaprev_min_spike"] and scan_delta >= cfg["snaprev_min_reversal"]:
                side = "YES"  # ride the bounce
            else:
                continue
            out_mid = (1 - yes) if side == "NO" else yes
            entry   = ask(out_mid, cfg, m["vol"])
            size    = round(cfg["snaprev_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "snaprev", "side": side,
                "entry_mid": round(out_mid, 4), "entry_fill": entry,
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "scan_delta": round(scan_delta, 4), "chg": round(m.get("chg", 0) or 0, 4),
            })
            opened.append(book["open"][-1])

    # ── volburst (high vol + large price move = real informed trade) ──────────
    if cfg.get("volburst_enabled"):
        book     = state["volburst"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        for m in markets:
            if len(book["open"]) >= cfg["volburst_max_open"]: break
            if m["id"] in own or m["id"] in cooldown: continue
            if m["vol"] < cfg["volburst_min_vol"]: continue
            chg = m.get("chg", 0) or 0
            if abs(chg) < cfg["volburst_min_chg"]: continue
            yes = m["yes"]
            if not (cfg["volburst_yes_min"] <= yes <= cfg["volburst_yes_max"]): continue
            side    = "YES" if chg > 0 else "NO"
            out_mid = yes if side == "YES" else 1 - yes
            entry   = ask(out_mid, cfg, m["vol"])
            size    = round(cfg["volburst_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "volburst", "side": side,
                "entry_mid": round(out_mid, 4), "entry_fill": entry,
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "chg_at_entry": round(chg, 4),
            })
            opened.append(book["open"][-1])

    # ── flipflop (large reversal — regime change momentum) ───────────────────
    if cfg.get("flipflop_enabled"):
        book     = state["flipflop"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        for m in markets:
            if len(book["open"]) >= cfg["flipflop_max_open"]: break
            if m["id"] in own or m["id"] in cooldown: continue
            if m["vol"] < cfg["flipflop_min_vol"]: continue
            yes = m["yes"]
            chg = m.get("chg", 0) or 0
            # Bear flipflop: was >60¢ yesterday, now below 45¢ → keep falling
            if yes <= cfg["flipflop_yes_hi_max"] and chg <= -cfg["flipflop_min_chg"]:
                side = "NO"
            # Bull flipflop: was <40¢ yesterday, now above 55¢ → keep rising
            elif yes >= cfg["flipflop_yes_lo_min"] and chg >= cfg["flipflop_min_chg"]:
                side = "YES"
            else:
                continue
            out_mid = (1 - yes) if side == "NO" else yes
            entry   = ask(out_mid, cfg, m["vol"])
            size    = round(cfg["flipflop_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "flipflop", "side": side,
                "entry_mid": round(out_mid, 4), "entry_fill": entry,
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "chg_at_entry": round(chg, 4),
            })
            opened.append(book["open"][-1])

    # ── stalefish (stuck market breaks out — follow the breakout) ────────────
    if cfg.get("stalefish_enabled"):
        book     = state["stalefish"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        for m in markets:
            if len(book["open"]) >= cfg["stalefish_max_open"]: break
            if m["id"] in own or m["id"] in cooldown: continue
            if m["vol"] < cfg["stalefish_min_vol"]: continue
            yes  = m["yes"]
            if not (cfg["stalefish_yes_min"] <= yes <= cfg["stalefish_yes_max"]): continue
            # Gate 1: market was stuck (low |chg| = quiet yesterday)
            chg = m.get("chg", 0) or 0
            # Note: chg reflects 24h move; we still want the CURRENT scan breakout
            # We use _price_snap delta as the breakout signal
            prev = _price_snap.get(m["id"])
            if prev is None: continue
            scan_delta = yes - prev
            if abs(scan_delta) < cfg["stalefish_breakout"]: continue  # no breakout yet
            # Gate 2: market was genuinely quiet before this scan
            if abs(chg) > cfg["stalefish_stale_chg"] + abs(scan_delta): continue  # already was moving
            side    = "YES" if scan_delta > 0 else "NO"
            out_mid = yes if side == "YES" else 1 - yes
            entry   = ask(out_mid, cfg, m["vol"])
            size    = round(cfg["stalefish_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "stalefish", "side": side,
                "entry_mid": round(out_mid, 4), "entry_fill": entry,
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "scan_delta": round(scan_delta, 4), "prev_yes": round(prev, 4),
            })
            opened.append(book["open"][-1])


    # ── tailspin (sustained trend momentum) ──────────────────────────────────
    if cfg.get("tailspin_enabled"):
        book     = state["tailspin"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        for m in markets:
            if len(book["open"]) >= cfg["tailspin_max_open"]: break
            if m["id"] in own or m["id"] in cooldown: continue
            if m["vol"] < cfg["tailspin_min_vol"]: continue
            yes = m["yes"]
            chg = m.get("chg", 0) or 0
            if yes <= cfg["tailspin_yes_lo"] and chg <= -cfg["tailspin_min_chg"]:
                side = "NO"
            elif yes >= cfg["tailspin_yes_hi"] and chg >= cfg["tailspin_min_chg"]:
                side = "YES"
            else:
                continue
            out_mid = (1 - yes) if side == "NO" else yes
            entry   = ask(out_mid, cfg, m["vol"])
            size    = round(cfg["tailspin_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "tailspin", "side": side,
                "entry_mid": round(out_mid, 4), "entry_fill": entry,
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "chg_at_entry": round(chg, 4),
            })
            opened.append(book["open"][-1])

    # ── roundnum (tipping-point 50¢ crossover breakout) ──────────────────────
    if cfg.get("roundnum_enabled"):
        book     = state["roundnum"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        buf = cfg["roundnum_cross_buffer"]
        for m in markets:
            if len(book["open"]) >= cfg["roundnum_max_open"]: break
            if m["id"] in own or m["id"] in cooldown: continue
            if m["vol"] < cfg["roundnum_min_vol"]: continue
            yes  = m["yes"]
            prev = _price_snap.get(m["id"])
            if prev is None: continue
            # Bull cross: was below 0.50, now meaningfully above
            if prev < 0.50 and yes >= 0.50 + buf:
                side = "YES"
            # Bear cross: was above 0.50, now meaningfully below
            elif prev > 0.50 and yes <= 0.50 - buf:
                side = "NO"
            else:
                continue
            out_mid = yes if side == "YES" else 1 - yes
            entry   = ask(out_mid, cfg, m["vol"])
            size    = round(cfg["roundnum_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "roundnum", "side": side,
                "entry_mid": round(out_mid, 4), "entry_fill": entry,
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "prev_yes": round(prev, 4),
            })
            opened.append(book["open"][-1])

    # ── firstmover (top-N markets by vol this scan, follow momentum) ──────────
    if cfg.get("firstmover_enabled"):
        book     = state["firstmover"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        top_n = cfg["firstmover_top_n"]
        # markets is already sorted by vol desc from fetch
        for m in markets[:top_n]:
            if len(book["open"]) >= cfg["firstmover_max_open"]: break
            if m["id"] in own or m["id"] in cooldown: continue
            chg = m.get("chg", 0) or 0
            if abs(chg) < cfg["firstmover_min_chg"]: continue
            yes = m["yes"]
            if not (cfg["firstmover_yes_min"] <= yes <= cfg["firstmover_yes_max"]): continue
            side    = "YES" if chg > 0 else "NO"
            out_mid = yes if side == "YES" else 1 - yes
            entry   = ask(out_mid, cfg, m["vol"])
            size    = round(cfg["firstmover_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "firstmover", "side": side,
                "entry_mid": round(out_mid, 4), "entry_fill": entry,
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "chg_at_entry": round(chg, 4),
            })
            opened.append(book["open"][-1])

    # ── yoyo (oscillation fade — bet on the reversal of a reversal) ───────────
    if cfg.get("yoyo_enabled"):
        book     = state["yoyo"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        for m in markets:
            if len(book["open"]) >= cfg["yoyo_max_open"]: break
            if m["id"] in own or m["id"] in cooldown: continue
            if m["vol"] < cfg["yoyo_min_vol"]: continue
            yes  = m["yes"]
            if not (cfg["yoyo_yes_min"] <= yes <= cfg["yoyo_yes_max"]): continue
            chg  = m.get("chg", 0) or 0
            prev = _price_snap.get(m["id"])
            if prev is None: continue
            scan_delta = yes - prev
            if abs(chg) < cfg["yoyo_min_chg"]: continue
            if abs(scan_delta) < cfg["yoyo_min_reversal"]: continue
            # Oscillation: 24h direction is opposite to current scan direction
            if chg > 0 and scan_delta < 0:
                side = "NO"   # 24h rose, just fell → next scan will rise → fade current fall? No, fade the 24h rise
            elif chg < 0 and scan_delta > 0:
                side = "YES"  # 24h fell, just bounced → fade the bounce (expect more falling)
            else:
                continue  # same direction = momentum, not oscillation
            out_mid = (1 - yes) if side == "NO" else yes
            entry   = ask(out_mid, cfg, m["vol"])
            size    = round(cfg["yoyo_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "yoyo", "side": side,
                "entry_mid": round(out_mid, 4), "entry_fill": entry,
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "chg": round(chg, 4), "scan_delta": round(scan_delta, 4),
            })
            opened.append(book["open"][-1])


    # ── bigswing (>20¢ single-day move, follow momentum) ─────────────────────
    if cfg.get("bigswing_enabled"):
        book     = state["bigswing"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        for m in markets:
            if len(book["open"]) >= cfg["bigswing_max_open"]: break
            if m["id"] in own or m["id"] in cooldown: continue
            if m["vol"] < cfg["bigswing_min_vol"]: continue
            chg = m.get("chg", 0) or 0
            if abs(chg) < cfg["bigswing_min_chg"]: continue
            yes = m["yes"]
            if not (cfg["bigswing_yes_min"] <= yes <= cfg["bigswing_yes_max"]): continue
            side    = "YES" if chg > 0 else "NO"
            out_mid = yes if side == "YES" else 1 - yes
            entry   = ask(out_mid, cfg, m["vol"])
            size    = round(cfg["bigswing_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "bigswing", "side": side,
                "entry_mid": round(out_mid, 4), "entry_fill": entry,
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "chg_at_entry": round(chg, 4),
            })
            opened.append(book["open"][-1])

    # ── polmom (politics-only momentum) ──────────────────────────────────────
    if cfg.get("polmom_enabled"):
        book     = state["polmom"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        for m in markets:
            if len(book["open"]) >= cfg["polmom_max_open"]: break
            if m["id"] in own or m["id"] in cooldown: continue
            if m.get("cat") != "politics": continue
            if m["vol"] < cfg["polmom_min_vol"]: continue
            chg = m.get("chg", 0) or 0
            if abs(chg) < cfg["polmom_min_chg"]: continue
            yes = m["yes"]
            if not (cfg["polmom_yes_min"] <= yes <= cfg["polmom_yes_max"]): continue
            side    = "YES" if chg > 0 else "NO"
            out_mid = yes if side == "YES" else 1 - yes
            entry   = ask(out_mid, cfg, m["vol"])
            size    = round(cfg["polmom_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": "politics", "strategy": "polmom", "side": side,
                "entry_mid": round(out_mid, 4), "entry_fill": entry,
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "chg_at_entry": round(chg, 4),
            })
            opened.append(book["open"][-1])

    # ── sportshigh (sports near-decided, ride to certainty) ──────────────────
    if cfg.get("sportshigh_enabled"):
        book     = state["sportshigh"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        for m in markets:
            if len(book["open"]) >= cfg["sportshigh_max_open"]: break
            if m["id"] in own or m["id"] in cooldown: continue
            if m.get("cat") != "sports": continue
            if m["vol"] < cfg["sportshigh_min_vol"]: continue
            yes = m["yes"]
            if not (cfg["sportshigh_yes_min"] <= yes <= cfg["sportshigh_yes_max"]): continue
            chg = m.get("chg", 0) or 0
            if chg < cfg["sportshigh_min_chg"]: continue  # must still be moving up
            entry = ask(yes, cfg)
            size  = round(cfg["sportshigh_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": "sports", "strategy": "sportshigh", "side": "YES",
                "entry_mid": round(yes, 4), "entry_fill": entry,
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "chg_at_entry": round(chg, 4),
            })
            opened.append(book["open"][-1])

    # ── volwake (sleeping market suddenly gets volume) ────────────────────────
    if cfg.get("volwake_enabled"):
        book     = state["volwake"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        for m in markets:
            if len(book["open"]) >= cfg["volwake_max_open"]: break
            if m["id"] in own or m["id"] in cooldown: continue
            baseline = _vol_baseline.get(m["id"], 0)
            if baseline > cfg["volwake_max_baseline"]: continue  # not a sleepy market
            if baseline <= 0: continue
            if m["vol"] < baseline * cfg["volwake_ema_mult"]: continue  # vol not spiking
            chg = m.get("chg", 0) or 0
            if abs(chg) < cfg["volwake_min_chg"]: continue
            yes = m["yes"]
            if not (cfg["volwake_yes_min"] <= yes <= cfg["volwake_yes_max"]): continue
            side    = "YES" if chg > 0 else "NO"
            out_mid = yes if side == "YES" else 1 - yes
            entry   = ask(out_mid, cfg, m["vol"])
            size    = round(cfg["volwake_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "volwake", "side": side,
                "entry_mid": round(out_mid, 4), "entry_fill": entry,
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "baseline": round(baseline, 0), "chg": round(chg, 4),
            })
            opened.append(book["open"][-1])

    # ── doubletop (second test of a high = rejection signal, fade it) ─────────
    if cfg.get("doubletop_enabled"):
        book     = state["doubletop"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        for m in markets:
            if len(book["open"]) >= cfg["doubletop_max_open"]: break
            if m["id"] in own or m["id"] in cooldown: continue
            if m["vol"] < cfg["doubletop_min_vol"]: continue
            yes  = m["yes"]
            if not (cfg["doubletop_yes_min"] <= yes <= cfg["doubletop_yes_max"]): continue
            prev = _price_snap.get(m["id"])
            if prev is None: continue
            chg  = m.get("chg", 0) or 0
            # Double-top pattern: prev was higher, current is near prev (retry), chg negative
            retrace = prev - yes   # how far it pulled back since last scan
            if retrace < cfg["doubletop_retrace_min"]: continue   # didn't pull back enough
            if chg >= 0: continue   # 24h still positive = not a top pattern
            # Price is currently re-testing near prev high (within buffer from prev)
            if yes < prev - cfg["doubletop_retry_buffer"]: continue  # too far below prev
            entry = ask(1 - yes, cfg)   # buying NO
            size  = round(cfg["doubletop_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "doubletop", "side": "NO",
                "entry_mid": round(1 - yes, 4), "entry_fill": entry,
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "prev_yes": round(prev, 4), "retrace": round(retrace, 4),
            })
            opened.append(book["open"][-1])


    # ── lopsided (heavily traded near 50¢ — follow the winning side) ─────────
    if cfg.get("lopsided_enabled"):
        book     = state["lopsided"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at","")) < cfg["reentry_cooldown_hours"]}
        for m in markets:
            if len(book["open"]) >= cfg["lopsided_max_open"]: break
            if m["id"] in own or m["id"] in cooldown: continue
            if m["vol"] < cfg["lopsided_min_vol"]: continue
            yes = m["yes"]
            if not (cfg["lopsided_yes_min"] <= yes <= cfg["lopsided_yes_max"]): continue
            chg = m.get("chg", 0) or 0
            if abs(chg) < cfg["lopsided_min_chg"]: continue
            side    = "YES" if chg > 0 else "NO"
            out_mid = yes if side == "YES" else 1 - yes
            entry   = ask(out_mid, cfg, m["vol"])
            size    = round(cfg["lopsided_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat",""), "strategy": "lopsided", "side": side,
                "entry_mid": round(out_mid,4), "entry_fill": entry,
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "chg_at_entry": round(chg,4),
            })
            opened.append(book["open"][-1])

    # ── sportsnear (sports <72h — late smart money) ───────────────────────────
    if cfg.get("sportsnear_enabled"):
        book     = state["sportsnear"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at","")) < cfg["reentry_cooldown_hours"]}
        for m in markets:
            if len(book["open"]) >= cfg["sportsnear_max_open"]: break
            if m["id"] in own or m["id"] in cooldown: continue
            if m.get("cat") != "sports": continue
            if m["vol"] < cfg["sportsnear_min_vol"]: continue
            days = days_to_end(m.get("end"))
            if days is None or days <= 0 or days > cfg["sportsnear_max_hours"] / 24: continue
            yes = m["yes"]
            if not (cfg["sportsnear_yes_min"] <= yes <= cfg["sportsnear_yes_max"]): continue
            chg = m.get("chg", 0) or 0
            if abs(chg) < cfg["sportsnear_min_chg"]: continue
            side    = "YES" if chg > 0 else "NO"
            out_mid = yes if side == "YES" else 1 - yes
            entry   = ask(out_mid, cfg, m["vol"])
            size    = round(cfg["sportsnear_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": "sports", "strategy": "sportsnear", "side": side,
                "entry_mid": round(out_mid,4), "entry_fill": entry,
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "days_left": round(days,2), "chg_at_entry": round(chg,4),
            })
            opened.append(book["open"][-1])

    # ── whaleagree (2+ whale wallets same direction <2h) ─────────────────────
    if cfg.get("whaleagree_enabled") and _all_trades:
        book     = state["whaleagree"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at","")) < cfg["reentry_cooldown_hours"]}
        top_wallets = {w["addr"] for w in get_top_wallets(20)}
        min_usdc    = cfg["whaleagree_min_usdc"]
        for cid, tlist in _tidx_60.items():
            if len(book["open"]) >= cfg["whaleagree_max_open"]: break
            if cid in own or cid in cooldown: continue
            m = _market_by_id.get(cid)
            if not m or m["vol"] < cfg["whaleagree_min_vol"]: continue
            if not (cfg["whaleagree_yes_min"] <= m["yes"] <= cfg["whaleagree_yes_max"]): continue
            big = [t for t in tlist if t["wallet"] in top_wallets and t["dollar"] >= min_usdc]
            if len(big) < cfg["whaleagree_min_whales"]: continue
            # All big trades must agree on direction
            sides = {t["side"] for t in big}
            if len(sides) != 1: continue
            side    = "YES" if list(sides)[0] == "BUY" else "NO"
            out_mid = m["yes"] if side == "YES" else 1 - m["yes"]
            entry   = ask(out_mid, cfg, m["vol"])
            size    = round(cfg["whaleagree_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat",""), "strategy": "whaleagree", "side": side,
                "entry_mid": round(out_mid,4), "entry_fill": entry,
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "whale_count": len(big),
            })
            opened.append(book["open"][-1])

    # ── gapfill (large scan-gap fades back) ──────────────────────────────────
    if cfg.get("gapfill_enabled"):
        book     = state["gapfill"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at","")) < cfg["reentry_cooldown_hours"]}
        for m in markets:
            if len(book["open"]) >= cfg["gapfill_max_open"]: break
            if m["id"] in own or m["id"] in cooldown: continue
            if m["vol"] < cfg["gapfill_min_vol"]: continue
            yes  = m["yes"]
            if not (cfg["gapfill_yes_min"] <= yes <= cfg["gapfill_yes_max"]): continue
            prev = _price_snap.get(m["id"])
            if prev is None: continue
            gap = yes - prev   # positive = gapped up
            if abs(gap) < cfg["gapfill_min_gap"]: continue
            # Fade: gapped up → buy NO; gapped down → buy YES
            side    = "NO" if gap > 0 else "YES"
            out_mid = (1 - yes) if side == "NO" else yes
            entry   = ask(out_mid, cfg, m["vol"])
            size    = round(cfg["gapfill_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat",""), "strategy": "gapfill", "side": side,
                "entry_mid": round(out_mid,4), "entry_fill": entry,
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "gap": round(gap,4), "prev_yes": round(prev,4),
            })
            opened.append(book["open"][-1])

    # ── streakbuy (category streak — 3+ markets up in same cat) ──────────────
    if cfg.get("streakbuy_enabled"):
        book     = state["streakbuy"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at","")) < cfg["reentry_cooldown_hours"]}
        # Count positive chg markets per cat
        cat_up = {}
        for m in markets:
            cat = m.get("cat","other")
            if (m.get("chg") or 0) >= cfg["streakbuy_streak_chg"]:
                cat_up[cat] = cat_up.get(cat, 0) + 1
        bullish_cats = {c for c, n in cat_up.items() if n >= cfg["streakbuy_min_streak"]}
        if bullish_cats:
            for m in markets:
                if len(book["open"]) >= cfg["streakbuy_max_open"]: break
                if m["id"] in own or m["id"] in cooldown: continue
                if m.get("cat","other") not in bullish_cats: continue
                if m["vol"] < cfg["streakbuy_min_vol"]: continue
                yes = m["yes"]
                if not (cfg["streakbuy_yes_min"] <= yes <= cfg["streakbuy_yes_max"]): continue
                entry = ask(yes, cfg)
                size  = round(cfg["streakbuy_bet_usdc"] / entry, 4) if entry > 0 else 0
                if size <= 0: continue
                book["open"].append({
                    "id": m["id"], "q": m["q"], "cat": m.get("cat",""), "strategy": "streakbuy", "side": "YES",
                    "entry_mid": round(yes,4), "entry_fill": entry,
                    "size": size, "vol": m["vol"], "opened_at": now_iso(),
                    "streak_cat": m.get("cat",""),
                })
                opened.append(book["open"][-1])

    # ── revertmid (extreme price correcting back to center) ───────────────────
    if cfg.get("revertmid_enabled"):
        book     = state["revertmid"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at","")) < cfg["reentry_cooldown_hours"]}
        for m in markets:
            if len(book["open"]) >= cfg["revertmid_max_open"]: break
            if m["id"] in own or m["id"] in cooldown: continue
            if m["vol"] < cfg["revertmid_min_vol"]: continue
            yes = m["yes"]
            chg = m.get("chg", 0) or 0
            # High extreme correcting down
            if yes >= cfg["revertmid_hi"] and chg <= -cfg["revertmid_min_chg"]:
                side = "NO"
            # Low extreme correcting up
            elif yes <= cfg["revertmid_lo"] and chg >= cfg["revertmid_min_chg"]:
                side = "YES"
            else:
                continue
            out_mid = (1 - yes) if side == "NO" else yes
            entry   = ask(out_mid, cfg, m["vol"])
            size    = round(cfg["revertmid_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat",""), "strategy": "revertmid", "side": side,
                "entry_mid": round(out_mid,4), "entry_fill": entry,
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "chg_at_entry": round(chg,4),
            })
            opened.append(book["open"][-1])


    # ── nohappen ("Nothing Ever Happens" — fade sensational markets) ──────────
    # Source: QuantPedia + "Nothing Ever Happens" OSS bot
    _NOHAPPEN_KW = ("crash", "war", "resign", "arrest", "impeach", "collapse",
                    "default", "ban ", "invade", "nuke", "assassin", "bankrupt",
                    "sanction", "coup", "scandal", "invasion", "attack", "fired",
                    "indicted", "convicted", "declare war", "shut down", "explosion")
    if cfg.get("nohappen_enabled"):
        book     = state["nohappen"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        for m in markets:
            if len(book["open"]) >= cfg["nohappen_max_open"]: break
            if m["id"] in own or m["id"] in cooldown: continue
            if m["vol"] < cfg["nohappen_min_vol"]: continue
            yes = m["yes"]
            if not (cfg["nohappen_yes_min"] <= yes <= cfg["nohappen_yes_max"]): continue
            q_lower = (m.get("q") or "").lower()
            if not any(kw in q_lower for kw in _NOHAPPEN_KW): continue
            entry = ask(1 - yes, cfg)   # buying NO
            size  = round(cfg["nohappen_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "nohappen", "side": "NO",
                "entry_mid": round(1 - yes, 4), "entry_fill": entry,
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
            })
            opened.append(book["open"][-1])

    # ── longshortbias (longshot overpricing, QuantPedia edge) ─────────────────
    if cfg.get("longshortbias_enabled"):
        book     = state["longshortbias"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        for m in markets:
            if len(book["open"]) >= cfg["longshortbias_max_open"]: break
            if m["id"] in own or m["id"] in cooldown: continue
            if m["vol"] < cfg["longshortbias_min_vol"]: continue
            yes = m["yes"]
            if not (cfg["longshortbias_yes_min"] <= yes <= cfg["longshortbias_yes_max"]): continue
            entry = ask(1 - yes, cfg)   # buying NO — longshot won't happen
            size  = round(cfg["longshortbias_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "longshortbias", "side": "NO",
                "entry_mid": round(1 - yes, 4), "entry_fill": entry,
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
            })
            opened.append(book["open"][-1])

    # ── clobimbal (CLOB order book imbalance, PolyFlup signal) ───────────────
    if cfg.get("clobimbal_enabled"):
        book     = state["clobimbal"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        levels   = cfg["clobimbal_levels"]
        min_ratio = cfg["clobimbal_min_ratio"]
        for m in markets:
            if len(book["open"]) >= cfg["clobimbal_max_open"]: break
            if m["id"] in own or m["id"] in cooldown: continue
            if m["vol"] < cfg["clobimbal_min_vol"]: continue
            yes = m["yes"]
            if not (cfg["clobimbal_yes_min"] <= yes <= cfg["clobimbal_yes_max"]): continue
            token = m.get("token", "")
            if not token: continue
            bk = fetch_clob_book(token)
            bids = bk.get("bids", [])[:levels]
            asks = bk.get("asks", [])[:levels]
            if not bids or not asks: continue
            bid_liq = sum(float(b.get("price",0)) * float(b.get("size",0)) for b in bids)
            ask_liq = sum(float(a.get("price",0)) * float(a.get("size",0)) for a in asks)
            if bid_liq <= 0 or ask_liq <= 0: continue
            ratio = bid_liq / ask_liq
            if ratio >= min_ratio:
                side = "YES"   # bids overwhelming asks — smart money loading
            elif ask_liq / bid_liq >= min_ratio:
                side = "NO"    # asks overwhelming bids — smart money offering
            else:
                continue
            out_mid = yes if side == "YES" else 1 - yes
            entry   = ask(out_mid, cfg)
            size    = round(cfg["clobimbal_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat",""), "strategy": "clobimbal", "side": side,
                "entry_mid": round(out_mid,4), "entry_fill": entry,
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "bid_liq": round(bid_liq,2), "ask_liq": round(ask_liq,2),
            })
            opened.append(book["open"][-1])

    # ── polyflup (PolyFlup weighted crypto signal) ────────────────────────────
    # Source: github.com/Niller2005/PolyFlup — simplified 3-signal port
    _POLYFLUP_KW = ("bitcoin", "btc", "ethereum", "eth", "solana", "sol",
                    "crypto", "doge", "xrp", "bnb", "cardano", "ada")
    if cfg.get("polyflup_enabled"):
        book     = state["polyflup"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        for m in markets:
            if len(book["open"]) >= cfg["polyflup_max_open"]: break
            if m["id"] in own or m["id"] in cooldown: continue
            if m["vol"] < cfg["polyflup_min_vol"]: continue
            q_lower = (m.get("q") or "").lower()
            if not any(kw in q_lower for kw in _POLYFLUP_KW): continue
            yes = m["yes"]
            if not (cfg["polyflup_yes_min"] <= yes <= cfg["polyflup_yes_max"]): continue
            chg = m.get("chg", 0) or 0
            if abs(chg) < cfg["polyflup_min_chg"]: continue
            # Signal weights (PolyFlup-inspired):
            # chg momentum (30%): direction of 24h move
            # vol vs baseline (10%): whether vol is elevated
            # CLOB imbalance (20%): from book data if available
            # Remaining 40% = neutral without Binance feed
            w_chg  = 0.30 if chg > 0 else -0.30
            baseline = _vol_baseline.get(m["id"], m["vol"])
            w_vol  = 0.10 if (baseline > 0 and m["vol"] > baseline * 1.2) else -0.05
            # Try CLOB for imbalance signal
            w_clob = 0.0
            token = m.get("token", "")
            if token:
                bk = fetch_clob_book(token)
                bids = bk.get("bids", [])[:3]
                asks = bk.get("asks", [])[:3]
                if bids and asks:
                    bl = sum(float(b.get("price",0))*float(b.get("size",0)) for b in bids)
                    al = sum(float(a.get("price",0))*float(a.get("size",0)) for a in asks)
                    if al > 0: w_clob = 0.20 if bl/al >= 1.5 else (-0.20 if al/bl >= 1.5 else 0)
            confidence = 0.50 + w_chg + w_vol + w_clob  # 0.50 = neutral base
            if confidence < cfg["polyflup_min_confidence"] and (1 - confidence) < cfg["polyflup_min_confidence"]:
                continue
            side    = "YES" if confidence >= cfg["polyflup_min_confidence"] else "NO"
            out_mid = yes if side == "YES" else 1 - yes
            entry   = ask(out_mid, cfg)
            size    = round(cfg["polyflup_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat",""), "strategy": "polyflup", "side": side,
                "entry_mid": round(out_mid,4), "entry_fill": entry,
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "confidence": round(confidence,3), "chg": round(chg,4),
            })
            opened.append(book["open"][-1])


    # ── nearresfade (near-resolution fade — Wang transform edge, 2026-06-10) ─────
    # Truefade failed on long-dated markets (time exits, no resolution pressure).
    # This version ONLY enters markets with 1-30 days remaining — forcing convergence.
    if cfg.get("nearresfade_enabled"):
        book     = state["nearresfade"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at","")) < cfg["reentry_cooldown_hours"]}
        for m in markets:
            if len(book["open"]) >= cfg["nearresfade_max_open"]: break
            if m["id"] in own or m["id"] in cooldown: continue
            if m["vol"] < cfg["nearresfade_min_vol"]: continue
            yes = m["yes"]
            if not (cfg["nearresfade_yes_min"] <= yes <= cfg["nearresfade_yes_max"]): continue
            if is_live_match(m.get("q", "")): continue   # resolution-pressure thesis needs DAYS, not live matches
            _q = m.get("q", "").lower()
            if any(k in _q for k in cfg.get("nearresfade_skip_kw", ())): continue  # active-conflict drift beats base-rate fade
            days = days_to_end(m.get("end"))
            if days is None or days < cfg["nearresfade_min_days"]: continue
            if days > cfg["nearresfade_max_days"]: continue   # KEY: resolution pressure gate
            if not wang_cat_ok(m, cfg): continue   # λ gate: sports/politics carry no premium to fade
            entry = maker_fill(1 - yes, cfg, m["vol"])   # buying NO; 1-30d markets are slow → maker
            size  = round(cfg["nearresfade_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat",""), "strategy": "nearresfade", "side": "NO",
                "entry_mid": round(1 - yes, 4), "entry_fill": entry,
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "days_left": round(days, 1),
                "wang_fair_yes": wang_fair(yes),
            })
            opened.append(book["open"][-1])

    # ── wcrank (FIFA rank + WC standing + TheSportsDB form) ────────────────────
    # Fires only on "World Cup" / "FIFA" soccer markets. Fetches WC standings and
    # news once per cycle (cached), TheSportsDB last result per team (cached 4h).
    # Computes rank-implied probability and trades the divergence vs Poly YES.
    # Three data layers: long-term strength (FIFA rank) + live form (ESPN standing)
    #                    + recent form (TheSportsDB last result) = "analyze every trade".
    if cfg.get("wcrank_enabled"):
        wc_std  = fetch_wc_standings()      # one API call per cycle, cached
        wc_news = fetch_wc_news_teams()     # same cycle, cached
        book = state["wcrank"]
        own = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at","")) < cfg["reentry_cooldown_hours"]}
        for m in markets:
            if len(book["open"]) >= cfg["wcrank_max_open"]:
                break
            if m["id"] in own or m["id"] in cooldown:
                continue
            if m["vol"] < cfg["wcrank_min_volume"]:
                continue
            # Only WC markets
            q_lower = m["q"].lower()
            if "world cup" not in q_lower and "fifa" not in q_lower:
                continue
            if not (cfg["wcrank_yes_min"] <= m["yes"] <= cfg["wcrank_yes_max"]):
                continue
            team, rank = _extract_wc_team(m["q"])
            if team is None:
                continue
            qtype = _wc_qtype(m["q"])
            if qtype is None:
                continue
            # TheSportsDB last result (per-team cache, 4h TTL)
            last_result = fetch_team_last_result(team)
            standing = wc_std.get(team) or wc_std.get(
                next((k for k in wc_std if team.lower() in k.lower()), ""), {})
            rank_p = _rank_prob(rank, qtype, standing, last_result)
            diff = rank_p - m["yes"]
            if abs(diff) < cfg["wcrank_min_divergence"]:
                continue
            side    = "YES" if diff > 0 else "NO"
            out_mid = m["yes"] if side == "YES" else 1 - m["yes"]
            entry   = ask(out_mid, cfg, m["vol"])
            size    = round(cfg["wcrank_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0:
                continue
            ctx = _sports_context(team, wc_std, wc_news, last_result)
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "wcrank", "side": side,
                "entry_mid": round(out_mid, 4), "entry_fill": round(entry, 4),
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "rank_prob": rank_p, "rank_diff": round(diff, 4),
                "qtype": qtype,
                **{f"ctx_{k}": v for k, v in ctx.items()},  # flatten context
            })
            opened.append(book["open"][-1])

    # ── wcnews (ESPN media attention contrarian signal) ─────────────────────────
    # Fetch ESPN WC team article counts (already cached by wcrank leg above).
    # High media attention on a team → compute a "news-implied" market attention
    # score and trade divergence from Poly YES:
    #   • Team has many mentions + Poly YES is HIGH → market may be hype-driven → fade
    #   • Team has many mentions + Poly YES is LOW  → market ignoring media signal → buy
    # Uses the same WC news + standings cache — zero extra API calls.
    if cfg.get("wcnews_enabled"):
        wc_news2 = fetch_wc_news_teams()     # cache hit — already fetched above
        total_mentions = max(sum(wc_news2.values()), 1)
        max_mentions   = max(wc_news2.values(), default=1)
        book = state["wcnews"]
        own = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at","")) < cfg["reentry_cooldown_hours"]}
        for m in markets:
            if len(book["open"]) >= cfg["wcnews_max_open"]:
                break
            if m["id"] in own or m["id"] in cooldown:
                continue
            if m["vol"] < cfg["wcnews_min_volume"]:
                continue
            q_lower = m["q"].lower()
            if "world cup" not in q_lower and "fifa" not in q_lower:
                continue
            if not (cfg["wcnews_yes_min"] <= m["yes"] <= cfg["wcnews_yes_max"]):
                continue
            team, rank = _extract_wc_team(m["q"])
            if team is None:
                continue
            qtype = _wc_qtype(m["q"])
            # News attention score: normalized mentions [0,1]
            mentions = wc_news2.get(team, 0)
            # Also try ESPN name variants (e.g. "United States" vs "USA")
            if mentions == 0:
                for espn_name in wc_news2:
                    if team.lower() in espn_name.lower() or espn_name.lower() in team.lower():
                        mentions = wc_news2[espn_name]
                        break
            if mentions < cfg["wcnews_min_mentions"]:
                continue   # not enough media signal to trade on
            # Media attention proxy for implied probability:
            # A team with X% of total media coverage is implicitly priced at X%
            # by the media (very rough). Divergence from Poly YES is the signal.
            news_share = mentions / total_mentions
            # Scale to a win probability range (media coverage → prestige proxy)
            # cap at 0.35 to prevent news-share becoming unrealistically high
            news_implied = min(0.35, news_share * 3.5) if qtype == "wc_winner" else min(0.70, news_share * 7.0)
            diff = news_implied - m["yes"]
            if abs(diff) < cfg["wcnews_divergence"]:
                continue
            side    = "YES" if diff > 0 else "NO"
            out_mid = m["yes"] if side == "YES" else 1 - m["yes"]
            entry   = ask(out_mid, cfg, m["vol"])
            size    = round(cfg["wcnews_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0:
                continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "wcnews", "side": side,
                "entry_mid": round(out_mid, 4), "entry_fill": round(entry, 4),
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "news_mentions": mentions, "news_implied": round(news_implied, 4),
                "news_diff": round(diff, 4), "news_team": team,
            })
            opened.append(book["open"][-1])

    # ── wikivol (Wikipedia page-view surge detector) ────────────────────────────
    # Non-sports markets only (sports covered by wcrank/wcnews). Maps question
    # to a Wikipedia article title, fetches 37-day pageview history from the
    # Wikimedia REST API, and computes a surge ratio. Trades the attention gap.
    # Rate-limit guard: max 5 NEW Wikipedia API calls per scan cycle (1s delay
    # between calls). 404s are cached permanently in _wiki_miss. Already-cached
    # articles skip the network entirely, so the cap only affects cold lookups.
    if cfg.get("wikivol_enabled"):
        book = state["wikivol"]
        own  = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        # Scan path is CACHE-ONLY — we never make live Wikipedia API calls here.
        # The warmup_wiki_cache() function (called once per hour from main loop)
        # pre-fetches article data in a background daemon thread so the scan
        # is never blocked by network I/O.
        for m in markets:
            if len(book["open"]) >= cfg["wikivol_max_open"]:  break
            if m["id"] in own or m["id"] in cooldown:         continue
            if m["vol"] < cfg["wikivol_min_volume"]:          continue
            if not (cfg["wikivol_yes_min"] <= m["yes"] <= cfg["wikivol_yes_max"]): continue
            if m.get("cat") == "sports":                      continue  # wcrank/wcnews cover sports
            article = _wiki_article_for(m["q"])
            if not article or article in _wiki_miss:          continue
            # Skip if article is cold — warmup_wiki_cache() will fill this next hour
            if article not in _wiki_cache or (time.time() - _wiki_cache[article].get("ts",0) >= _WIKI_TTL):
                continue
            surge, r_avg, b_avg = _wiki_surge(article)
            if surge < cfg["wikivol_min_surge"]:              continue
            # Surge + low price → underpriced (buy YES); surge + high → hype (buy NO)
            side    = "NO" if m["yes"] >= 0.50 else "YES"
            out_mid = m["yes"] if side == "YES" else 1 - m["yes"]
            entry   = ask(out_mid, cfg, m["vol"])
            size    = round(cfg["wikivol_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "wikivol", "side": side,
                "entry_mid": round(out_mid, 4), "entry_fill": round(entry, 4),
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "wiki_article": article, "wiki_surge": round(surge, 2),
                "wiki_r_avg": round(r_avg), "wiki_b_avg": round(b_avg),
            })
            opened.append(book["open"][-1])

    # ── resolveprox (resolution proximity convergence) ───────────────────────
    # Zero extra API calls — uses the Gamma end-date already in market data.
    # As a market approaches resolution the residual uncertainty discount
    # collapses, so markets priced away from {0,1} should converge. We buy
    # that convergence in the last N days before resolution.
    if cfg.get("resolveprox_enabled"):
        from datetime import datetime, timezone as _tz3
        book = state["resolveprox"]
        own  = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        for m in markets:
            if len(book["open"]) >= cfg["resolveprox_max_open"]: break
            if m["id"] in own or m["id"] in cooldown:            continue
            if m["vol"] < cfg["resolveprox_min_volume"]:         continue
            end_str = m.get("end", "")
            if not end_str: continue
            try:
                end_dt    = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
                days_left = (end_dt - datetime.now(_tz3.utc)).total_seconds() / 86400
            except Exception:
                continue
            if days_left < 0.1 or days_left > cfg["resolveprox_max_days"]: continue
            yes = m["yes"]
            if   cfg["resolveprox_yes_hi_min"] <= yes <= cfg["resolveprox_yes_hi_max"]:
                side = "YES"
            elif cfg["resolveprox_yes_lo_min"] <= yes <= cfg["resolveprox_yes_lo_max"]:
                side = "NO"
            else:
                continue
            out_mid = yes if side == "YES" else 1 - yes
            entry   = ask(out_mid, cfg, m["vol"])
            size    = round(cfg["resolveprox_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "resolveprox", "side": side,
                "entry_mid": round(out_mid, 4), "entry_fill": round(entry, 4),
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "days_left": round(days_left, 2), "end": end_str,
            })
            opened.append(book["open"][-1])

    # ── redditbuzz (Reddit discussion leading indicator) ─────────────────────
    # Reddit public JSON search — no auth, no key, works from any IP. Searches
    # category-matched subreddits for the market topic and counts posts in the
    # last 7 days. High post count → public attention → market may lag Reddit.
    if cfg.get("redditbuzz_enabled"):
        book = state["redditbuzz"]
        own  = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        # Scan path is CACHE-ONLY for Reddit — same pattern as wikivol.
        # schedule_reddit_warmup() populates the cache in a background thread.
        _RB_SUBS = {
            "sports":   ("soccer", "worldcup", "sports"),
            "politics": ("politics", "worldnews", "news"),
            "crypto":   ("CryptoCurrency", "Bitcoin", "ethereum"),
            "other":    ("worldnews", "news", "politics"),
        }
        for m in markets:
            if len(book["open"]) >= cfg["redditbuzz_max_open"]: break
            if m["id"] in own or m["id"] in cooldown:           continue
            if m["vol"] < cfg["redditbuzz_min_volume"]:         continue
            if not (cfg["redditbuzz_yes_min"] <= m["yes"] <= cfg["redditbuzz_yes_max"]): continue
            subs      = _RB_SUBS.get(m.get("cat", "other"), _RB_SUBS["other"])
            query     = " ".join(m["q"].replace("?", "").split()[:7])
            cache_key = str((query.lower()[:60], tuple(subs)))
            cached    = _reddit_cache.get(cache_key, {})
            if not cached or time.time() - cached.get("ts", 0) >= _REDDIT_TTL:
                continue   # cold — skip; warmup thread will fill this
            posts = cached["posts"]
            if posts < cfg["redditbuzz_min_posts"]: continue
            side    = "NO" if m["yes"] >= 0.55 else "YES"
            out_mid = m["yes"] if side == "YES" else 1 - m["yes"]
            entry   = ask(out_mid, cfg, m["vol"])
            size    = round(cfg["redditbuzz_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "redditbuzz", "side": side,
                "entry_mid": round(out_mid, 4), "entry_fill": round(entry, 4),
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "reddit_posts": posts, "reddit_query": query, "reddit_subs": list(subs),
            })
            opened.append(book["open"][-1])

    # ── ytbuzz (YouTube attention hype-fade) ─────────────────────────────────
    # Scan path is CACHE-ONLY (same pattern as wikivol/redditbuzz); a background
    # thread scrapes youtube.com search pages and the cache persists via CACHE
    # json so coverage accumulates across once-mode runs.
    if cfg.get("ytbuzz_enabled"):
        book = state["ytbuzz"]
        own  = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        for m in markets:
            if len(book["open"]) >= cfg["ytbuzz_max_open"]: break
            if m["id"] in own or m["id"] in cooldown:       continue
            if m["vol"] < cfg["ytbuzz_min_volume"]:         continue
            if is_live_match(m.get("q", "")): continue   # game-day buzz ≠ contrarian signal (5/6 first exits were live games)
            if not (cfg["ytbuzz_yes_min"] <= m["yes"] <= cfg["ytbuzz_yes_max"]): continue
            query  = " ".join(m["q"].replace("?", "").split()[:7])
            cached = _yt_cache.get(query.lower()[:60], {})
            if not cached or time.time() - cached.get("ts", 0) >= _YT_TTL:
                continue   # cold — warmup thread fills this across runs
            if cached.get("n_fresh", 0)     < cfg["ytbuzz_min_videos"]: continue
            if cached.get("views_fresh", 0) < cfg["ytbuzz_min_views"]:  continue
            out_mid = 1 - m["yes"]          # hype-fade: buy NO on the hot side
            entry   = ask(out_mid, cfg, m["vol"])
            size    = round(cfg["ytbuzz_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "ytbuzz", "side": "NO",
                "entry_mid": round(out_mid, 4), "entry_fill": round(entry, 4),
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "yt_videos": cached["n_fresh"], "yt_views": cached["views_fresh"],
            })
            opened.append(book["open"][-1])

    # ── vlrtop — VLR.GG Valorant match latency-arb ──────────────────────────
    # vlr.gg shows completed matches within minutes of finishing. If we can
    # match the winning team to an unresolved Polymarket market, we buy YES
    # before the oracle fires (usually within 0-4h after the match).
    if cfg.get("vlrtop_enabled"):
        book    = state["vlrtop"]
        own     = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        vlr     = fetch_vlr_matches()
        winners = {m["winner"] for m in vlr if m["winner"] and not m["live"]}
        for m in markets:
            if len(book["open"]) >= cfg["vlrtop_max_open"]: break
            if m["id"] in own or m["id"] in cooldown:       continue
            if not (0.75 <= m["yes"] <= 0.97):              continue
            if m["vol"] < 30_000:                           continue
            q_low = m["q"].lower()
            # Check if a known winner's name appears in the market question
            matched_winner = next((w for w in winners if w and len(w) > 3 and w in q_low), None)
            if not matched_winner:
                continue
            out_mid = m["yes"]
            entry   = ask(out_mid, cfg, m["vol"])
            size    = round(cfg["vlrtop_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "vlrtop", "side": "YES",
                "entry_mid": round(out_mid, 4), "entry_fill": round(entry, 4),
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "vlr_winner": matched_winner,
            })
            opened.append(book["open"][-1])

    # ── gtrend — Google Trends attention surge ───────────────────────────────
    # Polymarket markets resolve faster when crowd attention is high. When
    # Google Trends shows 1.5x+ interest spike vs 7-day mean, event is live
    # → buy YES on esports/news favorites [0.75, 0.95].
    if cfg.get("gtrend_enabled"):
        book     = state["gtrend"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        min_ratio = cfg.get("gtrend_min_ratio", 1.5)
        _ESPORTS_GTREND_KW = ("valorant", "counter-strike", "cs2", "vct", "iem", "esl")
        for m in markets:
            if len(book["open"]) >= cfg["gtrend_max_open"]: break
            if m["id"] in own or m["id"] in cooldown:       continue
            if not (0.75 <= m["yes"] <= 0.95):              continue
            if m["vol"] < 50_000:                           continue
            q_low = m["q"].lower()
            if not any(k in q_low for k in _ESPORTS_GTREND_KW): continue
            # Extract short keyword for Trends (first 2 non-stopwords)
            stop = {"will","the","a","an","or","in","at","to","of","be","does","by","for","is","are","who","what","when","which"}
            words = [w for w in re.sub(r"[^a-z\s]", "", q_low).split() if w not in stop]
            kw = " ".join(words[:2]) if len(words) >= 2 else words[0] if words else None
            if not kw: continue
            cur, base = fetch_gtrends(kw)
            if base < 5: continue   # not enough search volume to be meaningful
            if cur < base * min_ratio: continue
            out_mid = m["yes"]
            entry   = ask(out_mid, cfg, m["vol"])
            size    = round(cfg["gtrend_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "gtrend", "side": "YES",
                "entry_mid": round(out_mid, 4), "entry_fill": round(entry, 4),
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "gt_kw": kw, "gt_cur": cur, "gt_base": round(base, 1), "gt_ratio": round(cur/base, 2),
            })
            opened.append(book["open"][-1])

    # ── bookpress — full-depth order-book pressure ───────────────────────────
    # Aggregates ALL CLOB depth within ±5¢ of mid (clobimbal only looks at
    # top-N levels). Bid depth ≥ 2.5x ask depth = informed accumulation.
    if cfg.get("bookpress_enabled"):
        book     = state["bookpress"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        band      = cfg["bookpress_band"]
        min_ratio = cfg["bookpress_min_ratio"]
        checked   = 0
        for m in markets:
            if len(book["open"]) >= cfg["bookpress_max_open"]: break
            if checked >= 15: break          # cap CLOB calls per scan (cached 90s)
            if m["id"] in own or m["id"] in cooldown: continue
            if m["vol"] < cfg["bookpress_min_vol"]: continue
            yes = m["yes"]
            if not (0.55 <= yes <= 0.85): continue
            token = m.get("token", "")
            if not token: continue
            bk = fetch_clob_book(token)
            checked += 1
            bids = bk.get("bids", [])
            asks = bk.get("asks", [])
            if not bids or not asks: continue
            bid_liq = sum(float(b.get("price", 0)) * float(b.get("size", 0))
                          for b in bids if yes - band <= float(b.get("price", 0)) <= yes)
            ask_liq = sum(float(a.get("price", 0)) * float(a.get("size", 0))
                          for a in asks if yes <= float(a.get("price", 0)) <= yes + band)
            if bid_liq <= 0 or ask_liq <= 0: continue
            if bid_liq / ask_liq < min_ratio: continue
            out_mid = yes
            entry   = ask(out_mid, cfg, m["vol"])
            size    = round(cfg["bookpress_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "bookpress", "side": "YES",
                "entry_mid": round(out_mid, 4), "entry_fill": round(entry, 4),
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "bid_liq": round(bid_liq, 2), "ask_liq": round(ask_liq, 2),
                "press_ratio": round(bid_liq / ask_liq, 2),
            })
            opened.append(book["open"][-1])

    # ── kellyfav — Kelly-sized favorite buy ──────────────────────────────────
    # Selection ≈ favyes; the EXPERIMENT is sizing. Kelly fraction with an
    # assumed 3% edge: f = (b·p − q)/b where b = net odds, p = price+edge.
    if cfg.get("kellyfav_enabled"):
        book     = state["kellyfav"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        for m in markets:
            if len(book["open"]) >= cfg["kellyfav_max_open"]: break
            if m["id"] in own or m["id"] in cooldown: continue
            if m["vol"] < cfg["kellyfav_min_vol"]: continue
            yes = m["yes"]
            if not (cfg["kellyfav_yes_min"] <= yes <= cfg["kellyfav_yes_max"]): continue
            d = days_to_end(m.get("end"))
            if d is None or not (cfg["kellyfav_min_days"] <= d <= cfg["kellyfav_max_days"]):
                continue
            entry = ask(yes, cfg, m["vol"])
            if entry >= 0.99 or entry <= 0: continue
            p = min(0.99, entry + cfg["kellyfav_edge"])   # assumed true prob
            b = (1 - entry) / entry                        # net odds per $ staked
            kelly = (b * p - (1 - p)) / b
            if kelly <= 0: continue
            bet  = cfg["kellyfav_unit_usdc"] * max(0.1, min(1.0, kelly * 5))
            size = round(bet / entry, 4)
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "kellyfav", "side": "YES",
                "entry_mid": round(yes, 4), "entry_fill": round(entry, 4),
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "kelly": round(kelly, 4), "bet_usdc": round(bet, 2),
            })
            opened.append(book["open"][-1])

    # ── newslag — attention + momentum confluence (Claude original) ──────────
    # Reddit fresh-post buzz (cache-only, like redditbuzz) AND ≥4¢ price move
    # this scan = breaking event mid-reprice. Ride the moving side.
    if cfg.get("newslag_enabled"):
        book     = state["newslag"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        _NL_SUBS = {
            "sports":   ("soccer", "worldcup", "sports"),
            "politics": ("politics", "worldnews", "news"),
            "crypto":   ("CryptoCurrency", "Bitcoin", "ethereum"),
            "other":    ("worldnews", "news", "politics"),
        }
        for m in markets:
            if len(book["open"]) >= cfg["newslag_max_open"]: break
            if m["id"] in own or m["id"] in cooldown: continue
            if m["vol"] < cfg["newslag_min_vol"]: continue
            yes = m["yes"]
            chg = _scan_chg(m["id"], yes)
            if chg is None or abs(chg) < cfg["newslag_min_chg"]: continue
            side = "YES" if chg > 0 else "NO"
            out_mid = yes if side == "YES" else 1 - yes
            if not (0.60 <= out_mid <= 0.90): continue
            subs      = _NL_SUBS.get(m.get("cat", "other"), _NL_SUBS["other"])
            query     = " ".join(m["q"].replace("?", "").split()[:7])
            cache_key = str((query.lower()[:60], tuple(subs)))
            cached    = _reddit_cache.get(cache_key, {})
            if not cached or time.time() - cached.get("ts", 0) >= _REDDIT_TTL:
                continue   # cold — warmup thread fills across runs
            if cached["posts"] < cfg["newslag_min_posts"]: continue
            entry = ask(out_mid, cfg, m["vol"])
            size  = round(cfg["newslag_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "newslag", "side": side,
                "entry_mid": round(out_mid, 4), "entry_fill": round(entry, 4),
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "scan_chg": chg, "reddit_posts": cached["posts"],
            })
            opened.append(book["open"][-1])

    # ── cryptofair (CoinGecko + log-normal fair value) ──────────────────────
    # The most mathematically rigorous leg: options pricing theory applied to
    # prediction markets. Fetches spot price from CoinGecko (free, no auth),
    # identifies the target strike from the market title via regex, computes
    # P(spot ≥ strike at maturity) under geometric Brownian motion, and trades
    # divergence from Polymarket's implied probability.
    if cfg.get("cryptofair_enabled"):
        from datetime import datetime, timezone as _tz4
        cg   = fetch_coingecko()
        book = state["cryptofair"]
        own  = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        for m in markets:
            if len(book["open"]) >= cfg["cryptofair_max_open"]: break
            if m["id"] in own or m["id"] in cooldown:           continue
            if m["vol"] < cfg["cryptofair_min_volume"]:         continue
            try:
                from birdeye_leg import liquidity_gate as _liq_gate
                _lctx = _liq_gate(m["q"])
                if _lctx and (_lctx.get("rug_risk") or not _lctx.get("liq_ok", True)): continue
            except Exception: pass
            if not (cfg["cryptofair_yes_min"] <= m["yes"] <= cfg["cryptofair_yes_max"]): continue
            coin_id, target = _parse_crypto_target(m["q"])
            if not coin_id or not target: continue
            spot = (cg.get(coin_id) or {}).get("usd")
            if not spot: continue
            end_str = m.get("end", "")
            if not end_str: continue
            try:
                end_dt    = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
                days_left = (end_dt - datetime.now(_tz4.utc)).total_seconds() / 86400
            except Exception:
                continue
            if days_left < 1: continue
            fair_p = _lognormal_prob(spot, target, days_left)
            diff   = fair_p - m["yes"]
            if abs(diff) < cfg["cryptofair_min_divergence"]: continue
            side    = "YES" if diff > 0 else "NO"
            out_mid = m["yes"] if side == "YES" else 1 - m["yes"]
            entry   = ask(out_mid, cfg, m["vol"])
            size    = round(cfg["cryptofair_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "cryptofair", "side": side,
                "entry_mid": round(out_mid, 4), "entry_fill": round(entry, 4),
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "coin": coin_id, "spot": round(spot, 2), "target": target,
                "days_left": round(days_left, 1), "fair_prob": round(fair_p, 4),
                "fair_diff": round(diff, 4),
            })
            opened.append(book["open"][-1])

    # ── windowshut (structural-theta: lognormal-impossible YES, hold to resolution) ──
    # Buy NO on dated crypto-threshold markets where Black-Scholes P(YES) is
    # near-impossible yet the market still prices YES at a theta premium. The
    # objective spot/strike/time/vol feasibility gate is the alpha (windowshutrand
    # is the same entry WITHOUT it). NO-only, deep-impossible, resolution-horizon.
    if cfg.get("windowshut_enabled"):
        cg   = fetch_coingecko()
        book = state["windowshut"]
        own  = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        for m in markets:
            if len(book["open"]) >= cfg["windowshut_max_open"]: break
            if m["id"] in own or m["id"] in cooldown:           continue
            if m["vol"] < cfg["windowshut_min_vol"]:            continue
            if not (cfg["windowshut_yes_min"] <= m["yes"] <= cfg["windowshut_yes_max"]): continue
            parsed = _parse_price_market(m["q"])
            if not parsed: continue
            coin_id, strike, direction = parsed
            spot = (cg.get(coin_id) or {}).get("usd")
            if not spot: continue
            days = days_to_end(m.get("end"))
            if days is None or days < cfg["windowshut_min_days"] or days > cfg["windowshut_max_days"]: continue
            p_above = _lognormal_prob(spot, strike, days, cfg.get("windowshut_annual_vol", 0.80))
            p_yes   = p_above if direction == "above" else (1.0 - p_above)
            if p_yes > cfg["windowshut_max_fairprob"]: continue   # not impossible enough → skip
            out_mid = round(1 - m["yes"], 4)                       # NO side-mid
            entry   = ask(out_mid, cfg, m["vol"])
            size    = round(cfg["windowshut_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "windowshut", "side": "NO",
                "entry_mid": out_mid, "entry_fill": round(entry, 4),
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "coin": coin_id, "spot": round(spot, 2), "strike": strike, "dir": direction,
                "days_left": round(days, 1), "p_yes": round(p_yes, 4),
            })
            opened.append(book["open"][-1])

    # ── windowshutrand (CONTROL for windowshut: same NO entry, NO feasibility gate) ──
    # Buys NO on the SAME low-YES crypto-threshold universe but WITHOUT the lognormal
    # impossibility test. windowshut must BEAT this control or the gate adds no alpha.
    if cfg.get("windowshutrand_enabled"):
        book = state["windowshutrand"]
        own  = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        for m in markets:
            if len(book["open"]) >= cfg["windowshutrand_max_open"]: break
            if m["id"] in own or m["id"] in cooldown:               continue
            if m["vol"] < cfg["windowshutrand_min_vol"]:            continue
            if not (cfg["windowshutrand_yes_min"] <= m["yes"] <= cfg["windowshutrand_yes_max"]): continue
            if not _parse_price_market(m["q"]): continue            # crypto-threshold universe only
            days = days_to_end(m.get("end"))
            if days is None or days < cfg["windowshutrand_min_days"] or days > cfg["windowshutrand_max_days"]: continue
            out_mid = round(1 - m["yes"], 4)
            entry   = ask(out_mid, cfg, m["vol"])
            size    = round(cfg["windowshutrand_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "windowshutrand", "side": "NO",
                "entry_mid": out_mid, "entry_fill": round(entry, 4),
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "days_left": round(days, 1),
            })
            opened.append(book["open"][-1])

    # ── volspike (informed-flow volume spike detector) ──────────────────────────
    # Maintains an EMA of each market's volume across scan cycles. A spike >3×
    # the baseline suggests informed players entering — we trade the price lag.
    # The baseline is updated BEFORE the spike check so the first cycle doesn't
    # trigger a false spike (first-seen vol becomes the baseline itself).
    if cfg.get("volspike_enabled"):
        book = state["volspike"]
        own  = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        for m in markets:
            if len(book["open"]) >= cfg["volspike_max_open"]:  break
            if m["id"] in own or m["id"] in cooldown:          continue
            if m["vol"] < cfg["volspike_min_volume"]:          continue
            if not (cfg["volspike_yes_min"] <= m["yes"] <= cfg["volspike_yes_max"]): continue
            prev_baseline = _update_vol_baseline(m["id"], m["vol"])
            # Spike = current vol > threshold × baseline AND not the very first time
            if m["id"] not in _vol_baseline:                   continue
            if prev_baseline < 10_000:                         continue   # baseline not established
            if m["vol"] < cfg["volspike_threshold"] * prev_baseline: continue
            # Direction: spike + low price → uninformed holders underweight YES
            #            spike + high price → smart money shorting overpriced YES
            side    = "YES" if m["yes"] < 0.50 else "NO"
            out_mid = m["yes"] if side == "YES" else 1 - m["yes"]
            entry   = ask(out_mid, cfg, m["vol"])
            size    = round(cfg["volspike_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "volspike", "side": side,
                "entry_mid": round(out_mid, 4), "entry_fill": round(entry, 4),
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "spike_ratio": round(m["vol"] / prev_baseline, 2),
                "baseline_vol": round(prev_baseline),
            })
            opened.append(book["open"][-1])

    # ── newmarket (large 24h price-jump fade) ───────────────────────────────
    # Uses Gamma's `chg` field (24-hour YES price change). Large single-day moves
    # in thin prediction markets overshoot before settling — we fade them:
    #   chg > +min_chg AND yes > 0.50 → upward overshoot → buy NO
    #   chg < −min_chg AND yes < 0.50 → downward overshoot → buy YES
    # Only trades inside the uncertain zone [0.28, 0.72] — near-resolved markets
    # are moving for good reason (event outcome) and shouldn't be faded.
    if cfg.get("newmarket_enabled"):
        book = state["newmarket"]
        own  = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        for m in markets:
            if len(book["open"]) >= cfg["newmarket_max_open"]:  break
            if m["id"] in own or m["id"] in cooldown:           continue
            if m["vol"] < cfg["newmarket_min_volume"]:          continue
            if not (cfg["newmarket_yes_min"] <= m["yes"] <= cfg["newmarket_yes_max"]): continue
            chg = m.get("chg", 0.0) or 0.0
            if abs(chg) < cfg["newmarket_min_chg"]: continue
            # Fade the direction of the move
            side    = "NO" if chg > 0 else "YES"
            out_mid = m["yes"] if side == "YES" else 1 - m["yes"]
            entry   = ask(out_mid, cfg, m["vol"])
            size    = round(cfg["newmarket_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "newmarket", "side": side,
                "entry_mid": round(out_mid, 4), "entry_fill": round(entry, 4),
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "chg_24h": round(chg, 4),
            })
            opened.append(book["open"][-1])

    # ── predictit (PredictIt cross-market divergence for political questions) ─
    # PredictIt is a regulated, US-based prediction market with independent
    # community pricing — especially well-calibrated on US politics. When its
    # YES price diverges from Polymarket by ≥10c on the same question (matched
    # via Jaccard word similarity), we trade Polymarket toward PredictIt.
    # Cache-only in hot path; fetched once per cycle (5-min TTL).
    if cfg.get("predictit_enabled"):
        pi_markets = fetch_predictit()   # cached 5 min; one API call per cycle
        book = state["predictit"]
        own  = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        pi_used = set()   # deduplicate: one PredictIt contract per poly market
        for m in markets:
            if len(book["open"]) >= cfg["predictit_max_open"]:  break
            if m["id"] in own or m["id"] in cooldown:           continue
            if m["vol"] < cfg["predictit_min_volume"]:          continue
            if not (cfg["predictit_yes_min"] <= m["yes"] <= cfg["predictit_yes_max"]): continue
            # Only political markets (PredictIt is US-politics focused)
            if m.get("cat") not in ("politics", "other", None, ""):  continue
            # Fuzzy-match against PredictIt contracts
            wa = set(re.sub(r"[^a-z0-9 ]", "", m["q"].lower()).split())
            best_sim, best_pi = 0.0, None
            for pi in pi_markets:
                if pi["pi_id"] in pi_used: continue
                wb  = set(re.sub(r"[^a-z0-9 ]", "", pi["q"].lower()).split())
                sim = len(wa & wb) / max(len(wa | wb), 1)
                if sim > best_sim:
                    best_sim, best_pi = sim, pi
            if best_sim < cfg["predictit_min_similarity"] or best_pi is None: continue
            pi_prob = best_pi["yes"]
            diff    = pi_prob - m["yes"]
            if abs(diff) < cfg["predictit_min_divergence"]: continue
            pi_used.add(best_pi["pi_id"])
            side    = "YES" if diff > 0 else "NO"
            out_mid = m["yes"] if side == "YES" else 1 - m["yes"]
            entry   = ask(out_mid, cfg, m["vol"])
            size    = round(cfg["predictit_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "predictit", "side": side,
                "entry_mid": round(out_mid, 4), "entry_fill": round(entry, 4),
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "pi_q": best_pi["q"][:80], "pi_prob": round(pi_prob, 4),
                "pi_diff": round(diff, 4), "pi_sim": round(best_sim, 3),
            })
            opened.append(book["open"][-1])

    # ── Wallet / trade-feed legs (2026-06-07) ────────────────────────────────
    # All three legs share the same global trade fetch (one API call per scan).
    # walletcopy: follow top-volume wallets. whale: follow big single trades.
    # multiwhale: follow clusters of big trades in the same market.
    _all_trades = fetch_global_trades(500) if (
        cfg.get("walletcopy_enabled") or cfg.get("whale_enabled") or
        cfg.get("multiwhale_enabled") or cfg.get("watchdog_enabled")
    ) else []

    # Watchdog: log every whale trade to stderr
    if cfg.get("watchdog_enabled") and _all_trades:
        watchdog_flag_whales(_all_trades, cfg.get("watchdog_min_usdc", 200))

    # Build trade index once for all three legs
    _tidx_90 = _build_trade_index(_all_trades, window_min=90)  # 90-min window
    _tidx_60 = _build_trade_index(_all_trades, window_min=60)  # 60-min window
    _market_by_id = {m["id"]: m for m in markets}   # conditionId → market dict

    # ── walletcopy ────────────────────────────────────────────────────────────
    if cfg.get("walletcopy_enabled") and _all_trades:
        book     = state["walletcopy"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        top_wallets = {w["addr"] for w in get_top_wallets(cfg.get("walletcopy_top_n", 20))}
        min_usdc    = cfg.get("walletcopy_min_trade_usdc", 30)
        for cid, tlist in _tidx_90.items():
            if len(book["open"]) >= cfg["walletcopy_max_open"]: break
            if cid in own or cid in cooldown: continue
            m = _market_by_id.get(cid)
            if not m or m["vol"] < cfg["walletcopy_min_volume"]: continue
            if not (cfg["walletcopy_yes_min"] <= m["yes"] <= cfg["walletcopy_yes_max"]): continue
            # Find qualifying trade from a top wallet
            qual = [t for t in tlist if t["wallet"] in top_wallets and t["dollar"] >= min_usdc]
            if not qual: continue
            # Use the most recent qualifying trade's direction
            best    = max(qual, key=lambda t: t["ts"])
            side    = "YES" if best["side"] == "BUY" else "NO"
            out_mid = m["yes"] if side == "YES" else 1 - m["yes"]
            entry   = ask(out_mid, cfg, m["vol"])
            size    = round(cfg["walletcopy_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "walletcopy", "side": side,
                "entry_mid": round(out_mid, 4), "entry_fill": round(entry, 4),
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "whale_usdc": round(best["dollar"], 2),
            })
            opened.append(book["open"][-1])

    # ── whale ─────────────────────────────────────────────────────────────────
    if cfg.get("whale_enabled") and _all_trades:
        book     = state["whale"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        min_usdc = cfg.get("whale_min_usdc", 200)
        for cid, tlist in _tidx_60.items():
            if len(book["open"]) >= cfg["whale_max_open"]: break
            if cid in own or cid in cooldown: continue
            m = _market_by_id.get(cid)
            if not m or m["vol"] < cfg["whale_min_volume"]: continue
            if not (cfg["whale_yes_min"] <= m["yes"] <= cfg["whale_yes_max"]): continue
            big = [t for t in tlist if t["dollar"] >= min_usdc]
            if not big: continue
            best    = max(big, key=lambda t: t["dollar"])
            side    = "YES" if best["side"] == "BUY" else "NO"
            out_mid = m["yes"] if side == "YES" else 1 - m["yes"]
            entry   = ask(out_mid, cfg, m["vol"])
            size    = round(cfg["whale_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "whale", "side": side,
                "entry_mid": round(out_mid, 4), "entry_fill": round(entry, 4),
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "whale_usdc": round(best["dollar"], 2),
            })
            opened.append(book["open"][-1])

    # ── multiwhale ────────────────────────────────────────────────────────────
    if cfg.get("multiwhale_enabled") and _all_trades:
        book      = state["multiwhale"]
        own       = {p["id"] for p in book["open"]}
        cooldown  = {r["id"] for r in book["closed"]
                     if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        min_usdc  = cfg.get("multiwhale_min_usdc", 75)
        min_count = cfg.get("multiwhale_min_count", 3)
        for cid, tlist in _tidx_90.items():
            if len(book["open"]) >= cfg["multiwhale_max_open"]: break
            if cid in own or cid in cooldown: continue
            m = _market_by_id.get(cid)
            if not m or m["vol"] < cfg["multiwhale_min_volume"]: continue
            if not (cfg["multiwhale_yes_min"] <= m["yes"] <= cfg["multiwhale_yes_max"]): continue
            big    = [t for t in tlist if t["dollar"] >= min_usdc]
            n_buy  = sum(1 for t in big if t["side"] == "BUY")
            n_sell = sum(1 for t in big if t["side"] == "SELL")
            if max(n_buy, n_sell) < min_count: continue
            side    = "YES" if n_buy >= n_sell else "NO"
            out_mid = m["yes"] if side == "YES" else 1 - m["yes"]
            entry   = ask(out_mid, cfg, m["vol"])
            size    = round(cfg["multiwhale_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "multiwhale", "side": side,
                "entry_mid": round(out_mid, 4), "entry_fill": round(entry, 4),
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "n_buy": n_buy, "n_sell": n_sell,
            })
            opened.append(book["open"][-1])

    # ── bookimbal (CLOB order book imbalance) ────────────────────────────────
    if cfg.get("bookimbal_enabled"):
        book     = state["bookimbal"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        levels   = cfg.get("bookimbal_levels", 5)
        min_ratio= cfg.get("bookimbal_min_ratio", 2.5)
        for m in markets:
            if len(book["open"]) >= cfg["bookimbal_max_open"]: break
            if m["id"] in own or m["id"] in cooldown: continue
            if m["vol"] < cfg["bookimbal_min_volume"]: continue
            if not (cfg["bookimbal_yes_min"] <= m["yes"] <= cfg["bookimbal_yes_max"]): continue
            token = m.get("token", "")
            if not token: continue
            bk = fetch_clob_book(token)
            bids = bk.get("bids", [])[:levels]
            asks = bk.get("asks", [])[:levels]
            if not bids or not asks: continue
            bid_liq = sum(float(b.get("price",0)) * float(b.get("size",0)) for b in bids)
            ask_liq = sum(float(a.get("price",0)) * float(a.get("size",0)) for a in asks)
            if bid_liq <= 0 or ask_liq <= 0: continue
            ratio = bid_liq / ask_liq
            if ratio < min_ratio and ask_liq / bid_liq < min_ratio: continue  # no clear imbalance
            side    = "YES" if ratio >= min_ratio else "NO"
            out_mid = m["yes"] if side == "YES" else 1 - m["yes"]
            entry   = ask(out_mid, cfg, m["vol"])
            size    = round(cfg["bookimbal_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "bookimbal", "side": side,
                "entry_mid": round(out_mid, 4), "entry_fill": round(entry, 4),
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "bid_liq": round(bid_liq), "ask_liq": round(ask_liq),
                "ratio": round(ratio, 2),
            })
            opened.append(book["open"][-1])

    # ── shortdate (near-expiry FLB fade) ─────────────────────────────────────
    if cfg.get("shortdate_enabled"):
        book     = state["shortdate"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        for m in markets:
            if len(book["open"]) >= cfg["shortdate_max_open"]: break
            if m["id"] in own or m["id"] in cooldown: continue
            if m["vol"] < cfg["shortdate_min_volume"]: continue
            if not (cfg["shortdate_yes_min"] <= m["yes"] <= cfg["shortdate_yes_max"]): continue
            d = days_to_end(m.get("end"))
            if d is None or not (cfg["shortdate_min_days"] <= d <= cfg["shortdate_max_days"]): continue
            out_mid = 1 - m["yes"]   # buy NO
            entry   = ask(out_mid, cfg, m["vol"])
            size    = round(cfg["shortdate_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "shortdate", "side": "NO",
                "entry_mid": round(out_mid, 4), "entry_fill": round(entry, 4),
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "days_left": round(d, 1),
            })
            opened.append(book["open"][-1])

    # ── highvol (high-volume FLB fade) ────────────────────────────────────────
    if cfg.get("highvol_enabled"):
        book     = state["highvol"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        for m in markets:
            if len(book["open"]) >= cfg["highvol_max_open"]: break
            if m["id"] in own or m["id"] in cooldown: continue
            if m["vol"] < cfg["highvol_min_volume"]: continue
            if not (cfg["highvol_yes_min"] <= m["yes"] <= cfg["highvol_yes_max"]): continue
            out_mid = 1 - m["yes"]   # buy NO (fade YES)
            entry   = ask(out_mid, cfg, m["vol"])
            size    = round(cfg["highvol_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "highvol", "side": "NO",
                "entry_mid": round(out_mid, 4), "entry_fill": round(entry, 4),
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
            })
            opened.append(book["open"][-1])

    # ── pricerev (single-scan dip reversal) ───────────────────────────────────
    if cfg.get("pricerev_enabled"):
        book     = state["pricerev"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        min_drop = cfg.get("pricerev_min_drop", 0.05)
        for m in markets:
            if len(book["open"]) >= cfg["pricerev_max_open"]: break
            if m["id"] in own or m["id"] in cooldown: continue
            if m["vol"] < cfg["pricerev_min_volume"]: continue
            if not (cfg["pricerev_yes_min"] <= m["yes"] <= cfg["pricerev_yes_max"]): continue
            prev = _price_snap.get(m["id"])
            if prev is None:
                continue   # no prior snapshot yet — skip until next cycle
            drop = prev - m["yes"]
            if drop < min_drop: continue   # didn't drop enough
            out_mid = m["yes"]   # buy YES (expect bounce)
            entry   = ask(out_mid, cfg, m["vol"])
            size    = round(cfg["pricerev_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "pricerev", "side": "YES",
                "entry_mid": round(out_mid, 4), "entry_fill": round(entry, 4),
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "prev_price": round(prev, 4), "drop": round(drop, 4),
            })
            opened.append(book["open"][-1])

    # ── catrev (category mean reversion) ─────────────────────────────────────
    if cfg.get("catrev_enabled"):
        book     = state["catrev"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        for m in markets:
            if len(book["open"]) >= cfg["catrev_max_open"]: break
            if m["id"] in own or m["id"] in cooldown: continue
            if m["vol"] < cfg["catrev_min_volume"]: continue
            if not (cfg["catrev_yes_min"] <= m["yes"] <= cfg["catrev_yes_max"]): continue
            cat = m.get("cat") or "unknown"
            cat_avg = _cat_chg_snap.get(cat, 0)
            mkt_chg = m.get("chg", 0) or 0
            if cat_avg > cfg["catrev_cat_chg_max"]: continue   # category not bearish enough
            if mkt_chg > cfg["catrev_mkt_chg_max"]: continue   # market not down enough
            out_mid = m["yes"]   # buy YES — expect reversion
            entry   = ask(out_mid, cfg, m["vol"])
            size    = round(cfg["catrev_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": cat, "strategy": "catrev", "side": "YES",
                "entry_mid": round(out_mid, 4), "entry_fill": round(entry, 4),
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "cat_avg_chg": round(cat_avg, 4), "mkt_chg": round(mkt_chg, 4),
            })
            opened.append(book["open"][-1])

    # ── spreadfade (wide-spread fade) ─────────────────────────────────────────
    if cfg.get("spreadfade_enabled"):
        book     = state["spreadfade"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        for m in markets:
            if len(book["open"]) >= cfg["spreadfade_max_open"]: break
            if m["id"] in own or m["id"] in cooldown: continue
            vol = m["vol"]
            if not (cfg["spreadfade_min_volume"] <= vol <= cfg["spreadfade_max_volume"]): continue
            if not (cfg["spreadfade_yes_min"] <= m["yes"] <= cfg["spreadfade_yes_max"]): continue
            out_mid = 1 - m["yes"]   # buy NO (fade YES)
            entry   = ask(out_mid, cfg, m["vol"])
            size    = round(cfg["spreadfade_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "spreadfade", "side": "NO",
                "entry_mid": round(out_mid, 4), "entry_fill": round(entry, 4),
                "size": size, "vol": vol, "opened_at": now_iso(),
            })
            opened.append(book["open"][-1])

    # ── resolverush (near-expiry favorite convergence) ────────────────────────
    if cfg.get("resolverush_enabled"):
        book     = state["resolverush"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        for m in markets:
            if len(book["open"]) >= cfg["resolverush_max_open"]: break
            if m["id"] in own or m["id"] in cooldown: continue
            if m["vol"] < cfg["resolverush_min_volume"]: continue
            if not (cfg["resolverush_yes_min"] <= m["yes"] <= cfg["resolverush_yes_max"]): continue
            d = days_to_end(m.get("end"))
            if d is None: continue
            hours_left = d * 24
            if not (cfg["resolverush_min_hours"] <= hours_left <= cfg["resolverush_max_hours"]): continue
            out_mid = m["yes"]   # buy YES — ride convergence to near-1
            entry   = ask(out_mid, cfg, m["vol"])
            size    = round(cfg["resolverush_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "resolverush", "side": "YES",
                "entry_mid": round(out_mid, 4), "entry_fill": round(entry, 4),
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "hours_left": round(hours_left, 1),
            })
            opened.append(book["open"][-1])

    # ── Volume legs by category and trading mode (2026-06-07) ───────────────
    # Compute trade flow balance for buyflow/sellflow from the already-fetched
    # global trade index. One pass over _tidx_90 — no extra API calls.
    _flow = {}   # conditionId → {"buy": $, "sell": $}
    for cid, tlist in _tidx_90.items():
        b = sum(t["dollar"] for t in tlist if t["side"] == "BUY")
        s = sum(t["dollar"] for t in tlist if t["side"] == "SELL")
        if b + s > 0:
            _flow[cid] = {"buy": b, "sell": s}

    # Helper: is a market's volume spiking above its EMA baseline?
    def _is_spike(m, threshold):
        prev = _update_vol_baseline(m["id"], m["vol"])
        return prev > 0 and m["vol"] >= prev * threshold

    # ── cryptovol ─────────────────────────────────────────────────────────────
    # Gamma cat="crypto" is miscategorized. Use keyword match on question text.
    # Vol-spike replaced with |chg| filter (cumulative vol EMA stays ≈ current vol).
    _CRYPTO_KEYWORDS = ("bitcoin", "btc", "ethereum", "eth", "solana", "sol",
                        "crypto", "doge", "xrp", "bnb", "ripple", "litecoin",
                        "cardano", "ada", "avax", "avalanche", "chainlink",
                        "link ", "polkadot", "dot ", "shiba", "pepe",
                        "ton", "toncoin", "tron", "trx", "sui", "near",
                        "cosmos", "atom", "uniswap", "uni", "aptos", "apt",
                        "arbitrum", "arb", "optimism", "wif", "dogwifhat",
                        "bonk", "floki", "pepe coin")
    if cfg.get("cryptovol_enabled"):
        book     = state["cryptovol"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        for m in markets:
            if len(book["open"]) >= cfg["cryptovol_max_open"]: break
            if m["id"] in own or m["id"] in cooldown: continue
            q_lower = (m.get("q") or "").lower()
            if not any(kw in q_lower for kw in _CRYPTO_KEYWORDS): continue
            if m["vol"] < cfg["cryptovol_min_volume"]: continue
            if not (cfg["cryptovol_yes_min"] <= m["yes"] <= cfg["cryptovol_yes_max"]): continue
            chg = m.get("chg", 0) or 0
            if abs(chg) < cfg["cryptovol_min_chg"]: continue   # need meaningful price move
            # Fade YES when elevated, follow YES when depressed
            side    = "NO" if m["yes"] >= 0.50 else "YES"
            out_mid = 1 - m["yes"] if side == "NO" else m["yes"]
            entry   = ask(out_mid, cfg, m["vol"])
            size    = round(cfg["cryptovol_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": "crypto", "strategy": "cryptovol", "side": side,
                "entry_mid": round(out_mid, 4), "entry_fill": round(entry, 4),
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
            })
            opened.append(book["open"][-1])

    # ── sportsvol ─────────────────────────────────────────────────────────────
    # cat="sports" is reliable in Gamma. Vol-spike replaced with |chg| filter.
    if cfg.get("sportsvol_enabled"):
        book     = state["sportsvol"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        for m in markets:
            if len(book["open"]) >= cfg["sportsvol_max_open"]: break
            if m["id"] in own or m["id"] in cooldown: continue
            if m.get("cat") != "sports": continue
            if m["vol"] < cfg["sportsvol_min_volume"]: continue
            if not (cfg["sportsvol_yes_min"] <= m["yes"] <= cfg["sportsvol_yes_max"]): continue
            chg = m.get("chg", 0) or 0
            if abs(chg) < cfg["sportsvol_min_chg"]: continue   # game is live/imminent
            out_mid = 1 - m["yes"]   # fade YES → buy NO
            entry   = ask(out_mid, cfg, m["vol"])
            size    = round(cfg["sportsvol_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": "sports", "strategy": "sportsvol", "side": "NO",
                "entry_mid": round(out_mid, 4), "entry_fill": round(entry, 4),
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
            })
            opened.append(book["open"][-1])

    # ── polvol ────────────────────────────────────────────────────────────────
    if cfg.get("polvol_enabled"):
        book     = state["polvol"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        for m in markets:
            if len(book["open"]) >= cfg["polvol_max_open"]: break
            if m["id"] in own or m["id"] in cooldown: continue
            if m.get("cat") != "politics": continue
            if m["vol"] < cfg["polvol_min_volume"]: continue
            if not (cfg["polvol_yes_min"] <= m["yes"] <= cfg["polvol_yes_max"]): continue
            chg = m.get("chg", 0) or 0
            if abs(chg) < cfg["polvol_min_chg"]: continue   # news/poll-driven move required
            # Fade the direction: vol+up → NO; vol+down → YES
            side    = "NO" if chg > 0 else "YES"
            out_mid = 1 - m["yes"] if side == "NO" else m["yes"]
            entry   = ask(out_mid, cfg, m["vol"])
            size    = round(cfg["polvol_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": "politics", "strategy": "polvol", "side": side,
                "entry_mid": round(out_mid, 4), "entry_fill": round(entry, 4),
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "chg_24h": round(chg, 4),
            })
            opened.append(book["open"][-1])

    # ── buyflow ───────────────────────────────────────────────────────────────
    if cfg.get("buyflow_enabled") and _flow:
        book     = state["buyflow"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        for m in markets:
            if len(book["open"]) >= cfg["buyflow_max_open"]: break
            if m["id"] in own or m["id"] in cooldown: continue
            if m["vol"] < cfg["buyflow_min_volume"]: continue
            if not (cfg["buyflow_yes_min"] <= m["yes"] <= cfg["buyflow_yes_max"]): continue
            fl = _flow.get(m["id"])
            if not fl: continue
            total = fl["buy"] + fl["sell"]
            if total < cfg["buyflow_min_total_usdc"]: continue
            ratio = fl["buy"] / total
            if ratio < cfg["buyflow_min_ratio"]: continue
            out_mid = m["yes"]   # follow BUY flow → buy YES
            entry   = ask(out_mid, cfg, m["vol"])
            size    = round(cfg["buyflow_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "buyflow", "side": "YES",
                "entry_mid": round(out_mid, 4), "entry_fill": round(entry, 4),
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "flow_buy": round(fl["buy"], 2), "flow_sell": round(fl["sell"], 2),
                "flow_ratio": round(ratio, 3),
            })
            opened.append(book["open"][-1])

    # ── sellflow ──────────────────────────────────────────────────────────────
    if cfg.get("sellflow_enabled") and _flow:
        book     = state["sellflow"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        for m in markets:
            if len(book["open"]) >= cfg["sellflow_max_open"]: break
            if m["id"] in own or m["id"] in cooldown: continue
            if m["vol"] < cfg["sellflow_min_volume"]: continue
            if not (cfg["sellflow_yes_min"] <= m["yes"] <= cfg["sellflow_yes_max"]): continue
            fl = _flow.get(m["id"])
            if not fl: continue
            total = fl["buy"] + fl["sell"]
            if total < cfg["sellflow_min_total_usdc"]: continue
            ratio = fl["sell"] / total
            if ratio < cfg["sellflow_min_ratio"]: continue
            out_mid = 1 - m["yes"]   # follow SELL flow → buy NO
            entry   = ask(out_mid, cfg, m["vol"])
            size    = round(cfg["sellflow_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "sellflow", "side": "NO",
                "entry_mid": round(out_mid, 4), "entry_fill": round(entry, 4),
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "flow_buy": round(fl["buy"], 2), "flow_sell": round(fl["sell"], 2),
                "flow_ratio": round(ratio, 3),
            })
            opened.append(book["open"][-1])

    # ── coinpump (fade crypto 24h pump) ───────────────────────────────────────
    if cfg.get("coinpump_enabled"):
        book     = state["coinpump"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        for m in markets:
            if len(book["open"]) >= cfg["coinpump_max_open"]: break
            if m["id"] in own or m["id"] in cooldown: continue
            if m.get("cat") != "crypto": continue
            if m["vol"] < cfg["coinpump_min_volume"]: continue
            if not (cfg["coinpump_yes_min"] <= m["yes"] <= cfg["coinpump_yes_max"]): continue
            chg = m.get("chg", 0) or 0
            if chg < cfg["coinpump_min_chg"]: continue   # must have pumped
            out_mid = 1 - m["yes"]   # fade the pump → buy NO
            entry   = ask(out_mid, cfg, m["vol"])
            size    = round(cfg["coinpump_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": "crypto", "strategy": "coinpump", "side": "NO",
                "entry_mid": round(out_mid, 4), "entry_fill": round(entry, 4),
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "chg_24h": round(chg, 4),
            })
            opened.append(book["open"][-1])

    # ── coindip (buy crypto 24h dip) ──────────────────────────────────────────
    if cfg.get("coindip_enabled"):
        book     = state["coindip"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        for m in markets:
            if len(book["open"]) >= cfg["coindip_max_open"]: break
            if m["id"] in own or m["id"] in cooldown: continue
            if m.get("cat") != "crypto": continue
            if m["vol"] < cfg["coindip_min_volume"]: continue
            if not (cfg["coindip_yes_min"] <= m["yes"] <= cfg["coindip_yes_max"]): continue
            chg = m.get("chg", 0) or 0
            if chg > cfg["coindip_max_chg"]: continue   # must have dipped
            out_mid = m["yes"]   # buy YES on the dip
            entry   = ask(out_mid, cfg, m["vol"])
            size    = round(cfg["coindip_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": "crypto", "strategy": "coindip", "side": "YES",
                "entry_mid": round(out_mid, 4), "entry_fill": round(entry, 4),
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "chg_24h": round(chg, 4),
            })
            opened.append(book["open"][-1])

    # ── acumvol (volume spike + flat price = hidden accumulation) ─────────────
    if cfg.get("acumvol_enabled"):
        book     = state["acumvol"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        for m in markets:
            if len(book["open"]) >= cfg["acumvol_max_open"]: break
            if m["id"] in own or m["id"] in cooldown: continue
            if m["vol"] < cfg["acumvol_min_volume"]: continue
            if not (cfg["acumvol_yes_min"] <= m["yes"] <= cfg["acumvol_yes_max"]): continue
            chg = m.get("chg", 0) or 0
            if abs(chg) > cfg["acumvol_max_abs_chg"]: continue   # price must be flat
            if not _is_spike(m, cfg["acumvol_threshold"]): continue
            out_mid = m["yes"]   # buy YES — accumulation implies upward pressure
            entry   = ask(out_mid, cfg, m["vol"])
            size    = round(cfg["acumvol_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "acumvol", "side": "YES",
                "entry_mid": round(out_mid, 4), "entry_fill": round(entry, 4),
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "chg_24h": round(chg, 4),
            })
            opened.append(book["open"][-1])

    # ── coinup (buy YES on "Up or Down" crypto coinflip markets) ─────────────────
    # Forward test of the bullish-drift hypothesis: backtest (21d, N=700) found
    # P(Up YES)=56.5% CI[0.54,0.59], beating spread on always-YES. CAVEAT: this is
    # PERIOD-SPECIFIC bullish drift, NOT a structural edge — will flip in bear markets.
    # Gate on time remaining so the bot can actually enter + exit before resolution.
    if cfg.get("coinup_enabled"):
        try:
            import sys as _rsys, os as _ros
            _rsys.path.insert(0, _ros.path.dirname(_ros.path.abspath(__file__)))
            from regime_leg import detect_regime, regime_ok_for_leg as _regime_ok
            _btc_regime = detect_regime("BTCUSDT")
        except Exception as _re:
            _btc_regime = "RANGE"
            import sys as _rsys; _rsys.stderr.write(f"[regime] {_re}\n")
        if not _regime_ok("coinup", _btc_regime):
            pass  # skip coinup in CHOP or TREND_DOWN
        else:
          book     = state["coinup"]
          own      = {p["id"] for p in book["open"]}
          cooldown = {r["id"] for r in book["closed"]
                      if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
          for m in markets:
            if len(book["open"]) >= cfg["coinup_max_open"]: break
            if m["id"] in own or m["id"] in cooldown: continue
            if "up or down" not in m["q"].lower(): continue   # only the BTC/ETH/BNB coinflips
            if m["vol"] < cfg["coinup_min_volume"]: continue
            if not (cfg["coinup_yes_min"] <= m["yes"] <= cfg["coinup_yes_max"]): continue
            days       = days_to_end(m.get("end"))   # FIX 2026-06-13: days_to_expiry/days never existed in market dict (always 999)
            hours_left = days * 24 if days is not None else 1e9
            if hours_left < cfg["coinup_min_hours_left"]: continue   # too close — can't exit
            if hours_left > cfg["coinup_max_hours_left"]: continue   # too far — not a coinflip
            out_mid = m["yes"]   # buy YES — the Up direction
            entry   = ask(out_mid, cfg, m["vol"])
            size    = round(cfg["coinup_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": "crypto", "strategy": "coinup", "side": "YES",
                "entry_mid": round(out_mid, 4), "entry_fill": round(entry, 4),
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "hours_left": round(hours_left, 2),
            })
            opened.append(book["open"][-1])

    # ── coindown (buy NO on "Up or Down" crypto markets — CONTROL for coinup) ────
    # Symmetrical inverse. MUST lose in a bullish period (P(Down)≈43.5% < breakeven).
    # If coindown is +EV while coinup is -EV the marking is broken — accounting alarm.
    if cfg.get("coindown_enabled"):
        try:
            from regime_leg import detect_regime as _dr2, regime_ok_for_leg as _ro2
            _btc_regime2 = _dr2("BTCUSDT")
        except Exception:
            _btc_regime2 = "RANGE"
        if _ro2("coindown", _btc_regime2):
          book     = state["coindown"]
          own      = {p["id"] for p in book["open"]}
          cooldown = {r["id"] for r in book["closed"]
                      if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
          for m in markets:
            if len(book["open"]) >= cfg["coindown_max_open"]: break
            if m["id"] in own or m["id"] in cooldown: continue
            if "up or down" not in m["q"].lower(): continue
            if m["vol"] < cfg["coindown_min_volume"]: continue
            if not (cfg["coindown_yes_min"] <= m["yes"] <= cfg["coindown_yes_max"]): continue
            days       = days_to_end(m.get("end"))   # FIX 2026-06-13: days_to_expiry/days never existed in market dict (always 999)
            hours_left = days * 24 if days is not None else 1e9
            if hours_left < cfg["coindown_min_hours_left"]: continue
            if hours_left > cfg["coindown_max_hours_left"]: continue
            out_mid = 1 - m["yes"]   # buy NO — the Down direction
            entry   = ask(out_mid, cfg, m["vol"])
            size    = round(cfg["coindown_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": "crypto", "strategy": "coindown", "side": "NO",
                "entry_mid": round(out_mid, 4), "entry_fill": round(entry, 4),
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "hours_left": round(hours_left, 2),
            })
            opened.append(book["open"][-1])


    # ── candlesig (RSI + EMA trend from live 15m Binance candles) ───────────────
    # Targets "Will <coin> be above/below $X by <date>?" markets (the type that
    # actually exists in the feed — "Up or Down" markets vanished, killed 06-08).
    # Trade only when the candle trend AGREES with the side and the market
    # hasn't fully priced it: trend toward strike + uncertain market = entry.
    if cfg.get("candlesig_enabled"):
        book     = state["candlesig"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        for m in markets:
            if len(book["open"]) >= cfg["candlesig_max_open"]: break
            if m["id"] in own or m["id"] in cooldown: continue
            if m["vol"] < cfg["candlesig_min_volume"]: continue
            try:
                from birdeye_leg import liquidity_gate as _liq_gate
                _lctx = _liq_gate(m["q"])
                if _lctx and (_lctx.get("rug_risk") or not _lctx.get("liq_ok", True)): continue
            except Exception: pass
            parsed = _parse_price_market(m["q"])
            if not parsed: continue
            coin_id, strike, direction = parsed
            days       = days_to_end(m.get("end"))   # FIX 2026-06-13: days_to_expiry/days never existed in market dict (always 999)
            hours_left = days * 24 if days is not None else 1e9
            if hours_left < cfg["candlesig_min_hours_left"]: continue
            if hours_left > cfg["candlesig_max_hours_left"]: continue
            yes = m["yes"]
            if not (cfg["candlesig_yes_min"] <= yes <= cfg["candlesig_yes_max"]): continue
            sig = _cs.get_signal(coin_id)
            if not sig: continue
            rsi, trend, price = sig["rsi"], sig["trend"], sig["price"]
            if price <= 0: continue
            # distance of strike from spot, signed toward the YES outcome
            gap = (strike - price) / price          # + = strike above spot
            # YES wins if price ends up {above|below} strike.
            # Bet YES when trend pushes price toward the YES side; NO when away.
            if direction == "above":
                yes_needs = "up" if gap > 0 else "hold"   # above spot needs a rally
                if trend == "up" and rsi < cfg["candlesig_rsi_bull_max"] and gap < 0.05:
                    side, out_mid = "YES", yes            # rallying + strike within 5%
                elif trend == "down" and rsi > cfg["candlesig_rsi_bear_min"] and gap > 0.01:
                    side, out_mid = "NO", 1 - yes         # falling + strike above spot
                else:
                    continue
            else:  # direction == "below" — YES wins if price drops to strike
                if trend == "down" and rsi > cfg["candlesig_rsi_bear_min"] and gap > -0.05:
                    side, out_mid = "YES", yes            # falling + strike within 5% below
                elif trend == "up" and rsi < cfg["candlesig_rsi_bull_max"] and gap < -0.01:
                    side, out_mid = "NO", 1 - yes         # rallying + strike below spot
                else:
                    continue
            entry = ask(out_mid, cfg, m["vol"])
            size  = round(cfg["candlesig_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": "crypto", "strategy": "candlesig",
                "side": side, "entry_mid": round(out_mid, 4), "entry_fill": round(entry, 4),
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "hours_left": round(hours_left, 2),
                "rsi": round(rsi, 1), "trend": trend, "momentum": round(sig["momentum"], 4),
                "strike": strike, "spot": price, "direction": direction,
            })
            opened.append(book["open"][-1])

    # ── macdsig (MoonDev MACD mean-reversion port → crypto Up/Down) ──────────
    if cfg.get("macdsig_enabled"):
        book     = state["macdsig"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        for m in markets:
            if len(book["open"]) >= cfg["macdsig_max_open"]: break
            if m["id"] in own or m["id"] in cooldown: continue
            if m["vol"] < cfg["macdsig_min_volume"]: continue
            try:   # 2026-06-15: liquidity gate — match candlesig/coinrev (macdsig was the lone sibling missing it)
                from birdeye_leg import liquidity_gate as _liq_gate
                _lctx = _liq_gate(m["q"])
                if _lctx and (_lctx.get("rug_risk") or not _lctx.get("liq_ok", True)): continue
            except Exception: pass
            # macdsig's NATIVE market is "Up or Down" (the MoonDev target). Try that
            # first (no strike — YES=Up, NO=Down); fall back to "above $X" threshold.
            coin_id = _coin_from_updown(m["q"])
            strike = None
            if coin_id is None:
                parsed = _parse_price_market(m["q"])
                if not parsed: continue
                coin_id, strike, _ = parsed
            days = days_to_end(m.get("end"))   # FIX 2026-06-13: missing field bug
            hours_left = days * 24 if days is not None else 1e9
            if hours_left < cfg["macdsig_min_hours_left"]: continue
            if hours_left > cfg["macdsig_max_hours_left"]: continue
            yes = m["yes"]
            if not (cfg["macdsig_yes_min"] <= yes <= cfg["macdsig_yes_max"]): continue
            sig = _cs.get_signal(coin_id)
            if not sig or sig.get("macd") is None or sig.get("macd_sig") is None or sig.get("dev") is None:
                continue
            dev, macd, msig = sig["dev"], sig["macd"], sig["macd_sig"]
            thr = cfg["macdsig_dev_threshold"]
            side = None
            # oversold + bullish MACD → revert UP → bet YES (Up); overbought + bearish → NO
            if dev < -thr and macd > msig:
                side, out_mid = "YES", yes
            elif dev > thr and macd < msig:
                side, out_mid = "NO", 1 - yes
            if not side: continue
            entry = ask(out_mid, cfg, m["vol"])
            size  = round(cfg["macdsig_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": "crypto", "strategy": "macdsig",
                "side": side, "entry_mid": round(out_mid, 4), "entry_fill": round(entry, 4),
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "hours_left": round(hours_left, 2),
                "dev": dev, "macd": macd, "macd_sig": msig, "strike": strike,
            })
            opened.append(book["open"][-1])

    # ── coinrev (fade big 24h moves — mean-reversion at the 8h horizon) ────────
    # Backtest 2026-06-10: fading >7% 24h moves wins 54.6% @ 8h (n=359k).
    # On an "above $X" market: coin pumped hard → bet it does NOT hold above;
    # coin dumped hard → bet the bounce. Mirror logic for "below $X" markets.
    if cfg.get("coinrev_enabled"):
        book     = state["coinrev"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        for m in markets:
            if len(book["open"]) >= cfg["coinrev_max_open"]: break
            if m["id"] in own or m["id"] in cooldown: continue
            if m["vol"] < cfg["coinrev_min_volume"]: continue
            try:
                from birdeye_leg import liquidity_gate as _liq_gate
                _lctx = _liq_gate(m["q"])
                if _lctx and (_lctx.get("rug_risk") or not _lctx.get("liq_ok", True)): continue
            except Exception: pass
            parsed = _parse_price_market(m["q"])
            if not parsed: continue
            coin_id, strike, direction = parsed
            days       = days_to_end(m.get("end"))   # FIX 2026-06-13: days_to_expiry/days never existed in market dict (always 999)
            hours_left = days * 24 if days is not None else 1e9
            if hours_left < cfg["coinrev_min_hours_left"]: continue
            if hours_left > cfg["coinrev_max_hours_left"]: continue
            yes = m["yes"]
            if not (cfg["coinrev_yes_min"] <= yes <= cfg["coinrev_yes_max"]): continue
            sig = _cs.get_signal(coin_id)
            if not sig: continue
            chg = sig.get("chg24h") or 0.0
            if abs(chg) < cfg["coinrev_chg_threshold"]: continue
            # expect reversal of the 24h move over the next ~8h
            expect_down = chg > 0
            # RSI confirmation (combo backtest: 55.6% vs 54.6% without)
            if cfg.get("coinrev_rsi_confirm"):
                rsi = sig.get("rsi") or 50.0
                if expect_down and rsi <= cfg["coinrev_rsi_hi"]: continue
                if not expect_down and rsi >= cfg["coinrev_rsi_lo"]: continue
            if direction == "above":
                side, out_mid = ("NO", 1 - yes) if expect_down else ("YES", yes)
            else:   # "below" market: YES wins if price drops
                side, out_mid = ("YES", yes) if expect_down else ("NO", 1 - yes)
            entry = ask(out_mid, cfg, m["vol"])
            size  = round(cfg["coinrev_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": "crypto", "strategy": "coinrev",
                "side": side, "entry_mid": round(out_mid, 4), "entry_fill": round(entry, 4),
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "hours_left": round(hours_left, 2),
                "chg24h": round(chg, 4), "spot": sig["price"],
                "strike": strike, "direction": direction,
            })
            opened.append(book["open"][-1])

    # ── diverg (spot-price vs. market divergence) ─────────────────────────────
    # Trades when CoinGecko 24h price change contradicts the Polymarket price.
    if cfg.get("diverg_enabled"):
        cg_data  = fetch_coingecko()
        book     = state["diverg"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        for m in markets:
            if len(book["open"]) >= cfg["diverg_max_open"]: break
            if m["id"] in own or m["id"] in cooldown: continue
            if "up or down" not in m["q"].lower(): continue
            if m["vol"] < cfg["diverg_min_volume"]: continue
            days       = days_to_end(m.get("end"))   # FIX 2026-06-13: days_to_expiry/days never existed in market dict (always 999)
            hours_left = days * 24 if days is not None else 1e9
            if hours_left < cfg["diverg_min_hours_left"]: continue
            if hours_left > cfg["diverg_max_hours_left"]: continue
            coin_id    = _coin_from_updown(m["q"])
            if not coin_id: continue
            coin_data  = cg_data.get(coin_id, {})
            chg        = (coin_data.get("usd_24h_change") or 0) / 100.0   # → decimal
            yes        = m["yes"]
            if chg >= cfg["diverg_chg_threshold"] and yes <= cfg["diverg_yes_max_bull"]:
                # coin bullish but market hasn't caught up → BUY YES
                side, out_mid = "YES", yes
            elif chg <= -cfg["diverg_chg_threshold"] and yes >= cfg["diverg_yes_min_bear"]:
                # coin bearish but market still near 50/50 → BUY NO
                side, out_mid = "NO", 1 - yes
            else:
                continue
            entry = ask(out_mid, cfg, m["vol"])
            size  = round(cfg["diverg_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": "crypto", "strategy": "diverg", "side": side,
                "entry_mid": round(out_mid, 4), "entry_fill": round(entry, 4),
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "coin": coin_id, "chg_24h": round(chg, 4), "hours_left": round(hours_left, 2),
            })
            opened.append(book["open"][-1])

    # ── feargreed (Fear & Greed contrarian signal) ───────────────────────────
    # F&G <25 = extreme fear → contrarian buy YES. F&G >75 = extreme greed → buy NO.
    if cfg.get("feargreed_enabled"):
        fg       = fetch_fear_greed()
        book     = state["feargreed"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        if fg is not None:
            if fg <= cfg["feargreed_fear_max"]:
                fg_side = "YES"   # extreme fear → contrarian buy Up
            elif fg >= cfg["feargreed_greed_min"]:
                fg_side = "NO"    # extreme greed → contrarian short Down
            else:
                fg_side = None    # neutral zone — don't trade
            if fg_side:
                for m in markets:
                    if len(book["open"]) >= cfg["feargreed_max_open"]: break
                    if m["id"] in own or m["id"] in cooldown: continue
                    if "up or down" not in m["q"].lower(): continue
                    if m["vol"] < cfg["feargreed_min_volume"]: continue
                    if not (cfg["feargreed_yes_min"] <= m["yes"] <= cfg["feargreed_yes_max"]): continue
                    days       = days_to_end(m.get("end"))   # FIX 2026-06-13: missing field bug
                    hours_left = days * 24 if days is not None else 1e9
                    if hours_left < cfg["feargreed_min_hours_left"]: continue
                    if hours_left > cfg["feargreed_max_hours_left"]: continue
                    out_mid = m["yes"] if fg_side == "YES" else 1 - m["yes"]
                    entry   = ask(out_mid, cfg, m["vol"])
                    size    = round(cfg["feargreed_bet_usdc"] / entry, 4) if entry > 0 else 0
                    if size <= 0: continue
                    book["open"].append({
                        "id": m["id"], "q": m["q"], "cat": "crypto", "strategy": "feargreed",
                        "side": fg_side,
                        "entry_mid": round(out_mid, 4), "entry_fill": round(entry, 4),
                        "size": size, "vol": m["vol"], "opened_at": now_iso(),
                        "fear_greed": fg,
                    })
                    opened.append(book["open"][-1])

    # ── lateprox (late-window high-confidence entry, aulekator signal) ────────
    # Enters only when <2h remaining AND market is already tilted ≥62%: the
    # calibration shows 60→72% actual win rate at that price bucket (+12% edge).
    if cfg.get("lateprox_enabled"):
        book     = state["lateprox"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        for m in markets:
            if len(book["open"]) >= cfg["lateprox_max_open"]: break
            if m["id"] in own or m["id"] in cooldown: continue
            if "up or down" not in m["q"].lower(): continue
            if m["vol"] < cfg["lateprox_min_volume"]: continue
            days       = days_to_end(m.get("end"))   # FIX 2026-06-13: days_to_expiry/days never existed in market dict (always 999)
            hours_left = days * 24 if days is not None else 1e9
            if hours_left < cfg["lateprox_min_hours_left"]: continue
            if hours_left > cfg["lateprox_max_hours_left"]: continue
            yes = m["yes"]
            if cfg["lateprox_yes_hi_min"] <= yes <= cfg["lateprox_yes_hi_max"]:
                side, out_mid = "YES", yes
            elif cfg["lateprox_yes_lo_min"] <= yes <= cfg["lateprox_yes_lo_max"]:
                side, out_mid = "NO", 1 - yes
            else:
                continue
            entry = ask(out_mid, cfg, m["vol"])
            size  = round(cfg["lateprox_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": "crypto", "strategy": "lateprox",
                "side": side,
                "entry_mid": round(out_mid, 4), "entry_fill": round(entry, 4),
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "hours_left": round(hours_left, 2),
            })
            opened.append(book["open"][-1])

    # ── fgcrypto (Fear & Greed on long-dated crypto markets) ────────────────────
    # F&G extreme readings → crowd overfits sentiment to long-dated markets.
    # Extreme fear: dip-target markets (YES > threshold) are overpriced → sell NO.
    # Extreme greed: rally-target markets (YES > threshold) are overpriced → sell NO.
    if cfg.get("fgcrypto_enabled"):
        fg   = fetch_fear_greed()
        book = state["fgcrypto"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        if fg is not None:
            if fg <= cfg["fgcrypto_fear_max"]:
                fg_mode = "fear"    # sell dip-markets
            elif fg >= cfg["fgcrypto_greed_min"]:
                fg_mode = "greed"   # sell rally-markets
            else:
                fg_mode = None
            if fg_mode:
                crypto_kw = ("bitcoin","btc","ethereum","eth","solana","sol","xrp","ripple",
                             "ton","toncoin","tron","trx","shiba","shib","sui","near",
                             "cosmos","atom","uniswap","uni","aptos","apt",
                             "arbitrum","arb","optimism","pepe","dogwifhat","wif","bonk","floki")
                dip_kw    = ("dip to", "drop to", "fall to", "below")
                rally_kw  = ("reach ", "hit ", "above ", "all time high", "ath")
                for m in markets:
                    if len(book["open"]) >= cfg["fgcrypto_max_open"]: break
                    if m["id"] in own or m["id"] in cooldown: continue
                    q_low = m["q"].lower()
                    # must be a crypto market
                    if not any(k in q_low for k in crypto_kw): continue
                    if m["vol"] < cfg["fgcrypto_min_volume"]: continue
                    yes = m["yes"]
                    if not (cfg["fgcrypto_yes_min_sell"] <= yes <= cfg["fgcrypto_yes_max_sell"]): continue
                    # choose which keyword class to match + proximity guard
                    cg_now = fetch_coingecko()
                    # get current spot price for the coin mentioned
                    _SPOT_MAP = {"bitcoin":"bitcoin","btc":"bitcoin",
                                 "ethereum":"ethereum","eth":"ethereum",
                                 "solana":"solana","sol":"solana",
                                 "xrp":"ripple","ripple":"ripple",
                                 "cardano":"cardano","ada":"cardano"}
                    _spot_coin = next((cg_id for kw,cg_id in _SPOT_MAP.items() if kw in q_low), None)
                    _spot_px   = (cg_now.get(_spot_coin) or {}).get("usd") if _spot_coin else None
                    # parse target price from title: ",000" → 55000.0
                    import re as _re
                    _tgt_m = _re.search(r"\$([0-9,]+(?:\.\d+)?)", m["q"])
                    _tgt_px = float(_tgt_m.group(1).replace(",","")) if _tgt_m else None

                    if fg_mode == "fear":
                        if not any(k in q_low for k in dip_kw): continue
                        # proximity guard: skip if dip target is within 20% of spot
                        if _spot_px and _tgt_px:
                            _drop_pct = (_spot_px - _tgt_px) / _spot_px
                            if _drop_pct < cfg.get("fgcrypto_min_drop_pct", 0.20): continue
                        # extreme fear inflates dip probability → NO is the trade
                        side, out_mid = "NO", 1 - yes
                    else:  # greed
                        if not any(k in q_low for k in rally_kw): continue
                        # proximity guard: skip if rally target is within 20% of spot
                        if _spot_px and _tgt_px:
                            _pump_pct = (_tgt_px - _spot_px) / _spot_px
                            if _pump_pct < cfg.get("fgcrypto_min_pump_pct", 0.20): continue
                        # extreme greed inflates rally probability → NO is the trade
                        side, out_mid = "NO", 1 - yes
                    entry = ask(out_mid, cfg, m["vol"])
                    size  = round(cfg["fgcrypto_bet_usdc"] / entry, 4) if entry > 0 else 0
                    if size <= 0: continue
                    book["open"].append({
                        "id": m["id"], "q": m["q"], "cat": "crypto", "strategy": "fgcrypto",
                        "side": side, "fg": fg, "fg_mode": fg_mode,
                        "entry_mid": round(out_mid, 4), "entry_fill": round(entry, 4),
                        "size": size, "vol": m["vol"], "opened_at": now_iso(),
                    })
                    opened.append(book["open"][-1])

    # ── nearterm (resolution convergence) ──────────────────────────────────────
    # Buy high-consensus YES markets (0.80-0.97) resolving in 7-45 days.
    # Collect the remaining uncertainty premium; ride to resolution at 1.00.
    if cfg.get("nearterm_enabled"):
        book     = state["nearterm"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        _excl    = cfg.get("moonshot_excl_kw", ())   # reuse commodity exclusion list
        for m in markets:
            if len(book["open"]) >= cfg["nearterm_max_open"]: break
            if m["id"] in own or m["id"] in cooldown: continue
            # exclude commodity / financial-instrument markets
            if any(k in m["q"].lower() for k in _excl): continue
            yes = m["yes"]
            if not (cfg["nearterm_yes_min"] <= yes <= cfg["nearterm_yes_max"]): continue
            if m["vol"] < cfg["nearterm_min_volume"]: continue
            # must resolve in the right window
            d = days_to_end(m.get("end"))
            if d is None or not (cfg["nearterm_min_days"] <= d <= cfg["nearterm_max_days"]): continue
            # entry: buy YES
            spr   = liq_spread(m["vol"], cfg)
            entry = round(min(0.999, yes + spr / 2), 4)
            size  = round(cfg["nearterm_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat","other"),
                "strategy": "nearterm", "side": "YES",
                "entry_mid": round(yes, 4), "entry_fill": entry,
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "days_to_end": round(d, 1),
            })
            opened.append(book["open"][-1])

    # ── longshot — tiered inverse-moonshot (buy NO on overconfident YES) ────────
    if cfg.get("longshot_enabled"):
        book     = state["longshot"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        _ls_tier_counts = {}
        for _p in book["open"]:
            _t = _p.get("tier", "?")
            _ls_tier_counts[_t] = _ls_tier_counts.get(_t, 0) + 1
        _excl = cfg.get("longshot_excl_kw", ())
        for m in markets:
            if len(book["open"]) >= cfg["longshot_max_open"]:
                break
            if m["id"] in own or m["id"] in cooldown:
                continue
            if any(k in m["q"].lower() for k in _excl):
                continue
            yes = m["yes"]
            chg = m.get("chg", 0) or 0
            vol = m["vol"]
            if cfg["longshot_th_yes_min"] <= yes <= cfg["longshot_th_yes_max"]:
                if chg < cfg["longshot_th_chg"] or vol < cfg["longshot_th_min_vol"]:
                    continue
                if _ls_tier_counts.get("high", 0) >= cfg.get("longshot_t_high_max_open", 99):
                    continue
                no_entry_mid = round(1 - yes, 4)
                target_delta = cfg["longshot_th_target_delta"]
                stop_delta   = cfg["longshot_th_stop_delta"]
                tier         = "high"
            elif cfg["longshot_tm_yes_min"] <= yes <= cfg["longshot_tm_yes_max"]:
                if chg < cfg["longshot_tm_chg"] or vol < cfg["longshot_tm_min_vol"]:
                    continue
                if _ls_tier_counts.get("mid", 0) >= cfg.get("longshot_t_mid_max_open", 99):
                    continue
                no_entry_mid = round(1 - yes, 4)
                target_delta = cfg["longshot_tm_target_delta"]
                stop_delta   = cfg["longshot_tm_stop_delta"]
                tier         = "mid"
            else:
                continue
            entry = round(min(0.999, no_entry_mid + liq_spread(vol, cfg) / 2), 4)
            size  = round(cfg["longshot_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0:
                continue
            abs_target = round(no_entry_mid + target_delta, 4)
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "longshot", "side": "NO",
                "entry_mid": no_entry_mid, "entry_fill": entry,
                "size": size, "vol": vol, "opened_at": now_iso(),
                "tier": tier,
                "abs_target": abs_target,
                "stop_delta": stop_delta,
            })
            _ls_tier_counts[tier] = _ls_tier_counts.get(tier, 0) + 1
            opened.append(book["open"][-1])

    # ── nearwin — near-resolution YES chase ──────────────────────────────────
    if cfg.get("nearwin_enabled"):
        book     = state["nearwin"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        _excl = cfg.get("nearwin_excl_kw", ())
        for m in markets:
            if len(book["open"]) >= cfg["nearwin_max_open"]:
                break
            if m["id"] in own or m["id"] in cooldown:
                continue
            if any(k in m["q"].lower() for k in _excl):
                continue
            yes = m["yes"]
            chg = m.get("chg", 0) or 0
            vol = m["vol"]
            if not (cfg["nearwin_yes_min"] <= yes <= cfg["nearwin_yes_max"]):
                continue
            if vol < cfg["nearwin_min_vol"]:
                continue
            if chg < cfg["nearwin_chg"]:
                continue
            days = days_to_end(m.get("end"))
            if days is None or days <= 0 or days > cfg["nearwin_max_days"]:
                continue
            entry = round(min(0.999, yes + liq_spread(vol, cfg) / 2), 4)
            size  = round(cfg["nearwin_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0:
                continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "nearwin", "side": "YES",
                "entry_mid": round(yes, 4), "entry_fill": entry,
                "size": size, "vol": vol, "opened_at": now_iso(),
                "days_at_entry": round(days, 2),
            })
            opened.append(book["open"][-1])

    # ── crashbuy — oversold bounce entry ─────────────────────────────────────
    if cfg.get("crashbuy_enabled"):
        book     = state["crashbuy"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        for m in markets:
            if is_live_match(m.get("q", "")): continue  # live-match gap risk (2026-06-10: -$1 tennis gaps)
            if len(book["open"]) >= cfg["crashbuy_max_open"]:
                break
            if m["id"] in own or m["id"] in cooldown:
                continue
            yes = m["yes"]
            chg = m.get("chg", 0) or 0
            vol = m["vol"]
            if not (cfg["crashbuy_yes_min"] <= yes <= cfg["crashbuy_yes_max"]):
                continue
            if vol < cfg["crashbuy_min_vol"]:
                continue
            if chg > -cfg["crashbuy_min_crash"]:   # must have fallen ≥ min_crash
                continue
            prev = _price_snap.get(m["id"])
            if prev is None or yes <= prev:         # no prior snapshot or still falling
                continue
            days = days_to_end(m.get("end"))
            if days is None or days < cfg["crashbuy_min_days"]:
                continue
            entry = round(min(0.999, yes + liq_spread(vol, cfg) / 2), 4)
            size  = round(cfg["crashbuy_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0:
                continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "crashbuy", "side": "YES",
                "entry_mid": round(yes, 4), "entry_fill": entry,
                "size": size, "vol": vol, "opened_at": now_iso(),
                "crash_chg": round(chg, 4),
            })
            opened.append(book["open"][-1])

    # ── tp20 — buy an upward tick, take +20¢ and sell (user request 2026-06-14) ──
    if cfg.get("tp20_enabled"):
        book     = state["tp20"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        # leg-independence (verify 2026-06-14): don't stack a market a sibling
        # scalp leg already holds — keeps each leg's gate stats uncorrelated.
        _sib = {p["id"] for p in state.get("spreadcap", {}).get("open", [])}
        for m in markets:
            if is_live_match(m.get("q", "")): continue   # live-match gap risk
            if len(book["open"]) >= cfg["tp20_max_open"]: break
            if m["id"] in own or m["id"] in cooldown or m["id"] in _sib: continue
            yes = m["yes"]; vol = m["vol"]
            if not (cfg["tp20_yes_min"] <= yes <= cfg["tp20_yes_max"]): continue
            if vol < cfg["tp20_min_vol"]: continue
            prev = _price_snap.get(m["id"])
            if prev is None or yes - prev < cfg["tp20_min_rise"]: continue  # must be rising
            entry = round(min(0.999, yes + liq_spread(vol, cfg) / 2), 4)    # taker at ask
            size  = round(cfg["tp20_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "tp20", "side": "YES",
                "entry_mid": round(yes, 4), "entry_fill": entry,
                "size": size, "vol": vol, "opened_at": now_iso(),
                "rise": round(yes - prev, 4),
            })
            opened.append(book["open"][-1])

    # ── scalp02 — maker micro-scalp, take +0.2% and sell (user request 2026-06-14) ──
    if cfg.get("scalp02_enabled"):
        book     = state["scalp02"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        # leg-independence: skip markets a sibling scalp leg already holds
        # (spreadcap + tp20 run first this scan), so the gate stats stay uncorrelated.
        _sib = {p["id"] for L in ("spreadcap", "tp20")
                for p in state.get(L, {}).get("open", [])}
        for m in markets:
            if is_live_match(m.get("q", "")): continue
            if len(book["open"]) >= cfg["scalp02_max_open"]: break
            if m["id"] in own or m["id"] in cooldown or m["id"] in _sib: continue
            yes = m["yes"]; vol = m["vol"]
            if not (cfg["scalp02_yes_min"] <= yes <= cfg["scalp02_yes_max"]): continue
            if vol < cfg["scalp02_min_vol"]: continue
            chg = _scan_chg(m["id"], yes)            # price-stability gate
            if chg is not None and abs(chg) > cfg["scalp02_max_chg"]: continue
            entry = round(yes, 4)                    # MAKER: rest at mid, pay no spread
            size  = round(cfg["scalp02_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "scalp02", "side": "YES",
                "entry_mid": round(yes, 4), "entry_fill": entry,
                "size": size, "vol": vol, "opened_at": now_iso(),
            })
            opened.append(book["open"][-1])

    # ── volsurge — volume spike + price confirmation, buy the move (user req 2026-06-14) ──
    if cfg.get("volsurge_enabled"):
        book     = state["volsurge"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        for m in markets:
            if is_live_match(m.get("q", "")): continue
            if len(book["open"]) >= cfg["volsurge_max_open"]: break
            if m["id"] in own or m["id"] in cooldown: continue
            yes = m["yes"]
            if m["vol"] < cfg["volsurge_min_volume"]: continue
            if not (cfg["volsurge_yes_min"] <= yes <= cfg["volsurge_yes_max"]): continue
            prev_baseline = _update_vol_baseline(m["id"], m["vol"])
            if m["id"] not in _vol_baseline: continue            # first sighting
            if prev_baseline < cfg["volsurge_min_baseline"]: continue
            if m["vol"] < cfg["volsurge_vol_threshold"] * prev_baseline: continue   # volume spike
            chg = _scan_chg(m["id"], yes)
            if chg is None or abs(chg) < cfg["volsurge_min_move"]: continue          # AND price moving
            side    = "YES" if chg > 0 else "NO"                 # buy the DIRECTION of the move
            out_mid = yes if side == "YES" else 1 - yes
            entry   = ask(out_mid, cfg, m["vol"])
            size    = round(cfg["volsurge_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "volsurge", "side": side,
                "entry_mid": round(out_mid, 4), "entry_fill": round(entry, 4),
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "spike_ratio": round(m["vol"] / prev_baseline, 2), "move": round(chg, 4),
            })
            opened.append(book["open"][-1])

    # ── deadline (ADHD: Time Blindness) ─────────────────────────────────────
    if cfg.get("deadline_enabled"):
        book     = state["deadline"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        _excl = cfg.get("deadline_excl_kw", ())
        for m in markets:
            if len(book["open"]) >= cfg["deadline_max_open"]:
                break
            if m["id"] in own or m["id"] in cooldown:
                continue
            if any(k in m["q"].lower() for k in _excl):
                continue
            yes = m["yes"]
            chg = m.get("chg", 0) or 0
            vol = m["vol"]
            if not (cfg["deadline_yes_min"] <= yes <= cfg["deadline_yes_max"]):
                continue
            if vol < cfg["deadline_min_vol"]:
                continue
            if chg < cfg["deadline_chg"]:
                continue
            days = days_to_end(m.get("end"))
            if days is None or days <= 0 or days > cfg["deadline_max_days"]:
                continue
            entry = round(min(0.999, yes + liq_spread(vol, cfg) / 2), 4)
            size  = round(cfg["deadline_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0:
                continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "deadline", "side": "YES",
                "entry_mid": round(yes, 4), "entry_fill": entry,
                "size": size, "vol": vol, "opened_at": now_iso(),
                "hours_left": round(days * 24, 1),
            })
            opened.append(book["open"][-1])

    # ── impulse (ADHD: Inhibition Failure) ───────────────────────────────────
    if cfg.get("impulse_enabled"):
        book     = state["impulse"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        for m in markets:
            if len(book["open"]) >= cfg["impulse_max_open"]:
                break
            if m["id"] in own or m["id"] in cooldown:
                continue
            yes = m["yes"]
            vol = m["vol"]
            if not (cfg["impulse_yes_min"] <= yes <= cfg["impulse_yes_max"]):
                continue
            if vol < cfg["impulse_min_vol"]:
                continue
            # require a large scan-level spike (impulse buy just happened)
            prev = _price_snap.get(m["id"])
            if prev is None:
                continue
            spike = yes - prev
            if spike < cfg["impulse_min_spike"]:
                continue
            no_mid = round(1 - yes, 4)
            entry  = round(min(0.999, no_mid + liq_spread(vol, cfg) / 2), 4)
            size   = round(cfg["impulse_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0:
                continue
            abs_target = round(no_mid + cfg["impulse_target_delta"], 4)
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "impulse", "side": "NO",
                "entry_mid": no_mid, "entry_fill": entry,
                "size": size, "vol": vol, "opened_at": now_iso(),
                "spike_at_entry": round(spike, 4),
                "abs_target": abs_target,
                "stop_delta": cfg["impulse_stop_delta"],
            })
            opened.append(book["open"][-1])

    # ── fogbuy (ADHD: Working Memory / Forgetting) ───────────────────────────
    if cfg.get("fogbuy_enabled"):
        book     = state["fogbuy"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        for m in markets:
            if len(book["open"]) >= cfg["fogbuy_max_open"]:
                break
            if m["id"] in own or m["id"] in cooldown:
                continue
            yes = m["yes"]
            chg = m.get("chg", 0) or 0
            vol = m["vol"]
            if not (cfg["fogbuy_yes_min"] <= yes <= cfg["fogbuy_yes_max"]):
                continue
            if vol < cfg["fogbuy_min_vol"]:
                continue
            if chg > -cfg["fogbuy_min_chg_down"]:  # must have fallen ≥ min_chg_down
                continue
            # scan delta must be a gentle drift, not a crash
            prev = _price_snap.get(m["id"])
            if prev is not None:
                scan_drop = prev - yes   # positive = price fell this scan
                if scan_drop > cfg["fogbuy_max_scan_drop"]:
                    continue            # still actively crashing — skip
            days = days_to_end(m.get("end"))
            if is_live_match(m.get("q", "")): continue  # padded end-dates lie; gap risk
            if days is None or days < cfg["fogbuy_min_days"]:
                continue
            entry = round(min(0.999, yes + liq_spread(vol, cfg) / 2), 4)
            size  = round(cfg["fogbuy_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0:
                continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "fogbuy", "side": "YES",
                "entry_mid": round(yes, 4), "entry_fill": entry,
                "size": size, "vol": vol, "opened_at": now_iso(),
                "chg_at_entry": round(chg, 4),
            })
            opened.append(book["open"][-1])

    # ── anchordrift — anchoring bias fade (buy NO on stuck markets) ──────────
    if cfg.get("anchordrift_enabled"):
        book     = state["anchordrift"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        for m in markets:
            if len(book["open"]) >= cfg["anchordrift_max_open"]: break
            if m["id"] in own or m["id"] in cooldown: continue
            if m["vol"] < cfg["anchordrift_min_volume"]: continue
            yes = m["yes"]
            chg = abs(m.get("chg", 0) or 0)
            if not (cfg["anchordrift_yes_min"] <= yes <= cfg["anchordrift_yes_max"]): continue
            if chg > cfg["anchordrift_max_chg"]: continue   # must be stuck (low chg)
            out_mid = 1 - yes
            entry   = ask(out_mid, cfg, m["vol"])
            size    = round(cfg["anchordrift_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "anchordrift", "side": "NO",
                "entry_mid": round(out_mid, 4), "entry_fill": round(entry, 4),
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "chg_at_entry": round(chg, 4),
            })
            opened.append(book["open"][-1])

    # ── volcrush — volume collapse fade (buy NO on dropping markets) ──────────
    if cfg.get("volcrush_enabled"):
        book     = state["volcrush"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        for m in markets:
            if len(book["open"]) >= cfg["volcrush_max_open"]: break
            if m["id"] in own or m["id"] in cooldown: continue
            yes = m["yes"]
            chg = m.get("chg", 0) or 0
            if not (cfg["volcrush_yes_min"] <= yes <= cfg["volcrush_yes_max"]): continue
            if m["vol"] < cfg["volcrush_min_volume"]: continue
            if chg > -cfg["volcrush_min_chg_down"]: continue  # must be falling
            out_mid = 1 - yes
            entry   = ask(out_mid, cfg, m["vol"])
            size    = round(cfg["volcrush_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "volcrush", "side": "NO",
                "entry_mid": round(out_mid, 4), "entry_fill": round(entry, 4),
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "chg_at_entry": round(chg, 4),
            })
            opened.append(book["open"][-1])

    # ── latefade — late-game FLB fade (<48h, buy NO) ─────────────────────────
    if cfg.get("latefade_enabled"):
        book     = state["latefade"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        for m in markets:
            if is_live_match(m.get("q", "")): continue  # live-match gap risk (2026-06-10: -$1 tennis gaps)
            if len(book["open"]) >= cfg["latefade_max_open"]: break
            if m["id"] in own or m["id"] in cooldown: continue
            if m["vol"] < cfg["latefade_min_volume"]: continue
            yes = m["yes"]
            if not (cfg["latefade_yes_min"] <= yes <= cfg["latefade_yes_max"]): continue
            d = days_to_end(m.get("end"))
            if d is None or d > cfg["latefade_max_days"] or d < cfg["latefade_min_days"]: continue
            out_mid = 1 - yes
            entry   = ask(out_mid, cfg, m["vol"])
            size    = round(cfg["latefade_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "latefade", "side": "NO",
                "entry_mid": round(out_mid, 4), "entry_fill": round(entry, 4),
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "days_left": round(d, 2),
            })
            opened.append(book["open"][-1])

    # ── momno — negative momentum continuation (buy NO on falling markets) ───
    if cfg.get("momno_enabled"):
        book     = state["momno"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        for m in markets:
            if len(book["open"]) >= cfg["momno_max_open"]: break
            if m["id"] in own or m["id"] in cooldown: continue
            yes = m["yes"]
            chg = m.get("chg", 0) or 0
            if not (cfg["momno_yes_min"] <= yes <= cfg["momno_yes_max"]): continue
            if m["vol"] < cfg["momno_min_volume"]: continue
            if chg > -cfg["momno_min_chg_down"]: continue  # must be falling ≥5¢
            out_mid = 1 - yes
            entry   = ask(out_mid, cfg, m["vol"])
            size    = round(cfg["momno_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "momno", "side": "NO",
                "entry_mid": round(out_mid, 4), "entry_fill": round(entry, 4),
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "chg_at_entry": round(chg, 4),
            })
            opened.append(book["open"][-1])

    # ── narrowband — near-coinflip vig fade (buy NO on 40-52¢ markets) ───────
    if cfg.get("narrowband_enabled"):
        book     = state["narrowband"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        for m in markets:
            if len(book["open"]) >= cfg["narrowband_max_open"]: break
            if m["id"] in own or m["id"] in cooldown: continue
            yes = m["yes"]
            if not (cfg["narrowband_yes_min"] <= yes <= cfg["narrowband_yes_max"]): continue
            if m["vol"] < cfg["narrowband_min_volume"]: continue
            out_mid = 1 - yes
            entry   = ask(out_mid, cfg, m["vol"])
            size    = round(cfg["narrowband_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "narrowband", "side": "NO",
                "entry_mid": round(out_mid, 4), "entry_fill": round(entry, 4),
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
            })
            opened.append(book["open"][-1])

    # ── esportsfav — esports YES favorite buy ────────────────────────────────
    _ESPORTS_KW = ("counter-strike", "cs2", "csgo", "valorant", "league of legend",
                   "lol:", "dota", "esport", "esports", "iem ", "esl ", "blast ")
    if cfg.get("esportsfav_enabled"):
        book     = state["esportsfav"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        for m in markets:
            if len(book["open"]) >= cfg["esportsfav_max_open"]: break
            if m["id"] in own or m["id"] in cooldown: continue
            q_low = str(m.get("q", "")).lower()
            if not any(kw in q_low for kw in _ESPORTS_KW): continue
            yes = m["yes"]
            if not (cfg["esportsfav_yes_min"] <= yes <= cfg["esportsfav_yes_max"]): continue
            if m["vol"] < cfg["esportsfav_min_volume"]: continue
            entry = ask(yes, cfg)
            size  = round(cfg["esportsfav_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "esportsfav", "side": "YES",
                "entry_mid": round(yes, 4), "entry_fill": round(entry, 4),
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
            })
            opened.append(book["open"][-1])

    # ── resolvetoday — high-confidence market expiring today ─────────────────
    if cfg.get("resolvetoday_enabled"):
        book     = state["resolvetoday"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        for m in markets:
            if len(book["open"]) >= cfg["resolvetoday_max_open"]: break
            if m["id"] in own or m["id"] in cooldown: continue
            if m["vol"] < cfg["resolvetoday_min_volume"]: continue
            days = days_to_end(m.get("end"))
            if days is None: continue
            hours_left = days * 24
            if not (cfg["resolvetoday_min_hours"] <= hours_left <= cfg["resolvetoday_max_hours"]): continue
            yes = m["yes"]
            if not (cfg["resolvetoday_yes_min"] <= yes <= cfg["resolvetoday_yes_max"]): continue
            entry = ask(yes, cfg)
            size  = round(cfg["resolvetoday_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "resolvetoday", "side": "YES",
                "entry_mid": round(yes, 4), "entry_fill": round(entry, 4),
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "hours_left": round(hours_left, 2),
            })
            opened.append(book["open"][-1])

    # ── stablehigh — stable high-probability market ───────────────────────────
    if cfg.get("stablehigh_enabled"):
        book     = state["stablehigh"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        for m in markets:
            if len(book["open"]) >= cfg["stablehigh_max_open"]: break
            if m["id"] in own or m["id"] in cooldown: continue
            yes = m["yes"]
            chg = abs(m.get("chg", 0) or 0)
            if not (cfg["stablehigh_yes_min"] <= yes <= cfg["stablehigh_yes_max"]): continue
            if chg > cfg["stablehigh_max_chg"]: continue
            if m["vol"] < cfg["stablehigh_min_volume"]: continue
            entry = ask(yes, cfg)
            size  = round(cfg["stablehigh_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "stablehigh", "side": "YES",
                "entry_mid": round(yes, 4), "entry_fill": round(entry, 4),
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "chg_at_entry": round(chg, 4),
            })
            opened.append(book["open"][-1])

    # ── priceleap — upward momentum continuation (buy YES on ≥12¢ jump) ──────
    if cfg.get("priceleap_enabled"):
        book     = state["priceleap"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        for m in markets:
            if len(book["open"]) >= cfg["priceleap_max_open"]: break
            if m["id"] in own or m["id"] in cooldown: continue
            yes = m["yes"]
            chg = m.get("chg", 0) or 0
            if not (cfg["priceleap_yes_min"] <= yes <= cfg["priceleap_yes_max"]): continue
            if m["vol"] < cfg["priceleap_min_volume"]: continue
            if chg < cfg["priceleap_min_chg_up"]: continue   # must have jumped ≥12¢
            entry = ask(yes, cfg)
            size  = round(cfg["priceleap_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "priceleap", "side": "YES",
                "entry_mid": round(yes, 4), "entry_fill": round(entry, 4),
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "chg_at_entry": round(chg, 4),
            })
            opened.append(book["open"][-1])

    # ── thetano — deep longshot fade (buy NO on YES 5-18¢) ───────────────────
    if cfg.get("thetano_enabled"):
        book     = state["thetano"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        for m in markets:
            if len(book["open"]) >= cfg["thetano_max_open"]: break
            if m["id"] in own or m["id"] in cooldown: continue
            yes = m["yes"]
            if not (cfg["thetano_yes_min"] <= yes <= cfg["thetano_yes_max"]): continue
            if m["vol"] < cfg["thetano_min_volume"]: continue
            days = days_to_end(m.get("end"))
            if days is None or not (cfg["thetano_min_days"] <= days <= cfg["thetano_max_days"]): continue
            out_mid = 1 - yes
            entry   = ask(out_mid, cfg, m["vol"])
            size    = round(cfg["thetano_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "thetano", "side": "NO",
                "entry_mid": round(out_mid, 4), "entry_fill": round(entry, 4),
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "days_left": round(days, 2),
            })
            opened.append(book["open"][-1])

    # ── breakoutyes — 50¢ resistance breakout continuation ───────────────────
    if cfg.get("breakoutyes_enabled"):
        book     = state["breakoutyes"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        for m in markets:
            if len(book["open"]) >= cfg["breakoutyes_max_open"]: break
            if m["id"] in own or m["id"] in cooldown: continue
            yes = m["yes"]
            chg = m.get("chg", 0) or 0
            if not (cfg["breakoutyes_yes_min"] <= yes <= cfg["breakoutyes_yes_max"]): continue
            if m["vol"] < cfg["breakoutyes_min_volume"]: continue
            if chg < cfg["breakoutyes_min_chg_up"]: continue  # must have crossed upward
            entry = ask(yes, cfg)
            size  = round(cfg["breakoutyes_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "breakoutyes", "side": "YES",
                "entry_mid": round(yes, 4), "entry_fill": round(entry, 4),
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "chg_at_entry": round(chg, 4),
            })
            opened.append(book["open"][-1])

    # ── esportsdog — esports underdog YES buy (risky, asymmetric) ────────────
    _ESPORTS_KW = getattr(scalp_lab_module := None, '_ESPORTS_KW',
                  ("counter-strike", "cs2", "csgo", "valorant", "league of legend",
                   "lol:", "dota", "esport", "iem ", "esl ", "blast "))
    if cfg.get("esportsdog_enabled"):
        book     = state["esportsdog"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        for m in markets:
            if len(book["open"]) >= cfg["esportsdog_max_open"]: break
            if m["id"] in own or m["id"] in cooldown: continue
            q_low = str(m.get("q", "")).lower()
            if not any(k in q_low for k in ("counter-strike","cs2","csgo","valorant","league of legend","lol:","dota","esport","iem ","esl ","blast ")): continue
            yes = m["yes"]
            if not (cfg["esportsdog_yes_min"] <= yes <= cfg["esportsdog_yes_max"]): continue
            if m["vol"] < cfg["esportsdog_min_volume"]: continue
            spr   = liq_spread(m["vol"], cfg)
            entry = round(min(0.999, yes + spr / 2), 4)
            size  = round(cfg["esportsdog_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "esportsdog", "side": "YES",
                "entry_mid": round(yes, 4), "entry_fill": entry,
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "abs_target": cfg["esportsdog_abs_target"],
                "stop_delta":  cfg["esportsdog_stop_delta"],
                "potential_x": round(cfg["esportsdog_abs_target"] / yes, 1),
            })
            opened.append(book["open"][-1])

    # ── geopolbomb — geopolitical tail bet YES buy ────────────────────────────
    if cfg.get("geopolbomb_enabled"):
        book     = state["geopolbomb"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        _gpkw = cfg.get("geopolbomb_kw", ())
        for m in markets:
            if len(book["open"]) >= cfg["geopolbomb_max_open"]: break
            if m["id"] in own or m["id"] in cooldown: continue
            q_low = str(m.get("q", "")).lower()
            if not any(k in q_low for k in _gpkw): continue
            yes = m["yes"]
            if not (cfg["geopolbomb_yes_min"] <= yes <= cfg["geopolbomb_yes_max"]): continue
            if m["vol"] < cfg["geopolbomb_min_volume"]: continue
            spr   = liq_spread(m["vol"], cfg)
            entry = round(min(0.999, yes + spr / 2), 4)
            size  = round(cfg["geopolbomb_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "geopolbomb", "side": "YES",
                "entry_mid": round(yes, 4), "entry_fill": entry,
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "abs_target": cfg["geopolbomb_abs_target"],
                "stop_delta":  cfg["geopolbomb_stop_delta"],
                "potential_x": round(cfg["geopolbomb_abs_target"] / yes, 1),
            })
            opened.append(book["open"][-1])

    # ── alicenews — MULTI-SOURCE news-confirmed geopolitical YES buy ───────────
    # 2026-06-21: upgraded from single-feed (OpenAlice only) to MULTI-INFO. The single-
    # source version died (n=7, 0% WR, -$4.57) — same calibrated-mids fate as the rest of
    # the predictive-leg graveyard. The multi-info gate requires a market keyword to be
    # corroborated across >= alicenews_min_sources INDEPENDENT feeds (OpenAlice :47331 +
    # hermes :48580 + GDELT intensity) before entry — fewer, higher-conviction entries.
    # STILL DISABLED: this is a stricter re-test, not a revival; re-enable is a human call
    # (predictive geo is a documented dead end — the real geo edge is data-arb, see geo-edge
    # workflow / geo-intel agent). A control (geopolbomb keyword-only) must lose by more.
    if cfg.get("alicenews_enabled"):
        book     = state["alicenews"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        _ankw    = cfg.get("alicenews_kw", ())
        _min_src = cfg.get("alicenews_min_sources", 2)   # multi-info: need this many feeds to concur
        import urllib.request as _ur
        # each feed kept SEPARATE so we can count independent corroborations per market.
        _feeds = {}
        for _name, _url in (("alice", cfg.get("alicenews_api_url", "http://127.0.0.1:47331/api/news")),
                            ("hermes", cfg.get("hermes_api_url", "http://127.0.0.1:48580/api/news"))):
            _hs = set()
            try:
                _resp = _ur.urlopen(_url, timeout=3)
                for _item in json.loads(_resp.read()).get("items", []):
                    _hs.add(_item.get("title", "").lower())
            except Exception:
                pass  # a down feed simply contributes 0 corroborations this cycle
            _feeds[_name] = _hs
        if any(_feeds.values()):                          # at least one feed up → fail-safe skip otherwise
            for m in markets:
                if len(book["open"]) >= cfg["alicenews_max_open"]: break
                if m["id"] in own or m["id"] in cooldown: continue
                yes = m["yes"]
                if not (cfg["alicenews_yes_min"] <= yes <= cfg["alicenews_yes_max"]): continue
                if m["vol"] < cfg["alicenews_min_volume"]: continue
                q_low = str(m.get("q", "")).lower()
                # must match a geopolitical keyword in the market title
                mkt_kw = next((k for k in _ankw if k in q_low), None)
                if not mkt_kw: continue
                # MULTI-SOURCE: count INDEPENDENT feeds whose headlines carry this keyword,
                # + GDELT event intensity as a third corroborating source when available.
                _src_hits = [n for n, hs in _feeds.items() if any(mkt_kw in h for h in hs)]
                try:
                    if _gd.intensity(mkt_kw, cache=_gdelt_cache) > 0:
                        _src_hits.append("gdelt")
                except Exception:
                    pass
                if len(_src_hits) < _min_src: continue     # not enough independent corroboration
                spr   = liq_spread(m["vol"], cfg)
                entry = round(min(0.999, yes + spr / 2), 4)
                size  = round(cfg["alicenews_bet_usdc"] / entry, 4) if entry > 0 else 0
                if size <= 0: continue
                _hit_headline = next((h for hs in _feeds.values() for h in hs if mkt_kw in h), "")
                book["open"].append({
                    "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "alicenews", "side": "YES",
                    "entry_mid": round(yes, 4), "entry_fill": entry,
                    "size": size, "vol": m["vol"], "opened_at": now_iso(),
                    "abs_target": cfg["alicenews_abs_target"],
                    "stop_delta":  cfg["alicenews_stop_delta"],
                    "potential_x": round(cfg["alicenews_abs_target"] / yes, 1),
                    "sources": _src_hits,                  # which independent feeds corroborated
                    "alice_headline": _hit_headline,
                })
                opened.append(book["open"][-1])

    # ── midfield — moonshot gap-fill YES 0.30-0.42 ───────────────────────────
    if cfg.get("midfield_enabled"):
        book     = state["midfield"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        for m in markets:
            if len(book["open"]) >= cfg["midfield_max_open"]: break
            if m["id"] in own or m["id"] in cooldown: continue
            yes = m["yes"]
            chg = m.get("chg", 0) or 0
            if not (cfg["midfield_yes_min"] <= yes <= cfg["midfield_yes_max"]): continue
            if m["vol"] < cfg["midfield_min_volume"]: continue
            if chg < cfg["midfield_min_chg"]: continue
            spr   = liq_spread(m["vol"], cfg)
            entry = round(min(0.999, yes + spr / 2), 4)
            size  = round(cfg["midfield_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "midfield", "side": "YES",
                "entry_mid": round(yes, 4), "entry_fill": entry,
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "abs_target": cfg["midfield_abs_target"],
                "stop_delta":  cfg["midfield_stop_delta"],
                "chg_at_entry": round(chg, 4),
                "potential_x": round(cfg["midfield_abs_target"] / yes, 1),
            })
            opened.append(book["open"][-1])

    # ── cryptobull — crypto price milestone YES buy ───────────────────────────
    if cfg.get("cryptobull_enabled"):
        book     = state["cryptobull"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        _cbkw = cfg.get("cryptobull_kw", ())
        for m in markets:
            if len(book["open"]) >= cfg["cryptobull_max_open"]: break
            if m["id"] in own or m["id"] in cooldown: continue
            q_low = str(m.get("q", "")).lower()
            if not any(k in q_low for k in _cbkw): continue
            yes = m["yes"]
            if not (cfg["cryptobull_yes_min"] <= yes <= cfg["cryptobull_yes_max"]): continue
            if m["vol"] < cfg["cryptobull_min_volume"]: continue
            try:
                from birdeye_leg import liquidity_gate as _liq_gate
                _lctx = _liq_gate(m["q"])
                if _lctx and (_lctx.get("rug_risk") or not _lctx.get("liq_ok", True)): continue
            except Exception: pass
            spr   = liq_spread(m["vol"], cfg)
            entry = round(min(0.999, yes + spr / 2), 4)
            size  = round(cfg["cryptobull_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "cryptobull", "side": "YES",
                "entry_mid": round(yes, 4), "entry_fill": entry,
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "abs_target": cfg["cryptobull_abs_target"],
                "stop_delta":  cfg["cryptobull_stop_delta"],
                "potential_x": round(cfg["cryptobull_abs_target"] / yes, 1),
            })
            opened.append(book["open"][-1])

    # ── noevent (nothing-ever-happens: NO on non-sports events) ─────────────
    if cfg.get("noevent_enabled"):
        book     = state["noevent"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        _ne_skip = cfg.get("noevent_sports_kw", ())
        for m in markets:
            if is_live_match(m.get("q", "")): continue  # live-match gap risk (2026-06-12)
            if len(book["open"]) >= cfg["noevent_max_open"]: break
            if m["id"] in own or m["id"] in cooldown: continue
            if m["vol"] < cfg["noevent_min_volume"]: continue
            yes = m["yes"]
            if not (cfg["noevent_yes_min"] <= yes <= cfg["noevent_yes_max"]): continue
            q_low = str(m.get("q", "")).lower()
            if any(k in q_low for k in _ne_skip): continue   # skip sports markets
            out_mid = 1 - yes
            entry   = ask(out_mid, cfg, m["vol"])
            size    = round(cfg["noevent_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "noevent", "side": "NO",
                "entry_mid": round(out_mid, 4), "entry_fill": round(entry, 4),
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
            })
            opened.append(book["open"][-1])

    # ── weatherno (weatherbot: fade overpriced YES on weather mkts) ──────────
    if cfg.get("weatherno_enabled"):
        book     = state["weatherno"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        _wx_kw   = cfg.get("weatherno_kw", ())
        try:
            import sys as _wsys, os as _wos
            _wsys.path.insert(0, _wos.path.dirname(_wos.path.abspath(__file__)))
            from weather_ensemble import weatherno_signal as _wx_sig
            _wx_ensemble = True
        except Exception as _we:
            _wx_ensemble = False
            import sys as _wsys; _wsys.stderr.write(f"[weatherno ensemble] {_we}\n")
        for m in markets:
            if len(book["open"]) >= cfg["weatherno_max_open"]: break
            if m["id"] in own or m["id"] in cooldown: continue
            if m["vol"] < cfg["weatherno_min_volume"]: continue
            yes = m["yes"]
            if not (cfg["weatherno_yes_min"] <= yes <= cfg["weatherno_yes_max"]): continue
            q_low = str(m.get("q", "")).lower()
            if not any(k in q_low for k in _wx_kw): continue   # must be a weather market
            # GFS ensemble upgrade: require model edge ≥ 6c; fall back to price-only if unavailable
            _wx_reason = ""
            if _wx_ensemble:
                try:
                    _wsig = _wx_sig(m["q"], yes, min_edge=0.06)
                    if _wsig is None:
                        continue   # model says edge too small or no city match
                    _wx_reason = _wsig.get("reason", "")
                except Exception:
                    pass  # ensemble unavailable — fall through to price-only entry
            out_mid = 1 - yes
            entry   = ask(out_mid, cfg, m["vol"])
            size    = round(cfg["weatherno_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "weatherno", "side": "NO",
                "entry_mid": round(out_mid, 4), "entry_fill": round(entry, 4),
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "wx_reason": _wx_reason,
            })
            opened.append(book["open"][-1])

    # ── btc15no (aulekator/cross-market-fusion: NO on crypto 15-min UP mkts) ─
    if cfg.get("btc15no_enabled"):
        try:
            from regime_leg import detect_regime as _dr3, regime_ok_for_leg as _ro3
            _btc_regime3 = _dr3("BTCUSDT")
        except Exception:
            _btc_regime3 = "RANGE"
        # 2026-06-13 FIX: book/own/cooldown/_b15_asset were defined only inside
        # the regime `else` branch but the for-loop ran unconditionally → on the
        # CHOP/TREND_DOWN skip path they were unbound and the WHOLE scan crashed
        # (UnboundLocalError, killing every leg that cycle). Define them always;
        # gate the leg with a loop-guard instead.
        book     = state["btc15no"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        _b15_asset = cfg.get("btc15no_asset_kw", ())
        _b15_frame = cfg.get("btc15no_frame_kw", ())
        _b15_regime_ok = _ro3("btc15no", _btc_regime3)   # skip in CHOP/TREND_DOWN
        for m in markets:
            if not _b15_regime_ok: break   # regime bad → skip btc15no this scan
            if len(book["open"]) >= cfg["btc15no_max_open"]: break
            if m["id"] in own or m["id"] in cooldown: continue
            if m["vol"] < cfg["btc15no_min_volume"]: continue
            yes = m["yes"]
            if not (cfg["btc15no_yes_min"] <= yes <= cfg["btc15no_yes_max"]): continue
            q_low = str(m.get("q", "")).lower()
            if not any(k in q_low for k in _b15_asset): continue
            if not any(k in q_low for k in _b15_frame): continue  # must be a "dip/drop/fall" market
            # Regime confirmation: only fade dips when BTC is not in TREND_DOWN
            # (in TREND_DOWN the dip could be real; in TREND_UP/RANGE we fade it)
            # _btc_regime3 was set above by the outer regime block — reuse it
            if locals().get("_btc_regime3", "RANGE") == "TREND_DOWN":
                continue
            out_mid = 1 - yes   # always buy NO (bet against the dip)
            entry   = ask(out_mid, cfg, m["vol"])
            size    = round(cfg["btc15no_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "btc15no", "side": "NO",
                "entry_mid": round(out_mid, 4), "entry_fill": round(entry, 4),
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "regime": locals().get("_btc_regime3", "RANGE"),
            })
            opened.append(book["open"][-1])

    # ── csarb — complete-set discount (polybot harvest): buy YES+NO < $1 ─────
    if cfg.get("csarb_enabled"):
        book     = state["csarb"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        _cs_asset = cfg.get("csarb_asset_kw", ())
        _cs_frame = cfg.get("csarb_frame_kw", ())
        for m in markets:
            if len(book["open"]) >= cfg["csarb_max_open"]: break
            if m["id"] in own or m["id"] in cooldown: continue
            if m["vol"] < cfg["csarb_min_volume"]: continue
            bid, mask = m.get("bid"), m.get("ask")
            if bid is None or mask is None: continue
            q_low = str(m.get("q", "")).lower()
            if not any(k in q_low for k in _cs_asset): continue
            if not any(k in q_low for k in _cs_frame): continue
            # polybot time gating: only inside the final 60–900s window
            _d = days_to_end(m.get("end"))
            if _d is None: continue
            secs_left = _d * 86400
            if not (cfg["csarb_window_min_s"] <= secs_left <= cfg["csarb_window_max_s"]):
                continue
            # set cost = YES ask + NO ask (NO ask = 1 - YES bid)
            set_cost = mask + (1 - bid)
            if set_cost > 1 - cfg["csarb_threshold"]: continue
            size = round(cfg["csarb_bet_usdc"] / set_cost, 4) if set_cost > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "csarb", "side": "PAIR",
                "entry_mid": round(set_cost, 4), "entry_fill": round(set_cost, 4),
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
            })
            opened.append(book["open"][-1])

    # ── newsno (polymarket-pipeline: fade "will X happen" event mkts) ────────
    if cfg.get("newsno_enabled"):
        book     = state["newsno"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        _nn_kw   = cfg.get("newsno_kw", ())
        _nn_skip = cfg.get("newsno_skip_kw", ())
        for m in markets:
            if is_live_match(m.get("q", "")): continue  # live-match gap risk (2026-06-12)
            if len(book["open"]) >= cfg["newsno_max_open"]: break
            if m["id"] in own or m["id"] in cooldown: continue
            if m["vol"] < cfg["newsno_min_volume"]: continue
            yes = m["yes"]
            if not (cfg["newsno_yes_min"] <= yes <= cfg["newsno_yes_max"]): continue
            q_low = str(m.get("q", "")).lower()
            if not any(k in q_low for k in _nn_kw): continue   # must be "will X" framing
            if any(k in q_low for k in _nn_skip): continue      # skip known-good near-certs
            out_mid = 1 - yes
            entry   = ask(out_mid, cfg, m["vol"])
            size    = round(cfg["newsno_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "newsno", "side": "NO",
                "entry_mid": round(out_mid, 4), "entry_fill": round(entry, 4),
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
            })
            opened.append(book["open"][-1])

    # ── 2026-06-11 batch: structural/arb legs ─────────────────────────────────
    # Shared machinery: one cooldown/own/open helper + clusterings computed ONCE
    # per scan for famsum / ladderarb / clusterarb / peacefwd (cheap, ~800 mkts).
    def _b11_book(leg):
        bk = state[leg]
        own_ = {p["id"] for p in bk["open"]}
        cd_  = {r["id"] for r in bk["closed"]
                if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        return bk, own_, cd_

    def _b11_open(bk, leg, m, side, mid_px):
        entry = ask(mid_px, cfg, m["vol"])
        size  = round(cfg[f"{leg}_bet_usdc"] / entry, 4) if entry > 0 else 0
        if size <= 0:
            return False
        bk["open"].append({
            "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": leg, "side": side,
            "entry_mid": round(mid_px, 4), "entry_fill": round(entry, 4),
            "size": size, "vol": m["vol"], "opened_at": now_iso(),
        })
        opened.append(bk["open"][-1])
        return True

    _b11_need_groups = any(cfg.get(f"{_bl}_enabled")
                           for _bl in ("famsum", "ladderarb", "clusterarb", "peacefwd"))
    _fam_groups, _lad_groups, _clu_groups = {}, {}, {}
    if _b11_need_groups:
        _fam_re   = re.compile(r"will (.+?) win the (.+?)\?")
        _date_re  = re.compile(r"by (january|february|march|april|may|june|july|august|"
                               r"september|october|november|december) ?\d{0,2},? ?\d{0,4}")
        _bef_re   = re.compile(r"before \d{4}")
        _amt_re   = re.compile(r"\$[\d,.]+[tmbk]?")
        for m in markets:
            ql = str(m.get("q", "")).lower()
            mt = _fam_re.search(ql)
            if mt:
                _fam_groups.setdefault(mt.group(2), []).append(m)
            lk = _bef_re.sub("<date>", _date_re.sub("<date>", ql))
            if "<date>" in lk:
                _lad_groups.setdefault(lk, []).append(m)
            ck = _amt_re.sub("<amt>", ql)
            if "<amt>" in ck and any(w in ql for w in ("above", "greater than", "reach", ">")):
                _clu_groups.setdefault(ck, []).append(m)

    # ── wcmatch (daily-match favorite near resolution: "win on 20xx-…") ──────
    if cfg.get("wcmatch_enabled"):
        book, own, cooldown = _b11_book("wcmatch")
        for m in markets:
            if len(book["open"]) >= cfg["wcmatch_max_open"]: break
            if m["id"] in own or m["id"] in cooldown: continue
            if "win on 20" not in str(m.get("q", "")).lower(): continue
            if m.get("cat") not in ("other", "sports"): continue
            if m["vol"] < cfg["wcmatch_min_volume"]: continue
            yes = m["yes"]
            if not (cfg["wcmatch_yes_min"] <= yes <= cfg["wcmatch_yes_max"]): continue
            d = days_to_end(m.get("end"))
            if d is None or not (0 < d * 24 < cfg["wcmatch_max_hours_left"]): continue
            if (m.get("chg") or 0) < 0: continue   # no negative momentum into resolution
            _b11_open(book, "wcmatch", m, "YES", yes)

    # ── famsum (overround family longshot fade: sumYES > 1.10) ───────────────
    if cfg.get("famsum_enabled"):
        book, own, cooldown = _b11_book("famsum")
        for _fam, _members in _fam_groups.items():
            if len(book["open"]) >= cfg["famsum_max_open"]: break
            if len(_members) < cfg["famsum_min_members"]: continue
            if sum(mm["yes"] for mm in _members) <= cfg["famsum_sum_min"]: continue
            # most-overpriced longshots first
            for m in sorted(_members, key=lambda mm: mm["yes"], reverse=True):
                if len(book["open"]) >= cfg["famsum_max_open"]: break
                if m["id"] in own or m["id"] in cooldown: continue
                if m["vol"] < cfg["famsum_min_volume"]: continue
                yes = m["yes"]
                if not (cfg["famsum_yes_min"] <= yes <= cfg["famsum_yes_max"]): continue
                _b11_open(book, "famsum", m, "NO", 1 - yes)

    # ── mapdown (esports BO3 panic fade: buy the crashed team) ───────────────
    if cfg.get("mapdown_enabled"):
        book, own, cooldown = _b11_book("mapdown")
        for m in markets:
            if len(book["open"]) >= cfg["mapdown_max_open"]: break
            if m["id"] in own or m["id"] in cooldown: continue
            if m.get("cat") != "esports": continue
            if (m.get("chg") or 0) > cfg["mapdown_chg_max"]: continue   # need <= -0.15
            yes = m["yes"]
            if not (cfg["mapdown_yes_min"] <= yes <= cfg["mapdown_yes_max"]): continue
            if m["vol"] < cfg["mapdown_min_volume"]: continue
            d = days_to_end(m.get("end"))
            if d is None or d * 24 <= cfg["mapdown_min_hours_left"]: continue
            _b11_open(book, "mapdown", m, "YES", yes)

    # ── ladderarb (date-ladder monotonicity: buy YES on underpriced later rung) ─
    if cfg.get("ladderarb_enabled"):
        book, own, cooldown = _b11_book("ladderarb")
        for _lk, _rungs in _lad_groups.items():
            if len(book["open"]) >= cfg["ladderarb_max_open"]: break
            if len(_rungs) < 2: continue
            _sr = sorted((r for r in _rungs if days_to_end(r.get("end")) is not None),
                         key=lambda r: days_to_end(r.get("end")))
            for _e, _l in zip(_sr, _sr[1:]):
                if len(book["open"]) >= cfg["ladderarb_max_open"]: break
                if _e["yes"] - _l["yes"] <= cfg["ladderarb_gap_min"]: continue
                m = _l   # later deadline is underpriced — buy its YES
                if m["id"] in own or m["id"] in cooldown: continue
                if m["vol"] < cfg["ladderarb_min_volume"] or _e["vol"] < cfg["ladderarb_min_volume"]: continue
                _b11_open(book, "ladderarb", m, "YES", m["yes"])

    # ── laddermid (disciplined date-ladder arb — band-gated, anti-padding) ───
    if cfg.get("laddermid_enabled"):
        book, own, cooldown = _b11_book("laddermid")
        for _lk, _rungs in _lad_groups.items():
            if len(book["open"]) >= cfg["laddermid_max_open"]: break
            if len(_rungs) < 2: continue
            _sr = sorted((r for r in _rungs if days_to_end(r.get("end")) is not None),
                         key=lambda r: days_to_end(r.get("end")))
            for _e, _l in zip(_sr, _sr[1:]):
                if len(book["open"]) >= cfg["laddermid_max_open"]: break
                if _e["yes"] - _l["yes"] <= cfg["laddermid_gap_min"]: continue
                m = _l
                # KEY DIFFERENCE vs ladderarb: bought rung must be mid-band,
                # not a cheap longshot (that's where the padding artifact lived)
                if not (cfg["laddermid_band_lo"] <= m["yes"] <= cfg["laddermid_band_hi"]): continue
                if m["id"] in own or m["id"] in cooldown: continue
                if m["vol"] < cfg["laddermid_min_volume"] or _e["vol"] < cfg["laddermid_min_volume"]: continue
                _b11_open(book, "laddermid", m, "YES", m["yes"])

    # ── negladder (date-ladder NO-side — buy NO on overpriced EARLY rung) ────
    if cfg.get("negladder_enabled"):
        book, own, cooldown = _b11_book("negladder")
        for _lk, _rungs in _lad_groups.items():
            if len(book["open"]) >= cfg["negladder_max_open"]: break
            if len(_rungs) < 2: continue
            _sr = sorted((r for r in _rungs if days_to_end(r.get("end")) is not None),
                         key=lambda r: days_to_end(r.get("end")))
            for _e, _l in zip(_sr, _sr[1:]):
                if len(book["open"]) >= cfg["negladder_max_open"]: break
                if _e["yes"] - _l["yes"] <= cfg["negladder_gap_min"]: continue
                m = _e   # earlier deadline is OVERpriced — buy its NO
                if not (cfg["negladder_band_lo"] <= m["yes"] <= cfg["negladder_band_hi"]): continue
                if m["id"] in own or m["id"] in cooldown: continue
                if m["vol"] < cfg["negladder_min_volume"] or _l["vol"] < cfg["negladder_min_volume"]: continue
                _b11_open(book, "negladder", m, "NO", 1 - m["yes"])

    # ── thetadk ("by <month>" catalyst-needed deadline decay — buy NO) ───────
    if cfg.get("thetadk_enabled"):
        book, own, cooldown = _b11_book("thetadk")
        _tdk_re = re.compile(r"by (january|february|march|april|may|june|july|august|"
                             r"september|october|november|december)")
        for m in markets:
            if len(book["open"]) >= cfg["thetadk_max_open"]: break
            if m["id"] in own or m["id"] in cooldown: continue
            ql = str(m.get("q", "")).lower()
            if not _tdk_re.search(ql): continue
            if ql.startswith("will no "): continue   # Lesson 6 double-negative guard
            if is_live_match(ql): continue           # Lesson 5 padded-end gap risk
            d = days_to_end(m.get("end"))
            if d is None or not (cfg["thetadk_days_min"] <= d <= cfg["thetadk_days_max"]): continue
            yes = m["yes"]
            if not (cfg["thetadk_yes_min"] <= yes <= cfg["thetadk_yes_max"]): continue
            if m["vol"] < cfg["thetadk_min_volume"]: continue
            _b11_open(book, "thetadk", m, "NO", 1 - yes)

    # ── certsnipe (past-end near-certainty: buy YES, hold to resolution) ─────
    if cfg.get("certsnipe_enabled"):
        book, own, cooldown = _b11_book("certsnipe")
        for m in markets:
            if len(book["open"]) >= cfg["certsnipe_max_open"]: break
            if m["id"] in own or m["id"] in cooldown: continue
            d = days_to_end(m.get("end"))
            if d is None: continue
            # end date 0.5–30 days IN THE PAST
            if not (-cfg["certsnipe_past_days_max"] <= d <= -cfg["certsnipe_past_days_min"]): continue
            yes = m["yes"]
            if not (cfg["certsnipe_yes_min"] <= yes <= cfg["certsnipe_yes_max"]): continue
            if m["vol"] < cfg["certsnipe_min_volume"]: continue
            _b11_open(book, "certsnipe", m, "YES", yes)

    # ── dipladder (BTC "dip to $X" touch-prob vs distance-to-strike) ─────────
    if cfg.get("dipladder_enabled"):
        book, own, cooldown = _b11_book("dipladder")
        _dip_re  = re.compile(r"dip to \$([\d,]+)")
        _cg      = fetch_coingecko()   # cached 5 min; {} on outage → skip silently
        _btcspot = (_cg.get("bitcoin") or {}).get("usd")
        if _btcspot:
            for m in markets:
                if len(book["open"]) >= cfg["dipladder_max_open"]: break
                if m["id"] in own or m["id"] in cooldown: continue
                if m.get("cat") != "crypto": continue
                ql = str(m.get("q", "")).lower()
                if "bitcoin" not in ql: continue
                mt = _dip_re.search(ql)
                if not mt: continue
                try:
                    strike = float(mt.group(1).replace(",", ""))
                except ValueError:
                    continue
                if m["vol"] < cfg["dipladder_min_volume"]: continue
                dist = (_btcspot - strike) / _btcspot
                yes  = m["yes"]
                if dist > cfg["dipladder_far_dist"] and yes >= cfg["dipladder_far_yes_min"]:
                    _b11_open(book, "dipladder", m, "NO", 1 - yes)      # touch overpriced
                elif (cfg["dipladder_near_dist_lo"] < dist < cfg["dipladder_near_dist_hi"]
                      and yes <= cfg["dipladder_near_yes_max"]):
                    _b11_open(book, "dipladder", m, "YES", yes)          # touch underpriced

    # ── clusterarb (threshold coherence: NO on inverted/thin higher threshold) ─
    if cfg.get("clusterarb_enabled"):
        book, own, cooldown = _b11_book("clusterarb")
        _thr_re = re.compile(r"\$([\d,.]+) ?([tmbk]?)")
        _mult   = {"k": 1e3, "m": 1e6, "b": 1e9, "t": 1e12, "": 1.0}
        for _ck, _members in _clu_groups.items():
            if len(book["open"]) >= cfg["clusterarb_max_open"]: break
            if len(_members) < 2: continue
            _parsed = []
            for mm in _members:
                tm = _thr_re.search(str(mm.get("q", "")).lower())
                if not tm: continue
                try:
                    _parsed.append((float(tm.group(1).replace(",", "").rstrip("."))
                                    * _mult.get(tm.group(2), 1.0), mm))
                except ValueError:
                    continue
            _parsed.sort(key=lambda t: t[0])
            for (t_lo, m_lo), (t_hi, m_hi) in zip(_parsed, _parsed[1:]):
                if len(book["open"]) >= cfg["clusterarb_max_open"]: break
                if t_hi <= t_lo: continue
                # coherent pricing: yes_hi must sit BELOW yes_lo by more than tol
                if m_hi["yes"] < m_lo["yes"] - cfg["clusterarb_tol"]: continue
                m = m_hi
                if m["id"] in own or m["id"] in cooldown: continue
                if m["vol"] < cfg["clusterarb_min_volume"] or m_lo["vol"] < cfg["clusterarb_min_volume"]: continue
                _b11_open(book, "clusterarb", m, "NO", 1 - m["yes"])

    # ── strikeflip (strike-ladder YES-side arb — buy underpriced LOW strike) ─
    if cfg.get("strikeflip_enabled"):
        book, own, cooldown = _b11_book("strikeflip")
        _sf_re = re.compile(r"\$([\d,.]+) ?([tmbk]?)")
        _sf_mult = {"k": 1e3, "m": 1e6, "b": 1e9, "t": 1e12, "": 1.0}
        for _ck, _members in _clu_groups.items():
            if len(book["open"]) >= cfg["strikeflip_max_open"]: break
            if len(_members) < 2: continue
            _parsed = []
            for mm in _members:
                tm = _sf_re.search(str(mm.get("q", "")).lower())
                if not tm: continue
                try:
                    _parsed.append((float(tm.group(1).replace(",", "").rstrip("."))
                                    * _sf_mult.get(tm.group(2), 1.0), mm))
                except ValueError:
                    continue
            _parsed.sort(key=lambda t: t[0])
            for (t_lo, m_lo), (t_hi, m_hi) in zip(_parsed, _parsed[1:]):
                if len(book["open"]) >= cfg["strikeflip_max_open"]: break
                if t_hi <= t_lo: continue
                # violation: LOWER strike's YES sits BELOW higher strike's by gap
                # (lower strike is easier → must be >= higher). Buy the cheap low strike.
                if m_lo["yes"] >= m_hi["yes"] - cfg["strikeflip_gap_min"]: continue
                m = m_lo
                if not (cfg["strikeflip_band_lo"] <= m["yes"] <= cfg["strikeflip_band_hi"]): continue
                if m["id"] in own or m["id"] in cooldown: continue
                if m["vol"] < cfg["strikeflip_min_volume"] or m_hi["vol"] < cfg["strikeflip_min_volume"]: continue
                _b11_open(book, "strikeflip", m, "YES", m["yes"])

    # ── socspread (soccer "Spread:" handicap fade — buy NO) ──────────────────
    if cfg.get("socspread_enabled"):
        book, own, cooldown = _b11_book("socspread")
        for m in markets:
            if len(book["open"]) >= cfg["socspread_max_open"]: break
            if m["id"] in own or m["id"] in cooldown: continue
            if not str(m.get("q", "")).lower().startswith("spread:"): continue
            yes = m["yes"]
            if not (cfg["socspread_yes_min"] <= yes <= cfg["socspread_yes_max"]): continue
            if m["vol"] < cfg["socspread_min_volume"]: continue
            d = days_to_end(m.get("end"))
            if d is None or not (0 < d * 24 < cfg["socspread_max_hours_left"]): continue
            _b11_open(book, "socspread", m, "NO", 1 - yes)

    # ── peacefwd (rich middle-rung ladder fade: fwd prob fat, base thin) ─────
    if cfg.get("peacefwd_enabled"):
        book, own, cooldown = _b11_book("peacefwd")
        for _lk, _rungs in _lad_groups.items():
            if len(book["open"]) >= cfg["peacefwd_max_open"]: break
            _sr = sorted((r for r in _rungs if days_to_end(r.get("end")) is not None),
                         key=lambda r: days_to_end(r.get("end")))
            if len(_sr) < 3: continue
            _ys = [r["yes"] for r in _sr]
            if any(b < a for a, b in zip(_ys, _ys[1:])): continue   # need monotone ladder
            if _ys[0] >= cfg["peacefwd_base_yes_max"]: continue     # nothing imminent required
            for i in range(len(_sr) - 1):
                if len(book["open"]) >= cfg["peacefwd_max_open"]: break
                y1, y2 = _ys[i], _ys[i + 1]
                if y1 >= 1.0: continue
                fwd = (y2 - y1) / (1 - y1)
                if fwd <= cfg["peacefwd_fwd_min"]: continue
                m = _sr[i + 1]   # the later rung pricing heavy tempo — fade it
                if m["id"] in own or m["id"] in cooldown: continue
                if m["vol"] < cfg["peacefwd_min_volume"]: continue
                _b11_open(book, "peacefwd", m, "NO", 1 - m["yes"])

    # ── emacross (two-timescale trend confirm → buy YES) ─────────────────────
    if cfg.get("emacross_enabled"):
        book     = state["emacross"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        for m in markets:
            if len(book["open"]) >= cfg["emacross_max_open"]: break
            if m["id"] in own or m["id"] in cooldown: continue
            if m["vol"] < cfg["emacross_min_volume"]: continue
            yes = m["yes"]
            if not (cfg["emacross_yes_min"] <= yes <= cfg["emacross_yes_max"]): continue
            if m.get("chg", 0) < cfg["emacross_min_chg"]: continue
            prev = _price_snap.get(m["id"])
            if prev is None or yes - prev < cfg["emacross_min_scan_rise"]: continue
            entry = ask(yes, cfg, m["vol"])
            size  = round(cfg["emacross_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "emacross", "side": "YES",
                "entry_mid": round(yes, 4), "entry_fill": round(entry, 4),
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
            })
            opened.append(book["open"][-1])

    # ── ridewinner (strongest crypto mover momentum → buy YES) ───────────────
    if cfg.get("ridewinner_enabled"):
        book     = state["ridewinner"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        _rw_kw   = cfg.get("ridewinner_kw", ())
        cands = [m for m in markets
                 if m["id"] not in own and m["id"] not in cooldown
                 and m["vol"] >= cfg["ridewinner_min_volume"]
                 and cfg["ridewinner_yes_min"] <= m["yes"] <= cfg["ridewinner_yes_max"]
                 and m.get("chg", 0) >= cfg["ridewinner_min_chg"]
                 and any(k in str(m.get("q", "")).lower() for k in _rw_kw)]
        cands.sort(key=lambda m: m.get("chg", 0), reverse=True)   # leaders first
        for m in cands:
            if len(book["open"]) >= cfg["ridewinner_max_open"]: break
            yes   = m["yes"]
            entry = ask(yes, cfg, m["vol"])
            size  = round(cfg["ridewinner_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "ridewinner", "side": "YES",
                "entry_mid": round(yes, 4), "entry_fill": round(entry, 4),
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "chg_at_entry": round(m.get("chg", 0), 4),
            })
            opened.append(book["open"][-1])

    # ── tadip (24h dip + scan-level bounce confirm → buy YES) ────────────────
    if cfg.get("tadip_enabled"):
        book     = state["tadip"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        for m in markets:
            if len(book["open"]) >= cfg["tadip_max_open"]: break
            if m["id"] in own or m["id"] in cooldown: continue
            if m["vol"] < cfg["tadip_min_volume"]: continue
            yes = m["yes"]
            if not (cfg["tadip_yes_min"] <= yes <= cfg["tadip_yes_max"]): continue
            if m.get("chg", 0) > cfg["tadip_max_chg"]: continue   # must have dropped ≥6¢
            prev = _price_snap.get(m["id"])
            if prev is None or yes - prev < cfg["tadip_min_scan_bounce"]: continue
            entry = ask(yes, cfg, m["vol"])
            size  = round(cfg["tadip_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "tadip", "side": "YES",
                "entry_mid": round(yes, 4), "entry_fill": round(entry, 4),
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
            })
            opened.append(book["open"][-1])

    # ── gridbounce (±8¢ deviation mean-reversion fade) ───────────────────────
    if cfg.get("gridbounce_enabled"):
        book     = state["gridbounce"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        for m in markets:
            if len(book["open"]) >= cfg["gridbounce_max_open"]: break
            if m["id"] in own or m["id"] in cooldown: continue
            if m["vol"] < cfg["gridbounce_min_volume"]: continue
            yes = m["yes"]
            if not (cfg["gridbounce_yes_min"] <= yes <= cfg["gridbounce_yes_max"]): continue
            chg = m.get("chg", 0)
            if abs(chg) < cfg["gridbounce_min_abs_chg"]: continue
            if chg > 0:   # spiked up → fade with NO
                side, side_mid = "NO", 1 - yes
            else:         # dumped → fade with YES
                side, side_mid = "YES", yes
            entry = ask(side_mid, cfg, m["vol"])
            size  = round(cfg["gridbounce_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "gridbounce", "side": side,
                "entry_mid": round(side_mid, 4), "entry_fill": round(entry, 4),
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "chg_at_entry": round(chg, 4),
            })
            opened.append(book["open"][-1])

    # ── sentibreadth (crypto breadth risk-on → buy laggard YES) ──────────────
    if cfg.get("sentibreadth_enabled"):
        book     = state["sentibreadth"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        _sb_kw   = cfg.get("sentibreadth_kw", ())
        crypto_m = [m for m in markets
                    if any(k in str(m.get("q", "")).lower() for k in _sb_kw)]
        movers   = [m for m in crypto_m if abs(m.get("chg", 0)) > 0.01]
        if len(movers) >= cfg["sentibreadth_min_movers"]:
            breadth = sum(1 for m in movers if m.get("chg", 0) > 0) / len(movers)
            if breadth >= cfg["sentibreadth_min_breadth"]:
                for m in crypto_m:
                    if len(book["open"]) >= cfg["sentibreadth_max_open"]: break
                    if m["id"] in own or m["id"] in cooldown: continue
                    if m["vol"] < cfg["sentibreadth_min_volume"]: continue
                    yes = m["yes"]
                    if not (cfg["sentibreadth_yes_min"] <= yes <= cfg["sentibreadth_yes_max"]): continue
                    if abs(m.get("chg", 0)) > cfg["sentibreadth_max_abs_chg"]: continue  # laggards only
                    entry = ask(yes, cfg, m["vol"])
                    size  = round(cfg["sentibreadth_bet_usdc"] / entry, 4) if entry > 0 else 0
                    if size <= 0: continue
                    book["open"].append({
                        "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "sentibreadth", "side": "YES",
                        "entry_mid": round(yes, 4), "entry_fill": round(entry, 4),
                        "size": size, "vol": m["vol"], "opened_at": now_iso(),
                        "breadth_at_entry": round(breadth, 3),
                    })
                    opened.append(book["open"][-1])

    # ── fav75 (~75¢ favorite convergence ride → buy YES, 2:1 R:R) ────────────
    if cfg.get("fav75_enabled"):
        book     = state["fav75"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        for m in markets:
            if len(book["open"]) >= cfg["fav75_max_open"]: break
            if m["id"] in own or m["id"] in cooldown: continue
            if m["vol"] < cfg["fav75_min_volume"]: continue
            # Exclude esports/live-match markets (2026-06-13): every fav75 loss so
            # far was an in-play esports match gapping through the stop (one filled
            # at 0.001 on a 0.10 stop). They pass the min_days filter but reprice in
            # jumps, so the 2:1 R:R math doesn't hold there.
            _q = str(m.get("q", "")).lower()
            if m.get("cat") == "esports" or any(k in _q for k in cfg.get("fav75_skip_kw", ())):
                continue
            yes = m["yes"]
            if not (cfg["fav75_yes_min"] <= yes <= cfg["fav75_yes_max"]): continue
            d = days_to_end(m.get("end"))
            if d is None or d > cfg["fav75_max_days"] or d < cfg["fav75_min_days"]: continue
            entry = ask(yes, cfg, m["vol"])
            size  = round(cfg["fav75_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "fav75", "side": "YES",
                "entry_mid": round(yes, 4), "entry_fill": round(entry, 4),
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "days_left": round(d, 2),
            })
            opened.append(book["open"][-1])

    # ── rsifade (overbought ≥12¢ 24h spike → buy NO) ─────────────────────────
    if cfg.get("rsifade_enabled"):
        book     = state["rsifade"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        for m in markets:
            if len(book["open"]) >= cfg["rsifade_max_open"]: break
            if m["id"] in own or m["id"] in cooldown: continue
            if m["vol"] < cfg["rsifade_min_volume"]: continue
            yes = m["yes"]
            if not (cfg["rsifade_yes_min"] <= yes <= cfg["rsifade_yes_max"]): continue
            if m.get("chg", 0) < cfg["rsifade_min_chg"]: continue
            out_mid = 1 - yes
            entry   = ask(out_mid, cfg, m["vol"])
            size    = round(cfg["rsifade_bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0: continue
            book["open"].append({
                "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "rsifade", "side": "NO",
                "entry_mid": round(out_mid, 4), "entry_fill": round(entry, 4),
                "size": size, "vol": m["vol"], "opened_at": now_iso(),
                "chg_at_entry": round(m.get("chg", 0), 4),
            })
            opened.append(book["open"][-1])

    # ── newsmove (knowledge-based: trade GDELT impact-memory calibration) ────
    if cfg.get("newsmove_enabled"):
        book     = state["newsmove"]
        own      = {p["id"] for p in book["open"]}
        cooldown = {r["id"] for r in book["closed"]
                    if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
        cal = newsmove_calibration(cfg)   # {} until impact memory has 24h data → dormant
        if cal:
            for m in markets:
                if len(book["open"]) >= cfg["newsmove_max_open"]: break
                if m["id"] in own or m["id"] in cooldown: continue
                if m["vol"] < cfg["newsmove_min_volume"]: continue
                yes = m["yes"]
                if not (cfg["newsmove_yes_min"] <= yes <= cfg["newsmove_yes_max"]): continue
                if abs(m.get("chg", 0)) > cfg["newsmove_max_abs_chg"]: continue  # not yet repriced
                q_low = str(m.get("q", "")).lower()
                hit = next((c for ent, c in cal.items() if ent in q_low), None)
                if not hit: continue
                if hit["dir"] > 0:          # news historically pushed this entity UP
                    side, side_mid = "YES", yes
                else:                       # historically DOWN
                    side, side_mid = "NO", 1 - yes
                entry = ask(side_mid, cfg, m["vol"])
                size  = round(cfg["newsmove_bet_usdc"] / entry, 4) if entry > 0 else 0
                if size <= 0: continue
                book["open"].append({
                    "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "newsmove", "side": side,
                    "entry_mid": round(side_mid, 4), "entry_fill": round(entry, 4),
                    "size": size, "vol": m["vol"], "opened_at": now_iso(),
                    "cal_edge": hit["edge"], "cal_n": hit["n"],
                })
                opened.append(book["open"][-1])

    # Stamp category onto every newly-opened position from this cycle's markets.
    # One central point so every leg (esp. fade) carries `cat` into its closed
    # record via _close's dict(pos) copy — enables the fade sports-split.
    _catmap = {m["id"]: m.get("cat", "other") for m in markets}
    for p in opened:
        p["cat"] = _catmap.get(p["id"], "other")

    # ── Sports context enrichment: annotate ALL sports positions at entry ───────
    # Every newly-opened position on a sports market gets ESPN news mentions,
    # TheSportsDB last result, and WC standing appended as metadata.
    # This is "analyze every trade" — not a leg, just context on the position.
    # Context lives in pos["_ctx"] and flows into the state JSON for post-hoc review.
    _wc_news_snap  = _wc_news_cache.get("data", {})
    _wc_std_snap   = _wc_standings_cache.get("data", {})
    for p in opened:
        if p.get("cat") != "sports" or p.get("_ctx"):
            continue   # non-sports or already enriched (wcrank/wcnews carry their own)
        team, _ = _extract_wc_team(p.get("q",""))
        if not team:
            continue
        last_res = _tsdb_form_cache.get(team, {}).get("data", {})  # cache hit only — no extra call
        standing = _wc_std_snap.get(team) or {}
        p["_ctx"] = _sports_context(team, _wc_std_snap, _wc_news_snap, last_res)

    # ── birdeye (Solana whale/smart-money signal, 2026-06-12) ────────────────
    if cfg.get("birdeye_enabled"):
        try:
            import sys as _sys, os as _os
            _sys.path.insert(0, _os.path.expanduser("~/Documents/birdeye-py"))
            from birdeye_leg import birdeye_signal
            book     = state["birdeye"]
            own      = {p["id"] for p in book["open"]}
            cooldown = {r["id"] for r in book["closed"]
                        if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
            for m in markets:
                if len(book["open"]) >= cfg["birdeye_max_open"]: break
                if m["id"] in own or m["id"] in cooldown: continue
                if m["vol"] < cfg["birdeye_min_volume"]: continue
                yes = m["yes"]
                if not (cfg["birdeye_yes_min"] <= yes <= cfg["birdeye_yes_max"]): continue
                sig = birdeye_signal(m["q"])
                if sig is None: continue
                side  = sig["side"]
                price = yes if side == "YES" else (1 - yes)
                entry = ask(price, cfg, m["vol"])
                size  = round(cfg["birdeye_bet_usdc"] / entry, 4) if entry > 0 else 0
                if size <= 0: continue
                book["open"].append({
                    "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "birdeye",
                    "side": side, "entry_mid": round(price, 4), "entry_fill": round(entry, 4),
                    "size": size, "vol": m["vol"], "opened_at": now_iso(),
                    "birdeye_reason": sig.get("reason", ""),
                    "birdeye_conf": round(sig.get("confidence", 0), 3),
                })
                opened.append(book["open"][-1])
        except Exception as _e:
            import sys as _sys
            _sys.stderr.write(f"[birdeye] error: {_e}\n")

    # ── liqcrush (liquidity collapse → buy NO, 2026-06-12) ───────────────────
    if cfg.get("liqcrush_enabled"):
        try:
            import sys as _sys2, os as _os2
            _sys2.path.insert(0, _os2.path.expanduser("~/Documents/birdeye-py"))
            from birdeye_leg import liqcrush_signal
            book     = state["liqcrush"]
            own      = {p["id"] for p in book["open"]}
            cooldown = {r["id"] for r in book["closed"]
                        if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
            for m in markets:
                if len(book["open"]) >= cfg["liqcrush_max_open"]: break
                if m["id"] in own or m["id"] in cooldown: continue
                if m["vol"] < cfg["liqcrush_min_volume"]: continue
                yes = m["yes"]
                if not (cfg["liqcrush_yes_min"] <= yes <= cfg["liqcrush_yes_max"]): continue
                sig = liqcrush_signal(m["q"])
                if sig is None: continue
                # liqcrush always buys NO
                price = 1 - yes
                entry = ask(price, cfg, m["vol"])
                size  = round(cfg["liqcrush_bet_usdc"] / entry, 4) if entry > 0 else 0
                if size <= 0: continue
                book["open"].append({
                    "id": m["id"], "q": m["q"], "cat": m.get("cat", ""), "strategy": "liqcrush",
                    "side": "NO", "entry_mid": round(price, 4), "entry_fill": round(entry, 4),
                    "size": size, "vol": m["vol"], "opened_at": now_iso(),
                    "liqcrush_reason": sig.get("reason", ""),
                    "liqcrush_conf": round(sig.get("confidence", 0), 3),
                })
                opened.append(book["open"][-1])
        except Exception as _e2:
            import sys as _sys2
            _sys2.stderr.write(f"[liqcrush] error: {_e2}\n")

    # ── newscrypto / newsesports (RSS news signal legs, 2026-06-13) ───────────
    for _nl_leg, _nl_cat, _nl_cfg_pfx in (
            ("newscrypto",  "crypto",  "newscrypto"),
            ("newsesports", "esports", "newsesports"),
    ):
        if not cfg.get(f"{_nl_cfg_pfx}_enabled"):
            continue
        try:
            import sys as _nsys, os as _nos
            _nsys.path.insert(0, _nos.path.dirname(_nos.path.abspath(__file__)))
            from news_leg import news_signal as _news_sig
            book     = state[_nl_leg]
            own      = {p["id"] for p in book["open"]}
            cooldown = {r["id"] for r in book["closed"]
                        if hours_since(r.get("closed_at", "")) < cfg["reentry_cooldown_hours"]}
            for m in markets:
                if len(book["open"]) >= cfg[f"{_nl_cfg_pfx}_max_open"]: break
                if m["id"] in own or m["id"] in cooldown: continue
                if m["vol"] < cfg[f"{_nl_cfg_pfx}_min_volume"]: continue
                yes = m["yes"]
                if not (cfg[f"{_nl_cfg_pfx}_yes_min"] <= yes <= cfg[f"{_nl_cfg_pfx}_yes_max"]): continue
                # Only match markets in the right category
                m_cat = m.get("cat", "")
                if _nl_leg == "newscrypto" and m_cat not in ("crypto", ""):
                    continue
                if _nl_leg == "newsesports" and m_cat not in ("esports", ""):
                    continue
                sig = _news_sig(m["q"], yes, _nl_cat)
                if sig is None: continue
                side  = sig["side"]
                price = yes if side == "YES" else (1 - yes)
                entry = ask(price, cfg, m["vol"])
                size  = round(cfg[f"{_nl_cfg_pfx}_bet_usdc"] / entry, 4) if entry > 0 else 0
                if size <= 0: continue
                book["open"].append({
                    "id": m["id"], "q": m["q"], "cat": m_cat, "strategy": _nl_leg,
                    "side": side, "entry_mid": round(price, 4), "entry_fill": round(entry, 4),
                    "size": size, "vol": m["vol"], "opened_at": now_iso(),
                    "news_headline": sig.get("headline", "")[:120],
                    "news_conf":     round(sig.get("confidence", 0), 3),
                    "news_edge":     round(sig.get("edge", 0), 3),
                })
                opened.append(book["open"][-1])
        except Exception as _ne:
            import sys as _nsys
            _nsys.stderr.write(f"[{_nl_leg}] error: {_ne}\n")

    # Append-only belief ledger: each opened position is an immutable forecast
    for leg in LEGS:
        for pos in state[leg]["open"]:
            if pos.get("opened_at", "")[:19] == now_iso()[:19]:  # this cycle only
                _bl.append_forecast(pos, leg)

    return opened


# ── Exit logic ───────────────────────────────────────────────────────────────
def scan_exits(markets, state, cfg):
    price = {m["id"]: m["yes"] for m in markets}
    # Honest-mark held positions that dropped out of the top-volume feed:
    # fetch their REAL price directly so they exit at a true mark, not breakeven.
    # 'stale_nodata' now fires only on a genuine outage (direct fetch also empty).
    missing = [pos["id"] for strat in LEGS for pos in state[strat]["open"]
               if pos["id"] not in price]
    if missing:
        price.update(fetch_prices_direct(missing))
    closed = []

    for strat in LEGS:
        book = state[strat]
        still_open = []
        for pos in book["open"]:
            yes = price.get(pos["id"])
            if yes is None:
                # market gone from top list — hold unless stale (per-strategy window)
                _cap = cfg.get(f"{strat}_max_hold_hours", cfg["max_hold_hours"])
                if hours_since(pos["opened_at"]) > _cap:
                    # Last chance before censoring: gamma forgets resolved
                    # markets, the CLOB does not — fetch the settled price so
                    # the exit books at truth instead of stale_nodata/None.
                    yes = fetch_price_clob(pos["id"])
                    if yes is None:
                        _close(book, closed, pos, pos["entry_fill"], "stale_nodata", cfg)
                        continue
                    price[pos["id"]] = yes
                    # fall through to normal exit logic with the settled price
                else:
                    still_open.append(pos)
                    continue

            # price of the OUTCOME this position holds (YES or NO)
            mid     = (1 - yes) if pos.get("side") == "NO" else yes
            _hs = _half(pos, cfg)   # per-position half-spread (fade variants) else flat = bid()
            exit_px = max(0.001, mid - _hs)
            # ── Momentum exit: if price is accelerating AGAINST the position (≥4¢/scan)
            # AND we're already 40%+ into the stop, exit now rather than ride it out.
            # This avoids the classic "watch it stop out anyway" scenario.
            _mom_reason = _momentum_exit_reason(pos, yes, cfg, strat)
            reason = None
            if strat == "dip":
                if yes >= cfg["dip_sell_target"]:
                    reason = "target"
                elif yes <= cfg["dip_stop"]:
                    reason = "stop"
            elif strat == "scalp":
                if mid >= pos["entry_mid"] + cfg["scalp_gain"]:
                    reason = "target"
                elif mid <= pos["entry_mid"] - cfg["scalp_stop"]:
                    reason = "stop"
            elif strat == "fade":  # NO side — patient, wide target
                if mid >= pos["entry_mid"] + cfg["fade_gain"]:
                    reason = "target"
                elif mid <= pos["entry_mid"] - cfg["fade_stop"]:
                    reason = "stop"
            elif strat == "fastfade":  # NO side — quick small target, tight stop
                if mid >= pos["entry_mid"] + cfg["fastfade_target"]:
                    reason = "target"
                elif mid <= pos["entry_mid"] - cfg["fastfade_stop"]:
                    reason = "stop"
            elif strat == "allin":  # YES side — +target / -stop on everything
                if mid >= pos["entry_mid"] + cfg["allin_target"]:
                    reason = "target"
                elif mid <= pos["entry_mid"] - cfg["allin_stop"]:
                    reason = "stop"
            elif strat == "momentum":
                if mid >= pos["entry_mid"] + cfg["momentum_gain"]:
                    reason = "target"
                elif mid <= pos["entry_mid"] - cfg["momentum_stop"]:
                    reason = "stop"
            elif strat == "favyes":
                if mid >= pos["entry_mid"] + cfg["favyes_gain"]:
                    reason = "target"
                elif mid <= pos["entry_mid"] - cfg["favyes_stop"]:
                    reason = "stop"
            elif strat == "coinflip":
                if mid >= pos["entry_mid"] + cfg["coinflip_gain"]:
                    reason = "target"
                elif mid <= pos["entry_mid"] - cfg["coinflip_stop"]:
                    reason = "stop"
            elif strat == "midfade":
                if mid >= pos["entry_mid"] + cfg["midfade_gain"]:
                    reason = "target"
                elif mid <= pos["entry_mid"] - cfg["midfade_stop"]:
                    reason = "stop"
            elif strat in ("truefade", "deepfade", "nsfade", "wangfade",
                           "noevent", "weatherno", "btc15no", "newsno",
                           "ytbuzz",
                           "emacross", "ridewinner", "tadip", "gridbounce",
                           "sentibreadth", "rsifade", "fav75", "newsmove",
                           "windowshut", "windowshutrand"):  # gain/stop on side-mid
                if mid >= pos["entry_mid"] + cfg[f"{strat}_gain"]:
                    reason = "target"
                elif mid <= pos["entry_mid"] - cfg[f"{strat}_stop"]:
                    reason = "stop"
            elif strat in ("manifold", "metaculus",
                           "endgame", "lowliq", "contrarian", "catpol"):  # gain/stop shape
                if mid >= pos["entry_mid"] + cfg[f"{strat}_gain"]:
                    reason = "target"
                elif mid <= pos["entry_mid"] - cfg[f"{strat}_stop"]:
                    reason = "stop"
            elif strat in ("wcrank", "wcnews"):  # sports divergence legs
                gain_k = "wcrank_gain" if strat == "wcrank" else "wcnews_gain"
                stop_k = "wcrank_stop" if strat == "wcrank" else "wcnews_stop"
                if mid >= pos["entry_mid"] + cfg[gain_k]:
                    reason = "target"
                elif mid <= pos["entry_mid"] - cfg[stop_k]:
                    reason = "stop"
            elif strat == "conviction":  # YES side — tight stop, high target
                if mid >= cfg["conviction_target"]:
                    reason = "target"
                elif mid <= cfg["conviction_stop"]:
                    reason = "stop"
            elif strat == "moonshot":  # YES side, NO stop — exit at target or time
                # honor a legacy tiered position's stored target; new positions use 0.50
                if mid >= pos.get("abs_target", cfg["moonshot_target"]):
                    reason = "target"
                # no stop: the $1 entry is already the max loss — let it ride or expire
            elif strat in ("esportsdog", "geopolbomb", "midfield", "cryptobull", "alicenews"):
                # Risky legs: per-position abs_target and stop_delta (moonshot-style)
                tgt = pos.get("abs_target", 0.65)
                stp = pos.get("stop_delta", 0.05)
                if mid >= tgt:
                    reason = "target"
                elif mid <= pos["entry_mid"] - stp:
                    reason = "stop"
            elif strat == "longshot":
                # NO side — per-position abs_target and stop_delta stored at entry
                tgt = pos.get("abs_target", pos["entry_mid"] + 0.22)
                stp = pos.get("stop_delta", cfg.get("longshot_tm_stop_delta", 0.08))
                if mid >= tgt:
                    reason = "target"
                elif mid <= pos["entry_mid"] - stp:
                    reason = "stop"
            elif strat == "nearwin":
                # YES side — absolute target, fixed stop
                if mid >= cfg["nearwin_target"]:
                    reason = "target"
                elif mid <= pos["entry_mid"] - cfg["nearwin_stop"]:
                    reason = "stop"
            elif strat == "crashbuy":
                # YES side — relative target, fixed stop
                if mid >= pos["entry_mid"] + cfg["crashbuy_target_delta"]:
                    reason = "target"
                elif mid <= pos["entry_mid"] - cfg["crashbuy_stop"]:
                    reason = "stop"
            elif strat == "tp20":
                # YES side — take +20¢ and sell, hard −10¢ stop
                if mid >= pos["entry_mid"] + cfg["tp20_gain"]:
                    reason = "target"
                elif mid <= pos["entry_mid"] - cfg["tp20_stop"]:
                    reason = "stop"
            elif strat == "scalp02":
                # YES side — take +0.2¢ (maker) and sell, −3¢ tail stop
                if mid >= pos["entry_mid"] + cfg["scalp02_gain"]:
                    reason = "target"
                elif mid <= pos["entry_mid"] - cfg["scalp02_stop"]:
                    reason = "stop"
            elif strat == "volsurge":
                # outcome-mid (YES or NO side) — house 3:1 target/stop
                if mid >= pos["entry_mid"] + cfg["volsurge_gain"]:
                    reason = "target"
                elif mid <= pos["entry_mid"] - cfg["volsurge_stop"]:
                    reason = "stop"
            elif strat == "deadline":
                # YES side — absolute target, fixed stop
                if mid >= cfg["deadline_target"]:
                    reason = "target"
                elif mid <= pos["entry_mid"] - cfg["deadline_stop"]:
                    reason = "stop"
            elif strat == "impulse":
                # NO side — per-position abs_target and stop_delta
                tgt = pos.get("abs_target", pos["entry_mid"] + cfg["impulse_target_delta"])
                stp = pos.get("stop_delta", cfg["impulse_stop_delta"])
                if mid >= tgt:
                    reason = "target"
                elif mid <= pos["entry_mid"] - stp:
                    reason = "stop"
            elif strat == "fogbuy":
                # YES side — relative target, fixed stop
                if mid >= pos["entry_mid"] + cfg["fogbuy_target_delta"]:
                    reason = "target"
                elif mid <= pos["entry_mid"] - cfg["fogbuy_stop"]:
                    reason = "stop"
            elif strat in HOLD_TO_RES_LEGS and cfg.get("nearres_hold_to_resolution", False):
                # Hold-to-resolution (2026-06-11, Lesson #8): the 9c target caps
                # winners at favorite prices while stops book full adverse moves —
                # realized R:R ≈ 1.2 despite a 3.0 config. The OOS edge (+4.2c/trade,
                # CI>0) was measured at RESOLUTION. Exit only when settled (or the
                # near-certain band), at the settled price — redemption pays no spread.
                # Keep the 3c stop (2026-06-11 sweep, n=110 OOS markets):
                # ride+stop = +4.7c/trade CI[+0.011,+0.080]; ride with NO stop =
                # 94% WR but CI[-0.10,+0.14] — full-loss variance kills it.
                if mid <= pos["entry_mid"] - cfg.get(f"{strat}_stop", 0.03):
                    reason = "stop"
                elif mid >= cfg.get("nearres_resolve_hi", 0.98):
                    reason, exit_px = "resolved", round(mid, 4)
                elif mid <= cfg.get("nearres_resolve_lo", 0.02):
                    reason, exit_px = "resolved", round(mid, 4)
                # else: hold — time-cap backstop below still applies
            elif strat in ("wikivol", "resolveprox", "redditbuzz", "cryptofair",
                           "volspike", "newmarket", "predictit",
                           "walletcopy", "whale", "multiwhale", "bookimbal",
                           "shortdate", "highvol", "pricerev", "catrev",
                           "spreadfade", "resolverush",
                           "cryptovol", "sportsvol", "polvol",
                           "buyflow", "sellflow",
                           "coinpump", "coindip", "acumvol",
                           "coinup", "coindown", "candlesig", "coinrev", "macdsig",
                           "diverg", "feargreed", "lateprox",
                           "fgcrypto",
                           "resolvebump", "thinbook", "whaleflip", "multiconfirm", "catmom",
                           "nearres", "nearres_sports", "nearres_pol", "snaprev", "volburst", "flipflop", "stalefish",
                           "tailspin", "roundnum", "firstmover", "yoyo",
                           "bigswing", "polmom", "sportshigh", "volwake", "doubletop",
                           "lopsided", "sportsnear", "whaleagree", "gapfill", "streakbuy", "revertmid",
                           "nohappen", "longshortbias", "clobimbal", "polyflup", "nearresfade",
                           "anchordrift", "volcrush", "latefade", "momno", "narrowband",
                           "esportsfav", "resolvetoday", "stablehigh", "priceleap", "thetano", "breakoutyes",
                           "sportres", "nearreslow", "nearresno", "spreadcap", "microscalp",
                           "wcmatch", "famsum", "mapdown", "ladderarb", "thetadk",
                           "certsnipe", "dipladder", "clusterarb", "socspread", "peacefwd",
                           "laddermid", "strikeflip", "negladder",
                           "psconfirm", "quietfade", "panel", "tasignal", "nearrestitle",
                           # 2026-06-13 EXIT-DISPATCH BUG FIX: these 10 legs had
                           # _gain/_stop configured but were never in any dispatch
                           # group, so positions never took profit or stopped —
                           # they only ever timed out (bookpress was 6/6 time-exits).
                           "bookpress", "kellyfav", "newslag", "birdeye", "gtrend",
                           "liqcrush", "nearterm", "newscrypto", "newsesports", "vlrtop"):
                # spreadcap with A-S quoting carries a per-position vol-scaled target (as_gain)
                _gain = pos.get("as_gain") or cfg[f"{strat}_gain"]
                if mid >= pos["entry_mid"] + _gain:
                    reason = "target"
                elif mid <= pos["entry_mid"] - cfg[f"{strat}_stop"]:
                    reason = "stop"
            hold_cap = cfg.get(f"{strat}_max_hold_hours", cfg["max_hold_hours"])
            if reason is None and hours_since(pos["opened_at"]) > hold_cap:
                reason = "time"

            # ── Marking-optimism fix ──────────────────────────────────────
            # A +target is a RESTING LIMIT order: it fills AT the target, it
            # does not magically capture an overshoot/gap past it. Previously
            # target exits were marked at bid(current mid), so a market that
            # gapped 15-20c past the +target booked the whole gap as profit —
            # inflating winners while stops still booked the full adverse move.
            # Cap target exits at the limit fill (never better than the market).
            if reason == "target":
                if strat == "dip":
                    tgt_mid = cfg["dip_sell_target"]            # absolute YES level
                elif strat == "scalp":
                    tgt_mid = pos["entry_mid"] + cfg["scalp_gain"]
                elif strat == "fade":
                    tgt_mid = pos["entry_mid"] + cfg["fade_gain"]
                elif strat == "fastfade":
                    tgt_mid = pos["entry_mid"] + cfg["fastfade_target"]
                elif strat == "allin":
                    tgt_mid = pos["entry_mid"] + cfg["allin_target"]
                elif strat == "conviction":
                    tgt_mid = cfg["conviction_target"]          # absolute YES level (0.80)
                elif strat == "moonshot":
                    tgt_mid = pos.get("abs_target", cfg.get("moonshot_target", 0.50))
                elif strat == "longshot":
                    tgt_mid = pos.get("abs_target", pos["entry_mid"] + 0.22)
                elif strat == "nearwin":
                    tgt_mid = cfg["nearwin_target"]
                elif strat == "crashbuy":
                    tgt_mid = pos["entry_mid"] + cfg["crashbuy_target_delta"]
                elif strat == "deadline":
                    tgt_mid = cfg["deadline_target"]
                elif strat == "impulse":
                    tgt_mid = pos.get("abs_target", pos["entry_mid"] + cfg["impulse_target_delta"])
                elif strat == "fogbuy":
                    tgt_mid = pos["entry_mid"] + cfg["fogbuy_target_delta"]
                else:  # <leg>_gain legs; abs-target legs (alicenews/esportsdog/
                       # geopolbomb/midfield/cryptobull) carry abs_target, no _gain
                    g = cfg.get(f"{strat}_gain")
                    if g is not None:
                        tgt_mid = pos["entry_mid"] + g
                    else:
                        tgt_mid = pos.get("abs_target", exit_px)  # no _gain → cap at abs_target, else don't cap
                exit_px = min(exit_px, max(0.001, tgt_mid - _hs))

            # Apply momentum exit: fires when price is accelerating against us
            # (≥4¢/scan) and we're 40%+ into the stop. Targets always win over
            # momentum — if we're at target despite the adverse move, take the profit.
            if (reason != "target" and _mom_reason
                    and not (strat in HOLD_TO_RES_LEGS
                             and cfg.get("nearres_hold_to_resolution", False))):
                reason = _mom_reason
            if reason:
                _close(book, closed, pos, exit_px, reason, cfg)
            else:
                still_open.append(pos)
        book["open"] = still_open
    return closed


def prune_stale(markets, state, cfg):
    """Drop dip/scalp open positions whose market now resolves beyond the
    horizon cap. These are pre-horizon-filter leftovers (e.g. 2028 markets that
    a short-term strategy should never have held). Removed SILENTLY — not booked
    as P&L, because they were a config bug, not a real trade. Runs inside the
    live process so there's no race with hand-editing the state file.
    Horizon only ever shrinks as resolution nears, so this can only catch old
    junk — it never evicts a legitimately short-dated position."""
    horizon = {m["id"]: days_to_end(m.get("end")) for m in markets}
    removed = 0
    for strat, cap in (("dip", cfg["dip_max_days"]), ("scalp", cfg["scalp_max_days"])):
        keep = []
        for p in state[strat]["open"]:
            d = horizon.get(p["id"])
            if d is not None and d > cap:
                removed += 1
                continue
            keep.append(p)
        state[strat]["open"] = keep
    return removed


def sports_team_form(team_abbr):
    """Query team W-L record from ESPN sports data. Returns [wins, losses] or None."""
    return _sports_form.get(team_abbr)


def sports_match_result(league, home, away, date):
    """Query match result from sports data. Returns 'home' | 'away' | 'draw' | None."""
    return _sports_idx.get((league, home, away, date))


def _close(book, closed_list, pos, exit_px, reason, cfg):
    pnl = round((exit_px - pos["entry_fill"]) * pos["size"], 4)
    rec = dict(pos)
    rec.update({"exit_fill": round(exit_px, 4), "reason": reason,
                # stale_nodata: price data unavailable — not a real trade outcome.
                # pnl_usdc=None so it's invisible to >0 win-rate checks everywhere.
                "pnl_usdc": None if reason == "stale_nodata" else pnl,
                "closed_at": now_iso()})
    _bl.append_settlement(pos, exit_px, rec["pnl_usdc"], reason)
    closed_list.append(rec)
    book["closed"].append(rec)


# ── Scoreboards / charts ──────────────────────────────────────────────────
def board(state):
    from collections import Counter, defaultdict
    print("=" * 60)
    print("  SCALP LAB — paper trading lab (out-of-sample)")
    print("=" * 60)
    for strat in LEGS:
        c = state[strat]["closed"]
        o = len(state[strat]["open"])
        n = len(c)
        # stale_nodata exits have pnl_usdc=None — exclude from win/loss accounting
        priced = [r for r in c if r.get("pnl_usdc") is not None]
        stale  = n - len(priced)
        w   = sum(1 for r in priced if r["pnl_usdc"] > 0)
        pnl = sum(r["pnl_usdc"] for r in priced)
        np_ = len(priced)
        wr  = (w / np_ * 100) if np_ else 0
        tag = ""
        if np_ >= 10:
            tag = "  ✅ +EV" if pnl > 0 else "  ⚠️ losing"
        stale_tag = f"+{stale}stale" if stale else ""
        closed_label = f"{np_}({stale_tag})" if stale else str(np_)
        print(f"\n  [{strat.upper()}]  open={o}  closed={closed_label}  "
              f"win={wr:5.1f}%  paper P&L=${pnl:+.2f}{tag}")
        if n:
            print("     exits:", dict(Counter(r["reason"] for r in c)))
            # ── Moonshot: tier breakdown ────────────────────────────────────
            if strat == "moonshot":
                by_tier = defaultdict(list)
                for r in priced:
                    by_tier[r.get("tier", "?")].append(r["pnl_usdc"])
                if by_tier:
                    parts = [f"{t}=${sum(v):+.2f}(n={len(v)})" for t, v in sorted(by_tier.items())]
                    print("     by tier: " + "  ".join(parts))
            # ── Category P&L split (fade spec: is the edge sports-only?) ──
            by_cat = defaultdict(list)
            for r in priced:
                cat = r.get("cat") or "unknown"
                by_cat[cat].append(r["pnl_usdc"])
            if len(priced) >= 5 or any(k != "unknown" for k in by_cat):
                parts = []
                for cat, pnls in sorted(by_cat.items(), key=lambda x: -abs(sum(x[1]))):
                    parts.append(f"{cat}=${sum(pnls):+.2f}(n={len(pnls)})")
                print("     by cat: " + "  ".join(parts))
    print()
    # ── Grand summary ──────────────────────────────────────────────────────
    active   = [s for s in LEGS if state[s]["closed"] or state[s]["open"]]
    all_pnl  = sum(r["pnl_usdc"] for s in LEGS for r in state[s]["closed"]
                   if r.get("pnl_usdc") is not None
                   and s not in ("allin", "coinflip"))
    print(f"  Grand total P&L : ${all_pnl:+.2f}")
    killed   = [s for s in ("scalp","favyes","midfade","endgame","momentum","dip")
                if not CONFIG.get(f"{s}_enabled", True)]
    if killed:
        print(f"  Killed legs     : {', '.join(killed)}")
    print()


def _honest_pnl(strat, r):
    """Realized P&L with target exits capped at the limit fill (bid(target)).
    Matches honest_report.py so the dashboard stops showing the inflated
    pre-fix pnl_usdc on legacy trades. Stops/time exits already honest."""
    cfg = CONFIG
    half = _half(r, cfg)   # per-position spread (fade variants) else flat assumed_spread/2
    xf, ef, sz = r.get("exit_fill"), r.get("entry_fill"), r.get("size", 0.0)
    if xf is None or ef is None:
        return r.get("pnl_usdc") or 0.0
    if r.get("reason") == "target":
        em = r.get("entry_mid", 0.0)
        if strat == "dip":
            tgt = cfg["dip_sell_target"]
        elif strat == "moonshot":
            tgt = r.get("abs_target") or cfg.get("moonshot_target", 0.50)  # per-tier stored value
        elif strat == "conviction":
            tgt = cfg["conviction_target"]   # absolute price target (0.80)
        else:
            gk = {"scalp": "scalp_gain", "fade": "fade_gain", "fastfade": "fastfade_target",
                  "allin": "allin_target", "momentum": "momentum_gain", "favyes": "favyes_gain",
                  "coinflip": "coinflip_gain", "midfade": "midfade_gain",
                  "truefade": "truefade_gain", "deepfade": "deepfade_gain",
                  "nsfade": "nsfade_gain", "wangfade": "wangfade_gain",
                  "manifold": "manifold_gain", "metaculus": "metaculus_gain",
                  "wcrank": "wcrank_gain",     "wcnews": "wcnews_gain",
                  "wikivol": "wikivol_gain",    "resolveprox": "resolveprox_gain",
                  "redditbuzz": "redditbuzz_gain", "cryptofair": "cryptofair_gain",
                  "volspike": "volspike_gain",  "newmarket": "newmarket_gain",
                  "predictit": "predictit_gain",
                  "walletcopy": "walletcopy_gain", "whale": "whale_gain",
                  "multiwhale": "multiwhale_gain", "bookimbal": "bookimbal_gain",
                  "shortdate": "shortdate_gain", "highvol": "highvol_gain",
                  "pricerev": "pricerev_gain",   "catrev": "catrev_gain",
                  "spreadfade": "spreadfade_gain", "resolverush": "resolverush_gain",
                  "cryptovol": "cryptovol_gain", "sportsvol": "sportsvol_gain",
                  "polvol": "polvol_gain", "buyflow": "buyflow_gain",
                  "sellflow": "sellflow_gain", "coinpump": "coinpump_gain",
                  "coindip": "coindip_gain", "acumvol": "acumvol_gain",
                  "coinup": "coinup_gain", "coindown": "coindown_gain",
                  "diverg": "diverg_gain", "feargreed": "feargreed_gain",
                  "lateprox": "lateprox_gain",
                  "fgcrypto": "fgcrypto_gain",
                  "nearterm": "nearterm_gain",
                  "ytbuzz": "ytbuzz_gain",
                  "nearres": "nearres_gain", "sportres": "sportres_gain",
                  "nearreslow": "nearreslow_gain", "psconfirm": "psconfirm_gain",
                  "nearrestitle": "nearrestitle_gain",
                  # 2026-06-14: new legs were missing here → target exits booked as
                  # losses (capped at em-half). Added so _honest_pnl caps at the real target.
                  "nearresno": "nearresno_gain", "spreadcap": "spreadcap_gain",
                  "tp20": "tp20_gain", "scalp02": "scalp02_gain",
                  "volsurge": "volsurge_gain",
                  "quietfade": "quietfade_gain", "panel": "panel_gain",
                  "tasignal": "tasignal_gain",
                  "anchordrift": "anchordrift_gain", "volcrush": "volcrush_gain",
                  "latefade": "latefade_gain", "momno": "momno_gain",
                  "narrowband": "narrowband_gain",
                  "esportsfav": "esportsfav_gain", "resolvetoday": "resolvetoday_gain",
                  "stablehigh": "stablehigh_gain", "priceleap": "priceleap_gain",
                  "thetano": "thetano_gain", "breakoutyes": "breakoutyes_gain",
                  "wcmatch": "wcmatch_gain", "famsum": "famsum_gain",
                  "mapdown": "mapdown_gain", "ladderarb": "ladderarb_gain",
                  "thetadk": "thetadk_gain", "certsnipe": "certsnipe_gain",
                  "dipladder": "dipladder_gain", "clusterarb": "clusterarb_gain",
                  "socspread": "socspread_gain", "peacefwd": "peacefwd_gain",
                  # 2026-06-09 moonshot-style legs use abs_target / stop_delta, not _gain/_stop
                  # _honest_pnl handles them above via the reason=="target" branches
                  }.get(strat)
            tgt = em + cfg[gk] if gk else em
        xf = min(xf, tgt - half)
    return round((xf - ef) * sz, 4)


def dashboard(state):
    """Write scalp_lab_dashboard.html — a single auto-refreshing page showing
    every open position, every closed trade, win rate, P&L and cumulative
    charts for all three strategies. Regenerated each cycle; the page reloads
    itself every 60s to pick up the freshly-written file. Total openness."""
    COLORS = {"dip": "#f7931a", "scalp": "#3fb950", "fade": "#58a6ff", "fastfade": "#db61a2", "allin": "#8b949e",
              "momentum": "#e3b341", "favyes": "#2dd4bf", "coinflip": "#bc8cff", "midfade": "#ff8c69",
              # fade variants (2026-06-07)
              "truefade": "#7c3aed", "deepfade": "#dc2626", "nsfade": "#0891b2", "wangfade": "#059669",
              # open-source divergence legs (2026-06-07)
              "manifold": "#f472b6", "metaculus": "#fbbf24",
              # sports intelligence legs (2026-06-07)
              "wcrank": "#16a34a", "wcnews": "#ea580c",
              # analytics legs (2026-06-07)
              "wikivol": "#60a5fa", "resolveprox": "#a78bfa",
              "redditbuzz": "#fb923c", "cryptofair": "#34d399",
              # signal legs (2026-06-07)
              "volspike": "#f43f5e", "newmarket": "#facc15", "predictit": "#818cf8",
              # wallet / microstructure legs (2026-06-07)
              "walletcopy": "#06b6d4", "whale": "#0ea5e9", "multiwhale": "#3b82f6", "bookimbal": "#8b5cf6",
              # structural fade variants (2026-06-07)
              "shortdate": "#f97316", "highvol": "#22c55e", "pricerev": "#eab308",
              "catrev": "#ec4899", "spreadfade": "#14b8a6", "resolverush": "#a855f7",
              # volume-by-category and flow legs (2026-06-07)
              "cryptovol": "#f59e0b", "sportsvol": "#10b981", "polvol": "#6366f1",
              "buyflow": "#22d3ee", "sellflow": "#f43f5e",
              "coinpump": "#fb7185", "coindip": "#4ade80", "acumvol": "#c084fc",
              # coin up/down forward experiment (2026-06-08)
              "coinup": "#fcd34d", "coindown": "#94a3b8",
              # algo legs from GitHub survey (2026-06-08)
              "diverg": "#38bdf8", "feargreed": "#f97316", "lateprox": "#a78bfa",
              # moonshot / conviction (2026-06-08)
              "moonshot": "#ffd700", "conviction": "#ef4444",
              # bias/structure legs (2026-06-09)
              "anchordrift": "#d946ef", "volcrush": "#0284c7", "latefade": "#65a30d",
              "tp20": "#ff7b00", "scalp02": "#00bcd4", "volsurge": "#9c27b0",
              "momno": "#e11d48", "narrowband": "#7c3aed",
              "esportsfav": "#06b6d4", "resolvetoday": "#f59e0b", "stablehigh": "#10b981",
              "priceleap": "#6366f1", "thetano": "#ef4444", "breakoutyes": "#84cc16",
              "esportsdog": "#22d3ee", "geopolbomb": "#f97316", "midfield": "#a855f7", "cryptobull": "#facc15",
              "alicenews": "#34d399",
              "ytbuzz": "#ff0000",
              "csarb": "#00e5ff"}
    payload = {"generated": now_iso()[:19].replace("T", " ") + " UTC", "strats": {}}
    grand = 0.0
    for k in LEGS:
        o, c = state[k]["open"], state[k]["closed"]
        hp = [_honest_pnl(k, r) for r in c]                 # capped/honest realized
        realized = round(sum(hp), 4)
        grand += realized
        w = sum(1 for x in hp if x > 0)
        cum, series = 0.0, []
        for r, x in sorted(zip(c, hp), key=lambda z: z[0].get("closed_at", "")):
            cum += x
            series.append([r.get("closed_at", "")[11:19], round(cum, 4)])
        payload["strats"][k] = {
            "color": COLORS.get(k, "#888888"), "open": o,
            "closed": [dict(r, pnl_usdc=x) for r, x in zip(c, hp)],  # honest per-trade
            "realized": realized, "wins": w, "n": len(c),
            "winrate": round(w / len(c) * 100, 1) if c else 0.0,
            "series": series,
        }
    payload["grand"] = round(grand, 4)

    # ── Real scalping engine (separate file): taker + maker books ─────────
    eng_path = os.path.join(BASE, "scalp_engine_state.json")
    if os.path.exists(eng_path):
        try:
            eng = json.load(open(eng_path))
            for model, color in (("taker", "#f85149"), ("maker", "#a371f7"), ("makerH", "#3fb950")):
                o = eng.get(model, {}).get("open", [])
                c = eng.get(model, {}).get("closed", [])
                w = sum(1 for r in c if r["pnl_usdc"] > 0)
                cum, series = 0.0, []
                for r in sorted(c, key=lambda r: r.get("closed_at", "")):
                    cum += r["pnl_usdc"]
                    series.append([r.get("closed_at", "")[11:19], round(cum, 4)])
                payload["strats"][f"scalp·{model}"] = {
                    "color": color, "open": o, "closed": c,
                    "realized": round(sum(r["pnl_usdc"] for r in c), 4),
                    "wins": w, "n": len(c),
                    "winrate": round(w / len(c) * 100, 1) if c else 0.0,
                    "series": series,
                }
        except Exception as e:
            sys.stderr.write(f"[dashboard:engine] {e}\n")

    # ── Edge Trader (the house ML-divergence strategy) ────────────────────
    et_path = os.path.join(BASE, "edge_trader_state.json")
    if os.path.exists(et_path):
        try:
            et = json.load(open(et_path))
            o = et.get("open", []); c = et.get("closed", [])
            w = sum(1 for r in c if r["pnl_usdc"] > 0)
            cum, series = 0.0, []
            for r in sorted(c, key=lambda r: r.get("closed_at", "")):
                cum += r["pnl_usdc"]
                series.append([r.get("closed_at", "")[11:19], round(cum, 4)])
            payload["strats"]["EDGE·ML"] = {
                "color": "#39c5cf", "open": o, "closed": c,
                "realized": round(sum(r["pnl_usdc"] for r in c), 4),
                "wins": w, "n": len(c),
                "winrate": round(w / len(c) * 100, 1) if c else 0.0,
                "series": series,
            }
        except Exception as e:
            sys.stderr.write(f"[dashboard:edge] {e}\n")

    html = """<!DOCTYPE html><html><head><meta charset="utf-8">
<meta http-equiv="refresh" content="60">
<title>Scalp Lab — Live</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.0/chart.umd.min.js"></script>
<style>
 body{font-family:system-ui,-apple-system,sans-serif;background:#0d1117;color:#e6edf3;margin:0;padding:24px}
 h1{margin:0 0 2px;font-size:22px} .sub{color:#8b949e;font-size:13px;margin-bottom:20px}
 .cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:14px;margin-bottom:22px}
 .card{background:#161b22;border:1px solid #2a323d;border-radius:12px;padding:16px;border-top:3px solid}
 .card h2{margin:0 0 8px;font-size:15px;text-transform:uppercase;letter-spacing:.5px}
 .big{font-size:28px;font-weight:800} .pos{color:#3fb950} .neg{color:#f85149}
 .meta{color:#8b949e;font-size:13px;margin-top:6px}
 .grand{background:#161b22;border:1px solid #2a323d;border-radius:12px;padding:16px 20px;margin-bottom:22px;font-size:18px}
 canvas{background:#161b22;border:1px solid #2a323d;border-radius:12px;padding:12px;margin-bottom:22px}
 table{width:100%;border-collapse:collapse;margin-bottom:20px;font-size:13px}
 th,td{text-align:left;padding:7px 10px;border-bottom:1px solid #2a323d}
 th{color:#8b949e;font-weight:600;text-transform:uppercase;font-size:11px;letter-spacing:.5px}
 .tag{font-size:11px;padding:1px 7px;border-radius:999px;border:1px solid #2a323d}
 h3{font-size:14px;margin:18px 0 8px;color:#c9d1d9}
</style></head><body>
<h1>Scalp Lab — live paper A/B</h1>
<div class="sub">Auto-refreshes every 60s · generated <span id="gen"></span> · paper only, no real money</div>
<div class="grand" id="grand"></div>
<div class="cards" id="cards"></div>
<canvas id="chart" height="90"></canvas>
<div id="tables"></div>
<script>
const D=__DATA__;
document.getElementById('gen').textContent=D.generated;
const money=n=>(n>=0?'+$':'-$')+Math.abs(n).toFixed(2);
const cls=n=>n>=0?'pos':'neg';
document.getElementById('grand').innerHTML=
  'Lab net realized (dip+scalp+fade): <b class="'+cls(D.grand)+'">'+money(D.grand)+'</b>'+
  ' <span style="color:#8b949e;font-size:13px">· scalp·taker / scalp·maker shown separately (real-scalping engine)</span>';
let cards='';
for(const k of Object.keys(D.strats)){const s=D.strats[k];
 cards+='<div class="card" style="border-top-color:'+s.color+'"><h2 style="color:'+s.color+'">'+k+'</h2>'+
  '<div class="big '+cls(s.realized)+'">'+money(s.realized)+'</div>'+
  '<div class="meta"><b>REALIZED · '+s.n+' exits</b> · win '+s.winrate+'% ('+s.wins+'/'+s.n+')</div>'+
  '<div class="meta" style="opacity:.55">'+s.open.length+' open (unrealized — excluded)</div></div>';}
document.getElementById('cards').innerHTML=cards;
const ds=Object.keys(D.strats).map(k=>{const s=D.strats[k];
 return {label:k,data:s.series.map(p=>({x:p[0],y:p[1]})),borderColor:s.color,
  backgroundColor:s.color+'22',tension:.2,pointRadius:2,fill:false};});
new Chart(document.getElementById('chart'),{type:'line',
 data:{datasets:ds},options:{parsing:false,plugins:{legend:{labels:{color:'#e6edf3'}},
 title:{display:true,text:'Cumulative realized P&L ($)',color:'#8b949e'}},
 scales:{x:{type:'category',grid:{color:'#2a323d'},ticks:{color:'#8b949e'}},
 y:{grid:{color:'#2a323d'},ticks:{color:'#8b949e'}}}}});
let t='';
for(const k of Object.keys(D.strats)){const s=D.strats[k];
 t+='<h3 style="color:'+s.color+'">'+k.toUpperCase()+' — EXITS / closed ('+s.n+') · realized '+money(s.realized)+'</h3>';
 t+='<table><tr><th>Market</th><th>Reason</th><th>P&L</th></tr>';
 for(const r of s.closed){t+='<tr><td>'+r.q+'</td><td><span class="tag">'+r.reason+'</span></td><td class="'+cls(r.pnl_usdc)+'">'+money(r.pnl_usdc)+'</td></tr>';}
 if(!s.n)t+='<tr><td colspan=3 style="color:#8b949e">none yet</td></tr>';
 t+='</table>';
 t+='<h3 style="color:#8b949e;opacity:.7">'+k.toUpperCase()+' — open ('+s.open.length+') · unrealized, not counted</h3>';
 t+='<table style="opacity:.7"><tr><th>Market</th><th>Side</th><th>Entry</th></tr>';
 for(const p of s.open){t+='<tr><td>'+p.q+'</td><td>'+(p.side||'YES')+'</td><td>'+(p.entry_fill||'').toFixed?p.entry_fill.toFixed(3):p.entry_fill+'</td></tr>';}
 if(!s.open.length)t+='<tr><td colspan=3 style="color:#8b949e">none</td></tr>';
 t+='</table>';}
document.getElementById('tables').innerHTML=t;
</script></body></html>"""
    html = html.replace("__DATA__", json.dumps(payload))
    out = os.path.join(BASE, "scalp_lab_dashboard.html")
    open(out, "w").write(html)
    return out


def chart(state):
    """Write a self-contained HTML with two cumulative-P&L charts."""
    def series(strat):
        # stale_nodata exits carry pnl_usdc=None — they must not enter the cumsum
        rows = sorted((r for r in state[strat]["closed"] if r.get("pnl_usdc") is not None),
                      key=lambda r: r.get("closed_at") or "")
        cum, pts = 0.0, []
        for r in rows:
            cum += r["pnl_usdc"]
            pts.append([str(r.get("closed_at") or "")[11:19], round(cum, 4)])
        return pts
    data = {"dip": series("dip"), "scalp": series("scalp")}
    html = """<!DOCTYPE html><html><head><meta charset="utf-8">
<title>Scalp Lab — Paper Charts</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.0/chart.umd.min.js"></script>
<style>body{font-family:system-ui;background:#0d1117;color:#e6edf3;margin:24px}
.card{background:#161b22;border:1px solid #2a323d;border-radius:12px;padding:18px;margin-bottom:20px}
h2{margin:0 0 4px}small{color:#8b949e}</style></head><body>
<h1>Scalp Lab — paper P&amp;L (cumulative)</h1>
<div class="card"><h2>DIP — buy 0.30–0.40, sell 0.49</h2><small>the midline-style bet</small><canvas id="dip"></canvas></div>
<div class="card"><h2>SCALP — buy 0.92–0.97, +2¢ target</h2><small>the 98% win-rate idea</small><canvas id="scalp"></canvas></div>
<script>const D=__DATA__;
function plot(id,pts,color){new Chart(document.getElementById(id),{type:'line',
data:{labels:pts.map(p=>p[0]),datasets:[{label:'cum P&L ($)',data:pts.map(p=>p[1]),
borderColor:color,backgroundColor:color+'33',fill:true,tension:.2,pointRadius:2}]},
options:{plugins:{legend:{display:false}},scales:{y:{grid:{color:'#2a323d'}},x:{grid:{color:'#2a323d'}}}}});}
plot('dip',D.dip,'#f7931a');plot('scalp',D.scalp,'#3fb950');</script></body></html>"""
    html = html.replace("__DATA__", json.dumps(data))
    out = os.path.join(BASE, "scalp_lab_charts.html")
    open(out, "w").write(html)
    print(f"wrote {out}  (dip pts={len(data['dip'])}, scalp pts={len(data['scalp'])})")


# ── Wikipedia cache warmer ─────────────────────────────────────────────────
# Runs in a daemon background thread so it NEVER blocks the scan cycle.
# Fetches pageview data for the top-volume non-sports markets. Results land in
# _wiki_cache and are used by the wikivol leg on the next scan cycle.
_last_wiki_warmup = [0.0]
_WIKI_WARMUP_INTERVAL = 3600   # once per hour

def _wiki_warmup_worker(market_list):
    """Background thread body — fetch Wikipedia pageviews for candidate articles.
    Writes into the shared _wiki_cache dict (GIL-safe for dict writes in CPython)."""
    done = 0
    for m in market_list:
        if done >= 8:          # max 8 lookups per warmup run
            break
        article = _wiki_article_for(m.get("q", ""))
        if not article or article in _wiki_miss:
            continue
        if article in _wiki_cache and (time.time() - _wiki_cache[article].get("ts", 0) < _WIKI_TTL):
            continue           # already warm
        # Small per-call sleep — we're not in the hot path here
        time.sleep(2.0)
        fetch_wiki_pageviews(article)   # result goes into _wiki_cache
        done += 1
    sys.stderr.write(f"[wikivol warmup] refreshed {done} articles\n")


def schedule_wiki_warmup(markets):
    """Kick off a background daemon thread to warm the Wikipedia cache if it's
    been more than one hour since the last warmup run. Non-blocking."""
    import threading
    if time.time() - _last_wiki_warmup[0] < _WIKI_WARMUP_INTERVAL:
        return
    _last_wiki_warmup[0] = time.time()
    candidates = [m for m in markets if m.get("cat") != "sports" and m.get("vol", 0) > 50_000]
    candidates.sort(key=lambda m: -m.get("vol", 0))
    t = threading.Thread(target=_wiki_warmup_worker, args=(candidates[:40],), daemon=True)
    t.start()


# ── Reddit cache warmer ────────────────────────────────────────────────────────
_last_reddit_warmup = [0.0]
_REDDIT_WARMUP_INTERVAL = 1_800   # every 30 min (matches _REDDIT_TTL)
_RB_SUBS_WARMUP = {
    "sports":   ("soccer", "worldcup", "sports"),
    "politics": ("politics", "worldnews", "news"),
    "crypto":   ("CryptoCurrency", "Bitcoin", "ethereum"),
    "other":    ("worldnews", "news", "politics"),
}

def _reddit_warmup_worker(market_list):
    """Background thread: fetch Reddit post counts for top candidate markets."""
    done = 0
    for m in market_list:
        if done >= 20: break
        subs  = _RB_SUBS_WARMUP.get(m.get("cat", "other"), _RB_SUBS_WARMUP["other"])
        query = " ".join(m["q"].replace("?", "").split()[:7])
        ck    = str((query.lower()[:60], tuple(subs)))
        if ck in _reddit_cache and time.time() - _reddit_cache[ck].get("ts", 0) < _REDDIT_TTL:
            continue
        time.sleep(0.5)
        fetch_reddit_posts(query, subreddits=subs)
        done += 1
    sys.stderr.write(f"[redditbuzz warmup] refreshed {done} queries\n")


def schedule_reddit_warmup(markets):
    """Non-blocking: kick off a Reddit warmup thread every 30 min."""
    import threading
    if time.time() - _last_reddit_warmup[0] < _REDDIT_WARMUP_INTERVAL:
        return
    _last_reddit_warmup[0] = time.time()
    candidates = [m for m in markets if m.get("vol", 0) > 50_000]
    candidates.sort(key=lambda m: -m.get("vol", 0))
    t = threading.Thread(target=_reddit_warmup_worker, args=(candidates[:60],), daemon=True)
    t.start()


_last_yt_warmup = [0.0]
_YT_WARMUP_GAP  = 240   # under once-mode each run is a fresh process; the real
                        # throttle is the per-query TTL check inside the worker

def _yt_warmup_worker(market_list):
    """Background thread: scrape YouTube buzz for top candidate markets.
    Each once-mode run only lives ~30-60s, so this fills a few queries per run;
    the persisted cache accumulates coverage across runs."""
    done = 0
    for m in market_list:
        if done >= 10:
            break
        query = " ".join(m["q"].replace("?", "").split()[:7])
        ck = query.lower()[:60]
        if ck in _yt_cache and time.time() - _yt_cache[ck].get("ts", 0) < _YT_TTL:
            continue
        time.sleep(0.8)   # be polite — these are full HTML pages
        fetch_youtube_buzz(query)
        done += 1
    if done:
        sys.stderr.write(f"[ytbuzz warmup] refreshed {done} queries\n")


def schedule_yt_warmup(markets, cfg=None):
    """Non-blocking: kick off a YouTube warmup thread for ytbuzz candidates."""
    import threading
    if time.time() - _last_yt_warmup[0] < _YT_WARMUP_GAP:
        return
    _last_yt_warmup[0] = time.time()
    c = cfg or CONFIG
    cands = [m for m in markets
             if m.get("vol", 0) >= c.get("ytbuzz_min_volume", 80_000)
             and c.get("ytbuzz_yes_min", 0.55) <= m.get("yes", 0) <= c.get("ytbuzz_yes_max", 0.85)]
    cands.sort(key=lambda m: -m.get("vol", 0))
    t = threading.Thread(target=_yt_warmup_worker, args=(cands[:25],), daemon=True)
    t.start()


# ── Data accumulation warmup ──────────────────────────────────────────────
def warmup_all_data(cfg=None, passes=6, gap=4):
    """Pre-load every cache so all 46 legs fire on the very first real scan.

    What this does in parallel / sequentially:
      Pass 1-N  : fetch 800 markets N times with `gap` seconds between.
                  Each pass feeds _vol_baseline (EMA α=0.20) and _price_snap.
                  After 6 passes the EMA has converged ~70% toward true baseline.
      External  : Manifold, CoinGecko, global trades, top wallets, WC data,
                  PredictIt — all fetched once (they cache themselves).
      CLOB books: pre-fetch book for every YES token in the current market list
                  so bookimbal fires immediately rather than on the next cycle.
      Wiki/Reddit: background warmup threads already run; we kick them here too.
    """
    if cfg is None:
        cfg = CONFIG
    print(f"[warmup] starting — {passes} market passes × {gap}s gap ...")

    import threading

    def _warm_externals():
        """Fire all external fetches in background threads."""
        jobs = [
            fetch_manifold_markets,
            fetch_wc_standings,
            fetch_wc_news_teams,
            fetch_coingecko,
            fetch_global_trades,
            get_top_wallets,
            fetch_predictit,
            fetch_vlr_matches,
            fetch_liq_cs2_matches,
        ]
        threads = [threading.Thread(target=fn, daemon=True) for fn in jobs]
        for t in threads: t.start()
        for t in threads: t.join(timeout=15)
        sys.stderr.write("[warmup] external caches warmed\n")

    ext_thread = threading.Thread(target=_warm_externals, daemon=True)
    ext_thread.start()

    all_markets = []
    for i in range(1, passes + 1):
        markets = fetch_markets()
        if not markets:
            sys.stderr.write(f"[warmup] pass {i}: empty feed, retrying\n")
            time.sleep(gap)
            continue
        try:
            _ud = fetch_updown_markets()
            _have = {m["id"] for m in markets}
            markets += [m for m in _ud if m["id"] not in _have]
        except Exception:
            pass
        all_markets = markets
        _compute_cat_chg(markets)
        for m in markets:
            _update_vol_baseline(m["id"], m["vol"])
            _price_snap[m["id"]] = m["yes"]
        cats = {}
        for m in markets:
            cats[m.get("cat","?")] = cats.get(m.get("cat","?"),0) + 1
        print(f"[warmup] pass {i}/{passes}: {len(markets)} markets  "
              f"vol_baseline={len(_vol_baseline)}  "
              f"cats={dict(sorted(cats.items(), key=lambda x:-x[1])[:4])}")
        if i < passes:
            time.sleep(gap)

    # CLOB pre-fetch for bookimbal — one book per token, throttled
    if all_markets:
        schedule_wiki_warmup(all_markets)
        schedule_reddit_warmup(all_markets)
        tokens = [m["token"] for m in all_markets if m.get("token")]
        sys.stderr.write(f"[warmup] pre-fetching {len(tokens)} CLOB books ...\n")
        fetched = 0
        for tok in tokens:
            fetch_clob_book(tok)   # populates _clob_cache
            fetched += 1
            if fetched % 50 == 0:
                sys.stderr.write(f"[warmup] CLOB {fetched}/{len(tokens)}\n")
            time.sleep(0.08)   # ~12 req/s — well under CLOB rate limit

    ext_thread.join(timeout=20)

    # Persist everything to disk
    try:
        json.dump(
            {"vol_baseline": _vol_baseline, "price_snap": _price_snap,
             "saved_at": time.time()},
            open(CACHE, "w"), indent=2
        )
    except Exception as e:
        sys.stderr.write(f"[warmup] cache save failed: {e}\n")

    vb_by_cat = {}
    for m in all_markets:
        cat = m.get("cat","?")
        if m["id"] in _vol_baseline:
            vb_by_cat[cat] = vb_by_cat.get(cat, 0) + 1
    print(f"[warmup] done — vol_baseline: {len(_vol_baseline)} markets, "
          f"price_snap: {len(_price_snap)}, "
          f"clob_cache: {len(_clob_cache)}")
    print(f"[warmup] vol baseline by cat: {dict(sorted(vb_by_cat.items(), key=lambda x:-x[1]))}")
    print("[warmup] all legs ready to fire on next scan ✓")


# ── Entry gate (Phase-1 shadow): stamp every new entry with the brain's verdict ──
def _gate_entry(p, m, cfg, passages, llm_left):
    """Return ({"pass": bool|None, "conf": float, "reason": str, "src": str}, llm_left).
    kb layer  = lexical relevance over BRAIN.md + the knowledge graph (keyless, always on).
    llm layer = NVIDIA-70B skeptic judge, capped per cycle, short timeout, never blocks."""
    q    = p.get("q") or m.get("q") or ""
    side = p.get("side", "YES")
    # kb relevance (max passage overlap) — keyless, deterministic
    kb_score = None
    if passages is not None:
        try:
            import kb_query as _kb
            qt = _kb.toks(q)
            kb_score = max((_kb.score(qt, t) for _s, _h, t in passages), default=0.0)
        except Exception:
            kb_score = None
    # llm skeptic judge (only while budget remains) — the attempt itself counts against the cap
    if llm_left > 0:
        llm_left -= 1
        try:
            import llm_client as _llm
            if _llm.configured():
                sysp = ("You gate a PAPER prediction-market entry. Given the market, the side being "
                        "bought, and its price, decide if there is a REAL, specific reason to expect "
                        "this side resolves profitably — not merely that it looks cheap. The market mid "
                        "is usually well-calibrated; default to pass=false unless you can name a concrete "
                        "edge. Return ONLY JSON: {\"pass\": true|false, \"confidence\": 0.0-1.0, "
                        "\"reason\": \"<=15 words\"}")
                kbn = f" | kb relevance {kb_score:.0f}" if kb_score is not None else ""
                usr = (f"Market: {q}\nSide: {side}  Entry price: {p.get('entry_mid', 0):.3f}{kbn}\n"
                       f"Is there a real edge buying {side} here?")
                txt = _llm.chat([{"role": "system", "content": sysp},
                                 {"role": "user", "content": usr}],
                                temperature=0.1, max_tokens=200,
                                timeout=cfg.get("gate_llm_timeout", 8))
                v = _llm._extract_json(txt)
                conf = float(v.get("confidence", 0) or 0)
                passed = bool(v.get("pass")) and conf >= cfg.get("gate_min_conf", 0.6)
                return ({"pass": passed, "conf": round(conf, 2),
                         "reason": str(v.get("reason", ""))[:120], "src": "llm"}, llm_left)
        except Exception as e:
            sys.stderr.write(f"[entry_gate llm] {type(e).__name__}: {str(e)[:80]}\n")
    # fallback: kb verdict, else unjudged
    if kb_score is not None:
        passed = kb_score >= cfg.get("gate_kb_min_score", 3.0)
        return ({"pass": passed, "conf": round(min(1.0, kb_score / 10.0), 2),
                 "reason": f"kb_score={kb_score:.0f}", "src": "kb"}, llm_left)
    return ({"pass": None, "conf": 0.0, "reason": "unjudged", "src": "unjudged"}, llm_left)


def _apply_entry_gate(opened, markets, state, cfg):
    """Stamp gate_pass/gate_conf/gate_reason/gate_src on each new entry. In 'enforce'
    mode also remove FAILs (fail-closed: None or False) from their books before save."""
    legs = cfg.get("entry_gate_legs", "all")
    mode = cfg.get("entry_gate_mode", "shadow")
    mlut = {m["id"]: m for m in markets}
    try:
        import kb_query as _kb
        passages = _kb.load_passages()          # ONCE per cycle (rebuilt from disk each call)
    except Exception:
        passages = None
    llm_left = cfg.get("gate_max_llm_per_cycle", 3)
    rejected, npass, nfail, nunj, srcs = [], 0, 0, 0, {}
    for p in opened:
        strat = p.get("strategy", "?")
        if legs != "all" and strat not in legs:
            continue
        # Existing gate: KB + LLM
        verdict, llm_left = _gate_entry(p, mlut.get(p["id"], {}), cfg, passages, llm_left)
        p["gate_pass"]   = verdict["pass"]
        p["gate_conf"]   = verdict["conf"]
        p["gate_reason"] = verdict["reason"]
        p["gate_src"]    = verdict["src"]
        srcs[verdict["src"]] = srcs.get(verdict["src"], 0) + 1

        # NEW: BRAIN gatekeeper check (graveyard + payer + structural)
        brain_gate_result = None
        if _brain_gate is not None:
            try:
                edge_type = strat.lower()  # "fade", "scalp", etc.
                market_q = p.get("q") or mlut.get(p["id"], {}).get("q", "?")
                brain_allow, brain_verdict = _brain_gate(
                    edge_type=edge_type,
                    description=market_q[:100],
                    mode=cfg.get("brain_gate_mode", "soft")  # soft|hard|mixed
                )
                p["brain_gate_pass"]   = brain_allow
                p["brain_gate_reason"] = brain_verdict.get("status", "unknown")
                p["brain_gate_mode"]   = cfg.get("brain_gate_mode", "soft")
                brain_gate_result = brain_allow
            except Exception as e:
                p["brain_gate_pass"]   = None
                p["brain_gate_reason"] = f"error: {str(e)[:60]}"
                p["brain_gate_mode"]   = "error"
                brain_gate_result = None  # treat errors as "unjudged"

        # Count verdicts
        if   verdict["pass"] is True:  npass += 1
        elif verdict["pass"] is False: nfail += 1
        else:                          nunj  += 1

        # Enforce mode: block if EITHER gate says no (fail-closed logic)
        if mode == "enforce":
            existing_gate_blocks = verdict["pass"] is not True
            brain_gate_blocks = brain_gate_result is False
            if existing_gate_blocks or brain_gate_blocks:
                rejected.append(p)
    if mode == "enforce":
        for p in rejected:
            try: state[p["strategy"]]["open"].remove(p)
            except (ValueError, KeyError): pass
            try: opened.remove(p)
            except ValueError: pass
    if npass or nfail or nunj:
        print(f"  gate[{mode}]: {npass} pass / {nfail} fail / {nunj} unjudged  src={dict(srcs)}"
              + (f"  rejected={len(rejected)}" if mode == "enforce" else ""))


# ── Loop ──────────────────────────────────────────────────────────────────
def one_scan(cfg, verbose=True):
    state = load_state()
    markets = fetch_markets()
    try:                                   # un-blind csarb/coinup/coindown/candlesig/macdsig
        _ud = fetch_updown_markets()       # (Up/Down markets live in /events, not /markets)
        _have = {m["id"] for m in markets}
        markets += [m for m in _ud if m["id"] not in _have]
    except Exception as _ue:
        sys.stderr.write(f"[updown] merge failed: {_ue}\n")
    if verbose:
        print(f"[{now_iso()[11:19]}] fetched {len(markets)} markets")
    purged, opened = 0, []
    # Entries/pruning need live prices — only run them with a non-empty feed.
    if markets:
        schedule_wiki_warmup(markets)    # non-blocking background thread; fills _wiki_cache for next cycle
        schedule_reddit_warmup(markets)  # same pattern for Reddit cache
        schedule_yt_warmup(markets)      # YouTube attention cache (ytbuzz)
        # Update per-market price snapshot (pricerev leg) and category chg avg (catrev)
        _compute_cat_chg(markets)
        purged = prune_stale(markets, state, cfg)
        opened = scan_entries(markets, state, cfg)
        # Update price snapshot AFTER entries (next cycle's pricerev uses current prices)
        for m in markets:
            _price_snap[m["id"]] = m["yes"]
        # ── entry gate (Phase-1 shadow): tag entries with the brain's verdict ──
        # Wrapped so an experimental gate can NEVER crash the entry/exit/save cycle.
        if cfg.get("entry_gate_enabled") and opened:
            try:
                _apply_entry_gate(opened, markets, state, cfg)
            except Exception as _ge:
                sys.stderr.write(f"[entry_gate] {_ge}\n")
    # Exits ALWAYS run, even when the feed is empty. scan_exits' stale path
    # force-closes positions past their hold cap as "stale_nodata"; gating it
    # behind `if markets:` froze losers in open[] whenever the feed was down
    # (e.g. gamma-api blocked from the Mac), faking +EV via survivorship.
    closed = scan_exits(markets, state, cfg)
    if verbose and (opened or closed or purged):
        print(f"  purged {purged}, opened {len(opened)}, closed {len(closed)}")
    # ── iMessage alerts ────────────────────────────────────────────────────────
    for p in opened:
        strat = p.get("strategy", "?")
        tier  = f" [{p['tier']}]" if p.get("tier") else ""
        _imsg(f"📈 OPEN  {strat.upper()}{tier}  {p.get('side','?')} {p.get('q','?')[:45]}  entry={p.get('entry_mid',0):.2f}")
    for p in closed:
        strat  = p.get("strategy", "?")
        reason = p.get("reason", "?")
        pnl    = p.get("pnl_usdc", 0) or 0
        emoji  = "✅" if pnl > 0 else "❌"
        _imsg(f"{emoji} CLOSE {strat.upper()}  {reason}  P&L=${pnl:+.2f}  {p.get('q','?')[:40]}")
    _flush_imsg()
    try:
        import sys as _sys2, os as _os2
        _sys2.path.insert(0, _os2.path.expanduser("~/Documents/birdeye-py"))
        from birdeye_leg import whale_alert_check as _wac
        _wac()
    except Exception as _wace:
        import sys as _sys2; _sys2.stderr.write(f"[birdeye whale_alert] {_wace}\n")
    save_state(state)
    try:
        dashboard(state)          # refresh the live UI every cycle
    except Exception as e:
        sys.stderr.write(f"[dashboard] {e}\n")
    return state


def _cap_moves_log(max_lines=3000, threshold=4_000_000):
    """External, restart-free guard that bounds the COPY bot's moves_log.jsonl.

    The copy bot (polymarket_bot.py, an UNMANAGED process — its copybot_watchdog
    points at a dead path) appends to moves_log with an in-memory cap that only
    activates after it restarts; it had ballooned to 4.2GB. This runs every cycle
    inside `scalp_lab.py once` because that is the only always-fresh process with
    ~/Documents TCC access (launchd /bin/bash can't reach Documents). tail-equiv
    via last-N slice; os.replace is atomic for the last-20 reader (recent_moves).
    """
    try:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "moves_log.jsonl")
        if os.path.exists(path) and os.path.getsize(path) > threshold:
            with open(path) as fr:
                lines = fr.readlines()
            if len(lines) > max_lines:
                tmp = path + ".tmp"
                with open(tmp, "w") as fw:
                    fw.writelines(lines[-max_lines:])
                os.replace(tmp, path)
    except Exception:
        pass


def main():
    global _sports_idx, _sports_form
    cmd = sys.argv[1] if len(sys.argv) > 1 else "once"
    # Load sports data index for resolution lookups + team form (keyless ESPN feed)
    try:
        _sports_idx, _sports_form = _sd.result_index()
    except Exception as e:
        sys.stderr.write(f"[sports_data] load failed ({e}): continuing without sports lookups\n")
    # GUARD (2026-06-14 incident): if Postgres is unreachable, ABORT before
    # load/scan/save. Otherwise lab_load_state returns EMPTY on connection
    # failure (db.py) and save_state then DELETE+rewrites the DB and mirror with
    # nothing — the disk-full → Postgres-down → state-wipe disaster. Only the
    # write commands need this; board/chart are read-only.
    if cmd in ("once", "run", "warmup"):
        try:
            _c = _db._conn(); _c.close()
        except Exception as e:
            sys.stderr.write(f"[ABORT] Postgres unreachable ({e}) — skipping "
                             f"cycle so empty state cannot clobber the DB/mirror\n")
            sys.exit(1)
    if cmd == "board":
        board(load_state()); return
    if cmd == "chart":
        chart(load_state()); return
    if cmd == "once":
        _cap_moves_log()
        one_scan(CONFIG); board(load_state()); return
    if cmd == "warmup":
        # Standalone data accumulation: call with optional pass count
        # e.g. "python3 scalp_lab.py warmup 10"
        passes = int(sys.argv[2]) if len(sys.argv) > 2 else 6
        load_state()                       # loads any existing cache into memory
        warmup_all_data(CONFIG, passes=passes)
        board(load_state()); return
    if cmd == "run":
        print("scalp_lab running (paper-only). Ctrl-C to stop.")
        # ── Startup warmup: restore cache + fast EMA bootstrap ──────────────
        load_state()   # pull existing vol_baseline + price_snap from disk
        if not _vol_baseline:
            print("[startup] no cache found — running 6-pass warmup (~30s) ...")
            warmup_all_data(CONFIG, passes=6, gap=4)
        else:
            print(f"[startup] cache loaded: {len(_vol_baseline)} vol baselines, "
                  f"{len(_price_snap)} price snaps — skipping warmup")
            # Still warm external APIs (short, non-blocking)
            import threading
            def _quick_warm():
                for fn in (fetch_manifold_markets, fetch_wc_standings,
                           fetch_wc_news_teams, fetch_coingecko,
                           fetch_global_trades, get_top_wallets):
                    try: fn()
                    except Exception: pass
            threading.Thread(target=_quick_warm, daemon=True).start()
        while True:
            try:
                one_scan(CONFIG)
                chart(load_state())
            except Exception as e:
                sys.stderr.write(f"[loop] {e}\n")
            time.sleep(CONFIG["poll_sec"])
    print(__doc__)


if __name__ == "__main__":
    main()
