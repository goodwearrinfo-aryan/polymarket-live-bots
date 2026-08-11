#!/usr/bin/env python3
"""candle_knowledge_logger.py — READ-ONLY logger (NOT a trading leg, places NO trades).

Builds an EMPIRICAL candle-pattern knowledge base for the tracked majors, the HONEST
way: detect a setup on a just-CLOSED 15m candle, then FORWARD-record the realized
close-to-close return 1h (+4 candles) and 4h (+16 candles) later. No lookahead, no
in-sample reuse in the forward log. Over time this answers, per setup: does it actually
predict the next move, or is it folklore?

Setups (per coin, per closed 15m candle; Binance klines, free/no-auth via candle_signals):
  rsi_oversold(<30,+)  rsi_overbought(>70,-)  trend_up(+)/trend_down(-) (EMA20 vs EMA50)
  bull_engulf(+)/bear_engulf(-) (2-candle OHLC)  doji(neutral, body<10% range)
  big_up(+)/big_down(-) (|candle ret|>1.5%)  stretch_up(->mean-revert)/stretch_down(+)
  (the (+/-) is the LABELLED directional bias; the avg forward return is what judges it —
   e.g. a negative mean on big_up means continuation fails and it mean-reverts.)

Two views in the report:
  - FORWARD log (the truth): accumulates no-lookahead samples from deployment onward.
    Slow at first (a few per coin per hour) — like latticesnap, it settles for free.
  - IN-SAMPLE table (illustrative, recomputed each run over fetched history): immediate
    directional read, but overlapping windows => autocorrelated, inflated n. Lower weight.

Graduation gate (this is measurement, NOT a leg): turn a setup into a leg ONLY if, after
n>=30 FORWARD samples, its mean return CI (bootstrap, see leg_health.py) excludes 0 AFTER
a realistic cost, AND it holds out of sample. |mean/SE|>2 here is a hint, not a verdict.
PAPER ONLY, read-only, stdlib only.
Usage: python3 candle_knowledge_logger.py once | report
"""
import os, sys, json, time, urllib.request
import candle_signals as cs   # reuse the keyless Binance fetcher + EMA/RSI + symbol map

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "candle_knowledge_state.json")
LOG = os.path.join(HERE, "candle_knowledge_log.jsonl")   # forward-resolved samples
VAULT = os.path.expanduser("~/Documents/PolymarketVault/Reports/candle_knowledge.md")
UA = {"User-Agent": "Mozilla/5.0"}

IV_MS = 15 * 60 * 1000          # 15m candles
H1, H4 = 4, 16                  # forward horizons in candles (1h, 4h)
KLIMIT = 500                    # 500 x 15m ~= 5.2 days of history per fetch
# core liquid majors (the coins the updown/coinflip legs actually trade)
COINS = ["bitcoin", "ethereum", "solana", "binancecoin", "ripple",
         "dogecoin", "cardano", "avalanche-2"]

SETUP_BIAS = {
    "rsi_oversold": 1, "rsi_overbought": -1,
    "trend_up": 1, "trend_down": -1,
    "bull_engulf": 1, "bear_engulf": -1,
    "big_up": 1, "big_down": -1,
    "stretch_up": -1, "stretch_down": 1,   # stretched from EMA20 => mean-reversion bias
    "doji": 0,
}


# Second candle source via ccxt (any reachable exchange) when Binance REST fails/empty —
# e.g. if api.binance.com geo-blocks (a real risk in India). Tried in order.
_CCXT_EXCHANGES = ["kraken", "coinbase", "kucoin", "okx"]


def _klines_ccxt(binance_sym, limit=KLIMIT):
    """Fallback OHLCV via ccxt, same {t,o,h,l,c} shape and same 15m UTC boundaries, so the
    open_time keys still align with Binance across runs (forward-resolution stays consistent)."""
    try:
        import ccxt
    except Exception:
        return []
    base = binance_sym[:-4] if binance_sym.endswith("USDT") else binance_sym
    for exname in _CCXT_EXCHANGES:
        try:
            ex = getattr(ccxt, exname)({"enableRateLimit": True, "timeout": 15000})
        except Exception:
            continue
        for quote in ("USDT", "USD"):
            try:
                ohlcv = ex.fetch_ohlcv(f"{base}/{quote}", timeframe="15m", limit=limit)
            except Exception:
                continue
            if ohlcv and len(ohlcv) >= 60:
                sys.stderr.write(f"[candle_knowledge] {binance_sym}: ccxt fallback via "
                                 f"{exname} {base}/{quote} ({len(ohlcv)} candles)\n")
                return [{"t": int(c[0]), "o": float(c[1]), "h": float(c[2]),
                         "l": float(c[3]), "c": float(c[4]),
                         "v": float(c[5]) if len(c) > 5 else 0.0} for c in ohlcv]
    return []


def _klines(symbol, limit=KLIMIT):
    """Primary: Binance REST. Falls back to the ccxt second source if Binance fails/empty."""
    url = (f"https://api.binance.com/api/v3/klines"
           f"?symbol={symbol}&interval=15m&limit={limit}")
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=15) as r:
            data = json.loads(r.read())
        kl = [{"t": int(c[0]), "o": float(c[1]), "h": float(c[2]),
               "l": float(c[3]), "c": float(c[4]), "v": float(c[5])} for c in data]
        if kl:
            return kl
    except Exception as e:
        sys.stderr.write(f"[candle_knowledge] binance klines {symbol} failed: {e}\n")
    return _klines_ccxt(symbol, limit)


def fetch_all():
    """Return {coin_id: [klines...]} for the core coins (uses candle_signals' symbol map)."""
    out = {}
    for cid in COINS:
        sym = cs._SYMBOL_MAP.get(cid)
        if not sym:
            continue
        kl = _klines(sym)
        if len(kl) >= 60:
            out[cid] = kl
    return out


def _setups(kl, i):
    """Setups present at candle index i, using ONLY data up to and including i (no lookahead)."""
    k = kl[i]
    o, h, l, c = k["o"], k["h"], k["l"], k["c"]
    closes = [x["c"] for x in kl[:i + 1]]
    s = set()
    rsi = cs._rsi(closes[-30:]) if len(closes) >= 15 else 50.0
    if rsi < 30:
        s.add("rsi_oversold")
    if rsi > 70:
        s.add("rsi_overbought")
    e20, e50 = cs._ema(closes, 20), cs._ema(closes, 50)
    if e20 and e50:
        a, b = e20[-1], e50[-1]
        if a > b * 1.002:
            s.add("trend_up")
        elif a < b * 0.998:
            s.add("trend_down")
        if a > 0:
            dev = c / a - 1
            if dev > 0.02:
                s.add("stretch_up")
            elif dev < -0.02:
                s.add("stretch_down")
    if i >= 1:
        p = kl[i - 1]
        po, pc = p["o"], p["c"]
        if pc < po and c > o and o <= pc and c >= po:
            s.add("bull_engulf")
        if pc > po and c < o and o >= pc and c <= po:
            s.add("bear_engulf")
    rng, body = h - l, abs(c - o)
    if rng > 0 and body < 0.10 * rng:
        s.add("doji")
    r = (c / o - 1) if o > 0 else 0.0
    if r > 0.015:
        s.add("big_up")
    elif r < -0.015:
        s.add("big_down")
    return s


# ---------- forward log (no-lookahead truth) ----------

def load_state():
    if os.path.exists(STATE):
        try:
            return json.load(open(STATE))
        except Exception:
            pass
    return {"hwm": {}, "pending": {}}


def save_state(st):
    tmp = STATE + ".tmp"
    json.dump(st, open(tmp, "w"))
    os.replace(tmp, STATE)


def detect(kls, st):
    """Add pending records for NEW closed candles. First time a coin is seen, SEED its
    high-water mark and add nothing — so every logged sample is genuinely forward."""
    try:
        import orderflow_signals as _ofs
    except ImportError:
        _ofs = None

    new = 0
    for coin, kl in kls.items():
        closed = kl[:-1]                      # drop the still-forming last candle
        if len(closed) < 55:
            continue
        hwm = st["hwm"].get(coin)
        if hwm is None:
            st["hwm"][coin] = closed[-1]["t"]   # seed forward-only
            continue
        for i in range(len(closed)):
            t = closed[i]["t"]
            if t <= hwm:
                continue
            setups = _setups(closed, i)
            if setups:
                rec = {"coin": coin, "det_t": t, "det_c": closed[i]["c"],
                       "setups": sorted(setups)}
                # capture order flow state at detection time (delta / DOM / dPOC)
                sym = cs._SYMBOL_MAP.get(coin)
                if _ofs and sym:
                    try:
                        of = _ofs.get_of(sym, klines_24h=closed[-96:]) or {}
                        for k in ("delta", "book_imb", "poc_dist"):
                            if of.get(k) is not None:
                                rec[k] = of[k]
                    except Exception as _e:
                        sys.stderr.write(f"[candle_knowledge] OF fetch {sym}: {_e}\n")
                st["pending"][f"{coin}:{t}"] = rec
                new += 1
            st["hwm"][coin] = t
    return new


def resolve(kls, st):
    """Mature pending records once the +4h candle exists; write close-to-close returns."""
    done = 0
    cmaps = {coin: {k["t"]: k["c"] for k in kl[:-1]} for coin, kl in kls.items()}
    now_ms = int(time.time() * 1000)
    for key, p in list(st["pending"].items()):
        cm = cmaps.get(p["coin"]) or {}
        t4, t16 = p["det_t"] + H1 * IV_MS, p["det_t"] + H4 * IV_MS
        det_c = p["det_c"]
        if t16 in cm and det_c > 0:
            r1 = (cm[t4] / det_c - 1) if t4 in cm else None
            r4 = cm[t16] / det_c - 1
            rec = {"coin": p["coin"], "det_t": p["det_t"], "setups": p["setups"],
                   "r1h": round(r1, 5) if r1 is not None else None,
                   "r4h": round(r4, 5), "logged": int(time.time())}
            # carry OF fields captured at detection time
            for k in ("delta", "book_imb", "poc_dist"):
                if k in p:
                    rec[k] = p[k]
            with open(LOG, "a") as f:
                f.write(json.dumps(rec) + "\n")
            del st["pending"][key]
            done += 1
        elif now_ms - p["det_t"] > 7 * 86400 * 1000:   # prune un-resolvable stragglers
            del st["pending"][key]
    return done


# ---------- aggregation (shared by forward + in-sample) ----------

def _agg(samples):
    """samples: list of (setups_list, r1h_or_None, r4h_or_None) -> {(setup,hz): stats}."""
    buckets = {}
    for setups, r1, r4 in samples:
        for s in setups:
            for hz, r in (("1h", r1), ("4h", r4)):
                if r is None:
                    continue
                buckets.setdefault((s, hz), []).append(r)
    res = {}
    for (s, hz), rets in buckets.items():
        n = len(rets)
        mean = sum(rets) / n
        bias = SETUP_BIAS.get(s, 0)
        hits = (sum(1 for r in rets if (bias > 0 and r > 0) or (bias < 0 and r < 0))
                if bias != 0 else None)
        var = sum((r - mean) ** 2 for r in rets) / n if n > 1 else 0.0
        se = (var ** 0.5) / (n ** 0.5) if n else 0.0
        t = mean / se if se > 0 else 0.0
        res[(s, hz)] = {"n": n, "mean_bps": round(mean * 10000, 1),
                        "hit_rate": round(100 * hits / n, 1) if hits is not None else None,
                        "t": round(t, 2), "proto": bool(n >= 30 and abs(t) > 2)}
    return res


def _forward_samples():
    out = []
    if not os.path.exists(LOG):
        return out
    for line in open(LOG):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
            out.append((r.get("setups") or [], r.get("r1h"), r.get("r4h")))
        except Exception:
            pass
    return out


def _insample_samples(kls):
    out = []
    for coin, kl in kls.items():
        closed = kl[:-1]
        c = [x["c"] for x in closed]
        for i in range(len(closed) - H4):
            setups = _setups(closed, i)
            if not setups or c[i] <= 0:
                continue
            out.append((sorted(setups), c[i + H1] / c[i] - 1, c[i + H4] / c[i] - 1))
    return out


# ---------- report ----------

def _table(res):
    if not res:
        return ["_(none yet)_"]
    rows = ["| Setup | Hz | n | hit% | mean bps | t | proto? |",
            "|-------|----|--:|-----:|---------:|--:|:------:|"]
    for (s, hz) in sorted(res, key=lambda k: (k[0], k[1])):
        st = res[(s, hz)]
        hr = f"{st['hit_rate']}" if st["hit_rate"] is not None else "—"
        rows.append(f"| {s} | {hz} | {st['n']} | {hr} | {st['mean_bps']} | {st['t']} | "
                    f"{'✅' if st['proto'] else ''} |")
    return rows


def export(st, insample=None):
    fwd = _agg(_forward_samples())
    nfwd = len(_forward_samples())
    L = ["# Candle Knowledge — empirical setup → forward-return base",
         "",
         "> READ-ONLY logger (no trades). Detects 15m candle setups on the just-closed candle,",
         "> then forward-records the 1h/+4h close-to-close return (no lookahead).",
         f"> Updated {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())} · "
         f"forward n={nfwd} · pending={len(st['pending'])} · coins={len(st['hwm'])}",
         "",
         "## FORWARD log — the truth (accumulates from deployment, no lookahead)",
         ""]
    L += _table(fwd)
    proto = [f"{s} {hz}" for (s, hz), v in fwd.items() if v["proto"]]
    L += ["",
          ("**Proto-signals (n≥30, |t|>2): " + ", ".join(proto) + "**") if proto else
          "_No proto-signals yet. Need n≥30 forward samples per setup; this fills over days._",
          "",
          "> A proto-signal is a HINT, not an edge. Before it becomes a leg it must clear "
          "bootstrap CI>0 (leg_health.py) AFTER a realistic cost AND hold out of sample.",
          ""]
    if insample is not None:
        L += ["## IN-SAMPLE table — illustrative only (overlapping windows, inflated n)",
              ""]
        L += _table(insample)
        L += ["", "_In-sample = recomputed each run over fetched history. Backward-looking, "
              "autocorrelated. Use only to prioritise which setups to watch in the forward log._"]
    os.makedirs(os.path.dirname(VAULT), exist_ok=True)
    open(VAULT, "w").write("\n".join(L) + "\n")
    return nfwd, proto


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "once"
    st = load_state()
    if cmd == "once":
        kls = fetch_all()
        if not kls:
            print("candle_knowledge: no candles fetched (Binance unreachable?) — skipped")
            export(st, insample=None)
            return
        nn = detect(kls, st)
        nd = resolve(kls, st)
        save_state(st)
        insample = _agg(_insample_samples(kls))
        nfwd, proto = export(st, insample=insample)
        print(f"candle_knowledge: coins={len(kls)} new_pending={nn} resolved={nd} "
              f"forward_n={nfwd} pending={len(st['pending'])} proto={proto}")
    elif cmd == "report":
        nfwd, proto = export(st, insample=None)
        print(f"candle_knowledge: forward_n={nfwd} pending={len(st['pending'])} proto={proto} -> {VAULT}")
    else:
        print("usage: candle_knowledge_logger.py once|report")


if __name__ == "__main__":
    main()
