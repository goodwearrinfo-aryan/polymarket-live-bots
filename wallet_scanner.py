#!/usr/bin/env python3
"""
wallet_scanner.py — Scan recent Polymarket traders and flag by win rate.

Pulls the last N global trades, extracts unique wallets, flags each as
SHARP / NEUTRAL / FISH, and prints a ranked leaderboard.

Usage:
  python3 wallet_scanner.py              # scan ~500 recent trades
  python3 wallet_scanner.py --trades 2000 --min-positions 10
  python3 wallet_scanner.py --sharp-only
"""

import json, sys, time, urllib.request, urllib.parse, argparse
from concurrent.futures import ThreadPoolExecutor, as_completed

UA = "Mozilla/5.0"

# coinflip mill: short-dated crypto Up/Down series — calibrated 5-min markets,
# never copyable at any lag. Mirrors newmarket_logger.MILL_* / brainbot.
_MILL_Q_HINTS    = ("up or down",)
_MILL_SLUG_HINTS = ("updown", "up-or-down", "-up-down-", "hourly")

def _is_coinflip(title, slug=""):
    t = (title or "").lower(); s = (slug or "").lower()
    return any(h in t for h in _MILL_Q_HINTS) or any(h in s for h in _MILL_SLUG_HINTS)

def _get(path, params=None):
    url = f"https://data-api.polymarket.com/{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=12) as r:
        return json.loads(r.read())

def fetch_top_market_ids(n=100):
    """Fetch top markets by volume from gamma API."""
    try:
        url = (f"https://gamma-api.polymarket.com/markets?limit={n}"
               "&active=false&order=volume&ascending=false")
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=12) as r:
            markets = json.loads(r.read())
        return [m["conditionId"] for m in markets if m.get("conditionId")]
    except Exception as e:
        sys.stderr.write(f"[markets] {e}\n")
        return []

def fetch_wallets(n_trades):
    """Pull wallets from recent global trades + per-market trades on top markets."""
    wallets = set()

    # Layer 1: recent global feed (max ~500-700 unique)
    try:
        batch = _get("trades", {"limit": 500})
        for t in batch:
            w = t.get("proxyWallet", "").lower()
            if w:
                wallets.add(w)
        sys.stderr.write(f"[wallets] global feed: {len(wallets)} wallets\n")
    except Exception as e:
        sys.stderr.write(f"[wallets] global feed error: {e}\n")

    if len(wallets) >= n_trades:
        return list(wallets)

    # Layer 2: per-market trades on top-volume markets (parallel)
    market_ids = fetch_top_market_ids(100)
    sys.stderr.write(f"[wallets] fetching trades across {len(market_ids)} top markets...\n")

    def _market_wallets(cid):
        try:
            url = f"https://data-api.polymarket.com/trades?conditionId={cid}&limit=500"
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=12) as r:
                trades = json.loads(r.read())
            return {t.get("proxyWallet", "").lower() for t in trades if t.get("proxyWallet")}
        except Exception:
            return set()

    with ThreadPoolExecutor(max_workers=20) as ex:
        futures = [ex.submit(_market_wallets, cid) for cid in market_ids]
        for f in as_completed(futures):
            wallets |= f.result()
            if len(wallets) >= n_trades:
                break

    sys.stderr.write(f"[wallets] total unique: {len(wallets)}\n")
    return list(wallets)

def flag_wallet(addr, min_positions):
    try:
        positions = _get("positions", {"user": addr, "limit": 500, "sizeThreshold": 0})
        if not positions:
            return None

        closed = [p for p in positions if p.get("redeemable") or p.get("curPrice", 0.5) in (0.0, 1.0)]
        if len(closed) < min_positions:
            return None

        wins           = [p for p in closed if p.get("realizedPnl", 0) > 0]
        total_realized = sum(p.get("realizedPnl", 0) for p in positions)
        total_wagered  = sum(p.get("totalBought", 0) for p in positions)
        avg_bet        = total_wagered / len(positions) if positions else 0
        win_rate       = len(wins) / len(closed) * 100 if closed else 0
        cf             = sum(1 for p in closed if _is_coinflip(p.get("title", ""), p.get("slug", "")))
        coinflip_share = cf / len(closed) if closed else 0

        # Two SHARP archetypes:
        # 1. High-accuracy: WR≥55% + positive P&L (favorite traders)
        # 2. Value/longshot: WR<55% but large positive P&L (big wins on underdogs)
        if (win_rate >= 55 and total_realized > 0) or (total_realized >= 500 and win_rate >= 15):
            flag = "SHARP"
        elif total_realized < -500 or (win_rate <= 20 and total_realized <= 0):
            flag = "FISH"
        else:
            flag = "NEUTRAL"

        return {
            "addr":           addr,
            "flag":           flag,
            "win_rate":       round(win_rate, 1),
            "wins":           len(wins),
            "closed":         len(closed),
            "realized_pnl":   round(total_realized, 2),
            "total_wagered":  round(total_wagered, 2),
            "avg_bet":        round(avg_bet, 2),
            "coinflip_share": round(coinflip_share, 3),
        }
    except Exception:
        return None

def scan(n_trades=500, min_positions=5, sharp_only=False, max_workers=20):
    print(f"Fetching recent trades (up to {n_trades})...")
    wallets = fetch_wallets(n_trades)
    print(f"Found {len(wallets)} unique wallets. Flagging each (min {min_positions} closed positions)...")

    results = []
    done = 0
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(flag_wallet, w, min_positions): w for w in wallets}
        for f in as_completed(futures):
            done += 1
            r = f.result()
            if r:
                results.append(r)
            if done % 50 == 0:
                print(f"  {done}/{len(wallets)} checked, {len(results)} qualify so far...")

    if sharp_only:
        results = [r for r in results if r["flag"] == "SHARP"]

    # Sort: SHARP first, then by realized P&L desc
    order = {"SHARP": 0, "NEUTRAL": 1, "FISH": 2}
    results.sort(key=lambda r: (order.get(r["flag"], 3), -r["realized_pnl"]))

    icons = {"SHARP": "🟢", "NEUTRAL": "🟡", "FISH": "🔴"}

    print()
    print(f"{'='*85}")
    print(f"  {'FLAG':<10} {'WIN%':>6}  {'W/L':>8}  {'REALIZED P&L':>14}  {'AVG BET':>10}  WALLET")
    print(f"{'='*85}")
    for r in results:
        icon = icons.get(r["flag"], "❓")
        wl   = f"{r['wins']}W/{r['closed']-r['wins']}L"
        print(f"  {icon} {r['flag']:<8} {r['win_rate']:>5.1f}%  {wl:>8}  ${r['realized_pnl']:>13,.2f}  ${r['avg_bet']:>9,.2f}  {r['addr'][:12]}...{r['addr'][-6:]}")

    print(f"{'='*85}")
    sharp   = [r for r in results if r["flag"] == "SHARP"]
    neutral = [r for r in results if r["flag"] == "NEUTRAL"]
    fish    = [r for r in results if r["flag"] == "FISH"]
    print(f"  Total qualified: {len(results)}  |  🟢 SHARP: {len(sharp)}  🟡 NEUTRAL: {len(neutral)}  🔴 FISH: {len(fish)}")
    print()

    if sharp:
        print("  🟢 SHARP wallets (copy candidates):")
        for r in sharp:
            print(f"     {r['addr']}  WR={r['win_rate']}%  P&L=${r['realized_pnl']:,.2f}  avgbet=${r['avg_bet']:,.2f}")
    print()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scan recent Polymarket wallets and flag by win rate")
    parser.add_argument("--trades",        type=int, default=500,  help="Recent trades to pull (default 500)")
    parser.add_argument("--min-positions", type=int, default=5,    help="Min closed positions to qualify (default 5)")
    parser.add_argument("--sharp-only",    action="store_true",     help="Only show SHARP accounts")
    parser.add_argument("--workers",       type=int, default=20,   help="Parallel workers (default 20)")
    args = parser.parse_args()
    scan(args.trades, args.min_positions, args.sharp_only, args.workers)
