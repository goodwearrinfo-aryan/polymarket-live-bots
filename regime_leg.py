"""
regime_leg.py — BTC market regime detector for coinflip/crypto legs.

Ported from rafaelfariasbsb/polymarket signal_engine.py (regime detection only).
Uses Binance REST (no WebSocket — bot runs once/60s via watchdog).

Returns one of: TREND_UP / TREND_DOWN / RANGE / CHOP

CHOP  → suppress crypto directional legs (signal halved or skipped)
TREND → boost leg score in trend direction, fade counter-trend signals
RANGE → neutral, no adjustment

Used by: coinup, coindown, diverg, btc15no, feargreed
"""

import time
import json
import os
import urllib.request
import logging

log = logging.getLogger(__name__)

CACHE_FILE = os.path.expanduser("~/Documents/polymarket/scalp_lab_cache.json")
CACHE_TTL  = 300   # 5 min — one regime per bot cycle

# EMA periods (from rafaelfariasbsb signal_engine.py)
EMA_FAST   = 5
EMA_SLOW   = 12
EMA_TREND_THRESHOLD = 0.003   # 0.3% EMA gap = trending

# ATR period + chop threshold
ATR_PERIOD = 14
ATR_CHOP_THRESHOLD = 0.001   # ATR/close < 0.1% on 15-min candles = choppy


def _ema(values: list, period: int) -> float:
    if not values:
        return 0.0
    k = 2 / (period + 1)
    e = values[0]
    for v in values[1:]:
        e = v * k + e * (1 - k)
    return e


def _fetch_klines(symbol: str = "BTCUSDT", interval: str = "1m", limit: int = 20) -> list:
    """Binance REST klines — no API key needed for public market data."""
    url = (f"https://api.binance.com/api/v3/klines"
           f"?symbol={symbol}&interval={interval}&limit={limit}")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "scalp_lab/1.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read())
        # Each item: [open_time, open, high, low, close, volume, ...]
        return [{"o": float(c[1]), "h": float(c[2]), "l": float(c[3]),
                 "c": float(c[4]), "v": float(c[5])} for c in data]
    except Exception as e:
        log.warning(f"regime_leg fetch_klines error: {e}")
        return []


def detect_regime(symbol: str = "BTCUSDT") -> str:
    """
    Returns: TREND_UP | TREND_DOWN | RANGE | CHOP

    Uses 15-min candles — same timeframe as the coinflip/btc15no markets.
    Cached for CACHE_TTL seconds.  Returns RANGE on any fetch failure (fail-open).
    """
    cache_key = f"regime_{symbol}"
    try:
        with open(CACHE_FILE) as f:
            cache = json.load(f)
    except Exception:
        cache = {}

    entry = cache.get(cache_key, {})
    if entry and time.time() - entry.get("ts", 0) < CACHE_TTL:
        return entry["regime"]

    candles = _fetch_klines(symbol, "15m", 20)
    if len(candles) < EMA_SLOW + 2:
        return "RANGE"

    closes = [c["c"] for c in candles]
    fast_ema = _ema(closes, EMA_FAST)
    slow_ema = _ema(closes, EMA_SLOW)

    # EMA gap as fraction of price
    ema_diff = (fast_ema - slow_ema) / slow_ema if slow_ema > 0 else 0

    # ATR (14-period)
    trs = []
    for i in range(1, len(candles)):
        h, l, pc = candles[i]["h"], candles[i]["l"], candles[i - 1]["c"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    atr = sum(trs[-ATR_PERIOD:]) / ATR_PERIOD if trs else 0
    atr_pct = atr / closes[-1] if closes[-1] > 0 else 0

    # Regime logic
    if atr_pct < ATR_CHOP_THRESHOLD:
        regime = "CHOP"
    elif ema_diff > EMA_TREND_THRESHOLD:
        regime = "TREND_UP"
    elif ema_diff < -EMA_TREND_THRESHOLD:
        regime = "TREND_DOWN"
    else:
        regime = "RANGE"

    log.debug(f"regime_leg {symbol}: ema_diff={ema_diff:.4f} atr_pct={atr_pct:.4f} → {regime}")

    cache[cache_key] = {"ts": time.time(), "regime": regime}
    try:
        with open(CACHE_FILE, "w") as f:
            json.dump(cache, f)
    except Exception:
        pass

    return regime


def regime_ok_for_leg(leg: str, regime: str) -> bool:
    """
    Gate function: True = leg may fire, False = skip.

    coinup  / feargreed-YES : only in TREND_UP or RANGE (not CHOP, not TREND_DOWN)
    coindown / btc15no-NO   : only in TREND_DOWN or RANGE
    diverg                  : RANGE and TREND both ok; CHOP = skip
    """
    if regime == "CHOP":
        return False   # never trade in chop
    if leg in ("coinup",):
        return regime in ("TREND_UP", "RANGE")
    if leg in ("coindown",):
        return regime in ("TREND_DOWN", "RANGE")
    if leg in ("btc15no",):
        return regime in ("TREND_UP", "RANGE")  # fade dips: only sensible when not already trending down
    # diverg, feargreed, others — allow TREND_UP + TREND_DOWN + RANGE
    return True


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.DEBUG)
    r = detect_regime("BTCUSDT")
    print(f"BTC regime: {r}")
    for leg in ("coinup", "coindown", "diverg", "btc15no"):
        ok = regime_ok_for_leg(leg, r)
        print(f"  {leg}: {'OK' if ok else 'SKIP'}")
