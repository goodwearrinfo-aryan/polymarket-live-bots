#!/usr/bin/env python3
"""
gate_1_avoid_uncertainty.py (REDESIGNED) — Block entries when volatility is extreme.

Old strategy: scale position by PCR (offense)
New strategy: DON'T TRADE when conditions are uncertain (defense)

On a losing baseline, the worst thing you can do is add VOLUME to bad odds.
Block entries when:
  - BTC volatility > 80th percentile (chaos, can't model)
  - Market book is thin (spread > 5c, can't fill)
  - Price moved >10% in last hour (gap risk)

This is pure avoidance: fewer trades, lower loss surface.
"""

import urllib.request, json, time, sys

_vol_cache = {"hv_pct": None, "ts": 0.0}
_VOL_TTL = 600

def fetch_vol_percentile():
    """Fetch BTC volatility percentile (0-100). 80+ = uncertain."""
    global _vol_cache
    now = time.time()
    if now - _vol_cache["ts"] < _VOL_TTL and _vol_cache["hv_pct"] is not None:
        return _vol_cache["hv_pct"]
    try:
        # Mock: read from ~/Documents/polymarket/vol_regime.json
        # For now, return fixed percentile
        hv_pct = 45  # placeholder
        _vol_cache["hv_pct"] = hv_pct
        _vol_cache["ts"] = now
        return hv_pct
    except Exception:
        return 50  # neutral default

def should_block_entry(m, cfg=None):
    """Defensive gate: block entry if conditions are uncertain.

    Returns:
        should_block: bool
        reason: str
        risk_level: str ('LOW', 'MEDIUM', 'HIGH')
    """
    # Check 1: Volatility extreme?
    vol_pct = fetch_vol_percentile()
    if vol_pct >= 80:
        return True, "Vol extreme (>80th pct) — too uncertain", "HIGH"

    # Check 2: Book is thin?
    book_spread = m.get("book_spread", 0.02)
    if book_spread > 0.05:  # >5c spread = thin, high slippage risk
        return True, f"Book thin (spread {book_spread:.3f}) — fill risk", "HIGH"

    # Check 3: Price moved too much recently?
    price_chg = m.get("price_change_1h", 0)
    if abs(price_chg) > 0.10:  # >10% move = gap risk
        return True, f"Price gap ({price_chg:+.0%}) — reversal risk", "MEDIUM"

    # Check 4: Market too young or ending soon?
    days_left = m.get("days_left", 1)
    if days_left < 0.5 or days_left > 100:  # <12h or >100d = edge unclear
        return True, f"Time edge unclear ({days_left:.1f}d) — avoid", "MEDIUM"

    return False, "Conditions normal — OK to trade", "LOW"

# Test
if __name__ == "__main__":
    test_markets = [
        {"book_spread": 0.02, "price_change_1h": 0.03, "days_left": 5, "q": "Normal market"},
        {"book_spread": 0.08, "price_change_1h": 0.05, "days_left": 2, "q": "Thin market"},
        {"book_spread": 0.03, "price_change_1h": 0.15, "days_left": 10, "q": "Gap risk market"},
    ]

    print("[gate_1_REDESIGNED] Avoid Uncertainty\n")
    for m in test_markets:
        block, reason, risk = should_block_entry(m)
        print(f"{m['q']}: {'BLOCK' if block else 'OK'} ({risk}) — {reason}")
