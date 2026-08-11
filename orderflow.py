#!/usr/bin/env python3
"""
orderflow.py — footprint + volume-profile reader off the Polymarket trade feed.
Read-only research; the feasible slice of the order-flow toolkit on this venue
(MBO/true heatmap need an order-level feed Polymarket doesn't publish).

Per market it builds, from data-api/trades:
  FOOTPRINT  — per 1¢ price bucket: BUY vol, SELL vol, delta (aggressor imbalance)
  PROFILE    — POC (point of control = max-volume price), 70% value area
  SUMMARY    — total volume, net delta, buy/sell split

Run: python3 orderflow.py [conditionId] [max_trades]
     (no args → current highest-24h-volume market)
"""
import json, sys, urllib.request
from collections import defaultdict

UA = {"User-Agent": "Mozilla/5.0"}


def get(u):
    return json.loads(urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=15).read())


def top_market():
    ms = get("https://gamma-api.polymarket.com/markets?closed=false"
             "&order=volume24hr&ascending=false&limit=1")
    return ms[0]["conditionId"], (ms[0].get("question") or "")[:60]


def fetch_trades(cid, limit):
    out, offset = [], 0
    while len(out) < limit:
        take = min(500, limit - len(out))
        page = get(f"https://data-api.polymarket.com/trades?market={cid}"
                   f"&limit={take}&offset={offset}")
        if not page:
            break
        out.extend(page)
        if len(page) < take:
            break
        offset += take
    return out


def main():
    cid = sys.argv[1] if len(sys.argv) > 1 else None
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 1000
    if cid:
        q = ""
    else:
        cid, q = top_market()

    trades = fetch_trades(cid, limit)
    if not trades:
        print("no trades"); return

    # pick the dominant token (a binary market has 2 outcome tokens; profile one)
    by_asset = defaultdict(float)
    for t in trades:
        by_asset[t.get("asset")] += float(t.get("size") or 0)
    main_asset = max(by_asset, key=by_asset.get)
    out_name = next((t.get("outcome") for t in trades if t.get("asset") == main_asset), "?")

    foot = defaultdict(lambda: {"buy": 0.0, "sell": 0.0})
    for t in trades:
        if t.get("asset") != main_asset:
            continue
        bucket = round(float(t.get("price") or 0), 2)
        sz = float(t.get("size") or 0)
        foot[bucket]["buy" if t.get("side") == "BUY" else "sell"] += sz

    rows = sorted(foot.items(), reverse=True)
    tot_buy = sum(v["buy"] for _, v in rows)
    tot_sell = sum(v["sell"] for _, v in rows)
    tot = tot_buy + tot_sell
    poc = max(rows, key=lambda kv: kv[1]["buy"] + kv[1]["sell"])[0] if rows else 0

    # 70% value area around POC
    vol_by_px = {p: v["buy"] + v["sell"] for p, v in rows}
    target = 0.70 * tot
    va = {poc}
    acc = vol_by_px[poc]
    prices = sorted(vol_by_px, reverse=True)
    lo = hi = prices.index(poc)
    while acc < target and (lo > 0 or hi < len(prices) - 1):
        up = vol_by_px[prices[hi + 1]] if hi + 1 < len(prices) else -1
        dn = vol_by_px[prices[lo - 1]] if lo - 1 >= 0 else -1
        if up >= dn:
            hi += 1; va.add(prices[hi]); acc += up
        else:
            lo -= 1; va.add(prices[lo]); acc += dn
    va_hi, va_lo = max(va), min(va)

    print(f"\nORDER FLOW — {q or cid[:14]}")
    print(f"outcome token: {out_name}   trades: {sum(1 for t in trades if t.get('asset')==main_asset)}\n")
    print(f"{'price':>6} {'BUY':>9} {'SELL':>9} {'DELTA':>9}  footprint")
    mx = max((v['buy'] + v['sell'] for _, v in rows), default=1)
    for p, v in rows:
        d = v["buy"] - v["sell"]
        bar = "█" * int(24 * (v["buy"] + v["sell"]) / mx)
        tag = " ← POC" if p == poc else (" ·VA" if p in va else "")
        print(f"{p:>6.2f} {v['buy']:>9.0f} {v['sell']:>9.0f} {d:>+9.0f}  {bar}{tag}")
    print(f"\nPOC {poc:.2f}   value area [{va_lo:.2f}, {va_hi:.2f}] (70% vol)")
    print(f"total {tot:,.0f}  buy {tot_buy:,.0f} ({100*tot_buy/tot:.0f}%)  "
          f"sell {tot_sell:,.0f} ({100*tot_sell/tot:.0f}%)  net delta {tot_buy-tot_sell:+,.0f}")

    # ── Order-flow CANDLES: OHLC of price + volume + delta per time bucket ──
    import time as _t
    BUCKET = int(sys.argv[3]) * 60 if len(sys.argv) > 3 else 1800   # default 30m
    main_tr = [t for t in trades if t.get("asset") == main_asset and t.get("timestamp")]
    main_tr.sort(key=lambda t: int(t["timestamp"]))
    cndl = defaultdict(lambda: {"o": None, "h": 0, "l": 9, "c": 0, "buy": 0.0, "sell": 0.0})
    for t in main_tr:
        b = (int(t["timestamp"]) // BUCKET) * BUCKET
        p = float(t.get("price") or 0); sz = float(t.get("size") or 0)
        c = cndl[b]
        if c["o"] is None: c["o"] = p
        c["h"] = max(c["h"], p); c["l"] = min(c["l"], p); c["c"] = p
        c["buy" if t.get("side") == "BUY" else "sell"] += sz
    print(f"\nORDER-FLOW CANDLES ({BUCKET//60}m)   O/H/L/C · volume · delta")
    print(f"{'time':>5} {'open':>5} {'high':>5} {'low':>5} {'close':>6} {'vol':>9} {'delta':>9}")
    for b in sorted(cndl)[-16:]:
        c = cndl[b]
        v = c["buy"] + c["sell"]; d = c["buy"] - c["sell"]
        hm = _t.strftime("%H:%M", _t.localtime(b))
        arrow = "▲" if c["c"] >= c["o"] else "▼"
        print(f"{hm:>5} {c['o']:>5.2f} {c['h']:>5.2f} {c['l']:>5.2f} {c['c']:>5.2f}{arrow} "
              f"{v:>9.0f} {d:>+9.0f}")


if __name__ == "__main__":
    main()
