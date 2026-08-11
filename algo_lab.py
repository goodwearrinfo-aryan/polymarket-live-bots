#!/usr/bin/env python3
"""
algo_lab.py — 26 open-source algos raced PAPER, LIVE, MULTI-ASSET, since-inception.

Canonical OSS strategy logic (freqtrade/jesse/backtrader/TA-Lib style) reimplemented
keyless in pure Python. Each algo runs LONG/FLAT on EACH of BTC/ETH/SOL (15m),
fee-aware (6 bps/side), no-lookahead, with PERSISTENT per-asset equity stepped
forward only on NEW bars each run (idempotent) — curves grow since inception.

Ranked by AVERAGE return across the 3 assets (an algo that works on all three is
more trustworthy than one that lucked out on one). A CHAMPION iMessage fires when
one algo decisively + durably leads (age-gated >=2d, beats B&H, >=3pp ahead of #2,
deduped) — honest, won't cry wolf early.

26 algos: SMA/EMA/DEMA/TEMA/Hull cross, MACD, Donchian, Momentum x2, EMA-ribbon,
golden-cross, triple-SMA, Supertrend, Keltner, TRIX, OBV-trend, Awesome-Osc,
Bollinger lower + %B-breakout, RSI, Stochastic, Williams%R, CCI, Z-score, VWAP,
Buy&Hold (control).

launchd com.aryan.algo-lab (5min). The ALICE portal reads algo_lab_state.json.
PAPER ONLY, keyless, $0.
"""
import json, os, time, subprocess, urllib.request, math
from collections import deque

PM = os.path.expanduser("~/Documents/polymarket")
STATE = os.path.join(PM, "algo_lab_state.json")
ASSETS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
CG = {"BTCUSDT": "bitcoin", "ETHUSDT": "ethereum", "SOLUSDT": "solana"}
INTERVAL, LIMIT, FEE, WARMUP, CURVE_CAP = "15m", 300, 0.0006, 110, 400
ALERT_TARGETS = ["krisharyan@icloud.com", "+918449447444"]

def fetch(asset):
    url = f"https://api.binance.com/api/v3/klines?symbol={asset}&interval={INTERVAL}&limit={LIMIT}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "algolab"})
        k = json.loads(urllib.request.urlopen(req, timeout=12).read())
        return ([int(c[0] // 1000) for c in k], [float(c[1]) for c in k], [float(c[2]) for c in k],
                [float(c[3]) for c in k], [float(c[4]) for c in k], [float(c[5]) for c in k])
    except Exception:
        url = f"https://api.coingecko.com/api/v3/coins/{CG[asset]}/ohlc?vs_currency=usd&days=14"
        req = urllib.request.Request(url, headers={"User-Agent": "algolab"})
        d = json.loads(urllib.request.urlopen(req, timeout=12).read())
        return ([c[0] // 1000 for c in d], [c[1] for c in d], [c[2] for c in d],
                [c[3] for c in d], [c[4] for c in d], [1.0] * len(d))

# ---------- indicators ----------
def sma_s(xs, n):
    out, q, s = [], deque(), 0.0
    for x in xs:
        q.append(x); s += x
        if len(q) > n: s -= q.popleft()
        out.append(s / len(q))
    return out
def ema_s(xs, n):
    k = 2 / (n + 1); out, e = [], None
    for x in xs:
        e = x if e is None else x * k + e * (1 - k); out.append(e)
    return out
def wma_s(xs, n):
    out = []
    for i in range(len(xs)):
        w = xs[max(0, i - n + 1):i + 1]; k = len(w)
        out.append(sum(x * (j + 1) for j, x in enumerate(w)) / (k * (k + 1) / 2))
    return out
def hull_s(C, n):
    half, full = wma_s(C, max(1, n // 2)), wma_s(C, n)
    return wma_s([2 * half[i] - full[i] for i in range(len(C))], max(1, int(n ** 0.5)))
def std_s(xs, n):
    out, q = [], deque()
    for x in xs:
        q.append(x)
        if len(q) > n: q.popleft()
        m = sum(q) / len(q); out.append((sum((v - m) ** 2 for v in q) / len(q)) ** 0.5)
    return out
def rsi_s(xs, n=14):
    out = [50.0]
    for i in range(1, len(xs)):
        lo = max(1, i - n + 1)
        g = sum(max(0, xs[j] - xs[j - 1]) for j in range(lo, i + 1))
        l = sum(max(0, xs[j - 1] - xs[j]) for j in range(lo, i + 1))
        out.append(100.0 if l == 0 else 100 - 100 / (1 + (g / n) / (l / n)))
    return out
def roll(xs, n, fn):
    out, q = [], deque()
    for x in xs:
        q.append(x)
        if len(q) > n: q.popleft()
        out.append(fn(q))
    return out
def atr_s(H, L, C, n=14):
    tr = [H[i] - L[i] if i == 0 else max(H[i] - L[i], abs(H[i] - C[i - 1]), abs(L[i] - C[i - 1])) for i in range(len(C))]
    return ema_s(tr, n)

def psar_s(H, L, af0=0.02, afmax=0.2):
    n = len(H)
    if not n: return []
    trend = [1] * n; up = True; af = af0; ep = H[0]; sar = L[0]
    for i in range(1, n):
        sar = sar + af * (ep - sar)
        if up:
            sar = min(sar, L[i - 1], L[i - 2] if i >= 2 else L[i - 1])
            if H[i] > ep: ep = H[i]; af = min(afmax, af + af0)
            if L[i] < sar: up = False; sar = ep; ep = L[i]; af = af0
        else:
            sar = max(sar, H[i - 1], H[i - 2] if i >= 2 else H[i - 1])
            if L[i] < ep: ep = L[i]; af = min(afmax, af + af0)
            if H[i] > sar: up = True; sar = ep; ep = H[i]; af = af0
        trend[i] = 1 if up else 0
    return trend

def dmi_s(H, L, C, n=14):
    m = len(C); tr = [0.0] * m; pdm = [0.0] * m; ndm = [0.0] * m
    for i in range(1, m):
        tr[i] = max(H[i] - L[i], abs(H[i] - C[i - 1]), abs(L[i] - C[i - 1]))
        up = H[i] - H[i - 1]; dn = L[i - 1] - L[i]
        pdm[i] = up if (up > dn and up > 0) else 0.0
        ndm[i] = dn if (dn > up and dn > 0) else 0.0
    atr = ema_s(tr, n); sp = ema_s(pdm, n); sn = ema_s(ndm, n)
    pdi = [100 * sp[i] / atr[i] if atr[i] else 0.0 for i in range(m)]
    ndi = [100 * sn[i] / atr[i] if atr[i] else 0.0 for i in range(m)]
    dx = [100 * abs(pdi[i] - ndi[i]) / (pdi[i] + ndi[i]) if (pdi[i] + ndi[i]) else 0.0 for i in range(m)]
    return pdi, ndi, ema_s(dx, n)

def signals(T, O, H, L, C, V):
    n = len(C)
    sma5, sma10, sma20, sma30, sma50, sma100 = (sma_s(C, k) for k in (5, 10, 20, 30, 50, 100))
    ema8, ema12, ema21, ema26, ema55 = (ema_s(C, k) for k in (8, 12, 21, 26, 55))
    macd = [a - b for a, b in zip(ema12, ema26)]; macdsig = ema_s(macd, 9)
    std20 = std_s(C, 20); rsi = rsi_s(C, 14)
    atr14, atr10 = atr_s(H, L, C, 14), atr_s(H, L, C, 10)
    hh14, ll14 = roll(H, 14, max), roll(L, 14, min)
    hh20, ll20 = roll(C, 20, max), roll(C, 20, min)
    hull20 = hull_s(C, 20)
    # DEMA/TEMA(12 vs 26), TRIX(15)
    de12, de26 = ema_s(ema_s(C, 12), 12), ema_s(ema_s(C, 26), 26)
    dema12 = [2 * ema12[i] - de12[i] for i in range(n)]; dema26 = [2 * ema26[i] - de26[i] for i in range(n)]
    te = ema_s(ema_s(ema_s(C, 12), 12), 12); tema12 = [3 * ema12[i] - 3 * de12[i] + te[i] for i in range(n)]
    te26 = ema_s(ema_s(ema_s(C, 26), 26), 26); tema26 = [3 * ema26[i] - 3 * de26[i] + te26[i] for i in range(n)]
    tr3 = ema_s(ema_s(ema_s(C, 15), 15), 15)
    trix = [0.0] + [(tr3[i] / tr3[i - 1] - 1) * 100 if tr3[i - 1] else 0 for i in range(1, n)]
    med = [(H[i] + L[i]) / 2 for i in range(n)]
    ao = [a - b for a, b in zip(sma_s(med, 5), sma_s(med, 34))]
    # OBV trend
    obv = [0.0]
    for i in range(1, n):
        obv.append(obv[-1] + (V[i] if C[i] > C[i - 1] else (-V[i] if C[i] < C[i - 1] else 0)))
    obvsma = sma_s(obv, 20)
    # vwap / cci / williams
    vwap, qp, qv = [], deque(), deque()
    cci, wr = [], []
    for i in range(n):
        tp = (H[i] + L[i] + C[i]) / 3
        qp.append(tp * V[i]); qv.append(V[i])
        if len(qp) > 20: qp.popleft(); qv.popleft()
        vwap.append(sum(qp) / max(1e-9, sum(qv)))
        lo = max(0, i - 19); tps = [(H[j] + L[j] + C[j]) / 3 for j in range(lo, i + 1)]
        mt = sum(tps) / len(tps); md = sum(abs(x - mt) for x in tps) / len(tps)
        cci.append(0.0 if md == 0 else (tp - mt) / (0.015 * md))
        rng = hh14[i] - ll14[i]; wr.append(-50.0 if rng <= 0 else -100 * (hh14[i] - C[i]) / rng)
    def stoch(i):
        rng = hh14[i] - ll14[i]; return 0.5 if rng <= 0 else (C[i] - ll14[i]) / rng
    def pctb(i):
        up = sma20[i] + 2 * std20[i]; lo = sma20[i] - 2 * std20[i]
        return 0.5 if up <= lo else (C[i] - lo) / (up - lo)
    # supertrend
    st = []; fu = fd = 0.0; tr = 1
    for i in range(n):
        hl2 = (H[i] + L[i]) / 2; bu = hl2 + 3 * atr10[i]; bl = hl2 - 3 * atr10[i]
        if i == 0: fu, fd, tr = bu, bl, 1
        else:
            fu = bu if (bu < fu or C[i - 1] > fu) else fu
            fd = bl if (bl > fd or C[i - 1] < fd) else fd
            tr = 1 if C[i] > fu else (-1 if C[i] < fd else tr)
        st.append(1 if tr == 1 else 0)
    # ---- batch-2 OSS algos ----
    psar = psar_s(H, L)
    pdi, ndi, adx = dmi_s(H, L, C, 14)
    ema13 = ema_s(C, 13); hist = [macd[i] - macdsig[i] for i in range(n)]
    rsi2 = rsi_s(C, 2); sma200 = sma_s(C, 200)
    turtle = [0] * n; tpos = 0
    au = [50.0] * n; ad = [50.0] * n
    vmp = [0.0] * n; vmm = [0.0] * n; vtr = [0.0] * n
    hac = [0.0] * n; hao = [0.0] * n
    conn = [0] * n; cpos = 0
    mfi = [50.0] * n; cmo = [0.0] * n
    tp = [(H[i] + L[i] + C[i]) / 3 for i in range(n)]
    mf = [tp[i] * V[i] for i in range(n)]
    mfv = [(((C[i] - L[i]) - (H[i] - C[i])) / (H[i] - L[i]) * V[i]) if H[i] > L[i] else 0.0 for i in range(n)]
    for i in range(n):
        hh = max(H[max(0, i - 20):i]) if i else H[0]
        ll = min(L[max(0, i - 10):i]) if i else L[0]
        tpos = 1 if C[i] > hh else (0 if C[i] < ll else tpos); turtle[i] = tpos
        loa = max(0, i - 25); wh = H[loa:i + 1]; wl = L[loa:i + 1]; mm = len(wh)
        if mm > 1:
            au[i] = 100 * (25 - ((mm - 1) - wh.index(max(wh)))) / 25
            ad[i] = 100 * (25 - ((mm - 1) - wl.index(min(wl)))) / 25
        if i:
            vmp[i] = abs(H[i] - L[i - 1]); vmm[i] = abs(L[i] - H[i - 1])
            vtr[i] = max(H[i] - L[i], abs(H[i] - C[i - 1]), abs(L[i] - C[i - 1]))
        hac[i] = (O[i] + H[i] + L[i] + C[i]) / 4
        hao[i] = (hao[i - 1] + hac[i - 1]) / 2 if i else (O[i] + C[i]) / 2
        cpos = 1 if (rsi2[i] < 10 and C[i] > sma200[i]) else (0 if rsi2[i] > 65 else cpos); conn[i] = cpos
        if i:
            lo = max(1, i - 13)
            pos = sum(mf[j] for j in range(lo, i + 1) if tp[j] > tp[j - 1])
            neg = sum(mf[j] for j in range(lo, i + 1) if tp[j] < tp[j - 1])
            mfi[i] = 100.0 if neg == 0 else 100 - 100 / (1 + pos / neg)
            su = sum(max(0, C[j] - C[j - 1]) for j in range(lo, i + 1))
            sd = sum(max(0, C[j - 1] - C[j]) for j in range(lo, i + 1))
            cmo[i] = 0.0 if (su + sd) == 0 else (su - sd) / (su + sd) * 100
    svmp, svmm, svtr = roll(vmp, 14, sum), roll(vmm, 14, sum), roll(vtr, 14, sum)
    vip = [svmp[i] / svtr[i] if svtr[i] else 1 for i in range(n)]
    vim = [svmm[i] / svtr[i] if svtr[i] else 1 for i in range(n)]
    smfv, sv = roll(mfv, 20, sum), roll(V, 20, sum)
    cmf = [smfv[i] / sv[i] if sv[i] else 0 for i in range(n)]
    base = {
        "SMA cross (10/30)":   [1 if sma10[i] > sma30[i] else 0 for i in range(n)],
        "EMA cross (12/26)":   [1 if ema12[i] > ema26[i] else 0 for i in range(n)],
        "DEMA cross (12/26)":  [1 if dema12[i] > dema26[i] else 0 for i in range(n)],
        "TEMA cross (12/26)":  [1 if tema12[i] > tema26[i] else 0 for i in range(n)],
        "Hull MA (20)":        [1 if C[i] > hull20[i] else 0 for i in range(n)],
        "MACD (12/26/9)":      [1 if macd[i] > macdsig[i] else 0 for i in range(n)],
        "EMA ribbon (8/21/55)":[1 if ema8[i] > ema21[i] > ema55[i] else 0 for i in range(n)],
        "Golden cross (50/100)":[1 if sma50[i] > sma100[i] else 0 for i in range(n)],
        "Triple SMA (5/10/20)":[1 if sma5[i] > sma10[i] > sma20[i] else 0 for i in range(n)],
        "Donchian mid (20)":   [1 if C[i] > (hh20[i] + ll20[i]) / 2 else 0 for i in range(n)],
        "Momentum ROC(10)":    [1 if i >= 10 and C[i] > C[i - 10] else 0 for i in range(n)],
        "Dual momentum ROC(20)":[1 if i >= 20 and C[i] > C[i - 20] else 0 for i in range(n)],
        "Supertrend(10,3)":    st,
        "Keltner breakout(20)":[1 if C[i] > ema21[i] + 2 * atr14[i] else 0 for i in range(n)],
        "TRIX(15) > 0":        [1 if trix[i] > 0 else 0 for i in range(n)],
        "OBV trend (20)":      [1 if obv[i] > obvsma[i] else 0 for i in range(n)],
        "Awesome Osc (>0)":    [1 if ao[i] > 0 else 0 for i in range(n)],
        "Bollinger %B>0.8":    [1 if pctb(i) > 0.8 else 0 for i in range(n)],
        "RSI(14) mean-rev":    [1 if rsi[i] < 40 else 0 for i in range(n)],
        "Bollinger lower(20)": [1 if C[i] < sma20[i] - 2 * std20[i] else 0 for i in range(n)],
        "Z-score(20) < -1":    [1 if std20[i] > 0 and (C[i] - sma20[i]) / std20[i] < -1 else 0 for i in range(n)],
        "Stochastic(14)<25":   [1 if stoch(i) < 0.25 else 0 for i in range(n)],
        "Williams %R<-80":     [1 if wr[i] < -80 else 0 for i in range(n)],
        "CCI(20) < -100":      [1 if cci[i] < -100 else 0 for i in range(n)],
        "VWAP(20) reversion":  [1 if C[i] < vwap[i] else 0 for i in range(n)],
        "Parabolic SAR":       [1 if psar[i] else 0 for i in range(n)],
        "ADX/DMI (+DI>-DI,25)":[1 if pdi[i] > ndi[i] and adx[i] > 25 else 0 for i in range(n)],
        "Turtle 20/10":        turtle,
        "Aroon (25)":          [1 if au[i] > ad[i] else 0 for i in range(n)],
        "Vortex (14)":         [1 if vip[i] > vim[i] else 0 for i in range(n)],
        "Heikin-Ashi trend":   [1 if i and hac[i] > hao[i] and hac[i - 1] > hao[i - 1] else 0 for i in range(n)],
        "Elder Impulse":       [1 if i and ema13[i] > ema13[i - 1] and hist[i] > hist[i - 1] else 0 for i in range(n)],
        "Connors RSI-2":       conn,
        "MFI(14) < 25":        [1 if mfi[i] < 25 else 0 for i in range(n)],
        "Chaikin MF(20)>0":    [1 if cmf[i] > 0 else 0 for i in range(n)],
        "Chande Mom(14)>0":    [1 if cmo[i] > 0 else 0 for i in range(n)],
        "Buy & Hold (ctrl)":   [1 for _ in range(n)],
    }
    # ================= batch-3: 10 distinct OSS algos (adaptive / regime / multi-horizon) =================
    # Researched + formula-verified (STC, KAMA, Fisher, TTM-Squeeze, QQE, Chandelier, CHOP-gate, KST, RVI,
    # Ichimoku). long/flat to stay comparable to the rest of the board; added BEFORE the meta block so the
    # ensemble can learn from them.
    ema20 = ema_s(C, 20)
    def _stoch_n(xs, k):
        mn, mx = roll(xs, k, min), roll(xs, k, max)
        return [100 * (xs[i] - mn[i]) / (mx[i] - mn[i]) if mx[i] > mn[i] else 0.0 for i in range(n)]
    def _linreg_last(xs, i, k):                                  # OLS endpoint over xs[i-k+1:i+1]
        if i < k - 1: return 0.0
        ys = xs[i - k + 1:i + 1]; m = len(ys)
        sx = (m - 1) * m / 2; sxx = (m - 1) * m * (2 * m - 1) / 6
        sy = sum(ys); sxy = sum(j * ys[j] for j in range(m)); den = m * sxx - sx * sx
        if den == 0: return ys[-1]
        b = (m * sxy - sx * sy) / den; a = (sy - b * sx) / m
        return a + b * (m - 1)
    # 1. Schaff Trend Cycle (23/50/10) — double-stochastic of MACD
    macd_l = [ema_s(C, 23)[i] - ema_s(C, 50)[i] for i in range(n)]
    stc = ema_s(_stoch_n(ema_s(_stoch_n(macd_l, 10), 3), 10), 3)
    # 2. KAMA cross (er=10, fast=2, slow=30) — efficiency-ratio adaptive MA
    kama = [C[0]] * n
    for i in range(1, n):
        if i >= 10:
            vol = sum(abs(C[j] - C[j - 1]) for j in range(i - 9, i + 1)); er = abs(C[i] - C[i - 10]) / vol if vol else 0.0
        else: er = 0.0
        sc = (er * (2 / 3 - 2 / 31) + 2 / 31) ** 2
        kama[i] = kama[i - 1] + sc * (C[i] - kama[i - 1])
    # 3. Fisher Transform (10) — Gaussian-normalized turning points
    hl2 = [(H[i] + L[i]) / 2 for i in range(n)]
    fmn, fmx = roll(hl2, 10, min), roll(hl2, 10, max); fish = [0.0] * n; fv = 0.0
    for i in range(n):
        rng = fmx[i] - fmn[i]; raw = 2 * (hl2[i] - fmn[i]) / rng - 1 if rng > 0 else 0.0
        fv = max(-0.999, min(0.999, 0.33 * raw + 0.67 * fv))
        fish[i] = 0.5 * math.log((1 + fv) / (1 - fv)) + (0.5 * fish[i - 1] if i else 0.0)
    # 4. TTM Squeeze Momentum (20) — vol-compression-gated linreg momentum
    trs = [H[i] - L[i] if i == 0 else max(H[i] - L[i], abs(H[i] - C[i - 1]), abs(L[i] - C[i - 1])) for i in range(n)]
    kcr = sma_s(trs, 20); hh20a, ll20a = roll(H, 20, max), roll(L, 20, min)
    dmid = [C[i] - ((hh20a[i] + ll20a[i]) / 2 + sma20[i]) / 2 for i in range(n)]
    sqz = []
    for i in range(n):
        squeeze = (sma20[i] + 2 * std20[i] < sma20[i] + 1.5 * kcr[i]) and (sma20[i] - 2 * std20[i] > sma20[i] - 1.5 * kcr[i])
        sqz.append(1 if (not squeeze and _linreg_last(dmid, i, 20) > 0) else 0)
    # 5. QQE (RSI14, sf5) — ATR-trailing band inside RSI space
    rsi_sm = ema_s(rsi, 9); atr_rsi = [0.0] + [abs(rsi_sm[i] - rsi_sm[i - 1]) for i in range(1, n)]
    dar = [x * 4.236 for x in ema_s(ema_s(atr_rsi, 9), 9)]; qqe = [0] * n; lb = 0.0; sb = 100.0; qt = 1
    for i in range(n):
        nl, ns = rsi_sm[i] - dar[i], rsi_sm[i] + dar[i]; plb, psb = lb, sb   # cross the PRIOR bar's band
        lb = max(plb, nl) if (i and rsi_sm[i - 1] > plb and rsi_sm[i] > plb) else nl
        sb = min(psb, ns) if (i and rsi_sm[i - 1] < psb and rsi_sm[i] < psb) else ns
        if i:
            if rsi_sm[i] > psb and rsi_sm[i - 1] <= psb: qt = 1          # pierce upper trailing band → up
            elif rsi_sm[i] < plb and rsi_sm[i - 1] >= plb: qt = 0        # pierce lower trailing band → flat
        qqe[i] = qt
    # 6. Chandelier Exit (22,3) — ATR swing-extreme trailing flip
    atr22 = atr_s(H, L, C, 22); hh22, ll22 = roll(H, 22, max), roll(L, 22, min)
    cl = [hh22[i] - 3 * atr22[i] for i in range(n)]; cs = [ll22[i] + 3 * atr22[i] for i in range(n)]
    chan = [0] * n; cd = 1
    for i in range(n):
        if i: cd = 1 if C[i] > cs[i - 1] else (0 if C[i] < cl[i - 1] else cd)
        chan[i] = cd
    # 7. CHOP-gate trend — Choppiness-Index regime gate on an EMA20 trend
    atrsum = roll(trs, 14, sum); hh14c, ll14c = roll(H, 14, max), roll(L, 14, min)
    chop = [100 * math.log10(atrsum[i] / (hh14c[i] - ll14c[i])) / math.log10(14)
            if (hh14c[i] > ll14c[i] and atrsum[i] > 0) else 100.0 for i in range(n)]
    chopgate = [1 if (chop[i] < 38.2 and C[i] > ema20[i]) else 0 for i in range(n)]
    # 8. KST — weighted multi-horizon ROC oscillator
    def _roc(k): return [(C[i] - C[i - k]) / C[i - k] * 100 if i >= k and C[i - k] else 0.0 for i in range(n)]
    r1, r2, r3, r4 = sma_s(_roc(10), 10), sma_s(_roc(15), 10), sma_s(_roc(20), 10), sma_s(_roc(30), 15)
    kst = [r1[i] + 2 * r2[i] + 3 * r3[i] + 4 * r4[i] for i in range(n)]; kstsig = sma_s(kst, 9)
    # 9. RVI(10) — close-vs-open vigor normalized by bar range
    rnum = [(C[i] - O[i]) + 2 * (C[i - 1] - O[i - 1]) + 2 * (C[i - 2] - O[i - 2]) + (C[i - 3] - O[i - 3]) if i >= 3 else 0.0 for i in range(n)]
    rden = [(H[i] - L[i]) + 2 * (H[i - 1] - L[i - 1]) + 2 * (H[i - 2] - L[i - 2]) + (H[i - 3] - L[i - 3]) if i >= 3 else 0.0 for i in range(n)]
    srn, srd = sma_s(rnum, 10), sma_s(rden, 10); rvi = [srn[i] / srd[i] if srd[i] else 0.0 for i in range(n)]
    rvisig = [(rvi[i] + 2 * rvi[i - 1] + 2 * rvi[i - 2] + rvi[i - 3]) / 6 if i >= 3 else rvi[i] for i in range(n)]
    # 10. Ichimoku cloud — time-displaced Kumo, no-lookahead (senkou[i-26])
    hh9, ll9 = roll(H, 9, max), roll(L, 9, min); hh26, ll26 = roll(H, 26, max), roll(L, 26, min); hh52, ll52 = roll(H, 52, max), roll(L, 52, min)
    tkn = [(hh9[i] + ll9[i]) / 2 for i in range(n)]; kjn = [(hh26[i] + ll26[i]) / 2 for i in range(n)]
    ska = [(tkn[i] + kjn[i]) / 2 for i in range(n)]; skb = [(hh52[i] + ll52[i]) / 2 for i in range(n)]
    ichi = [0] * n
    for i in range(n):
        if i >= 26:
            kt = max(ska[i - 26], skb[i - 26])
            if C[i] > kt and tkn[i] > kjn[i] and C[i - 26] > kt: ichi[i] = 1
    base["Schaff Trend Cycle"]    = [1 if stc[i] > 25 else 0 for i in range(n)]
    base["KAMA cross (10,2,30)"]  = [1 if C[i] > kama[i] else 0 for i in range(n)]
    base["Fisher Transform(10)"]  = [1 if i and fish[i] > fish[i - 1] else 0 for i in range(n)]
    base["TTM Squeeze Mom"]       = sqz
    base["QQE (RSI14)"]           = qqe
    base["Chandelier Exit(22,3)"] = chan
    base["CHOP-gate EMA20"]       = chopgate
    base["KST oscillator"]        = [1 if kst[i] > kstsig[i] else 0 for i in range(n)]
    base["RVI(10)"]               = [1 if rvi[i] > rvisig[i] else 0 for i in range(n)]
    base["Ichimoku cloud"]        = ichi
    # ---- SMART meta-strategies: ensemble that learns from the base algos (no-lookahead) ----
    reg = regimes(C)
    nm = [k for k in base if not k.startswith("Buy")]
    perf = {k: {"up": 0.0, "down": 0.0, "chop": 0.0} for k in nm}
    tot = {k: 0.0 for k in nm}
    adaptive = [0] * n; vote = [0] * n; follow = [0] * n
    for i in range(1, n):
        r = (C[i] / C[i - 1] - 1) * 100
        for k in nm:                                    # credit each algo's realized return (past only)
            if base[k][i - 1] == 1:
                perf[k][reg[i]] += r; tot[k] += r
        if i > 60:                                      # pick best-in-current-regime + all-time best, from HISTORY
            best = max(nm, key=lambda k: perf[k][reg[i]]); lead = max(nm, key=lambda k: tot[k])
        else:
            best = lead = nm[0]
        adaptive[i] = base[best][i]                     # follow that algo's CURRENT signal
        follow[i] = base[lead][i]
        vote[i] = 1 if sum(base[k][i] for k in nm) * 2 > len(nm) else 0
    base["Meta: Regime-Adaptive"] = adaptive
    base["Meta: Ensemble Vote"] = vote
    base["Meta: Follow-Leader"] = follow
    # ---- SHORT legs: thin-liquidity overextension fade (paper, forward-tested) ----
    # short (-1) when close > z*std above the 20-bar mean AND volume < ll*avg-volume (thin),
    # held HOR bars then flat. Added AFTER the meta block so the long-only ensemble ignores them.
    volma, pmean, pstd = sma_s(V, 20), sma_s(C, 20), std_s(C, 20)
    def liqfade(ll, z, hor=8):
        pos, hold = [0] * n, 0
        for i in range(n):
            if volma[i] > 0 and pstd[i] > 0 and V[i] < ll * volma[i] and (C[i] - pmean[i]) / pstd[i] > z:
                hold = hor
            pos[i] = -1 if hold > 0 else 0
            if hold > 0:
                hold -= 1
        return pos
    base["LiqFade short (0.4,1.0)"] = liqfade(0.4, 1.0)
    base["LiqFade short (0.6,1.0)"] = liqfade(0.6, 1.0)
    return base

def regimes(C):
    """Per-bar market regime (no lookahead): up/down-trend vs chop, off SMA50 + slope."""
    s50 = sma_s(C, 50)
    out = []
    for i in range(len(C)):
        if i < 55:
            out.append("chop")
        elif C[i] > s50[i] and s50[i] > s50[i - 5]:
            out.append("up")
        elif C[i] < s50[i] and s50[i] < s50[i - 5]:
            out.append("down")
        else:
            out.append("chop")
    return out

def step(book, sig, T, C, reg):
    last_t = book.get("last_t", 0)
    start = WARMUP if last_t == 0 else max(1, next((i for i in range(len(T)) if T[i] > last_t), len(T)))
    for name, s in sig.items():
        a = book["algos"].setdefault(name, {"equity": 1.0, "pos": 0, "trades": 0, "curve": []})
        a.setdefault("reg", {"up": 0.0, "down": 0.0, "chop": 0.0})    # return earned IN each regime
        for i in range(start, len(T)):
            if a["pos"] != 0:                                 # pos 1=long, -1=short, 0=flat
                r = C[i] / C[i - 1] - 1
                a["equity"] *= (1 + a["pos"] * r)
                a["reg"][reg[i]] = a["reg"].get(reg[i], 0.0) + a["pos"] * r * 100
            if s[i] != a["pos"]:
                a["equity"] *= (1 - FEE); a["trades"] += 1; a["pos"] = s[i]
            a["curve"].append({"time": T[i], "value": round((a["equity"] - 1) * 100, 3)})
        a["curve"] = a["curve"][-CURVE_CAP:]
    book["last_t"] = T[-1]
    book["regime"] = reg[-1] if reg else "chop"

def imsg(body):
    osa = ('on run argv\ntell application "Messages"\nset s to 1st service whose service type = iMessage\n'
           'send (item 2 of argv) to buddy (item 1 of argv) of s\nend tell\nend run')
    for t in ALERT_TARGETS:
        try:
            subprocess.run(["osascript", "-e", osa, t, body], capture_output=True, timeout=10)
        except Exception:
            pass

def run():
    try:
        state = json.load(open(STATE))
        if state.get("interval") != INTERVAL or "assets" not in state:
            raise ValueError
    except Exception:
        state = {"started": int(time.time()), "assets": {}, "interval": INTERVAL, "champion": {"name": None}}

    names = None
    for asset in ASSETS:
        T, O, H, L, C, V = fetch(asset)
        sig = signals(T, O, H, L, C, V); names = list(sig.keys())
        step(state["assets"].setdefault(asset, {"last_t": 0, "algos": {}}), sig, T, C, regimes(C))

    def pnl(asset, name):
        return (state["assets"][asset]["algos"][name]["equity"] - 1) * 100
    lb = []
    for name in names:
        per = {a: round(pnl(a, name), 2) for a in ASSETS if name in state["assets"][a]["algos"]}
        avg = round(sum(per.values()) / len(per), 3) if per else 0.0
        b = state["assets"]["BTCUSDT"]["algos"].get(name, {})
        lb.append({"name": name, "avg_pnl": avg, "by_asset": per, "pos": b.get("pos", 0),
                   "trades": sum(state["assets"][a]["algos"][name]["trades"] for a in ASSETS if name in state["assets"][a]["algos"])})
    lb.sort(key=lambda r: r["avg_pnl"], reverse=True)
    bh = next((r["avg_pnl"] for r in lb if r["name"].startswith("Buy")), 0.0)
    state["leaderboard"] = lb
    state["buyhold_pct"] = bh
    state["beat_buyhold"] = sum(1 for r in lb if not r["name"].startswith("Buy") and r["avg_pnl"] > bh)
    state["ts"] = int(time.time())
    state["age_days"] = round((state["ts"] - state["started"]) / 86400, 2)

    # market regime (majority across assets) + per-algo regime attribution + best-in-regime
    from collections import Counter
    cur = Counter(state["assets"][a].get("regime", "chop") for a in ASSETS).most_common(1)[0][0]
    state["regime"] = cur
    for r in lb:
        rb = {"up": 0.0, "down": 0.0, "chop": 0.0}; cnt = 0
        for a in ASSETS:
            ar = state["assets"][a]["algos"].get(r["name"], {}).get("reg")
            if ar:
                for k in rb:
                    rb[k] += ar.get(k, 0)
                cnt += 1
        r["reg"] = {k: round(v / max(1, cnt), 2) for k, v in rb.items()}
        r["reg_now"] = r["reg"].get(cur, 0)
    rl = max((r for r in lb if not r["name"].startswith("Buy")), key=lambda r: r["reg_now"], default=None)
    state["regime_leader"] = rl["name"] if rl else None

    # ROBUSTNESS GATE — reward "works on ALL 3 assets + breadth across regimes", not one-asset (SOL) luck.
    # A high avg carried by a single asset is fragile; rank robust algos by their WORST asset (min_asset),
    # so consistency beats a lucky tail. regime_breadth = # of regimes the algo earned positively in.
    for r in lb:
        per = list(r["by_asset"].values())
        r["min_asset"] = round(min(per), 2) if per else 0.0
        r["regime_breadth"] = sum(1 for v in r["reg"].values() if v > 0)
        r["robust"] = bool(not r["name"].startswith("Buy") and r["avg_pnl"] > bh
                           and r["min_asset"] > 0 and r["regime_breadth"] >= 2)
    robust = sorted((r for r in lb if r["robust"]), key=lambda r: r["min_asset"], reverse=True)
    state["robust_leaderboard"] = [r["name"] for r in robust]
    state["robust_leader"] = robust[0]["name"] if robust else None

    # champion: decisive + durable + age-gated + ROBUST (positive on all 3 assets), deduped
    nc = [r for r in lb if not r["name"].startswith("Buy")]
    champ = state.get("champion", {"name": None})
    if (nc and state["age_days"] >= 2 and nc[0]["avg_pnl"] > bh and nc[0]["avg_pnl"] > 0
            and nc[0].get("robust")                                  # no crying SOL-luck wolf
            and (len(nc) < 2 or nc[0]["avg_pnl"] - nc[1]["avg_pnl"] >= 3.0)):
        lead = nc[0]
        if champ.get("name") != lead["name"]:
            champ = {"name": lead["name"], "avg_pnl": lead["avg_pnl"], "since": state["ts"], "alerted": False}
        if not champ.get("alerted"):
            gap = lead["avg_pnl"] - (nc[1]["avg_pnl"] if len(nc) > 1 else 0)
            imsg(f"[ALGO LAB] champion: {lead['name']} +{lead['avg_pnl']:.1f}% avg (BTC/ETH/SOL), "
                 f"{state['age_days']}d, +{gap:.1f}pp ahead of #2, vs B&H {bh:+.1f}%")
            champ["alerted"] = True
    else:
        champ = {"name": None}
    state["champion"] = champ

    tmp = STATE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, STATE)
    rld = next((r for r in robust), None)
    print(f"algo_lab: {len(lb)} algos x{len(ASSETS)} assets, {state['age_days']}d | "
          f"leader {lb[0]['name']} {lb[0]['avg_pnl']:+.2f}% avg | B&H {bh:+.2f}% | "
          f"{state['beat_buyhold']} beat | {len(robust)} robust"
          + (f" (top {rld['name']} worst-asset {rld['min_asset']:+.2f}%)" if rld else "")
          + f" | champion={champ.get('name')}")
    return state

if __name__ == "__main__":
    run()
