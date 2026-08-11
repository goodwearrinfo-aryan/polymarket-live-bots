#!/usr/bin/env python3
"""
wallet_algo.py — reverse-engineer SHARP wallets' strategies from public data.
Read-only research; paper lab support. No keys, no orders.

For each wallet in insider_wallets.json, pulls up to MAX_TRADES from
data-api.polymarket.com/trades and profiles:
  - category mix (sports/esports/crypto/politics/other via title keywords)
  - entry price band histogram (where do they buy?)
  - side bias (BUY vs SELL, YES vs NO)
  - bet sizing (median/p90 USD, flat vs scaled)
  - time-of-day histogram (UTC hours — reveals their timezone / bot-ness)
  - inter-trade gap (median seconds — bots cluster <60s)
  - repeat-market behaviour (laddering / averaging detection)
  - hold style where both BUY and SELL on same market appear (scalp vs hold)

Output: stdout report + wallet_algo.json
Run: python3 wallet_algo.py [max_trades_per_wallet]
"""
import json, os, sys, time, urllib.request
from collections import Counter, defaultdict

BASE   = os.path.expanduser("~/Documents/polymarket")
OUT_F  = os.path.join(BASE, "wallet_algo.json")
UA     = {"User-Agent": "Mozilla/5.0"}
MAX_TRADES = int(sys.argv[1]) if len(sys.argv) > 1 else 1000

CATS = {
    "esports":  ("counter-strike", "cs2", "valorant", "league of legends", "dota",
                 "iem ", "blast", "vct ", "lck", "lpl", "esport"),
    "sports":   ("nba", "nfl", "mlb", "nhl", "tennis", "atp", "wta", "soccer",
                 "premier league", "la liga", "serie a", "ufc", " vs. ", " vs "),
    "crypto":   ("bitcoin", "btc", "ethereum", "eth", "solana", "sol ", "xrp",
                 "crypto", "up or down"),
    "politics": ("election", "president", "senate", "congress", "trump", "biden",
                 "nominee", "governor", "parliament", "minister"),
}

def fetch_trades(addr, limit=MAX_TRADES):
    out, offset = [], 0
    while len(out) < limit:
        take = min(500, limit - len(out))
        url = (f"https://data-api.polymarket.com/trades?user={addr}"
               f"&limit={take}&offset={offset}")
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=15) as r:
                page = json.loads(r.read())
        except Exception as e:
            sys.stderr.write(f"[{addr[:8]}] fetch error at offset {offset}: {e}\n")
            break
        if not page:
            break
        out.extend(page)
        if len(page) < take:
            break
        offset += take
        time.sleep(0.3)
    return out

def cat_of(title):
    t = title.lower()
    for cat, kws in CATS.items():
        if any(k in t for k in kws):
            return cat
    return "other"

def pct(n, d):
    return round(100 * n / d, 1) if d else 0.0

def quantile(sorted_vals, q):
    if not sorted_vals:
        return 0.0
    i = min(len(sorted_vals) - 1, int(q * len(sorted_vals)))
    return sorted_vals[i]

def profile(addr, label):
    trades = fetch_trades(addr)
    if not trades:
        return {"label": label, "n": 0}
    trades.sort(key=lambda t: int(t.get("timestamp", 0)))

    n = len(trades)
    cats   = Counter(cat_of(t.get("title", "")) for t in trades)
    sides  = Counter(t.get("side", "?") for t in trades)
    outcomes = Counter(t.get("outcome", "?") for t in trades if t.get("side") == "BUY")

    # entry price bands (BUY trades only — where they take positions)
    bands = Counter()
    for t in trades:
        if t.get("side") != "BUY":
            continue
        p = float(t.get("price", 0))
        bands[f"{int(p*10)/10:.1f}-{int(p*10)/10+0.1:.1f}"] += 1

    usd = sorted(float(t.get("price", 0)) * float(t.get("size", 0)) for t in trades)
    hours = Counter(time.gmtime(int(t.get("timestamp", 0))).tm_hour for t in trades)

    # inter-trade gaps (bot detection)
    ts = [int(t.get("timestamp", 0)) for t in trades]
    gaps = sorted(b - a for a, b in zip(ts, ts[1:]) if b > a)

    # repeat-market behaviour
    by_mkt = defaultdict(list)
    for t in trades:
        by_mkt[t.get("conditionId") or t.get("title", "")].append(t)
    repeats = [v for v in by_mkt.values() if len(v) > 1]
    # scalp detection: same market, both BUY and SELL
    scalped = sum(1 for v in repeats
                  if {x.get("side") for x in v} >= {"BUY", "SELL"})

    span_days = (ts[-1] - ts[0]) / 86400 if len(ts) > 1 else 0

    return {
        "label": label, "n": n,
        "span_days": round(span_days, 1),
        "trades_per_day": round(n / span_days, 1) if span_days else None,
        "cats": {k: pct(v, n) for k, v in cats.most_common()},
        "sides": dict(sides),
        "buy_outcome_bias": {k: pct(v, sum(outcomes.values())) for k, v in outcomes.most_common()},
        "entry_bands_pct": {k: pct(v, sum(bands.values()))
                            for k, v in sorted(bands.items())},
        "usd_median": round(quantile(usd, 0.5), 2),
        "usd_p90": round(quantile(usd, 0.9), 2),
        "usd_max": round(usd[-1], 2) if usd else 0,
        "top_hours_utc": [h for h, _ in hours.most_common(5)],
        "median_gap_s": quantile(gaps, 0.5) if gaps else None,
        "pct_gaps_under_60s": pct(sum(1 for g in gaps if g < 60), len(gaps)),
        "n_markets": len(by_mkt),
        "pct_markets_repeated": pct(len(repeats), len(by_mkt)),
        "n_scalped_markets": scalped,
        "sample_titles": [t.get("title", "")[:60] for t in trades[-5:]],
    }

def verdict(p):
    """One-line human read of the algo."""
    if p["n"] == 0:
        return "no data"
    bits = []
    top_cat = next(iter(p["cats"]), "?")
    bits.append(f"{top_cat}-focused ({p['cats'].get(top_cat, 0)}%)")
    if p["pct_gaps_under_60s"] > 40:
        bits.append("BOT (gaps<60s)")
    elif p["pct_gaps_under_60s"] > 15:
        bits.append("semi-automated")
    else:
        bits.append("manual-paced")
    bands = p["entry_bands_pct"]
    if bands:
        top_band = max(bands, key=bands.get)
        bits.append(f"buys mostly {top_band} ({bands[top_band]}%)")
    if p["n_scalped_markets"] > p["n_markets"] * 0.3:
        bits.append("scalper (in-and-out)")
    elif p["pct_markets_repeated"] > 50:
        bits.append("ladders/averages into markets")
    else:
        bits.append("one-shot entries, holds")
    return " · ".join(bits)

def main():
    wallets = json.load(open(os.path.join(BASE, "insider_wallets.json")))
    report = {}
    for addr, label in wallets.items():
        print(f"\n{'='*78}\n{addr}  ({label})")
        p = profile(addr, label)
        report[addr] = p
        if p["n"] == 0:
            print("  no trades returned")
            continue
        print(f"  n={p['n']} trades over {p['span_days']}d "
              f"({p['trades_per_day']}/day) across {p['n_markets']} markets")
        print(f"  cats: {p['cats']}")
        print(f"  sides: {p['sides']}  buy-outcome bias: {p['buy_outcome_bias']}")
        print(f"  entry bands: {p['entry_bands_pct']}")
        print(f"  bet USD: median ${p['usd_median']}  p90 ${p['usd_p90']}  max ${p['usd_max']}")
        print(f"  top hours UTC: {p['top_hours_utc']}  median gap {p['median_gap_s']}s "
              f"({p['pct_gaps_under_60s']}% gaps <60s)")
        print(f"  repeated markets: {p['pct_markets_repeated']}%  scalped: {p['n_scalped_markets']}")
        print(f"  recent: {p['sample_titles']}")
        v = verdict(p)
        report[addr]["verdict"] = v
        print(f"  >> VERDICT: {v}")
    json.dump(report, open(OUT_F, "w"), indent=1)
    print(f"\nwrote {OUT_F}")

if __name__ == "__main__":
    main()
