#!/usr/bin/env python3
"""deribit_vol.py — keyless Deribit crypto-options vol research. PAPER, read-only.

The crypto_touch finding hinted crypto markets price IMPLIED vol above REALIZED.
Options make that measurable and (paper) harvestable. This tool, for BTC & ETH, with
NO keys (Deribit public API):
  • pulls the full option chain in one call (mark_iv + Greeks per instrument)
  • builds the ATM implied-vol TERM STRUCTURE per expiry
  • compares each to TRAILING REALIZED vol of matched tenor (Binance daily candles)
  • reports the VOL-RISK PREMIUM (IV − RV) and the 25%-OTM put/call SKEW

A persistently positive VRP = sellers of vol earn a premium — BUT it is compensation
for fat left tails (a crash pays the option buyer), so a positive VRP is NOT free money;
it's the classic short-vol trap. This measures whether it's even there before any
strategy is built. NO orders, NO funds.

Usage:
  python3 deribit_vol.py                 # BTC + ETH term structure + VRP table
  python3 deribit_vol.py --once          # append a snapshot to deribit_vol.log
"""
import os, sys, json, math, time, argparse, urllib.request
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "deribit_vol.log")
DERIBIT = "https://www.deribit.com/api/v2/public/"
BINANCE = "https://api.binance.com/api/v3/"
ASSETS = [("BTC", "BTCUSDT", "btc_usd"), ("ETH", "ETHUSDT", "eth_usd")]


def _get(url, timeout=20):
    return json.loads(urllib.request.urlopen(
        urllib.request.Request(url, headers={"User-Agent": "deribit-vol/1.0"}), timeout=timeout).read())


def deribit(path):
    return _get(DERIBIT + path)["result"]


def realized_vol(symbol, days):
    """Annualized % realized vol from `days` of Binance DAILY closes. Floored at a
    14-day window: fewer daily samples than that is statistically meaningless and
    fabricates wild VRP at the short end (a 3-calm-day window reads ~7% and invents a
    +28 premium). So sub-14d options compare front IV to a 14d realized baseline."""
    days = max(14, min(days, 365))
    ks = _get(BINANCE + f"klines?symbol={symbol}&interval=1d&limit={days + 1}")
    closes = [float(k[4]) for k in ks]
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes)) if closes[i - 1] > 0]
    if len(rets) < 3:
        return None
    m = sum(rets) / len(rets)
    var = sum((r - m) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var * 365) * 100


def parse_instrument(name):
    """'BTC-26MAR27-105000-C' -> (expiry_ts, strike, 'C'|'P'). Deribit expiry = DDMMMYY."""
    p = name.split("-")
    exp_ts = time.mktime(time.strptime(p[1], "%d%b%y")) + 8 * 3600   # Deribit expiry 08:00 UTC
    return exp_ts, float(p[2]), p[3]


def chain(currency):
    """All listed options with a mark IV, parsed."""
    out = []
    for s in deribit(f"get_book_summary_by_currency?currency={currency}&kind=option"):
        if s.get("mark_iv") is None:
            continue
        try:
            exp, strike, typ = parse_instrument(s["instrument_name"])
        except Exception:
            continue
        out.append({"exp": exp, "strike": strike, "type": typ, "iv": s["mark_iv"],
                    "under": s.get("underlying_price"), "oi": s.get("open_interest") or 0,
                    "vol": s.get("volume") or 0})
    return out


def term_structure(currency, symbol, index_name):
    """Per expiry: ATM IV, matched realized vol, VRP, 25%-OTM skew, OI."""
    ch = chain(currency)
    if not ch:
        return None, []
    idx = deribit(f"get_index_price?index_name={index_name}")["index_price"]
    now = time.time()
    byexp = defaultdict(list)
    for o in ch:
        byexp[o["exp"]].append(o)
    rows = []
    for exp in sorted(byexp):
        dte = (exp - now) / 86400
        if dte < 0.2:
            continue
        opts = byexp[exp]
        fwd = next((o["under"] for o in opts if o["under"]), idx)
        atm = min(opts, key=lambda o: abs(o["strike"] - fwd))
        calls = [o for o in opts if o["type"] == "C"]
        puts = [o for o in opts if o["type"] == "P"]
        op = min(puts, key=lambda o: abs(o["strike"] - 0.9 * fwd)) if puts else None
        oc = min(calls, key=lambda o: abs(o["strike"] - 1.1 * fwd)) if calls else None
        skew = (op["iv"] - oc["iv"]) if (op and oc) else None
        rv = realized_vol(symbol, int(round(dte)))
        rows.append({"dte": dte, "atm_iv": atm["iv"], "rv": rv,
                     "vrp": (atm["iv"] - rv) if rv is not None else None,
                     "skew": skew, "fwd": fwd, "oi": sum(o["oi"] for o in opts)})
    return idx, rows


def report():
    print(f"\n{'='*78}\n  DERIBIT CRYPTO-OPTIONS VOL — implied vs realized (VRP) + skew — PAPER\n{'='*78}")
    for cur, sym, idx_name in ASSETS:
        try:
            idx, rows = term_structure(cur, sym, idx_name)
        except Exception as e:
            print(f"  {cur}: fetch failed ({e})"); continue
        if not rows:
            print(f"  {cur}: no expiries"); continue
        print(f"\n  {cur}  spot ${idx:,.0f}   ({len(rows)} expiries)")
        print(f"  {'DTE':>5} {'ATM_IV':>7} {'real_vol':>9} {'VRP(IV-RV)':>11} {'25%skew(P-C)':>13} {'OI':>9}")
        vrps = []
        for r in rows:
            rv = f"{r['rv']:.1f}%" if r["rv"] is not None else "   -- "
            vrp = f"{r['vrp']:+.1f}" if r["vrp"] is not None else "  -- "
            sk = f"{r['skew']:+.1f}" if r["skew"] is not None else "  -- "
            if r["vrp"] is not None:
                vrps.append(r["vrp"])
            print(f"  {r['dte']:>5.0f} {r['atm_iv']:>6.1f}% {rv:>9} {vrp:>11} {sk:>13} {r['oi']:>9,.0f}")
        if vrps:
            avg = sum(vrps) / len(vrps)
            pos = sum(1 for v in vrps if v > 0)
            print(f"  → mean VRP {avg:+.1f} vol-pts across the curve; {pos}/{len(vrps)} expiries IV>RV. "
                  f"{'Premium present (but = crash insurance, NOT free).' if avg > 1 else 'No clear premium.'}")
    print(f"\n{'='*78}")
    print("  NB: a positive VRP is the SHORT-VOL TRAP — it pays steadily then a crash takes it back.")
    print("  Skew (put−call IV) > 0 = market pays up for crash protection. Measure first, harvest never on faith.\n")


def once():
    rec = {"ts": int(time.time())}
    for cur, sym, idx_name in ASSETS:
        try:
            idx, rows = term_structure(cur, sym, idx_name)
            vrps = [r["vrp"] for r in rows if r["vrp"] is not None]
            near = min(rows, key=lambda r: r["dte"]) if rows else None
            rec[cur] = {"spot": round(idx, 2), "expiries": len(rows),
                        "mean_vrp": round(sum(vrps) / len(vrps), 2) if vrps else None,
                        "front_atm_iv": round(near["atm_iv"], 2) if near else None,
                        "front_dte": round(near["dte"], 2) if near else None,
                        "front_skew": round(near["skew"], 2) if near and near["skew"] is not None else None}
        except Exception as e:
            rec[cur] = {"error": str(e)}
    with open(LOG, "a") as f:
        f.write(json.dumps(rec) + "\n")
    print(f"[{time.strftime('%Y-%m-%d %H:%MZ', time.gmtime(rec['ts']))}] "
          + "  ".join(f"{c}: VRP {rec[c].get('mean_vrp')} IV {rec[c].get('front_atm_iv')}"
                      for c, _, _ in ASSETS if isinstance(rec.get(c), dict) and 'error' not in rec[c])
          + f"  -> {LOG}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="append a VRP snapshot to the log")
    a = ap.parse_args()
    once() if a.once else report()
