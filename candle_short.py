#!/usr/bin/env python3
"""
candle_short.py — a SELECTIVE short strategy: fade thin-liquidity price spikes.

Thesis (not pattern-spotting): DON'T short every bearish candle. Short only when
BOTH conditions hold on a bar:
  (1) "more than usual price" — close is > Z std-devs above its rolling mean
      (price is stretched/overextended up), AND
  (2) "low liquidity"        — bar volume < LL x its rolling-average volume (thin).
A spike that isn't backed by real volume tends to mean-revert -> short it, cover after H bars.

Honesty is baked in (project lesson: a signal without a control is storytelling):
every config is scored against the UNCONDITIONAL short return (the control) with a z-score,
and the threshold SWEEP shows whether being more selective actually buys more edge or just
fewer trades. On a calibrated, upward-drifting market most configs will read 'no edge' —
that's the truthful result, not a bug.

Modes
-----
  backtest (default): sweep (LL, Z) x horizon across coins; mean short-ret vs control + z.
  signal:  python candle_short.py --signal BTCUSDT   # is the fade armed on the last closed bar?

Keyless: Binance klines (paged, incl. volume) -> CoinGecko OHLC fallback. stdlib, PAPER, $0.

  python candle_short.py
  python candle_short.py --coins BTCUSDT,ETHUSDT --bars 5000 --interval 1h
  python candle_short.py --ll 0.6 --z 1.5 --horizons 5,15        # one explicit config
  python candle_short.py --confluence                            # also require a bearish candle
  python candle_short.py --signal BTCUSDT
"""
import sys, json, math, time, argparse, urllib.request

FEE = 0.0006   # 6 bps/side taker; a short round-trip pays 2x
VN, PN = 20, 20                 # volume-MA window, price mean/std window
CG = {"BTCUSDT": "bitcoin", "ETHUSDT": "ethereum", "SOLUSDT": "solana",
      "BNBUSDT": "binancecoin", "XRPUSDT": "ripple", "DOGEUSDT": "dogecoin",
      "ADAUSDT": "cardano", "LINKUSDT": "chainlink", "AVAXUSDT": "avalanche-2"}
DEFAULT_COINS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
                 "DOGEUSDT", "ADAUSDT", "LINKUSDT", "AVAXUSDT"]
SWEEP_LL = [0.8, 0.6, 0.4]                      # low-liquidity multiples (tighter = thinner)
SWEEP_Z = [1.0, 1.5, 2.0]                       # price-elevation z thresholds (tighter = more stretched)


# ----------------------------- data (keyless) -----------------------------
def fetch_klines(symbol, interval="15m", bars=5000):
    """Paged Binance klines (1000/page); CoinGecko OHLC fallback (synth volume=1).
    Returns O,H,L,C,V oldest->newest."""
    out = []
    end = None
    try:
        while len(out) < bars:
            url = (f"https://api.binance.com/api/v3/klines?symbol={symbol}"
                   f"&interval={interval}&limit=1000")
            if end is not None:
                url += f"&endTime={end}"
            req = urllib.request.Request(url, headers={"User-Agent": "candle-short"})
            page = json.loads(urllib.request.urlopen(req, timeout=15).read())
            if not page:
                break
            out = page + out
            end = page[0][0] - 1
            if len(page) < 1000:
                break
            time.sleep(0.15)
        out = out[-bars:]
        O = [float(c[1]) for c in out]; H = [float(c[2]) for c in out]
        L = [float(c[3]) for c in out]; C = [float(c[4]) for c in out]
        V = [float(c[5]) for c in out]
        if len(C) >= 120:
            return O, H, L, C, V
        raise ValueError("short history")
    except Exception as e:
        cid = CG.get(symbol)
        if not cid:
            sys.stderr.write(f"[candle_short] {symbol} fetch failed: {e}\n")
            return [], [], [], [], []
        url = f"https://api.coingecko.com/api/v3/coins/{cid}/ohlc?vs_currency=usd&days=180"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "candle-short"})
            d = json.loads(urllib.request.urlopen(req, timeout=15).read())
            sys.stderr.write(f"[candle_short] {symbol}: CoinGecko fallback (no volume -> liq filter off)\n")
            return ([c[1] for c in d], [c[2] for c in d], [c[3] for c in d],
                    [c[4] for c in d], [1.0] * len(d))
        except Exception as e2:
            sys.stderr.write(f"[candle_short] {symbol} fallback failed: {e2}\n")
            return [], [], [], [], []


def sma(series, n):
    out, q, s = [], [], 0.0
    for x in series:
        q.append(x); s += x
        if len(q) > n: s -= q.pop(0)
        out.append(s / len(q))
    return out


def roll_mean_std(C, n):
    mean, std, q, s = [], [], [], 0.0
    for x in C:
        q.append(x); s += x
        if len(q) > n: s -= q.pop(0)
        m = s / len(q)
        var = sum((v - m) ** 2 for v in q) / len(q)
        mean.append(m); std.append(var ** 0.5)
    return mean, std


# ----------------- bearish candlestick patterns (optional confluence) -----
def _b(o, c): return abs(c - o)
def _r(h, l): return max(h - l, 1e-12)
def bearish_candle(O, H, L, C, i):
    """Any of: bearish engulfing / shooting star / dark cloud — a down-reversal hint."""
    eng = (i > 0 and C[i - 1] > O[i - 1] and C[i] < O[i] and O[i] >= C[i - 1]
           and C[i] <= O[i - 1])
    body = _b(O[i], C[i]); upper = H[i] - max(O[i], C[i]); lower = min(O[i], C[i]) - L[i]
    star = body > 0 and upper >= 2 * body and lower <= body * 0.5 and body <= _r(H[i], L[i]) * 0.4
    dcc = False
    if i > 0 and C[i - 1] > O[i - 1] and O[i] > C[i - 1] and C[i] < O[i]:
        dcc = C[i] < (O[i - 1] + C[i - 1]) / 2 and C[i] > O[i - 1]
    return eng or star or dcc


# ------------------------------- backtest ---------------------------------
def _prep(O, H, L, C, V):
    volma = sma(V, VN)
    pmean, pstd = roll_mean_std(C, PN)
    return volma, pmean, pstd


def fade_armed(O, H, L, C, V, volma, pmean, pstd, i, ll, z, confluence):
    if volma[i] <= 0 or pstd[i] <= 0:
        return False
    low_liq = V[i] < ll * volma[i]
    high_px = (C[i] - pmean[i]) / pstd[i] > z
    if not (low_liq and high_px):
        return False
    return bearish_candle(O, H, L, C, i) if confluence else True


def backtest(coins, interval, bars, horizons, combos, confluence):
    agg = {cb: {h: {"ret": [], "hits": 0, "n": 0} for h in horizons} for cb in combos}
    base = {h: {"ret": [], "down": 0, "n": 0} for h in horizons}
    start_i = max(VN, PN, 50)
    for sym in coins:
        O, H, L, C, V = fetch_klines(sym, interval, bars)
        if len(C) < 120:
            sys.stderr.write(f"[candle_short] skip {sym} (history {len(C)})\n"); continue
        volma, pmean, pstd = _prep(O, H, L, C, V)
        Hmax = max(horizons)
        for i in range(start_i, len(C) - Hmax):
            for h in horizons:                              # control: unconditional short
                sr = (C[i] - C[i + h]) / C[i]
                base[h]["ret"].append(sr)
                base[h]["down"] += 1 if C[i + h] < C[i] else 0
                base[h]["n"] += 1
            for (ll, z) in combos:
                if not fade_armed(O, H, L, C, V, volma, pmean, pstd, i, ll, z, confluence):
                    continue
                for h in horizons:
                    sr = (C[i] - C[i + h]) / C[i] - 2 * FEE
                    a = agg[(ll, z)][h]
                    a["ret"].append(sr); a["hits"] += 1 if C[i + h] < C[i] else 0; a["n"] += 1
    return agg, base


def _mean(xs): return sum(xs) / len(xs) if xs else 0.0
def _z_hit(hits, n, p0):
    if n == 0: return 0.0
    se = math.sqrt(p0 * (1 - p0) / n)
    return ((hits / n) - p0) / se if se > 0 else 0.0


def report(agg, base, horizons, combos, confluence, interval):
    print(f"\ncandle_short — thin-liquidity overextension FADE  (short stretched price on low volume)")
    print(f"interval {interval} | vol-MA {VN} | price z over {PN} | fee {FEE*1e4:.0f}bps/side x2"
          f" | confluence(bearish candle): {'ON' if confluence else 'off'}")
    print("rule: SHORT when close > Z*std above mean AND volume < LL*avg-volume; cover after H bars.")
    print("'edge%' = config mean short-ret minus the UNCONDITIONAL short-ret (control).\n")
    for h in horizons:
        b = base[h]
        bs = _mean(b["ret"]); bd = (b["down"] / b["n"]) if b["n"] else 0.5
        base_dir = max(bd, 1 - bd)
        print(f"=== horizon {h} bars ===  control: mean short-ret {bs*100:+.3f}%  | "
              f"P(down) {bd*100:.1f}%  | baseline hit {base_dir*100:.1f}%  (n={b['n']:,})")
        print(f"{'LL':>5}{'Z':>6}{'signals':>9}{'down%':>8}{'mean$%':>9}{'edge%':>8}{'z(dir)':>8}  verdict")
        print("-" * 70)
        for cb in combos:
            a = agg[cb][h]; ll, z = cb
            if a["n"] < 25:
                print(f"{ll:>5.1f}{z:>6.1f}{a['n']:>9}{'—':>8}{'—':>9}{'—':>8}{'—':>8}  too few (n<25)")
                continue
            mr = _mean(a["ret"]); dr = a["hits"] / a["n"]; edge = mr - bs
            zz = _z_hit(a["hits"], a["n"], base_dir)
            real = edge > 0 and mr > 0 and zz > 2.0
            verdict = "EDGE ✓" if real else ("flat/noise" if abs(edge) < 0.001 else "no edge")
            print(f"{ll:>5.1f}{z:>6.1f}{a['n']:>9}{dr*100:>7.1f}%{mr*100:>+8.3f}%"
                  f"{edge*100:>+7.3f}%{zz:>+8.1f}  {verdict}")
        print()
    print("Read it honestly: going tighter (lower LL, higher Z) SHOULD trade less but, if the thesis is\n"
          "real, earn MORE per trade and beat the control with z>2. If edge stays ~0 as you tighten, the\n"
          "spikes aren't reverting — the fade has no edge here. This is a SWEEP, so an 'EDGE ✓' is a\n"
          "best-of-N pick; confirm it walk-forward / out-of-sample before sizing.")


# --------------------------------- signal ---------------------------------
def signal(sym, interval, bars, ll, z, confluence):
    O, H, L, C, V = fetch_klines(sym, interval, min(bars, 400))
    if len(C) < max(VN, PN) + 5:
        print(f"{sym}: insufficient data"); return
    volma, pmean, pstd = _prep(O, H, L, C, V)
    i = len(C) - 1
    liq = V[i] / volma[i] if volma[i] else float("nan")
    zp = (C[i] - pmean[i]) / pstd[i] if pstd[i] else float("nan")
    armed = fade_armed(O, H, L, C, V, volma, pmean, pstd, i, ll, z, confluence)
    print(f"{sym} {interval} | close {C[i]:,.2f}")
    print(f"  price elevation z = {zp:+.2f}  (need > {z})   {'✓' if zp > z else '×'}")
    print(f"  volume vs avg     = {liq:.2f}x  (need < {ll})  {'✓' if liq < ll else '×'}")
    if confluence:
        print(f"  bearish candle    = {'✓' if bearish_candle(O,H,L,C,i) else '×'}")
    print(f"  -> {'SHORT armed (paper)' if armed else 'FLAT — conditions not met'}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--signal", metavar="SYMBOL")
    ap.add_argument("--coins", default=",".join(DEFAULT_COINS))
    ap.add_argument("--interval", default="15m")
    ap.add_argument("--bars", type=int, default=5000)
    ap.add_argument("--horizons", default="8,24")
    ap.add_argument("--ll", type=float, help="single low-liq multiple (skip the sweep)")
    ap.add_argument("--z", type=float, help="single price-z threshold (skip the sweep)")
    ap.add_argument("--confluence", action="store_true", help="also require a bearish candle")
    a = ap.parse_args()
    horizons = [int(x) for x in a.horizons.split(",")]
    if a.signal:
        signal(a.signal.upper(), a.interval, a.bars, a.ll or 0.6, a.z or 1.5, a.confluence); return
    coins = [c.strip().upper() for c in a.coins.split(",") if c.strip()]
    if a.ll is not None and a.z is not None:
        combos = [(a.ll, a.z)]
    else:
        combos = [(ll, z) for z in SWEEP_Z for ll in SWEEP_LL]
    agg, base = backtest(coins, a.interval, a.bars, horizons, combos, a.confluence)
    report(agg, base, horizons, combos, a.confluence, a.interval)


if __name__ == "__main__":
    main()
