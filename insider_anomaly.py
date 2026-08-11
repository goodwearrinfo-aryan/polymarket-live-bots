#!/usr/bin/env python3
"""insider_anomaly.py — READ-ONLY unsupervised insider discovery (the
polymarket-insider-detection anomaly_scan method, pragmatic port).

whale_screen.py finds insiders by THRESHOLD (P&L≥$100k, avg_bet≥$500) — it misses
informed wallets with modest P&L but insider-LIKE behaviour. This instead scores
each harvested wallet by how BEHAVIOURALLY anomalous it is (IsolationForest over
~7 features) AND how well it matches the INFORMED archetype (high WR, concentrated,
non-coinflip, positive P&L) — surfacing candidates the threshold scan can't see.

Pipeline: harvested pool (goldsky+onchain, fills≥MIN_FILLS, not already watched)
-> data-api /positions features -> RobustScaler -> IsolationForest anomaly score
-> keep INFORMED-direction anomalies -> rank -> insider_anomaly_candidates.json
+ Obsidian. PAPER ONLY, read-only. Usage: python3 insider_anomaly.py [--max N]
"""
import os, sys, json, math, argparse, urllib.request, urllib.parse, collections
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
WATCHLIST = os.path.join(HERE, "insider_wallets.json")
OUT = os.path.join(HERE, "insider_anomaly_candidates.json")
VAULT = os.path.expanduser("~/Documents/PolymarketVault/Reports/insider_anomaly.md")
UA = {"User-Agent": "Mozilla/5.0"}
MIN_FILLS = 25
DEFAULT_MAX = 500          # cap data-api calls per run


def _get(path, params):
    u = f"https://data-api.polymarket.com/{path}?" + urllib.parse.urlencode(params)
    return json.loads(urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=15).read())


def features(addr):
    """Behavioural feature vector from a wallet's positions, or None."""
    try:
        pos = _get("positions", {"user": addr, "limit": 500, "sizeThreshold": 0})
    except Exception:
        return None
    if not pos or len(pos) < 5:
        return None
    closed = [p for p in pos if p.get("redeemable") or p.get("curPrice", 0.5) in (0.0, 1.0)]
    if len(closed) < 5:
        return None
    wins = sum(1 for p in closed if p.get("realizedPnl", 0) > 0)
    wr = wins / len(closed)
    pnl = sum(p.get("realizedPnl", 0) for p in pos)
    wagered = sum(p.get("totalBought", 0) for p in pos)
    avg_bet = wagered / len(pos)
    cf = sum(1 for p in pos if "up or down" in (p.get("title") or "").lower()) / len(pos)
    # concentration: Herfindahl of $ across markets (1=one market, ~0=spread thin)
    sizes = [p.get("totalBought", 0) for p in pos if p.get("totalBought", 0) > 0]
    tot = sum(sizes) or 1
    herf = sum((s / tot) ** 2 for s in sizes)
    # avg entry price on WINNERS (insiders buy cheap longshots that hit -> low entry)
    win_entries = [p.get("avgPrice", 0.5) for p in closed if p.get("realizedPnl", 0) > 0 and p.get("avgPrice")]
    avg_win_entry = sum(win_entries) / len(win_entries) if win_entries else 0.5
    return {"addr": addr, "wr": wr, "pnl": pnl, "avg_bet": avg_bet, "coinflip": cf,
            "n_closed": len(closed), "herf": herf, "avg_win_entry": avg_win_entry, "wagered": wagered}


def candidate_pool(maxn):
    have = {k.lower() for k in json.load(open(WATCHLIST))} if os.path.exists(WATCHLIST) else set()
    fills = {}
    for sp in ("goldsky_sample_wallets_state.json", "goldsky_wallets_state.json", "onchain_wallets_state.json"):
        p = os.path.join(HERE, sp)
        if not os.path.exists(p):
            continue
        for a, w in json.load(open(p)).get("wallets", {}).items():
            fills[a] = max(fills.get(a, 0), w.get("fills", 0) if isinstance(w, dict) else 1)
    pool = [a for a, n in sorted(fills.items(), key=lambda kv: -kv[1]) if n >= MIN_FILLS and a not in have]
    return pool[:maxn]


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--max", type=int, default=DEFAULT_MAX); a = ap.parse_args()
    import numpy as np
    from sklearn.ensemble import IsolationForest
    from sklearn.preprocessing import RobustScaler

    pool = candidate_pool(a.max)
    print(f"scanning {len(pool)} harvested wallets (fills>={MIN_FILLS}, not watched) for insider-like anomalies…")
    feats = []
    with ThreadPoolExecutor(max_workers=16) as ex:
        for f in as_completed({ex.submit(features, w) for w in pool}):
            r = f.result()
            if r:
                feats.append(r)
    print(f"{len(feats)} wallets with usable position history")
    if len(feats) < 20:
        print("too few for anomaly detection"); return

    cols = ["wr", "pnl", "avg_bet", "coinflip", "n_closed", "herf", "avg_win_entry"]
    X = np.array([[math.log1p(abs(r[c])) * (1 if r[c] >= 0 else -1) if c in ("pnl", "avg_bet", "n_closed")
                   else r[c] for c in cols] for r in feats], dtype=float)
    Xs = RobustScaler().fit_transform(X)
    iso = IsolationForest(n_estimators=200, contamination="auto", random_state=42)
    iso.fit(Xs)
    anom = -iso.score_samples(Xs)                      # higher = more anomalous

    # direct toward the INFORMED archetype: high WR, low coinflip, +pnl, concentrated
    for i, r in enumerate(feats):
        r["anomaly"] = round(float(anom[i]), 3)
        informed = (r["wr"] >= 0.5 and r["coinflip"] < 0.3 and r["pnl"] > 0)
        # archetype score: reward WR, concentration, conviction; penalise coinflip
        r["insider_score"] = round((r["wr"] * 2 + r["herf"] + min(r["pnl"], 50000) / 50000
                                     - r["coinflip"] * 3 + (0.5 - min(r["avg_win_entry"], 0.5))) * r["anomaly"], 3) if informed else -1

    cand = sorted([r for r in feats if r["insider_score"] > 0], key=lambda r: -r["insider_score"])[:25]
    json.dump([{k: r[k] for k in ("addr", "insider_score", "anomaly", "wr", "pnl", "avg_bet", "coinflip", "herf", "n_closed")}
               for r in cand], open(OUT, "w"), indent=2)

    L = ["# Insider Anomaly Scan — informed wallets the threshold scan misses",
         "",
         "> READ-ONLY unsupervised (IsolationForest over behavioural features), directed toward the",
         "> INFORMED archetype (high WR · concentrated · non-coinflip · +P&L). Candidates to vet,",
         "> NOT confirmed insiders — anomalous ≠ informed until validated.",
         f"> {len(feats)} scanned · {len(cand)} insider-direction candidates", "",
         "| insider_score | anomaly | WR | P&L | avg_bet | concentr | wallet |",
         "|--:|--:|--:|--:|--:|--:|---|"]
    for r in cand[:20]:
        L.append(f"| {r['insider_score']} | {r['anomaly']} | {r['wr']*100:.0f}% | ${r['pnl']:,.0f} | "
                 f"${r['avg_bet']:,.0f} | {r['herf']:.2f} | `{r['addr']}` |")
    L += ["", "**Next:** vet the top few with `account_flag.py <addr>`; add genuine informed "
          "wallets to insider_wallets.json (then insider_cotrade re-checks their independence)."]
    os.makedirs(os.path.dirname(VAULT), exist_ok=True)
    open(VAULT, "w").write("\n".join(L) + "\n")
    print(f"\n{len(cand)} insider-direction candidates -> {OUT}")
    for r in cand[:8]:
        print(f"  score={r['insider_score']:.2f} anom={r['anomaly']:.2f} WR={r['wr']*100:.0f}% "
              f"pnl=${r['pnl']:,.0f} cf={r['coinflip']:.0%}  {r['addr']}")


if __name__ == "__main__":
    main()
