"""
news_leg.py — RSS news signal leg for scalp_lab.

Architecture (ADR-001):
  Fetch → Parse → Entity-match → Score → Gate → Signal

Two tracked legs:
  newscrypto  — BTC/ETH/crypto news vs crypto Polymarket markets
  newsesports — esports match results/upsets vs nearres/esports markets

No external dependencies — stdlib only (urllib, xml.etree, re, json).
Sentiment via a hand-crafted lexicon (no nltk).
Cache in scalp_lab_cache.json (5-min TTL).

Called from scalp_lab.py:
    from news_leg import news_signal
    sig = news_signal(market_question, category)   # → dict or None
"""

import os
import re
import json
import time
import logging
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

log = logging.getLogger(__name__)

CACHE_FILE = os.path.expanduser("~/Documents/polymarket/scalp_lab_cache.json")
CACHE_TTL  = 300    # 5 min — don't re-fetch every cycle
MAX_AGE_H  = 8      # ignore headlines older than 8 hours
MIN_CONF   = 0.55   # minimum sentiment confidence to emit a signal
MIN_EDGE   = 0.07   # |news_prob - market_yes| must exceed this

# ── RSS feeds ─────────────────────────────────────────────────────────────────
RSS_FEEDS = {
    "crypto": [
        "https://cointelegraph.com/rss",
        "https://decrypt.co/feed",
        "https://feeds.bbci.co.uk/news/technology/rss.xml",
    ],
    "esports": [
        "https://www.hltv.org/rss/news",
        "https://cointelegraph.com/rss",   # crypto/esports overlap on token markets
    ],
    "general": [
        "https://feeds.bbci.co.uk/news/world/rss.xml",
    ],
}

# ── Entity map: keyword in headline → internal token ─────────────────────────
# token → what Polymarket market questions say
ENTITY_MAP = {
    # crypto
    "bitcoin":      "BTC", "btc":          "BTC", "satoshi":      "BTC",
    "ethereum":     "ETH", "ether":        "ETH", "vitalik":      "ETH",
    "solana":       "SOL", "solana":       "SOL",
    "xrp":          "XRP", "ripple":       "XRP",
    "dogecoin":     "DOGE","doge":         "DOGE",
    "bnb":          "BNB", "binance":      "BNB",
    # macro
    "federal reserve": "FED", "fed ":      "FED", "fomc":         "FED",
    "interest rate":   "FED", "rate cut":  "FED", "rate hike":    "FED",
    "inflation":    "MACRO",  "cpi":       "MACRO","gdp":         "MACRO",
    # esports games
    "valorant":     "VALORANT","vct":      "VALORANT","vlr":      "VALORANT",
    "counter-strike":"CSGO",  "cs2":       "CSGO",   "csgo":      "CSGO",
    "dota":         "DOTA",   "dota 2":    "DOTA",
    "league of legends":"LOL","esports":   "ESPORTS",
    # major esports teams — map team → ESPORTS so any market question with the team matches
    "team liquid":  "ESPORTS","navi":      "ESPORTS","natus vincere":"ESPORTS",
    "faze":         "ESPORTS","fnatic":    "ESPORTS","vitality":    "ESPORTS",
    "g2 esports":   "ESPORTS","g2 ":       "ESPORTS","astralis":    "ESPORTS",
    "loud":         "ESPORTS","evil geniuses":"ESPORTS","100 thieves":"ESPORTS",
    "cloud9":       "ESPORTS","sentinels": "ESPORTS","nrg":         "ESPORTS",
    "heroic":       "ESPORTS","ence":      "ESPORTS","mouz":        "ESPORTS",
    # market events
    "etf":          "ETF",    "spot etf":  "ETF",    "sec ":      "SEC",
    "hack":         "HACK",   "exploit":   "HACK",   "bridge hack":"HACK",
    "bankruptcy":   "CRASH",  "insolvent": "CRASH",  "collapse":  "CRASH",
    "halving":      "HALVING","all-time high":"ATH", "ath":       "ATH",
}

# Market question → set of tokens it's about
def _question_tokens(q: str, cat: str = "") -> set:
    q_low = " " + q.lower() + " "
    tokens = set()
    for kw, tok in ENTITY_MAP.items():
        if re.search(r'(?<![a-z])' + re.escape(kw.strip()) + r'(?![a-z])', q_low):
            tokens.add(tok)
    # Any esports-category market can match ESPORTS headlines
    if cat == "esports" and not tokens:
        tokens.add("ESPORTS")
    return tokens

# ── Sentiment lexicon ─────────────────────────────────────────────────────────
# (word, direction, weight)  direction: +1=bullish -1=bearish
_LEXICON = {
    # strongly bullish
    "surge": (+1, 0.9), "soar": (+1, 0.9), "rally": (+1, 0.8),
    "breakout": (+1, 0.8), "bullish": (+1, 0.9), "adopt": (+1, 0.7),
    "approval": (+1, 0.8), "approve": (+1, 0.8), "approved": (+1, 0.9),
    "launch": (+1, 0.6), "partnership": (+1, 0.6), "integration": (+1, 0.5),
    "record": (+1, 0.7), "high": (+1, 0.6), "ath": (+1, 0.9),
    "buy": (+1, 0.5), "accumulate": (+1, 0.7), "invest": (+1, 0.5),
    "etf": (+1, 0.7), "institutional": (+1, 0.7), "inflow": (+1, 0.8),
    "upgrade": (+1, 0.6), "beat": (+1, 0.6), "win": (+1, 0.7),
    "victory": (+1, 0.7), "champion": (+1, 0.7), "advance": (+1, 0.6),
    "wins":    (+1, 0.7), "won":      (+1, 0.7), "qualified": (+1, 0.7),
    "qualify": (+1, 0.6), "through":  (+1, 0.4), "top seed":  (+1, 0.6),
    # strongly bearish
    "crash": (-1, 0.9), "plunge": (-1, 0.9), "dump": (-1, 0.8),
    "bearish": (-1, 0.9), "sell": (-1, 0.5), "outflow": (-1, 0.8),
    "hack": (-1, 0.9), "exploit": (-1, 0.9), "breach": (-1, 0.8),
    "ban": (-1, 0.9), "crackdown": (-1, 0.8), "restrict": (-1, 0.7),
    "fine": (-1, 0.6), "lawsuit": (-1, 0.7), "fraud": (-1, 0.9),
    "bankrupt": (-1, 0.9), "insolvent": (-1, 0.9), "collapse": (-1, 0.9),
    "lose": (-1, 0.7), "lost": (-1, 0.7), "eliminated": (-1, 0.8),
    "upset": (-1, 0.7), "defeat": (-1, 0.7), "drop": (-1, 0.6),
    "fall": (-1, 0.5), "concern": (-1, 0.4), "fear": (-1, 0.5),
    "delay": (-1, 0.5), "reject": (-1, 0.8), "deny": (-1, 0.7),
    # negation words — flip the next sentiment token
    "not": None, "no ": None, "never": None, "without": None,
    "fail": (-1, 0.7), "failed": (-1, 0.8), "unable": (-1, 0.6),
}


def _score_sentiment(text: str) -> tuple:
    """
    Returns (direction, confidence) where:
      direction: 1 = bullish, -1 = bearish, 0 = neutral
      confidence: 0.0–1.0

    Simple negation handling: "not rally" → bearish.
    """
    words = re.findall(r"[a-z]+", text.lower())
    score = 0.0
    negate = False
    for word in words:
        if word in ("not", "no", "never", "without", "fail", "failed"):
            negate = True
            continue
        if word in _LEXICON:
            val = _LEXICON[word]
            if val is None:
                continue
            direction, weight = val
            if negate:
                direction = -direction
                negate = False
            score += direction * weight
        else:
            negate = False   # negation only applies to next sentiment word

    if abs(score) < 0.3:
        return 0, 0.0
    direction = 1 if score > 0 else -1
    confidence = min(1.0, abs(score) / 2.0)   # normalise — two strong signals = 1.0
    return direction, confidence


# ── RSS fetcher ───────────────────────────────────────────────────────────────
def _fetch_feed(url: str, timeout: int = 8) -> list:
    """
    Returns list of {title, link, published_ts} dicts.
    Silently returns [] on error.
    """
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "scalp_lab/1.0 (news_leg)"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            xml_bytes = r.read()

        root = ET.fromstring(xml_bytes)
        # Handle both RSS <channel><item> and Atom <entry>
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        items = root.findall(".//item") or root.findall(".//atom:entry", ns)

        result = []
        now = time.time()
        for item in items[:20]:   # cap at 20 items per feed
            title = (item.findtext("title") or
                     item.findtext("atom:title", namespaces=ns) or "")
            link  = (item.findtext("link") or
                     item.findtext("atom:link", namespaces=ns) or "")
            pub   = (item.findtext("pubDate") or
                     item.findtext("dc:date",
                                   namespaces={"dc": "http://purl.org/dc/elements/1.1/"}) or
                     item.findtext("atom:published", namespaces=ns) or "")
            # Parse publish time — try several formats
            ts = now
            for fmt in ("%a, %d %b %Y %H:%M:%S %z",
                        "%a, %d %b %Y %H:%M:%S %Z",
                        "%Y-%m-%dT%H:%M:%S%z",
                        "%Y-%m-%dT%H:%M:%SZ"):
                try:
                    dt = datetime.strptime(pub.strip(), fmt)
                    if dt.tzinfo:
                        ts = dt.timestamp()
                    else:
                        ts = dt.replace(tzinfo=timezone.utc).timestamp()
                    break
                except (ValueError, AttributeError):
                    continue
            result.append({"title": title.strip(), "link": link, "ts": ts})
        return result

    except Exception as e:
        log.debug(f"news_leg feed error {url}: {e}")
        return []


# ── Cache helpers ─────────────────────────────────────────────────────────────
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


def fetch_news(category: str = "crypto") -> list:
    """
    Returns list of recent {title, ts, entities} dicts for the given category.
    Results cached for CACHE_TTL seconds. category: "crypto" | "esports" | "general"
    """
    cache_key = f"news_{category}"
    cache = _load_cache()
    entry = cache.get(cache_key, {})
    if entry and time.time() - entry.get("ts", 0) < CACHE_TTL:
        return entry["items"]

    feeds = RSS_FEEDS.get(category, [])
    items = []
    now = time.time()
    max_age = MAX_AGE_H * 3600

    for url in feeds:
        for item in _fetch_feed(url):
            age = now - item["ts"]
            if age > max_age:
                continue
            # Extract entities — use word-boundary match to avoid " eth " prefix issue
            entities = set()
            title_low = " " + item["title"].lower() + " "  # pad for boundary matching
            for kw, tok in ENTITY_MAP.items():
                # Match keyword as whole word (surrounded by non-alpha or string boundary)
                if re.search(r'(?<![a-z])' + re.escape(kw.strip()) + r'(?![a-z])', title_low):
                    entities.add(tok)
            if not entities:
                continue
            items.append({
                "title":    item["title"],
                "ts":       item["ts"],
                "age_h":    round(age / 3600, 2),
                "entities": list(entities),
            })

    # Dedup by title similarity (exact first word + length bucket)
    seen = set()
    deduped = []
    for item in sorted(items, key=lambda x: x["ts"], reverse=True):
        key = item["title"][:40].lower()
        if key not in seen:
            seen.add(key)
            deduped.append(item)

    cache[cache_key] = {"ts": time.time(), "items": deduped[:30]}
    _save_cache(cache)
    log.debug(f"news_leg fetched {len(deduped)} items for [{category}]")
    return deduped[:30]


# ── Main signal function ───────────────────────────────────────────────────────
def news_signal(market_question: str, market_yes: float,
                category: str = "crypto") -> dict | None:
    """
    Main entry point for scalp_lab.

    Returns {side, confidence, edge, headline, reason} or None.

    side: "YES" | "NO"
    confidence: 0.0–1.0 (sentiment strength × recency)
    edge: abs(news_implied_prob - market_yes)
    headline: triggering headline text
    reason: human-readable explanation

    Logic:
      bullish news + market_yes < 0.60 → buy YES (market underpriced)
      bearish news + market_yes > 0.50 → buy NO  (market overpriced)
    """
    q_tokens = _question_tokens(market_question, category)
    if not q_tokens:
        return None

    items = fetch_news(category)
    if not items:
        return None

    now = time.time()
    best_score = 0.0
    best_item  = None
    best_dir   = 0

    for item in items:
        # Entity overlap: headline must share at least one token with the market
        overlap = q_tokens & set(item["entities"])
        if not overlap:
            continue

        direction, conf = _score_sentiment(item["title"])
        if direction == 0:
            continue

        # Recency decay: exp(-age_h / 2) — half-strength at 2h
        recency = max(0.3, 1.0 - item["age_h"] / (MAX_AGE_H * 2))
        adjusted = conf * recency

        if adjusted > best_score:
            best_score = adjusted
            best_item  = item
            best_dir   = direction

    if best_item is None or best_score < MIN_CONF:
        return None

    # Map news direction to trade side
    # bullish news → expect YES to be correct → only buy YES if market underprices
    # bearish news → expect NO → only buy NO if market overprices YES
    if best_dir == 1:   # bullish
        side = "YES"
        news_implied_prob = min(0.80, market_yes + best_score * 0.20)
        edge = news_implied_prob - market_yes
    else:               # bearish
        side = "NO"
        news_implied_prob = max(0.20, market_yes - best_score * 0.25)
        edge = market_yes - news_implied_prob

    if edge < MIN_EDGE:
        return None

    age_str = f"{best_item['age_h']:.1f}h ago"
    return {
        "side":       side,
        "confidence": round(best_score, 3),
        "edge":       round(edge, 3),
        "headline":   best_item["title"],
        "entities":   best_item["entities"],
        "reason":     (f"news [{category}] {age_str}: conf={best_score:.2f} "
                       f"edge={edge:+.2f} → {side} | {best_item['title'][:80]}"),
    }


def scan_all(markets: list) -> dict:
    """
    Scan all markets and return {market_id: signal} for any that get a news signal.
    Called once per cycle to batch all feed fetches (avoids N×fetch).
    """
    # Pre-fetch both categories
    crypto_items  = fetch_news("crypto")
    esports_items = fetch_news("esports")
    signals = {}
    for m in markets:
        cat  = m.get("cat", "")
        q    = m.get("q", "")
        yes  = m.get("yes", 0.5)
        feed_cat = "esports" if cat == "esports" else "crypto"
        sig = news_signal(q, yes, feed_cat)
        if sig:
            signals[m["id"]] = sig
    return signals


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.DEBUG)

    print("=== CRYPTO NEWS ===")
    items = fetch_news("crypto")
    print(f"fetched {len(items)} items")
    for it in items[:5]:
        d, c = _score_sentiment(it["title"])
        sentiment = {1: "BULL", -1: "BEAR", 0: "NEUTRAL"}[d]
        print(f"  [{sentiment} {c:.2f}] {it['title'][:70]}  ({it['age_h']:.1f}h)")

    print("\n=== ESPORTS NEWS ===")
    items2 = fetch_news("esports")
    print(f"fetched {len(items2)} items")
    for it in items2[:5]:
        d, c = _score_sentiment(it["title"])
        sentiment = {1: "BULL", -1: "BEAR", 0: "NEUTRAL"}[d]
        print(f"  [{sentiment} {c:.2f}] {it['title'][:70]}  ({it['age_h']:.1f}h)")

    print("\n=== SIGNAL TEST ===")
    test_markets = [
        ("Will Bitcoin be above $70k on June 30?",   0.45, "crypto"),
        ("Will Ethereum price increase this week?",  0.52, "crypto"),
        ("Will Team Liquid win their next match?",   0.55, "esports"),
        ("Will NAVI advance to the grand final?",    0.60, "esports"),
    ]
    for q, yes, cat in test_markets:
        sig = news_signal(q, yes, cat)
        print(f"{q[:55]}")
        print(f"  → {sig}")
