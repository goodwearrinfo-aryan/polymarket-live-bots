#!/usr/bin/env python3
"""leg_correlation.py — quantum-inspired-feature-selection style redundancy
detector for scalp_lab legs.

The problem it solves (from CLAUDE.md): with ~80 legs, many are "price-costumes"
of each other — they fire on the SAME markets, take the SAME side, and so carry
NO independent information. Crowning one as "the edge" when it's just a relabel
of another is best-of-N selection bias.

This tool, on closed-trade history (scalp_lab_state.json), computes:

  1. MARKET OVERLAP  — Jaccard(market_ids_A, market_ids_B): do two legs trade the
                       same markets? High = candidate costumes.
  2. SIDE AGREEMENT  — on shared markets, do they take the same side? High same-
                       side + high overlap = redundant signal (one is dropable).
  3. PNL CORRELATION — on shared markets, do their per-market P&Ls move together?
  4. INDEPENDENCE     — rank each leg by UNIQUE market coverage (markets only it
                       traded). This is the "independent information" score: the
                       quantum-inspired feature-selection analog — keep the legs
                       that cover information no other leg does.

No quantum hardware, no external deps. Pure stdlib over your real data.

Usage:  python3 leg_correlation.py
        python3 leg_correlation.py --min-exits 5   # only legs with >=5 exits
        python3 leg_correlation.py --pairs 20      # show top N redundant pairs
"""
import json
import os
import sys
import argparse
from collections import defaultdict
from math import sqrt

BASE  = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(BASE, "scalp_lab_state.json")


def load_legs(min_exits):
    """Return {leg: [trade, ...]} for legs with >= min_exits priced closed trades."""
    with open(STATE) as f:
        data = json.load(f)
    out = {}
    for leg, book in data.items():
        if not isinstance(book, dict) or "closed" not in book:
            continue
        priced = [t for t in book["closed"] if t.get("pnl_usdc") is not None
                  and t.get("id") is not None]
        if len(priced) >= min_exits:
            out[leg] = priced
    return out


def market_sets(legs):
    """leg -> set(market_id)."""
    return {leg: {t["id"] for t in trades} for leg, trades in legs.items()}


def jaccard(a, b):
    if not a or not b:
        return 0.0
    u = len(a | b)
    return len(a & b) / u if u else 0.0


def side_map(trades):
    """market_id -> side ('YES'/'NO') for one leg (last trade on that market wins)."""
    m = {}
    for t in trades:
        m[t["id"]] = t.get("side", "?")
    return m


def pnl_by_market(trades):
    """market_id -> summed pnl for one leg."""
    d = defaultdict(float)
    for t in trades:
        d[t["id"]] += t.get("pnl_usdc", 0.0) or 0.0
    return dict(d)


def pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx == 0 or vy == 0:
        return None
    return cov / sqrt(vx * vy)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-exits", type=int, default=5,
                    help="only analyze legs with >= this many priced exits")
    ap.add_argument("--pairs", type=int, default=15,
                    help="how many top-overlap pairs to print")
    ap.add_argument("--overlap-floor", type=float, default=0.15,
                    help="ignore pairs sharing fewer markets than this Jaccard")
    args = ap.parse_args()

    legs = load_legs(args.min_exits)
    if len(legs) < 2:
        print(f"Need >=2 legs with >={args.min_exits} exits. Found {len(legs)}.")
        sys.exit(0)

    msets = market_sets(legs)
    sides = {leg: side_map(t) for leg, t in legs.items()}
    pmkt  = {leg: pnl_by_market(t) for leg, t in legs.items()}

    print("=" * 74)
    print("  LEG REDUNDANCY / INDEPENDENCE  —  quantum-inspired feature selection")
    print(f"  legs analyzed: {len(legs)}  (>= {args.min_exits} priced exits each)")
    print("=" * 74)

    # ── 1+2+3. pairwise overlap, side-agreement, pnl-corr ──────────────────────
    pairs = []
    names = sorted(legs.keys())
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = names[i], names[j]
            sa, sb = msets[a], msets[b]
            jac = jaccard(sa, sb)
            shared = sa & sb
            if jac < args.overlap_floor or len(shared) < 2:
                continue
            # side agreement on shared markets
            agree = sum(1 for mid in shared
                        if sides[a].get(mid) == sides[b].get(mid))
            agree_pct = agree / len(shared)
            # pnl correlation on shared markets
            xs = [pmkt[a][mid] for mid in shared]
            ys = [pmkt[b][mid] for mid in shared]
            corr = pearson(xs, ys)
            # redundancy score: overlap x side-agreement (0..1). High = costume.
            redun = jac * agree_pct
            pairs.append((redun, a, b, jac, len(shared), agree_pct, corr))

    pairs.sort(reverse=True)
    print(f"\n  TOP REDUNDANT PAIRS  (overlap x same-side; high = price-costumes)")
    print(f"  {'leg A':<13}{'leg B':<13}{'shared':>7}{'jacc':>7}{'same-side':>10}{'pnl-corr':>9}{'  flag'}")
    print("  " + "-" * 70)
    if not pairs:
        print("  (no pairs above overlap floor — legs are well-separated)")
    for redun, a, b, jac, nsh, ap_, corr in pairs[:args.pairs]:
        cstr = f"{corr:+.2f}" if corr is not None else "  n/a"
        flag = ""
        if jac >= 0.4 and ap_ >= 0.8:
            flag = "<< COSTUME"
        elif jac >= 0.25 and ap_ >= 0.7:
            flag = "< overlap"
        print(f"  {a:<13}{b:<13}{nsh:>7}{jac:>7.2f}{ap_:>9.0%}{cstr:>9}{'  ' + flag}")

    # ── 4. independence ranking ────────────────────────────────────────────────
    # For each leg: fraction of its markets that NO other leg traded.
    all_other = {}
    for leg in names:
        others = set()
        for o in names:
            if o != leg:
                others |= msets[o]
        all_other[leg] = others

    indep = []
    for leg in names:
        own = msets[leg]
        unique = own - all_other[leg]
        frac = len(unique) / len(own) if own else 0.0
        # P&L on the UNIQUE markets — is the leg's edge in its own territory?
        upnl = sum(pmkt[leg].get(mid, 0.0) for mid in unique)
        tot  = sum(pmkt[leg].values())
        indep.append((frac, leg, len(own), len(unique), upnl, tot))

    indep.sort(reverse=True)
    print(f"\n  INDEPENDENCE RANKING  (unique-market coverage — keep the high ones)")
    print(f"  {'leg':<13}{'markets':>8}{'unique':>8}{'indep%':>8}{'uniq P&L':>10}{'tot P&L':>9}")
    print("  " + "-" * 64)
    for frac, leg, nown, nuniq, upnl, tot in indep:
        tag = ""
        if frac < 0.2:
            tag = "  << low-indep (likely redundant)"
        elif frac > 0.8:
            tag = "  << independent"
        print(f"  {leg:<13}{nown:>8}{nuniq:>8}{frac:>7.0%}{upnl:>+10.2f}{tot:>+9.2f}{tag}")

    print(f"\n  READING IT:")
    print(f"   • COSTUME pairs → the two legs are near-duplicates. Killing one")
    print(f"     loses no information and reduces best-of-N selection bias.")
    print(f"   • low-indep legs → mostly trade markets other legs already cover;")
    print(f"     their track record is NOT independent evidence of an edge.")
    print(f"   • high-indep + positive uniq P&L → the real candidates: an edge in")
    print(f"     territory no other leg is claiming. THESE survive DSR best.")
    print()


if __name__ == "__main__":
    main()
