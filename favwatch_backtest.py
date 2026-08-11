#!/usr/bin/env python3
"""favwatch_backtest.py — BACKTEST the favwatch question on HISTORY (via oddpool
free historical top-of-book), instead of waiting days for favorites to resolve.

Question (favwatch): do heavy favorites [0.85,0.97] near resolution PAY when bought,
AFTER the gap-through losses + the spread you actually pay? buy-favorite P&L =
settle(1/0) - entry_ASK. The gap is captured when a favorite resolves to $0.

Method: recently-RESOLVED binary markets (gamma) -> oddpool top-of-book history ->
find the favorite token's first snapshot with mid in [0.85,0.97], take its best_ASK
as the realized entry (NOT the mid — spread is the whole point) -> settle from the
on-chain/gamma resolution -> aggregate mean P&L, win-rate vs implied, loss-gap tail.

PAPER ONLY, read-only. Usage: python3 favwatch_backtest.py [--n 25] [--gran 5m]
"""
import os, sys, json, time, argparse, urllib.request, urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import oddpool_history as oh
UA = {"User-Agent": "Mozilla/5.0"}
FAV_LO, FAV_HI = 0.85, 0.97


def _g(url, timeout=15):
    return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout))


def resolved_binaries(n):
    """Recently-closed binary markets with a clean 1/0 resolution."""
    ms = _g("https://gamma-api.polymarket.com/markets?" + urllib.parse.urlencode(
        {"closed": "true", "limit": 500, "order": "volume", "ascending": "false"}))
    out = []
    for m in ms:
        op = m.get("outcomePrices"); op = json.loads(op) if isinstance(op, str) else op
        toks = m.get("clobTokenIds"); toks = json.loads(toks) if isinstance(toks, str) else toks
        if not op or len(op) != 2 or not toks or len(toks) != 2:
            continue
        if set(op) != {"0", "1"}:                  # must be cleanly resolved
            continue
        if float(m.get("volume") or 0) < 5_000:
            continue
        out.append({"cid": m.get("conditionId"), "toks": [str(t) for t in toks],
                    "win_idx": 0 if op[0] == "1" else 1, "q": m.get("question")})
        if len(out) >= n:
            break
    return out


def backtest_one(mk, gran):
    """Return (entry_ask, settle, won, spread) for the favorite, or None."""
    try:
        by = oh.series_by_token(mk["cid"], gran, pages=1)
    except Exception:
        return None
    if not by:
        return None
    # which token is the favorite? the one whose mid entered [0.85,0.97]
    for asset_id, snaps in by.items():
        entry = next((s for s in snaps if s.get("mid") is not None and s.get("best_ask") is not None
                      and FAV_LO <= s["mid"] <= FAV_HI), None)
        if not entry:
            continue
        # map this token to its outcome index
        try:
            idx = mk["toks"].index(str(asset_id))
        except ValueError:
            continue
        won = (idx == mk["win_idx"])
        settle = 1.0 if won else 0.0
        ask = float(entry["best_ask"])
        return {"entry_ask": ask, "settle": settle, "won": won,
                "pnl": round(settle - ask, 4), "spread": float(entry.get("spread", 0)), "q": mk["q"]}
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=25)
    ap.add_argument("--gran", default="5m")
    a = ap.parse_args()
    mkts = resolved_binaries(a.n)
    print(f"backtesting favwatch on {len(mkts)} resolved binary markets (oddpool history)…")
    res = []
    for i, mk in enumerate(mkts):
        r = backtest_one(mk, a.gran)
        if r:
            res.append(r)
            tag = "WON " if r["won"] else "LOST"
            print(f"  [{i+1}] {tag} entry_ask={r['entry_ask']:.3f} pnl={r['pnl']:+.3f}  {(r['q'] or '')[:40]}")
        time.sleep(1.2)                            # be gentle on the free tier
    if not res:
        print("\nno favorite-band entries found in history (markets may lack oddpool coverage)"); return
    n = len(res)
    wins = sum(1 for r in res if r["won"])
    pnl = [r["pnl"] for r in res]
    losses = [r["pnl"] for r in res if not r["won"]]
    mean_entry = sum(r["entry_ask"] for r in res) / n
    print(f"\n{'='*64}\nFAVWATCH BACKTEST  (n={n}, buy favorite at the ASK, settle 1/0)")
    print(f"  favorite WIN rate : {100*wins/n:.1f}%   vs implied (mean entry ask) {100*mean_entry:.1f}%")
    print(f"  mean P&L / exit   : {100*sum(pnl)/n:+.2f}¢   (THE number — must beat 0 net of the ask)")
    if losses:
        print(f"  mean loss gap     : {100*sum(losses)/len(losses):+.2f}¢  ({len(losses)} favorites cratered)")
    print(f"  mean spread paid  : {100*sum(r['spread'] for r in res)/n:.2f}¢")
    edge = sum(pnl)/n
    print(f"\n  VERDICT: {'POSITIVE — worth forward-testing' if edge>0 else 'NEGATIVE — favorites do NOT pay net of the ask (the gap eats the win rate, exactly the nearres lesson)'}")
    json.dump(res, open(os.path.join(HERE, "favwatch_backtest_results.json"), "w"), indent=2)


if __name__ == "__main__":
    main()
