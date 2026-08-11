#!/usr/bin/env python3
"""
feeds.py — unified external-data layer for the bot's feature store.

Sources (all free tier):
  • CoinGecko     — crypto spot + 24h change            (no key)
  • yfinance      — stocks/indices/FX historical+quote   (no key; pip install yfinance)
  • Alpha Vantage — market data                          (free key)
  • Finnhub       — real-time-ish quotes                 (free key)
  • NewsAPI       — news headlines                       (free key)

API keys: put them in feeds_keys.json (created on first run) OR env vars
(ALPHAVANTAGE_KEY, FINNHUB_KEY, NEWSAPI_KEY). Everything is FAIL-SOFT: a missing
key or network error returns None and never raises, so the bot is never blocked.

CLI:
  python3 feeds.py status            # show which feeds are live vs need a key
  python3 feeds.py crypto bitcoin
  python3 feeds.py stock AAPL
  python3 feeds.py news "iran nuclear deal"
"""
import os, sys, json, urllib.parse, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
KEYS_FILE = os.path.join(HERE, "feeds_keys.json")
UA = "polymarket-feeds/1.0"
TIMEOUT = 8


def _keys():
    k = {"ALPHAVANTAGE_KEY": "", "FINNHUB_KEY": "", "NEWSAPI_KEY": ""}
    if os.path.exists(KEYS_FILE):
        try:
            k.update({kk: vv for kk, vv in json.load(open(KEYS_FILE)).items() if vv})
        except Exception:
            pass
    for name in k:
        if os.environ.get(name):
            k[name] = os.environ[name]
    return k


def _seed_keys_file():
    if not os.path.exists(KEYS_FILE):
        json.dump({
            "_help": "Paste your free API keys below, then save. Get them at: "
                     "alphavantage.co/support/#api-key , finnhub.io/register , newsapi.org/register",
            "ALPHAVANTAGE_KEY": "", "FINNHUB_KEY": "", "NEWSAPI_KEY": ""},
            open(KEYS_FILE, "w"), indent=2)


def _get(url, params=None):
    if params:
        url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception as e:
        sys.stderr.write(f"[feeds] {url[:50]} failed: {e}\n")
        return None


# ── keyless ─────────────────────────────────────────────────────────────────
def coingecko(coin_id="bitcoin"):
    d = _get("https://api.coingecko.com/api/v3/simple/price",
             {"ids": coin_id, "vs_currencies": "usd", "include_24hr_change": "true"})
    if d and coin_id in d:
        return {"price": d[coin_id].get("usd"), "change_24h": d[coin_id].get("usd_24h_change")}
    return crypto_fallback(coin_id)   # CoinGecko throttled → keyless coinpaprika → coinlore


# ── keyless crypto-price fallback (coinpaprika → coinlore) ────────────────────
# So the crypto signal never goes blind when CoinGecko's free tier 429s.
# Maps built + price-VERIFIED against CoinGecko 2026-06-20 (a symbol match is not
# enough — TON's symbol collides with Tokamak/Gram, so it is deliberately omitted:
# a missing coin degrades gracefully, a WRONG coin corrupts the signal).
# coinpaprika is precise across the roster (primary). coinlore rounds sub-cent
# prices to ~1 sig-fig, so it carries only the large caps whose price matched
# CoinGecko within 5% (secondary, last-resort).
_BROWSER_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"

_CG_TO_PAPRIKA = {
    "bitcoin": "btc-bitcoin", "ethereum": "eth-ethereum", "solana": "sol-solana",
    "binancecoin": "bnb-binance-coin", "dogecoin": "doge-dogecoin", "ripple": "xrp-xrp",
    "cardano": "ada-cardano", "avalanche-2": "avax-avalanche", "polkadot": "dot-polkadot-token",
    "chainlink": "link-chainlink", "litecoin": "ltc-litecoin", "stellar": "xlm-stellar",
    "tron": "trx-tron", "shiba-inu": "shib-shiba-inu", "sui": "sui-sui",
    "near": "near-near-protocol", "cosmos": "atom-cosmos", "uniswap": "uni-uniswap",
    "aptos": "apt-aptos", "arbitrum": "arb-arbitrum", "optimism": "op-optimism",
    "pepe": "pepe-pepe", "dogwifhat": "wif-dogwifcoin", "bonk": "bonk-bonk",
    "floki": "floki-floki-inu",
}
_CG_TO_LORE = {  # coinlore numeric ids — large caps verified within 5% of CoinGecko
    "bitcoin": "90", "ethereum": "80", "solana": "48543", "binancecoin": "2710",
    "dogecoin": "2", "ripple": "58", "cardano": "257", "avalanche-2": "44883",
    "polkadot": "45219", "chainlink": "2751", "litecoin": "1", "stellar": "89",
    "tron": "2713", "sui": "93845", "near": "48563", "cosmos": "33830",
    "uniswap": "47305", "aptos": "111341", "arbitrum": "93847",
}


def _get_browser(url):
    """Like _get but with a browser UA — coinpaprika/coinlore 403 non-browser agents."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _BROWSER_UA})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception as e:
        sys.stderr.write(f"[feeds] fallback {url[:48]} failed: {e}\n")
        return None


def _paprika(cg_id):
    cid = _CG_TO_PAPRIKA.get(cg_id)
    if not cid:
        return None
    d = _get_browser(f"https://api.coinpaprika.com/v1/tickers/{cid}")
    q = (d or {}).get("quotes", {}).get("USD") if isinstance(d, dict) else None
    if q and q.get("price") is not None:
        return {"price": q["price"], "change_24h": q.get("percent_change_24h")}
    return None


def _coinlore(cg_id):
    cid = _CG_TO_LORE.get(cg_id)
    if not cid:
        return None
    d = _get_browser(f"https://api.coinlore.net/api/ticker/?id={cid}")
    if isinstance(d, list) and d:
        try:
            return {"price": float(d[0]["price_usd"]),
                    "change_24h": float(d[0].get("percent_change_24h", 0))}
        except Exception:
            return None
    return None


def crypto_fallback(cg_id):
    """Keyless {price, change_24h} for a CoinGecko coin_id via coinpaprika → coinlore.
    Returns None if neither source covers the coin (graceful degradation)."""
    return _paprika(cg_id) or _coinlore(cg_id)


def crypto_multi_cg_shape(cg_ids):
    """Fallback for MANY coins, returned in CoinGecko simple/price shape
    {cg_id: {"usd": price, "usd_24h_change": pct}} so a caller can drop it in
    where a CoinGecko response is expected. Skips coins with no fallback."""
    out = {}
    for cg in cg_ids:
        r = crypto_fallback(cg)
        if r and r.get("price") is not None:
            out[cg] = {"usd": r["price"], "usd_24h_change": r.get("change_24h")}
    return out


def binance(symbol="BTCUSDT"):
    """Live price + 24h change + order-book imbalance from Binance public API (no key)."""
    t = _get("https://api.binance.com/api/v3/ticker/24hr", {"symbol": symbol})
    if not t or "lastPrice" not in t:
        return None
    out = {"price": float(t["lastPrice"]), "change_24h_pct": float(t.get("priceChangePercent", 0)),
           "volume": float(t.get("volume", 0)), "imbalance": None, "spread": None}
    d = _get("https://api.binance.com/api/v3/depth", {"symbol": symbol, "limit": 20})
    try:
        if d and d.get("bids") and d.get("asks"):
            bid_d = sum(float(p) * float(q) for p, q in d["bids"])
            ask_d = sum(float(p) * float(q) for p, q in d["asks"])
            tot = bid_d + ask_d
            best_bid = float(d["bids"][0][0]); best_ask = float(d["asks"][0][0])
            out["imbalance"] = round((bid_d - ask_d) / tot, 4) if tot else 0.0
            out["spread"] = round(best_ask - best_bid, 2)
    except Exception:
        pass
    return out


def binance_klines(symbol="BTCUSDT", interval="1h", limit=24):
    """Recent candles -> [(close_time_sec, close_price)] for momentum features."""
    d = _get("https://api.binance.com/api/v3/klines",
             {"symbol": symbol, "interval": interval, "limit": limit})
    if not isinstance(d, list):
        return []
    return [(int(k[6] // 1000), float(k[4])) for k in d]


def stock(ticker):
    """Quote + recent change via yfinance (self-installs)."""
    try:
        import yfinance
    except ImportError:
        import subprocess
        subprocess.run([sys.executable, "-m", "pip", "install", "yfinance",
                        "--break-system-packages", "-q"], check=False)
        try:
            import yfinance
        except Exception:
            return None
    try:
        h = yfinance.Ticker(ticker).history(period="5d")
        if h is None or h.empty:
            return None
        last = float(h["Close"].iloc[-1])
        prev = float(h["Close"].iloc[-2]) if len(h) > 1 else last
        return {"price": round(last, 4), "change_pct": round((last - prev) / prev * 100, 2) if prev else 0.0}
    except Exception:
        return None


# ── key-gated ────────────────────────────────────────────────────────────────
def alpha_vantage(symbol):
    key = _keys()["ALPHAVANTAGE_KEY"]
    if not key:
        return None
    d = _get("https://www.alphavantage.co/query",
             {"function": "GLOBAL_QUOTE", "symbol": symbol, "apikey": key})
    q = (d or {}).get("Global Quote", {})
    if q:
        try:
            return {"price": float(q.get("05. price", 0)),
                    "change_pct": float(str(q.get("10. change percent", "0")).rstrip("%"))}
        except Exception:
            return None
    return None


def finnhub(symbol):
    key = _keys()["FINNHUB_KEY"]
    if not key:
        return None
    d = _get("https://finnhub.io/api/v1/quote", {"symbol": symbol, "token": key})
    if d and "c" in d:
        return {"price": d.get("c"), "change_pct": d.get("dp")}
    return None


def newsapi(query, page_size=10):
    key = _keys()["NEWSAPI_KEY"]
    if not key:
        return None
    d = _get("https://newsapi.org/v2/everything",
             {"q": query, "pageSize": page_size, "sortBy": "publishedAt",
              "language": "en", "apiKey": key})
    arts = (d or {}).get("articles")
    return [{"title": a.get("title"), "source": (a.get("source") or {}).get("name"),
             "publishedAt": a.get("publishedAt")} for a in arts] if arts else None


def status():
    k = _keys()
    print("Feed status:")
    print("  CoinGecko (crypto)     : LIVE (no key)")
    print("  Binance (price+book)   : LIVE (no key)")
    print("  yfinance (stocks/FX)   : LIVE (no key; installs on first use)")
    print(f"  Alpha Vantage          : {'LIVE' if k['ALPHAVANTAGE_KEY'] else 'NEEDS KEY -> alphavantage.co/support/#api-key'}")
    print(f"  Finnhub                : {'LIVE' if k['FINNHUB_KEY'] else 'NEEDS KEY -> finnhub.io/register'}")
    print(f"  NewsAPI                : {'LIVE' if k['NEWSAPI_KEY'] else 'NEEDS KEY -> newsapi.org/register'}")
    print(f"\nPaste keys into: {KEYS_FILE}")


if __name__ == "__main__":
    _seed_keys_file()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "status":
        status()
    elif cmd == "crypto":
        print(coingecko(sys.argv[2] if len(sys.argv) > 2 else "bitcoin"))
    elif cmd == "stock":
        print(stock(sys.argv[2]))
    elif cmd == "news":
        r = newsapi(sys.argv[2]) if len(sys.argv) > 2 else None
        print(json.dumps(r, indent=2) if r else "no key / no results")
    else:
        print(__doc__)
