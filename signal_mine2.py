#!/usr/bin/env python3
"""signal_mine2.py — round 2: more signals + the coinrev x RSI combo. 8h horizon.
Same honesty caveat: in-sample, N comparisons, winners inflated by selection."""
import math, psycopg2
from datetime import datetime, timezone

H = 32
COINS = ["btcusdt","ethusdt","solusdt","bnbusdt","xrpusdt","dogeusdt",
         "adausdt","linkusdt","avaxusdt","ltcusdt","maticusdt","tonusdt"]

con = psycopg2.connect(dbname="aryanagarwal")
cur = con.cursor()
data = {}
for c in COINS:
    cur.execute(f"SELECT open_time, close, high, low FROM candles_{c} ORDER BY open_time")
    rows = cur.fetchall()
    data[c] = {"t":[r[0] for r in rows], "cl":[float(r[1]) for r in rows],
               "hi":[float(r[2]) for r in rows], "lo":[float(r[3]) for r in rows]}
con.close()

# Precompute RSI(14) wilder + SMA20/std20 for Bollinger per coin
IND = {}
for c in COINS:
    cl = data[c]["cl"]; n = len(cl)
    rsi = [None]*n
    if n > 15:
        g=l=0.0
        for i in range(1,15):
            d = cl[i]-cl[i-1]; g += max(d,0); l += abs(min(d,0))
        ag, al = g/14, l/14
        rsi[14] = 100.0 if al==0 else 100-100/(1+ag/al)
        for i in range(15,n):
            d = cl[i]-cl[i-1]
            ag = (ag*13+max(d,0))/14; al = (al*13+abs(min(d,0)))/14
            rsi[i] = 100.0 if al==0 else 100-100/(1+ag/al)
    # rolling SMA20 + std via cumulative sums
    sma = [None]*n; sd = [None]*n
    s = sq = 0.0
    for i in range(n):
        s += cl[i]; sq += cl[i]*cl[i]
        if i >= 20:
            s -= cl[i-20]; sq -= cl[i-20]*cl[i-20]
        if i >= 19:
            m = s/20
            var = max(sq/20 - m*m, 0)
            sma[i] = m; sd[i] = math.sqrt(var)
    IND[c] = (rsi, sma, sd)

def test(name, fn):
    hits = n = 0
    for c in COINS:
        d = data[c]; cl = d["cl"]
        rsi, sma, sd = IND[c]
        for i in range(100, len(cl)-H):
            s = fn(d, cl, rsi, sma, sd, i)
            if s == 0: continue
            n += 1
            up = cl[i+H] > cl[i]
            if (s>0 and up) or (s<0 and not up): hits += 1
    if n == 0:
        print(f"{name:<34} no setups"); return None
    p = hits/n; z = (p-0.5)/math.sqrt(0.25/n)
    print(f"{name:<34} n={n:>9,}  hit={100*p:5.1f}%  z={z:+6.1f}")
    return p

# 1. RSI extremes alone (no EMA filter)
def rsi_ext(d, cl, rsi, sma, sd, i):
    if rsi[i] is None: return 0
    if rsi[i] < 25: return +1   # deeply oversold -> bounce
    if rsi[i] > 75: return -1
    return 0

# 2. Bollinger touch (close beyond 2 sigma)
def boll(d, cl, rsi, sma, sd, i):
    if sma[i] is None or sd[i] == 0: return 0
    if cl[i] > sma[i] + 2*sd[i]: return -1   # fade upper band
    if cl[i] < sma[i] - 2*sd[i]: return +1
    return 0

# 3. Streaks: 6+ consecutive same-color candles -> fade
def streak(d, cl, rsi, sma, sd, i):
    if i < 7: return 0
    ups = downs = 0
    for j in range(i-5, i+1):
        if cl[j] > cl[j-1]: ups += 1
        elif cl[j] < cl[j-1]: downs += 1
    if ups == 6: return -1
    if downs == 6: return +1
    return 0

# 4. Weekend effect: Saturday/Sunday drift
def weekend(d, cl, rsi, sma, sd, i):
    wd = datetime.fromtimestamp(d["t"][i]/1000, tz=timezone.utc).weekday()
    return +1 if wd >= 5 else 0

# 5. BTC leads alts: BTC's last 8h move predicts alt's next 8h (alts only)
btc_cl = data["btcusdt"]["cl"]; btc_t = data["btcusdt"]["t"]
btc_idx = {t: i for i, t in enumerate(btc_t)}
def btclead(d, cl, rsi, sma, sd, i):
    if d is data["btcusdt"]: return 0
    bi = btc_idx.get(d["t"][i])
    if bi is None or bi < 32: return 0
    r = btc_cl[bi]/btc_cl[bi-32] - 1
    if r > 0.02: return +1
    if r < -0.02: return -1
    return 0

# 6. COMBO: coinrev (>7% 24h fade) + RSI agreement
def combo(d, cl, rsi, sma, sd, i):
    if i < 96 or rsi[i] is None: return 0
    r = cl[i]/cl[i-96] - 1
    if r > 0.07 and rsi[i] > 65: return -1   # pumped AND overbought -> fade down
    if r < -0.07 and rsi[i] < 35: return +1  # dumped AND oversold -> fade up
    return 0

# 7. coinrev reference (>7% alone, for comparison)
def coinrev_ref(d, cl, rsi, sma, sd, i):
    if i < 96: return 0
    r = cl[i]/cl[i-96] - 1
    if r > 0.07: return -1
    if r < -0.07: return +1
    return 0

print(f"{'signal':<34} {'setups':>11}  {'hit@8h':>9}  {'z':>8}")
print("-" * 68)
test("rsi extremes (<25 / >75)", rsi_ext)
test("bollinger 2-sigma fade", boll)
test("streak 6 same-color fade", streak)
test("weekend drift (Sat/Sun up)", weekend)
test("btc-leads-alts (8h, >2%)", btclead)
test("COMBO coinrev+RSI confirm", combo)
test("coinrev alone (reference)", coinrev_ref)
