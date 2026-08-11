"""
candle_signals.py — live candle-based signals from Binance (free, no auth).

Fetches the last N x 15m candles for a coin and computes:
  - EMA(20) and EMA(50)
  - RSI(14)
  - Momentum (close / close[N] - 1)

Returns a dict:
  {
    "trend":    "up" | "down" | "flat",
    "rsi":      float,
    "momentum": float,   # e.g. +0.03 = +3% over last 20 candles
    "price":    float,
  }

Cache TTL: 10 min (matches Polymarket scan interval).
"""

import time
import json
import urllib.request
import sys

_CACHE: dict = {}      # coin_id → {"signal": {...}, "ts": float}
_TTL = 600             # 10 minutes

# CoinGecko coin_id → Binance symbol
_SYMBOL_MAP = {
    "bitcoin":     "BTCUSDT",
    "ethereum":    "ETHUSDT",
    "binancecoin": "BNBUSDT",
    "solana":      "SOLUSDT",
    "ripple":      "XRPUSDT",
    "dogecoin":    "DOGEUSDT",
    "cardano":     "ADAUSDT",
    "chainlink":   "LINKUSDT",
    "toncoin":     "TONUSDT",
    "tron":        "TRXUSDT",
    "shiba-inu":   "SHIBUSDT",
    "sui":         "SUIUSDT",
    "near":        "NEARUSDT",
    "cosmos":      "ATOMUSDT",
    "uniswap":     "UNIUSDT",
    "aptos":       "APTUSDT",
    "arbitrum":    "ARBUSDT",
    "optimism":    "OPUSDT",
    "pepe":        "PEPEUSDT",
    "dogwifhat":   "WIFUSDT",
    "bonk":        "BONKUSDT",
    "floki":       "FLOKIUSDT",
    "avalanche-2": "AVAXUSDT",
}


def _ema(values: list[float], period: int) -> list[float]:
    if len(values) < period:
        return []
    k = 2 / (period + 1)
    emas = [sum(values[:period]) / period]
    for v in values[period:]:
        emas.append(v * k + emas[-1] * (1 - k))
    return emas


def _rsi(closes: list[float], period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains  = [max(d, 0) for d in deltas[-period:]]
    losses = [abs(min(d, 0)) for d in deltas[-period:]]
    avg_g  = sum(gains) / period
    avg_l  = sum(losses) / period
    if avg_l == 0:
        return 100.0
    rs = avg_g / avg_l
    return round(100 - 100 / (1 + rs), 2)


def _fetch_candles(symbol: str, limit: int = 100) -> list[float]:
    """Fetch close prices from Binance, with fallback to crypto-trader JSON.

    Priority:
      1. Crypto-trader JSON (~/trader/data/candles_<pair>_15m.json) if fresh
         — no network call, single source of truth across polymarket legs
      2. Binance REST klines (original behavior)
    """
    from trader_candles import load_candles_or_fetch

    def _binance_fetch():
        url = (f"https://api.binance.com/api/v3/klines"
               f"?symbol={symbol}&interval=15m&limit={limit}")
        with urllib.request.urlopen(url, timeout=10) as r:
            data = json.loads(r.read())
        return [float(c[4]) for c in data]  # close prices

    try:
        closes = load_candles_or_fetch(
            symbol, "15m",
            binance_fetcher=_binance_fetch,
            log_prefix="[candle_signals] ",
        )
        return closes
    except Exception as e:
        sys.stderr.write(f"[candle_signals] fetch {symbol} failed: {e}\n")
        return []


def get_signal(coin_id: str) -> dict | None:
    """Return candle signal for a CoinGecko coin_id, cached 10 min."""
    now = time.time()
    cached = _CACHE.get(coin_id)
    if cached and now - cached["ts"] < _TTL:
        return cached["signal"]

    symbol = _SYMBOL_MAP.get(coin_id)
    if not symbol:
        return None

    closes = _fetch_candles(symbol, limit=100)
    if len(closes) < 55:
        return None

    ema20_series = _ema(closes, 20)
    ema50_series = _ema(closes, 50)
    if not ema20_series or not ema50_series:
        return None

    ema20 = ema20_series[-1]
    ema50 = ema50_series[-1]
    rsi   = _rsi(closes[-30:])   # RSI on last 30 closes
    price = closes[-1]
    mom   = round((closes[-1] / closes[-20] - 1), 4) if closes[-20] > 0 else 0.0
    # 24h change: 96 x 15m candles back (limit=100 gives us exactly enough)
    chg24 = round((closes[-1] / closes[-96] - 1), 4) if len(closes) >= 96 and closes[-96] > 0 else 0.0

    if ema20 > ema50 * 1.002:
        trend = "up"
    elif ema20 < ema50 * 0.998:
        trend = "down"
    else:
        trend = "flat"

    # MACD(12/26/9) for the macdsig leg (MoonDev mean-reversion port, 2026-06-13)
    _macd_line = _macd_sig = None
    _e12, _e26 = _ema(closes, 12), _ema(closes, 26)
    if _e12 and _e26:
        _n = min(len(_e12), len(_e26))
        _macd_series = [_e12[-_n + i] - _e26[-_n + i] for i in range(_n)]
        _macd_line = _macd_series[-1]
        _sig_series = _ema(_macd_series, 9)
        _macd_sig = _sig_series[-1] if _sig_series else None

    signal = {
        "trend":    trend,
        "rsi":      rsi,
        "momentum": mom,
        "chg24h":   chg24,
        "price":    round(price, 6),
        "ema20":    round(ema20, 6),
        "ema50":    round(ema50, 6),
        "macd":     round(_macd_line, 6) if _macd_line is not None else None,
        "macd_sig": round(_macd_sig, 6) if _macd_sig is not None else None,
        "dev":      round(price / ema20 - 1, 4) if ema20 > 0 else None,
    }
    _CACHE[coin_id] = {"signal": signal, "ts": now}
    return signal


def bulk_signals(coin_ids: list[str]) -> dict:
    """Fetch signals for multiple coins. Returns {coin_id: signal}."""
    return {cid: sig for cid in coin_ids if (sig := get_signal(cid)) is not None}
