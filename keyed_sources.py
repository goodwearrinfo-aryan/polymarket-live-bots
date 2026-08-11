#!/usr/bin/env python3
"""
keyed_sources.py — keyed (API-key-gated) data fetchers; the keyed complement to the
KEYLESS data_sources.py registry. Kept SEPARATE on purpose: data_sources.py guarantees
keyless GETs, so keyed providers must NOT leak into it. This module wraps the handful of
free-tier APIs that need a key.

Design (mirrors llm_client.py conventions):
  - stdlib only (urllib), no external deps
  - keys read from this dir's .env (never hardcoded)
  - FAIL-SOFT: a missing key returns {"ok": False, "configured": False, "error": ...}
    instead of raising, so the module is safe to import/use BEFORE all keys are added
  - browser User-Agent (some providers sit behind Cloudflare -> 403 error 1010 on
    the default python-urllib signature)

Configured today (live):  TAVILY_API_KEY
Pending (just add the key to .env — NO code change needed):
  FINNHUB_API_KEY, COINGECKO_API_KEY (demo), MARKETAUX_API_KEY

Usage:
  import keyed_sources as K
  K.status()                           # which keyed APIs are live vs pending (no network)
  K.tavily_search("polymarket btc")    # AI web search
  K.finnhub_quote("AAPL")              # real-time stock quote
  K.coingecko_price(["bitcoin"])       # crypto spot (keyless fallback if no demo key)
  K.marketaux_news(symbols="AAPL")     # finance news + sentiment
  $ python3 keyed_sources.py           # prints the live/pending status table
"""
import os, json, urllib.request, urllib.parse, urllib.error

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def _load_env(path=None):
    """Seed os.environ from .env without overriding real env (mirror llm_client)."""
    path = path or os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    except FileNotFoundError:
        pass


_load_env()

# provider label -> (key env var, signup URL) — drives status() + pending reporting
KEYED = {
    "tavily":       ("TAVILY_API_KEY",       "https://app.tavily.com"),
    "finnhub":      ("FINNHUB_API_KEY",      "https://finnhub.io/register"),
    "coingecko":    ("COINGECKO_API_KEY",    "https://www.coingecko.com/en/developers/dashboard"),
    "marketaux":    ("MARKETAUX_API_KEY",    "https://www.marketaux.com"),
    "indiankanoon": ("INDIANKANOON_TOKEN",   "https://api.indiankanoon.org"),
}


def _key(name):
    return os.environ.get(KEYED[name][0], "").strip()


def _nokey(name):
    env, signup = KEYED[name]
    return {"ok": False, "configured": False,
            "error": f"{env} not set in .env (sign up at {signup})"}


def _err(e):
    if isinstance(e, urllib.error.HTTPError):
        try:
            detail = e.read().decode()[:200]
        except Exception:
            detail = ""
        return {"ok": False, "configured": True, "error": f"HTTP {e.code} {detail}"}
    return {"ok": False, "configured": True, "error": f"{type(e).__name__}: {e}"}


def _get(url, headers=None, timeout=30):
    h = {"User-Agent": _UA, "Accept": "application/json"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def _post(url, body, headers=None, timeout=30):
    h = {"User-Agent": _UA, "Content-Type": "application/json", "Accept": "application/json"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


# ---- Tavily: AI web search (LIVE) ----
def tavily_search(query, max_results=5, depth="basic"):
    k = _key("tavily")
    if not k:
        return _nokey("tavily")
    try:
        d = _post("https://api.tavily.com/search",
                  {"query": query, "max_results": max_results, "search_depth": depth},
                  headers={"Authorization": f"Bearer {k}"})
        return {"ok": True, "results": d.get("results", []), "answer": d.get("answer")}
    except Exception as e:
        return _err(e)


# ---- Finnhub: real-time stock quote / company news ----
def finnhub_quote(symbol):
    k = _key("finnhub")
    if not k:
        return _nokey("finnhub")
    try:
        d = _get(f"https://finnhub.io/api/v1/quote?symbol={urllib.parse.quote(symbol)}&token={k}")
        return {"ok": True, "symbol": symbol, "quote": d}  # keys: c,d,dp,h,l,o,pc,t
    except Exception as e:
        return _err(e)


def finnhub_news(category="general"):
    k = _key("finnhub")
    if not k:
        return _nokey("finnhub")
    try:
        d = _get(f"https://finnhub.io/api/v1/news?category={urllib.parse.quote(category)}&token={k}")
        return {"ok": True, "news": (d[:20] if isinstance(d, list) else d)}
    except Exception as e:
        return _err(e)


# ---- CoinGecko: crypto price (demo key -> higher limits; keyless fallback) ----
def coingecko_price(ids, vs="usd"):
    if isinstance(ids, (list, tuple)):
        ids = ",".join(ids)
    k = _key("coingecko")
    url = (f"https://api.coingecko.com/api/v3/simple/price?ids={urllib.parse.quote(ids)}"
           f"&vs_currencies={urllib.parse.quote(vs)}&include_24hr_change=true")
    headers = {"x-cg-demo-api-key": k} if k else None
    try:
        d = _get(url, headers=headers)
        return {"ok": True, "configured": bool(k), "prices": d,
                "note": None if k else "keyless fallback (no COINGECKO_API_KEY)"}
    except Exception as e:
        return _err(e)


# ---- Marketaux: finance news + sentiment ----
def marketaux_news(symbols=None, search=None, limit=10):
    k = _key("marketaux")
    if not k:
        return _nokey("marketaux")
    q = {"api_token": k, "limit": str(limit), "language": "en"}
    if symbols:
        q["symbols"] = symbols
    if search:
        q["search"] = search
    url = "https://api.marketaux.com/v1/news/all?" + urllib.parse.urlencode(q)
    try:
        d = _get(url)
        return {"ok": True, "data": d.get("data", [])}
    except Exception as e:
        return _err(e)


def _ik_post(path, body_str, tok):
    """POST to IndianKanoon with form-encoded body (not JSON)."""
    h = {"User-Agent": _UA, "Authorization": f"Token {tok}",
         "Content-Type": "application/x-www-form-urlencoded"}
    req = urllib.request.Request(
        f"https://api.indiankanoon.org{path}",
        data=body_str.encode(),
        headers=h,
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def indiankanoon_search(query, pagenum=0):
    """Search IndianKanoon — 1.1M+ Indian court judgments and acts.
    Returns {found, docs: [{tid, title, doctype, publishdate, headline, docsource}]}."""
    tok = _key("indiankanoon")
    if not tok:
        return _nokey("indiankanoon")
    body = f"formInput={urllib.parse.quote(query)}&pagenum={pagenum}"
    return _ik_post("/search/", body, tok)


def indiankanoon_doc(docid):
    """Fetch full text of a specific IndianKanoon document by tid."""
    tok = _key("indiankanoon")
    if not tok:
        return _nokey("indiankanoon")
    return _ik_post(f"/doc/{docid}/", "", tok)


def status():
    """Report which keyed APIs are live vs pending (no network calls)."""
    return {name: {"configured": bool(_key(name)), "env": env, "signup": signup}
            for name, (env, signup) in KEYED.items()}


if __name__ == "__main__":
    print("keyed_sources status:")
    for name, s in status().items():
        flag = "LIVE   " if s["configured"] else "PENDING"
        tail = "" if s["configured"] else "-> " + s["signup"]
        print(f"  [{flag}] {name:10} {s['env']:20} {tail}")
