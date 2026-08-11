#!/usr/bin/env python3
"""
ml/resfeed.py — RESOLUTION-SOURCE feed: the genuine "second voice".

The only voice that can add edge is one that sees the real-world fact a market
resolves on BEFORE the order book fully reprices it. The cleanest case is a
CRYPTO THRESHOLD market — e.g. "Will Bitcoin be above $66,000 on June 3?" — where
the live spot price (CoinGecko, keyless) tells you the answer-in-progress
directly, independent of the Polymarket price.

This module:
  • parses a market question into (coin, threshold, direction)
  • pulls the live underlying spot price via feeds.coingecko()
  • returns a resolution signal: signed margin to threshold + a P(YES) lean

SCOPE / HONEST LIMITS (read before trusting it):
  • LIVE only. CoinGecko's free tier does not give arbitrary historical spot at
    each past "as_of", so this CANNOT be backfilled into the existing dataset for
    a historical backtest. It is meant to feed the LIVE bot as a second voice,
    where its whole value (acting before the book reprices) actually exists.
  • Crypto threshold markets only, for now. Other categories return None (no
    signal) and the panel falls back to momentum alone.
  • Fail-soft: any parse/network error returns None and never raises.
  • Edge is NOT proven. This is the plumbing for the edge your notes want to
    test, not a guarantee of one.

CLI (run on the Mac, needs internet):
  python3 ml/resfeed.py "Will Bitcoin be above $66,000 on June 3?"
"""
import os, re, sys, math

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))   # import feeds.py from project root
try:
    import feeds
except Exception:
    feeds = None

COIN_MAP = {
    "bitcoin": "bitcoin", "btc": "bitcoin",
    "ethereum": "ethereum", "eth": "ethereum",
    "solana": "solana", "sol": "solana",
    "dogecoin": "dogecoin", "doge": "dogecoin",
    "ripple": "ripple", "xrp": "ripple",
}

_NUM = r"\$?\s*([0-9][0-9,]*\.?[0-9]*)\s*([kKmM]?)"


def _to_float(num, suffix):
    v = float(num.replace(",", ""))
    if suffix.lower() == "k":
        v *= 1_000
    elif suffix.lower() == "m":
        v *= 1_000_000
    return v


def parse_crypto_threshold(question: str):
    """Return (coin_id, threshold_usd, direction) or None. direction: 'above'/'below'."""
    if not question:
        return None
    q = question.lower()
    coin = next((cid for kw, cid in COIN_MAP.items() if re.search(rf"\b{kw}\b", q)), None)
    if not coin:
        return None
    direction = None
    if any(w in q for w in ("above", "over", "greater", ">=", "at least", "reach", "hit", "exceed")):
        direction = "above"
    elif any(w in q for w in ("below", "under", "less than", "<=", "dip", "drop below")):
        direction = "below"
    m = re.search(_NUM, q)
    if not m or direction is None:
        return None
    try:
        thr = _to_float(m.group(1), m.group(2))
    except Exception:
        return None
    if thr <= 0:
        return None
    return (coin, thr, direction)


def res_signal(question: str):
    """
    Live resolution signal for a crypto threshold market.
    Returns dict or None:
      { coin, threshold, direction, spot, margin, p_yes, source }
    margin = signed fractional distance of spot past the threshold in the YES
    direction (positive => currently resolving YES). p_yes is a bounded logistic
    lean on that margin — a *prior*, not a calibrated probability.
    """
    if feeds is None:
        return None
    parsed = parse_crypto_threshold(question)
    if not parsed:
        return None
    coin, thr, direction = parsed
    q = feeds.coingecko(coin)
    if not q or q.get("price") in (None, 0):
        return None
    spot = float(q["price"])
    raw = (spot - thr) / thr                      # >0 means spot above threshold
    margin = raw if direction == "above" else -raw
    p_yes = 1.0 / (1.0 + math.exp(-margin / 0.01))  # ~1% move ≈ meaningful tilt
    return {"coin": coin, "threshold": thr, "direction": direction,
            "spot": spot, "margin": round(margin, 5),
            "p_yes": round(p_yes, 4), "source": "coingecko"}


def live_feature(question: str):
    """Feature value(s) for the live feature store. 0.0 = no signal (neutral)."""
    s = res_signal(question)
    if not s:
        return {"res_margin": 0.0, "res_pyes": 0.5, "res_has": 0.0}
    return {"res_margin": s["margin"], "res_pyes": s["p_yes"], "res_has": 1.0}


if __name__ == "__main__":
    qn = " ".join(sys.argv[1:]) or "Will Bitcoin be above $66,000 on June 3?"
    print("question:", qn)
    print("parsed  :", parse_crypto_threshold(qn))
    print("signal  :", res_signal(qn))
    print("feature :", live_feature(qn))
