#!/usr/bin/env python3
"""btc_touch_daytrade.py — PAPER day-trade of a Polymarket one-touch contract on
15-minute BTC candles. Read-only research. NO live orders, NO keys, NO new bot leg.

The market "Will Bitcoin dip to $57,500 in June?" resolves YES the instant any
Binance 1m Low ≤ 57500 (verified on-chain 2026-06-15). So the YES contract is a
live ONE-TOUCH barrier probability that re-rates with BTC — it can be day-traded
without holding to resolution: BTC dips toward the barrier → YES spikes → sell;
BTC bounces → YES falls → re-enter. This script asks the only honest question:

  does scalping the proximity on 15m candles beat just holding — net of spread?

Two marks:
  • MODEL  one-touch closed form (driftless GBM, trailing realized vol). Used for
           the backtest, since CLOB price-history is dead (no historical YES tape).
  • REAL   live Gamma outcomePrices[0] (the actual YES bid context). Used by --once
           to accumulate a real tape going forward for a later model-vs-real edge test.

Policies compared (1 contract, spread charged on every entry/exit):
  hold_to_res   buy YES once, value at expiry = model touch-prob at T→end
  buyhold       buy YES at start, mark-to-model each bar, exit at end
  momentum_15m  long YES next bar iff BTC fell this bar (riding toward barrier)
  meanrev_15m   long YES when model mark dips a band below its trailing MA

Usage:
  python3 btc_touch_daytrade.py --backtest                 # 15m candles, last ~10d
  python3 btc_touch_daytrade.py --backtest --spread 0.02   # round-trip spread (¢)
  python3 btc_touch_daytrade.py --once                     # one live paper mark -> log
"""
import os, sys, json, math, time, argparse, urllib.request
from statistics import NormalDist

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "btc_touch_daytrade.log")

BARRIER = 57500.0
COND_ID = "0x6992fdf39435fb8aa0b765e24f98cb621fd1f7b6458865387c0ea303da2f0003"
END_UTC = 1782878400          # 2026-07-01T04:00:00Z (end of June ET) — resolution
SYMBOL  = "BTCUSDT"
BARS_PER_YEAR = 365 * 24 * 4  # 15m candles
_N = NormalDist().cdf


def _get(url, timeout=20):
    return json.loads(urllib.request.urlopen(
        urllib.request.Request(url, headers={"User-Agent": "touch-dt/1.0"}), timeout=timeout).read())


def klines(symbol, interval="15m", limit=1000):
    """Binance public klines -> [(close_ms, close_price), …] oldest→newest.

    Fallback order (W4):
      1. Crypto-trader JSON at ~/trader/data/candles_<pair>_<tf>.json if fresh
      2. Binance REST (original behavior)
    """
    from trader_candles import load_candles_or_fetch

    def _binance():
        u = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
        return _get(u)

    raw = load_candles_or_fetch(
        symbol, interval,
        binance_fetcher=_binance,
        log_prefix="[btc_touch_daytrade] ",
    )
    # load_candles_or_fetch returns either:
    #   - 12-col Binance rows [openTime, o, h, l, c, v, closeTime, ...] (fallback)
    #   - 6-col [ts_ms, o, h, l, c, v] rows (trader JSON — we translated closeTime→ts)
    # Normalize both to (close_ms, close_price).
    out = []
    for row in raw:
        if isinstance(row, (list, tuple)) and len(row) >= 7:
            # Full Binance kline: closeTime is k[6], close is k[4]
            out.append((int(row[6]), float(row[4])))
        elif isinstance(row, (list, tuple)) and len(row) == 6:
            # Trader JSON normalized: ts is row[0], close is row[4]
            out.append((int(row[0]), float(row[4])))
    return out


def one_touch_down(S, B, sigma, T):
    """P(min BTC over [0,T] ≤ B) under driftless GBM (r=0). Closed form, continuous
    monitoring — the fair value of the YES one-touch contract."""
    if S <= B:
        return 1.0
    if T <= 0 or sigma <= 0:
        return 0.0
    ln = math.log(B / S)
    sqT = sigma * math.sqrt(T)
    half = 0.5 * sigma * sigma * T
    p = _N((ln + half) / sqT) + (S / B) * _N((ln - half) / sqT)
    return max(0.0, min(1.0, p))


def realized_vol(closes, i, window=96):
    """Annualized vol from up to `window` trailing 15m log-returns ending at bar i."""
    lo = max(1, i - window + 1)
    rets = [math.log(closes[j] / closes[j - 1]) for j in range(lo, i + 1) if closes[j - 1] > 0]
    if len(rets) < 5:
        return 0.5
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(BARS_PER_YEAR)


def backtest(spread, vol_window):
    ks = klines(SYMBOL)
    times = [t // 1000 for t, _ in ks]
    closes = [c for _, c in ks]
    n = len(closes)
    half_sp = spread / 2.0

    # model YES mark + trailing vol at each bar
    marks, vols = [], []
    for i in range(n):
        T = max(0.0, (END_UTC - times[i]) / (365 * 24 * 3600))
        v = realized_vol(closes, i, vol_window)
        vols.append(v)
        marks.append(one_touch_down(closes[i], BARRIER, v, T))

    span_days = (times[-1] - times[0]) / 86400
    print(f"\n{'='*68}")
    print(f"  BTC ONE-TOUCH ${BARRIER:,.0f} — paper 15m day-trade backtest")
    print(f"  {n} candles ≈ {span_days:.1f}d  | BTC {closes[0]:,.0f}→{closes[-1]:,.0f}  "
          f"| barrier {(closes[-1]-BARRIER)/closes[-1]*100:+.1f}% away")
    print(f"  model YES {marks[0]:.3f}→{marks[-1]:.3f}  | realized vol {vols[-1]*100:.0f}%  "
          f"| round-trip spread {spread*100:.0f}¢")
    print(f"{'='*68}")

    touched = min(closes) <= BARRIER  # did BTC touch the barrier in-sample?
    start = vol_window                # skip vol-estimate warmup so every mark is clean

    # ---- policy P&L (1 contract; +mark gain while long; pay half_sp per side) ----
    def run(pos_at):
        """pos_at(i) -> desired position {0,1} for bar i+1, decided at close[i]."""
        pnl, prev_pos, trades, paid = 0.0, 0, 0, 0.0
        for i in range(start, n - 1):
            pos = pos_at(i)
            if pos != prev_pos:                 # transition: pay half-spread
                paid += half_sp; trades += 1
            pnl += pos * (marks[i + 1] - marks[i])
            prev_pos = pos
        if prev_pos != 0:                       # close final position
            paid += half_sp; trades += 1
        return pnl - paid, trades, paid

    # baselines (all measured from the post-warmup mark)
    m0 = marks[start]
    bh_pnl = marks[-1] - m0 - spread                            # buy&hold YES, one round trip
    res_val = 1.0 if touched else marks[-1]                     # hold to resolution (in-sample)
    res_pnl = res_val - m0 - half_sp

    # momentum: long next bar iff BTC fell this bar
    mom = run(lambda i: 1 if closes[i] < closes[i - 1] else 0)

    # mean-reversion: long when mark dips `band` below trailing MA of the mark
    MA, band = 8, 0.01
    def mr(i):
        ma = sum(marks[i - MA + 1:i + 1]) / MA
        return 1 if marks[i] < ma - band else 0
    mrv = run(mr)
    print(f"  (skipped first {start} bars of vol warmup; clean marks from YES={m0:.3f})")

    rows = [
        ("hold_to_res ", res_pnl, 1, "touched→1.0" if touched else f"expiry≈{res_val:.2f}"),
        ("buyhold     ", bh_pnl, 1, f"exit {marks[-1]:.2f}"),
        ("momentum_15m", mom[0], mom[1], f"spread paid {mom[2]:.2f}"),
        ("meanrev_15m ", mrv[0], mrv[1], f"spread paid {mrv[2]:.2f}"),
    ]
    print(f"  {'policy':<13} {'net P&L/contract':>17} {'trades':>7}   note")
    for name, pnl, tr, note in rows:
        print(f"  {name:<13} {pnl:>+15.4f}   {tr:>7}   {note}")

    best = max(rows, key=lambda r: r[1])
    active = max((r for r in rows if r[0].strip() in ("momentum_15m", "meanrev_15m")), key=lambda r: r[1])
    hold = next(r for r in rows if r[0].strip() == "hold_to_res")
    print(f"{'='*68}")
    print(f"  best policy: {best[0].strip()}  ({best[1]:+.4f}/contract)")
    if active[1] > hold[1] and active[1] > 0:
        print(f"  → active scalping BEAT holding AND cleared spread (+{active[1]:.4f}). Worth a live paper tape.")
    elif active[1] > hold[1]:
        print(f"  → active beat holding but is still ≤0 after spread — spread-dominated, as Track 2 warned.")
    else:
        print(f"  → holding wins; 15m scalping does NOT beat it net of {spread*100:.0f}¢ spread. No edge here.")
    print(f"  NOTE: backtest marks against the MODEL touch-prob (no live YES tape exists).")
    print(f"  It tests MECHANICS+spread, not real mispricing. Run --once to build a real tape.")
    print()


def once():
    """One live paper mark: model touch-prob + real Gamma YES, appended to log."""
    spot = float(_get(f"https://api.binance.com/api/v3/ticker/price?symbol={SYMBOL}")["price"])
    now = int(time.time())
    T = max(0.0, (END_UTC - now) / (365 * 24 * 3600))
    # quick vol from last day of 15m candles
    ks = klines(SYMBOL, limit=96)
    closes = [c for _, c in ks]
    vol = realized_vol(closes, len(closes) - 1, 96)
    model = one_touch_down(spot, BARRIER, vol, T)
    real = None
    try:
        d = _get(f"https://gamma-api.polymarket.com/markets?condition_ids={COND_ID}")
        m = d[0] if isinstance(d, list) and d else d
        op = m.get("outcomePrices")
        real = float(json.loads(op)[0]) if op else None
    except Exception:
        pass
    rec = {"ts": now, "spot": round(spot, 1), "vol": round(vol, 3),
           "T_days": round(T * 365, 2), "model_yes": round(model, 4),
           "real_yes": real, "edge": (round(model - real, 4) if real is not None else None)}
    with open(LOG, "a") as f:
        f.write(json.dumps(rec) + "\n")
    tag = ""
    if real is not None:
        tag = f"  | real {real:.3f}  model−real {model-real:+.3f} ({'model rich' if model>real else 'model cheap'})"
    print(f"[{time.strftime('%Y-%m-%d %H:%M', time.gmtime(now))}Z] BTC ${spot:,.0f}  "
          f"vol {vol*100:.0f}%  {T*365:.1f}d left  model YES {model:.3f}{tag}  -> {LOG}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--backtest", action="store_true")
    ap.add_argument("--once", action="store_true", help="one live paper mark -> log")
    ap.add_argument("--spread", type=float, default=0.02, help="round-trip spread (default 2¢)")
    ap.add_argument("--vol-window", type=int, default=96, help="trailing candles for vol (default 96=1d)")
    a = ap.parse_args()
    if a.once:
        once()
    else:
        backtest(a.spread, a.vol_window)
