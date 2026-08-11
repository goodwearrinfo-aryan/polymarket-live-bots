#!/usr/bin/env python3
"""whale_screen.py — screen on-chain harvested wallets for the event-market
whale archetype (like 0xbd04...bb0: big P&L, big bets, slow event markets —
NOT crypto Up/Down coinflip HFT, which is non-copyable at our 60s lag).

Two-stage, data-api only (read-only):
  1. positions screen: realized_pnl >= --min-pnl AND avg_bet >= --min-bet
  2. style screen on survivors: last 100 trades; drop wallets where >50%%
     of trades are "Up or Down" coinflip markets

Output: whale_candidates.json ranked by P&L (excludes insider_wallets.json).

Usage: python3 whale_screen.py --target 20
"""
import os, sys, json, time, argparse, urllib.request, urllib.parse, collections
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "onchain_wallets_state.json")
WATCHLIST = os.path.join(HERE, "insider_wallets.json")
OUT = os.path.join(HERE, "whale_candidates.json")            # canonical research set (all whales, merge source)
ACTIVE_OUT = os.path.join(HERE, "whale_candidates_active.json")  # default gated live-view (dormant dropped)
UA = {"User-Agent": "Mozilla/5.0"}


DONE_F = os.path.join(HERE, "whale_screen_done.txt")

def _get(path, params, tries=5):
    u = f"https://data-api.polymarket.com/{path}?" + urllib.parse.urlencode(params)
    for a in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=15) as r:
                return json.loads(r.read())
        except Exception:
            if a == tries - 1:
                raise
            time.sleep(2 * (a + 1))


def positions_screen(addr, min_pnl, min_bet, min_closed=5):
    try:
        pos = _get("positions", {"user": addr, "limit": 500, "sizeThreshold": 0})
        if not pos:
            return None
        closed = [p for p in pos if p.get("redeemable") or p.get("curPrice", 0.5) in (0.0, 1.0)]
        if len(closed) < min_closed:
            return None
        pnl = sum(p.get("realizedPnl", 0) for p in pos)
        wagered = sum(p.get("totalBought", 0) for p in pos)
        avg_bet = wagered / len(pos)
        if pnl < min_pnl or avg_bet < min_bet:
            return None
        wins = sum(1 for p in closed if p.get("realizedPnl", 0) > 0)
        return {"addr": addr, "realized_pnl": round(pnl, 2), "avg_bet": round(avg_bet, 2),
                "win_rate": round(100 * wins / len(closed), 1), "closed": len(closed),
                "open": len(pos) - len(closed), "wagered": round(wagered, 2)}
    except Exception:
        return None


def style_screen(cand, max_dormant_days=None):
    """Reject coinflip HFT and (optionally) dormant wallets; annotate mix + recency.
    A whale that stopped trading is worthless to a live copy-watcher no matter
    how big its P&L — max_dormant_days drops those."""
    try:
        ts = _get("trades", {"user": cand["addr"], "limit": 100})
        if not ts:
            return None
        coinflip = sum(1 for t in ts if "up or down" in (t.get("title") or "").lower())
        if coinflip / len(ts) > 0.5:
            return None
        last_days = round((time.time() - max(int(t["timestamp"]) for t in ts)) / 86400, 1)
        if max_dormant_days is not None and last_days > max_dormant_days:
            return None
        titles = collections.Counter((t.get("title") or "")[:50] for t in ts)
        cand["coinflip_share"] = round(coinflip / len(ts), 2)
        cand["last_trade_days"] = last_days
        cand["top_markets"] = [t for t, _ in titles.most_common(3)]
        return cand
    except Exception:
        return None


def _load_pools(min_fills):
    """Union every *_wallets_state.json pool (V2 RPC + Goldsky V1), gating on
    cumulative fills across pools so low-activity noise never hits data-api."""
    import glob
    fills = {}
    for sp in glob.glob(os.path.join(HERE, "*_wallets_state.json")):
        try:
            for a, w in json.load(open(sp))["wallets"].items():
                fills[a] = fills.get(a, 0) + (w.get("fills", 1) if isinstance(w, dict) else 1)
        except Exception:
            continue
    return [a for a, n in fills.items() if n >= min_fills]


def main(target, min_pnl, min_bet, workers, min_fills=1, max_dormant_days=None, out=OUT):
    wallets = _load_pools(min_fills)
    known = {k.lower() for k in json.load(open(WATCHLIST))} if os.path.exists(WATCHLIST) else set()
    done = set(open(DONE_F).read().split()) if os.path.exists(DONE_F) else set()
    todo = [w for w in wallets if w not in known and w not in done]
    print(f"screening {len(todo)} harvested wallets for whales (pnl>=${min_pnl:,}, avg_bet>=${min_bet:,})…")
    stage1, n = [], 0
    dfh = open(DONE_F, "a")
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(positions_screen, w, min_pnl, min_bet): w for w in todo}
        for f in as_completed(futs):
            n += 1
            dfh.write(futs[f] + "\n")
            r = f.result()
            if r:
                stage1.append(r)
                print(f"  stage1 hit: {r['addr']}  PnL=${r['realized_pnl']:,.0f}  avgbet=${r['avg_bet']:,.0f}  WR={r['win_rate']}%", flush=True)
            if n % 1000 == 0:
                dfh.flush()
                print(f"  …{n}/{len(todo)}  ({len(stage1)} stage-1 hits)", flush=True)
    dfh.close()
    print(f"stage 1: {len(stage1)} wallets pass size screen; checking style (coinflip filter)…")
    whales = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for f in as_completed({ex.submit(style_screen, c, max_dormant_days) for c in stage1}):
            r = f.result()
            if r:
                whales.append(r)
    if os.path.exists(OUT):                       # merge with prior runs
        try:
            prev = json.load(open(OUT))
            have = {w["addr"] for w in whales}
            for w in prev:
                if not isinstance(w, dict) or w.get("addr") in have:
                    continue
                # apply the same recency gate to merged-in prior whales
                if max_dormant_days is not None and w.get("last_trade_days", 0) > max_dormant_days:
                    continue
                whales.append(w)
        except Exception:
            pass
    whales.sort(key=lambda r: -r["realized_pnl"])
    whales = whales[:target] if target else whales
    with open(out, "w") as f:
        json.dump(whales, f, indent=2)
    print(f"\n{'='*100}")
    for r in whales:
        print(f"  ${r['realized_pnl']:>10,.0f}  WR={r['win_rate']:>5.1f}%  bet=${r['avg_bet']:>8,.0f}  "
              f"open={r['open']:>3}  cf={r['coinflip_share']:.0%}  {r['addr']}  | {r['top_markets'][0][:40]}")
    print(f"{'='*100}\n{len(whales)} event-market whales -> {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=int, default=20)
    ap.add_argument("--min-pnl", type=float, default=10000)
    ap.add_argument("--min-bet", type=float, default=500)
    ap.add_argument("--workers", type=int, default=20)
    ap.add_argument("--min-fills", type=int, default=1, help="min cumulative on-chain fills before screening")
    ap.add_argument("--max-dormant-days", type=float, default=60,
                    help="drop whales idle longer than this (default 60; 0/None disables). "
                         "Note: resolves-without-new-trades can falsely age a live whale, so keep it loose.")
    ap.add_argument("--out", default=ACTIVE_OUT,
                    help="output file (default: whale_candidates_active.json — the gated live view). "
                         "Pass --out whale_candidates.json --max-dormant-days 0 for a full research run.")
    a = ap.parse_args()
    md = a.max_dormant_days if a.max_dormant_days and a.max_dormant_days > 0 else None
    main(a.target, a.min_pnl, a.min_bet, a.workers, a.min_fills, md, a.out)
