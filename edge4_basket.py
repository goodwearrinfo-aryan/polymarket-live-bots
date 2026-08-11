"""
edge4_basket.py — COMBINATORIAL / BASKET ARB.

Two free-money structures (no prediction required):

  A. BINARY OVERROUND: a single market where best ASK(YES) + best ASK(NO) < 1.00
     after fee. Buy both sides -> guaranteed $1 payout for < $1 cost. (Polymarket
     NO ask = 1 - YES bid, so this reduces to bid/ask crossing — rare but checked.)

  B. MUTUALLY-EXCLUSIVE BASKET (negRisk events): in a "who wins" event where
     exactly one outcome resolves YES, sum of YES prices SHOULD be ~1.00. If
     sum(YES asks) < 1.00, buy every outcome -> one pays $1, cost < $1 = locked
     profit. If sum(YES bids) > 1.00, the SHORT basket (buy every NO) is the arb.

Immune to every killer: no directional view, no trade-out, hold to resolution.
The only risk is execution (filling all legs) and correlated/ambiguous resolution.

Read-only. Paper research. No funds.
"""
import sys
import edge_common as ec

FEE = 0.0   # Polymarket has no per-trade fee; spread is the cost

def binary_overround():
    print("="*80)
    print("  EDGE 4A — BINARY OVERROUND (ask_YES + ask_NO < 1 => lock)")
    print("="*80)
    mkts = ec.poly_markets(pages=8, min_vol=5000)
    hits = []
    for m in mkts:
        # NO ask ≈ 1 - YES bid. Buy YES @ ask, buy NO @ (1 - YES bid).
        if m["bid"] is None or m["ask"] is None:
            continue
        cost = m["ask"] + (1 - m["bid"])  # = 1 + (ask - bid) = 1 + spread  -> always ≥1
        if cost < 1.0 - FEE:
            hits.append((cost, m))
    hits.sort(key=lambda t: t[0])
    print(f"  Scanned {len(mkts)} markets. Locks (cost<1): {len(hits)}")
    print(f"  (Expected ~0: cost = 1 + spread by construction; any hit = crossed book.)\n")
    for cost, m in hits[:15]:
        print(f"  cost={cost:.3f}  bid={m['bid']:.2f} ask={m['ask']:.2f}  {m['q'][:50]}")
    return hits

def basket_arb():
    print("\n" + "="*80)
    print("  EDGE 4B — MUTUALLY-EXCLUSIVE BASKET (sum of YES != 1)")
    print("="*80)
    evs = ec.poly_events(pages=10)
    me = [e for e in evs if e["mutually_exclusive"] and len(e["markets"]) >= 2]
    print(f"  Events: {len(evs)}  mutually-exclusive (negRisk, ≥2 outcomes): {len(me)}\n")
    rows = []
    for e in me:
        mids = [m["yes"] for m in e["markets"]]
        asks = [(m["ask"] if m["ask"] is not None else m["yes"]) for m in e["markets"]]
        bids = [(m["bid"] if m["bid"] is not None else m["yes"]) for m in e["markets"]]
        s_mid = sum(mids); s_ask = sum(asks); s_bid = sum(bids)
        # long-basket arb: buy all YES at ask, sum < 1
        long_edge = 1.0 - s_ask
        # short-basket arb: buy all NO at (1-bid), cost = N - sum(bids); payout = N-1
        n = len(e["markets"])
        short_cost = n - s_bid
        short_edge = (n - 1) - short_cost  # = s_bid - 1
        rows.append((max(long_edge, short_edge), s_mid, s_ask, s_bid, n, e))
    rows.sort(key=lambda t: t[0], reverse=True)
    print(f"  {'edge':>7} {'#out':>4} {'Σmid':>6} {'Σask':>6} {'Σbid':>6}  event")
    print("  " + "-"*74)
    for edge, s_mid, s_ask, s_bid, n, e in rows[:25]:
        kind = ""
        if 1.0 - s_ask > 0.01:
            kind = f"LONG +{1.0-s_ask:.3f}"
        elif s_bid - 1.0 > 0.01:
            kind = f"SHORT +{s_bid-1.0:.3f}"
        print(f"  {edge:>+7.3f} {n:>4} {s_mid:>6.3f} {s_ask:>6.3f} {s_bid:>6.3f}  "
              f"{e['title'][:40]:40} {kind}")
    actionable = [r for r in rows if r[0] > 0.01]
    print(f"\n  → Basket arbs with >1¢ locked edge: {len(actionable)}")
    print(f"    Caveat: needs ALL outcomes listed (missing tail outcome => Σ<1 is fake);")
    print(f"    verify the event is truly exhaustive + mutually exclusive before trusting.")
    return actionable

if __name__ == "__main__":
    binary_overround()
    basket_arb()
