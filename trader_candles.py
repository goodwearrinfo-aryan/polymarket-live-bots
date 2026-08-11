"""trader_candles.py — read candles from the crypto-trader's shared JSON files.

The crypto trader (~/trader) serializes its per-pair candle buffers to JSON
every 60s in `~/trader/data/candles_<pair>_<tf>.json`. Polymarket legs
(`candle_signals.py`, `btc_touch_daytrade.py`) can fall back to this shared
source when their own Binance fetch is unavailable (offline, rate-limited,
sandboxed, etc).

Usage:
    from trader_candles import load_candles_or_fetch

    candles = load_candles_or_fetch(
        symbol="BTCUSDT",
        interval="15m",
        limit=500,
        binance_fetcher=lambda: fetch_from_binance(...),
        max_age_seconds=300,
    )

Pattern:
    1. Try the trader JSON file (if exists, fresh enough).
    2. Fall back to the supplied `binance_fetcher` callable.
    3. Returns a list of candle dicts in the same shape Binance returns
       (so consumers don't need to translate).

Candle shape returned: list of dicts with keys
    {"timestamp": ms, "open": float, "high": float, "low": float, "close": float, "volume": float}
matching what the polymarket legs expect.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

# Default location of the trader's data dir
TRADER_DATA_DIR = Path.home() / "trader" / "data"

# Map Binance symbol + interval -> our filename
# Binance uses no slash (BTCUSDT), our trader uses BTC/USDT
_SYMBOL_TO_FILE = {
    "BTCUSDT": "BTC_USDT",
    "ETHUSDT": "ETH_USDT",
}


def _to_trader_pair(symbol: str) -> str | None:
    """BTCUSDT -> BTC/USDT (matching trader's CandleBuffer keys)."""
    # Common crypto symbols
    for bare, slashed in [
        ("BTCUSDT", "BTC/USDT"),
        ("ETHUSDT", "ETH/USDT"),
        ("BTCUSDC", "BTC/USDC"),
        ("ETHUSDC", "ETH/USDC"),
    ]:
        if symbol == bare:
            return slashed
    # Generic: try to split on common quote currencies
    for quote in ("USDT", "USDC", "BUSD", "USD"):
        if symbol.endswith(quote):
            base = symbol[: -len(quote)]
            return f"{base}/{quote}"
    return None


def _filename(symbol: str, interval: str) -> str:
    pair = _to_trader_pair(symbol) or symbol
    safe_pair = pair.replace("/", "_")
    safe_int = interval.replace("m", "m").replace("h", "h")  # already ASCII
    return f"candles_{safe_pair}_{safe_int}.json"


def load_from_trader(
    symbol: str,
    interval: str,
    *,
    data_dir: Path | None = None,
    max_age_seconds: int = 300,
) -> list[dict] | None:
    """Try to load fresh candles from the trader's JSON.

    Returns a list of candle dicts in Binance-compatible shape, or None
    if the file is missing, stale, or unreadable.
    """
    data_dir = data_dir or TRADER_DATA_DIR
    path = data_dir / _filename(symbol, interval)
    if not path.exists():
        return None
    try:
        mtime = path.stat().st_mtime
        if (time.time() - mtime) > max_age_seconds:
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        raw = payload.get("candles", [])
        # Translate trader's {ts, o, h, l, c, v} -> Binance {timestamp_ms, o, h, l, c, v}
        out = []
        for row in raw:
            ts_iso = row.get("ts", "")
            try:
                # ISO 8601 with Z or +00:00 -> ms
                ts_dt = datetime.fromisoformat(ts_iso.replace("Z", "+00:00"))
                ts_ms = int(ts_dt.timestamp() * 1000)
            except Exception:
                continue
            out.append([
                ts_ms,
                row.get("o", 0.0),
                row.get("h", 0.0),
                row.get("l", 0.0),
                row.get("c", 0.0),
                row.get("v", 0.0),
            ])
        return out
    except Exception:
        return None


def load_candles_or_fetch(
    symbol: str,
    interval: str,
    *,
    binance_fetcher: Callable[[], list],
    max_age_seconds: int = 300,
    data_dir: Path | None = None,
    log_prefix: str = "",
) -> list:
    """Try trader JSON first, then fall back to `binance_fetcher()`.

    The fallback is silent on success; logs to stderr if neither source
    works so debugging is easier.
    """
    trader_data = load_from_trader(symbol, interval, max_age_seconds=max_age_seconds, data_dir=data_dir)
    if trader_data is not None and len(trader_data) > 0:
        return trader_data
    try:
        return binance_fetcher()
    except Exception as e:
        import sys as _sys
        _sys.stderr.write(f"{log_prefix}candles fetch failed (trader + binance): {e}\n")
        return []