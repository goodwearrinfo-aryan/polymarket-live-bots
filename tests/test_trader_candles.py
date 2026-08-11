"""Tests for trader_candles.py — runs against a temp dir, no real Binance."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from trader_candles import (
    _filename,
    _to_trader_pair,
    load_candles_or_fetch,
    load_from_trader,
)


def _write_fake_trader_file(data_dir: Path, symbol: str, interval: str, n: int = 5) -> Path:
    pair = _to_trader_pair(symbol)
    safe_pair = pair.replace("/", "_")
    path = data_dir / f"candles_{safe_pair}_{interval}.json"
    base = datetime(2026, 6, 22, 12, 0, tzinfo=timezone.utc)
    payload = {
        "pair": pair,
        "timeframe": interval,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "n": n,
        "candles": [
            {
                "ts": base.replace(minute=i).isoformat(),
                "o": 100.0 + i,
                "h": 101.0 + i,
                "l": 99.0 + i,
                "c": 100.5 + i,
                "v": 10.0,
            }
            for i in range(n)
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_to_trader_pair_common_symbols():
    assert _to_trader_pair("BTCUSDT") == "BTC/USDT"
    assert _to_trader_pair("ETHUSDT") == "ETH/USDT"
    assert _to_trader_pair("SOLUSDT") == "SOL/USDT"
    assert _to_trader_pair("UNKNOWN") is None


def test_filename_format():
    assert _filename("BTCUSDT", "15m") == "candles_BTC_USDT_15m.json"


def test_load_returns_none_when_missing(tmp_path):
    result = load_from_trader("BTCUSDT", "15m", data_dir=tmp_path)
    assert result is None


def test_load_returns_binance_shape(tmp_path):
    _write_fake_trader_file(tmp_path, "BTCUSDT", "15m", n=5)
    out = load_from_trader("BTCUSDT", "15m", data_dir=tmp_path)
    assert out is not None
    assert len(out) == 5
    # Binance shape: [ts_ms, o, h, l, c, v]
    first = out[0]
    assert len(first) == 6
    assert isinstance(first[0], int)   # ts_ms
    assert first[1] == 100.0           # open
    assert first[4] == 100.5          # close


def test_load_returns_none_when_stale(tmp_path):
    import os, time
    path = _write_fake_trader_file(tmp_path, "BTCUSDT", "15m", n=3)
    old = time.time() - 600
    os.utime(path, (old, old))
    result = load_from_trader("BTCUSDT", "15m", data_dir=tmp_path, max_age_seconds=300)
    assert result is None


def test_load_or_fetch_uses_trader_when_fresh(tmp_path):
    _write_fake_trader_file(tmp_path, "BTCUSDT", "15m", n=5)
    sentinel = {"called": False}
    def fallback():
        sentinel["called"] = True
        return []
    out = load_candles_or_fetch(
        "BTCUSDT", "15m",
        binance_fetcher=fallback,
        data_dir=tmp_path,
    )
    assert len(out) == 5
    assert sentinel["called"] is False  # fallback NOT called


def test_load_or_fetch_falls_back_to_binance(tmp_path):
    def fallback():
        return [[1, 2, 3, 4, 5, 6], [7, 8, 9, 10, 11, 12]]
    out = load_candles_or_fetch(
        "BTCUSDT", "15m",
        binance_fetcher=fallback,
        data_dir=tmp_path,  # empty — no trader file
    )
    assert len(out) == 2


def test_load_or_fetch_returns_empty_when_both_fail(tmp_path):
    def fallback():
        raise ConnectionError("binance down")
    out = load_candles_or_fetch(
        "BTCUSDT", "15m",
        binance_fetcher=fallback,
        data_dir=tmp_path,
    )
    assert out == []