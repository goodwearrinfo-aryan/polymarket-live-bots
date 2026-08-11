#!/usr/bin/env python3
"""
heatmap.py — Polymarket liquidity heatmap from the CLOB order book.
Read-only. The depth side of order flow: resting limit-order size by price.
(True MBO/Bookmap needs the order-level stream Polymarket doesn't publish; this
polls the aggregated /book — snapshots, so pulls between polls are invisible.)

Modes:
  snapshot (default)  — one depth ladder: ask walls above, bid walls below,
                        bar length = resting size, brightest = the wall.
  --watch N [secs]    — Bookmap-style time×price grid: N snapshots, cell
                        shade (░▒▓█) = resting size at that price over time.

Run: python3 heatmap.py [conditionId] [--watch N] [poll_secs]
     (no conditionId → top-24h-volume market; picks the YES token)
"""
import json, sys, time, urllib.request
from collections import defaultdict

UA = {"User-Agent": "Mozilla/5.0"}
SHADE = " ░▒▓█"


def get(u):
    return json.loads(urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=15).read())


def resolve_token(cid):
    if cid:
        m = get(f"https://gamma-api.polymarket.com/markets?condition_ids={cid}")[0]
    else:
        m = get("https://gamma-api.polymarket.com/markets?closed=false"
                "&order=volume24hr&ascending=false&limit=1")[0]
    toks = json.loads(m.get("clobTokenIds", "[]"))
    return toks[0], (m.get("question") or "")[:55]


def book(tok):
    b = get(f"https://clob.polymarket.com/book?token_id={tok}")
    bids = [(float(x["price"]), float(x["size"])) for x in b.get("bids", [])]
    asks = [(float(x["price"]), float(x["size"])) for x in b.get("asks", [])]
    return bids, asks


def snapshot(tok, q):
    bids, asks = book(tok)
    bd = dict(bids); ad = dict(asks)
    best_bid = max((p for p, _ in bids), default=0)
    best_ask = min((p for p, _ in asks), default=1)
    mid = (best_bid + best_ask) / 2
    # focus on the actionable window around mid (±0.15); the 0.999/0.001 tails are
    # just resolution backstop liquidity, not tradeable structure.
    win = float(sys.argv[sys.argv.index("--win") + 1]) if "--win" in sys.argv else 0.15
    levels = sorted([p for p, _ in bids + asks if abs(p - mid) <= win], reverse=True)
    mx = max([s for p, s in bids + asks if abs(p - mid) <= win] or [1])
    print(f"\nLIQUIDITY HEATMAP — {q}")
    print(f"best bid {best_bid:.3f}  /  best ask {best_ask:.3f}  /  "
          f"spread {best_ask-best_bid:.3f}  (window ±{win} around mid {mid:.3f})\n")
    print(f"{'price':>6} {'side':>4} {'resting size':>13}")
    for p in levels:
        if p in ad and ad[p] > 0:
            sz, side = ad[p], "ASK"
        elif p in bd and bd[p] > 0:
            sz, side = bd[p], "BID"
        else:
            continue
        bar = "█" * int(40 * sz / mx)
        wall = " ◄ WALL" if sz > 0.5 * mx else ""
        print(f"{p:>6.3f} {side:>4} {sz:>13,.0f}  {bar}{wall}")


def watch(tok, q, n, secs):
    snaps = []
    mid = 0.5
    for i in range(n):
        bids, asks = book(tok)
        bb = max((p for p, _ in bids), default=0)
        ba = min((p for p, _ in asks), default=1)
        mid = (bb + ba) / 2          # true trading mid from best bid/ask
        d = {}
        for p, s in bids + asks:
            d[round(p, 2)] = d.get(round(p, 2), 0) + s
        snaps.append((time.strftime("%H:%M:%S"), d))
        if i < n - 1:
            time.sleep(secs)
    win = float(sys.argv[sys.argv.index("--win") + 1]) if "--win" in sys.argv else 0.15
    prices = sorted({p for _, d in snaps for p in d if abs(p - mid) <= win}, reverse=True)
    mx = max([s for _, d in snaps for p, s in d.items() if abs(p - mid) <= win] or [1])
    print(f"\nLIQUIDITY HEATMAP (time×price) — {q}")
    hdr = "price │ " + " ".join(t[3:] for t, _ in snaps)   # mm:ss
    print(hdr)
    for p in prices:
        row = ""
        for _, d in snaps:
            v = d.get(p, 0)
            row += " " + SHADE[min(4, int(4 * v / mx))] * 2 + "  "
        print(f"{p:>5.2f} │{row}")
    print(f"\nshade = resting size (█ = {mx:,.0f} max); {n} snapshots @ {secs}s")


def main():
    args = [a for a in sys.argv[1:]]
    cid = next((a for a in args if a.startswith("0x")), None)
    tok, q = resolve_token(cid)
    if "--watch" in args:
        i = args.index("--watch")
        n = int(args[i + 1]) if len(args) > i + 1 else 8
        secs = int(args[i + 2]) if len(args) > i + 2 and args[i + 2].isdigit() else 3
        watch(tok, q, n, secs)
    else:
        snapshot(tok, q)


if __name__ == "__main__":
    main()
