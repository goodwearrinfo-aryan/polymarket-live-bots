#!/usr/bin/env python3
"""
gate_3_macro_divergence.py — Trade the divergence between Deribit PCR (institutional)
and Polymarket sentiment (retail prediction market).

Premise: institutions (derivatives) and retail (spot) often have different views.
When they diverge maximally, one is wrong → big move coming.

Example:
  - Deribit PCR = 0.3 (extreme bullish, calls pile)
  - Polymarket esports favorite = 0.95 (very confident YES)
  - Divergence: institutions expect UP, but retail already priced UP → overcrowded
  - Trade: sell YES / buy NO (unwind the overconfidence)

  - Deribit PCR = 3.0 (extreme fear, puts pile)
  - Polymarket underdog = 0.15 (panic, very low probability)
  - Divergence: institutions expect DOWN, but retail already capitulated
  - Trade: buy YES (capitulation bounce)
"""

import urllib.request, json, sys, time

_pcr_cache = {"pcr": None, "ts": 0.0}
_PCR_TTL = 300

def fetch_pcr():
    """Fetch Deribit PCR. Returns float [0, 5] or None."""
    global _pcr_cache
    now = time.time()
    if now - _pcr_cache["ts"] < _PCR_TTL and _pcr_cache["pcr"] is not None:
        return _pcr_cache["pcr"]
    try:
        url = "https://www.deribit.com/api/v2/public/get_instruments?currency=BTC&kind=option&expired=false"
        req = urllib.request.Request(url, headers={"User-Agent": "gate_3/1.0"})
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
        sys.stderr.write(f"[gate_3] PCR fetch error: {e}\n")
        return None

def pcr_sentiment(pcr):
    """Map PCR to institutional sentiment.

    Returns: str in {'EXTREME_FEAR', 'FEAR', 'NEUTRAL', 'GREED', 'EXTREME_GREED'}
    """
    if pcr is None:
        return "UNKNOWN"
    if pcr > 3.0:
        return "EXTREME_FEAR"
    elif pcr > 2.0:
        return "FEAR"
    elif pcr > 1.0 and pcr < 1.5:
        return "NEUTRAL"
    elif pcr < 0.8:
        return "GREED"
    elif pcr < 0.5:
        return "EXTREME_GREED"
    else:
        return "NEUTRAL"

def retail_sentiment(yes_price):
    """Map Polymarket YES price to retail sentiment.

    Returns: str in {'PANIC', 'BEARISH', 'NEUTRAL', 'BULLISH', 'OVERCONFIDENT'}
    """
    if yes_price < 0.20:
        return "PANIC"
    elif yes_price < 0.40:
        return "BEARISH"
    elif yes_price < 0.60:
        return "NEUTRAL"
    elif yes_price < 0.85:
        return "BULLISH"
    else:
        return "OVERCONFIDENT"

def macro_divergence_signal(m):
    """Detect macro divergence and generate trade signal.

    Returns:
        has_signal: bool
        side: str ('YES' or 'NO')
        conviction: float [0, 1]
        reason: str
    """
    pcr = fetch_pcr()
    if pcr is None:
        return False, None, 0.0, "PCR unavailable"

    yes_price = m.get("yes", 0.5)
    inst_sent = pcr_sentiment(pcr)
    retail_sent = retail_sentiment(yes_price)

    # Case 1: Institutions bullish, retail overconfident → sell (mania unwind)
    if inst_sent in ["GREED", "EXTREME_GREED"] and retail_sent == "OVERCONFIDENT":
        return True, "NO", 0.85, f"Insti bullish ({inst_sent}), retail overconfident ({yes_price:.2f}) → fade YES"

    # Case 2: Institutions fearful, retail panicked → buy YES (capitulation bounce)
    if inst_sent in ["EXTREME_FEAR", "FEAR"] and retail_sent in ["PANIC", "BEARISH"]:
        return True, "YES", 0.80, f"Insti fearful ({inst_sent}), retail panicked ({yes_price:.2f}) → buy YES"

    # Case 3: Institutions neutral, retail extreme (either way) → fade retail
    if inst_sent == "NEUTRAL":
        if retail_sent == "PANIC":
            return True, "YES", 0.70, f"Insti neutral, retail panic ({yes_price:.2f}) → bounce buy"
        elif retail_sent == "OVERCONFIDENT":
            return True, "NO", 0.70, f"Insti neutral, retail overconfident ({yes_price:.2f}) → fade"

    return False, None, 0.0, f"No divergence: insti={inst_sent}, retail={retail_sent}"

# Test
if __name__ == "__main__":
    test_markets = [
        {"yes": 0.95, "q": "Test market (overconfident)"},
        {"yes": 0.15, "q": "Test market (panic)"},
        {"yes": 0.50, "q": "Test market (neutral)"},
    ]

    print("[gate_3] Macro Divergence Signals\n")
    for m in test_markets:
        has_sig, side, conv, reason = macro_divergence_signal(m)
        status = f"{side} (conv {conv:.2f})" if has_sig else "NO SIGNAL"
        print(f"{m['q']}: {status} — {reason}")
