#!/usr/bin/env python3
"""oddpool_history.py — fetch historical top-of-book (best_bid/ask/mid/spread)
for a Polymarket market via oddpool's FREE tier. This is the price+spread time
series the project lost when CLOB /prices-history went dead — the data needed to
BACKTEST the loggers (favwatch/sharpdrift/tapeshock…) on history instead of
waiting days for forward data.

oddpool free tier: /historical/polymarket/top-of-book?market_id=<conditionId>
  &granularity=1m|5m&limit=<=500 ; paginates via pagination_key ; 429-rate-limited.
Returns one snapshot PER TOKEN (asset_id distinguishes YES/NO).

PAPER ONLY, read-only. Usage as a library, or:
  python3 oddpool_history.py <market_id> [--gran 5m] [--pages 4]
"""
import os, sys, json, time, urllib.request, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = "https://api.oddpool.com"


def _key():
    k = os.environ.get("ODDPOOL_KEY", "")
    if not k:
        for line in open(os.path.join(HERE, ".env")):
            if line.startswith("ODDPOOL_KEY="):
                k = line.strip().split("=", 1)[1]
    return k


_KEY = _key()


def _get(path, tries=7):
    """Free-tier oddpool is heavily rate-limited — wait the 429 out (up to ~1min)."""
    for a in range(tries):
        try:
            req = urllib.request.Request(BASE + path, headers={"X-API-Key": _KEY, "User-Agent": "pm/1.0"})
            return json.load(urllib.request.urlopen(req, timeout=20))
        except urllib.error.HTTPError as e:
            if e.code == 429 and a < tries - 1:
                time.sleep(3.0 * (a + 1)); continue   # 3,6,9,12,15,18s
            raise
        except Exception:
            if a < tries - 1:
                time.sleep(2.0 * (a + 1)); continue
            raise


def top_of_book(market_id, granularity="5m", pages=4, sleep=0.6):
    """All snapshots (both tokens) for a market, oldest→newest. granularity 1m|5m."""
    out = []
    pk = None
    for _ in range(pages):
        path = f"/historical/polymarket/top-of-book?market_id={market_id}&granularity={granularity}&limit=200"
        if pk:
            path += f"&pagination_key={pk}"
        try:
            d = _get(path)
        except Exception:
            break
        snaps = d.get("snapshots", [])
        out.extend(snaps)
        pg = d.get("pagination") or {}
        pk = pg.get("pagination_key")
        if not pg.get("has_more") or not pk:
            break
        time.sleep(sleep)
    out.sort(key=lambda s: s.get("timestamp", 0))
    return out


def series_by_token(market_id, granularity="5m", pages=4):
    """{asset_id: [snaps sorted by ts]} — one series per outcome token."""
    by = {}
    for s in top_of_book(market_id, granularity, pages):
        by.setdefault(s.get("asset_id"), []).append(s)
    return by


def search_markets(q, limit=20):
    return _get(f"/search/markets?q={urllib.parse.quote(q)}&limit={limit}")


if __name__ == "__main__":
    import urllib.parse, argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("market_id")
    ap.add_argument("--gran", default="5m")
    ap.add_argument("--pages", type=int, default=4)
    a = ap.parse_args()
    by = series_by_token(a.market_id, a.gran, a.pages)
    print(f"market {a.market_id[:14]}…  tokens={len(by)}")
    for tok, snaps in by.items():
        if snaps:
            print(f"  token {tok[:18]}…  {len(snaps)} snaps  "
                  f"mid {snaps[0]['mid']:.3f}→{snaps[-1]['mid']:.3f}  "
                  f"spread~{sum(s['spread'] for s in snaps)/len(snaps):.3f}")
