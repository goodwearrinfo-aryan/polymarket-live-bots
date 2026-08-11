#!/usr/bin/env python3
"""
hermes_news.py — local keyless RSS news feed service (OpenAlice's sibling).
Ported from oss-bots/Hermes news_feed.py DESIGN (60+ RSS, minute polling,
dedupe-by-id) but stdlib-only (urllib + xml.etree — no requests/feedparser)
to match the lab idiom. PAPER research support only.

Serves the same shape as OpenAlice (:47331/api/news):
  GET http://127.0.0.1:48580/api/news  ->  {"items":[{"title","link","source","first_seen_utc"}]}
  GET http://127.0.0.1:48580/api/health -> {"ok":true,"items":N,"feeds_ok":K,"last_poll":...}

Feeds: world/geopolitics + markets (the quietfade conflict vocabulary lives in
world headlines; crypto headlines come from fetch_crypto_headlines separately).
Keeps 48h of headlines, max 800. Polls every 120s in a daemon thread.

Run:  python3 hermes_news.py            (foreground; launchd job runs this)
"""
import json, threading, time, hashlib, sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler

PORT      = 48580   # 4733x range squatted by a node service on this Mac
POLL_SEC  = 120
MAX_ITEMS = 800
MAX_AGE_H = 48
UA        = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36")

# Curated from Hermes DEFAULT_FEEDS + world/geopolitics additions.
FEEDS = [
    "http://feeds.bbci.co.uk/news/world/rss.xml",
    "https://www.theguardian.com/world/rss",
    "https://feeds.npr.org/1004/rss.xml",
    "https://www.aljazeera.com/xml/rss/all.xml",
    "https://www.ft.com/world?format=rss",
    "https://feeds.bloomberg.com/markets/news.rss",
    "https://feeds.bloomberg.com/economics/news.rss",
    "https://www.ft.com/markets?format=rss",
    "http://feeds.bbci.co.uk/news/business/rss.xml",
    "https://www.theguardian.com/business/rss",
    "https://finance.yahoo.com/news/rss",
    "https://feeds.npr.org/1014/rss.xml",
    "https://www.theguardian.com/us-news/rss",
    "https://www.federalreserve.gov/feeds/press_all.xml",
    "https://techcrunch.com/feed/",
    "https://feeds.bloomberg.com/technology/news.rss",
    "https://feeds.content.dowjones.io/public/rss/mw_topstories",
    "https://feeds.content.dowjones.io/public/rss/mw_bulletins",
    "https://feeds.wsjonline.com/wsj/economics/feed",
    "https://www.ft.com/news-feed?format=rss",
    "https://feeds.npr.org/1006/rss.xml",
    "https://www.theverge.com/rss/index.xml",
    "https://www.investing.com/rss/news.rss",
    "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",
    "https://feeds.a.dj.com/rss/WSJcomUSBusiness.xml",
    "https://www.ft.com/companies?format=rss",
    "https://www.ft.com/global-economy?format=rss",
    "https://www.ft.com/technology?format=rss",
    "https://www.ft.com/world/us?format=rss",
    "https://www.theguardian.com/business/economics/rss",
    "https://feeds.marketwatch.com/marketwatch/marketpulse/",
    "https://www.investors.com/feed/",
    "https://feeds.arstechnica.com/arstechnica/index",
    "https://www.wired.com/feed/rss",
    "https://www.federalreserve.gov/feeds/press_monetary.xml",
    "https://apps.bea.gov/rss/rss.xml",
    "https://www.sec.gov/news/pressreleases.rss",
    "https://www.cftc.gov/RSS/RSSGP/rssgp.xml",
    "https://www.cftc.gov/RSS/RSSENF/rssenf.xml",
    "https://www.prnewswire.com/rss/news-releases-list.rss",
    "https://rss.globenewswire.com/rssfeed/",
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://cointelegraph.com/rss",
    "https://decrypt.co/feed",
    "https://www.cnbc.com/id/100003114/device/rss/rss.html",
    "https://www.cnbc.com/id/10000664/device/rss/rss.html",
    "https://feeds.skynews.com/feeds/rss/world.xml",
    "https://moxie.foxnews.com/google-publisher/world.xml",
    "https://feeds.nbcnews.com/nbcnews/public/world",
    "https://abcnews.go.com/abcnews/internationalheadlines",
    "https://www.cbsnews.com/latest/rss/world",
    "https://www.dexerto.com/feed/",
    "https://dotesports.com/feed",
]

_lock  = threading.Lock()
_items = {}          # id -> item dict
_stats = {"last_poll": None, "feeds_ok": 0, "polls": 0}


def _fetch(url, timeout=10):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except Exception:
        return None


def _parse_rss(data, source_url):
    """Titles+links from RSS 2.0 <item> or Atom <entry>. Lenient."""
    out = []
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        try:  # strip junk before first '<'
            txt = data.decode("utf-8", "ignore")
            root = ET.fromstring(txt[txt.index("<"):])
        except Exception:
            return out
    ns = {"a": "http://www.w3.org/2005/Atom"}
    nodes = root.iter("item")
    entries = list(nodes) or root.findall(".//a:entry", ns)
    for e in entries:
        title = e.findtext("title") or e.findtext("a:title", namespaces=ns) or ""
        link  = e.findtext("link") or ""
        if not link:
            ln = e.find("a:link", ns)
            link = ln.get("href", "") if ln is not None else ""
        guid  = e.findtext("guid") or e.findtext("a:id", namespaces=ns) or link or title
        title = " ".join(title.split())
        if not title:
            continue
        out.append({
            "id":    hashlib.sha256(guid.encode()).hexdigest()[:24],
            "title": title,
            "link":  link.strip(),
            "source": source_url.split("/")[2],
        })
    return out


def _poll_once():
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    ok = 0
    fresh = []
    from concurrent.futures import ThreadPoolExecutor
    def _one(url):
        data = _fetch(url)
        return _parse_rss(data, url) if data else []
    with ThreadPoolExecutor(max_workers=8) as ex:
        for got in ex.map(_one, FEEDS):
            if got:
                ok += 1
                fresh.extend(got)
    cutoff = time.time() - MAX_AGE_H * 3600
    with _lock:
        for it in fresh:
            if it["id"] not in _items:
                it["first_seen_utc"] = now_iso
                it["_ts"] = time.time()
                _items[it["id"]] = it
        # evict old + cap size (newest kept)
        for k in [k for k, v in _items.items() if v["_ts"] < cutoff]:
            del _items[k]
        if len(_items) > MAX_ITEMS:
            for k, _ in sorted(_items.items(), key=lambda kv: kv[1]["_ts"])[:len(_items) - MAX_ITEMS]:
                del _items[k]
        _stats.update({"last_poll": now_iso, "feeds_ok": ok})
        _stats["polls"] += 1


def _poll_loop():
    while True:
        try:
            _poll_once()
        except Exception as e:
            sys.stderr.write(f"[hermes] poll error: {e}\n")
        time.sleep(POLL_SEC)


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/api/news"):
            with _lock:
                items = sorted(_items.values(), key=lambda v: v["_ts"], reverse=True)
                out = [{k: v for k, v in it.items() if k != "_ts"} for it in items]
            self._json({"items": out})
        elif self.path.startswith("/api/health"):
            with _lock:
                self._json({"ok": _stats["polls"] > 0, "items": len(_items), **_stats})
        else:
            self._json({"error": "unknown path"}, 404)


if __name__ == "__main__":
    threading.Thread(target=_poll_loop, daemon=True).start()
    print(f"hermes_news serving on http://127.0.0.1:{PORT}/api/news ({len(FEEDS)} feeds, poll {POLL_SEC}s)")
    HTTPServer.allow_reuse_address = True
    HTTPServer(("127.0.0.1", PORT), H).serve_forever()
