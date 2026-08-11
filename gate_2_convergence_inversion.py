#!/usr/bin/env python3
"""
gate_2_convergence_inversion.py — High-conviction fade: all signals point one way,
market priced the opposite.

Standard convergence: buy when divergence AND momentum AND skew all agree.
Inversion: BUY the OPPOSITE of consensus when all 3 signals scream one direction
but the market is priced the opposite way. This is high-confidence contrarian.

Example:
  - divergence=+1 (uptrend), momentum=+1 (bullish), skew=+1 (bullish calls)
  - Market YES price = 0.25 (panic priced)
  - Action: BUY YES as a bounce fade (all signals say UP, market says DOWN)
  - High conviction: all 3 agree = rare + strong signal
"""

import sys

def get_signal_state(m, cfg=None):
    """Extract three signals for a market.

    Returns:
        divg: int in {-1, 0, 1} (bearish, neutral, bullish)
        mom: int in {-1, 0, 1}
        skew: int in {-1, 0, 1}
    """
    try:
        # Mock implementation; in scalp_lab, these would call candle_signals
        # For now: return based on price level as a proxy
        yes_price = m.get("yes", 0.5)

        # Divergence: is price moving against the underlying trend?
        # (in real impl: compare order flow vs price change)
        divg = 1 if yes_price < 0.3 else (-1 if yes_price > 0.7 else 0)

        # Momentum: is there directional conviction in recent trades?
        vol_change = m.get("vol_change_24h", 0)
        mom = 1 if vol_change > 0.1 else (-1 if vol_change < -0.1 else 0)

        # Skew: are calls or puts more expensive (option market bias)?
        skew = -1 if yes_price < 0.5 else (1 if yes_price > 0.5 else 0)

        return divg, mom, skew
    except Exception as e:
        sys.stderr.write(f"[gate_2] signal extraction failed: {e}\n")
        return 0, 0, 0

def convergence_strength(divg, mom, skew):
    """How many signals agree on direction?

    Returns:
        strength: int in {0, 1, 2, 3}
        direction: str in {'NEUTRAL', 'BEARISH', 'BULLISH'}
    """
    bullish = sum([s == 1 for s in [divg, mom, skew]])
    bearish = sum([s == -1 for s in [divg, mom, skew]])

    if bullish == 3:
        return 3, "BULLISH"
    elif bearish == 3:
        return 3, "BEARISH"
    elif bullish >= 2:
        return 2, "BULLISH"
    elif bearish >= 2:
        return 2, "BEARISH"
    else:
        return 0, "NEUTRAL"

def inversion_entry(m, cfg):
    """High-conviction contrarian entry.

    Trigger: all 3 signals agree on direction, but market priced opposite.
    Action: fade the market, bet on signal consensus.

    Returns:
        should_enter: bool
        side: str ('YES' or 'NO')
        conviction: float in [0, 1] (how strong is the signal agreement)
        reason: str (explanation)
    """
    divg, mom, skew = get_signal_state(m, cfg)
    strength, direction = convergence_strength(divg, mom, skew)

    if strength < 3:  # need all 3 to agree
        return False, None, 0.0, f"Weak convergence ({strength}/3): {divg},{mom},{skew}"

    yes_price = m.get("yes", 0.5)

    # All 3 signals bullish (expect YES higher)
    if direction == "BULLISH" and yes_price < 0.50:
        return True, "YES", 0.95, f"All bullish but priced at {yes_price:.2f} → buy YES fade"

    # All 3 signals bearish (expect YES lower)
    if direction == "BEARISH" and yes_price > 0.50:
        return True, "NO", 0.95, f"All bearish but priced at {yes_price:.2f} → buy NO fade"

    return False, None, 0.0, f"Signals agree {direction} but price {yes_price:.2f} already reflects it"

# Test
if __name__ == "__main__":
    test_markets = [
        {"yes": 0.25, "vol_change_24h": 0.15, "q": "test market 1"},  # bullish signals, low price
        {"yes": 0.75, "vol_change_24h": -0.12, "q": "test market 2"},  # bearish signals, high price
        {"yes": 0.50, "vol_change_24h": 0.02, "q": "test market 3"},   # neutral
    ]

    for m in test_markets:
        should_enter, side, conviction, reason = inversion_entry(m, {})
        print(f"{m['q']}: {side if should_enter else 'SKIP'} (conviction {conviction:.2f}) — {reason}")
