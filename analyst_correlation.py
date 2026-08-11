#!/usr/bin/env python3
"""
analyst_correlation.py — DETERMINISTIC correlation/concentration audit of the analyst
paper book. No LLM, no API, zero cost (pure Python). Does the pm-correlation-auditor's
job in code: tags each position by its underlying DRIVER, finds clusters that secretly
ride the same factor, and reports the EFFECTIVE independent sample size (so N correlated
NOs don't masquerade as N independent edges and inflate a bootstrap CI).

The lesson: bootstrap CIs assume independent draws. If 3 positions all lose together when
one thing happens (a fast Mideast peace), they are ~1 bet wearing 3 hats — pseudo-replication.

Run: python3 analyst_correlation.py
"""
import os, json

BASE = os.path.dirname(os.path.abspath(__file__))
BOOK = os.path.join(BASE, "analyst_positions.json")

# Driver tags: (keyword-in-question, driver, "what makes this side LOSE")
# Each position gets a driver + the macro event that would move it against us.
DRIVERS = [
    (["hormuz", "strait"],          "mideast_peace_tempo", "fast comprehensive Iran/Mideast reopening+peace"),
    (["enrichment", "uranium", "nuclear"], "mideast_peace_tempo", "fast comprehensive Iran nuclear deal"),
    (["hezbollah", "israel"],       "mideast_peace_tempo", "fast comprehensive Israel/Hezbollah peace"),
    (["iran", "ceasefire"],         "mideast_peace_tempo", "Iran diplomacy stalls (this is the YES leg — anti-correlated)"),
    (["bitcoin", "btc", "ethereum", "eth", "crypto"], "crypto_price", "crypto price move"),
    (["brazil", "election", "president", "santos", "lula", "bolsonaro"], "election", "election surprise"),
]


def driver_of(q):
    ql = q.lower()
    for kws, drv, lose in DRIVERS:
        if any(k in ql for k in kws):
            return drv, lose
    return "idiosyncratic", "market-specific"


def _settled_pnls(positions):
    """[(driver, realized_pnl)] for settled positions that have a realized P&L."""
    out = []
    for p in positions:
        if p.get("status") == "settled" and p.get("realized_pnl") is not None:
            drv, _ = driver_of(p.get("q", ""))
            out.append((drv, float(p["realized_pnl"])))
    return out


def cluster_block_bootstrap_ci(positions, B=10000, lo=5, hi=95, seed=12345):
    """Honest CI over settled analyst positions' realized P&L.

    Computes TWO bootstrap CIs so the pseudo-replication inflation is visible:
      - naive    : resample positions (rows) i.i.d. — assumes independence, too tight
                   when positions secretly share a driver.
      - cluster  : resample DRIVER CLUSTERS *whole* (block bootstrap) — preserves
                   within-cluster correlation, so correlated NOs count as ~one draw.

    The block bootstrap is the honest one for the graduation gate. With a SINGLE
    cluster it is degenerate (no between-cluster variation to resample) — that is
    correct: an all-one-driver book has ~1 independent draw and no valid CI.
    """
    import random
    data = _settled_pnls(positions)
    n = len(data)
    if n == 0:
        return {"n_settled": 0, "note": "no settled positions yet — CI not computable"}

    rnd = random.Random(seed)
    pnls = [v for _, v in data]
    mean = sum(pnls) / n

    def _pct(s, q):
        return s[min(len(s) - 1, max(0, int(q / 100 * len(s))))]

    # naive row bootstrap
    naive = sorted(sum(pnls[rnd.randrange(n)] for _ in range(n)) / n for _ in range(B))
    naive_ci = [round(_pct(naive, lo), 4), round(_pct(naive, hi), 4)]
    naive_w = round(naive_ci[1] - naive_ci[0], 4)

    # cluster-block bootstrap: resample whole driver clusters
    clusters = {}
    for drv, v in data:
        clusters.setdefault(drv, []).append(v)
    blocks = list(clusters.values())
    k = len(blocks)
    cluster_dist = []
    for _ in range(B):
        drawn = [blocks[rnd.randrange(k)] for _ in range(k)]
        pooled = [v for blk in drawn for v in blk]
        cluster_dist.append(sum(pooled) / len(pooled))
    cluster_dist.sort()
    cluster_ci = [round(_pct(cluster_dist, lo), 4), round(_pct(cluster_dist, hi), 4)]
    cluster_w = round(cluster_ci[1] - cluster_ci[0], 4)

    res = {
        "n_settled": n, "n_clusters": k, "mean_pnl": round(mean, 4),
        "naive_ci": naive_ci, "naive_width": naive_w,
        "cluster_ci": cluster_ci, "cluster_width": cluster_w,
        "ci_positive_naive": naive_ci[0] > 0,
        "ci_positive_cluster": cluster_ci[0] > 0,
    }
    if k < 2 or cluster_w == 0:
        res["effective_n"] = float(k)
        res["degenerate"] = True
        res["note"] = (f"single driver cluster — block bootstrap degenerate; "
                       f"effectively ~{k} independent draw(s), no valid CI")
    else:
        res["effective_n"] = round(n * (naive_w / cluster_w) ** 2, 1) if cluster_w else float(k)
        res["inflation"] = round(cluster_w / naive_w, 2) if naive_w else None
        res["degenerate"] = False
    return res


def main():
    book = json.load(open(BOOK))
    pos = book.get("positions", [])
    print(f"\n=== ANALYST BOOK CORRELATION AUDIT — {len(pos)} positions ===\n")

    clusters = {}
    for p in pos:
        q = p.get("q", "")
        drv, lose = driver_of(q)
        clusters.setdefault(drv, []).append((p.get("side"), q, lose))

    eff_n = 0.0
    for drv, items in sorted(clusters.items(), key=lambda x: -len(x[1])):
        # within a shared driver, same-side positions are positively correlated
        sides = [s for s, _, _ in items]
        n = len(items)
        # effective independent bets in a same-driver cluster: ~1 + 0.4*(extra same-side),
        # i.e. heavy discount for correlated repeats (rho~0.6 assumed within a driver).
        same = max(sides.count("NO"), sides.count("YES"))
        opp = n - same
        eff = (1 + 0.4 * (same - 1)) + opp * 0.6   # opposite-side legs are partial hedges
        eff_n += eff
        flag = "  ⚠️ CONCENTRATED" if n >= 3 else ""
        print(f"[{drv}]  {n} positions → ~{eff:.1f} effective bets{flag}")
        for side, q, lose in items:
            print(f"     {side:3} | {q[:54]}")
        if n >= 2:
            print(f"     ↳ shared risk: all {drv} positions move on '{items[0][2]}'")
        print()

    print(f"NAIVE sample size (if treated independent): {len(pos)}")
    print(f"EFFECTIVE independent sample size:          ~{eff_n:.1f}")
    print(f"Pseudo-replication factor:                  {len(pos)/eff_n:.2f}x "
          f"(a bootstrap CI over these is ~{(len(pos)/eff_n)**0.5:.2f}x too tight)")

    # dominant-factor tail
    biggest = max(clusters.items(), key=lambda x: len(x[1]))
    if len(biggest[1]) >= 2:
        nos = [q for s, q, _ in biggest[1] if s == "NO"]
        print(f"\nDOMINANT FACTOR: '{biggest[0]}' ({len(biggest[1])} of {len(pos)} positions).")
        if len(nos) >= 2:
            print(f"  TAIL RISK: {len(nos)} NO positions lose TOGETHER if '{biggest[1][0][2]}' happens.")
            print(f"  These are NOT independent edges — size them as ~one correlated thesis.")

    # Real cluster-block bootstrap over SETTLED positions (the honest gate CI)
    bs = cluster_block_bootstrap_ci(pos)
    print(f"\n=== CLUSTER-BLOCK BOOTSTRAP (settled positions only) ===")
    if bs["n_settled"] == 0:
        print(f"  {bs['note']}")
    else:
        print(f"  settled n={bs['n_settled']} across {bs['n_clusters']} cluster(s), "
              f"mean P&L ${bs['mean_pnl']:+.4f}")
        print(f"  naive   CI [{bs['naive_ci'][0]:+.4f}, {bs['naive_ci'][1]:+.4f}]  "
              f"(width {bs['naive_width']}, CI>0={bs['ci_positive_naive']})")
        if bs.get("degenerate"):
            print(f"  cluster CI — {bs['note']}")
        else:
            print(f"  cluster CI [{bs['cluster_ci'][0]:+.4f}, {bs['cluster_ci'][1]:+.4f}]  "
                  f"(width {bs['cluster_width']}, CI>0={bs['ci_positive_cluster']})")
            print(f"  → cluster CI is {bs['inflation']}x wider; effective n≈{bs['effective_n']} "
                  f"vs {bs['n_settled']} nominal")


if __name__ == "__main__":
    main()
