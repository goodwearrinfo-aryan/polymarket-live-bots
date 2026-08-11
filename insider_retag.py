#!/usr/bin/env python3
"""
insider_retag.py — ONE-TIME backfill. Re-flags every wallet already in
insider_wallets.json with a win-% colour tier and builds the separate
elite list (insider_wallets_elite.json, WR>79% only).

Colour tiers (by closed win-rate):
  🟢 ELITE  WR > 79%   (also written to insider_wallets_elite.json)
  🟡 HIGH   70-79%
  🟠 MID    60-70%
  ⚪ LOW    < 60%      (kept in the list, flagged so you can see it slipped)

A wallet that can't be profiled (API down / <20 positions) KEEPS its original
label untouched — we never lose a wallet. FAVPAD flag = high win-rate from
buying near-certain favorites (avg entry >= 0.80), i.e. padding not edge.

Run: python3 insider_retag.py
"""
import json, os, time, urllib.request

BASE    = os.path.expanduser("~/Documents/polymarket")
WATCH_F = os.path.join(BASE, "insider_wallets.json")
ELITE_F = os.path.join(BASE, "insider_wallets_elite.json")
UA      = {"User-Agent": "Mozilla/5.0"}


def _get(url):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=15) as r:
            return json.loads(r.read())
    except Exception:
        return None


def reprofile(addr):
    """(label, is_elite) or (None, False) if not profilable."""
    pos = _get(f"https://data-api.polymarket.com/positions?user={addr}&limit=500") or []
    if len(pos) < 20:
        return None, False
    real   = sum(float(p.get("realizedPnl") or 0) for p in pos)
    wins   = sum(1 for p in pos if float(p.get("realizedPnl") or 0) > 0)
    losses = sum(1 for p in pos if float(p.get("realizedPnl") or 0) < 0)
    closed = wins + losses
    if closed < 20:
        return None, False
    wr = 100 * wins / closed
    if   wr > 79: dot, tname = "🟢", "ELITE"
    elif wr >= 70: dot, tname = "🟡", "HIGH"
    elif wr >= 60: dot, tname = "🟠", "MID"
    else:          dot, tname = "⚪", "LOW"
    # favorite-padding measured on WINNING positions only (see insider_finder)
    win_prices = [float(p.get("avgPrice") or 0) for p in pos
                  if float(p.get("realizedPnl") or 0) > 0 and float(p.get("avgPrice") or 0) > 0]
    win_entry = sum(win_prices) / len(win_prices) if win_prices else 0.0
    favpad = win_entry >= 0.80
    label = f"{dot}WR{wr:.0f}%-${real/1000:.0f}k-{tname}" + ("|FAVPAD" if favpad else "")
    return label, (wr > 79)


def main():
    watched = json.load(open(WATCH_F))
    elite   = {}
    tiers   = {"🟢": 0, "🟡": 0, "🟠": 0, "⚪": 0, "skip": 0}
    print(f"re-profiling {len(watched)} wallets…")
    for i, addr in enumerate(list(watched), 1):
        label, is_elite = reprofile(addr)
        if label:
            watched[addr] = label
            tiers[label[0]] = tiers.get(label[0], 0) + 1
            if is_elite:
                elite[addr] = label
        else:
            tiers["skip"] += 1
        if i % 20 == 0:
            print(f"  …{i}/{len(watched)}")
        time.sleep(0.4)

    json.dump(watched, open(WATCH_F, "w"), indent=2)
    json.dump(elite,   open(ELITE_F, "w"), indent=2)
    print(f"\n done. 🟢{tiers['🟢']} 🟡{tiers['🟡']} 🟠{tiers['🟠']} ⚪{tiers['⚪']} "
          f"(unprofilable kept: {tiers['skip']})")
    print(f" elite (WR>79%) -> {ELITE_F}: {len(elite)} wallets")


if __name__ == "__main__":
    main()
