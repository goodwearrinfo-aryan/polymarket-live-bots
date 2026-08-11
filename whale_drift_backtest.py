#!/usr/bin/env python3
"""whale_drift_backtest.py — does copying sharp whales' SLOW-market event bets
actually pay, net of the copier's lag + spread? (PAPER research, read-only.)

The cheapest real test the 10-leg judge flagged to code FIRST: it gates the whole
smart-money family. If a sharp whale's fresh big buys on slow event markets don't
clear a realistic copy haircut to resolution, there is no edge to copy at any
horizon and the consensus/whaleconsensus legs are dead without building them.

Method (per profiled sharp wallet):
  1. paginate data-api /trades?user=<addr> back through history
  2. keep BUY trades on SLOW event markets (drop crypto Up/Down + intraday by title)
  3. dedupe to FRESH entries (first buy per conditionId+outcomeIndex), big (>= --min-usd)
  4. a COPIER enters at whale_price + HAIRCUT (models 60s lag + spread + adverse impact)
  5. settle to resolution via ctf_resolution.winning_outcome (on-chain, gap-honest):
        terminal = 1.0 if the bought outcome won else 0.0
  6. edge/share = terminal - (whale_price + haircut); pool + bootstrap CI
Controls baked in: a RANDOM-outcome null (would-be EV of buying a random side at the
same prices) and the raw whale edge (no haircut) to show how much the haircut eats.

Usage:
  python3 whale_drift_backtest.py                 # all active whales, default haircut 0.03
  python3 whale_drift_backtest.py --haircut 0.02 --min-usd 1000 --pages 12
"""
import os, sys, json, time, argparse, urllib.request, urllib.parse, statistics, random
from concurrent.futures import ThreadPoolExecutor
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ctf_resolution

RESCACHE = os.path.join(HERE, "whale_drift_rescache.json")

UA = {"User-Agent": "Mozilla/5.0"}
COINFLIP_KEYS = ("up or down", "up/down", " et?", "am-", "pm-", "intraday")  # exclude fast markets


def _get(path, q, tries=4):
    u = f"https://data-api.polymarket.com/{path}?" + urllib.parse.urlencode(q)
    for a in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=20) as r:
                return json.loads(r.read())
        except Exception:
            if a == tries - 1:
                return []
            time.sleep(1.5 * (a + 1))


def wallet_trades(addr, pages, page=500):
    out, off = [], 0
    for _ in range(pages):
        b = _get("trades", {"user": addr, "limit": page, "offset": off})
        if not b:
            break
        out.extend(b)
        if len(b) < page:
            break
        off += page
        time.sleep(0.3)
    return out


def is_slow(title):
    t = (title or "").lower()
    return not any(k in t for k in COINFLIP_KEYS)


def fresh_big_buys(trades, min_usd):
    seen = set()
    # oldest-first so the FIRST buy per (market,outcome) is the fresh entry
    for t in sorted(trades, key=lambda x: int(x.get("timestamp", 0))):
        if t.get("side") != "BUY":
            continue
        if not is_slow(t.get("title")):
            continue
        cid = t.get("conditionId"); oi = t.get("outcomeIndex")
        if cid is None or oi is None:
            continue
        key = (cid, oi)
        if key in seen:
            continue
        usd = float(t.get("price", 0)) * float(t.get("size", 0))
        if usd < min_usd:
            continue
        seen.add(key)
        yield {"cid": cid, "oi": int(oi), "price": float(t["price"]),
               "usd": usd, "title": t.get("title", ""), "ts": int(t.get("timestamp", 0))}


def boot_ci(xs, n=2000, seed=0):
    if len(xs) < 2:
        return (float("nan"), float("nan"))
    rnd = random.Random(seed); m = len(xs)
    means = sorted(sum(rnd.choice(xs) for _ in range(m)) / m for _ in range(n))
    return (means[int(0.025 * n)], means[int(0.975 * n)])


_rescache = {}
def _load_cache():
    global _rescache
    if os.path.exists(RESCACHE):
        try: _rescache = json.load(open(RESCACHE))
        except Exception: _rescache = {}

def resolve_all(cids, workers=10):
    """Parallel-resolve unique conditionIds -> winning_outcome, cached to disk.
    Turns thousands of sequential eth_calls into a parallel batch over UNIQUE markets."""
    todo = [c for c in cids if c not in _rescache]
    if not todo:
        return
    print(f"resolving {len(todo)} unique markets on-chain ({len(cids)-len(todo)} cached)…", flush=True)
    def one(c):
        try:
            w = ctf_resolution.winning_outcome(c)   # 0,1,-1,None
        except Exception:
            w = None
        return c, w
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for c, w in ex.map(one, todo):
            _rescache[c] = w; done += 1
            if done % 200 == 0:
                json.dump(_rescache, open(RESCACHE, "w")); print(f"  …{done}/{len(todo)}", flush=True)
    json.dump(_rescache, open(RESCACHE, "w"))

def settle(cid, oi):
    win = _rescache.get(cid)
    if win is None or win == -1:   # unresolved / no winner
        return None
    return 1.0 if win == oi else 0.0


def _report(label, edges, raws, ctrls, haircut):
    if not edges:
        print(f"  [{label}] no resolved bets"); return
    mc = statistics.mean(edges); lo, hi = boot_ci(edges); mr = statistics.mean(raws)
    cm = statistics.mean(ctrls) if ctrls else float("nan")
    print(f"  [{label}]  n={len(edges)}")
    print(f"     raw whale edge/share (no haircut):  {mr:+.4f}")
    print(f"     COPY edge/share (haircut {haircut}):   {mc:+.4f}   95% CI [{lo:+.4f}, {hi:+.4f}]")
    print(f"     control (random side @ same probs):  {cm:+.4f}")
    if len(edges) >= 30 and lo > 0 and mc > cm:
        print(f"     VERDICT: ✅ CLEARS 0 at n>=30 (CI>0), beats control by {mc-cm:+.4f} — promote to gated leg")
    elif lo > 0:
        print(f"     VERDICT: 🟡 CI>0 but n<30 — accumulate before believing")
    else:
        print(f"     VERDICT: ❌ copy edge does not clear 0 — null at this haircut")


def run(wallets, haircut, min_usd, pages, band):
    lo_b, hi_b = band
    _load_cache()
    # PASS 1: gather all bets per wallet (cheap data-api), collect unique conditionIds
    print(f"gathering trades for {len(wallets)} wallets…", flush=True)
    wbets = {}
    for w in wallets:
        wbets[w] = list(fresh_big_buys(wallet_trades(w, pages), min_usd))
    all_cids = {b["cid"] for bs in wbets.values() for b in bs}
    # PASS 2: parallel-resolve unique markets on-chain (the expensive part, batched)
    resolve_all(all_cids)

    allp = {"e": [], "r": [], "c": []}
    midp = {"e": [], "r": [], "c": []}
    rnd = random.Random(42)
    print(f"\ncopyable = entry price + haircut < 0.99 (can't buy a favorite above $1); "
          f"midband = entry in [{lo_b},{hi_b}] (real uncertainty, not 0.99-padding/lottery-dust)\n")
    print(f"{'wallet':<14} {'bets':>5} {'reslvd':>6} {'mid-n':>6} {'rawAll':>8} {'copyAll':>8} {'copyMid':>8}")
    for w in wallets:
        bets = wbets[w]
        we, wr_, wmid = [], [], []
        for b in bets:
            p = b["price"]
            if p + haircut >= 0.99:           # uncopyable: a copier can't pay >~$1 for a near-certain favorite
                continue
            term = settle(b["cid"], b["oi"])
            if term is None:
                continue
            raw = term - p; cop = term - (p + haircut)
            ctrl = (1.0 if rnd.random() < p else 0.0) - (p + haircut)
            allp["e"].append(cop); allp["r"].append(raw); allp["c"].append(ctrl); we.append(cop); wr_.append(raw)
            if lo_b <= p <= hi_b:
                midp["e"].append(cop); midp["r"].append(raw); midp["c"].append(ctrl); wmid.append(cop)
        if we:
            print(f"{w[:12]:<14} {len(bets):>5} {len(we):>6} {len(wmid):>6} "
                  f"{statistics.mean(wr_):>+7.3f} {statistics.mean(we):>+7.3f} "
                  f"{(statistics.mean(wmid) if wmid else float('nan')):>+7.3f}")
        else:
            print(f"{w[:12]:<14} {len(bets):>5} {0:>6} {0:>6} {'--':>7} {'--':>7} {'--':>7}")
        time.sleep(0.4)

    print("\n" + "=" * 70)
    print("POOLED — ALL copyable slow-market bets (incl. mid-favorites <0.99):")
    _report("all-copyable", allp["e"], allp["r"], allp["c"], haircut)
    print(f"\nPOOLED — GENUINE-UNCERTAINTY band [{lo_b},{hi_b}] (the only place copy info can matter):")
    _report(f"midband", midp["e"], midp["r"], midp["c"], haircut)
    print("\nReading: if the edge lives ONLY in all-copyable but dies in midband, it's residual")
    print("favorite-padding (uncopyable / gap-fragile), not information. The midband line is the real test.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--wallets", default=os.path.join(HERE, "whale_candidates_active.json"))
    ap.add_argument("--haircut", type=float, default=0.03, help="copier lag+spread+impact haircut per share")
    ap.add_argument("--min-usd", type=float, default=1000, help="min whale bet notional to count")
    ap.add_argument("--pages", type=int, default=10, help="trade-history pages (500/page) per wallet")
    ap.add_argument("--band", default="0.15,0.85", help="genuine-uncertainty entry band lo,hi")
    a = ap.parse_args()
    band = tuple(float(x) for x in a.band.split(","))
    src = json.load(open(a.wallets))
    wallets = [w["addr"] for w in src] if isinstance(src, list) else list(src.keys())
    print(f"drift/copy backtest over {len(wallets)} sharp wallets — haircut={a.haircut} min_usd=${a.min_usd:,.0f}\n")
    run(wallets, a.haircut, a.min_usd, a.pages, band)
