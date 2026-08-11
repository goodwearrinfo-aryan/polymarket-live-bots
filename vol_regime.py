#!/usr/bin/env python3
"""vol_regime.py — daily BTC/ETH volatility-regime covariate (HV20 percentile + Deribit DVOL).

Computes 20d realized vol (annualized x365, crypto trades 24/7) and its
percentile rank over the trailing 120d, per the volatility skill:
  pct < 20  -> signal +1 (low-vol regime, expansion likely)
  pct > 80  -> signal -1 (high-vol regime, contraction likely)
  else      -> signal  0

Also fetches live BTC/ETH DVOL (implied vol index) from Deribit's keyless API
and derives:
  iv_rv_spread = dvol - hv20_annualized_pct   (IV premium over realized)
  iv_regime    = "elevated"   if btc_dvol > 60
               = "suppressed" if btc_dvol < 30
               = "normal"     otherwise

Appends one row per UTC day per asset to vol_regime.csv (idempotent — reruns
on the same day are no-ops), so it can sit in any loop without duplicating.
Writes vol_regime_latest.json for the analyst track to read each cycle.

Covariate for the crypto legs (coinup/coindown/diverg/lateprox); NOT a leg,
places no trades. Stdlib only; CoinGecko free API + Deribit public API.

Usage:  python3 vol_regime.py          # append today's rows + print
        python3 vol_regime.py --print  # compute and print only, no write
"""
import os, csv, json, math, time, argparse, statistics, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "vol_regime.csv")
JSON_OUT = os.path.join(HERE, "vol_regime_latest.json")
UA = {"User-Agent": "Mozilla/5.0"}
HV_WINDOW, LOOKBACK, LOW, HIGH = 20, 120, 20.0, 80.0
COINS = ["bitcoin", "ethereum"]

# iv_regime thresholds (BTC DVOL anchor)
DVOL_ELEVATED = 60.0
DVOL_SUPPRESSED = 30.0

DERIBIT_DVOL_SYMBOLS = {
    "bitcoin":  "BTC",
    "ethereum": "ETH",
}


def closes(coin, retries=3):
    u = (f"https://api.coingecko.com/api/v3/coins/{coin}/market_chart"
         f"?vs_currency=usd&days=365&interval=daily")
    for a in range(retries):
        try:
            with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=20) as r:
                return [p[1] for p in json.load(r)["prices"]]
        except Exception:
            if a == retries - 1:
                raise
            time.sleep(10 * (a + 1))


def fetch_dvol(coin, retries=3):
    """Return the latest hourly DVOL close from Deribit's public API (no key required).

    Endpoint: GET /api/v2/public/get_volatility_index_data
    Returns list of [ts_ms, open, high, low, close]; we take close of last candle.
    Returns None on any failure (fail-soft — DVOL is additive context, not a gate).
    """
    symbol = DERIBIT_DVOL_SYMBOLS.get(coin)
    if not symbol:
        return None
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - 3 * 3600 * 1000  # look back 3h to ensure at least one candle
    u = (f"https://www.deribit.com/api/v2/public/get_volatility_index_data"
         f"?currency={symbol}&start_timestamp={start_ms}&end_timestamp={now_ms}&resolution=3600")
    for a in range(retries):
        try:
            with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=15) as r:
                data = json.load(r)
            rows = data.get("result", {}).get("data", [])
            if not rows:
                return None
            return round(rows[-1][4], 2)  # index 4 = close
        except Exception:
            if a == retries - 1:
                return None
            time.sleep(5 * (a + 1))


def regime(coin):
    c = closes(coin)
    rets = [math.log(c[i] / c[i - 1]) for i in range(1, len(c))]
    hv = [statistics.stdev(rets[i - HV_WINDOW + 1:i + 1]) * math.sqrt(365)
          for i in range(HV_WINDOW - 1, len(rets))]
    if len(hv) < LOOKBACK:
        return None                      # not enough history for a percentile
    cur, window = hv[-1], hv[-LOOKBACK:]
    pct = 100.0 * sum(1 for x in window if x <= cur) / len(window)
    sig = 1 if pct < LOW else (-1 if pct > HIGH else 0)
    return {"hv20": round(cur, 4), "pct": round(pct, 1), "signal": sig}


def main(write=True):
    day = time.strftime("%Y-%m-%d", time.gmtime())
    done = set()
    if write and os.path.exists(OUT):
        with open(OUT) as f:
            done = {(r["date"], r["asset"]) for r in csv.DictReader(f)}
    new_file = not os.path.exists(OUT)

    results = {}  # coin -> {hv20, pct, signal, dvol, iv_rv_spread}
    rows = []
    for coin in COINS:
        r = regime(coin)
        if r is None:
            print(f"{coin}: insufficient history, signal=0 by convention")
            continue

        # fetch DVOL (fail-soft)
        dvol = fetch_dvol(coin)
        hv20_pct = round(r["hv20"] * 100, 2)   # annualized pct (e.g. 46.1)
        iv_rv_spread = round(dvol - hv20_pct, 2) if dvol is not None else None

        dvol_str = f"DVOL={dvol:5.1f}" if dvol is not None else "DVOL=n/a"
        spread_str = f"IV-RV={iv_rv_spread:+.1f}" if iv_rv_spread is not None else "IV-RV=n/a"
        print(f"{day} {coin:9s} HV20={hv20_pct:6.1f}%  pct={r['pct']:5.1f}  "
              f"signal={r['signal']:+d}  {dvol_str}  {spread_str}")

        results[coin] = {
            "hv20": r["hv20"],
            "hv20_pct": hv20_pct,
            "pct_120d": r["pct"],
            "signal": r["signal"],
            "dvol": dvol,
            "iv_rv_spread": iv_rv_spread,
        }

        if write and (day, coin) not in done:
            rows.append([day, coin, r["hv20"], r["pct"], r["signal"], dvol, iv_rv_spread])
        time.sleep(2)

    # Derive cross-asset iv_regime from BTC DVOL (anchor)
    btc = results.get("bitcoin", {})
    eth = results.get("ethereum", {})
    btc_dvol = btc.get("dvol")
    if btc_dvol is None:
        iv_regime = "unknown"
    elif btc_dvol > DVOL_ELEVATED:
        iv_regime = "elevated"
    elif btc_dvol < DVOL_SUPPRESSED:
        iv_regime = "suppressed"
    else:
        iv_regime = "normal"

    print(f"\niv_regime={iv_regime}  (btc_dvol={btc_dvol}, eth_dvol={eth.get('dvol')})")

    # Write JSON snapshot (always, regardless of --print flag, so analyst_agent can read it)
    payload = {
        "date": day,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "btc_hv20": btc.get("hv20"),
        "btc_hv_pct": btc.get("hv20_pct"),
        "btc_hv_pct_120d": btc.get("pct_120d"),
        "btc_signal": btc.get("signal"),
        "btc_dvol": btc_dvol,
        "iv_rv_spread_btc": btc.get("iv_rv_spread"),
        "eth_hv20": eth.get("hv20"),
        "eth_hv_pct": eth.get("hv20_pct"),
        "eth_hv_pct_120d": eth.get("pct_120d"),
        "eth_signal": eth.get("signal"),
        "eth_dvol": eth.get("dvol"),
        "iv_rv_spread_eth": eth.get("iv_rv_spread"),
        "iv_regime": iv_regime,
    }
    try:
        with open(JSON_OUT + ".tmp", "w") as f:
            json.dump(payload, f, indent=2)
        os.replace(JSON_OUT + ".tmp", JSON_OUT)
        print(f"wrote {JSON_OUT}")
    except Exception as e:
        print(f"[JSON write FAIL] {e}")

    if write and rows:
        with open(OUT, "a", newline="") as f:
            w = csv.writer(f)
            if new_file:
                w.writerow(["date", "asset", "hv20", "pct_120d", "signal", "dvol", "iv_rv_spread"])
            w.writerows(rows)
        print(f"appended {len(rows)} row(s) -> {OUT}")
    elif write:
        print("already logged today — no-op (CSV); JSON snapshot always refreshed")

    return payload


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--print", action="store_true", help="compute only, don't append to CSV")
    a = ap.parse_args()
    main(write=not getattr(a, "print"))
