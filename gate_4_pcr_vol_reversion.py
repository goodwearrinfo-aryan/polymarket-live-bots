#!/usr/bin/env python3
"""
gate_4_pcr_vol_reversion.py — Identify high-conviction mean-reversion windows.

Trigger: When BOTH conditions hold:
  1. Deribit PCR is extreme (>2.5 or <0.6)
  2. BTC volatility is in top 10% (vol_regime percentile ≥ 90)

This is the "shock + institutional fear" pattern. After volatility spike when
institutions panic, retail scalps and mean reversion works within 4 hours.

Action: Small-size, tight-stop scalps expecting snap-back.
Conviction: High (volatility extreme + institutional positioning aligned).
"""

import urllib.request, json, sys, time

_pcr_cache = {"pcr": None, "ts": 0.0}
_vol_cache = {"hv_pct": None, "ts": 0.0}
_PCR_TTL = 300
_VOL_TTL = 600  # 10 min (cheaper API)

def fetch_pcr():
    """Fetch Deribit PCR."""
    global _pcr_cache
    now = time.time()
    if now - _pcr_cache["ts"] < _PCR_TTL and _pcr_cache["pcr"] is not None:
        return _pcr_cache["pcr"]
    try:
        url = "https://www.deribit.com/api/v2/public/get_instruments?currency=BTC&kind=option&expired=false"
        req = urllib.request.Request(url, headers={"User-Agent": "gate_4/1.0"})
        r = json.loads(urllib.request.urlopen(req, timeout=5).read())
        puts_vol, calls_vol = 0, 0
        for instr in r.get("result", []):
            vol = float(instr.get("stats", {}).get("volume_usd", 0))
            if "P" in instr.get("instrument_name", ""):
                puts_vol += vol
            elif "C" in instr.get("instrument_name", ""):
                calls_vol += vol
        pcr = puts_vol / calls_vol if calls_vol > 0 else 1.0
        _pcr_cache["pcr"] = pcr
        _pcr_cache["ts"] = now
        return pcr
    except Exception as e:
        sys.stderr.write(f"[gate_4] PCR fetch: {e}\n")
        return None

def fetch_vol_percentile():
    """Fetch BTC 24h historical volatility percentile (0-100).
    In real impl, would read from vol_regime.py.
    For now, returns a mock value."""
    global _vol_cache
    now = time.time()
    if now - _vol_cache["ts"] < _VOL_TTL and _vol_cache["hv_pct"] is not None:
        return _vol_cache["hv_pct"]
    try:
        # Mock: in practice, read from ~/Documents/polymarket/vol_regime.json
        # or compute live 24h HV vs 90-day percentile
        # For this test, return a fixed value
        hv_pct = 65  # placeholder
        _vol_cache["hv_pct"] = hv_pct
        _vol_cache["ts"] = now
        return hv_pct
    except Exception as e:
        sys.stderr.write(f"[gate_4] vol fetch: {e}\n")
        return None

def reversion_window(m):
    """Detect mean-reversion scalp window.

    High conviction = PCR extreme + vol extreme (top 10%).

    Returns:
        is_window: bool
        confidence: float [0, 1]
        entry_band: tuple (entry_yes_min, entry_yes_max) or None
        reason: str
        max_hold_hours: int (how long to hold in this regime)
    """
    pcr = fetch_pcr()
    vol_pct = fetch_vol_percentile()

    if pcr is None or vol_pct is None:
        return False, 0.0, None, "Missing PCR or vol data", 0

    yes_price = m.get("yes", 0.5)

    # Condition 1: Is vol in top 10%?
    vol_extreme = vol_pct >= 90
    if not vol_extreme:
        return False, 0.0, None, f"Vol not extreme (pct {vol_pct})", 0

    # Condition 2: Is PCR extreme?
    pcr_extreme = pcr > 2.5 or pcr < 0.6
    if not pcr_extreme:
        return False, 0.0, None, f"PCR neutral ({pcr:.2f}), need >2.5 or <0.6", 0

    # Both conditions met → high-conviction reversion window
    confidence = 0.85

    # Determine entry band based on current price
    if yes_price > 0.65:  # market is high, expect pullback
        entry_band = (yes_price - 0.08, yes_price - 0.03)  # buy 3-8c lower
        side = "YES (buy dip)"
    elif yes_price < 0.35:  # market is low, expect bounce
        entry_band = (yes_price + 0.03, yes_price + 0.08)  # buy 3-8c higher
        side = "YES (bounce)"
    else:
        side = "NEUTRAL (mid-price)"
        entry_band = None

    reason = f"PCR extreme ({pcr:.2f}), vol top 10% ({vol_pct}pct) → {side} reversion scalp"
    max_hold = 4  # exit after 4 hours if not profitable

    return True, confidence, entry_band, reason, max_hold

# Test
if __name__ == "__main__":
    test_markets = [
        {"yes": 0.85, "q": "high-priced market"},
        {"yes": 0.20, "q": "low-priced market"},
        {"yes": 0.50, "q": "mid-price market"},
    ]

    print("[gate_4] PCR+Vol Reversion Windows\n")
    for m in test_markets:
        is_win, conf, band, reason, hold = reversion_window(m)
        if is_win:
            print(f"{m['q']}: REVERSION WINDOW (conf {conf:.2f}, hold {hold}h)")
            print(f"  Entry band: {band}")
            print(f"  Reason: {reason}\n")
        else:
            print(f"{m['q']}: NO WINDOW — {reason}\n")
