#!/usr/bin/env python3
"""
factor_structure.py — the LINEAR-ALGEBRA honesty instrument.

The bot already corrects pseudo-replication WITHIN a leg (cluster-block bootstrap) and BY
hand-tagged theme (multiarb / analyst scorecard). This generalizes it: let the COVARIANCE
EIGENSTRUCTURE of the legs' daily P&L tell us how many INDEPENDENT bets the whole book really
is — no manual theme tags. Effective # of independent legs = participation ratio of the
correlation eigenvalues, PR = (Σλ)² / Σλ²  ∈ [1, p]:
  PR ≈ p   → legs are independent (n is real)
  PR ≈ 1   → every leg rides one factor (n is a lie; ~1 bet wearing p hats)

Reports PR, the dominant eigenvector (which legs ARE the common factor), and the top
correlated pairs. Read-only. NOT a new edge — a correction on how independent the book is.
"""
import json, os, re, sys
from collections import defaultdict, Counter
from datetime import datetime, timezone
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "scalp_lab_state.json")
BASKET = os.path.join(HERE, "basket_paper_book.json")
VAULT = os.path.expanduser("~/Documents/PolymarketVault/Reports/Factor Structure.md")

_THEMES = [("election", r"president|primary|governor|senate|election|nominee|parliament|prime minister|mayor|midterm|balance of power|\bseat\b|nomination"),
           ("sports", r"world cup|champion|super bowl|nba|nfl|mlb|ucl|premier|finals|playoff|cup|lck|grand slam|f1|grand prix|league"),
           ("crypto", r"bitcoin|ethereum|solana|btc|eth|crypto|token|coin"),
           ("macro", r"\bgdp\b|recession|rate|inflation|\bfed\b|unemploy|cpi|gold|oil"),
           ("geopolitics", r"war|ceasefire|hormuz|iran|israel|ukraine|russia|nato"),
           ("corp/M&A", r"acquisition|merger|acquire|ipo|ceo|earnings")]

def _theme(q):
    ql = (q or "").lower()
    for name, pat in _THEMES:
        if re.search(pat, ql):
            return name
    return "other"

def basket_theme_breakdown():
    """Theme-variation tracker: the open basket-lock theme mix (green-book-concentration watch).
    Folded into the daily factor job so the arb track's diversification is visibly tracked."""
    try:
        b = json.load(open(BASKET))
    except Exception:
        return None
    raw = b.get("open", [])
    items = list(raw.values()) if isinstance(raw, dict) else list(raw)
    if not items:
        return None
    def txt(r):
        if isinstance(r, dict):
            return r.get("title") or r.get("slug") or r.get("q") or ""
        return str(r)            # open holds id/slug strings
    c = Counter(_theme(txt(r)) for r in items)
    tot = sum(c.values())
    return tot, c
MIN_DAYS = 5     # a leg needs >=5 active days to enter the matrix (else noise)
MIN_LEGS = 3     # need >=3 legs for a meaningful eigenstructure

def daily_pnl_by_leg():
    s = json.load(open(STATE))
    out = {}
    for leg, book in s.items():
        if not isinstance(book, dict):
            continue
        by_day = defaultdict(float)
        for r in book.get("closed", []):
            p = r.get("pnl_usdc")
            d = (r.get("closed_at") or "")[:10]
            if p is not None and d:
                by_day[d] += p
        if len(by_day) >= MIN_DAYS:
            out[leg] = by_day
    return out

def main():
    legs = daily_pnl_by_leg()
    if len(legs) < MIN_LEGS:
        print(f"only {len(legs)} legs with >= {MIN_DAYS} active days — too sparse for factor structure")
        sys.exit(0)
    days = sorted({d for bd in legs.values() for d in bd})
    names = sorted(legs)
    # legs × days matrix; a day with no exit for a leg = 0 realized P&L that day
    M = np.array([[legs[n].get(d, 0.0) for d in days] for n in names])  # p × T
    # keep only legs with non-zero variance
    keep = [i for i in range(len(names)) if M[i].std() > 0]
    M, names = M[keep], [names[i] for i in keep]
    p, T = M.shape
    C = np.corrcoef(M)                      # p × p correlation
    C = np.nan_to_num(C)
    w = np.clip(np.linalg.eigvalsh(C), 0, None)
    PR = (w.sum() ** 2) / (np.square(w).sum() + 1e-12)   # effective # independent legs

    frac = w[-1] / w.sum()
    verdict = ('book is ~well-diversified' if PR > 0.7 * p
               else ('SEVERE concentration — ~%.0f real bets in %d legs' % (PR, p) if PR < 0.4 * p
                     else 'partial concentration'))
    vecs = np.linalg.eigh(C)[1]
    v1 = np.abs(vecs[:, -1])
    top = sorted(zip(names, v1), key=lambda x: -x[1])[:6]
    pairs = []
    for i in range(p):
        for j in range(i + 1, p):
            pairs.append((C[i, j], names[i], names[j]))
    pairs.sort(key=lambda x: -abs(x[0]))

    L = [f"=== FACTOR STRUCTURE — {p} legs over {T} active days ===",
         f"effective independent bets (participation ratio): {PR:.1f} of {p} legs",
         f"  → {verdict}",
         f"  top factor explains {frac*100:.0f}% of cross-leg variance",
         "  legs loading the common factor (move together):"]
    L += [f"    {n:14} loading={ld:.2f}" for n, ld in top]
    L += ["  most-correlated leg pairs (pseudo-replication / duplicate-leg risk):"]
    L += [f"    {a} ~ {b}: r={c:+.2f}" for c, a, b in pairs[:6]]

    bt = basket_theme_breakdown()
    if bt:
        tot, c = bt
        top = c.most_common(1)[0]
        L += ["", f"=== BASKET-ARB THEME MIX — {tot} open locks (concentration watch) ==="]
        L += [f"    {name:14} {n:3} ({100*n/tot:.0f}%)" for name, n in c.most_common()]
        L += [f"  → {'⚠️ CONCENTRATED in ' + top[0] if top[1]/tot > 0.5 else 'reasonably mixed'} "
              f"({100*top[1]/tot:.0f}% top theme)"]
    report = "\n".join(L)
    print(report)

    try:
        os.makedirs(os.path.dirname(VAULT), exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        open(VAULT, "w").write(
            f"---\ntype: dashboard\nstatus: live\n---\n\n# 📐 Factor Structure (effective-N)\n\n"
            f"*Linear-algebra honesty instrument · auto {ts} by factor_structure.py. "
            f"How many INDEPENDENT bets the book really is (eigenvalue participation ratio) — "
            f"the leg count overstates independence. Use the effective-N for book-wide significance; "
            f"retire near-duplicate legs (r>0.9).*\n\n```\n{report}\n```\n")
    except Exception as e:
        print(f"vault write failed: {e}", file=sys.stderr)
    sys.exit(0)

if __name__ == "__main__":
    main()
