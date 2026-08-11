#!/usr/bin/env python3
"""bot_backtest.py — test the OSS-bot strategies that are reducible to a price-level
rule against our resolved-market dataset (ml/dataset.csv), honestly, with bootstrap
95% CIs. Pure paper on PAST resolved markets.

Of the 5 top OSS bots studied (poly-market-maker, Polymarket/agents, artvandelay,
btc-5min, py-clob-client) only the btc-5min thesis is testable on resolution data:
"near-expiry strong favorites rarely reverse" -> buy YES at a high price, hold to
resolution. The others are market-making / LLM / SDK and need order-book time series,
news, or an LLM in the loop (not representable in {price, outcome}).

We test that favorite-buy thesis against OUR fade (buy NO on overpriced YES) plus
controls. A strategy has a real edge ONLY if its bootstrap 95% CI on $/trade
excludes zero (hard rule 4). Spread is charged on entry. Output -> bot_backtest.json.
"""
import csv, json, random, collections, os

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "ml", "dataset.csv")
OUT  = os.path.join(HERE, "bot_backtest.json")
SPREAD = 0.02          # total bid-ask; pay half on entry
random.seed(7)         # reproducible bootstrap


def load():
    rows = list(csv.DictReader(open(DATA)))
    seen = collections.OrderedDict()
    for r in rows:                       # one row per market = honest independence
        seen.setdefault(r["condition_id"], r)
    out = []
    for r in seen.values():
        try:
            out.append((float(r["prob"]), int(float(r["final_outcome"])),
                        (r.get("category") or "other").lower()))
        except Exception:
            pass
    return out


def pnl_yes(price, oc):  return oc - (price + SPREAD / 2)            # buy YES at ask
def pnl_no(price, oc):   return (1 - oc) - ((1 - price) + SPREAD / 2)  # buy NO at ask


def run(data, pick):
    tr = []
    for price, oc, cat in data:
        s = pick(price, cat)
        if s == "YES":  tr.append(pnl_yes(price, oc))
        elif s == "NO": tr.append(pnl_no(price, oc))
    return tr


from psr_dsr import boot_ci   # canonical bootstrap CI (was duplicated here)


def win_pct(tr):
    return sum(1 for x in tr if x > 0) / len(tr) * 100 if tr else 0.0


STRATS = [
    ("FADE 0.20-0.55  (OURS: buy NO on overpriced YES)", lambda p, c: "NO" if 0.20 <= p <= 0.55 else None),
    ("FADE 0.20-0.55  sports only",      lambda p, c: "NO" if (0.20 <= p <= 0.55 and "sport" in c) else None),
    ("FADE 0.20-0.55  NON-sports",       lambda p, c: "NO" if (0.20 <= p <= 0.55 and "sport" not in c) else None),
    ("BUY-FAV >=0.80  (btc5m/favorite thesis: buy YES)", lambda p, c: "YES" if p >= 0.80 else None),
    ("BUY-FAV >=0.90  (buy YES)",        lambda p, c: "YES" if p >= 0.90 else None),
    ("BUY-FAV >=0.95  (btc5m $0.95 GTC fallback)",        lambda p, c: "YES" if p >= 0.95 else None),
    ("BUY-LONGSHOT <=0.20  (CONTROL: buy YES cheap)",     lambda p, c: "YES" if p <= 0.20 else None),
    ("BUY-ALL-YES  (CONTROL: must lose after spread)",    lambda p, c: "YES"),
]


def main():
    data = load()
    print(f"dataset: {len(data)} distinct resolved markets  (spread charged = {SPREAD})\n")
    hdr = f"{'strategy':52} {'n':>4} {'win%':>5} {'$/trade':>8} {'95% CI':>20}  edge?"
    print(hdr); print("-" * len(hdr))
    results = []
    for name, pick in STRATS:
        tr = run(data, pick)
        m, lo, hi = boot_ci(tr)
        win = win_pct(tr)
        sig = lo > 0   # CI excludes zero on the positive side = real edge
        flag = "YES (CI>0)" if sig else ("loses" if hi < 0 else "~0 / noise")
        print(f"{name:52} {len(tr):>4} {win:>4.0f}% {m:>+8.4f}  [{lo:>+.4f},{hi:>+.4f}]  {flag}")
        results.append({"strategy": name, "n": len(tr), "win_pct": round(win, 1),
                        "per_trade": round(m, 4), "ci_lo": round(lo, 4), "ci_hi": round(hi, 4),
                        "total": round(m * len(tr), 2), "edge": sig})
    json.dump({"spread": SPREAD, "n_markets": len(data), "results": results},
              open(OUT, "w"), indent=2)
    print(f"\nwrote {OUT}")
    print("\nRule: an edge counts ONLY if the 95% CI excludes zero. Point estimates lie.")


if __name__ == "__main__":
    main()
