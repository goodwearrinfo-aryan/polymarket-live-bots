#!/usr/bin/env python3
"""whale_lagimpact.py — the LAST variable for the whale-copy edge (PAPER, read-only).

Known so far (whale_drift_backtest + whale_spread_feasibility):
  raw whale edge  = +2.8c/share   (n=5,574, real)
  spread cost     = ~0.5c          (69% of markets <=1c)
  net copy edge  ≈ +2.8c - 0.5c - LAG_IMPACT
LAG_IMPACT = how much the price drifts UP in the minutes after a sharp whale's
buy — a copier enters late and pays that drift. This measures it directly:

  for each recent fresh whale BUY on a slow market, pull that market's trade tape,
  find trades on the SAME outcome token in [T+LAG_MIN, T+LAG_MAX] after the whale,
  lag_impact = median(post-trade price) - whale_fill_price.

Only RECENT buys qualify (the data-api market tape only returns the recent window),
so n is smaller than the drift backtest — this is a freshness-limited estimate that
the live sharpdrift_probe.py logger corroborates forward.

Usage: python3 whale_lagimpact.py [--lag-min 60] [--lag-max 900] [--days 4]
"""
import os, sys, json, time, argparse, urllib.request, urllib.parse, statistics
HERE = os.path.dirname(os.path.abspath(__file__))
UA = {"User-Agent": "Mozilla/5.0"}
COINFLIP = ("up or down", "up/down", " et?", "intraday")
NOW = int(time.time())


def _get(path, q, tries=4):
    u = f"https://data-api.polymarket.com/{path}?" + urllib.parse.urlencode(q)
    for a in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=15) as r:
                return json.loads(r.read())
        except Exception:
            if a == tries - 1:
                return []
            time.sleep(1.0 * (a + 1))


def whale_recent_buys(addr, days, band):
    lo, hi = band
    cutoff = NOW - days * 86400
    ts = _get("trades", {"user": addr, "limit": 500})
    seen, out = set(), []
    for t in sorted(ts, key=lambda x: int(x.get("timestamp", 0))):
        if t.get("side") != "BUY":
            continue
        tm = int(t.get("timestamp", 0))
        if tm < cutoff:
            continue
        title = (t.get("title") or "").lower()
        if any(k in title for k in COINFLIP):
            continue
        p = float(t.get("price", 0) or 0)
        if not (lo <= p <= hi):
            continue
        cid = t.get("conditionId"); tok = t.get("asset")
        if not cid or not tok:
            continue
        key = (cid, tok)
        if key in seen:
            continue
        seen.add(key)
        out.append({"cid": cid, "tok": str(tok), "price": p, "ts": tm,
                    "title": t.get("title", "")})
    return out


_tape = {}
def market_tape(cid):
    if cid in _tape:
        return _tape[cid]
    d = _get("trades", {"market": cid, "limit": 1000}) or []
    _tape[cid] = d
    return d


def lag_impact(buy, lag_min, lag_max):
    """median post-trade price on the same token in [T+lag_min, T+lag_max] minus whale price."""
    tape = market_tape(buy["cid"])
    win = [float(x["price"]) for x in tape
           if str(x.get("asset")) == buy["tok"]
           and buy["ts"] + lag_min <= int(x.get("timestamp", 0)) <= buy["ts"] + lag_max]
    if not win:
        return None
    return statistics.median(win) - buy["price"]


def run(wallets, lag_min, lag_max, days, band=(0.15, 0.85)):
    impacts = []
    print(f"window: copier enters {lag_min}-{lag_max}s after the whale; recent {days}d buys only\n")
    print(f"{'wallet':<14} {'buys':>5} {'measured':>9} {'med-impact':>11}")
    for w in wallets:
        buys = whale_recent_buys(w, days, band)
        wi = []
        for b in buys:
            li = lag_impact(b, lag_min, lag_max)
            if li is not None:
                wi.append(li); impacts.append(li)
            time.sleep(0.15)
        med = statistics.median(wi) if wi else float("nan")
        print(f"{w[:12]:<14} {len(buys):>5} {len(wi):>9} {med:>+11.4f}")
        time.sleep(0.3)

    print("\n" + "=" * 64)
    if len(impacts) < 5:
        print(f"only n={len(impacts)} measurable — too thin for a verdict (market tapes rolled past "
              f"older buys). Lean on the live sharpdrift logger as it accumulates.")
        return
    impacts.sort(); n = len(impacts)
    med = statistics.median(impacts); mean = statistics.mean(impacts)
    EDGE, SPREAD = 0.028, 0.005
    net = EDGE - SPREAD - med
    print(f"LAG-IMPACT over n={n} recent whale buys ({lag_min}-{lag_max}s after):")
    print(f"  median impact : {med:+.4f}  ({med*100:+.2f}c a copier pays vs the whale)")
    print(f"  mean impact   : {mean:+.4f}")
    print(f"  p25/p75       : {impacts[n//4]:+.4f} / {impacts[3*n//4]:+.4f}")
    print(f"\n  NET COPY EDGE ≈ +2.8c (raw) − 0.5c (spread) − {med*100:.2f}c (lag) = {net*100:+.2f}c/share")
    if net > 0.005:
        print(f"  VERDICT: ✅ POSITIVE net copy edge (~{net*100:.1f}c) — build the gated copy leg, "
              f"settle at resolution, prove to 30 exits + CI>0.")
    elif net > 0:
        print(f"  VERDICT: 🟡 THIN positive (~{net*100:.1f}c) — real but fragile; gate hard on "
              f"tight-spread markets and size small. Worth a gated paper test.")
    else:
        print(f"  VERDICT: ❌ lag-impact eats it ({med*100:.1f}c) — net {net*100:.1f}c. The whale's own "
              f"buy moves price too far before a copier can follow. Edge confirmed unharvestable.")
    print("\n  (corroborate with sharpdrift_probe.py live log as it accumulates forward-looking drift.)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--wallets", default=os.path.join(HERE, "whale_candidates_active.json"))
    ap.add_argument("--lag-min", type=int, default=60)
    ap.add_argument("--lag-max", type=int, default=900)
    ap.add_argument("--days", type=int, default=5)
    a = ap.parse_args()
    src = json.load(open(a.wallets))
    wallets = [w["addr"] for w in src] if isinstance(src, list) else list(src.keys())
    print(f"lag-impact measurement over {len(wallets)} sharp wallets\n")
    run(wallets, a.lag_min, a.lag_max, a.days)
