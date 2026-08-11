#!/usr/bin/env python3
"""insider_cotrade.py — READ-ONLY study of COORDINATION among the watched insider
wallets. sybil_watch.py catches NEW fresh-wallet bursts in real time; this instead
asks the static question that sharpdrift depends on: are the ~112 watched "sharp"
wallets actually INDEPENDENT, or are some of them one entity / an info-sharing ring?

Method (co-trading overlap, the polymarket-insider-detection approach, simplified):
  - for each watched wallet, fetch its recent traded markets (set of conditionIds).
  - pairwise Jaccard overlap of market-sets. Independent sharps have diverse books;
    a high Jaccard (same markets, same sides) = likely coordination / sybil.
  - union-find clusters at JACCARD_MIN (with >=MIN_SHARED markets to avoid noise).
  - emit insider_clusters.json (wallet -> cluster_id) so sharpdrift can collapse
    ring members to ONE identity (true independence), + an Obsidian report.

PAPER ONLY, read-only data-api. Usage: python3 insider_cotrade.py
"""
import os, sys, json, time, urllib.request, urllib.parse, itertools
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
WATCHLIST = os.path.join(HERE, "insider_wallets.json")
CLUSTERS = os.path.join(HERE, "insider_clusters.json")
VAULT = os.path.expanduser("~/Documents/PolymarketVault/Reports/insider_cotrade.md")
UA = {"User-Agent": "Mozilla/5.0"}

JACCARD_MIN = 0.45        # portfolio-overlap threshold for a coordination edge
MIN_SHARED = 3            # ...and at least this many shared markets
SIDE_KEY = True           # treat (market, outcome) as the unit (same side = stronger)


def base_addr(a):
    return (a or "").lower().split("-")[0]


def wallet_markets(addr):
    """Set of (conditionId, outcomeIndex) the wallet recently traded."""
    try:
        u = "https://data-api.polymarket.com/activity?" + urllib.parse.urlencode({"user": addr, "limit": 100})
        rows = json.load(urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=15))
    except Exception:
        return set()
    out = set()
    for r in rows:
        if r.get("type") != "TRADE":
            continue
        cid = r.get("conditionId"); oi = r.get("outcomeIndex")
        if cid is None:
            continue
        out.add(f"{cid}|{oi}" if SIDE_KEY and oi is not None else str(cid))
    return out


def jaccard(a, b):
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / len(a | b), inter


class UF:
    def __init__(s, items): s.p = {x: x for x in items}
    def find(s, x):
        while s.p[x] != x: s.p[x] = s.p[s.p[x]]; x = s.p[x]
        return x
    def union(s, a, b): s.p[s.find(a)] = s.find(b)


def main():
    wl = json.load(open(WATCHLIST))
    # collapse address sybil-variants (the -timestamp suffix) up front
    wallets = sorted({base_addr(a): a for a in wl}.keys())
    print(f"studying co-trading among {len(wallets)} watched wallets…")
    mkts = {}
    with ThreadPoolExecutor(max_workers=12) as ex:
        futs = {ex.submit(wallet_markets, w): w for w in wallets}
        for f in as_completed(futs):
            mkts[futs[f]] = f.result()
    active = [w for w in wallets if mkts[w]]
    print(f"{len(active)} wallets have recent activity")

    uf = UF(active)
    edges = []
    for a, b in itertools.combinations(active, 2):
        j, inter = jaccard(mkts[a], mkts[b])
        if j >= JACCARD_MIN and inter >= MIN_SHARED:
            uf.union(a, b); edges.append((a, b, round(j, 2), inter))

    # build clusters
    groups = {}
    for w in active:
        groups.setdefault(uf.find(w), []).append(w)
    rings = {root: ws for root, ws in groups.items() if len(ws) >= 2}

    # emit wallet -> cluster_id (independents get their own id)
    cmap = {}
    for i, (root, ws) in enumerate(sorted(groups.items(), key=lambda kv: -len(kv[1]))):
        for w in ws:
            cmap[w] = f"C{i}" if len(ws) >= 2 else f"solo-{w[:8]}"
    json.dump(cmap, open(CLUSTERS, "w"), indent=2)

    # report
    L = ["# Insider Co-Trading Study — are the watched sharps independent?",
         "",
         "> READ-ONLY. Pairwise market-portfolio overlap among watched wallets. High overlap =",
         "> likely one entity / coordinated ring → sharpdrift must NOT count them as independent.",
         f"> Updated {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())} · {len(active)} active wallets · "
         f"{len(rings)} coordinated cluster(s) · {len([w for w in active if len(groups[uf.find(w)])==1])} independents",
         "", "## Coordinated clusters (collapse to ONE identity in any consensus signal)", ""]
    if rings:
        for root, ws in sorted(rings.items(), key=lambda kv: -len(kv[1])):
            labels = [wl.get(w, "") or wl.get(w + "?", "") for w in ws]
            L.append(f"### Cluster of {len(ws)} wallets")
            for w in ws:
                L.append(f"- `{w}`  {wl.get(w,'')[:40]}")
            # top shared edge
            L.append("")
    else:
        L.append("_No coordinated clusters found above threshold — the watched sharps look independent._")
    L += ["", "## Strongest co-trading pairs", "", "| jaccard | shared mkts | wallet A | wallet B |", "|--:|--:|---|---|"]
    for a, b, j, inter in sorted(edges, key=lambda e: -e[2])[:15]:
        L.append(f"| {j} | {inter} | `{a[:12]}…` | `{b[:12]}…` |")
    L += ["", "**Why it matters:** sharpdrift's signal is '≥2 INDEPENDENT sharps agree'. Two wallets in "
          "the same cluster agreeing is ONE opinion, not two — counting them as consensus manufactures a "
          "false edge. sharpdrift should read insider_clusters.json and require ≥2 distinct cluster_ids."]
    os.makedirs(os.path.dirname(VAULT), exist_ok=True)
    open(VAULT, "w").write("\n".join(L) + "\n")

    print(f"\n{len(rings)} coordinated cluster(s), {len(edges)} co-trading edges -> {CLUSTERS}")
    for root, ws in sorted(rings.items(), key=lambda kv: -len(kv[1]))[:6]:
        print(f"  RING ({len(ws)}): " + ", ".join(w[:10] for w in ws))


if __name__ == "__main__":
    main()
