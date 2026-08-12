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

PAPER research, read-only. Resolution is ON-CHAIN via ctf_resolution
(winning_outcome on the CTF contract, gap-honest), sharing the
whale_drift_rescache.json disk cache so resolved markets never re-fetch.
NOTE: the old gamma `closed`+outcomePrices path silently returned n=0 for any
recent (post-T) horizon because it only counts markets closed at eval time.
On-chain resolution fixes that — a resolved market is truth regardless of age.

Verdict bar (hard): SELECTED wallets' POST-T dollar-weighted edge CI>0, n>=30.
Anything less = the copy edge does not survive honest out-of-sample testing.
"""
import os, sys, json, time, argparse, urllib.request, urllib.parse
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ctf_resolution
from whale_drift_backtest import is_slow  # reuse the same slow-market filter as drift

DATA = "https://data-api.polymarket.com"
RESCACHE = os.path.join(HERE, "whale_drift_rescache.json")
UA = {"User-Agent": "Mozilla/5.0"}
_res_cache = {}   # conditionId -> winning outcome index (0/1) or None

_loaded_cache = False


def _ensure_cache():
    global _loaded_cache
    if _loaded_cache:
        return
    _loaded_cache = True
    if os.path.exists(RESCACHE):
        try:
            _res_cache.update(json.load(open(RESCACHE)))
        except Exception:
            pass


def _save_cache():
    try:
        json.dump(_res_cache, open(RESCACHE, "w"))
    except Exception:
        pass


def _get(url):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20) as r:
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


def resolve_onchain(cids, workers=10):
    """Resolve unique unseen conditionIds via the CTF contract (winning_outcome)."""
    _ensure_cache()
    need = [c for c in cids if c not in _res_cache]
    if not need:
        return
    print(f"  resolving {len(need)} unseen markets on-chain…", flush=True)

    def one(c):
        try:
            return c, ctf_resolution.winning_outcome(c)
        except Exception:
            return c, None

    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for c, w in ex.map(one, need):
            _res_cache[c] = w
            done += 1
            if done % 200 == 0:
                _save_cache()
                print(f"    …{done}/{len(need)}", flush=True)
    _save_cache()


def collect(addr, pages, band, haircut, T):
    """Return (pre_edges, post_edges, pre_usd, post_usd) — split at cutoff T."""
    trades = wallet_trades(addr, pages)
    # only BUY, slow markets, in band, dedup to FIRST buy per (cid,outcome)
    fresh = {}
    for t in trades:
        if t.get("side") != "BUY":
            continue
        if not is_slow(t.get("title")):
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
        sz = float(t.get("size", 0) or 0)
        key = (cid, str(oi))
        if key not in fresh or ts < fresh[key]["ts"]:
            fresh[key] = {"cid": cid, "oi": oi, "price": price, "ts": ts, "usd": price * sz}
    resolve_onchain({f["cid"] for f in fresh.values()})
    pre, post, pre_u, post_u = [], [], [], []
    for f in fresh.values():
        win = _res_cache.get(f["cid"])
        if win is None or win == -1:
            continue
        try:
            terminal = 1.0 if win == int(f["oi"]) else 0.0
        except Exception:
            continue
        edge = terminal - (f["price"] + haircut)
        if f["ts"] < T:
            pre.append(edge); pre_u.append(f["usd"])
        else:
            post.append(edge); post_u.append(f["usd"])
    return pre, post, pre_u, post_u


def boot_ci(xs, n=2000, seed=0):
    if len(xs) < 2:
        return (0.0, 0.0)
    import random
    rnd = random.Random(seed); m = len(xs)
    means = sorted(sum(rnd.choice(xs) for _ in range(m)) / m for _ in range(n))
    return (means[int(n * .025)], means[int(n * .975)])


def dw(edges, usd):
    tw = sum(usd)
    return (sum(e * u for e, u in zip(edges, usd)) / tw) if edges and tw > 0 else float("nan")


def dw_boot_ci(edges, usd, n=2000, seed=7):
    if len(edges) < 2:
        return (float("nan"), float("nan"))
    import random
    rnd = random.Random(seed); m = len(edges)
    means = []
    for _ in range(n):
        idx = [rnd.randrange(m) for _ in range(m)]
        means.append(dw([edges[i] for i in idx], [usd[i] for i in idx]))
    means.sort()
    return (means[int(0.025 * n)], means[int(0.975 * n)])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wallets", default=os.path.join(HERE, "whale_candidates_active.json"))
    ap.add_argument("--days", type=int, default=21, help="cutoff: T = now - days")
    ap.add_argument("--pages", type=int, default=6)
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
    print(f"OOS copy backtest (ON-CHAIN resolution) — {len(wallets)} wallets, cutoff T={a.days}d ago, "
          f"haircut={a.haircut}, band={band}, select: pre-T n>={a.sel_min_n} & WR>={a.sel_min_wr}%\n")

    def work(addr):
        return addr, collect(addr, a.pages, band, a.haircut, T)
    data = {}
    with ThreadPoolExecutor(max_workers=10) as ex:
        for addr, (pre, post, pre_u, post_u) in ex.map(work, wallets):
            data[addr] = (pre, post, pre_u, post_u)

    sel_post, unsel_post, sel_pu, unsel_pu = [], [], [], []
    n_sel = 0
    sel_wallets = []
    for addr, (pre, post, pre_u, post_u) in data.items():
        pre_wr = (sum(1 for e in pre if e > -a.haircut) / len(pre) * 100) if pre else 0
        selected = len(pre) >= a.sel_min_n and pre_wr >= a.sel_min_wr
        if selected:
            n_sel += 1
            sel_wallets.append(addr)
            sel_post += post; sel_pu += post_u
        else:
            unsel_post += post; unsel_pu += post_u

    def line(tag, xs, us=None):
        if not xs:
            print(f"  {tag:<28} n=0")
            return
        n = len(xs); avg = sum(xs) / n; wr = sum(1 for e in xs if e > 0) / n * 100
        lo, hi = boot_ci(xs)
        verdict = "REAL EDGE (CI>0)" if lo > 0 else "no edge (CI<=0)"
        print(f"  {tag:<28} n={n:<5} edge/sh={avg:+.4f}  copier_WR={wr:>3.0f}%  CI[{lo:+.4f},{hi:+.4f}] {verdict}")
        if us:
            dlo, dhi = dw_boot_ci(xs, us)
            tw = sum(us)
            print(f"  {'$ '+tag+':':<28} $={tw:,.0f} $edge/$={dw(xs, us):+.4f}  CI[{dlo:+.4f},{dhi:+.4f}]")

    print(f"SELECTED {n_sel}/{len(wallets)} wallets on PRE-T record: {', '.join(w[:10] for w in sel_wallets) or 'none'}")
    print("Their POST-T (unseen) trades:")
    line("SELECTED wallets POST-T", sel_post, sel_pu)
    line("control: NOT-selected POST-T", unsel_post, unsel_pu)

    # hard verdict bar: selected POST-T dollar-weighted CI must be > 0
    print()
    if n_sel == 0:
        print("NO wallet passed pre-T selection → nothing to promote (consistent with null family).")
    elif len(sel_post) < 30:
        print(f"SELECTED post-T n={len(sel_post)} < 30 → cannot clear the n>=30 bar. Accumulate only.")
    else:
        dlo, dhi = dw_boot_ci(sel_post, sel_pu)
        if dlo > 0:
            print(f"VERDICT: ✅ SELECTED post-T $-weighted edge CI[{dlo:+.4f},{dhi:+.4f}] > 0, n={len(sel_post)} "
                  f"→ copy edge SURVIVES out-of-sample. Promote to gated leg (paper).")
        else:
            print(f"VERDICT: ❌ SELECTED post-T $-weighted CI[{dlo:+.4f},{dhi:+.4f}] includes/below 0, n={len(sel_post)} "
                  f"→ copy edge does NOT survive OOS. Family buried.")


if __name__ == "__main__":
    main()
