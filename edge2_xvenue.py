"""
edge2_xvenue.py — CROSS-VENUE DIVERGENCE (Polymarket vs Kalshi).

Thesis: the same real-world event is priced on both venues. When they disagree
by more than round-trip cost, take the cheap side on one venue (and optionally
hedge on the other). Immune to the calibration killer: it doesn't matter if
either venue is well-calibrated, only that they DIFFER.

Matches markets by fuzzy title (Jaccard on content tokens), reports the largest
price gaps. A gap is actionable only if |Δ| > assumed two-venue cost (~4-6¢).

Read-only. Paper research. No funds.
"""
import sys
import edge_common as ec

MIN_JACCARD = 0.34     # title-overlap threshold for "same event"
COST = 0.05            # assumed round-trip cost per leg (spread+fee), both venues

def scan():
    print("="*82)
    print("  EDGE 2 — CROSS-VENUE DIVERGENCE (Polymarket vs Kalshi)")
    print("="*82)
    poly = ec.poly_markets(pages=8, min_vol=10000)
    kal = ec.kalshi_markets(pages=8)
    print(f"  Polymarket: {len(poly)} markets   Kalshi: {len(kal)} markets")
    print(f"  Matching on title Jaccard ≥ {MIN_JACCARD}, flagging |Δ| > {COST:.0%} (cost)\n")

    matches = []
    for pm in poly:
        best, best_j = None, MIN_JACCARD
        for km in kal:
            j = ec.jaccard(pm["q"], km["title"])
            if j > best_j:
                best_j, best = j, km
        if best:
            gap = pm["yes"] - best["yes"]
            matches.append((abs(gap), gap, best_j, pm, best))

    matches.sort(key=lambda t: t[0], reverse=True)
    actionable = [m for m in matches if m[0] > COST]
    print(f"  {len(matches)} title matches, {len(actionable)} with gap > cost\n")
    print(f"  {'|Δ|':>5} {'poly':>5} {'kalshi':>6} {'sim':>4}  event")
    print("  " + "-"*76)
    for absg, gap, j, pm, km in matches[:25]:
        edge_net = absg - COST
        mark = f"NET+{edge_net:.2f}" if edge_net > 0 else ""
        print(f"  {absg:>5.2f} {pm['yes']:>5.2f} {km['yes']:>6.2f} {j:>4.2f}  "
              f"{pm['q'][:42]:42} {mark}")
        if edge_net > 0:
            cheap = "Poly" if pm["yes"] < km["yes"] else "Kalshi"
            print(f"        → buy YES on {cheap} (cheaper by {absg:.2f}); "
                  f"K:{km['title'][:38]}")
    print(f"\n  → Actionable (gap>cost): {len(actionable)}. Verify titles are the SAME")
    print(f"    event + same resolution criteria before trusting any gap (fuzzy match ≠ identical).")
    return actionable

if __name__ == "__main__":
    scan()
