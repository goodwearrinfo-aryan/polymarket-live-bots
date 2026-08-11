#!/usr/bin/env python3
"""
orderflow_signals.py — Real-time order flow metrics from Binance (free, no auth).

Three signals matching footprint-chart reading:
  delta     : net aggressive volume (market buys − market sells) last 15m
              >0 = buyers in control, <0 = sellers in control
  book_imb  : bid/ask size ratio at top 5 DOM levels
              >1.0 = bid-heavy (support), <1.0 = ask-heavy (resistance)
  poc_dist  : % distance from current price to daily POC (24h volume-profile peak)
              negative = price below POC (magnet above), positive = above POC

All from public Binance REST — no API key, no ccxt dependency.

Usage (from candle_knowledge_logger or standalone):
  from orderflow_signals import get_of
  of = get_of("ETHUSDT", klines_24h=kl[-96:])
  # -> {"delta": 1234.5, "book_imb": 1.23, "poc_dist": -0.004}

Standalone smoke-test:
  python3 orderflow_signals.py ETHUSDT
"""
import json, sys, time, urllib.request

UA = {"User-Agent": "Mozilla/5.0"}
_CACHE: dict = {}
_TTL = 60          # 1-minute cache — OF is faster-moving than candle signals


# ── helpers ────────────────────────────────────────────────────────────────────

def _fetch(url, timeout=10):
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception as e:
        sys.stderr.write(f"[orderflow] {url[:70]}… failed: {e}\n")
        return None


# ── three signals ──────────────────────────────────────────────────────────────

def _delta(symbol):
    """Net aggressive volume from last 1000 aggTrades clipped to the past 15m.

    Binance aggTrades: m=True → maker side = market SELL (aggressive seller);
                       m=False → taker filled a resting ask = market BUY.
    Returns {"net", "buy", "sell", "pct"} or None.
    pct = net / (buy+sell) * 100 — useful for normalizing across coins.
    """
    data = _fetch(f"https://api.binance.com/api/v3/aggTrades?symbol={symbol}&limit=1000")
    if not data:
        return None
    cutoff = int(time.time() * 1000) - 15 * 60 * 1000
    buy = sell = 0.0
    for t in data:
        if t.get("T", 0) < cutoff:
            continue
        qty = float(t.get("q", 0))
        if t.get("m"):
            sell += qty
        else:
            buy += qty
    total = buy + sell
    pct = round((buy - sell) / total * 100, 2) if total > 0 else 0.0
    return {"net": round(buy - sell, 4), "buy": round(buy, 4),
            "sell": round(sell, 4), "pct": pct}


def _book_imb(symbol, levels=5):
    """Bid/ask size ratio at the top `levels` DOM price levels.

    >1.0 = more size defending the bid (bullish pressure),
    <1.0 = more size on the ask (selling wall).
    """
    data = _fetch(f"https://api.binance.com/api/v3/depth?symbol={symbol}&limit=20")
    if not data:
        return None
    bid_sz = sum(float(b[1]) for b in data.get("bids", [])[:levels])
    ask_sz = sum(float(a[1]) for a in data.get("asks", [])[:levels])
    if ask_sz == 0:
        return None
    return round(bid_sz / ask_sz, 4)


def _poc_dist(symbol, klines_24h):
    """% distance from last close to the daily Point of Control.

    POC = price level (bucketed into 100 bins across the session's range) with the
    most total volume traded — the price the market keeps returning to.

    klines_24h must be a list of dicts with keys h, l, c, v (volume).
    Returns float or None.
    """
    if not klines_24h or len(klines_24h) < 4:
        return None
    valid = [k for k in klines_24h if k.get("v", 0) > 0]
    if not valid:
        return None
    lo = min(k["l"] for k in valid)
    hi = max(k["h"] for k in valid)
    if hi <= lo:
        return None

    BINS = 100
    bucket_size = (hi - lo) / BINS
    vol_by_bin = [0.0] * BINS

    for k in valid:
        klo, khi, kvol = k["l"], k["h"], float(k.get("v", 0))
        b_start = int((klo - lo) / bucket_size)
        b_end = min(int((khi - lo) / bucket_size), BINS - 1)
        span = max(b_end - b_start + 1, 1)
        per_bin = kvol / span
        for b in range(b_start, b_end + 1):
            vol_by_bin[b] += per_bin

    poc_bin = vol_by_bin.index(max(vol_by_bin))
    poc_price = lo + (poc_bin + 0.5) * bucket_size
    last_c = valid[-1]["c"]
    if poc_price <= 0:
        return None
    return round(last_c / poc_price - 1, 6)


# ── main entry ─────────────────────────────────────────────────────────────────

def get_of(symbol, klines_24h=None):
    """Return {"delta", "book_imb", "poc_dist"} for symbol. Results cached 60s.

    klines_24h — list of kline dicts with h, l, c, v keys (e.g. closed[-96:] for
    a 15m logger gives the last 24h). Pass None to skip poc_dist.
    """
    now = time.time()
    cached = _CACHE.get(symbol)
    if cached and now - cached["ts"] < _TTL:
        return cached["of"]

    d = _delta(symbol)
    result = {
        "delta":     d["net"] if d else None,
        "delta_pct": d["pct"] if d else None,
        "book_imb":  _book_imb(symbol),
        "poc_dist":  _poc_dist(symbol, klines_24h) if klines_24h is not None else None,
    }
    _CACHE[symbol] = {"ts": now, "of": result}
    return result


def get_klines(symbol, limit=500):
    """Fetch 15m OHLCV klines from Binance for a symbol.

    Returns list of {"t", "o", "h", "l", "c", "v"} dicts, or [] on failure.
    Used by dashboard.py so it can import from one place rather than duplicating the fetch.
    """
    data = _fetch(
        f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=15m&limit={limit}"
    )
    if not data:
        return []
    return [{"t": int(c[0]), "o": float(c[1]), "h": float(c[2]),
             "l": float(c[3]), "c": float(c[4]), "v": float(c[5])} for c in data]


if __name__ == "__main__":
    sym = sys.argv[1] if len(sys.argv) > 1 else "BTCUSDT"
    print(f"Order flow for {sym}:")
    of = get_of(sym)
    for k, v in of.items():
        print(f"  {k:12s} = {v}")
