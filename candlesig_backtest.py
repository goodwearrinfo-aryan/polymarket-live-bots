#!/usr/bin/env python3
"""
candlesig_backtest.py — does the RSI(14) + EMA(20/50) 15m signal predict
short-horizon direction AT ALL?

Test (per coin, on full history in PostgreSQL):
  At each candle where the leg WOULD fire:
    bull setup: EMA20 > EMA50*1.002 and RSI < 45  -> predict price HIGHER in H hours
    bear setup: EMA20 < EMA50*0.998 and RSI > 55  -> predict price LOWER in H hours
  Score: % of setups where direction was right after H = 8h and 24h.
  Baseline: unconditional P(up) over the same horizon (must beat this).

This mirrors the live leg: its Polymarket markets resolve on "price above/below
strike in hours-days", so directional hit-rate at 8-24h is the honest proxy.
"""
import sys
import psycopg2

H_CANDLES = {"8h": 32, "24h": 96}   # 15m candles per horizon
COINS = ["btcusdt", "ethusdt", "solusdt", "bnbusdt", "xrpusdt", "dogeusdt",
         "adausdt", "linkusdt", "avaxusdt", "ltcusdt", "maticusdt", "tonusdt"]

def ema_series(closes, period):
    k = 2 / (period + 1)
    out = [None] * len(closes)
    if len(closes) < period:
        return out
    e = sum(closes[:period]) / period
    out[period - 1] = e
    for i in range(period, len(closes)):
        e = closes[i] * k + e * (1 - k)
        out[i] = e
    return out

def rsi_series(closes, period=14):
    out = [None] * len(closes)
    if len(closes) < period + 1:
        return out
    gains = losses = 0.0
    for i in range(1, period + 1):
        d = closes[i] - closes[i - 1]
        gains += max(d, 0); losses += abs(min(d, 0))
    ag, al = gains / period, losses / period
    out[period] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    for i in range(period + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        ag = (ag * (period - 1) + max(d, 0)) / period
        al = (al * (period - 1) + abs(min(d, 0))) / period
        out[i] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return out

con = psycopg2.connect(dbname="aryanagarwal")
cur = con.cursor()

print(f"{'coin':<10} {'horizon':<8} {'setups':>8} {'hit%':>7} {'base%':>7} {'edge':>7}")
print("-" * 52)

total = {h: {"hits": 0, "n": 0, "base_up": 0, "base_n": 0} for h in H_CANDLES}

for coin in COINS:
    cur.execute(f"SELECT close FROM candles_{coin} ORDER BY open_time")
    closes = [float(r[0]) for r in cur.fetchall()]
    if len(closes) < 200:
        continue
    e20 = ema_series(closes, 20)
    e50 = ema_series(closes, 50)
    rsi = rsi_series(closes)

    for hname, H in H_CANDLES.items():
        hits = n = 0
        base_up = base_n = 0
        # sample baseline on every 10th candle (cheap, unbiased)
        for i in range(50, len(closes) - H, 10):
            base_n += 1
            if closes[i + H] > closes[i]:
                base_up += 1
        for i in range(50, len(closes) - H):
            if e20[i] is None or e50[i] is None or rsi[i] is None:
                continue
            bull = e20[i] > e50[i] * 1.002 and rsi[i] < 45
            bear = e20[i] < e50[i] * 0.998 and rsi[i] > 55
            if not (bull or bear):
                continue
            n += 1
            went_up = closes[i + H] > closes[i]
            if (bull and went_up) or (bear and not went_up):
                hits += 1
        if n == 0:
            continue
        hit_pct  = 100 * hits / n
        # directional baseline: bull setups need P(up), bear need P(down).
        # use max(P(up), P(down)) as the toughest fair baseline
        bu = base_up / base_n
        base_pct = 100 * max(bu, 1 - bu)
        print(f"{coin:<10} {hname:<8} {n:>8,} {hit_pct:>6.1f}% {base_pct:>6.1f}% "
              f"{hit_pct - base_pct:>+6.1f}")
        total[hname]["hits"] += hits
        total[hname]["n"] += n
        total[hname]["base_up"] += base_up
        total[hname]["base_n"] += base_n

print("-" * 52)
for hname in H_CANDLES:
    t = total[hname]
    if t["n"] == 0:
        continue
    hp = 100 * t["hits"] / t["n"]
    bu = t["base_up"] / t["base_n"]
    bp = 100 * max(bu, 1 - bu)
    import math
    # binomial z-score vs the baseline
    p0 = bp / 100
    se = math.sqrt(p0 * (1 - p0) / t["n"])
    z  = (hp / 100 - p0) / se if se > 0 else 0
    print(f"{'ALL':<10} {hname:<8} {t['n']:>8,} {hp:>6.1f}% {bp:>6.1f}% "
          f"{hp - bp:>+6.1f}  z={z:+.1f}")
con.close()
