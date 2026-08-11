"""
edge5_maker.py — MARKET-MAKING EV (be the spread, don't cross it).

Thesis: every dead edge so far was a TAKER (crossed the spread, paid it). A
MAKER posts limit orders and EARNS the spread. In markets with UNINFORMED flow
(novelty/meme/low-stakes), passive MM is +EV because the counterparty isn't
sharp. The risk is adverse selection (informed flow runs you over).

This probe measures, per market, the two quantities that decide maker EV:
  spread  = ask - bid                      (what you earn per round-trip fill)
  realized adverse move proxy: |distance of mid from 0.5| and volume
  (high-vol + tight markets = sharp flow = adverse selection;
   mid-range + wide spread + modest vol = uninformed = MM-friendly)

Scores markets by a heuristic maker-attractiveness: wide spread, moderate vol,
mid-range price (most uncertainty = most uninformed two-way flow).

Read-only. Paper research. No funds. (This is a SCREEN, not a backtest —
true maker EV needs fill simulation against the tape; flagged as next step.)
"""
import sys
import edge_common as ec

def scan():
    print("="*82)
    print("  EDGE 5 — MARKET-MAKING EV SCREEN (earn the spread on uninformed flow)")
    print("="*82)
    mkts = ec.poly_markets(pages=10, min_vol=1000)
    rows = []
    for m in mkts:
        if m["bid"] is None or m["ask"] is None:
            continue
        spread = m["ask"] - m["bid"]
        if spread <= 0 or spread > 0.5:
            continue
        mid = (m["bid"] + m["ask"]) / 2
        # uninformed-flow heuristic: want wide-ish spread (earn), mid-range price
        # (max two-way uncertainty), and NOT ultra-high vol (sharp flow).
        centrality = 1 - abs(mid - 0.5) * 2     # 1 at 0.5, 0 at rails
        vol = m["vol"]
        # maker score: spread you earn * how uninformed (central) * liquidity sanity
        # penalize very high vol (sharp) and very low (can't fill)
        liq_band = 1.0 if 5000 <= vol <= 200000 else (0.4 if vol < 5000 else 0.3)
        score = spread * (0.4 + 0.6 * centrality) * liq_band
        rows.append((score, spread, mid, vol, m))
    rows.sort(key=lambda t: t[0], reverse=True)
    print(f"  Scanned {len(mkts)} markets with two-sided quotes.\n")
    print(f"  Maker EV per round-trip ≈ spread/2 earned − adverse_selection_cost.")
    print(f"  A market is MM-friendly if spread is wide AND flow is uninformed (central, modest vol).\n")
    print(f"  {'score':>6} {'spread':>7} {'mid':>5} {'vol($k)':>8}  question")
    print("  " + "-"*72)
    for score, spread, mid, vol, m in rows[:25]:
        print(f"  {score:>6.3f} {spread:>7.3f} {mid:>5.2f} {vol/1000:>8.0f}  {m['q'][:50]}")
    # aggregate: median spread by vol bucket (sharpness gradient)
    print("\n  Median spread by volume bucket (sharp flow => tighter spreads):")
    buckets = {"<5k": [], "5-50k": [], "50-200k": [], ">200k": []}
    for _, spread, _, vol, _ in rows:
        if vol < 5000: buckets["<5k"].append(spread)
        elif vol < 50000: buckets["5-50k"].append(spread)
        elif vol < 200000: buckets["50-200k"].append(spread)
        else: buckets[">200k"].append(spread)
    for b, sp in buckets.items():
        if sp:
            sp.sort(); med = sp[len(sp)//2]
            print(f"    {b:>9}: n={len(sp):>4}  median spread={med:.3f}  (earn ~{med/2*100:.1f}¢/fill)")
    print(f"\n  → MM-friendly = wide spread + uninformed flow. Top candidates above.")
    print(f"    REAL EV requires fill-sim vs the tape (do you actually get filled, and")
    print(f"    does the mid run against you after?). This screen ranks where to RUN that sim.")
    return rows

if __name__ == "__main__":
    scan()
