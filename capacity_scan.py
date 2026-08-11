#!/usr/bin/env python3
"""
capacity_scan.py — does the nearres edge (favorites near resolution) port to
markets DEEP enough to make real money? Reuses backtest_nearres.simulate on each
category's resolved markets, AND probes live book depth on open markets so we see
edge × capacity together. The whole point: nearres works but esports books are
$1-5k deep; find a category where the SAME thesis survives AND can be sized.

Run: python3 capacity_scan.py [n_markets_per_cat]
"""
import json, sys, urllib.request, statistics
import backtest_nearres as BT

UA = {"User-Agent": "Mozilla/5.0"}
N = int(sys.argv[1]) if len(sys.argv) > 1 else 200
BAND = (0.88, 0.95)

CATS = {
    "esports":  BT.ESPORTS_KW,
    "sports":   BT.SPORTS_KW,
    "politics": ("election", "president", "senate", "nominee", "governor",
                 "primary", "prime minister", "parliament", "mayor", "win the 2026"),
    "crypto":   ("bitcoin", "ethereum", "solana", "up or down", "above", "btc", "eth"),
}


def get(u, t=12):
    try:
        return json.loads(urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=t).read())
    except Exception:
        return None


def book_depth_open(kws, sample=25):
    """Median USD resting within 5c of a favorite's mid, on OPEN markets in band."""
    rows = get("https://gamma-api.polymarket.com/markets?closed=false"
               "&order=volume24hr&ascending=false&limit=400") or []
    depths = []
    for m in rows:
        q = (m.get("question") or "").lower()
        if not any(k in q for k in kws):
            continue
        try:
            toks = json.loads(m.get("clobTokenIds", "[]"))
            op = json.loads(m.get("outcomePrices", "[]"))
        except Exception:
            continue
        if len(toks) < 2 or len(op) < 2:
            continue
        yes = float(op[0])
        fav_mid = yes if yes >= 0.5 else 1 - yes
        if not (BAND[0] <= fav_mid <= BAND[1]):
            continue
        tok = toks[0] if yes >= 0.5 else toks[1]
        bk = get(f"https://clob.polymarket.com/book?token_id={tok}", 8)
        if not bk:
            continue
        # USD of asks within 5c above mid (what you'd pay to size a buy)
        usd = sum(float(a["price"]) * float(a["size"])
                  for a in bk.get("asks", [])
                  if abs(float(a["price"]) - fav_mid) <= 0.05)
        if usd > 0:
            depths.append(usd)
        if len(depths) >= sample:
            break
    return depths


def main():
    print(f"CAPACITY SCAN — nearres band {BAND}, {N} resolved mkts/cat, live depth probe\n")
    print(f"{'category':<10}{'n':>5}{'WR':>6}{'$/trade(2$)':>13}{'CI_lo':>9}{'edge?':>7}"
          f"{'med depth$':>12}{'sizeable?':>11}")
    results = {}
    for cat, kws in CATS.items():
        ms = BT.closed_markets(N, kws)
        pnls = []
        for m in ms:
            r = BT.simulate(m, BAND[0], BAND[1], window_h=4)
            if r:
                pnls.append(r[1])
        if len(pnls) >= 5:
            import random
            random.seed(11)
            boots = sorted(sum(random.choices(pnls, k=len(pnls))) / len(pnls) for _ in range(3000))
            wr = 100 * sum(1 for x in pnls if x > 0) / len(pnls)
            per = sum(pnls) / len(pnls)
            ci_lo = boots[75]
            edge = "YES" if ci_lo > 0 else "no"
        else:
            wr = per = ci_lo = 0; edge = f"n={len(pnls)}"
        depths = book_depth_open(kws)
        med = statistics.median(depths) if depths else 0
        sizeable = "YES >$20k" if med > 20000 else ("ok $2-20k" if med > 2000 else "THIN <$2k")
        results[cat] = dict(n=len(pnls), wr=wr, per=per, ci_lo=ci_lo, edge=edge, depth=med)
        print(f"{cat:<10}{len(pnls):>5}{wr:>5.0f}%{per:>+13.4f}{ci_lo:>+9.4f}{edge:>7}"
              f"{med:>12,.0f}{sizeable:>11}")

    print("\nVERDICT:")
    for cat, r in results.items():
        has_edge = isinstance(r["edge"], str) and r["edge"] == "YES"
        deep = r["depth"] > 20000
        if has_edge and deep:
            print(f"  ✅ {cat}: edge AND depth — the real-money target")
        elif has_edge:
            print(f"  ⚠️  {cat}: edge but {'thin' if r['depth']<2000 else 'limited'} books (${r['depth']:,.0f}) — caps size")
        elif deep:
            print(f"  💧 {cat}: deep books but NO edge here — don't trade favorites-near-res here")
        else:
            print(f"  ✗  {cat}: no edge, no depth")
    json.dump(results, open("capacity_scan.json", "w"), indent=1)


if __name__ == "__main__":
    main()
