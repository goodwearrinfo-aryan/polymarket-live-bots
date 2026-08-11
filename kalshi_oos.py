#!/usr/bin/env python3
"""
kalshi_oos.py — out-of-sample test of the nearres + truefade structure on
KALSHI settled markets. No account, no API key (public market data only).
Read-only research; paper lab support. Mirrors backtest_nearres.py method.

Method:
  1. Pull settled binary markets per series (the global settled feed is 100%
     MVE parlay legs — useless; series queries return the real markets).
  2. Per market: 1-min candlesticks for the last WINDOW_H hours before close.
     Usable bar = yes_bid > 0 and yes_ask < 1 (two-sided book).
  3. nearres replay: first bar whose FAVORITE side mid is in [0.88, 0.95]
     -> buy favorite at mid + HALF_SPREAD; exit target +9c / stop -3c on
     subsequent mids (stop first on tie, conservative), else settle.
  4. truefade replay: first bar with YES mid in [0.20, 0.55] -> buy NO,
     settle at resolution (no target/stop — matches the fade thesis).
  5. Controls: allin (buy YES at first usable bar), coinflip (random side).
     Controls MUST lose; if they don't, the replay is broken.
  6. Bootstrap 95% CI on per-trade $ P&L ($2 bets, live sizing).

Run: python3 kalshi_oos.py [max_markets_per_series]
"""
import json, random, sys, time, datetime, urllib.request

API   = "https://api.elections.kalshi.com/trade-api/v2"
UA    = {"User-Agent": "Mozilla/5.0"}
HALF_SPREAD = 0.015
BET, GAIN, STOP = 2.0, 0.09, 0.03
WINDOW_H = 4
NEARRES_BAND = (0.88, 0.95)
FADE_BAND    = (0.20, 0.55)
SERIES = ("KXBTCD", "KXBTC", "KXETHD", "KXETH", "KXHIGHNY", "KXHIGHCHI",
          "KXNBA", "KXMLB", "KXNHL", "KXUFC", "KXSOLD", "KXXRPD")
OUT_F = "kalshi_oos.json"


def get(url, retries=3):
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read())
        except Exception:
            time.sleep(1.5 * (i + 1))
    return None


def settled_markets(series, limit):
    out, cur = [], ""
    while len(out) < limit:
        u = f"{API}/markets?series_ticker={series}&status=settled&limit=200"
        if cur:
            u += f"&cursor={cur}"
        d = get(u)
        if not d:
            break
        for m in d.get("markets", []):
            if m.get("result") in ("yes", "no") and not m.get("mve_collection_ticker"):
                out.append(m)
        cur = d.get("cursor")
        if not cur:
            break
        time.sleep(0.25)
    return out[:limit]


def candles(series, ticker, close_ts):
    d = get(f"{API}/series/{series}/markets/{ticker}/candlesticks"
            f"?start_ts={close_ts - WINDOW_H*3600}&end_ts={close_ts}&period_interval=1")
    if not d:
        return []
    bars = []
    for c in d.get("candlesticks", []):
        try:
            bid = float(c["yes_bid"]["close_dollars"])
            ask = float(c["yes_ask"]["close_dollars"])
        except (KeyError, TypeError, ValueError):
            continue
        if bid <= 0 or ask >= 1 or ask <= bid:
            continue  # one-sided / empty book — mid would be fiction
        bars.append((bid + ask) / 2)
    return bars


def replay_target_stop(bars, i, entry_fill, outcome_price):
    """Walk mids after entry; stop first on tie (conservative)."""
    target, stop = entry_fill + GAIN, entry_fill - STOP
    for mid in bars[i + 1:]:
        if mid <= stop:
            # GAP-THROUGH HONEST (2026-06-14, see Lesson 12): book the stop at the
            # REALIZED mid, not the trigger. Favorites gap past the stop in one tick.
            return mid - HALF_SPREAD
        if mid >= target:
            return target - HALF_SPREAD   # limit fills at its price even on a favorable gap
    return outcome_price


def run(max_per_series=60):
    rng = random.Random(42)
    trades = {"nearres": [], "truefade": [], "allin": [], "coinflip": []}
    n_markets = n_priced = 0
    for series in SERIES:
        ms = settled_markets(series, max_per_series)
        for m in ms:
            n_markets += 1
            try:
                ct = int(datetime.datetime.fromisoformat(
                    m["close_time"].replace("Z", "+00:00")).timestamp())
            except Exception:
                continue
            bars = candles(series, m["ticker"], ct)
            if len(bars) < 5:
                continue
            n_priced += 1
            yes_won = m["result"] == "yes"

            # nearres: favorite side mid in band
            for i, mid in enumerate(bars):
                fav_yes = mid >= 0.5
                fav_mid = mid if fav_yes else 1 - mid
                if NEARRES_BAND[0] <= fav_mid <= NEARRES_BAND[1]:
                    fill = min(0.999, fav_mid + HALF_SPREAD)
                    outcome = 1.0 if (yes_won == fav_yes) else 0.0
                    fav_bars = bars if fav_yes else [1 - b for b in bars]
                    exitp = replay_target_stop(fav_bars, i, fill, outcome)
                    trades["nearres"].append(BET / fill * (exitp - fill))
                    break

            # truefade: YES mid in band -> buy NO, settle
            for mid in bars:
                if FADE_BAND[0] <= mid <= FADE_BAND[1]:
                    fill = min(0.999, (1 - mid) + HALF_SPREAD)
                    outcome = 0.0 if yes_won else 1.0
                    trades["truefade"].append(BET / fill * (outcome - fill))
                    break

            # controls: first usable bar
            mid0 = bars[0]
            fill = min(0.999, mid0 + HALF_SPREAD)
            trades["allin"].append(BET / fill * ((1.0 if yes_won else 0.0) - fill))
            if rng.random() < 0.5:
                trades["coinflip"].append(BET / fill * ((1.0 if yes_won else 0.0) - fill))
            else:
                nfill = min(0.999, (1 - mid0) + HALF_SPREAD)
                trades["coinflip"].append(BET / nfill * ((0.0 if yes_won else 1.0) - nfill))
        time.sleep(0.3)
        print(f"  {series}: {len(ms)} settled markets pulled", flush=True)

    print(f"\nKALSHI OOS — {n_markets} markets scanned, {n_priced} with usable books\n")
    summary = {"n_markets": n_markets, "n_priced": n_priced, "results": {}}
    for leg, pnl in trades.items():
        if not pnl:
            print(f"{leg:9s}  n=0 — no qualifying entries")
            summary["results"][leg] = {"n": 0}
            continue
        n = len(pnl)
        mean = sum(pnl) / n
        wins = sum(1 for x in pnl if x > 0)
        boots = sorted(sum(rng.choice(pnl) for _ in range(n)) / n
                       for _ in range(2000))
        lo, hi = boots[50], boots[1949]
        edge = "EDGE (CI>0)" if lo > 0 else ("loses (CI<0)" if hi < 0 else "~0 / noise")
        print(f"{leg:9s}  n={n:4d}  win={100*wins/n:5.1f}%  $/trade={mean:+.4f}"
              f"  CI=[{lo:+.4f},{hi:+.4f}]  {edge}")
        summary["results"][leg] = {"n": n, "win_pct": round(100*wins/n, 1),
                                   "per_trade": round(mean, 4),
                                   "ci_lo": round(lo, 4), "ci_hi": round(hi, 4),
                                   "edge": lo > 0}
    ctrl_ok = all(summary["results"].get(c, {}).get("ci_hi", -1) < 0 or
                  summary["results"].get(c, {}).get("per_trade", -1) < 0
                  for c in ("allin", "coinflip"))
    print("\ncontrols negative:", "OK" if ctrl_ok else "** BROKEN — do not trust this replay **")
    summary["controls_ok"] = ctrl_ok
    json.dump(summary, open(OUT_F, "w"), indent=1)
    print(f"wrote {OUT_F}")


if __name__ == "__main__":
    run(int(sys.argv[1]) if len(sys.argv) > 1 else 60)
