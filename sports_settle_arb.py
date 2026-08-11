#!/usr/bin/env python3
"""
sports_settle_arb.py — PAPER, read-only sports settlement data-arb scanner.

The honest, buildable extraction from the top-trader finding (2026-07-30):
the #1 Polymarket wallet ($364k/30d) trades live in-play SPORTS at $2M size — a
speed+capital business a 60s paper bot CANNOT copy (proven: your walletcopy/whale
legs all lose to adverse selection). But the SAME markets host a real, non-predictive
edge your project already trusts (settled-but-mispriced data arb, the Hormuz/PortWatch
family): when a game is DECIDED (final, or a blowout with seconds left) but the
Polymarket price hasn't converged to 0/1 yet, that gap is readable from public data —
you're not forecasting, you're reading ESPN faster than the book reprices.

This scanner is READ-ONLY: it reads ESPN (keyless) + Polymarket gamma (keyless), finds
determined-but-mispriced sports markets, and LOGS candidates. It NEVER places a trade.
Survivors go to the leg-design gauntlet, not straight to the book.
"""
from __future__ import annotations
import json, time, urllib.request, urllib.parse, os, sys
from datetime import datetime, timezone

ESPN = "https://site.api.espn.com/apis/site/v2/sports/{}/scoreboard"
LEAGUES = ["baseball/mlb", "basketball/nba", "football/nfl", "soccer/usa.1", "tennis/atp"]
GAMMA = "https://gamma-api.polymarket.com/events?closed=false&active=true&limit=60&order=volume24hr&ascending=false"
LOG = os.path.expanduser("~/polymarket-live/sports_settle_arb.log")
# a market whose outcome is DECIDED but price is still this far from 0/1 = candidate edge
MISPRICE_THRESHOLD = 0.06   # price gap after fees/slippage worth flagging

def get(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": "sports-settle-arb/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)

def finished_games():
    """Return {team_name_lower: 'WIN'|'LOSS'} for games that are FINAL."""
    decided = {}
    for lg in LEAGUES:
        try:
            data = get(ESPN.format(lg))
        except Exception:
            continue
        for ev in data.get("events", []):
            comp = (ev.get("competitions") or [{}])[0]
            status = (ev.get("status") or {}).get("type", {})
            if not status.get("completed"):
                continue  # only decided games
            for c in comp.get("competitors", []):
                name = (c.get("team", {}) or {}).get("displayName", "") or c.get("athlete", {}).get("displayName", "")
                if name:
                    decided[name.lower()] = "WIN" if c.get("winner") else "LOSS"
    return decided

def open_sports_markets():
    """Live Polymarket sports markets with a current price."""
    try:
        events = get(GAMMA)
    except Exception:
        return []
    events = events if isinstance(events, list) else events.get("data", [])
    # prop/derivative markets are NOT the moneyline "who wins" — exclude (false-positive source)
    PROP = ["handicap", "(-", "(+", "total", "over ", "under ", " run ", "runs ",
            "series", "game 1", "game 2", "game 3", "game 4", "game 5", "-1.5", "+1.5",
            "first ", "innings", "spread", "map ", "correct score",
            "o/u", "draw", "double chance", "both teams", "clean sheet"]
    out = []
    for ev in events:
        for m in ev.get("markets", []):
            q = (m.get("question") or "")
            ql = q.lower()
            if not any(k in ql for k in [" vs ", " vs. ", "@", " beat ", "wins "]):
                continue
            if any(k in ql for k in PROP):          # skip props/derivatives
                continue
            if m.get("closed") or m.get("umaResolutionStatus") == "resolved":
                continue                             # skip already-resolved
            try:
                price = float((json.loads(m.get("outcomePrices", "[]")) or [None])[0])
            except Exception:
                continue
            if not (0.03 < price < 0.97):            # already converged to 0/1 = no live edge
                continue
            out.append({"q": q, "price": price, "slug": m.get("slug", "")})
    return out

def scan():
    decided = finished_games()
    markets = open_sports_markets()
    hits = []
    for m in markets:
        ql = m["q"].lower()
        for team, result in decided.items():
            if team in ql:
                # market YES = "does <first team> win?"; if that team is decided, fair = 1 or 0
                fair = 1.0 if result == "WIN" else 0.0
                gap = abs(fair - m["price"])
                if gap >= MISPRICE_THRESHOLD:
                    hits.append({"q": m["q"], "team": team, "result": result,
                                 "price": round(m["price"], 3), "fair": fair,
                                 "gap": round(gap, 3), "slug": m["slug"]})
                break
    return hits

def main():
    hits = scan()
    stamp = datetime.now(timezone.utc).isoformat()
    rec = {"ts": stamp, "candidates": len(hits), "hits": hits}
    with open(LOG, "a") as f:
        f.write(json.dumps(rec) + "\n")
    if hits:
        print(f"[{stamp}] {len(hits)} settlement-arb candidate(s):")
        for h in hits:
            print(f"  gap={h['gap']:.2f}  price={h['price']}  fair={h['fair']}  {h['q'][:60]}")
    else:
        print(f"[{stamp}] no determined-but-mispriced sports markets (calibrated — the honest default)")

if __name__ == "__main__":
    main()
