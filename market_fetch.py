#!/usr/bin/env python3
"""
market_fetch.py — Fetch live or mock Polymarket data.

For now: mock data for testing.
Later: integrate with gamma.let.today or clob.polymarket.com (read-only, no key).
"""

import json

def fetch_mock_markets():
    """Return test markets."""
    return [
        {
            "id": "esports_dota_1",
            "q": "Dota2: International Grand Final - Liquid to win?",
            "yes": 0.32,
            "no": 0.68,
            "daysout": 2,
            "minutes_to_close": 120,
        },
        {
            "id": "esports_lol_1",
            "q": "LoL LEC: G2 Esports to win playoffs?",
            "yes": 0.45,
            "no": 0.55,
            "daysout": 8,
            "minutes_to_close": 600,
        },
        {
            "id": "crypto_btc_1",
            "q": "BTC above $50k by end of month?",
            "yes": 0.72,
            "no": 0.28,
            "daysout": 15,
            "minutes_to_close": 3000,
        },
        {
            "id": "crypto_eth_1",
            "q": "ETH/USD > $2500?",
            "yes": 0.65,
            "no": 0.35,
            "daysout": 10,
            "minutes_to_close": 2000,
        },
        {
            "id": "politics_1",
            "q": "US election: Republican wins 2024?",
            "yes": 0.55,
            "no": 0.45,
            "daysout": 200,
            "minutes_to_close": 20000,
        },
        {
            "id": "sports_nba_1",
            "q": "Lakers to win NBA championship this season?",
            "yes": 0.38,
            "no": 0.62,
            "daysout": 180,
            "minutes_to_close": 18000,
        },
    ]


def fetch_live_markets():
    """
    TODO: Integrate with Polymarket API (gamma.let.today).
    Example: GET https://gamma.let.today/api/markets?status=active
    """
    raise NotImplementedError("Live market fetching not yet implemented")


def get_markets(use_mock=True):
    """Get markets (mock or live)."""
    if use_mock:
        return fetch_mock_markets()
    else:
        return fetch_live_markets()


if __name__ == "__main__":
    markets = get_markets(use_mock=True)
    print(f"Fetched {len(markets)} markets:")
    for m in markets:
        print(f"  {m['id']}: YES {m['yes']:.2f}, NO {m['no']:.2f}")
