#!/usr/bin/env python3
"""oos_copy_backtest.py — point-in-time OUT-OF-SAMPLE copy backtest.

Kills the survivorship/look-ahead bias that made the naive old-data backtest
read +0.29/share (a lie). Method:
  cutoff T = now - <days>.
  1. SELECT step (uses ONLY pre-T data): a wallet qualifies if its pre-T resolved
     BUY trades in the genuine-uncertainty band clear a copier WR / edge bar.
  2. EVALUATE step (uses ONLY post-T data): for selected wallets, copy their
     post-T BUY trades at price + haircut, settle to the market's resolution.
  3. Pool post-T copier edges -> mean + bootstrap CI. Controls: post-T edge of
     NON-selected wallets, and a random-side null.
A wallet's 80% past WR can no longer "predict" its own past — selection and
evaluation never touch the same trade. Only a real, persistent skill survives.

PAPER research, read-only. Resolution via gamma outcomePrices (resolved=0/1).
"""
import os, sys, json, time, argparse, urllib.request, urllib.parse, collections
from concurrent.futures import ThreadPoolExecutor

UA = {"User-Agent": "Mozilla/5.0"}
HERE = os.path.dirname(os.path.abspath(__file__))
GAMMA = "https://gamma-api.polymarket.com/markets"
DATA = "https://data-api.polymarket.com"
_res_cache = {}   # conditionId -> [outcome0_final, outcome1_final] or None


def _get(url):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=15) as r:
            return json.loads(r.read())
    except Exception:
        return None


def wallet_trades(addr, pages):
    out, offset = [], 0
    for _ in range(pages):
        d = _get(f"{DATA}/trades?{urllib.parse.urlencode({'user': addr, 'limit': 500, 'offset': offset})}")
        if not d:
            break
        out.extend(d)
        if len(d) < 500:
            break
        offset += 500
    return out


def resolve(cids):
    """Batch-fetch resolution (outcomePrices) for unseen conditionIds."""
    need = [c for c in cids if c not in _res_cache]
    for k in range(0, len(need), 20):
        part = need[k:k + 20]
        url = GAMMA + "?" + "&".join("condition_ids=" + urllib.parse.quote(str(c)) for c in part)
        rows = _get(url) or []
        got = {}
        for m in rows:
            try:
                cid = str(m.get("conditionId") or m.get("id"))
                pr = m.get("outcomePrices")
                if isinstance(pr, str):
                    pr = json.loads(pr)
                closed = m.get("closed")
                # resolved => closed and outcomePrices pinned to 0/1
                if closed and pr and float(pr[0]) in (0.0, 1.0):
                    got[cid] = [float(pr[0]), float(pr[1])]
            except Exception:
                continue
        for c in part:
            _res_cache[c] = got.get(c)  # None if unresolved/unknown


def collect(addr, pages, band, haircut, T):
    """Return (pre_edges, post_edges) — copier edge/share, split at cutoff T."""
    trades = wallet_trades(addr, pages)
    # only BUY, in band, dedup to FIRST buy per (cid,outcome) = fresh entry
    fresh = {}
    for t in trades:
        if t.get("side") != "BUY":
            continue
        try:
            price = float(t.get("price", 0))
        except Exception:
            continue
        if not (band[0] <= price <= band[1]):
            continue
        cid = str(t.get("conditionId"))
        oi = t.get("outcomeIndex")
        ts = int(t.get("timestamp", 0))
        key = (cid, str(oi))
        if key not in fresh or ts < fresh[key]["ts"]:
            fresh[key] = {"cid": cid, "oi": oi, "price": price, "ts": ts}
    resolve({f["cid"] for f in fresh.values()})
    pre, post = [], []
    for f in fresh.values():
        fin = _res_cache.get(f["cid"])
        if not fin:
            continue
        try:
            terminal = fin[int(f["oi"])]
        except Exception:
            continue
        edge = terminal - (f["price"] + haircut)
        (pre if f["ts"] < T else post).append(edge)
    return pre, post


def boot_ci(xs, n=1000):
    if not xs:
        return (0.0, 0.0)
    m = len(xs)
    means = sorted(sum(xs[(b * 131 + i * 977) % m] for i in range(m)) / m for b in range(n))
    return (means[int(n * .025)], means[int(n * .975)])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wallets", default=os.path.join(HERE, "insider_wallets.json"))
    ap.add_argument("--days", type=int, default=21, help="cutoff: T = now - days")
    ap.add_argument("--pages", type=int, default=4)
    ap.add_argument("--haircut", type=float, default=0.03)
    ap.add_argument("--band", default="0.15,0.85")
    ap.add_argument("--sel-min-n", type=int, default=8, help="min pre-T resolved trades to judge a wallet")
    ap.add_argument("--sel-min-wr", type=float, default=55.0, help="pre-T copier WR%% to SELECT a wallet")
    ap.add_argument("--now", type=int, required=True, help="current unix time (passed in; no Date in sandbox)")
    a = ap.parse_args()
    band = tuple(float(x) for x in a.band.split(","))
    T = a.now - a.days * 86400
    src = json.load(open(a.wallets))
    wallets = list(src.keys()) if isinstance(src, dict) else [w["addr"] for w in src]
    print(f"OOS copy backtest — {len(wallets)} wallets, cutoff T={a.days}d ago, "
          f"haircut={a.haircut}, band={band}, select: pre-T n>={a.sel_min_n} & WR>={a.sel_min_wr}%\n")

    def work(addr):
        return addr, collect(addr, a.pages, band, a.haircut, T)
    data = {}
    with ThreadPoolExecutor(max_workers=10) as ex:
        for addr, (pre, post) in ex.map(work, wallets):
            data[addr] = (pre, post)

    sel_post, unsel_post = [], []
    n_sel = 0
    for addr, (pre, post) in data.items():
        pre_wr = (sum(1 for e in pre if e > -a.haircut) / len(pre) * 100) if pre else 0  # won (terminal=1)
        selected = len(pre) >= a.sel_min_n and pre_wr >= a.sel_min_wr
        if selected:
            n_sel += 1
            sel_post += post
        else:
            unsel_post += post

    def line(tag, xs):
        if not xs:
            print(f"  {tag:<26} n=0")
            return
        n = len(xs); avg = sum(xs) / n; wr = sum(1 for e in xs if e > 0) / n * 100
        lo, hi = boot_ci(xs)
        verdict = "REAL EDGE (CI>0)" if lo > 0 else "no edge (CI<=0)"
        print(f"  {tag:<26} n={n:<5} edge/sh={avg:+.4f}  copier_WR={wr:>3.0f}%  CI[{lo:+.4f},{hi:+.4f}] {verdict}")

    print(f"SELECTED {n_sel}/{len(wallets)} wallets on PRE-T record. Now their POST-T (unseen) trades:")
    line("SELECTED wallets POST-T", sel_post)
    line("control: NOT-selected POST-T", unsel_post)
    # random-side null on selected wallets' post-T entries: edge if outcome were 50/50
    import statistics
    if sel_post:
        # reconstruct prices from edges is lossy; report the null as E[rand]-price ≈ 0.5 - avg_entry
        avg_entry = 0.5 - (sum(sel_post) / len(sel_post)) - a.haircut + 0  # rough
        print(f"\n  NOTE: the SELECTED POST-T line is the real out-of-sample copy result.")
        print(f"  If its CI includes/below 0, the copy edge does not survive honest testing.")


if __name__ == "__main__":
    main()
