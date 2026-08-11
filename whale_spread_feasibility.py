#!/usr/bin/env python3
"""whale_spread_feasibility.py — is the bid-ask spread tight enough to harvest the
whale-copy edge? (PAPER research, read-only.)

The drift backtest (whale_drift_backtest.py) found: sharp whales' slow-market bets
are +2.8c/share raw, but the copy edge only clears CI>0 at <=1c ALL-IN copy cost.
That all-in cost = spread + lag-impact. This isolates the SPREAD half: it pulls the
LIVE CLOB book on the slow-market positions these whales currently hold (copyable
band [0.15,0.85]) and measures the real half-spread a copier would pay.

If a meaningful fraction of those books are <=1c half-spread, SPREAD is not the
blocker — the edge's fate then rests on lag-impact alone (measured separately).
If they're all wide, the edge is dead on spread.

Usage: python3 whale_spread_feasibility.py [--wallets file] [--per-whale 25]
"""
import os, sys, json, time, argparse, urllib.request, urllib.parse, statistics
HERE = os.path.dirname(os.path.abspath(__file__))
UA = {"User-Agent": "Mozilla/5.0"}
COINFLIP = ("up or down", "up/down", " et?", "intraday")


def _get(url, tries=4):
    for a in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=15) as r:
                return json.loads(r.read())
        except Exception:
            if a == tries - 1:
                return None
            time.sleep(1.0 * (a + 1))


def open_positions(addr):
    d = _get("https://data-api.polymarket.com/positions?" +
             urllib.parse.urlencode({"user": addr, "limit": 500, "sizeThreshold": 0}))
    return d or []


def book_halfspread(token):
    b = _get(f"https://clob.polymarket.com/book?token_id={token}")
    if not b:
        return None
    bids = b.get("bids") or []; asks = b.get("asks") or []
    if not bids or not asks:
        return None
    bb = max(float(x["price"]) for x in bids)
    ba = min(float(x["price"]) for x in asks)
    if ba <= bb:
        return None
    return (ba - bb) / 2.0


def run(wallets, per_whale, band=(0.15, 0.85)):
    lo, hi = band
    spreads = []
    print(f"{'wallet':<14} {'openpos':>7} {'copyable':>8} {'booked':>6} {'med½sp':>8}")
    for w in wallets:
        pos = open_positions(w)
        # copyable: open (not redeemable), slow market, current price in band
        cand = []
        for p in pos:
            if p.get("redeemable"):
                continue
            title = (p.get("title") or "").lower()
            if any(k in title for k in COINFLIP):
                continue
            cp = float(p.get("curPrice", 0) or 0)
            if not (lo <= cp <= hi):
                continue
            tok = p.get("asset") or p.get("tokenId")
            if tok:
                cand.append(tok)
        cand = cand[:per_whale]
        ws = []
        for tok in cand:
            hs = book_halfspread(tok)
            if hs is not None:
                ws.append(hs); spreads.append(hs)
            time.sleep(0.15)
        med = statistics.median(ws) if ws else float("nan")
        print(f"{w[:12]:<14} {len(pos):>7} {len(cand):>8} {len(ws):>6} {med:>8.4f}")
        time.sleep(0.3)

    print("\n" + "=" * 60)
    if not spreads:
        print("no copyable open positions with live books found.")
        return
    spreads.sort()
    n = len(spreads)
    def pct(thr):  # fraction of books at/below threshold
        return 100.0 * sum(1 for s in spreads if s <= thr) / n
    print(f"half-spread distribution over {n} live books on whales' copyable open positions:")
    print(f"  median  : {statistics.median(spreads):.4f}  ({statistics.median(spreads)*100:.2f}c)")
    print(f"  p25/p75 : {spreads[n//4]:.4f} / {spreads[3*n//4]:.4f}")
    print(f"  <=0.5c  : {pct(0.005):.0f}% of books")
    print(f"  <=1.0c  : {pct(0.010):.0f}% of books   <- the drift-backtest breakeven")
    print(f"  <=2.0c  : {pct(0.020):.0f}% of books")
    frac1 = pct(0.010)
    print()
    if frac1 >= 50:
        print(f"VERDICT: ✅ SPREAD IS NOT THE BLOCKER — {frac1:.0f}% of the markets these whales")
        print("  trade have <=1c half-spread. The whale-copy edge's fate rests on LAG-IMPACT")
        print("  (price drift in the 60s after the whale buys), NOT the spread. Measure that next.")
    elif frac1 >= 20:
        print(f"VERDICT: 🟡 MIXED — only {frac1:.0f}% of books clear 1c. A copy leg would have to")
        print("  GATE on book half-spread <=1c at entry and skip the rest. Viable but narrow.")
    else:
        print(f"VERDICT: ❌ SPREAD KILLS IT — only {frac1:.0f}% of these books are <=1c half-spread.")
        print("  Even before lag-impact, the spread alone exceeds the edge on most of these markets.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--wallets", default=os.path.join(HERE, "whale_candidates_active.json"))
    ap.add_argument("--per-whale", type=int, default=25)
    a = ap.parse_args()
    src = json.load(open(a.wallets))
    wallets = [w["addr"] for w in src] if isinstance(src, list) else list(src.keys())
    print(f"spread-feasibility over {len(wallets)} sharp wallets' OPEN copyable positions\n")
    run(wallets, a.per_whale)
