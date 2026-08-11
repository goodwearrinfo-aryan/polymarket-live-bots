"""
news_data.py — lightweight news + market-data feed for the Polymarket bot.

No API keys required. Sources:
  • GDELT 2.0 Doc API      — recent global news headlines
      https://api.gdeltproject.org/api/v2/doc/doc
  • CoinGecko simple price — crypto spot price + 24h change
      https://api.coingecko.com/api/v3/simple/price

Design rules:
  • Fail soft. ANY network/parse error returns a NEUTRAL context. The news
    layer must never crash or hang a scan — short timeouts + try/except
    everywhere, results cached so we don't refetch the same market each loop.
  • Cheap. Per-market results are cached for `ttl` seconds; crypto prices for
    a shorter window. A whole scan touches each unique market at most once.

Public surface used by the bot:
  NewsFeed(ttl, log).market_context(title, category) -> dict
  NewsFeed(...).news_signal(title, category)         -> "BUY"/"SELL"/None

market_context() returns:
  {
    "n_articles":      int,      # headlines found in the last 24h
    "sentiment":       float,    # -1.0 .. +1.0  (title lexicon)
    "breaking":        bool,     # lots of very-recent coverage -> volatile
    "momentum":        float|None,  # crypto 24h % change, if a coin matched
    "headline":        str,      # single most recent headline (for logging)
    "confidence_delta":float,    # suggested +/- to apply to a signal's conf
    "source":          str,      # "gdelt"/"coingecko"/"none"
  }
"""

import re
import time
import json
import urllib.parse
import urllib.request
from typing import Optional, List, Dict

_HTTP_TIMEOUT = 6          # seconds; short so a slow source can't stall a scan
_UA = "polymarket-bot-newsfeed/1.0"

# ── tiny headline sentiment lexicon (directional, not exact) ────────────────
_POS = {
    "win", "wins", "winning", "won", "surge", "surges", "soar", "soars", "rally",
    "rallies", "gain", "gains", "rise", "rises", "record", "high", "boost",
    "approve", "approved", "approval", "deal", "agreement", "peace", "breakthrough",
    "success", "advance", "advances", "lead", "leads", "ahead", "favorite", "beat",
    "beats", "strong", "growth", "up", "bullish", "confirmed", "secures",
}
_NEG = {
    "lose", "loses", "losing", "lost", "fall", "falls", "drop", "drops", "plunge",
    "plunges", "crash", "crashes", "slump", "decline", "declines", "fail", "fails",
    "failure", "reject", "rejected", "collapse", "war", "attack", "attacks", "strike",
    "strikes", "ban", "banned", "crisis", "fear", "fears", "weak", "down", "bearish",
    "delay", "delayed", "scandal", "probe", "charged", "resign", "resigns", "ousted",
    "dead", "death", "killed", "threat", "sanction", "sanctions",
}

# market-title scaffolding to strip before building a search query
_STOP = {
    "will", "the", "a", "an", "of", "to", "in", "on", "by", "be", "is", "are",
    "and", "or", "for", "at", "from", "with", "any", "his", "her", "their", "this",
    "that", "after", "before", "out", "up", "down", "vs", "vs.", "win", "wins",
    "above", "below", "than", "june", "july", "may", "march", "april", "august",
    "september", "october", "november", "december", "january", "february",
    "2025", "2026", "30", "31",
}

# crypto title keyword -> CoinGecko id
_COINS = {
    "bitcoin": "bitcoin", "btc": "bitcoin",
    "ethereum": "ethereum", "eth": "ethereum",
    "solana": "solana", "sol": "solana",
    "dogecoin": "dogecoin", "doge": "dogecoin",
    "ripple": "ripple", "xrp": "ripple",
    "cardano": "cardano", "ada": "cardano",
}


class NewsFeed:
    def __init__(self, ttl: int = 900, crypto_ttl: int = 300,
                 max_articles: int = 10, log=None):
        self.ttl = ttl
        self.crypto_ttl = crypto_ttl
        self.max_articles = max_articles
        self.log = log
        self._mkt_cache: Dict[str, tuple] = {}     # title -> (ts, context)
        self._coin_cache: Dict[str, tuple] = {}     # coin_id -> (ts, data)

    # ── low-level HTTP (always fail soft) ──────────────────────────────────
    def _get_json(self, url: str) -> Optional[dict]:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": _UA})
            with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except Exception as e:
            if self.log:
                self.log.debug(f"newsfeed fetch failed ({url[:60]}…): {e}")
            return None

    # ── query building ─────────────────────────────────────────────────────
    @staticmethod
    def _keywords(title: str, limit: int = 4) -> List[str]:
        words = re.findall(r"[A-Za-z][A-Za-z'’]+", title or "")
        kept = [w for w in words if w.lower() not in _STOP and len(w) > 2]
        # prefer capitalised tokens (names/places) but keep order
        caps = [w for w in kept if w[0].isupper()]
        chosen = (caps or kept)[:limit]
        return chosen

    @staticmethod
    def _score(text: str) -> int:
        toks = re.findall(r"[a-z']+", (text or "").lower())
        return sum(1 for t in toks if t in _POS) - sum(1 for t in toks if t in _NEG)

    # ── GDELT headlines ─────────────────────────────────────────────────────
    def headlines(self, title: str) -> List[dict]:
        kws = self._keywords(title)
        if not kws:
            return []
        query = " ".join(kws)
        params = urllib.parse.urlencode({
            "query": query, "mode": "ArtList", "format": "json",
            "maxrecords": self.max_articles, "timespan": "24h", "sort": "datedesc",
        })
        data = self._get_json(f"https://api.gdeltproject.org/api/v2/doc/doc?{params}")
        if not data or not isinstance(data, dict):
            return []
        return data.get("articles") or []

    # ── CoinGecko crypto price ──────────────────────────────────────────────
    def crypto(self, coin_id: str) -> Optional[dict]:
        now = time.time()
        hit = self._coin_cache.get(coin_id)
        if hit and now - hit[0] < self.crypto_ttl:
            return hit[1]
        params = urllib.parse.urlencode({
            "ids": coin_id, "vs_currencies": "usd", "include_24hr_change": "true",
        })
        data = self._get_json(f"https://api.coingecko.com/api/v3/simple/price?{params}")
        out = None
        if data and coin_id in data:
            d = data[coin_id]
            out = {"price": d.get("usd"), "change_24h": d.get("usd_24h_change")}
        self._coin_cache[coin_id] = (now, out)
        return out

    def _coin_for(self, title: str) -> Optional[str]:
        low = (title or "").lower()
        for kw, cid in _COINS.items():
            if re.search(rf"\b{re.escape(kw)}\b", low):
                return cid
        return None

    # ── main entry point ─────────────────────────────────────────────────────
    def market_context(self, title: str, category: str = None) -> dict:
        now = time.time()
        hit = self._mkt_cache.get(title)
        if hit and now - hit[0] < self.ttl:
            return hit[1]

        ctx = {
            "n_articles": 0, "sentiment": 0.0, "breaking": False,
            "momentum": None, "headline": "", "confidence_delta": 0.0,
            "source": "none",
        }

        try:
            arts = self.headlines(title)
            if arts:
                ctx["source"] = "gdelt"
                ctx["n_articles"] = len(arts)
                ctx["headline"] = (arts[0].get("title") or "")[:140]
                scores = [self._score(a.get("title", "")) for a in arts]
                raw = sum(scores)
                # normalise into -1..1 by article count
                ctx["sentiment"] = max(-1.0, min(1.0, raw / max(len(arts), 1)))
                # breaking: heavy coverage in the window = volatile / risky
                recent = 0
                for a in arts:
                    sd = a.get("seendate", "")
                    try:
                        t = time.mktime(time.strptime(sd, "%Y%m%dT%H%M%SZ"))
                        if now - t < 6 * 3600:
                            recent += 1
                    except Exception:
                        pass
                ctx["breaking"] = recent >= max(5, self.max_articles - 1)

            coin = self._coin_for(title) if (category in (None, "crypto") or
                                             self._coin_for(title)) else None
            if coin:
                c = self.crypto(coin)
                if c and c.get("change_24h") is not None:
                    ctx["momentum"] = round(float(c["change_24h"]), 2)
                    if ctx["source"] == "none":
                        ctx["source"] = "coingecko"

            # confidence delta: sentiment nudges, momentum adds for crypto
            delta = ctx["sentiment"] * 0.03
            if ctx["momentum"] is not None:
                delta += max(-0.03, min(0.03, ctx["momentum"] / 100.0))
            if ctx["breaking"]:
                delta -= 0.01   # extra caution amid heavy breaking coverage
            ctx["confidence_delta"] = round(max(-0.05, min(0.05, delta)), 4)
        except Exception as e:
            if self.log:
                self.log.debug(f"market_context error for {title[:40]!r}: {e}")

        self._mkt_cache[title] = (now, ctx)
        return ctx

    # ── optional: news-driven signal (conservative) ──────────────────────────
    def news_signal(self, title: str, category: str = None,
                    min_articles: int = 4, min_abs_sentiment: float = 0.5):
        """
        Returns "BUY" / "SELL" / None. Only fires on clear, well-covered
        sentiment so it doesn't act on noise. Skips when coverage is 'breaking'
        (too volatile to trust a direction).
        """
        ctx = self.market_context(title, category)
        if ctx["n_articles"] < min_articles or ctx["breaking"]:
            return None
        if ctx["sentiment"] >= min_abs_sentiment:
            return "BUY"
        if ctx["sentiment"] <= -min_abs_sentiment:
            return "SELL"
        return None


if __name__ == "__main__":
    import sys
    logging = None
    nf = NewsFeed()
    samples = sys.argv[1:] or [
        "Will the price of Bitcoin be above $66,000 on June 3?",
        "Will the Oklahoma City Thunder win the 2026 NBA Finals?",
        "US x Iran permanent peace deal by May 31, 2026?",
    ]
    for s in samples:
        ctx = nf.market_context(s)
        print(f"\n{s}\n  {json.dumps(ctx, ensure_ascii=False)}")
        print(f"  news_signal -> {nf.news_signal(s)}")
