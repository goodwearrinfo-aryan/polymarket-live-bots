#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════╗
║   POLYMARKET TOP-TRADER COPY BOT  (Self-Learning Edition)   ║
║                    v2 — Full Upgrade                         ║
╚══════════════════════════════════════════════════════════════╝

Three copy strategies + a self-improving learning engine:

  CONSENSUS  — 3+ top traders hold the same side of a market.
  INSIDER    — Top trader makes a sudden large bet on a thin market.
  MIDLINE    — Disabled (3.7% backtest WR — mean-reversion doesn't work).

  LEARNING   — Every signal is scored by historical win-rate before
                the bot decides to bet.  After every 20 closed trades
                the bot auto-tunes its own CONFIG thresholds.
                It also pulls resolved Polymarket markets to bootstrap
                learning from real history, not just its own trades.

v2 Upgrades:
  ⚡ Concurrent fetching  — 12 threads fetch 500 traders in parallel (10× faster)
  🏆 Trader-tier weighting — rank-1 traders count more than rank-500
  📐 Kelly criterion       — bet size scales with edge & confidence
  ⏱  Time-to-resolution   — only bet on markets resolving in 1–45 days
  📈 Trailing stop         — locks in gains, exits if price drops 8% from peak
  🗂  Category tracking    — brain learns sports / politics / crypto / finance
  🔄 Exponential backoff   — retries API calls with 2/4/8s delays on failure
  🎯 Per-strategy conf     — insider threshold stricter than consensus
  📉 Momentum veto         — skips signals where price is moving against us
  🏥 Spread quality filter — rejects broken/illiquid markets

Usage:
  python polymarket_bot.py --setup        # derive CLOB API creds (once)
  python polymarket_bot.py --dry-run      # simulate — no real money
  python polymarket_bot.py --bootstrap    # fetch 6 months of Poly history
  python polymarket_bot.py                # live (set dry_run=False first)

Files created at runtime:
  trade_log.json    — open + closed positions
  brain.json        — learned win-rate stats (the "memory")
  polymarket_bot.log
"""

import os
import sys
import json
import time
import logging
import argparse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, List, Tuple

import requests
from dotenv import load_dotenv
import db as _db

load_dotenv()

# ── Resolution-source "second voice" (fail-soft, optional) ──────────────────
# ml/resfeed.py reads the real-world fact a crypto-threshold market resolves on
# (live spot vs threshold), independent of the Polymarket price. Imported
# guarded so a missing module / import error never stops the bot.
try:
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "ml"))
    import resfeed as _resfeed
except Exception:
    _resfeed = None

try:
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import ApiCreds, OrderArgs, OrderType
    from py_clob_client.constants import POLYGON
    from py_clob_client.order_builder.constants import BUY, SELL
    _CLOB_AVAILABLE = True
except ImportError:
    # Dry-run mode works without py-clob-client.
    # Install it only when you want to place real orders:
    #   pip install py-clob-client
    _CLOB_AVAILABLE = False
    BUY  = "BUY"
    SELL = "SELL"
    POLYGON = 137

    class _Stub:
        """Placeholder — raises clearly if accidentally called in live mode."""
        def __init__(self, *a, **kw):
            raise RuntimeError(
                "py-clob-client is not installed. "
                "Run: pip install py-clob-client\n"
                "Or keep dry_run=True to simulate without real orders."
            )
    ClobClient = ApiCreds = OrderArgs = OrderType = _Stub

# ═══════════════════════════════════════════════════════════════════════════════
#  LOGGING
# ═══════════════════════════════════════════════════════════════════════════════
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s │ %(levelname)-8s │ %(message)s",
    handlers=[
        logging.FileHandler("polymarket_bot.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("pm_bot")

# ═══════════════════════════════════════════════════════════════════════════════
#  CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════
CONFIG: dict = {
    # ── Credentials ────────────────────────────────────────────────────────
    "private_key":    os.getenv("POLYMARKET_PRIVATE_KEY", ""),
    "api_key":        os.getenv("POLYMARKET_API_KEY", ""),
    "api_secret":     os.getenv("POLYMARKET_API_SECRET", ""),
    "api_passphrase": os.getenv("POLYMARKET_API_PASSPHRASE", ""),

    # ── Resolution-source second voice (logging only; does NOT change trades) ─
    # When on, every entry records the live resolution signal (res_margin/res_pyes/
    # res_has) so we can later measure whether it beats the market. No veto yet —
    # decision use is gated on that measurement.
    "resfeed_enabled":        True,

    # ── Safety ─────────────────────────────────────────────────────────────
    "dry_run":                True,   # paper until funded — flip False when depositing USDC
    "bet_size_usdc":          100.0,  # $100/trade (paper/dry-run simulation)
    "insider_bet_size_usdc":  50.0,   # $50 on insider signals
    "max_open_positions":     100,
    "max_daily_loss_usdc": 1_000_000_000.0,  # NO CAP (paper) — effectively disabled per user request
    # R:R FIX 2026-06-07 v2: diagnosis on 238 clean trades confirmed the problem:
    #   stop_loss  n=141  avg=-$13.97  (trigger -6%, fill -13.6% avg, worst -69.8%)
    #   take_profit n=94  avg=+$6.00   (exits working fine)
    # Math: need win rate > 14/(14+6) = 70%. Actual: 41%. Entire loss is stop slippage.
    # Fix: widen stop to 18% so only REAL losers stop out (noise spikes < 18% ignored).
    # With wider stop avg fill ~22%, avg win ~10% (trail activate raised below):
    #   breakeven = 22/(22+10) = 68.75% -- still needs good signals.
    # Secondary: raise min_confidence 0.48→0.55 to cut weakest entries.
    "take_profit_pct":     0.30,   # far backstop; trailing stop governs winner exits
    "stop_loss_pct":       0.18,   # was 0.06 → too tight, avg slippage hit -14% per stop

    # ── Trader tracking ─────────────────────────────────────────────────────
    "top_trader_count":      500,
    "leaderboard_window":    "all",
    # Win-rate metric DISABLED 2026-06-07: /positions is censored (winners get
    # redeemed & vanish, losers persist at price 0) -> measured "win rate" is biased
    # to ~0% (max 62% even for elite wallets). Unreliable, so NOT used to gate/boost.
    "winrate_boost_enabled":    False,
    "winrate_tracking_enabled": False,
    "winrate_min_resolved":     30,
    "winrate_threshold_pct":    90,
    # Quality boost. Measured 2026-06-07 on 227 closed trades: top-tier rank ≤0.20 is
    # the WORST bucket (-$12.44/trade, 29% win); no rank threshold is +EV. So (A)
    # rank-boost is DISABLED — would worsen P&L. (B) self-measured kept ENABLED but
    # PASSIVE (no behavioral effect until a wallet hits the trades+net-pos bar);
    # builds attribution data so we can see in days whether ANY wallet's signals
    # make us money. To activate B's boost effect set quality_boost_enabled=True.
    "quality_boost_enabled":    False,  # OFF — neither A nor B is validated yet
    "rank_boost_max_avg_rank":  0.00,   # (A) disabled (data: top-tier is worst bucket)
    "source_boost_min_trades":  8,      # (B) threshold to qualify as a "proven source"
    "quality_boost_conf":       0.05,
    "quality_boost_size_mult":  1.5,
    "refresh_traders_every": 3600,

    # ── Consensus strategy ─────────────────────────────────────────────────
    # The only reliably profitable strategy: 56.7% WR in backtests.
    # Given full access to all position slots so it can drive returns.
    # 2026-06-10 v3 quality boost: live data showed 32% WR vs 37.5% breakeven.
    # Raising trader count 5→7, share 0.65→0.72, volume 50k→150k to cut noise.
    "consensus_min_traders":   7,     # 5→7: 5-wallet consensus was still 32% WR; need stronger agreement
    "min_trader_trades":       100,   # only copy wallets with >100 observed trades (0 = off)
    "consensus_min_share":     0.72,  # 0.65→0.72: require tighter directional conviction
    "consensus_min_volume":    150_000, # 50k→150k: only liquid markets have real price discovery
    "consensus_max_positions": 90,    # consensus can fill almost all slots

    # ── Insider strategy ────────────────────────────────────────────────────
    # DISABLED 2026-06-10: live data 22% WR (9 trades), -$120 P&L — negative EV confirmed.
    # Was kept on short leash (3 consec loss pause) but never recovered.
    # Set max_positions=0 to fully disable until a new edge is measured.
    "insider_min_bet_usdc":             400,
    "insider_max_volume":               12_000,
    "insider_lookback_sec":             2700,
    "insider_max_price":                0.60,
    "insider_max_positions":            0,   # 10→0: DISABLED — 22% WR, confirmed negative EV
    # Circuit breaker: pause insider 72 h after 3 consecutive losses
    "insider_max_consecutive_losses":   3,
    "insider_pause_hours":              72,

    # ── Midline strategy ────────────────────────────────────────────────────
    # ENABLED by user request: buy YES at ≤0.48, sell at 0.50 (~4% scalp),
    # multiple positions allowed. NOTE: prior backtests showed only 3.7% WR
    # for this approach (prices tend to drift toward resolution 0/1, not back
    # to 0.50). Running in dry-run to see if live data agrees before going real.
    "midline_enabled":         False,   # disabled — midline strategy was 0 wins / 39 trades (-$4.77)
    "midline_buy_max":         0.48,
    "midline_sell_target":     0.50,
    "midline_min_volume":      7_000,
    "midline_max_positions":   25,
    "midline_min_traders":     2,    # ≥ this many top traders holding YES (pre-filter)
    "midline_max_candidates":  60,   # cap markets fetched per scan (prevents hang)

    # ── News / market-data feed (news_data.py) ───────────────────────────────
    # Pulls recent headlines (GDELT) + crypto prices (CoinGecko), no API key.
    # Fails soft: any fetch error -> neutral, never blocks a scan.
    "news_enabled":            True,    # master switch for the news layer
    "news_adjust_confidence":  True,    # nudge signal confidence by sentiment/momentum
    "news_filter_breaking":    True,    # skip trades during heavy breaking coverage
    "geo_min_yes_price":       0.25,    # geo markets: skip if YES < $0.25 — pump already ran, reversal risk
    "geo_min_wallets":         10,      # geo markets need 10+ wallets (5 is too concentrated to trust)
    "news_signals_enabled":    False,   # generate NEW trades from news alone (riskiest — off by default)
    "news_max_lookups":        25,      # cap news fetches per scan (keeps scans fast)
    "news_ttl_sec":            900,     # cache a market's news context for 15 min

    # ── General filters ──────────────────────────────────────────────────────
    "min_price":   0.35,   # raised from 0.08 — longshot bucket was 0 wins / 76 trades (-$8.32)
    "buy_max_avg_rank": 0.30,  # BUY only when traders are sharp (rank<0.30); BUY side lost -$30 otherwise
    "max_price":   0.65,   # 0.88→0.65: YES>0.65 (NO<0.35) was 0% WR — expensive NO = sucker bets

    # ── Learning engine ──────────────────────────────────────────────────────
    # Only raises min_confidence when the bot is losing — never lowers it
    # below the floor (lowering caused over-trading and losses).
    "min_confidence":          0.65,   # 0.60→0.65 — live data 32% WR at 0.60; need higher entry bar
    "min_confidence_floor":    0.65,   # auto-tune floor raised to match; never goes below this
    "autotune_every_n":        20,
    "min_bucket_trades":       5,
    #
    # How many months of Polymarket history to pull on bootstrap.
    "bootstrap_months":       6,

    # ── Timing ──────────────────────────────────────────────────────────────
    "scan_interval_sec": 20,          # full scan (leaderboard + signals) cadence
    "fast_monitor_interval_sec": 7,   # between scans, re-price held markets & check exits

    # ── Realistic fills (spread modeling) ────────────────────────────────────
    # Midpoint flatters paper P&L. With this on, entries fill at the ASK
    # (mid + half spread, you pay more) and positions are marked / exited at
    # the BID (mid - half spread, you receive less) — a conservative, more
    # honest estimate of real trading after crossing the bid-ask spread.
    "model_spread":    True,
    "assumed_spread":  0.02,   # total bid-ask spread assumed (1¢ each side)

    # ── Phone control + status (synced folder, e.g. iCloud Drive) ────────────
    # Bot writes status.json (monitor from phone) and reads control.json
    # (pause / resume / toggle midline by editing it in the phone Files app).
    "control_enabled": True,
    # Synced folder so the phone (Files app / iCloud Drive) can see status.json
    # and edit control.json. Auto-created. If you use Dropbox instead, change this.
    "control_dir":     "~/Library/Mobile Documents/com~apple~CloudDocs/PolymarketBot",

    # ── Kelly criterion bet sizing ───────────────────────────────────────────
    # Fractional Kelly: bet = kelly_fraction × (edge / odds) × bankroll
    # Capped at 2× normal bet size. Set kelly_fraction=0 to disable.
    "kelly_fraction":      0.0,     # DISABLED — flat $100/trade (bet_size_usdc)
    "kelly_bankroll_usdc": 100.0,   # assumed bankroll for sizing (update to match wallet)

    # ── Trailing stop ────────────────────────────────────────────────────────
    # Once a position has gained ≥ trailing_activate_pct, switch to a
    # trailing stop that closes if price drops trailing_stop_pct from its peak.
    "trailing_activate_pct": 0.10,   # was 0.05; arm trail at +10% — more room for winner to breathe
    "trailing_stop_pct":     0.08,   # close if drops 8% from peak (locks in the run)

    # ── Time-to-resolution filter ────────────────────────────────────────────
    # Only bet on markets resolving between these many days from now.
    # Floor lowered to ~1h (0.04d) so the bot CAN enter sub-24h markets — these
    # resolve fast and start populating the win/loss record quickly. The small
    # floor still avoids markets pinning in the final minutes (no edge left).
    "resolution_min_days": 1.0,   # 0.04→1.0: sub-24h markets are near-resolution noise; no edge there
    "resolution_max_days": 45,

    # ── Sports-specific guards ────────────────────────────────────────────────
    # Sports markets (tennis, football, basketball) are harder to predict.
    # Require more traders AND a lower avg rank (more elite agreement) before entry.
    "sports_min_traders":   8,     # 3→8: sports markets showed 0-40% WR — need much stronger consensus
    "sports_max_avg_rank":  0.15,  # 0.30→0.15: only enter sports if very elite wallets agree

    # ── Per-strategy confidence thresholds ───────────────────────────────────
    # Insider gets a stricter filter since it has higher variance.
    "min_confidence_consensus": 0.65,  # 0.60→0.65: aligned with raised global threshold
    "min_confidence_insider":   0.70,  # 0.55→0.70: insider disabled but threshold raised if re-enabled

    # ── Concurrency ──────────────────────────────────────────────────────────
    "fetch_workers": 48,   # threads for parallel position/activity fetching (activity uses half)
}

CLOB_HOST = "https://clob.polymarket.com"
DATA_API  = "https://data-api.polymarket.com"
GAMMA_API = "https://gamma-api.polymarket.com"

# ═══════════════════════════════════════════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _get(url: str, params: dict = None, timeout: int = 8, _retries: int = 1):
    """GET with exponential back-off.  Returns parsed JSON or None."""
    delay = 2
    for attempt in range(_retries):
        try:
            r = requests.get(url, params=params, timeout=timeout)
            if r.status_code == 429:
                wait = delay * (2 ** attempt)
                log.debug(f"Rate-limited {url} — retrying in {wait}s")
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r.json()
        except requests.HTTPError as e:
            log.debug(f"HTTP error {url}: {e}")
            return None
        except requests.RequestException as e:
            if attempt < _retries - 1:
                wait = delay * (2 ** attempt)
                log.debug(f"Request failed {url} — retrying in {wait}s ({e})")
                time.sleep(wait)
            else:
                log.debug(f"HTTP {url}: {e}")
    return None

# Hang-watchdog progress hook (wired up by run()). Every market fetch calls
# this, so a slow-but-progressing scan keeps the watchdog satisfied while a
# genuine wedge (no progress at all) still trips it. Default is a no-op.
_WATCHDOG = {"beat": (lambda: None)}

def _now() -> int:
    return int(time.time())

def _vol_bucket(volume: float) -> str:
    """Bucket market volume for pattern matching."""
    if volume < 5_000:    return "micro"
    if volume < 20_000:   return "thin"
    if volume < 100_000:  return "medium"
    return "deep"

def _price_bucket(price: float) -> str:
    """Bucket price for pattern matching."""
    if price < 0.15:  return "longshot"
    if price < 0.40:  return "low"
    if price < 0.60:  return "midrange"
    if price < 0.80:  return "high"
    return "nearCertain"

def _trader_bucket(count: int) -> str:
    if count < 3:  return "1-2"
    if count < 6:  return "3-5"
    if count < 11: return "6-10"
    return "11+"

def _category_from_market(mkt: dict) -> str:
    """Classify a Polymarket market into a broad category for brain tracking."""
    tags = mkt.get("tags", []) or []
    title = (mkt.get("question") or mkt.get("title") or "").lower()

    # Tags take priority — they're more reliable than keyword matching
    for tag in tags:
        t = str(tag.get("label", tag) if isinstance(tag, dict) else tag).lower()
        if any(k in t for k in ("sport", "soccer", "football", "nba", "nfl",
                                 "tennis", "cricket", "esport", "boxing", "mma",
                                 "hockey", "baseball", "rugby", "golf")):
            return "sports"
        if any(k in t for k in ("politic", "elect", "president", "congress",
                                 "senate", "govern", "vote", "referendum")):
            return "politics"
        if any(k in t for k in ("crypto", "bitcoin", "eth", "btc", "defi",
                                 "nft", "blockchain", "token", "coin")):
            return "crypto"
        if any(k in t for k in ("entertain", "movie", "music", "celebrity",
                                 "tv", "award", "oscar", "grammy")):
            return "entertainment"
        if any(k in t for k in ("econ", "finance", "market", "rate", "gdp",
                                 "inflation", "fed", "stock")):
            return "finance"

    # Fallback: keyword scan on title
    if any(k in title for k in ("bitcoin", " eth ", "crypto", " btc",
                                 " coin ", "defi", "nft", "blockchain")):
        return "crypto"
    if any(k in title for k in ("election", "president", "senate", "congress",
                                 "parliament", "vote", "trump", "harris",
                                 "biden", "governor")):
        return "politics"
    if any(k in title for k in ("win", "champion", "cup", "league", "match",
                                 " game ", "playoff", "score", " team ",
                                 "tournament", "championship", "vs ", " v ")):
        return "sports"
    if any(k in title for k in ("fed rate", "gdp", "inflation", "recession",
                                 "nasdaq", "s&p", "earnings")):
        return "finance"
    return "other"


# ═══════════════════════════════════════════════════════════════════════════════
#  LEARNING ENGINE
#  The bot's "brain". Scores signals, records outcomes, auto-tunes CONFIG.
# ═══════════════════════════════════════════════════════════════════════════════

class LearningEngine:
    """
    Maintains a performance database (brain.json) that tracks win/loss counts
    across multiple feature dimensions.  Each signal is scored before the bot
    decides to bet — only signals above `min_confidence` go through.

    Feature dimensions tracked:
        strategy        — "consensus" | "insider"
        price_bucket    — longshot / low / midrange / high / nearCertain
        vol_bucket      — micro / thin / medium / deep
        trader_bucket   — 1-2 / 3-5 / 6-10 / 11+

    Score = weighted average of win-rates across all dimensions.
    If a dimension has fewer than `min_bucket_trades` samples, it contributes
    with half-weight (Laplace smoothing keeps us from over-trusting thin data).

    Auto-tuning:
        Every N closed trades, the engine examines which buckets win most often
        and tightens CONFIG thresholds to steer future signals toward them.
    """

    BRAIN_FILE = "brain.json"

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.db: dict = self._load()
        self._trades_since_tune = 0

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load(self) -> dict:
        # Full default schema. Any key missing from an old/minimal/corrupt
        # brain.json is filled in here so the rest of the engine never hits a
        # KeyError (e.g. 'total_wins', 'by_strategy', 'live_wins').
        defaults = {
            "by_strategy":     {},   # strategy → {wins, losses}
            "by_price":        {},   # price_bucket → {wins, losses}
            "by_volume":       {},   # vol_bucket → {wins, losses}
            "by_trader_count": {},   # trader_bucket → {wins, losses}
            "by_combo":        {},   # "strategy|price|vol" → {wins, losses}
            "by_category":     {},   # sports/politics/crypto/finance/other
            "total_wins":      0,    # all-time (incl. bootstrap)
            "total_losses":    0,
            "live_wins":       0,    # live only — used for autotune
            "live_losses":     0,
            "autotune_log":    [],
        }
        try:
            with open(self.BRAIN_FILE) as f:
                data = json.load(f)
            if not isinstance(data, dict):
                data = {}
        except (FileNotFoundError, json.JSONDecodeError):
            data = {}
        # Merge: guarantee every expected key exists without clobbering saved values.
        for k, v in defaults.items():
            data.setdefault(k, v)
        return data

    def save(self):
        try:
            with open(self.BRAIN_FILE, "w") as f:
                json.dump(self.db, f, indent=2)
        except Exception as e:
            log.error(f"Brain save failed: {e}")

    # ── Core win-rate helper ─────────────────────────────────────────────────

    def _wr(self, bucket_dict: dict, key: str) -> Tuple[float, int]:
        """
        Returns (win_rate, sample_count) for a bucket key.
        Uses Laplace smoothing: assume 1 win + 1 loss if we have no data.
        """
        stats = bucket_dict.get(key, {})
        w = stats.get("wins", 0)
        l = stats.get("losses", 0)
        n = w + l
        # Laplace smoothed rate
        rate = (w + 1) / (n + 2)
        return rate, n

    # ── Signal scoring ────────────────────────────────────────────────────────

    def score_signal(self, sig: dict) -> float:
        """
        Returns a confidence score 0.0–1.0.
        Score = weighted average of win-rates across dimensions,
        multiplied by a trader-tier bonus (top-ranked traders score higher).

        Dimensions with < min_bucket_trades samples get half weight.
        """
        strategy = sig.get("strategy", "?")
        pb       = _price_bucket(sig.get("price", 0.5))
        vb       = _vol_bucket(sig.get("volume", 0))
        tb       = _trader_bucket(sig.get("trader_count", 0))
        cat      = sig.get("category", "other")
        combo    = f"{strategy}|{pb}|{vb}"

        # Ensure by_category exists (backwards-compat with old brain.json files)
        if "by_category" not in self.db:
            self.db["by_category"] = {}

        min_n = self.cfg["min_bucket_trades"]

        dimensions = [
            (self.db["by_strategy"],     strategy, 1.5),  # (dict, key, weight_mult)
            (self.db["by_price"],        pb,        1.0),
            (self.db["by_volume"],       vb,        1.0),
            (self.db["by_trader_count"], tb,        1.0),
            (self.db["by_combo"],        combo,     1.2),  # combo is most specific
            (self.db["by_category"],     cat,       0.8),  # category adds mild signal
        ]

        total_weight = 0.0
        weighted_sum = 0.0

        for bucket_dict, key, wm in dimensions:
            rate, n = self._wr(bucket_dict, key)
            # Full weight once we have enough samples, half weight otherwise
            weight = wm if n >= min_n else wm * 0.5
            weighted_sum += rate * weight
            total_weight  += weight

        base_score = weighted_sum / total_weight if total_weight > 0 else 0.5

        # ── Trader-tier bonus ─────────────────────────────────────────────
        # avg_rank_pct: 0.0 = rank-1 traders, 1.0 = rank-500 traders
        # Bonus ranges from +5% (top traders) to 0% (bottom of list)
        avg_rank_pct = sig.get("avg_rank_pct", 0.5)
        tier_bonus   = 0.05 * (1.0 - avg_rank_pct)
        score        = min(base_score + tier_bonus, 0.99)

        return round(score, 4)

    # ── Recording outcomes ────────────────────────────────────────────────────

    def record_outcome(self, sig_snapshot: dict, won: bool,
                       from_bootstrap: bool = False):
        """
        Called when a position closes.  Updates all dimension buckets.
        sig_snapshot must contain: strategy, price, volume, trader_count.
        Set from_bootstrap=True for historical pre-loading so bootstrap
        data doesn't bias the live autotune win-rate calculation.
        """
        strategy = sig_snapshot.get("strategy", "?")
        pb       = _price_bucket(sig_snapshot.get("price", 0.5))
        vb       = _vol_bucket(sig_snapshot.get("volume", 0))
        tb       = _trader_bucket(sig_snapshot.get("trader_count", 0))
        cat      = sig_snapshot.get("category", "other")
        combo    = f"{strategy}|{pb}|{vb}"
        outcome  = "wins" if won else "losses"

        # Backwards-compat with old brain.json files
        if "by_category" not in self.db:
            self.db["by_category"] = {}

        for bucket_dict, key in [
            (self.db["by_strategy"],     strategy),
            (self.db["by_price"],        pb),
            (self.db["by_volume"],       vb),
            (self.db["by_trader_count"], tb),
            (self.db["by_combo"],        combo),
            (self.db["by_category"],     cat),
        ]:
            if key not in bucket_dict:
                bucket_dict[key] = {"wins": 0, "losses": 0}
            bucket_dict[key][outcome] += 1

        if won:
            self.db["total_wins"] += 1
        else:
            self.db["total_losses"] += 1

        # Track live trades separately so bootstrap data doesn't skew autotune
        if not from_bootstrap:
            if won:
                self.db.setdefault("live_wins", 0)
                self.db["live_wins"] += 1
            else:
                self.db.setdefault("live_losses", 0)
                self.db["live_losses"] += 1

            self._trades_since_tune += 1

        self.save()

        if not from_bootstrap:
            live_w = self.db.get("live_wins", 0)
            live_l = self.db.get("live_losses", 0)
            live_n = live_w + live_l
            wr = live_w / live_n * 100 if live_n else 0
            log.info(f"Brain updated → live {wr:.1f}% win rate "
                     f"({live_w}W / {live_l}L live, "
                     f"{self.db['total_wins']}W total)")

            if self._trades_since_tune >= self.cfg["autotune_every_n"]:
                self._trades_since_tune = 0
                self.autotune(self.cfg)

    # ── Bootstrap from Polymarket history ─────────────────────────────────────

    def bootstrap_from_polymarket(self, months: int = 6):
        """
        Fetches resolved markets from Gamma API and checks what the top-trader
        consensus was at resolution time.  Records wins/losses so the brain
        starts with real historical data instead of empty buckets.

        This only needs to run once (--bootstrap flag).
        """
        log.info(f"Bootstrapping brain from {months} months of Polymarket history…")
        since = datetime.utcnow() - timedelta(days=30 * months)
        since_str = since.strftime("%Y-%m-%dT%H:%M:%S")

        fetched = 0
        cursor  = ""
        while True:
            params = {
                "closed":      "true",
                "limit":       100,
                "end_date_min": since_str,
            }
            if cursor:
                params["cursor"] = cursor

            data = _get(f"{GAMMA_API}/markets", params=params)
            if not data:
                break

            markets = data if isinstance(data, list) else data.get("markets", [])
            if not markets:
                break

            for mkt in markets:
                self._learn_from_resolved_market(mkt)
                fetched += 1

            cursor = (data.get("next_cursor") or "") if isinstance(data, dict) else ""
            if not cursor or cursor == "LTE=":
                break
            time.sleep(0.3)   # rate limit

        self.save()
        log.info(f"Bootstrap complete — processed {fetched} resolved markets")
        log.info(f"Brain now has {self.db['total_wins']}W / "
                 f"{self.db['total_losses']}L from history")

    def _learn_from_resolved_market(self, mkt: dict):
        """
        For a resolved market, figure out the winning outcome and estimate
        whether a consensus/insider bet would have won.
        Records the result into the brain.
        """
        # Market outcome
        winner = (mkt.get("winner") or "").upper()
        if winner not in ("YES", "NO"):
            return   # unresolved or unknown

        tokens  = mkt.get("tokens", [])
        vol     = float(mkt.get("volume") or mkt.get("volumeNum") or 0)
        if vol < 1000:
            return   # too thin to be informative

        # Try to find the YES price at time of resolution
        yes_price = 0.5
        for tok in tokens:
            if tok.get("outcome", "").upper() == "YES":
                yes_price = float(tok.get("price") or 0.5)
                break

        pb = _price_bucket(yes_price)
        vb = _vol_bucket(vol)

        # Simulate: would a consensus YES bet have won?
        if yes_price < 0.93:
            won = (winner == "YES")
            fake_sig = {
                "strategy":      "consensus",
                "price":         yes_price,
                "volume":        vol,
                "trader_count":  4,   # assume typical consensus count
            }
            self.record_outcome(fake_sig, won, from_bootstrap=True)

        # Simulate: would a consensus NO bet have won?
        no_price = 1.0 - yes_price
        if no_price < 0.93 and no_price > 0.05:
            won = (winner == "NO")
            fake_sig = {
                "strategy":      "consensus",
                "price":         no_price,
                "volume":        vol,
                "trader_count":  4,
            }
            self.record_outcome(fake_sig, won, from_bootstrap=True)

    # ── Auto-tuning ───────────────────────────────────────────────────────────

    def autotune(self, cfg: dict):
        """
        Examines brain stats and tightens CONFIG thresholds to match what
        has actually been profitable.  Changes are logged and saved.
        """
        log.info("═" * 50)
        log.info("  AUTO-TUNE: analysing brain and adjusting thresholds…")
        changes = {}
        min_n = self.cfg["min_bucket_trades"]

        # ── 1. Prefer the winning strategy ────────────────────────────────
        for strategy in ("consensus", "insider"):
            wr, n = self._wr(self.db["by_strategy"], strategy)
            if n >= min_n:
                log.info(f"  {strategy}: {wr:.1%} win rate ({n} trades)")

        # ── 2. Best price buckets ──────────────────────────────────────────
        best_price_wr  = 0.0
        worst_price_wr = 1.0
        for key, stats in self.db["by_price"].items():
            n = stats["wins"] + stats["losses"]
            if n < min_n:
                continue
            wr = stats["wins"] / n
            if wr > best_price_wr:
                best_price_wr = wr
            if wr < worst_price_wr:
                worst_price_wr = wr

        # Tighten min_price if longshots are losing
        lshot_wr, lshot_n = self._wr(self.db["by_price"], "longshot")
        if lshot_n >= min_n and lshot_wr < 0.45:
            new_min = min(cfg["min_price"] + 0.02, 0.35)
            if new_min != cfg["min_price"]:
                log.info(f"  min_price: {cfg['min_price']} → {new_min} "
                         f"(longshots losing at {lshot_wr:.1%})")
                cfg["min_price"] = new_min
                changes["min_price"] = new_min

        # Tighten max_price if near-certain bets are losing
        nc_wr, nc_n = self._wr(self.db["by_price"], "nearCertain")
        if nc_n >= min_n and nc_wr < 0.45:
            new_max = max(cfg["max_price"] - 0.02, 0.65)   # floor at 0.65 — never let auto-tune drift back to 0.88
            if new_max != cfg["max_price"]:
                log.info(f"  max_price: {cfg['max_price']} → {new_max} "
                         f"(near-certs losing at {nc_wr:.1%})")
                cfg["max_price"] = new_max
                changes["max_price"] = new_max

        # ── 3. Volume buckets ──────────────────────────────────────────────
        micro_wr, micro_n = self._wr(self.db["by_volume"], "micro")
        if micro_n >= min_n and micro_wr < 0.45:
            new_vol = min(cfg["consensus_min_volume"] + 5_000, 100_000)
            log.info(f"  consensus_min_volume → {new_vol:,} "
                     f"(micro markets losing at {micro_wr:.1%})")
            cfg["consensus_min_volume"] = new_vol
            changes["consensus_min_volume"] = new_vol

        deep_wr, deep_n = self._wr(self.db["by_volume"], "deep")
        if deep_n >= min_n and deep_wr > 0.60:
            # Deep markets outperforming — lower the floor to include more
            new_vol = max(cfg["consensus_min_volume"] - 2_000, 50_000)  # floor at 50k — never go back to 3k
            log.info(f"  consensus_min_volume → {new_vol:,} "
                     f"(deep markets winning at {deep_wr:.1%})")
            cfg["consensus_min_volume"] = new_vol
            changes["consensus_min_volume"] = new_vol

        # ── 4. Trader count ────────────────────────────────────────────────
        # If 3-5 traders bucket has good win rate, keep threshold at 3 or 4.
        small_wr, small_n = self._wr(self.db["by_trader_count"], "3-5")
        large_wr, large_n = self._wr(self.db["by_trader_count"], "6-10")

        if small_n >= min_n and large_n >= min_n:
            if large_wr > small_wr + 0.10:
                # Larger consensus clearly better — raise threshold
                new_t = min(cfg["consensus_min_traders"] + 1, 8)
                if new_t != cfg["consensus_min_traders"]:
                    log.info(f"  consensus_min_traders: "
                             f"{cfg['consensus_min_traders']} → {new_t} "
                             f"(big consensus wins {large_wr:.1%} vs {small_wr:.1%})")
                    cfg["consensus_min_traders"] = new_t
                    changes["consensus_min_traders"] = new_t
            elif small_wr > large_wr + 0.10:
                # Smaller consensus doing fine — lower threshold to get more bets
                new_t = max(cfg["consensus_min_traders"] - 1, 3)
                if new_t != cfg["consensus_min_traders"]:
                    log.info(f"  consensus_min_traders: "
                             f"{cfg['consensus_min_traders']} → {new_t}")
                    cfg["consensus_min_traders"] = new_t
                    changes["consensus_min_traders"] = new_t

        # ── 5. min_confidence — use LIVE trades only, not bootstrap ──────
        total_w = self.db.get("live_wins", 0)
        total_l = self.db.get("live_losses", 0)
        total_n = total_w + total_l
        if total_n >= 20:
            overall_wr = total_w / total_n
            floor = cfg.get("min_confidence_floor", 0.52)
            # Only RAISE min_confidence when bot is losing — never lower it
            # below the floor (lowering let in too many bad signals).
            if overall_wr < 0.45:
                new_conf = min(cfg["min_confidence"] + 0.03, 0.72)
                if new_conf != cfg["min_confidence"]:
                    log.info(f"  min_confidence → {new_conf} "
                             f"(bot struggling at {overall_wr:.1%})")
                    cfg["min_confidence"] = new_conf
                    changes["min_confidence"] = new_conf
            elif overall_wr > 0.62 and cfg["min_confidence"] > floor:
                # Bot is doing well — allow a tiny relaxation, but never below floor
                new_conf = max(cfg["min_confidence"] - 0.01, floor)
                if new_conf != cfg["min_confidence"]:
                    log.info(f"  min_confidence → {new_conf} "
                             f"(bot profitable at {overall_wr:.1%}, easing slightly)")
                    cfg["min_confidence"] = new_conf
                    changes["min_confidence"] = new_conf

        # ── Log the tune event ────────────────────────────────────────────
        if changes:
            self.db["autotune_log"].append({
                "ts":      datetime.now().isoformat(),
                "changes": changes,
            })
            self.save()
            log.info(f"  Auto-tune applied {len(changes)} change(s)")
        else:
            log.info("  Auto-tune: thresholds already well-calibrated")

        log.info("═" * 50)

    # ── Pretty print ──────────────────────────────────────────────────────────

    def print_brain(self):
        live_w = self.db.get("live_wins", 0)
        live_l = self.db.get("live_losses", 0)
        live_n = live_w + live_l
        live_wr = live_w / live_n * 100 if live_n else 0
        total   = self.db.get("total_wins",0) + self.db.get("total_losses",0)
        log.info("── Brain Summary ────────────────────────────────────")
        log.info(f"  Live trades : {live_wr:.1f}% win rate "
                 f"({live_w}W / {live_l}L, n={live_n})")
        log.info(f"  All-time    : {total} trades (incl. bootstrap)")
        for label, bucket in [
            ("By strategy",    self.db["by_strategy"]),
            ("By price zone",  self.db["by_price"]),
            ("By vol zone",    self.db["by_volume"]),
            ("By trader cnt",  self.db["by_trader_count"]),
            ("By category",    self.db.get("by_category", {})),
        ]:
            parts = []
            for k, v in bucket.items():
                n = v["wins"] + v["losses"]
                if n:
                    parts.append(f"{k}={v['wins']}/{n}")
            if parts:
                log.info(f"  {label}: {', '.join(parts)}")
        log.info("────────────────────────────────────────────────────")


# ═══════════════════════════════════════════════════════════════════════════════
#  TRADER TRACKER
# ═══════════════════════════════════════════════════════════════════════════════

class TraderTracker:

    def __init__(self, cfg: dict):
        self.cfg           = cfg
        self.top_wallets:  List[str]             = []
        self.wallet_ranks: Dict[str, float]      = {}  # addr → 0-indexed rank pct
        self.positions:    Dict[str, List[dict]] = {}
        self.activity:     Dict[str, List[dict]] = {}
        self._refreshed_at: int                  = 0

    def fetch_leaderboard(self) -> List[str]:
        target  = self.cfg["top_trader_count"]   # e.g. 500
        page_sz = 50                              # Polymarket max per page
        wallets: List[str] = []
        offset  = 0

        while len(wallets) < target:
            want = min(page_sz, target - len(wallets))
            data = _get(f"{DATA_API}/v1/leaderboard",
                        params={"timePeriod": self.cfg["leaderboard_window"].upper(),
                                "orderBy":    "PNL",
                                "limit":      want,
                                "offset":     offset}, _retries=2, timeout=10)
            if not data:
                break
            rows = data if isinstance(data, list) else data.get("data", [])
            if not rows:
                break
            for row in rows:
                addr = (row.get("proxyWallet")
                        or row.get("address")
                        or row.get("proxy_address")
                        or row.get("wallet", ""))
                if addr:
                    wallets.append(addr.lower())
            if len(rows) < want:
                break          # API returned fewer than asked — no more pages
            offset += want
            time.sleep(0.5)    # be polite between pages

        if wallets:
            self.top_wallets   = wallets
            self._refreshed_at = _now()
            # Build rank percentile map: rank 0 → 0.0 (best), rank N-1 → 1.0 (worst)
            n = len(wallets)
            self.wallet_ranks = {
                addr: i / max(n - 1, 1)
                for i, addr in enumerate(wallets)
            }
            log.info(f"Leaderboard: {len(self.top_wallets)} top traders fetched")
        else:
            log.warning("Leaderboard unavailable — keeping previous list")
        return self.top_wallets

    def needs_refresh(self) -> bool:
        return _now() - self._refreshed_at > self.cfg["refresh_traders_every"]

    def _fetch_positions(self, addr: str):
        data = _get(f"{DATA_API}/positions", params={"user": addr, "limit": 100})
        # None = genuine fetch failure (rate-limit/HTTP/network). Must stay distinct from
        # [] (wallet really holds nothing): conflating them made every transient failure
        # flap the whole wallet CLOSE→OPEN next cycle, exploding moves_log to 4.2GB.
        if data is None:
            return None
        rows = data if isinstance(data, list) else data.get("data", [])
        return [p for p in rows if float(p.get("size", 0) or 0) > 0]

    def refresh_positions(self):
        """Fetch positions for all tracked wallets concurrently."""
        workers = self.cfg.get("fetch_workers", 8)
        result:  Dict[str, List[dict]] = {}
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(self._fetch_positions, addr): addr
                       for addr in self.top_wallets}
            done = 0
            for fut in as_completed(futures):
                addr = futures[fut]
                try:
                    result[addr] = fut.result()
                except Exception as e:
                    log.debug(f"Position fetch failed for {addr[:8]}…: {e}")
                    result[addr] = None   # None = failed (preserve prev snapshot); [] = truly empty
                done += 1
                _WATCHDOG["beat"]()   # progress per wallet — a slow-but-moving
                                      # gather must not be mistaken for a hang
                # Light throttle every 50 completions to respect rate limits
                if done % 50 == 0:
                    time.sleep(0.3)
        self.positions = result
        log.info(f"Positions refreshed for {len(result)} wallets "
                 f"({workers} concurrent workers)")

    def _fetch_activity(self, addr: str) -> List[dict]:
        data = _get(f"{DATA_API}/activity", params={"user": addr, "limit": 50})
        if not data:
            return []
        return data if isinstance(data, list) else data.get("data", [])

    def refresh_activity(self, since_ts: int = 0):
        """Fetch recent activity for all tracked wallets concurrently.
        Uses fewer workers than positions to avoid hammering the activity endpoint.
        """
        # Use fewer workers for activity — this endpoint rate-limits more aggressively
        workers = max(4, self.cfg.get("fetch_workers", 8) // 2)

        def _fetch_filtered(addr: str) -> List[dict]:
            trades = self._fetch_activity(addr)
            if since_ts:
                trades = [t for t in trades
                          if int(t.get("timestamp", 0) or 0) >= since_ts]
            return trades

        result: Dict[str, List[dict]] = {}
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_fetch_filtered, addr): addr
                       for addr in self.top_wallets}
            done = 0
            for fut in as_completed(futures):
                addr = futures[fut]
                try:
                    result[addr] = fut.result()
                except Exception as e:
                    log.debug(f"Activity fetch failed for {addr[:8]}…: {e}")
                    result[addr] = []
                done += 1
                _WATCHDOG["beat"]()   # progress per wallet — a slow-but-moving
                                      # gather must not be mistaken for a hang
                # Throttle every 25 completions to avoid rate limits on activity API
                if done % 25 == 0:
                    time.sleep(0.5)
        self.activity = result
        log.info(f"Activity refreshed for {len(result)} wallets "
                 f"({workers} concurrent workers)")


# ═══════════════════════════════════════════════════════════════════════════════
#  BACKEND MOVE TRACKER  — logs every trader move to disk for analysis
# ═══════════════════════════════════════════════════════════════════════════════

class MoveTracker:
    """
    Tracks every position change across all 500 top traders.

    Files written:
      moves_log.jsonl       — append-only log of every open/close/increase/decrease
      trader_stats.json     — per-wallet activity counts and markets traded
      market_heatmap.json   — which markets are heating up right now
    """

    MOVES_FILE   = "moves_log.jsonl"
    MOVES_MAX_LINES = 3000   # keep-last-N cap (only recent_moves(n) ever reads this) — bounds the
                             # unbounded append that grew moves_log.jsonl to 4.2GB before this cap
    STATS_FILE   = "trader_stats.json"
    HEATMAP_FILE = "market_heatmap.json"
    WINRATE_FILE     = "trader_win_rate.json"
    LEADERBOARD_FILE = "winrate_leaderboard.json"

    def __init__(self):
        self._prev_positions: Dict[str, Dict[str, dict]] = {}
        self.trader_stats:    Dict[str, dict]             = {}
        self.market_heatmap:  Dict[str, dict]             = {}
        self.win_stats:       Dict[str, dict]             = {}
        self.win_leaders:     set                         = set()
        # Scalper detection: track when each wallet opened each position so
        # we can measure hold time when they close.
        # Structure: {wallet: {condition_id: open_epoch}}
        self._open_times:     Dict[str, Dict[str, float]] = {}
        self._load_stats()
        self._load_heatmap()
        self._load_win_stats()
        self.win_leaders = {w for w, s in self.win_stats.items()
                            if s.get("resolved", 0) >= 30 and s.get("winrate", 0) > 90}
        log.info(f"MoveTracker initialised — watching every trader move "
                 f"({len(self.win_leaders)} wallets on >90% win-rate board)")

    # ── Persistence ────────────────────────────────────────────────────────────

    def _load_stats(self):
        try:
            with open(self.STATS_FILE) as f:
                self.trader_stats = json.load(f)
        except Exception:
            self.trader_stats = {}

    def _save_stats(self):
        try:
            with open(self.STATS_FILE, "w") as f:
                json.dump(self.trader_stats, f, indent=2)
        except Exception as e:
            log.debug(f"MoveTracker: failed to save stats: {e}")

    # ── Forward win-rate tracking ────────────────────────────────────────────
    def _load_win_stats(self):
        try:
            with open(self.WINRATE_FILE) as f:
                self.win_stats = json.load(f)
        except Exception:
            self.win_stats = {}

    def _save_win_stats(self):
        try:
            with open(self.WINRATE_FILE, "w") as f:
                json.dump(self.win_stats, f, indent=2)
        except Exception as e:
            log.debug(f"MoveTracker: failed to save win stats: {e}")

    def _save_leaderboard(self, min_res, thresh):
        try:
            board = sorted(
                ({"wallet": w, "winrate": s["winrate"], "resolved": s["resolved"],
                  "wins": s["wins"]}
                 for w, s in self.win_stats.items()
                 if s["resolved"] >= min_res and s["winrate"] > thresh),
                key=lambda x: (-x["winrate"], -x["resolved"]))
            with open(self.LEADERBOARD_FILE, "w") as f:
                json.dump({"min_resolved": min_res, "threshold_pct": thresh,
                           "count": len(board), "wallets": board}, f, indent=2)
        except Exception as e:
            log.debug(f"MoveTracker: failed to save leaderboard: {e}")

    def update_win_rates(self, tracker, cfg):
        """Forward win-rate: reuse the positions already fetched this scan; when a
        wallet's position is observed RESOLVED (redeemable, or curPrice 0/1), record
        win (cashPnl>0) / loss ONCE per (wallet, market). Promote wallets with
        >= winrate_min_resolved resolutions AND win% > winrate_threshold_pct into
        self.win_leaders. No extra API calls — reuses tracker.positions."""
        min_res = cfg.get("winrate_min_resolved", 30)
        thresh  = cfg.get("winrate_threshold_pct", 90)
        changed = False
        for wallet, poss in (tracker.positions or {}).items():
            for p in (poss or []):   # poss may be None on a failed fetch
                cid = p.get("conditionId")
                if not cid:
                    continue
                try:
                    cur = float(p.get("curPrice"))
                except (TypeError, ValueError):
                    cur = -1.0
                if not (p.get("redeemable") or cur in (0.0, 1.0)):
                    continue
                s = self.win_stats.get(wallet)
                if s is None:
                    s = self.win_stats[wallet] = {
                        "wallet": wallet, "resolved": 0, "wins": 0,
                        "winrate": 0.0, "seen": []}
                if cid in s["seen"]:
                    continue
                s["seen"].append(cid)
                s["seen"] = s["seen"][-300:]
                s["resolved"] += 1
                if float(p.get("cashPnl") or 0) > 0:
                    s["wins"] += 1
                s["winrate"] = round(s["wins"] / s["resolved"] * 100, 1)
                changed = True
        if changed:
            self._save_win_stats()
            self.win_leaders = {
                w for w, s in self.win_stats.items()
                if s["resolved"] >= min_res and s["winrate"] > thresh}
            self._save_leaderboard(min_res, thresh)

    def _load_heatmap(self):
        try:
            with open(self.HEATMAP_FILE) as f:
                self.market_heatmap = json.load(f)
        except Exception:
            self.market_heatmap = {}

    def _save_heatmap(self):
        try:
            with open(self.HEATMAP_FILE, "w") as f:
                json.dump(self.market_heatmap, f, indent=2)
        except Exception as e:
            log.debug(f"MoveTracker: failed to save heatmap: {e}")

    def _append_move(self, move: dict):
        try:
            with open(self.MOVES_FILE, "a") as f:
                f.write(json.dumps(move) + "\n")
            # keep-last-N cap (only scan once the file is non-trivial; os.replace is atomic for readers)
            if os.path.getsize(self.MOVES_FILE) > 2_000_000:
                with open(self.MOVES_FILE) as fr:
                    lines = fr.readlines()
                if len(lines) > self.MOVES_MAX_LINES:
                    tmp = self.MOVES_FILE + ".tmp"
                    with open(tmp, "w") as fw:
                        fw.writelines(lines[-self.MOVES_MAX_LINES:])
                    os.replace(tmp, self.MOVES_FILE)
        except Exception as e:
            log.debug(f"MoveTracker: failed to append move: {e}")

    # ── Core diff logic ────────────────────────────────────────────────────────

    def _index_positions(self, pos_list: List[dict]) -> Dict[str, dict]:
        idx: Dict[str, dict] = {}
        for p in pos_list:
            cid = (p.get("conditionId") or p.get("condition_id") or
                   (p.get("market") or {}).get("conditionId", "")).lower()
            if cid:
                idx[cid] = p
        return idx

    def diff_and_log(self, tracker: "TraderTracker"):
        """
        Diff current vs previous positions for every wallet.
        Detect OPEN, CLOSE, INCREASE and log every change.
        """
        ts    = datetime.utcnow().isoformat() + "Z"
        epoch = int(time.time())
        new_moves: List[dict] = []

        for addr, pos_list in tracker.positions.items():
            if pos_list is None:
                continue   # fetch failed this cycle — skip diffing so a transient API
                           # failure can't phantom-CLOSE then re-OPEN the wallet's positions
            rank_pct   = tracker.wallet_ranks.get(addr, 0.5)
            rank_label = (
                "elite" if rank_pct < 0.1 else
                "top10" if rank_pct < 0.2 else
                "top25" if rank_pct < 0.5 else
                "mid"
            )
            curr = self._index_positions(pos_list)
            prev = self._prev_positions.get(addr, {})

            # NEW or INCREASED
            for cid, pos in curr.items():
                title    = ((pos.get("market") or {}).get("question") or
                            pos.get("title") or cid[:24])
                outcome  = (pos.get("outcome") or "").upper()
                size_now = float(pos.get("size") or 0)
                size_was = float((prev.get(cid) or {}).get("size") or 0)

                if size_was == 0:
                    action = "OPEN"
                    # Record open time for scalper detection
                    if addr not in self._open_times:
                        self._open_times[addr] = {}
                    self._open_times[addr][cid] = epoch
                elif size_now > size_was * 1.05:
                    action = "INCREASE"
                else:
                    continue

                move = {
                    "ts": ts, "epoch": epoch,
                    "wallet": addr,
                    "wallet_short": addr[:10] + "…",
                    "rank_pct": round(rank_pct, 3),
                    "rank_label": rank_label,
                    "action": action,
                    "condition_id": cid,
                    "title": title,
                    "outcome": outcome,
                    "size_now": round(size_now, 4),
                    "size_was": round(size_was, 4),
                    "delta": round(size_now - size_was, 4),
                }
                new_moves.append(move)
                self._update_heatmap(cid, title, action, outcome, addr, rank_pct)
                self._update_trader_stats(addr, rank_pct, action, cid)

            # CLOSED
            for cid, pos in prev.items():
                if cid not in curr:
                    title    = ((pos.get("market") or {}).get("question") or
                                pos.get("title") or cid[:24])
                    outcome  = (pos.get("outcome") or "").upper()
                    size_was = float(pos.get("size") or 0)

                    # ── Scalper detection: measure hold time ─────────────────
                    hold_sec = None
                    open_ep  = (self._open_times.get(addr) or {}).get(cid)
                    if open_ep:
                        hold_sec = epoch - open_ep
                        # Clean up
                        self._open_times[addr].pop(cid, None)
                    self._update_trader_stats(
                        addr, rank_pct, "CLOSE", cid, hold_sec=hold_sec)

                    move = {
                        "ts": ts, "epoch": epoch,
                        "wallet": addr,
                        "wallet_short": addr[:10] + "…",
                        "rank_pct": round(rank_pct, 3),
                        "rank_label": rank_label,
                        "action": "CLOSE",
                        "condition_id": cid,
                        "title": title,
                        "outcome": outcome,
                        "size_now": 0,
                        "size_was": round(size_was, 4),
                        "delta": round(-size_was, 4),
                        "hold_sec": round(hold_sec) if hold_sec else None,
                    }
                    new_moves.append(move)
                    self._update_heatmap(cid, title, "CLOSE", outcome, addr, rank_pct)

        # Save new snapshot. Update only wallets that fetched successfully; for failed
        # wallets (None) keep the previous snapshot so they don't flap on recovery.
        for addr, pos_list in tracker.positions.items():
            if pos_list is None:
                continue
            self._prev_positions[addr] = self._index_positions(pos_list)

        for m in new_moves:
            self._append_move(m)

        if new_moves:
            opens  = sum(1 for m in new_moves if m["action"] == "OPEN")
            closes = sum(1 for m in new_moves if m["action"] == "CLOSE")
            inc    = sum(1 for m in new_moves if m["action"] == "INCREASE")
            log.info(
                f"MoveTracker │ {len(new_moves)} moves — "
                f"{opens} opens  {closes} closes  {inc} increases"
            )
            elite = [m for m in new_moves
                     if m["action"] == "OPEN" and m["rank_pct"] < 0.2]
            for m in elite[:5]:
                log.info(
                    f"  🔥 ELITE OPEN │ {m['wallet_short']} "
                    f"(rank {m['rank_pct']:.2f}) → "
                    f"{m['outcome']} on \"{m['title'][:55]}\" "
                    f"size={m['size_now']}"
                )

            # ── Signal bridge for wallet_follow scalper ────────────────────
            # Write top-10% rank, YES/NO opens to wf_signals.json so the
            # scalp engine can enter without needing a Gamma title search.
            # Build signal list — include token_id directly so the scalp engine
            # doesn't need an unreliable Gamma title-search to find the token.
            wf_sig_list = []
            for m in new_moves:
                if m["action"] != "OPEN" or m["rank_pct"] > 0.20:
                    continue
                cid_m = m["condition_id"]
                # Find token_id from the current position data for this wallet/market
                token_id_m = None
                for addr2, pos_list2 in tracker.positions.items():
                    for p2 in (pos_list2 or []):
                        p2_cid = (p2.get("conditionId") or p2.get("condition_id") or
                                  (p2.get("market") or {}).get("conditionId", "")).lower()
                        if p2_cid == cid_m:
                            token_id_m = (p2.get("asset") or p2.get("token_id") or
                                          p2.get("tokenId") or p2.get("proxyWallet"))
                            break
                    if token_id_m:
                        break
                wf_sig_list.append({
                    "cid":      cid_m,
                    "token_id": token_id_m,   # direct token ID — avoids Gamma lookup
                    "title":    m["title"],
                    "outcome":  m["outcome"],
                    "rank":     m["rank_pct"],
                    "ts":       datetime.utcnow().isoformat() + "Z",
                })
            wf_candidates = wf_sig_list
            if wf_candidates:
                _wf_sig_path = os.path.join(
                    os.path.dirname(os.path.abspath(__file__)), "wf_signals.json"
                )
                try:
                    existing = []
                    if os.path.exists(_wf_sig_path):
                        existing = json.load(open(_wf_sig_path))
                    # Append new signals, keep last 100
                    existing.extend(wf_candidates)
                    existing = existing[-100:]
                    json.dump(existing, open(_wf_sig_path, "w"), indent=2)
                except Exception as _e:
                    log.debug(f"wf_signals write error: {_e}")

        self._save_stats()
        self._save_heatmap()
        return new_moves

    # ── Heatmap updater ────────────────────────────────────────────────────────

    def _update_heatmap(self, cid, title, action, outcome, wallet, rank_pct):
        if cid not in self.market_heatmap:
            self.market_heatmap[cid] = {
                "condition_id": cid, "title": title,
                "opens": 0, "closes": 0, "increases": 0,
                "net_flow": 0, "elite_opens": 0,
                "wallets_in": [], "wallets_out": [],
                "last_activity": "", "momentum_score": 0.0,
            }
        h = self.market_heatmap[cid]
        h["title"] = title
        h["last_activity"] = datetime.utcnow().isoformat() + "Z"
        if action == "OPEN":
            h["opens"] += 1
            h["net_flow"] += 1
            if wallet not in h["wallets_in"]:
                h["wallets_in"].append(wallet)
            if rank_pct < 0.2:
                h["elite_opens"] += 1
        elif action == "CLOSE":
            h["closes"] += 1
            h["net_flow"] -= 1
            if wallet not in h["wallets_out"]:
                h["wallets_out"].append(wallet)
        elif action == "INCREASE":
            h["increases"] += 1
            h["net_flow"] += 0.5
        h["momentum_score"] = round(
            h["opens"] * 1.0 + h["elite_opens"] * 3.0 +
            h["increases"] * 0.5 - h["closes"] * 0.8, 2
        )
        h["wallets_in"]  = h["wallets_in"][-20:]
        h["wallets_out"] = h["wallets_out"][-20:]

    # ── Trader stats updater ────────────────────────────────────────────────────

    def _update_trader_stats(self, wallet, rank_pct, action, cid, hold_sec=None):
        if wallet not in self.trader_stats:
            self.trader_stats[wallet] = {
                "wallet": wallet, "rank_pct": round(rank_pct, 3),
                "total_opens": 0, "total_closes": 0, "total_increases": 0,
                "markets_traded": [], "last_active": "",
                # Scalper detection fields
                "timed_exits":      0,   # exits where we measured hold time
                "fast_exits":       0,   # exits where hold < SCALPER_HOLD_MAX_SEC
                "scalper_score":    0.0, # fast_exits / timed_exits (≥0.6 = scalper)
                "is_scalper":       False,
            }
        s = self.trader_stats[wallet]
        # Backfill scalper fields for wallets loaded from disk before this feature existed
        s.setdefault("timed_exits",   0)
        s.setdefault("fast_exits",    0)
        s.setdefault("scalper_score", 0.0)
        s.setdefault("is_scalper",    False)
        s["rank_pct"]    = round(rank_pct, 3)
        s["last_active"] = datetime.utcnow().isoformat() + "Z"
        if action == "OPEN":
            s["total_opens"] = s.get("total_opens", 0) + 1
            if cid not in s["markets_traded"]:
                s["markets_traded"].append(cid)
        elif action == "CLOSE":
            s["total_closes"] = s.get("total_closes", 0) + 1
            # Update scalper score if we have hold-time data
            SCALPER_HOLD_MAX_SEC = 600   # <10 min hold = fast flip
            SCALPER_MIN_EXITS    = 3     # need at least 3 timed exits before labelling
            if hold_sec is not None:
                te = s.get("timed_exits", 0) + 1
                fe = s.get("fast_exits",  0) + (1 if hold_sec < SCALPER_HOLD_MAX_SEC else 0)
                s["timed_exits"]   = te
                s["fast_exits"]    = fe
                if te >= SCALPER_MIN_EXITS:
                    s["scalper_score"] = round(fe / te, 3)
                    s["is_scalper"]    = s["scalper_score"] >= 0.60
        elif action == "INCREASE":
            s["total_increases"] = s.get("total_increases", 0) + 1
        s["markets_traded"] = s["markets_traded"][-50:]

    # ── Analysis helpers ────────────────────────────────────────────────────────

    def top_markets(self, n: int = 10) -> List[dict]:
        return sorted(self.market_heatmap.values(),
                      key=lambda h: h["momentum_score"], reverse=True)[:n]

    def is_scalper(self, wallet: str) -> bool:
        """Return True if this wallet has been identified as a fast-flip scalper."""
        s = self.trader_stats.get(wallet, {})
        return bool(s.get("is_scalper", False))

    def scalper_wallets(self) -> set:
        """Return the set of wallets currently flagged as scalpers."""
        return {w for w, s in self.trader_stats.items() if s.get("is_scalper")}

    def most_active_traders(self, n: int = 10) -> List[dict]:
        return sorted(self.trader_stats.values(),
                      key=lambda s: s["total_opens"], reverse=True)[:n]

    def recent_moves(self, n: int = 20) -> List[dict]:
        moves = []
        try:
            with open(self.MOVES_FILE) as f:
                lines = f.readlines()
            for line in lines[-n:]:
                try:
                    moves.append(json.loads(line.strip()))
                except Exception:
                    pass
        except Exception:
            pass
        return moves


# ═══════════════════════════════════════════════════════════════════════════════
#  MARKET CACHE
# ═══════════════════════════════════════════════════════════════════════════════

class MarketCache:
    TTL = 60        # seconds before a cached market is considered stale
    CLOB_TTL = 10   # seconds before a cached live CLOB price is considered stale

    def __init__(self):
        self._cache:      Dict[str, dict] = {}
        self._prev_cache: Dict[str, dict] = {}   # previous snapshot for momentum
        self._fetched_at: Dict[str, int]  = {}
        self._clob_px:    Dict[str, tuple] = {}  # token_id -> (ts, price) live prices

    def get(self, condition_id: str) -> Optional[dict]:
        _WATCHDOG["beat"]()   # progress signal for the run-loop hang watchdog
        # Invalidate stale entries so monitor_positions sees fresh prices
        age = _now() - self._fetched_at.get(condition_id, 0)
        if condition_id in self._cache and age < self.TTL:
            return self._cache[condition_id]
        data = _get(f"{GAMMA_API}/markets",
                    params={"condition_ids": condition_id})
        if not data:
            # Return stale data rather than None if we have it
            return self._cache.get(condition_id)
        items = data if isinstance(data, list) else data.get("markets", [])
        if items:
            # Keep previous snapshot for momentum detection
            if condition_id in self._cache:
                self._prev_cache[condition_id] = self._cache[condition_id]
            self._cache[condition_id]      = items[0]
            self._fetched_at[condition_id] = _now()
            return items[0]
        return None

    def prefetch(self, cids) -> None:
        """Warm the cache for many condition_ids concurrently.

        The signal generators call get() once per candidate market. Done
        serially, those network lookups dominate scan time (minutes for ~100
        markets). Fetching them in parallel — results land in the shared
        cache — cuts that to seconds. Skips ids already fresh in cache.
        """
        targets = [c for c in dict.fromkeys(cids)            # dedupe, keep order
                   if c and (_now() - self._fetched_at.get(c, 0)) >= self.TTL]
        if not targets:
            return
        from concurrent.futures import ThreadPoolExecutor
        workers = min(12, len(targets))
        try:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                list(ex.map(self.get, targets))
        except Exception as e:
            log.debug(f"prefetch error (continuing serially): {e}")

    def refresh(self, cids) -> None:
        """Force a concurrent re-fetch of these markets, ignoring the TTL.
        Used by the fast exit-monitor between full scans so held-position
        prices are genuinely live rather than served from the 60s cache."""
        for c in cids:
            if c:
                self._fetched_at[c] = 0
        self.prefetch(cids)

    def volume(self, mkt: dict) -> float:
        return float(mkt.get("volume") or mkt.get("volumeNum") or 0)

    def clob_price(self, token_id: str) -> Optional[float]:
        """Live midpoint price for a token from the CLOB order book.
        Cached briefly; fail-soft (returns None on any error)."""
        if not token_id:
            return None
        hit = self._clob_px.get(token_id)
        if hit and _now() - hit[0] < self.CLOB_TTL:
            return hit[1]
        price = None
        try:
            data = _get(f"{CLOB_HOST}/midpoint",
                        params={"token_id": token_id}, timeout=6)
            if data and data.get("mid") not in (None, ""):
                p = float(data["mid"])
                if 0.0 < p < 1.0:
                    price = p
        except Exception:
            price = None
        if price is not None:
            self._clob_px[token_id] = (_now(), price)
        return price

    def best_price(self, mkt: dict, side: str) -> float:
        # 1) Live price from the CLOB order book (the real number).
        tid = self.token_id(mkt, side)
        live = self.clob_price(tid) if tid else None
        if live is not None:
            return live
        # 2) Fallback: Gamma token price or outcomePrices, if present.
        want = "YES" if side == BUY else "NO"
        for tok in mkt.get("tokens", []):
            if tok.get("outcome", "").upper() == want:
                p = tok.get("price")
                if p not in (None, ""):
                    return float(p)
        op = mkt.get("outcomePrices")
        if isinstance(op, str):
            try:
                op = json.loads(op)
            except Exception:
                op = None
        if isinstance(op, (list, tuple)) and len(op) >= 2:
            try:
                return float(op[0] if side == BUY else op[1])
            except Exception:
                pass
        # 3) Last resort: neutral 0.5 (unknown price).
        return 0.5

    def token_id(self, mkt: dict, side: str) -> Optional[str]:
        want = "YES" if side == BUY else "NO"
        for tok in mkt.get("tokens", []):
            if tok.get("outcome", "").upper() == want:
                tid = tok.get("token_id") or tok.get("tokenId")
                # Validate: real token IDs are long hex strings (>10 chars)
                if tid and len(str(tid)) > 10:
                    return str(tid)
        # Fallback: clobTokenIds — Gamma API sometimes returns this as a
        # JSON-encoded string rather than an actual list, so parse it safely.
        raw_ids = mkt.get("clobTokenIds", [])
        if isinstance(raw_ids, str):
            try:
                import json as _json
                raw_ids = _json.loads(raw_ids)
            except Exception:
                raw_ids = []
        if isinstance(raw_ids, list) and raw_ids:
            idx = 0 if side == BUY else 1
            tid = raw_ids[idx] if idx < len(raw_ids) else None
            # Validate length — guards against grabbing a single bracket char
            if tid and len(str(tid)) > 10:
                return str(tid)
        return None

    def title(self, mkt: dict) -> str:
        return mkt.get("question") or mkt.get("title") or "?"

    def days_to_close(self, mkt: dict) -> Optional[float]:
        """Days until market resolves.  Returns None if end date unknown."""
        end = (mkt.get("endDate") or mkt.get("end_date")
               or mkt.get("closeTime") or mkt.get("resolutionDate"))
        if not end:
            return None
        try:
            if isinstance(end, (int, float)):
                dt = datetime.fromtimestamp(int(end), tz=timezone.utc)
            else:
                end_s = str(end).replace("Z", "+00:00")
                dt = datetime.fromisoformat(end_s)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
            now = datetime.now(tz=timezone.utc)
            return (dt - now).total_seconds() / 86400
        except Exception:
            return None

    def spread_quality(self, mkt: dict) -> bool:
        """
        Returns True if YES + NO prices sum to roughly 1.0 (healthy market).
        A sum far from 1.0 indicates a broken/illiquid market.
        """
        yes_p = no_p = None
        for tok in mkt.get("tokens", []):
            p = float(tok.get("price") or 0)
            out = tok.get("outcome", "").upper()
            if out == "YES":
                yes_p = p
            elif out == "NO":
                no_p = p
        if yes_p is None or no_p is None:
            return True   # can't check — assume ok
        total = yes_p + no_p
        return 0.82 <= total <= 1.18   # allow ~18% slip before rejecting

    def momentum(self, condition_id: str, side: str) -> float:
        """
        Compare current price to previous cached price.
        Returns +1 (price moving our way), -1 (against), 0 (stable).
        """
        prev = self._prev_cache.get(condition_id)
        curr = self._cache.get(condition_id)
        if prev is None or curr is None:
            return 0.0
        prev_p = self.best_price(prev, side)
        curr_p = self.best_price(curr, side)
        if prev_p <= 0:
            return 0.0
        change = (curr_p - prev_p) / prev_p
        if change > 0.01:   return  1.0   # price moving our way
        if change < -0.01:  return -1.0   # price moving against us
        return 0.0

    def category(self, mkt: dict) -> str:
        return _category_from_market(mkt)


# ═══════════════════════════════════════════════════════════════════════════════
#  SIGNAL GENERATORS
# ═══════════════════════════════════════════════════════════════════════════════

def find_consensus(tracker: TraderTracker,
                   cache: MarketCache,
                   cfg: dict,
                   seasoned: set = None,
                   scalpers: set = None) -> List[dict]:
    votes: Dict[str, Dict[str, List[str]]] = defaultdict(
        lambda: {BUY: [], SELL: []})

    # Pre-build scalper set — wallets that flip in <10 min. Their open positions
    # are stale signals: they've already exited by the time we copy them.
    # Scalper tracking lives in MoveTracker, passed in as an optional set.
    _scalpers = scalpers or set()
    if _scalpers:
        log.debug(f"[SCALPER-FILTER] Excluding {len(_scalpers)} fast-flip wallets from consensus")

    for addr, positions in tracker.positions.items():
        # Only count votes from seasoned wallets (>100 trades) when filtering.
        if seasoned is not None and addr not in seasoned:
            continue
        # Skip wallets identified as scalpers — their open positions are stale
        if addr in _scalpers:
            continue
        for pos in (positions or []):
            cid  = (pos.get("conditionId") or pos.get("condition_id")
                    or pos.get("market") or "")
            raw  = (pos.get("side") or pos.get("outcome") or "").upper()
            size = float(pos.get("size") or 0)
            if not cid or size < 1:
                continue
            side = BUY if raw in ("BUY", "YES") else SELL
            votes[cid][side].append(addr)

    signals = []
    res_min = cfg.get("resolution_min_days", 1)
    res_max = cfg.get("resolution_max_days", 45)

    # ── Concurrent cache warm-up ────────────────────────────────────────────
    # Collect every market that the loop below will look up (those meeting the
    # trader-count / share thresholds) and fetch them in parallel up front.
    # This turns ~100 serial network calls into one parallel batch.
    _min_traders = cfg["consensus_min_traders"]
    _min_share   = cfg["consensus_min_share"]
    _candidates  = []
    for cid, sides in votes.items():
        total = len(sides[BUY]) + len(sides[SELL])
        if total == 0:
            continue
        if any(len(sides[s]) >= _min_traders
               and (len(sides[s]) / total) >= _min_share
               for s in (BUY, SELL)):
            _candidates.append(cid)
    cache.prefetch(_candidates)

    for cid, sides in votes.items():
        total = len(sides[BUY]) + len(sides[SELL])
        if total == 0:
            continue
        for side in (BUY, SELL):
            holders = sides[side]
            count   = len(holders)
            share   = count / total
            if count < cfg["consensus_min_traders"]:
                continue
            if share < cfg["consensus_min_share"]:
                continue
            mkt = cache.get(cid)
            if not mkt:
                continue

            # ── Time-to-resolution filter ──────────────────────────────────
            days = cache.days_to_close(mkt)
            if days is not None and not (res_min <= days <= res_max):
                continue

            # ── Market quality filter ──────────────────────────────────────
            if not cache.spread_quality(mkt):
                continue

            vol = cache.volume(mkt)
            if vol < cfg["consensus_min_volume"]:
                continue
            price = cache.best_price(mkt, side)
            if not (cfg["min_price"] <= price <= cfg["max_price"]):
                continue
            tid = cache.token_id(mkt, side)
            if not tid:
                continue

            # ── Trader rank weighting ──────────────────────────────────────
            # avg_rank_pct: 0.0 = top-ranked holders, 1.0 = bottom-ranked
            rank_pcts = [tracker.wallet_ranks.get(w, 0.5) for w in holders]
            avg_rank_pct = sum(rank_pcts) / len(rank_pcts) if rank_pcts else 0.5

            # ── Sports-specific filter ─────────────────────────────────────
            # Sports markets need more elite agreement — too noisy otherwise.
            cat = cache.category(mkt)
            if cat == "sports":
                sports_min = cfg.get("sports_min_traders", 6)
                sports_rank = cfg.get("sports_max_avg_rank", 0.30)
                if count < sports_min or avg_rank_pct > sports_rank:
                    continue

            # ── Momentum ──────────────────────────────────────────────────
            mom = cache.momentum(cid, side)

            signals.append({
                "strategy":     "consensus",
                "condition_id": cid,
                "token_id":     tid,
                "side":         side,
                "price":        price,
                "trader_count": count,
                "trader_share": round(share, 3),
                "volume":       vol,
                "title":        cache.title(mkt),
                "wallets":      holders[:5],
                "avg_rank_pct": round(avg_rank_pct, 3),
                "momentum":     mom,
                "category":     cache.category(mkt),
                "days_to_close": round(days, 1) if days is not None else None,
            })

    # Sort: more traders first, then by top-ranked holders
    signals.sort(key=lambda s: (s["trader_count"], -s["avg_rank_pct"]),
                 reverse=True)
    return signals


def find_insider(tracker: TraderTracker,
                 cache: MarketCache,
                 cfg: dict,
                 seasoned: set = None) -> List[dict]:
    cutoff = _now() - cfg["insider_lookback_sec"]
    bets: Dict[str, List[dict]] = defaultdict(list)

    for addr, trades in tracker.activity.items():
        # Only consider seasoned wallets (>100 trades) when filtering.
        if seasoned is not None and addr not in seasoned:
            continue
        for t in trades:
            ts    = int(t.get("timestamp") or 0)
            size  = float(t.get("size") or t.get("amount") or 0)
            price = float(t.get("price") or t.get("outcome_price") or 0)
            cid   = (t.get("conditionId") or t.get("condition_id")
                     or t.get("market") or "")
            raw   = (t.get("side") or t.get("type") or "").upper()
            if ts < cutoff or not cid or price <= 0 or size <= 0:
                continue
            usd  = size * price
            if usd < cfg["insider_min_bet_usdc"]:
                continue
            side = BUY if raw in ("BUY", "YES") else SELL
            bets[cid].append({"wallet": addr, "usd": usd,
                               "price": price, "side": side})

    signals = []
    res_min = cfg.get("resolution_min_days", 1)
    res_max = cfg.get("resolution_max_days", 45)

    for cid, bet_list in bets.items():
        mkt = cache.get(cid)
        if not mkt:
            continue

        # ── Time-to-resolution filter ──────────────────────────────────────
        days = cache.days_to_close(mkt)
        if days is not None and not (res_min <= days <= res_max):
            continue

        # ── Market quality filter ──────────────────────────────────────────
        if not cache.spread_quality(mkt):
            continue

        vol = cache.volume(mkt)
        if vol > cfg["insider_max_volume"]:
            continue
        buy_usd  = sum(b["usd"] for b in bet_list if b["side"] == BUY)
        sell_usd = sum(b["usd"] for b in bet_list if b["side"] == SELL)
        side     = BUY if buy_usd >= sell_usd else SELL
        total    = max(buy_usd, sell_usd)
        price    = cache.best_price(mkt, side)
        if price > cfg["insider_max_price"] or price < cfg["min_price"]:
            continue
        tid = cache.token_id(mkt, side)
        if not tid:
            continue
        wallets = list({b["wallet"] for b in bet_list if b["side"] == side})

        # ── Trader rank weighting ──────────────────────────────────────────
        rank_pcts = [tracker.wallet_ranks.get(w, 0.5) for w in wallets]
        avg_rank_pct = sum(rank_pcts) / len(rank_pcts) if rank_pcts else 0.5

        # ── Sports-specific filter ─────────────────────────────────────────
        ins_cat = cache.category(mkt)
        if ins_cat == "sports":
            sports_min  = cfg.get("sports_min_traders", 6)
            sports_rank = cfg.get("sports_max_avg_rank", 0.30)
            if len(wallets) < sports_min or avg_rank_pct > sports_rank:
                continue

        # ── Momentum ──────────────────────────────────────────────────────
        mom = cache.momentum(cid, side)

        signals.append({
            "strategy":     "insider",
            "condition_id": cid,
            "token_id":     tid,
            "side":         side,
            "price":        price,
            "bet_usd":      round(total, 2),
            "trader_count": len(wallets),
            "volume":       vol,
            "title":        cache.title(mkt),
            "wallets":      wallets[:5],
            "avg_rank_pct": round(avg_rank_pct, 3),
            "momentum":     mom,
            "category":     cache.category(mkt),
            "days_to_close": round(days, 1) if days is not None else None,
        })

    # Sort: largest USD bet first, then by top-ranked traders
    signals.sort(key=lambda s: (s["bet_usd"], -s["avg_rank_pct"]), reverse=True)
    return signals


def find_midline(tracker: TraderTracker,
                 cache: MarketCache,
                 cfg: dict) -> List[dict]:
    """
    MIDLINE STRATEGY — Buy cheap YES tokens, sell at 50 cents.

    Logic:
      1. Find markets where at least one top trader holds YES.
      2. Current YES price must be ≤ midline_buy_max (default 0.48).
      3. Volume must be ≥ midline_min_volume (market is real / active).
      4. Signal side is always BUY YES.
      5. The take-profit for these positions is overridden to
         midline_sell_target (0.50) instead of the normal TP %.

    Why this works:
      In binary prediction markets the "fair" price for a genuinely
      uncertain outcome is ~0.50.  When YES sits at 0.48 with top
      traders already holding it, there's a good chance it drifts back
      toward 0.50 as more money flows in — giving you a clean ~4 % gain
      on a relatively low-risk entry.
    """
    if not cfg.get("midline_enabled", False):
        return []

    buy_max    = cfg["midline_buy_max"]      # e.g. 0.48
    sell_tgt   = cfg["midline_sell_target"]  # e.g. 0.50
    min_vol    = cfg["midline_min_volume"]

    # Markets where top traders hold YES
    yes_markets: Dict[str, List[str]] = defaultdict(list)
    for addr, positions in tracker.positions.items():
        for pos in (positions or []):
            cid  = (pos.get("conditionId") or pos.get("condition_id")
                    or pos.get("market") or "")
            raw  = (pos.get("side") or pos.get("outcome") or "").upper()
            size = float(pos.get("size") or 0)
            if cid and size > 0 and raw in ("BUY", "YES"):
                yes_markets[cid].append(addr)

    # ── Pre-filter BEFORE any market fetch ───────────────────────────────────
    # Top traders collectively hold YES in thousands of markets; fetching all
    # of them hangs the scan. Require a minimum number of YES holders, then cap
    # to the most-held candidates, and only fetch those.
    min_traders = cfg.get("midline_min_traders", 2)
    max_cands   = cfg.get("midline_max_candidates", 60)
    candidates  = [(cid, h) for cid, h in yes_markets.items()
                   if len(h) >= min_traders]
    candidates.sort(key=lambda kv: len(kv[1]), reverse=True)
    candidates  = candidates[:max_cands]
    yes_markets = dict(candidates)

    # Warm the (now bounded) cache concurrently.
    cache.prefetch(list(yes_markets.keys()))

    signals = []
    for cid, holders in yes_markets.items():
        mkt = cache.get(cid)
        if not mkt:
            continue

        vol = cache.volume(mkt)
        if vol < min_vol:
            continue

        yes_price = cache.best_price(mkt, BUY)

        # Only fire if price is below our buy threshold
        if yes_price > buy_max or yes_price < cfg["min_price"]:
            continue

        tid = cache.token_id(mkt, BUY)
        if not tid:
            continue

        signals.append({
            "strategy":        "midline",
            "condition_id":    cid,
            "token_id":        tid,
            "side":            BUY,
            "price":           yes_price,
            "sell_target":     sell_tgt,    # ← overrides normal TP
            "trader_count":    len(holders),
            "volume":          vol,
            "title":           cache.title(mkt),
            "wallets":         holders[:5],
        })

    # Sort: furthest below 0.50 first (biggest potential gain)
    signals.sort(key=lambda s: s["price"])
    return signals


# ═══════════════════════════════════════════════════════════════════════════════
#  MAIN BOT
# ═══════════════════════════════════════════════════════════════════════════════

class PolymarketBot:

    def __init__(self, cfg: dict):
        self.cfg     = cfg
        self.client  = None
        self.tracker = TraderTracker(cfg)
        self.cache   = MarketCache()
        self.brain   = LearningEngine(cfg)

        self.move_tracker = MoveTracker()

        self.positions, self.closed = self._load_state()
        self.session_pnl = 0.0
        self.paused = False   # toggled from the phone via control.json
        self.stats = {
            "consensus": 0, "insider": 0, "midline": 0,
            "orders_real": 0, "orders_dry": 0,
            "skipped_low_conf": 0,
            "tp": 0, "sl": 0,
        }

        # ── Insider circuit breaker state ─────────────────────────────────
        self._insider_consec_losses = 0
        self._insider_paused_until  = 0   # Unix timestamp

        # ── News / market-data feed (optional, fail-soft) ─────────────────
        self.newsfeed = None
        self._news_lookups = 0   # per-scan counter, reset each loop
        if cfg.get("news_enabled"):
            try:
                from news_data import NewsFeed
                self.newsfeed = NewsFeed(ttl=cfg.get("news_ttl_sec", 900), log=log)
                log.info("News feed enabled (GDELT headlines + CoinGecko prices)")
            except Exception as e:
                log.warning(f"News feed unavailable, continuing without it: {e}")

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load_state(self):
        """Load both open positions and closed trade history from PostgreSQL."""
        open_pos, closed = _db.bot_load_state()
        if open_pos:
            log.info(f"Restored {len(open_pos)} open position(s) from db")
        return open_pos, closed

    def _save(self):
        try:
            _db.bot_save_state(self.positions, self.closed)
        except Exception as e:
            log.error(f"Save failed: {e}")

    # ── Phone control + status (via a synced folder e.g. iCloud Drive) ─────────

    def _control_path(self, name):
        d = os.path.expanduser(self.cfg.get("control_dir") or ".")
        try:
            os.makedirs(d, exist_ok=True)
        except Exception:
            d = "."
        return os.path.join(d, name)

    def read_control(self):
        """Read control.json (edited from the phone) and apply pause/midline.
        Fail-soft: any error leaves the bot running normally."""
        if not self.cfg.get("control_enabled"):
            return
        try:
            with open(self._control_path("control.json")) as f:
                c = json.load(f)
            self.paused = bool(c.get("paused", False))
            m = c.get("midline", None)
            if isinstance(m, bool):
                self.cfg["midline_enabled"] = m
            if self.paused:
                log.info("[CONTROL] PAUSED via control.json — no new entries this scan")
        except FileNotFoundError:
            # Seed a starter control file so the phone has something to edit.
            try:
                with open(self._control_path("control.json"), "w") as f:
                    json.dump({
                        "_help": "Edit on your phone: set paused true/false; midline true/false/null.",
                        "paused": False,
                        "midline": None,
                    }, f, indent=2)
            except Exception:
                pass
        except Exception as e:
            log.debug(f"read_control error: {e}")

    def write_status(self):
        """Write status.json (readable from the phone) with live metrics."""
        if not self.cfg.get("control_enabled"):
            return
        try:
            CUTOFF = "2026-05-30T14:20:00"
            clean = [t for t in self.closed
                     if t.get("opened_at", "") >= CUTOFF
                     and round(t.get("entry_price", 0.5), 3) != 0.5]
            w = sum(1 for t in clean if t.get("pnl_usdc", 0) > 0)
            pnl = round(sum(t.get("pnl_usdc", 0) for t in clean), 2)
            status = {
                "updated":       datetime.now().isoformat(timespec="seconds"),
                "running":       True,
                "paused":        getattr(self, "paused", False),
                "dry_run":       self.cfg.get("dry_run", True),
                "open":          len(self.positions),
                "closed_total":  len(self.closed),
                "clean_closed":  len(clean),
                "clean_wins":    w,
                "clean_losses":  len(clean) - w,
                "clean_win_rate": round(w / len(clean) * 100, 1) if clean else None,
                "clean_pnl_usdc": pnl,
                "midline_enabled": self.cfg.get("midline_enabled"),
                "news_enabled":  self.cfg.get("news_enabled"),
            }
            tmp = self._control_path("status.json.tmp")
            with open(tmp, "w") as f:
                json.dump(status, f, indent=2)
            os.replace(tmp, self._control_path("status.json"))
        except Exception as e:
            log.debug(f"write_status error: {e}")

    # ── Connection ────────────────────────────────────────────────────────────

    def connect(self):
        if self.cfg["dry_run"]:
            log.info("Dry-run mode — CLOB connection skipped (no real orders)")
            return
        if not _CLOB_AVAILABLE:
            log.error("py-clob-client not installed. Run: pip install py-clob-client")
            sys.exit(1)
        pk = self.cfg["private_key"]
        if not pk:
            log.error("POLYMARKET_PRIVATE_KEY not set. Add it to .env")
            sys.exit(1)
        creds = None
        if self.cfg["api_key"]:
            creds = ApiCreds(api_key=self.cfg["api_key"],
                             api_secret=self.cfg["api_secret"],
                             api_passphrase=self.cfg["api_passphrase"])
        self.client = ClobClient(host=CLOB_HOST, key=pk,
                                 chain_id=POLYGON, creds=creds,
                                 signature_type=0)
        log.info("Connected to Polymarket CLOB")

    def setup_credentials(self):
        self.client = ClobClient(CLOB_HOST, key=self.cfg["private_key"],
                                 chain_id=POLYGON, signature_type=0)
        creds = self.client.create_or_derive_api_creds()
        print("\n" + "═" * 60)
        print("  SAVE THESE IN YOUR .env FILE:")
        print(f"  POLYMARKET_API_KEY={creds.api_key}")
        print(f"  POLYMARKET_API_SECRET={creds.api_secret}")
        print(f"  POLYMARKET_API_PASSPHRASE={creds.api_passphrase}")
        print("═" * 60 + "\n")
        lines = []
        try:
            with open(".env") as f:
                lines = f.readlines()
        except FileNotFoundError:
            pass
        new = {"POLYMARKET_API_KEY": creds.api_key,
               "POLYMARKET_API_SECRET": creds.api_secret,
               "POLYMARKET_API_PASSPHRASE": creds.api_passphrase}
        written, patched = set(), []
        for ln in lines:
            k = ln.split("=")[0].strip()
            if k in new:
                patched.append(f"{k}={new[k]}\n"); written.add(k)
            else:
                patched.append(ln)
        for k, v in new.items():
            if k not in written:
                patched.append(f"{k}={v}\n")
        with open(".env", "w") as f:
            f.writelines(patched)
        log.info("Credentials saved to .env")

    def balance(self) -> float:
        try:
            return float(self.client.get_balance())
        except Exception as e:
            log.warning(f"Balance fetch: {e}")
            return 0.0

    # ── Kelly criterion bet sizing ────────────────────────────────────────────

    def kelly_bet(self, confidence: float, price: float,
                  strategy: str) -> float:
        """
        Fractional Kelly criterion bet sizing.

        In a binary prediction market at price `p`:
          - We estimate our win probability = `confidence`
          - Edge = confidence − price  (positive means we have an edge)
          - Odds = (1 − price) / price  (what we win per $ risked)
          - Raw Kelly fraction = edge / odds
          - We use quarter-Kelly for safety (cfg["kelly_fraction"])
          - Capped at 2× normal bet, floored at $0.50

        If no edge or Kelly is disabled, falls back to the fixed bet size.
        """
        frac = self.cfg.get("kelly_fraction", 0.25)
        if frac <= 0:
            # Kelly disabled — use fixed bet size
            if strategy == "insider":
                return self.cfg.get("insider_bet_size_usdc",
                                    self.cfg["bet_size_usdc"] / 2)
            return self.cfg["bet_size_usdc"]

        edge = confidence - price
        if edge <= 0 or price <= 0 or price >= 1:
            # No edge — fall back to fixed size
            fixed = self.cfg["bet_size_usdc"]
            return fixed / 2 if strategy == "insider" else fixed

        odds       = (1 - price) / price
        kelly_raw  = edge / odds
        bankroll   = self.cfg.get("kelly_bankroll_usdc", 100.0)
        kelly_bet  = kelly_raw * frac * bankroll

        # Floor / cap
        max_bet    = self.cfg["bet_size_usdc"] * 2
        min_bet    = 0.50
        if strategy == "insider":
            max_bet = self.cfg.get("insider_bet_size_usdc",
                                   self.cfg["bet_size_usdc"] / 2) * 2
        return round(max(min_bet, min(kelly_bet, max_bet)), 2)

    # ── Order placement ───────────────────────────────────────────────────────

    def _find_news_signals(self, base_sigs: list) -> list:
        """
        Generate trade signals directly from news sentiment. Conservative:
        only fires on clear, well-covered, non-breaking sentiment. Reuses the
        candidate markets surfaced by the trader signals (so it stays bounded
        and uses already-cached market data) but resolves the correct
        side-specific token for the news-chosen direction.
        """
        out, seen = [], {s["token_id"] for s in base_sigs}
        by_market = {}
        for s in base_sigs:
            by_market.setdefault(s["condition_id"], s)
        cap = self.cfg.get("news_max_lookups", 25)
        for cid, base in by_market.items():
            if self._news_lookups >= cap:
                break
            try:
                self._news_lookups += 1
                side = self.newsfeed.news_signal(
                    base.get("title", ""), base.get("category"))
                if not side:
                    continue
                mkt = self.cache.get(cid)
                if not mkt:
                    continue
                tok = self.cache.token_id(mkt, side)
                price = self.cache.best_price(mkt, side)
                if not tok or tok in seen:
                    continue
                if not (self.cfg["min_price"] <= price <= self.cfg["max_price"]):
                    continue
                out.append({
                    "strategy": "news", "condition_id": cid, "token_id": tok,
                    "side": side, "price": price, "trader_count": 0,
                    "volume": self.cache.volume(mkt), "title": self.cache.title(mkt),
                    "category": self.cache.category(mkt), "avg_rank_pct": 0.5,
                })
                seen.add(tok)
            except Exception as e:
                log.debug(f"news signal error: {e}")
        return out

    def place_order(self, sig: dict) -> bool:
        token_id = sig["token_id"]

        # ── Guard: duplicate ──────────────────────────────────────────────
        if token_id in self.positions:
            return False

        # ── Guard: position cap ───────────────────────────────────────────
        if len(self.positions) >= self.cfg["max_open_positions"]:
            return False

        # ── Guard: daily loss ─────────────────────────────────────────────
        if self.session_pnl <= -abs(self.cfg["max_daily_loss_usdc"]):
            log.warning("Daily loss cap reached — skipping order")
            return False

        # ── Guard: insider circuit breaker ────────────────────────────────
        strategy_lower = sig.get("strategy", "")
        if strategy_lower == "insider" and _now() < self._insider_paused_until:
            hrs_left = max(1, (self._insider_paused_until - _now()) // 3600)
            log.info(f"[SKIP] Insider circuit breaker active — {hrs_left}h remaining")
            return False

        # ── Guard: per-strategy position cap ─────────────────────────────
        strat_count = sum(1 for p in self.positions.values()
                          if p.get("strategy") == strategy_lower)
        cap_key = f"{strategy_lower}_max_positions"
        if cap_key in self.cfg and strat_count >= self.cfg[cap_key]:
            return False

        # ── Brain: confidence check (per-strategy threshold) ──────────────
        confidence = self.brain.score_signal(sig)

        # ── Quality boost (reliable). A: contributing wallet(s) are top profit-rank.
        #    B: a contributing wallet's copied signals have made US money (self-
        #    measured). Win-rate metric was dropped — it's censored (see config). ──
        sig["_q_boost"] = False
        if self.cfg.get("quality_boost_enabled", True):
            reasons = []
            arp = sig.get("avg_rank_pct")
            if arp is not None and arp <= self.cfg.get("rank_boost_max_avg_rank", 0.05):
                reasons.append(f"rank<={self.cfg.get('rank_boost_max_avg_rank', 0.05)}")
            self._ensure_signal_pnl()
            if self.good_sources and any(w in self.good_sources for w in (sig.get("wallets") or [])):
                reasons.append("proven-source")
            if reasons:
                confidence = min(0.99, confidence + self.cfg.get("quality_boost_conf", 0.05))
                sig["_q_boost"] = True
                log.info(f"[Q-BOOST {' '.join(reasons)}] conf {confidence:.2f} | {sig['title'][:45]}")

        # ── News / market-data layer (fail-soft, bounded per scan) ────────
        if (self.newsfeed and self.cfg.get("news_enabled")
                and self._news_lookups < self.cfg.get("news_max_lookups", 25)):
            try:
                self._news_lookups += 1
                nctx = self.newsfeed.market_context(
                    sig.get("title", ""), sig.get("category"))
                sig["news"] = nctx
                if nctx["source"] != "none":
                    if self.cfg.get("news_filter_breaking") and nctx["breaking"]:
                        log.info(f"[SKIP] breaking-news volatility "
                                 f"({nctx['n_articles']} articles) | "
                                 f"{sig['title'][:50]}")
                        return False
                    if self.cfg.get("news_adjust_confidence"):
                        confidence = max(0.0, min(
                            1.0, confidence + nctx["confidence_delta"]))
            except Exception as e:
                log.debug(f"news enrich error: {e}")

        # Use strategy-specific threshold if configured
        conf_key = f"min_confidence_{strategy_lower}"
        min_conf = self.cfg.get(conf_key, self.cfg["min_confidence"])
        if confidence < min_conf:
            log.info(
                f"[SKIP] confidence {confidence:.2f} < {min_conf:.2f} | "
                f"{sig['title'][:55]}"
            )
            self.stats["skipped_low_conf"] += 1
            return False

        # ── BUY gate ──────────────────────────────────────────────────────
        # Data (286 closed trades): BUY side = 6.4% win / -$29.92;
        # SELL side = 50.4% win / +$18.13. BUY only survives when the
        # signal comes from sharp traders (low avg_rank_pct). Restrict it.
        if str(sig.get("side")).upper() == "BUY":
            buy_max_rank = self.cfg.get("buy_max_avg_rank", 0.30)
            arp = sig.get("avg_rank_pct", 0.5)
            if arp is None or arp > buy_max_rank:
                log.info(
                    f"[SKIP] BUY gate: avg_rank_pct {arp} > {buy_max_rank} "
                    f"(BUY only on sharp traders) | {sig['title'][:45]}"
                )
                self.stats["skipped_buy_gate"] = self.stats.get("skipped_buy_gate", 0) + 1
                return False

        price = sig["price"]
        # ── Spread: you buy at the ASK, not the midpoint ──────────────────
        if self.cfg.get("model_spread"):
            price = min(0.99, price + self.cfg.get("assumed_spread", 0.02) / 2)

        # ── Momentum veto: skip if price clearly moving against us ────────
        mom = sig.get("momentum", 0)
        if mom == -1.0 and confidence < self.cfg["min_confidence"] + 0.06:
            log.info(f"[SKIP] Adverse momentum + borderline confidence | "
                     f"{sig['title'][:50]}")
            return False

        # ── Manipulation veto: block suspicious geopolitical/news markets ───────
        # Manipulation pattern: coordinated wallets pump YES high → flip hard to NO
        # → bot copies them → market reverses when whales exit. Tells:
        #   1. Geo keyword in title (Iran, Israel, war, airspace, Hormuz…)
        #   2. YES price already < 0.25 at bot entry → whale already ran the pump,
        #      bot entering at the bottom right before the reversal
        #   3. Fewer than 10 wallets on a geo market → too concentrated to be organic
        _GEO_KEYWORDS = [
            "iran", "israel", "airspace", "hormuz", "war", "sanction",
            "missile", "nuclear", "ceasefire", "conflict", "attack",
            "military", "airstrike", "invasion", "strait",
        ]
        _title_low   = sig.get("title", "").lower()
        _is_geo      = any(kw in _title_low for kw in _GEO_KEYWORDS)
        _yes_price   = sig["price"]  # YES price at entry time
        _n_wallets   = sig.get("trader_count", sig.get("signals", 1))
        _geo_min_yes = self.cfg.get("geo_min_yes_price", 0.25)   # if YES < 0.25, pump already ran
        _geo_min_wal = self.cfg.get("geo_min_wallets",   10)     # geo needs 10+ wallets, not 5
        if _is_geo:
            if _yes_price < _geo_min_yes:
                log.info(
                    f"[SKIP] Geo market — YES={_yes_price:.3f} < {_geo_min_yes} "
                    f"(pump may have already run, reversal risk) | {sig['title'][:50]}"
                )
                self.stats["skipped_manipulation"] = self.stats.get("skipped_manipulation", 0) + 1
                return False
            if _n_wallets < _geo_min_wal:
                log.info(
                    f"[SKIP] Geo market — only {_n_wallets} wallets (need ≥{_geo_min_wal} "
                    f"for geo — concentrated flow = manipulation risk) | {sig['title'][:50]}"
                )
                self.stats["skipped_manipulation"] = self.stats.get("skipped_manipulation", 0) + 1
                return False

        # ── Kelly criterion bet sizing ────────────────────────────────────
        usd = self.kelly_bet(confidence, price, strategy_lower)
        if sig.get("_q_boost"):
            usd *= self.cfg.get("quality_boost_size_mult", 1.5)
        size_tok   = round(usd / price, 4) if price > 0 else 0
        if size_tok <= 0:
            return False

        # ── Resolution-source second voice (logging only, fail-soft) ──────
        # Captures the live real-world signal at entry time so closed trades
        # carry it for later analysis. Never raises; never changes the bet.
        sig["_res"] = {"res_margin": 0.0, "res_pyes": 0.5, "res_has": 0.0}
        if self.cfg.get("resfeed_enabled") and _resfeed is not None:
            try:
                sig["_res"] = _resfeed.live_feature(sig.get("title", ""))
            except Exception as e:
                log.debug(f"resfeed error: {e}")

        strategy = sig["strategy"].upper()
        label    = sig["title"][:65]

        if self.cfg["dry_run"]:
            log.info(f"[DRY][{strategy}] conf={confidence:.2f} | {label}")
            log.info(
                f"  {sig['side']} {size_tok} tok @ {price:.4f} "
                f"(~${usd:.2f}) | traders={sig.get('trader_count', '?')} "
                f"vol=${sig.get('volume', 0):,.0f}"
            )
            self.stats["orders_dry"] += 1
            self._record(sig, price, size_tok, dry=True, confidence=confidence)
            return True

        try:
            args   = OrderArgs(token_id=token_id, price=price,
                               size=size_tok, side=sig["side"])
            signed = self.client.create_order(args)
            resp   = self.client.post_order(signed, OrderType.GTC)
            if resp and resp.get("success"):
                oid = resp.get("orderID", "?")
                log.info(f"[{strategy}] ORDER id={oid} conf={confidence:.2f} | {label}")
                self.stats["orders_real"] += 1
                self._record(sig, price, size_tok, dry=False, confidence=confidence)
                return True
            else:
                log.error(f"Order rejected: {resp}")
                return False
        except Exception as e:
            log.error(f"Order error: {e}")
            return False

    # ── (B) Self-measured wallet quality: attribute OUR copied-trade P&L back to
    #    the source wallet(s); boost wallets whose signals have made us money. ──
    def _ensure_signal_pnl(self):
        if hasattr(self, "wallet_signal_pnl"):
            return
        try:
            with open("wallet_signal_pnl.json") as f:
                self.wallet_signal_pnl = json.load(f)
        except Exception:
            self.wallet_signal_pnl = {}
        self._rebuild_good_sources()

    def _rebuild_good_sources(self):
        mt = self.cfg.get("source_boost_min_trades", 8)
        self.good_sources = {
            w for w, s in self.wallet_signal_pnl.items()
            if s.get("trades", 0) >= mt and s.get("net_pnl", 0) > 0}

    def _attribute_signal_pnl(self, pos, pnl, won):
        wallets = pos.get("wallets") or []
        if not wallets:
            return
        self._ensure_signal_pnl()
        for w in wallets:
            s = self.wallet_signal_pnl.get(w)
            if s is None:
                s = self.wallet_signal_pnl[w] = {"trades": 0, "wins": 0, "net_pnl": 0.0}
            s["trades"] += 1
            if won:
                s["wins"] += 1
            s["net_pnl"] = round(s["net_pnl"] + pnl, 4)
        try:
            with open("wallet_signal_pnl.json", "w") as f:
                json.dump(self.wallet_signal_pnl, f, indent=2)
        except Exception as e:
            log.debug(f"signal-pnl save failed: {e}")
        self._rebuild_good_sources()

    def _record(self, sig: dict, price: float, size: float,
                dry: bool, confidence: float):
        self.positions[sig["token_id"]] = {
            "token_id":       sig["token_id"],
            "condition_id":   sig["condition_id"],
            "title":          sig["title"],
            "strategy":       sig["strategy"],
            "side":           sig["side"],
            "entry_price":    price,
            "size":           size,
            "cost_usdc":      round(price * size, 4),
            "confidence":     confidence,
            "volume":         sig.get("volume", 0),
            "trader_count":   sig.get("trader_count", 0),
            "category":       sig.get("category", "other"),
            "avg_rank_pct":   sig.get("avg_rank_pct", 0.5),
            "wallets":        (sig.get("wallets") or [])[:5],
            "days_to_close":  sig.get("days_to_close"),
            "high_water_mark": price,   # tracks peak price for trailing stop
            "opened_at":      datetime.now().isoformat(),
            "dry":            dry,
            "news_headline":  (sig.get("news") or {}).get("headline", ""),
            "news_sentiment": (sig.get("news") or {}).get("sentiment", 0.0),
            "news_momentum":  (sig.get("news") or {}).get("momentum"),
            # resolution-source second voice (logged, not yet used for decisions)
            "res_margin":     (sig.get("_res") or {}).get("res_margin", 0.0),
            "res_pyes":       (sig.get("_res") or {}).get("res_pyes", 0.5),
            "res_has":        (sig.get("_res") or {}).get("res_has", 0.0),
        }
        self.stats[sig["strategy"]] = self.stats.get(sig["strategy"], 0) + 1

    # ── Position monitoring ───────────────────────────────────────────────────

    def monitor_positions(self):
        sl           = self.cfg["stop_loss_pct"]
        trail_act    = self.cfg.get("trailing_activate_pct", 0.12)
        trail_stop   = self.cfg.get("trailing_stop_pct", 0.08)
        to_close     = []

        for tid, pos in list(self.positions.items()):
            mkt = self.cache.get(pos["condition_id"])
            if not mkt:
                continue
            current = self.cache.best_price(mkt, pos["side"])
            # ── Spread: you'd sell at the BID, not the midpoint ───────────
            if self.cfg.get("model_spread") and current > 0:
                current = max(0.01, current - self.cfg.get("assumed_spread", 0.02) / 2)
            if current <= 0:
                continue

            # entry_price / high_water_mark come back as Decimal (DB/JSON) but
            # current (live price) is float — mixing them raises TypeError on the
            # subtraction below and crashed monitor_positions every scan. Coerce.
            entry   = float(pos["entry_price"])
            pnl_pct = (current - entry) / entry
            label   = pos["title"][:55]

            # ── Update high-water mark ─────────────────────────────────────
            hwm = float(pos.get("high_water_mark", entry))
            if current > hwm:
                pos["high_water_mark"] = current
                hwm = current

            # ── Midline positions: exit when price reaches sell_target ─────
            if pos.get("strategy") == "midline":
                sell_tgt = pos.get("sell_target",
                                   self.cfg["midline_sell_target"])
                if current >= sell_tgt:
                    log.info(
                        f"MIDLINE EXIT @ {current:.3f} "
                        f"(target was {sell_tgt:.2f}) | {label}"
                    )
                    to_close.append((tid, pos, current, "midline_target"))
                elif pnl_pct <= -sl:
                    log.info(f"MIDLINE STOP LOSS {pnl_pct:.1%} | {label}")
                    to_close.append((tid, pos, current, "stop_loss"))
                continue

            tp = self.cfg["take_profit_pct"]

            # ── Trailing stop (kicks in once profit ≥ trailing_activate_pct) ─
            peak_gain = (hwm - entry) / entry if entry > 0 else 0
            if peak_gain >= trail_act:
                drop_from_peak = (hwm - current) / hwm if hwm > 0 else 0
                if drop_from_peak >= trail_stop:
                    log.info(
                        f"TRAILING STOP | peak +{peak_gain:.1%}, "
                        f"dropped {drop_from_peak:.1%} from peak | {label}"
                    )
                    to_close.append((tid, pos, current, "trailing_stop"))
                    continue

            # ── Normal TP/SL ──────────────────────────────────────────────
            if pnl_pct >= tp:
                # Marking-optimism fix: a take-profit is a resting limit order —
                # it fills AT the +tp target, it does NOT capture an overshoot/gap
                # past it. Cap the exit at the limit fill (never better than market).
                tp_limit = entry * (1 + tp)
                log.info(f"TAKE PROFIT +{pnl_pct:.1%} | {label}")
                to_close.append((tid, pos, min(current, tp_limit), "take_profit"))
            elif pnl_pct <= -sl:
                log.info(f"STOP LOSS {pnl_pct:.1%} | {label}")
                to_close.append((tid, pos, current, "stop_loss"))

        for tid, pos, price, reason in to_close:
            self._close(tid, pos, price, reason)

    def _close(self, tid: str, pos: dict, price: float, reason: str):
        # ── For live trades, send the close order FIRST ───────────────────
        # Only update state (brain / PnL / positions) after confirmed close.
        # This prevents corrupted state when a close order fails.
        if not pos["dry"]:
            try:
                close_side = SELL if pos["side"] == BUY else BUY
                args   = OrderArgs(token_id=tid, price=price,
                                   size=pos["size"], side=close_side)
                signed = self.client.create_order(args)
                resp   = self.client.post_order(signed, OrderType.GTC)
                if not (resp and resp.get("success")):
                    log.error(f"Close order rejected: {resp}")
                    return   # abort — position stays open, nothing recorded
            except Exception as e:
                log.error(f"Close failed: {e}")
                return       # abort — position stays open, nothing recorded

        # ── Everything below runs only after a confirmed close ────────────
        pnl = (price - pos["entry_price"]) * pos["size"]
        won = pnl > 0
        self.session_pnl += pnl
        self._attribute_signal_pnl(pos, pnl, won)   # (B) self-measured wallet quality

        # Stats
        if reason == "take_profit":
            self.stats["tp"] += 1
        elif reason == "trailing_stop":
            self.stats["trailing_stop"] = self.stats.get("trailing_stop", 0) + 1
        elif reason == "midline_target":
            self.stats["midline_target"] = self.stats.get("midline_target", 0) + 1
        else:
            self.stats["sl"] += 1

        # ── Teach the brain ───────────────────────────────────────────────
        self.brain.record_outcome(
            sig_snapshot={
                "strategy":     pos["strategy"],
                "price":        pos["entry_price"],
                "volume":       pos.get("volume", 0),
                "trader_count": pos.get("trader_count", 0),
                "category":     pos.get("category", "other"),
            },
            won=won,
        )

        # ── Insider circuit breaker ───────────────────────────────────────
        if pos["strategy"] == "insider":
            if won:
                self._insider_consec_losses = 0
            else:
                self._insider_consec_losses += 1
                max_cl  = self.cfg.get("insider_max_consecutive_losses", 3)
                pause_h = self.cfg.get("insider_pause_hours", 72)
                if self._insider_consec_losses >= max_cl:
                    self._insider_paused_until  = _now() + pause_h * 3600
                    self._insider_consec_losses = 0
                    log.warning(
                        f"⚠️  Insider circuit breaker: {max_cl} losses in a row "
                        f"— pausing insider for {pause_h}h"
                    )

        tag = "DRY CLOSE" if pos["dry"] else "CLOSE"
        log.info(f"[{tag}] {reason} | {pos['title'][:55]} | PnL ${pnl:+.2f}")

        self.closed.append({**pos, "close_price": price,
                            "pnl_usdc": round(pnl, 4),
                            "reason": reason,
                            "closed_at": datetime.now().isoformat()})
        del self.positions[tid]

    # ── Stats ─────────────────────────────────────────────────────────────────

    def print_stats(self):
        log.info("─" * 62)
        log.info(f"  Mode          : {'DRY RUN' if self.cfg['dry_run'] else 'LIVE'}")
        log.info(f"  Traders       : {len(self.tracker.top_wallets)} tracked "
                 f"({self.cfg.get('fetch_workers', 12)} concurrent workers)")
        log.info(f"  Open positions: {len(self.positions)}")
        log.info(f"  Session PnL   : ${self.session_pnl:+.2f}")
        log.info(f"  Consensus bets: {self.stats['consensus']}")
        log.info(f"  Insider bets  : {self.stats['insider']}")
        log.info(f"  Midline bets  : {self.stats['midline']}")
        log.info(f"  Skipped (conf): {self.stats['skipped_low_conf']}")
        if _now() < self._insider_paused_until:
            hrs = max(1, (self._insider_paused_until - _now()) // 3600)
            log.info(f"  Insider status: ⛔ PAUSED ({hrs}h remaining)")
        else:
            log.info(f"  Insider status: ✅ active "
                     f"(consec losses: {self._insider_consec_losses})")
        log.info(f"  Orders        : {self.stats['orders_real']} real / "
                 f"{self.stats['orders_dry']} dry")
        log.info(f"  TP hits       : {self.stats['tp']}")
        log.info(f"  Trailing stops: {self.stats.get('trailing_stop', 0)}")
        log.info(f"  SL hits       : {self.stats['sl']}")
        min_conf_c = self.cfg.get("min_confidence_consensus",
                                  self.cfg["min_confidence"])
        min_conf_i = self.cfg.get("min_confidence_insider",
                                  self.cfg["min_confidence"])
        log.info(f"  Min conf (C/I): {min_conf_c:.2f} / {min_conf_i:.2f} "
                 f"(auto-adjusting)")
        kelly_f = self.cfg.get("kelly_fraction", 0.25)
        log.info(f"  Kelly fraction: {kelly_f:.0%} "
                 f"(bankroll ${self.cfg.get('kelly_bankroll_usdc', 100):.0f})")
        trail_act  = self.cfg.get("trailing_activate_pct", 0.12)
        trail_stop = self.cfg.get("trailing_stop_pct", 0.08)
        log.info(f"  Trailing stop : activates @ +{trail_act:.0%}, "
                 f"locks in @ -{trail_stop:.0%} from peak")
        self.brain.print_brain()
        # ── Backend move tracker summary ──────────────────────────────────
        hot = self.move_tracker.top_markets(5)
        if hot:
            log.info("── 🔥 Hottest Markets (by move momentum) ────────────────")
            for h in hot:
                log.info(
                    f"  [{h['momentum_score']:+.1f}] "
                    f"{h['opens']}↑ {h['closes']}↓ {h['elite_opens']}★ │ "
                    f"{h['title'][:60]}"
                )
        active = self.move_tracker.most_active_traders(3)
        if active:
            log.info("── 👑 Most Active Traders ────────────────────────────────")
            for s in active:
                log.info(
                    f"  {s['wallet'][:12]}… rank={s['rank_pct']:.2f} │ "
                    f"{s['total_opens']} opens  {s['total_closes']} closes"
                )
        log.info("─" * 62)

    # ── Main loop ─────────────────────────────────────────────────────────────

    def run(self):
        mode = "DRY RUN" if self.cfg["dry_run"] else "⚠️  LIVE TRADING"
        log.info("═" * 62)
        log.info("  Polymarket Self-Learning Copy Bot")
        log.info(f"  Mode        : {mode}")
        log.info(f"  Tracking    : top {self.cfg['top_trader_count']} traders")
        log.info(f"  Bet size    : ${self.cfg['bet_size_usdc']}")
        log.info(f"  Min conf    : {self.cfg['min_confidence']:.2f}")
        log.info(f"  Auto-tune   : every {self.cfg['autotune_every_n']} trades")
        log.info("═" * 62)

        self.connect()
        if not self.cfg["dry_run"]:
            bal = self.balance()
            log.info(f"Wallet balance: ${bal:.2f} USDC")

        # ── Hang watchdog ───────────────────────────────────────────────────
        # A scan can wedge on a slow/stuck network call during signal
        # generation. The self-healing launcher only restarts on a *crash*, so
        # a hang would otherwise freeze the bot indefinitely. This daemon thread
        # force-exits the process if no progress "beat" is recorded within
        # max_stall_sec, letting start_bot.command relaunch us cleanly.
        import threading
        max_stall = int(self.cfg.get("max_stall_sec", 300))
        last_beat = [time.time()]
        def _beat():
            last_beat[0] = time.time()
        # Route market-fetch progress through the watchdog so a slow scan
        # (many sequential market lookups) is not mistaken for a hang.
        _WATCHDOG["beat"] = _beat
        def _watchdog():
            while True:
                time.sleep(15)
                stalled = time.time() - last_beat[0]
                if stalled > max_stall:
                    log.error(f"WATCHDOG: no progress for {int(stalled)}s "
                              f"(> {max_stall}s limit) — forcing restart")
                    os._exit(1)
        threading.Thread(target=_watchdog, daemon=True).start()

        scan = 0
        while True:
            try:
                scan += 1
                _beat()
                log.info(f"\n── Scan #{scan}  {datetime.now().strftime('%H:%M:%S')} ──")

                self.read_control()   # phone pause/resume/midline toggle

                if self.session_pnl <= -abs(self.cfg["max_daily_loss_usdc"]):
                    log.warning(f"Daily loss cap hit (${self.session_pnl:.2f}). Halting.")
                    break

                if self.tracker.needs_refresh() or not self.tracker.top_wallets:
                    self.tracker.fetch_leaderboard()

                if not self.tracker.top_wallets:
                    time.sleep(self.cfg["scan_interval_sec"])
                    continue

                log.info("Fetching trader positions…")
                self.tracker.refresh_positions()
                _beat()

                # ── Track every move in the backend ────────────────────────
                self.move_tracker.diff_and_log(self.tracker)
                _beat()

                # ── (win-rate tracking disabled: censored metric — see config) ──
                if self.cfg.get("winrate_tracking_enabled", False):
                    try:
                        self.move_tracker.update_win_rates(self.tracker, self.cfg)
                    except Exception as e:
                        log.debug(f"win-rate update error: {e}")

                log.info("Fetching trader activity…")
                cutoff = _now() - self.cfg["insider_lookback_sec"]
                self.tracker.refresh_activity(since_ts=cutoff)
                _beat()

                self._news_lookups = 0   # reset news-fetch budget for this scan

                insider_sigs   = find_insider(self.tracker, self.cache, self.cfg)
                _beat()
                consensus_sigs = find_consensus(self.tracker, self.cache, self.cfg,
                                               scalpers=self.move_tracker.scalper_wallets())
                _beat()
                midline_sigs   = find_midline(self.tracker, self.cache, self.cfg)
                _beat()

                # ── News-driven signals (off by default; riskiest layer) ──
                news_sigs = []
                if self.cfg.get("news_signals_enabled") and self.newsfeed:
                    news_sigs = self._find_news_signals(
                        insider_sigs + midline_sigs + consensus_sigs)
                    _beat()

                log.info(
                    f"Raw signals → consensus: {len(consensus_sigs)}, "
                    f"insider: {len(insider_sigs)}, "
                    f"midline: {len(midline_sigs)}, "
                    f"news: {len(news_sigs)}"
                )

                # Priority: insider (fastest) → midline (cheapest) → consensus → news
                if self.paused:
                    log.info("[CONTROL] paused — skipping new entries (still monitoring exits)")
                else:
                    for sig in insider_sigs + midline_sigs + consensus_sigs + news_sigs:
                        if len(self.positions) >= self.cfg["max_open_positions"]:
                            break
                        self.place_order(sig)
                        _beat()

                self.monitor_positions()
                _beat()
                self._save()
                self.write_status()   # publish status.json for the phone
                self.print_stats()

                # ── Near-real-time exits ─────────────────────────────────
                # Between full scans, keep re-pricing only the markets we
                # actually hold (force-refresh, ignoring cache TTL) and run
                # the tested exit logic, so the 50¢ midline target and stops
                # fire within seconds instead of waiting a whole scan.
                interval = self.cfg["scan_interval_sec"]
                fast     = self.cfg.get("fast_monitor_interval_sec", 0)
                if fast and fast < interval:
                    log.info(f"Sleeping {interval}s (live exit-check every {fast}s)…")
                    waited = 0
                    while waited < interval:
                        time.sleep(min(fast, interval - waited))
                        waited += fast
                        try:
                            held = [p["condition_id"] for p in self.positions.values()]
                            self.cache.refresh(held)
                            self.monitor_positions()
                            self._save()
                            _beat()
                        except Exception as e:
                            log.error(f"fast exit-monitor error: {e}")
                else:
                    log.info(f"Sleeping {interval}s… (Ctrl+C to stop)")
                    time.sleep(interval)

            except KeyboardInterrupt:
                log.info("Stopped by user")
                break
            except Exception as e:
                log.error(f"Unexpected error: {e}", exc_info=True)
                time.sleep(30)

        self._save()
        self.print_stats()
        log.info("Bot stopped.")


# ═══════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Polymarket Self-Learning Top-Trader Copy Bot")
    parser.add_argument("--setup",     action="store_true",
                        help="Derive CLOB API credentials from wallet")
    parser.add_argument("--dry-run",   action="store_true",
                        help="Simulate only — no real orders")
    parser.add_argument("--bootstrap", action="store_true",
                        help="Pre-load brain from Polymarket history (run once)")
    args = parser.parse_args()

    if args.dry_run:
        CONFIG["dry_run"] = True

    bot = PolymarketBot(CONFIG)

    if args.setup:
        if not CONFIG["private_key"]:
            log.error("Set POLYMARKET_PRIVATE_KEY in .env first")
            sys.exit(1)
        bot.setup_credentials()
        return

    if args.bootstrap:
        log.info("Bootstrapping brain from Polymarket history…")
        bot.brain.bootstrap_from_polymarket(months=CONFIG["bootstrap_months"])
        bot.brain.print_brain()
        log.info("Done. Now run the bot normally.")
        return

    bot.run()


if __name__ == "__main__":
    main()
