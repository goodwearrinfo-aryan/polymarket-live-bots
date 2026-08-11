#!/usr/bin/env python3
"""
account_flag.py — Flag a Polymarket account by win rate and P&L.

Usage:
  python3 account_flag.py <wallet_address>
  python3 account_flag.py <wallet_address> --min-trades 10

Fetches all positions (open + closed) and computes:
  - Win rate (positions where realizedPnl > 0)
  - Total realized P&L
  - Portfolio value
  - Flag: SHARP / NEUTRAL / FISH
"""

import json, sys, urllib.request, urllib.parse, argparse

UA = "Mozilla/5.0"

def _get(path, params=None):
    url = f"https://data-api.polymarket.com/{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=12) as r:
        return json.loads(r.read())

def fetch_all_positions(addr):
    """Paginate through all positions for an address."""
    positions, offset, limit = [], 0, 500
    while True:
        batch = _get("positions", {"user": addr, "limit": limit, "offset": offset, "sizeThreshold": 0})
        if not batch:
            break
        positions.extend(batch)
        if len(batch) < limit:
            break
        offset += limit
    return positions

def fetch_value(addr):
    try:
        data = _get("value", {"user": addr})
        return data[0]["value"] if data else 0
    except Exception:
        return 0

def analyze(addr, min_trades=5):
    print(f"Fetching positions for {addr}...")
    positions = fetch_all_positions(addr)
    value     = fetch_value(addr)

    if not positions:
        print("No positions found.")
        return

    # Closed positions: redeemable=True or curPrice in {0,1} (resolved)
    closed = [p for p in positions if p.get("redeemable") or p.get("curPrice", 0.5) in (0.0, 1.0)]
    open_  = [p for p in positions if p not in closed]

    wins   = [p for p in closed if p.get("realizedPnl", 0) > 0]
    losses = [p for p in closed if p.get("realizedPnl", 0) <= 0]

    total_realized  = sum(p.get("realizedPnl", 0) for p in positions)
    total_wagered   = sum(p.get("totalBought", 0) for p in positions)
    avg_bet         = total_wagered / len(positions) if positions else 0
    win_rate        = len(wins) / len(closed) * 100 if closed else 0
    n_closed        = len(closed)

    # Flag
    if n_closed < min_trades:
        flag = "❓ INSUFFICIENT DATA"
    elif (win_rate >= 55 and total_realized > 0) or (total_realized >= 500 and win_rate >= 15):
        flag = "🟢 SHARP"
    elif total_realized < -500 or (win_rate <= 20 and total_realized <= 0):
        flag = "🔴 FISH"
    else:
        flag = "🟡 NEUTRAL"

    print()
    print(f"{'='*55}")
    print(f"  Account : {addr[:10]}...{addr[-6:]}")
    print(f"  Flag    : {flag}")
    print(f"{'='*55}")
    print(f"  Portfolio value   : ${value:>12,.2f}")
    print(f"  Realized P&L      : ${total_realized:>12,.2f}")
    print(f"  Total wagered     : ${total_wagered:>12,.2f}")
    print(f"  Avg bet size      : ${avg_bet:>12,.2f}")
    print(f"  Closed positions  : {n_closed}")
    print(f"  Open positions    : {len(open_)}")
    print(f"  Win rate          : {win_rate:.1f}%  ({len(wins)}W / {len(losses)}L)")
    print()

    if closed:
        top_wins = sorted(wins,   key=lambda p: p.get("realizedPnl", 0), reverse=True)[:3]
        top_loss = sorted(losses, key=lambda p: p.get("realizedPnl", 0))[:3]
        if top_wins:
            print("  Top wins:")
            for p in top_wins:
                print(f"    +${p['realizedPnl']:>8,.2f}  wagered ${p.get('totalBought',0):>8,.2f}  {p.get('title','')[:35]}")
        if top_loss:
            print("  Top losses:")
            for p in top_loss:
                print(f"    -${abs(p['realizedPnl']):>8,.2f}  wagered ${p.get('totalBought',0):>8,.2f}  {p.get('title','')[:35]}")
    print()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Flag a Polymarket account by win rate")
    parser.add_argument("address", help="Wallet address (0x...)")
    parser.add_argument("--min-trades", type=int, default=5,
                        help="Min closed positions before flagging (default 5)")
    args = parser.parse_args()
    analyze(args.address.lower(), args.min_trades)
