#!/usr/bin/env python3
"""
nearres_oos.py — OOS validation of nearres thesis using Gamma API historical data.

⚠️ CONTAMINATED / SUPERSEDED (2026-06-14, see Lesson 12) — DO NOT TRUST ITS P&L.
This script books a FIXED +9¢ on every win and −3¢ on every loss (see ~line 153),
i.e. it assumes every loss is a clean -3¢ stop. Esports favorites gap 0.93→~0.30 in
one tick, so real losses are 45–65¢, not 3¢. This optimism manufactured a fake edge.
The canonical, gap-honest OOS is backtest_nearres.py (re-priced: esports_hi NEW =
−$0.088/exit, CI [−0.129,−0.049], DSR FAIL). Kept for history only.

Fetches resolved esports markets from Gamma, checks which ones had YES prices
in the nearres band [0.88, 0.95] near resolution, and computes simulated P&L.

Run: python3 nearres_oos.py
"""
import json
import re
import ssl
import time
import urllib.request
import urllib.parse
import random
from datetime import datetime, timedelta, timezone

_SSL = ssl.create_default_context()
_SSL.check_hostname = False
_SSL.verify_mode = ssl.CERT_NONE

GAMMA_BASE = "https://gamma-api.polymarket.com"
CLOB_BASE  = "https://clob.polymarket.com"

NEARRES_YES_HI   = 0.88   # band lo (we buy YES when price ≥ 0.88)
NEARRES_YES_LO   = 0.12   # band hi for NO (buy NO when price ≤ 0.12)
NEARRES_MAX_ENTRY = 0.95
NEARRES_GAIN     = 0.09
NEARRES_STOP     = 0.03
NEARRES_BET      = 2.0
NEARRES_MAX_H    = 4.0    # max hours to resolution

ESPORTS_TAGS = {"esports", "e-sports", "gaming", "cs2", "dota", "lol", "valorant",
                "counterstrike", "league of legends", "overwatch", "rocket league"}

ESPORTS_KW = re.compile(
    r'\b(esport|gaming|cs2|dota|valorant|navi|faze|liquid|fnatic|astralis|nip|'
    r'vitality|heroic|ence|spirit|mouz|g2|cloud9|c9|100 thieves|sentinels|'
    r'evil geniuses|eg |natus vincere|mousesports|mibr|luminosity|'
    r'bo[1357]\b|best of [1357]|map [123]\b)\b',
    re.IGNORECASE
)


def _get(url, params=None):
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "nearres-oos/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=15, context=_SSL) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f"  fetch error: {e}")
        return None


def is_esports(market):
    q = market.get("question", "") or market.get("title", "") or ""
    tags = [str(t).lower() for t in (market.get("tags") or [])]
    if any(t in ESPORTS_TAGS for t in tags):
        return True
    if ESPORTS_KW.search(q):
        return True
    return False


def get_clob_price_history(token_id, start_ts, end_ts):
    """Fetch 1-hour candles from CLOB for a token. Returns list of {t, c}."""
    url = f"{CLOB_BASE}/prices-history"
    params = {"market": token_id, "startTs": int(start_ts), "endTs": int(end_ts), "fidelity": 60}
    data = _get(url, params)
    if not data or not isinstance(data.get("history"), list):
        return []
    return data["history"]


def simulate_nearres(market):
    """
    Simulate nearres entry on a resolved market.
    Returns dict with {sim_side, sim_entry, sim_pnl, outcome, method} or None.
    """
    q = market.get("question", "")
    tokens = market.get("tokens") or market.get("outcomePrices") or []

    # Get resolution time
    end_date_str = market.get("endDate") or market.get("end_date_iso") or ""
    if not end_date_str:
        return None
    try:
        if end_date_str.endswith("Z"):
            end_dt = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
        else:
            end_dt = datetime.fromisoformat(end_date_str)
    except Exception:
        return None

    # Get outcome (resolved price)
    outcome = None
    yes_token_id = None
    if isinstance(tokens, list) and len(tokens) >= 2:
        for tok in tokens:
            if isinstance(tok, dict):
                outcome_val = tok.get("outcome", "")
                price_val = float(tok.get("price", 0.5) or 0.5)
                tid = tok.get("token_id") or tok.get("id", "")
                if str(outcome_val).upper() == "YES":
                    if price_val > 0.9:
                        outcome = "YES"
                    elif price_val < 0.1:
                        outcome = "NO"
                    yes_token_id = tid

    if outcome is None:
        return None  # not resolved or ambiguous

    # Try to get CLOB price history for the last 8 hours before resolution
    entry_price = None
    entry_side = None

    if yes_token_id:
        end_ts = end_dt.timestamp()
        start_ts = end_ts - 8 * 3600
        history = get_clob_price_history(yes_token_id, start_ts, end_ts)

        # Look for YES price in nearres band within 4h of resolution
        for candle in history:
            candle_ts = candle.get("t", 0)
            candle_price = float(candle.get("p") or candle.get("c") or 0)
            hours_left = (end_ts - candle_ts) / 3600
            if 0 < hours_left <= NEARRES_MAX_H:
                if NEARRES_YES_HI <= candle_price <= NEARRES_MAX_ENTRY:
                    entry_price = candle_price
                    entry_side = "YES"
                    break
                elif candle_price <= NEARRES_YES_LO and (1 - candle_price) <= NEARRES_MAX_ENTRY:
                    entry_price = 1 - candle_price
                    entry_side = "NO"
                    break

    if entry_price is None:
        return None  # couldn't confirm entry in band

    # Simulate P&L
    if entry_side == "YES":
        won = (outcome == "YES")
    else:
        won = (outcome == "NO")

    gain_per_share = NEARRES_GAIN
    stop_per_share = NEARRES_STOP
    shares = NEARRES_BET / entry_price
    pnl = shares * gain_per_share if won else -shares * stop_per_share

    return {
        "q": q[:80],
        "outcome": outcome,
        "side": entry_side,
        "entry": round(entry_price, 3),
        "pnl": round(pnl, 4),
        "won": won,
    }


def fetch_resolved_esports(max_markets=2000, lookback_days=90):
    """Fetch resolved esports markets from Gamma, up to max_markets."""
    print(f"Fetching resolved esports markets (last {lookback_days}d)...")
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    results = []
    offset = 0
    batch = 500

    while len(results) < max_markets:
        params = {
            "limit": batch,
            "offset": offset,
            "closed": "true",
            "order": "endDate",
            "ascending": "false",
        }
        data = _get(f"{GAMMA_BASE}/markets", params)
        if not data or not isinstance(data, list) or not data:
            break

        found_in_batch = 0
        for m in data:
            end_str = m.get("endDate", "")
            try:
                if end_str.endswith("Z"):
                    end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
                else:
                    end_dt = datetime.fromisoformat(end_str)
                if end_dt < cutoff:
                    # Past the lookback window — stop
                    return results
            except Exception:
                continue

            if is_esports(m):
                results.append(m)
                found_in_batch += 1

        print(f"  offset={offset} batch={len(data)} esports_found={len(results)}")
        if len(data) < batch:
            break
        offset += batch
        time.sleep(0.3)

    return results


def main():
    markets = fetch_resolved_esports(max_markets=500, lookback_days=60)
    print(f"\nTotal esports markets fetched: {len(markets)}")

    print("\nSimulating nearres entries (checking CLOB price history)...")
    sims = []
    for i, m in enumerate(markets):
        if i % 20 == 0:
            print(f"  progress {i}/{len(markets)} sims={len(sims)}")
        result = simulate_nearres(m)
        if result:
            sims.append(result)
        time.sleep(0.05)  # rate-limit CLOB

    if not sims:
        print("\nNo simulated entries found — nearres band not hit in CLOB history window.")
        print("This likely means the CLOB price-history endpoint doesn't return data for closed markets.")
        print("Falling back to simple market-count analysis...")

        # Fallback: count esports markets by resolution direction
        yes_wins = sum(1 for m in markets if _market_resolved_yes(m))
        no_wins  = sum(1 for m in markets if _market_resolved_no(m))
        ambig    = len(markets) - yes_wins - no_wins
        print(f"\nResolved esports markets (n={len(markets)}):")
        print(f"  YES resolved: {yes_wins} ({100*yes_wins/max(1,len(markets)):.0f}%)")
        print(f"  NO resolved:  {no_wins}  ({100*no_wins/max(1,len(markets)):.0f}%)")
        print(f"  ambiguous:    {ambig}")
        print("\nNote: nearres buys the favored side when YES≥0.88 or NO≥0.88.")
        print("If the market already had a clear favorite at that band, it should resolve correctly.")
        return

    wins  = sum(1 for s in sims if s["won"])
    total = len(sims)
    pnls  = [s["pnl"] for s in sims]
    mean  = sum(pnls) / total
    wr    = wins / total

    print(f"\n=== nearres OOS simulation (n={total}) ===")
    print(f"Win rate:    {wr:.1%}  ({wins}/{total})")
    print(f"Mean P&L:    ${mean:+.4f}/trade")
    print(f"Total P&L:   ${sum(pnls):+.2f}")
    print(f"\nSample trades:")
    for s in sims[:10]:
        flag = "W" if s["won"] else "L"
        print(f"  [{flag}] {s['side']}@{s['entry']} → {s['outcome']:3s}  P&L={s['pnl']:+.4f}  {s['q']}")

    # Bootstrap CI
    import random as _r
    rng = _r.Random(42)
    N = len(pnls)
    means_boot = [sum(pnls[rng.randrange(N)] for _ in range(N)) / N for _ in range(3000)]
    means_boot.sort()
    lo, hi = means_boot[75], means_boot[2925]
    print(f"\nBootstrap 95% CI: [{lo:+.4f}, {hi:+.4f}]")
    if lo > 0:
        print("CI EXCLUDES 0 — nearres OOS edge confirmed.")
    else:
        print("CI includes 0 — sample may be too small or edge is weaker OOS.")


def _market_resolved_yes(m):
    tokens = m.get("tokens") or []
    for tok in tokens:
        if isinstance(tok, dict) and str(tok.get("outcome", "")).upper() == "YES":
            p = float(tok.get("price", 0.5) or 0.5)
            return p > 0.9
    return False


def _market_resolved_no(m):
    tokens = m.get("tokens") or []
    for tok in tokens:
        if isinstance(tok, dict) and str(tok.get("outcome", "")).upper() == "YES":
            p = float(tok.get("price", 0.5) or 0.5)
            return p < 0.1
    return False


if __name__ == "__main__":
    main()
