#!/usr/bin/env python3
"""
signal_mine.py — test multiple candle signals for 8h directional edge.
All tested on the same 12 coins / 10y of 15m candles in PostgreSQL.

HONESTY NOTE: this is in-sample mining of N signals — the best one's edge is
inflated by selection (same trap DSR corrects for in the lab). Any winner here
needs a forward paper leg before being believed.
"""
import math
import psycopg2

H = 32  # 8h in 15m candles
COINS = ["btcusdt", "ethusdt", "solusdt", "bnbusdt", "xrpusdt", "dogeusdt",
         "adausdt", "linkusdt", "avaxusdt", "ltcusdt", "maticusdt", "tonusdt"]

con = psycopg2.connect(dbname="aryanagarwal")
cur = con.cursor()

# Load all coins once: close, volume, taker_buy_vol, open_time
data = {}
for c in COINS:
    cur.execute(f"SELECT open_time, close, volume, taker_buy_vol FROM candles_{c} ORDER BY open_time")
    rows = cur.fetchall()
    data[c] = {
        "t":  [r[0] for r in rows],
        "cl": [float(r[1]) for r in rows],
        "v":  [float(r[2]) for r in rows],
        "tb": [float(r[3]) for r in rows],
    }
con.close()

def test(name, setup_fn):
    """setup_fn(d, i) -> +1 (predict up), -1 (predict down), 0 (no setup)"""
    hits = n = 0
    for c in COINS:
        d = data[c]
        cl = d["cl"]
        for i in range(100, len(cl) - H):
            s = setup_fn(d, i)
            if s == 0:
                continue
            n += 1
            up = cl[i + H] > cl[i]
            if (s > 0 and up) or (s < 0 and not up):
                hits += 1
    if n == 0:
        print(f"{name:<28} no setups")
        return
    p = hits / n
    se = math.sqrt(0.25 / n)
    z = (p - 0.5) / se
    print(f"{name:<28} n={n:>9,}  hit={100*p:5.1f}%  z={z:+6.1f}")

# 1. Momentum continuation: 24h return sign predicts next 8h
def mom24(d, i):
    if i < 96: return 0
    r = d["cl"][i] / d["cl"][i-96] - 1
    if r > 0.03: return +1
    if r < -0.03: return -1
    return 0

# 2. Mean reversion: big 8h move reverses
def mr8(d, i):
    if i < 32: return 0
    r = d["cl"][i] / d["cl"][i-32] - 1
    if r > 0.04: return -1
    if r < -0.04: return +1
    return 0

# 3. Volume spike + direction: 4h vol > 3x prior 24h avg, follow the move
def volspike(d, i):
    if i < 112: return 0
    v4 = sum(d["v"][i-16:i])
    v24avg = sum(d["v"][i-112:i-16]) / 6
    if v24avg <= 0 or v4 < 3 * v24avg: return 0
    r = d["cl"][i] / d["cl"][i-16] - 1
    if r > 0.01: return +1
    if r < -0.01: return -1
    return 0

# 4. Taker-buy pressure: 8h taker-buy ratio extreme
def takerbuy(d, i):
    if i < 32: return 0
    tb = sum(d["tb"][i-32:i]); v = sum(d["v"][i-32:i])
    if v <= 0: return 0
    ratio = tb / v
    if ratio > 0.56: return +1
    if ratio < 0.44: return -1
    return 0

# 5. Time-of-day: UTC hour drift (US open 13-16 UTC up bias?)
def tod(d, i):
    hour = (d["t"][i] // 3600000) % 24
    if 13 <= hour <= 15: return +1
    return 0

# 6. Candlesig reference (RSI+EMA from earlier backtest, for comparison)
import statistics
def make_indicators():
    ind = {}
    for c in COINS:
        cl = data[c]["cl"]
        k20, k50 = 2/21, 2/51
        e20 = [None]*len(cl); e50 = [None]*len(cl)
        if len(cl) > 50:
            a = sum(cl[:20])/20; b = sum(cl[:50])/50
            e20[19] = a; e50[49] = b
            for i in range(20, len(cl)):
                a = cl[i]*k20 + a*(1-k20); e20[i] = a
            for i in range(50, len(cl)):
                b = cl[i]*k50 + b*(1-k50); e50[i] = b
        # RSI14 wilder
        rsi = [None]*len(cl)
        if len(cl) > 15:
            g = l = 0.0
            for i in range(1, 15):
                dd = cl[i]-cl[i-1]; g += max(dd,0); l += abs(min(dd,0))
            ag, al = g/14, l/14
            rsi[14] = 100.0 if al == 0 else 100-100/(1+ag/al)
            for i in range(15, len(cl)):
                dd = cl[i]-cl[i-1]
                ag = (ag*13+max(dd,0))/14; al = (al*13+abs(min(dd,0)))/14
                rsi[i] = 100.0 if al == 0 else 100-100/(1+ag/al)
        ind[c] = (e20, e50, rsi)
    return ind
IND = make_indicators()
def candlesig(d, i):
    c = next(k for k in COINS if data[k] is d)
    e20, e50, rsi = IND[c]
    if e20[i] is None or e50[i] is None or rsi[i] is None: return 0
    if e20[i] > e50[i]*1.002 and rsi[i] < 45: return +1
    if e20[i] < e50[i]*0.998 and rsi[i] > 55: return -1
    return 0

print(f"{'signal':<28} {'setups':>11}  {'hit@8h':>9}  {'z':>8}")
print("-" * 60)
test("mom24 (continuation >3%)", mom24)
test("meanrev8 (reverse >4%)", mr8)
test("volspike (3x + follow)", volspike)
test("takerbuy (ratio extreme)", takerbuy)
test("timeofday (US open up)", tod)
test("candlesig (RSI+EMA ref)", candlesig)
