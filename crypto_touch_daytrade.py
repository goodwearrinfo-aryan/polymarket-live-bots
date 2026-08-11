#!/usr/bin/env python3
"""crypto_touch_daytrade.py — PAPER multi-coin one-touch tracker + 15m day-trade.
Read-only research. NO live orders, NO keys, NO new bot leg. Supersedes the
BTC-only btc_touch_daytrade.py (same engine, now market-driven across all coins).

Polymarket "dip to $X" / "reach $X" crypto markets resolve YES the instant spot
touches the barrier (one-touch). So each YES contract is a live barrier-touch
probability that re-rates with spot. This tool, for EVERY live such market:
  • discovers it from Gamma (real conditionId + barrier — nothing hardcoded/faked)
  • prices the fair one-touch prob from the matching Binance spot + trailing vol
  • compares model vs the market's real YES → surfaces the biggest gaps
  • can 15m-day-trade-backtest any one market (scalp-vs-hold, net of spread)

Markets discovered live (no fabricated IDs); junk (vol-index/premium/GTA) filtered.
The model marks at TRAILING REALIZED vol, which underprices touch (crypto jumps),
so "market rich" is the systematic baseline (implied>realized) — the SIGNAL is the
RELATIVE gap across markets, and how it moves over time (--once builds that tape).

Usage:
  python3 crypto_touch_daytrade.py                  # discover + model-vs-market table
  python3 crypto_touch_daytrade.py --discover       # refresh the market cache only
  python3 crypto_touch_daytrade.py --once           # one live mark of ALL markets -> log
  python3 crypto_touch_daytrade.py --backtest --cond 0x..   # 15m day-trade one market
"""
import os, sys, json, math, time, re, argparse, urllib.request
from statistics import NormalDist
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "crypto_touch_daytrade.log")
CACHE = os.path.join(HERE, "crypto_touch_markets.json")
_N = NormalDist().cdf
BARS_PER_YEAR = 365 * 24 * 4

# coin name (as it appears in the question) -> Binance spot symbol
COINS = {
    "bitcoin": "BTCUSDT", "btc": "BTCUSDT", "ethereum": "ETHUSDT", "ether": "ETHUSDT",
    "solana": "SOLUSDT", "xrp": "XRPUSDT", "ripple": "XRPUSDT", "dogecoin": "DOGEUSDT",
    "bnb": "BNBUSDT", "cardano": "ADAUSDT", "avalanche": "AVAXUSDT", "chainlink": "LINKUSDT",
    "polkadot": "DOTUSDT", "litecoin": "LTCUSDT", "sui": "SUIUSDT", "toncoin": "TONUSDT",
}
# questions containing these are NOT clean spot-price touches — drop them
JUNK = ("volatility index", "kimchi", "premium", "dominance", "market cap", "marketcap",
        "before gta", "vix", "etf", "flippen", "flippening", "rsi", "google", "trump")
DIR_REACH = ("reach", "hit", "above", "climb to", "exceed", "surpass", "top")
DIR_DIP = ("dip to", "below", "fall to", "drop to", "crash to", "decline to")


def _get(url, timeout=25):
    return json.loads(urllib.request.urlopen(
        urllib.request.Request(url, headers={"User-Agent": "ctd/1.0"}), timeout=timeout).read())


def _parse_barrier(token, suffix):
    v = float(token.replace(",", ""))
    if suffix == "m":
        v *= 1e6
    elif suffix == "k":
        v *= 1e3
    return v


def discover(max_pages=16):
    """Scan Gamma active markets → clean list of crypto one-touch markets.
    Each: {cond, coin(symbol), direction, barrier, end_ts, real_yes, q}."""
    out, seen = [], set()
    for off in range(0, max_pages * 500, 500):
        try:
            ms = _get(f"https://gamma-api.polymarket.com/markets?active=true&closed=false&limit=500&offset={off}")
        except Exception as e:
            print(f"  discover page {off} err: {e}", flush=True); break
        if not ms:
            break
        for m in ms:
            q = (m.get("question") or "").strip()
            ql = q.lower()
            if any(j in ql for j in JUNK):
                continue
            sym = next((COINS[c] for c in COINS if c in ql), None)
            if not sym:
                continue
            # direction + barrier (decimals & m/k suffix handled)
            mth = re.search(r"(dip to|reach|hit|above|below|climb to|fall to|drop to|exceed|surpass)\s*\$?\s*([\d,]+(?:\.\d+)?)\s*([mk])?", ql)
            if not mth:
                continue
            kw = mth.group(1)
            direction = "reach" if any(kw.startswith(d.split()[0]) for d in DIR_REACH) else "dip"
            barrier = _parse_barrier(mth.group(2), mth.group(3))
            if barrier <= 0:
                continue
            cid = m.get("conditionId")
            if not cid or cid in seen:
                continue
            seen.add(cid)
            end = m.get("endDate")
            end_ts = None
            if end:
                try:
                    end_ts = int(time.mktime(time.strptime(end[:19], "%Y-%m-%dT%H:%M:%S")))
                except Exception:
                    end_ts = None
            op = m.get("outcomePrices")
            real = None
            if op:
                try:
                    real = float(json.loads(op)[0]) if isinstance(op, str) else float(op[0])
                except Exception:
                    real = None
            out.append({"cond": cid, "coin": sym, "direction": direction, "barrier": barrier,
                        "end_ts": end_ts, "real_yes": real, "q": q})
    return out


def one_touch(S, B, sigma, T):
    """Fair value of a one-touch contract under driftless GBM (r=0), continuous
    monitoring. Auto-picks lower-barrier (dip, B<S) or upper-barrier (reach, B>S)."""
    if (B < S and S <= B) or (B > S and S >= B):
        return 1.0
    if T <= 0 or sigma <= 0 or S <= 0 or B <= 0:
        return 1.0 if (B < S and S <= B) or (B > S and S >= B) else 0.0
    sqT = sigma * math.sqrt(T)
    half = 0.5 * sigma * sigma * T
    if B < S:                                   # lower barrier ("dip")
        ln = math.log(B / S)
        p = _N((ln + half) / sqT) + (S / B) * _N((ln - half) / sqT)
    else:                                       # upper barrier ("reach")
        a = math.log(B / S)
        p = _N((-a - half) / sqT) + (S / B) * _N((-a + half) / sqT)
    return max(0.0, min(1.0, p))


def klines(symbol, interval="15m", limit=1000):
    u = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    return [(int(k[6]), float(k[4])) for k in _get(u)]


def spot(symbol):
    return float(_get(f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}")["price"])


def realized_vol(closes, bars_per_year):
    rets = [math.log(closes[j] / closes[j - 1]) for j in range(1, len(closes)) if closes[j - 1] > 0]
    if len(rets) < 5:
        return 0.5
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(bars_per_year)


def daily_vol(symbol, days=90):
    """Annualized vol from ~`days` of DAILY closes — horizon-appropriate for the
    multi-month touch markets (1 day of 15m candles wildly underprices a 200-day
    touch). For near-term markets the backtest uses its own 15m trailing vol."""
    ks = klines(symbol, interval="1d", limit=days)
    return realized_vol([c for _, c in ks], 365)


def _market_data(symbols):
    """Fetch spot + horizon-appropriate daily vol per UNIQUE symbol concurrently."""
    def one(sym):
        try:
            return sym, spot(sym), daily_vol(sym)
        except Exception:
            return sym, None, None
    with ThreadPoolExecutor(max_workers=10) as ex:
        return {s: (sp, v) for s, sp, v in ex.map(one, symbols)}


def mark_all(markets):
    """Return rows: (gap_abs, coin, direction, barrier, spot, vol, T_days, model, real, q, cond)."""
    md = _market_data(sorted({m["coin"] for m in markets}))
    now = int(time.time())
    rows = []
    for m in markets:
        sp, vol = md.get(m["coin"], (None, None))
        if sp is None:
            continue
        T = max(0.0, (m["end_ts"] - now) / (365 * 24 * 3600)) if m["end_ts"] else 0.0
        model = one_touch(sp, m["barrier"], vol, T)
        real = m["real_yes"]
        gap = (model - real) if real is not None else None
        rows.append((abs(gap) if gap is not None else -1, m["coin"], m["direction"], m["barrier"],
                     sp, vol, T * 365, model, real, m["q"], m["cond"]))
    rows.sort(reverse=True)
    return rows


def load_markets(refresh=False):
    if not refresh and os.path.exists(CACHE):
        d = json.load(open(CACHE))
        if d.get("markets"):
            return d["markets"]
    print("  discovering live crypto touch markets from Gamma…", flush=True)
    mk = discover()
    json.dump({"markets": mk, "discovered_at": int(time.time())}, open(CACHE, "w"))
    print(f"  cached {len(mk)} markets -> {CACHE}", flush=True)
    return mk


def table(refresh):
    markets = load_markets(refresh)
    rows = mark_all(markets)
    print(f"\n{'='*100}")
    print(f"  CRYPTO ONE-TOUCH — model (realized-vol) vs market YES — {len(rows)} live markets — paper")
    print(f"{'='*100}")
    print(f"  {'coin':8} {'dir':5} {'barrier':>11} {'spot':>11} {'vol':>5} {'days':>5} {'model':>6} {'real':>6} {'gap':>7}  market")
    for ga, coin, d, bar, sp, vol, days, model, real, q, cond in rows:
        rs = f"{real:.3f}" if real is not None else "  -- "
        gs = f"{model-real:+.3f}" if real is not None else "   -- "
        print(f"  {coin:8} {d:5} {bar:>11,.4g} {sp:>11,.4g} {vol*100:>4.0f}% {days:>5.0f} "
              f"{model:>6.3f} {rs:>6} {gs:>7}  {q[:42]}")
    print(f"{'='*100}")
    rich = [r for r in rows if r[8] is not None and (r[7] - r[8]) < -0.10]
    cheap = [r for r in rows if r[8] is not None and (r[7] - r[8]) > 0.10]
    print(f"  {len(rich)} markets where MARKET is rich vs realized-vol model (>10¢ over), "
          f"{len(cheap)} where market is cheap.")
    print("  NB: model marks at trailing realized vol → 'market rich' is the systematic baseline")
    print("  (implied>realized). Signal = the RELATIVE ranking + how each gap moves (--once tape).")
    print()


def once():
    markets = load_markets(False)
    rows = mark_all(markets)
    now = int(time.time())
    recs = [{"ts": now, "coin": c, "dir": d, "barrier": bar, "spot": round(sp, 4),
             "vol": round(vol, 3), "T_days": round(days, 2), "model": round(model, 4),
             "real": (round(real, 4) if real is not None else None),
             "gap": (round(model - real, 4) if real is not None else None), "cond": cond}
            for ga, c, d, bar, sp, vol, days, model, real, q, cond in rows]
    with open(LOG, "a") as f:
        for r in recs:
            f.write(json.dumps(r) + "\n")
    biggest = max((r for r in recs if r["gap"] is not None), key=lambda r: abs(r["gap"]), default=None)
    tag = (f" | biggest gap {biggest['coin']} {biggest['dir']} ${biggest['barrier']:g}: "
           f"model {biggest['model']:.2f} vs real {biggest['real']:.2f} ({biggest['gap']:+.2f})") if biggest else ""
    print(f"[{time.strftime('%Y-%m-%d %H:%MZ', time.gmtime(now))}] marked {len(recs)} crypto touch markets{tag} -> {LOG}")


# ── 15m day-trade backtest for ONE market (the original BTC engine, generalized) ─
def backtest(cond, spread, vol_window):
    markets = load_markets(False)
    m = next((x for x in markets if x["cond"] == cond), None)
    if not m:
        print(f"cond {cond} not in cache — run --discover. Available e.g.:")
        for x in markets[:8]:
            print(f"   {x['cond']}  {x['q'][:50]}")
        return
    ks = klines(m["coin"])
    times = [t // 1000 for t, _ in ks]
    closes = [c for _, c in ks]
    n = len(closes)
    half_sp = spread / 2.0
    end_ts = m["end_ts"] or times[-1]

    marks = []
    for i in range(n):
        T = max(0.0, (end_ts - times[i]) / (365 * 24 * 3600))
        lo = max(1, i - vol_window + 1)
        rets = [math.log(closes[j] / closes[j - 1]) for j in range(lo, i + 1) if closes[j - 1] > 0]
        if len(rets) < 5:
            v = 0.5
        else:
            mean = sum(rets) / len(rets); var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
            v = math.sqrt(var) * math.sqrt(BARS_PER_YEAR)
        marks.append(one_touch(closes[i], m["barrier"], v, T))

    start = vol_window
    def run(pos_at):
        pnl, prev, trades, paid = 0.0, 0, 0, 0.0
        for i in range(start, n - 1):
            pos = pos_at(i)
            if pos != prev:
                paid += half_sp; trades += 1
            pnl += pos * (marks[i + 1] - marks[i]); prev = pos
        if prev:
            paid += half_sp; trades += 1
        return pnl - paid, trades, paid

    breached = (m["barrier"] < closes[0] and min(closes) <= m["barrier"]) or \
               (m["barrier"] > closes[0] and max(closes) >= m["barrier"])
    m0 = marks[start]
    res_val = 1.0 if breached else marks[-1]

    mom_pnl, mom_tr, mom_paid = run(lambda i: 1 if closes[i] < closes[i - 1] else 0)
    MA, band = 8, 0.01
    mr_pnl, mr_tr, mr_paid = run(lambda i: 1 if marks[i] < sum(marks[i - MA + 1:i + 1]) / MA - band else 0)
    rows = [
        ("hold_to_res ", res_val - m0 - half_sp, 1, "touched→1.0" if breached else f"exp≈{res_val:.2f}"),
        ("buyhold     ", marks[-1] - m0 - spread, 1, f"exit {marks[-1]:.2f}"),
        ("momentum_15m", mom_pnl, mom_tr, f"spread paid {mom_paid:.2f}"),
        ("meanrev_15m ", mr_pnl, mr_tr, f"spread paid {mr_paid:.2f}"),
    ]

    print(f"\n{'='*70}")
    print(f"  {m['coin']} ONE-TOUCH {m['direction']} ${m['barrier']:,.4g} — paper 15m day-trade")
    print(f"  {m['q'][:60]}")
    print(f"  {n} candles ≈ {(times[-1]-times[0])/86400:.1f}d | {m['coin']} {closes[0]:,.4g}→{closes[-1]:,.4g} "
          f"| model {m0:.3f}→{marks[-1]:.3f} | spread {spread*100:.0f}¢")
    print(f"{'='*70}")
    print(f"  {'policy':<13} {'net P&L/contract':>17} {'trades':>7}   note")
    for name, pnl, tr, note in rows:
        print(f"  {name:<13} {pnl:>+15.4f}   {tr:>7}   {note}")
    active = max(rows[2:], key=lambda r: r[1])
    hold = rows[0]
    print(f"{'='*70}")
    if active[1] > hold[1] and active[1] > 0:
        print(f"  → active scalp BEAT holding AND cleared spread (+{active[1]:.4f}). Worth a live tape.")
    elif active[1] > hold[1]:
        print(f"  → active beat holding but still ≤0 after spread — spread-dominated (Track 2).")
    else:
        print(f"  → holding wins; 15m scalping loses net of {spread*100:.0f}¢ spread. No edge.")
    print(f"  (marks vs MODEL touch-prob, not a live YES tape — tests mechanics+spread.)\n")


def _corr(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return None
    return sxy / math.sqrt(sxx * syy)


def decompose():
    """Attribute each market's gap movement to SPOT vs VOL/TIME vs MARKET-REPRICING.

    Per interval:  Δgap = Δmodel_spot + Δmodel_voltime − Δreal
      Δmodel_spot   = one_touch(spot_new, B, vol_old, T_old) − model_old   (BTC moved)
      Δmodel_voltime= model_new − one_touch(spot_new, B, vol_old, T_old)    (fair value drifted)
      −Δreal        = market re-priced  ← the ONLY component that can be an edge

    If SPOT dominates, the gap is just tracking BTC (no signal). The edge question is
    whether the MARKET-REPRICING term systematically CLOSES the gap (rich market re-rates
    down) — measured here as market-share of |gap move| + gap mean-reversion corr.
    Reads the --once tape (crypto_touch_daytrade.log)."""
    from collections import defaultdict
    if not os.path.exists(LOG):
        print("no tape yet — wire --once into the watchdog first."); return
    recs = [json.loads(l) for l in open(LOG) if l.strip()]
    S = defaultdict(list)
    for r in recs:
        if r.get("real") is not None:
            S[(r["coin"], r["dir"], r["barrier"])].append(r)
    batches = sorted({r["ts"] for r in recs})
    span_h = (batches[-1] - batches[0]) / 3600 if len(batches) > 1 else 0

    rows, agg_spot, agg_vt, agg_mkt = [], 0.0, 0.0, 0.0
    for key, v in S.items():
        v.sort(key=lambda r: r["ts"])
        if len(v) < 2:
            continue
        coin, dirn, B = key
        spot_c = vt_c = mkt_c = 0.0
        gaps, dgaps = [], []
        for a0, b0 in zip(v[:-1], v[1:]):
            T_old = a0["T_days"] / 365.0
            m_spot = one_touch(b0["spot"], B, a0["vol"], T_old)
            spot_c += m_spot - a0["model"]
            vt_c += b0["model"] - m_spot
            mkt_c += -(b0["real"] - a0["real"])
            gaps.append(a0["gap"]); dgaps.append(b0["gap"] - a0["gap"])
        agg_spot += abs(spot_c); agg_vt += abs(vt_c); agg_mkt += abs(mkt_c)
        tot = abs(spot_c) + abs(vt_c) + abs(mkt_c) or 1e-9
        mkt_share = abs(mkt_c) / tot
        mr = _corr(gaps, dgaps)               # <0 = gap mean-reverts
        # is the MARKET re-pricing CLOSING the gap? gap<0 (rich) & mkt_c>0 → converging
        last_gap = v[-1]["gap"]
        converging = (last_gap < 0 and mkt_c > 0) or (last_gap > 0 and mkt_c < 0)
        rows.append((mkt_share, f"{coin[:4]} {dirn} {B:g}", spot_c, vt_c, mkt_c, mr, converging, len(v)))

    rows.sort(reverse=True)
    print(f"\n{'='*94}")
    print(f"  GAP DECOMPOSITION — spot vs vol/time vs market-repricing — {len(rows)} markets, "
          f"{len(batches)} marks / {span_h:.1f}h")
    print(f"{'='*94}")
    print(f"  {'market':16} {'Σspot':>8} {'Σvol/t':>8} {'Σmkt':>8} {'mkt%':>5} {'gap_MR':>7} {'conv?':>6} {'n':>3}")
    for ms, name, sc, vt, mc, mr, conv, n in rows[:20]:
        mrs = f"{mr:+.2f}" if mr is not None else "  -- "
        print(f"  {name:16} {sc:>+8.3f} {vt:>+8.3f} {mc:>+8.3f} {ms*100:>4.0f}% {mrs:>7} "
              f"{'yes' if conv else 'no':>6} {n:>3}")
    tot = agg_spot + agg_vt + agg_mkt or 1e-9
    n_conv = sum(1 for r in rows if r[6])
    print(f"{'='*94}")
    print(f"  PORTFOLIO attribution of all |gap movement|:  spot {agg_spot/tot*100:.0f}%  "
          f"vol/time {agg_vt/tot*100:.0f}%  market-repricing {agg_mkt/tot*100:.0f}%")
    print(f"  markets where market-repricing is CLOSING the gap (fade signal): {n_conv}/{len(rows)}")
    enough = len(batches) >= 200 and span_h >= 48
    if not enough:
        print(f"  ⚠️ INSUFFICIENT DATA: need ≥200 marks over ≥48h (have {len(batches)}/{span_h:.0f}h). "
              f"Numbers shown to validate the MECHANICS only — not an edge verdict yet.")
    else:
        edge = n_conv > len(rows) * 0.6 and agg_mkt / tot > 0.33
        print(f"  → {'MARKET re-pricing closes gaps across the book — investigate a fade.' if edge else 'no systematic gap-closing; premium persists / spot-dominated. No edge.'}")
    print()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--discover", action="store_true", help="refresh market cache from Gamma")
    ap.add_argument("--once", action="store_true", help="one live mark of all markets -> log")
    ap.add_argument("--backtest", action="store_true", help="15m day-trade backtest one market")
    ap.add_argument("--decompose", action="store_true", help="attribute gap moves: spot vs vol/time vs market-repricing")
    ap.add_argument("--cond", help="conditionId for --backtest")
    ap.add_argument("--spread", type=float, default=0.02)
    ap.add_argument("--vol-window", type=int, default=96)
    a = ap.parse_args()
    if a.discover:
        load_markets(refresh=True)
    elif a.once:
        once()
    elif a.decompose:
        decompose()
    elif a.backtest:
        if not a.cond:
            print("--backtest needs --cond <conditionId> (run plain to see the table)"); sys.exit(1)
        backtest(a.cond, a.spread, a.vol_window)
    else:
        table(refresh=False)
