#!/usr/bin/env python3
"""
wallet_deep.py — position-level P&L anatomy of watched SHARP wallets.
Read-only research (data-api.polymarket.com/positions). Paper lab support.

Per wallet: where the money is actually made/lost —
  - P&L by category (esports/sports/crypto/politics/other)
  - P&L by avg entry-price band (do they earn on favorites or longshots?)
  - realized vs unrealized split per category
  - top 3 wins / top 3 losses (titles)
  - loss-sitting diagnostic: share of open positions >50% underwater

Output: stdout + wallet_deep.json
"""
import json, os, urllib.request
from collections import defaultdict

BASE = os.path.expanduser("~/Documents/polymarket")
UA   = {"User-Agent": "Mozilla/5.0"}

CATS = {
    "esports":  ("counter-strike", "cs2", "valorant", "league of legends", "dota",
                 "iem ", "blast", "vct ", "lck", "lpl", "esport", "(bo3", "(bo5"),
    "sports":   ("nba", "nfl", "mlb", "nhl", "tennis", "atp", "wta", "soccer", "spread:",
                 "premier league", "la liga", "serie a", "ufc", " vs. ", " vs ", "o/u"),
    "crypto":   ("bitcoin", "btc", "ethereum", "eth ", "solana", "sol ", "xrp",
                 "crypto", "up or down"),
    "politics": ("election", "president", "senate", "congress", "trump", "biden",
                 "nominee", "governor", "parliament", "minister", "mayor"),
}

def cat_of(title):
    t = (title or "").lower()
    for c, kws in CATS.items():
        if any(k in t for k in kws):
            return c
    return "other"

def band(p):
    try:
        p = float(p)
    except (TypeError, ValueError):
        return "?"
    lo = int(p * 10) / 10
    return f"{lo:.1f}"

def fetch_positions(addr):
    out, offset = [], 0
    while True:
        url = (f"https://data-api.polymarket.com/positions?user={addr}"
               f"&limit=500&offset={offset}")
        try:
            req = urllib.request.Request(url, headers=UA)
            page = json.loads(urllib.request.urlopen(req, timeout=15).read())
        except Exception:
            break
        if not page:
            break
        out.extend(page)
        if len(page) < 500 or offset >= 2000:
            break
        offset += 500
    return out

def analyze(addr, label):
    pos = fetch_positions(addr)
    if not pos:
        return {"label": label, "n": 0}
    by_cat  = defaultdict(lambda: {"real": 0.0, "unreal": 0.0, "n": 0})
    by_band = defaultdict(lambda: {"pnl": 0.0, "n": 0})
    deep_red = n_open = 0
    rows = []
    for p in pos:
        title = p.get("title") or ""
        real  = float(p.get("realizedPnl") or 0)
        unrl  = float(p.get("cashPnl") or 0)
        avgpx = p.get("avgPrice")
        cur   = float(p.get("curPrice") or 0)
        size  = float(p.get("size") or 0)
        c = cat_of(title)
        by_cat[c]["real"]  += real
        by_cat[c]["unreal"] += unrl
        by_cat[c]["n"]     += 1
        by_band[band(avgpx)]["pnl"] += real + unrl
        by_band[band(avgpx)]["n"]  += 1
        if size > 0:
            n_open += 1
            try:
                if float(avgpx) > 0 and cur < 0.5 * float(avgpx):
                    deep_red += 1
            except (TypeError, ValueError):
                pass
        rows.append((real + unrl, title, real, unrl))
    rows.sort()
    total_real  = sum(v["real"] for v in by_cat.values())
    total_unrl  = sum(v["unreal"] for v in by_cat.values())
    return {
        "label": label, "n": len(pos), "n_open": n_open,
        "net": round(total_real + total_unrl, 0),
        "realized": round(total_real, 0), "unrealized": round(total_unrl, 0),
        "deep_red_open_pct": round(100 * deep_red / n_open, 0) if n_open else 0,
        "by_cat": {c: {"net": round(v["real"] + v["unreal"], 0), "n": v["n"]}
                   for c, v in sorted(by_cat.items(), key=lambda kv: -(kv[1]["real"] + kv[1]["unreal"]))},
        "by_band": {b: {"net": round(v["pnl"], 0), "n": v["n"]}
                    for b, v in sorted(by_band.items())},
        "top_wins":   [(round(x, 0), t[:60]) for x, t, *_ in rows[-3:][::-1]],
        "top_losses": [(round(x, 0), t[:60]) for x, t, *_ in rows[:3]],
    }

def main():
    wallets = json.load(open(os.path.join(BASE, "insider_wallets.json")))
    report = {}
    for addr, label in wallets.items():
        r = analyze(addr, label)
        report[addr] = r
        print(f"\n{'='*76}\n{label}  ({addr[:10]}…)  positions={r.get('n', 0)}")
        if not r.get("n"):
            continue
        print(f"  NET ${r['net']:+,.0f}  (realized {r['realized']:+,.0f} / unrealized {r['unrealized']:+,.0f})"
              f"   open={r['n_open']}, {r['deep_red_open_pct']:.0f}% of open >50% underwater")
        print(f"  by category: " + "  ".join(f"{c}:{v['net']:+,.0f}(n{v['n']})" for c, v in r["by_cat"].items()))
        print(f"  by entry band: " + "  ".join(f"{b}:{v['net']:+,.0f}" for b, v in r["by_band"].items() if abs(v['net']) >= 100))
        print(f"  top wins   : " + " | ".join(f"${w:+,.0f} {t}" for w, t in r["top_wins"]))
        print(f"  top losses : " + " | ".join(f"${w:+,.0f} {t}" for w, t in r["top_losses"]))
    json.dump(report, open(os.path.join(BASE, "wallet_deep.json"), "w"), indent=1)
    print(f"\nwrote wallet_deep.json")

if __name__ == "__main__":
    main()
