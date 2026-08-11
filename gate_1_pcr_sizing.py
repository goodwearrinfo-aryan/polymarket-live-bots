#!/usr/bin/env python3
"""
gate_1_pcr_sizing.py — Use Deribit Put-Call Ratio as a position-size multiplier.

Instead of gating entries (on/off), scale position size inversely with PCR extremeness.
- PCR 0.3 (extreme bullish mania) → 50% size reduction (crash risk)
- PCR 2.0+ (extreme fear) → 20% size increase (capitulation = opportunity)
- PCR 0.5–2.0 (normal) → 100% size (baseline)

Signal: institutional derivatives sentiment predicts mean-reversion volatility.
When institutions are certain, retail is catching the opposite move.
"""

import urllib.request, json, time, sys

_pcr_cache = {"pcr": None, "ts": 0.0}
_PCR_TTL = 300  # 5 min

def fetch_pcr_live():
    """Fetch Deribit BTC Put-Call Ratio. Returns float or None."""
    global _pcr_cache
    now = time.time()
    if now - _pcr_cache["ts"] < _PCR_TTL and _pcr_cache["pcr"] is not None:
        return _pcr_cache["pcr"]
    try:
        # Deribit options aggregated put/call volumes
        url = "https://www.deribit.com/api/v2/public/get_instruments?currency=BTC&kind=option&expired=false"
        req = urllib.request.Request(url, headers={"User-Agent": "gate_1/1.0"})
        r = json.loads(urllib.request.urlopen(req, timeout=5).read())
        # Sum put vs call notional (volume × last price)
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
        sys.stderr.write(f"[gate_1_pcr_sizing] fetch error: {e}\n")
        return 1.0  # neutral default

def size_multiplier(pcr):
    """Map PCR → position size multiplier.

    PCR < 0.5  (call mania):    mult = 0.50 (reduce 50%, bubble risk)
    0.5–1.0:                    mult = 0.75 (reduce 25%, slightly bullish)
    1.0–2.0:                    mult = 1.00 (neutral)
    2.0–3.0:                    mult = 1.15 (increase 15%, fear discount)
    PCR > 3.0 (put panic):      mult = 1.20 (increase 20%, capitulation)
    """
    if pcr is None:
        return 1.0
    if pcr < 0.5:
        return 0.50
    elif pcr < 1.0:
        return 0.75
    elif pcr < 2.0:
        return 1.00
    elif pcr < 3.0:
        return 1.15
    else:
        return 1.20

def apply_pcr_sizing(base_bet_usdc, market_id=None):
    """Apply PCR multiplier to a position size.

    Args:
        base_bet_usdc: nominal bet size (from scalp_lab CONFIG)
        market_id: optional, for logging

    Returns:
        adjusted_bet_usdc: base_bet_usdc * multiplier
        pcr: the PCR value used
        mult: the multiplier applied
    """
    pcr = fetch_pcr_live()
    mult = size_multiplier(pcr)
    adjusted = base_bet_usdc * mult
    if market_id:
        print(f"[gate_1] {market_id}: PCR={pcr:.2f} mult={mult:.2f} size ${base_bet_usdc} → ${adjusted:.2f}")
    return adjusted, pcr, mult

# Test
if __name__ == "__main__":
    for pcr_test in [0.3, 0.8, 1.5, 2.5, 3.5]:
        m = size_multiplier(pcr_test)
        print(f"PCR {pcr_test:.1f} → multiplier {m:.2f} → size $1.00 → ${1.00 * m:.2f}")
    print("\nLive PCR:")
    pcr, _ = fetch_pcr_live(), None
    print(f"Current Deribit BTC Put-Call: {pcr:.2f}" if pcr else "Could not fetch PCR")
