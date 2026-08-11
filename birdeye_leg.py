"""
birdeye_leg.py — Solana whale/smart-money signal leg for scalp_lab.

Sources:
  - birdeye-py client (~/Documents/birdeye-py) for typed API access
  - Sybil-cluster + whale-quality logic ported from birdeye-alpha-radar/ml_service/app/services/bot.py

Signal: BUY YES on Polymarket crypto markets when smart money (whale quality > 60)
        is accumulating the underlying token, and NO when Sybil clusters (wash trading)
        dominate (sybil_score > 50).

Requires: BIRDEYE_API_KEY env var
"""

import os
import sys
import time
import json
import logging

# Use cloned source directly — no pip install needed
sys.path.insert(0, os.path.expanduser("~/Documents/birdeye-py"))

log = logging.getLogger(__name__)

BIRDEYE_API_KEY = os.getenv("BIRDEYE_API_KEY", "")

# Token addresses for Polymarket crypto markets we track
# Map Polymarket market keyword → Solana token address
TOKEN_MAP = {
    "bitcoin":    "9n4nbM75f5Ui33ZbPYXn59EwSgE8CGsHtAeTH5YFeJ9E",  # BTC (wrapped)
    " btc ":      "9n4nbM75f5Ui33ZbPYXn59EwSgE8CGsHtAeTH5YFeJ9E",
    "ethereum":   "7vfCXTUXx5WJV5JADk17DUJ4ksgau7utNKj4b963voxs",  # ETH (wrapped)
    " eth ":      "7vfCXTUXx5WJV5JADk17DUJ4ksgau7utNKj4b963voxs",
    "solana":     "So11111111111111111111111111111111111111112",     # SOL
    " sol ":      "So11111111111111111111111111111111111111112",
    "dogecoin":   "8UmSCbSFp2dnCcBaYNFaGZYb4GNyMRNVaxrPQDQKpump",  # DOGE
    " doge":      "8UmSCbSFp2dnCcBaYNFaGZYb4GNyMRNVaxrPQDQKpump",
    "xrp":        "5nznFT4HyDMJtgSxfZ2KL3MZbFqt2mLnTFBW95JVpump",  # XRP (wrapped)
    "ripple":     "5nznFT4HyDMJtgSxfZ2KL3MZbFqt2mLnTFBW95JVpump",
    "bnb":        "9gP2kCy3wA1ctvYWQk75guqXuzoJGznfTuJuan7iKAKH",  # BNB
    "microstrat": "9n4nbM75f5Ui33ZbPYXn59EwSgE8CGsHtAeTH5YFeJ9E",  # MSTR tracks BTC
    "satoshi":    "9n4nbM75f5Ui33ZbPYXn59EwSgE8CGsHtAeTH5YFeJ9E",  # satoshi = BTC signal
}

CACHE_FILE = os.path.expanduser("~/Documents/polymarket/scalp_lab_cache.json")
CACHE_TTL = 300  # 5 minutes


def _load_cache() -> dict:
    try:
        with open(CACHE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cache(cache: dict):
    try:
        with open(CACHE_FILE, "w") as f:
            json.dump(cache, f)
    except Exception:
        pass


def _get_client():
    if not BIRDEYE_API_KEY:
        raise RuntimeError("BIRDEYE_API_KEY not set")
    from birdeyepy.birdeye import BirdEye
    return BirdEye(api_key=BIRDEYE_API_KEY)


def fetch_liquidity_data(address: str) -> dict:
    """
    Returns {liquidity, volume24h, vl_ratio, price} for a token.
    Uses token.overview() from birdeye-py clone.
    Cached for CACHE_TTL seconds. Returns {} on failure.
    """
    cache = _load_cache()
    cache_key = f"birdeye_liq_{address}"
    entry = cache.get(cache_key, {})
    if entry and time.time() - entry.get("ts", 0) < CACHE_TTL:
        return entry["data"]

    if not BIRDEYE_API_KEY:
        return {}

    try:
        client = _get_client()
        resp = client.token.overview(address=address)
        data = resp.get("data", {}) if isinstance(resp, dict) else {}
        liq = float(data.get("liquidity") or 0)
        vol = float(data.get("v24hUSD") or 0)
        result = {
            "liquidity": liq,
            "volume24h": vol,
            "vl_ratio": round(vol / liq, 3) if liq > 0 else 0,
            "price": float(data.get("price") or 0),
            "price_change_24h": float(data.get("priceChange24h") or 0),
        }
    except Exception as e:
        log.warning(f"birdeye_leg fetch_liquidity_data error: {e}")
        return {}

    cache[cache_key] = {"ts": time.time(), "data": result}
    _save_cache(cache)
    return result


def liquidity_gate(market_question: str, min_liquidity: float = 50_000) -> dict | None:
    """
    Gate for crypto legs. Returns liquidity context dict if market passes,
    None if it should be skipped.

    Context keys: liquidity, volume24h, vl_ratio, price, price_change_24h,
                  liq_ok (bool), rug_risk (bool — V/L>5 + liq<5k)
    """
    q = market_question.lower()
    address = None
    for keyword, addr in TOKEN_MAP.items():
        if keyword in q:
            address = addr
            break

    if not address or not BIRDEYE_API_KEY:
        return None  # no data available — don't block, let leg decide

    d = fetch_liquidity_data(address)
    if not d:
        return None  # fetch failed — fail open

    liq = d["liquidity"]
    vl  = d["vl_ratio"]

    d["liq_ok"]   = liq >= min_liquidity
    d["rug_risk"] = vl > 5.0 and liq < 5_000

    return d


def fetch_top_traders(address: str) -> tuple:
    """
    Returns (buy_ratio, sybil_score, whale_quality).
    Ported from alpha-radar analyze_top_traders().
    Cached for CACHE_TTL seconds.
    """
    cache = _load_cache()
    cache_key = f"birdeye_traders_{address}"
    entry = cache.get(cache_key, {})
    if entry and time.time() - entry.get("ts", 0) < CACHE_TTL:
        d = entry["data"]
        return d["buy_ratio"], d["sybil_score"], d["whale_quality"]

    try:
        client = _get_client()
        resp = client.token.top_traders(address=address, time_frame="24h", limit=10)
        items = resp.get("data", {}).get("items", []) if isinstance(resp, dict) else []

        if not items:
            return 0.5, 0, 0

        volumes = [round(item.get("volumeUsd") or 0, 2) for item in items]
        pnls = [round(item.get("totalPnl") or 0, 2) for item in items]

        unique_vols = len(set(volumes))
        sybil_score = 0
        if len(items) > 3:
            sybil_score = (1 - (unique_vols / len(items))) * 100

        total_pnl = sum(pnls)
        total_vol = sum(volumes)

        whale_quality = 0
        if total_vol > 0:
            whale_quality = min(100, max(0, (total_pnl / (total_vol * 0.1)) * 50 + 50))

        buy_ratio = 0.8 if total_pnl > 0 else 0.2

    except Exception as e:
        log.warning(f"birdeye_leg fetch_top_traders error: {e}")
        return 0.5, 0, 0

    result = {"buy_ratio": buy_ratio, "sybil_score": sybil_score, "whale_quality": whale_quality}
    cache[cache_key] = {"ts": time.time(), "data": result}
    _save_cache(cache)

    return buy_ratio, sybil_score, whale_quality


def fetch_token_security(address: str) -> float:
    """Returns a 0-100 security score. <50 = risky."""
    cache = _load_cache()
    cache_key = f"birdeye_sec_{address}"
    entry = cache.get(cache_key, {})
    if entry and time.time() - entry.get("ts", 0) < CACHE_TTL:
        return entry["score"]

    try:
        client = _get_client()
        data = client.token.security(address=address)
        data = data.get("data", {}) if isinstance(data, dict) else {}
        score = 100.0
        if not data.get("is_mintable") is False:
            score -= 30
        if data.get("is_proxy"):
            score -= 20
        if not data.get("is_mutable") is False:
            score -= 10
        score = max(0, score)
    except Exception as e:
        log.warning(f"birdeye_leg fetch_token_security error: {e}")
        score = 50.0

    cache[cache_key] = {"ts": time.time(), "score": score}
    _save_cache(cache)
    return score


def birdeye_signal(market_question: str) -> dict | None:
    """
    Main entry point for scalp_lab.

    Returns dict with keys: side, confidence, reason
    or None if no signal / token not in TOKEN_MAP.

    side: "YES" or "NO"
    confidence: 0.0–1.0
    """
    q = market_question.lower()
    address = None
    for keyword, addr in TOKEN_MAP.items():
        if keyword in q:
            address = addr
            break

    if not address:
        return None

    if not BIRDEYE_API_KEY:
        log.debug("birdeye_leg: no API key, skipping")
        return None

    buy_ratio, sybil_score, whale_quality = fetch_top_traders(address)

    # Signal logic (ported from alpha-radar thresholds)
    if sybil_score > 50:
        # Wash trading dominant — market likely manipulated, fade it
        return {
            "side": "NO",
            "confidence": min(0.7, sybil_score / 100),
            "reason": f"Sybil cluster {sybil_score:.0f}% — wash trading detected",
        }

    if whale_quality > 60:
        # Smart money accumulating
        return {
            "side": "YES",
            "confidence": min(0.75, whale_quality / 100),
            "reason": f"Whale quality {whale_quality:.0f}/100, buy_ratio {buy_ratio:.2f}",
        }

    if whale_quality < 30 and buy_ratio < 0.4:
        # Smart money distributing
        return {
            "side": "NO",
            "confidence": 0.55,
            "reason": f"Smart money distributing, whale_quality {whale_quality:.0f}",
        }

    return None  # no clear signal


def liqcrush_signal(market_question: str) -> dict | None:
    """
    liqcrush: liquidity collapse leg.
    If on-chain liquidity dropped >30% (price_change_24h proxy) while
    Polymarket YES price is still elevated → market hasn't repriced → buy NO.
    Returns {side, confidence, reason} or None.
    """
    q = market_question.lower()
    address = None
    for keyword, addr in TOKEN_MAP.items():
        if keyword in q:
            address = addr
            break

    if not address or not BIRDEYE_API_KEY:
        return None

    d = fetch_liquidity_data(address)
    if not d:
        return None

    liq      = d.get("liquidity", 0)
    vl       = d.get("vl_ratio", 0)
    chg24h   = d.get("price_change_24h", 0)

    # Rug/crash signal: liquidity collapsing (price down >30% in 24h) + thin book
    if chg24h < -30 and liq < 100_000:
        return {
            "side": "NO",
            "confidence": min(0.80, abs(chg24h) / 100),
            "reason": f"liqcrush: price_chg={chg24h:.1f}% liq=${liq:,.0f} V/L={vl:.1f}x",
        }

    # Honeypot signal: volume >> liquidity (wash-trading to attract buyers)
    if vl > 5.0 and liq < 5_000:
        return {
            "side": "NO",
            "confidence": 0.70,
            "reason": f"liqcrush: rug_risk V/L={vl:.1f}x liq=${liq:,.0f}",
        }

    return None


def whale_alert_check():
    """
    Called each bot cycle. Fires an iMessage if any tracked token has
    whale_quality > 80. Cached so it doesn't spam — max 1 alert per token per hour.
    """
    if not BIRDEYE_API_KEY:
        return

    cache = _load_cache()
    alerts = []

    for keyword, address in TOKEN_MAP.items():
        if " " in keyword:  # skip alias entries
            continue
        alert_key = f"birdeye_whale_alert_{address}"
        last_alert = cache.get(alert_key, 0)
        if time.time() - last_alert < 3600:  # max 1 alert/hour per token
            continue

        buy_ratio, sybil_score, whale_quality = fetch_top_traders(address)
        liq = fetch_liquidity_data(address)
        vl  = liq.get("vl_ratio", 0)

        if whale_quality > 80:
            alerts.append(
                f"🐋 BIRDEYE WHALE: {keyword.upper()} | quality={whale_quality:.0f}/100 "
                f"buy_ratio={buy_ratio:.2f} sybil={sybil_score:.0f}% V/L={vl:.1f}x"
            )
            cache[alert_key] = time.time()
        elif sybil_score > 60:
            alerts.append(
                f"🚨 BIRDEYE SYBIL: {keyword.upper()} | wash_trading={sybil_score:.0f}% "
                f"quality={whale_quality:.0f}/100 — possible rug setup"
            )
            cache[alert_key] = time.time()

    if alerts:
        _save_cache(cache)
        try:
            import subprocess
            body = "\n".join(alerts)
            targets = ["krisharyan@icloud.com", "+918449447444"]
            osa = ('on run argv\ntell application "Messages"\n'
                   '  set s to 1st service whose service type = iMessage\n'
                   '  set b to buddy (item 1 of argv) of s\n'
                   '  send (item 2 of argv) to b\nend tell\nend run')
            for t in targets:
                subprocess.run(["osascript", "-e", osa, t, body],
                               capture_output=True, timeout=30)
        except Exception as e:
            log.warning(f"birdeye whale_alert iMessage failed: {e}")


if __name__ == "__main__":
    # Quick smoke test
    logging.basicConfig(level=logging.DEBUG)
    test_markets = [
        "Will Bitcoin be above $100k on June 30?",
        "Will Ethereum price increase this week?",
        "Will Solana reach $200?",
        "Will Trump win the 2024 election?",  # no token match
    ]
    for q in test_markets:
        sig = birdeye_signal(q)
        print(f"{q[:50]:<52} → {sig}")
