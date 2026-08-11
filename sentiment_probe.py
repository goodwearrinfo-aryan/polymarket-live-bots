#!/usr/bin/env python3
"""sentiment_probe.py — News sentiment + Wikipedia attention per Polymarket market.

For every live market (vol >= threshold):
  1. Google News RSS headline count + sentiment (positive vs negative tone words)
     for the market's 2-4 keyword query. Fresh news velocity = attention signal.
  2. Wikipedia page-view spike for named entities in the question.
     Spike > 2x prior-7d avg = something notable just happened to this entity.

Composite score:
  sentiment_score = (pos_headlines - neg_headlines) / max(total_headlines, 1)
    +1 = all positive   0 = neutral   -1 = all negative
  attention_score = max wiki spike ratio across entities (1.0 = no spike)
  signal_strength = abs(sentiment_score) * log1p(headline_count) * attention_score

High signal_strength + clear direction = candidate for analyst review.

Read-only (no trades). Run every 3h via watchdog; Obsidian export each run.
Usage:
    python3 sentiment_probe.py        # scan + export
    python3 sentiment_probe.py report # reprint last cached results
"""

import json, math, os, re, sys, time, urllib.request, urllib.parse
from concurrent.futures import ThreadPoolExecutor

HERE   = os.path.dirname(os.path.abspath(__file__))
CACHE  = os.path.join(HERE, "sentiment_cache.json")
VAULT  = os.path.expanduser("~/Documents/PolymarketVault/Reports/sentiment_probe.md")
UA     = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}

GAMMA        = "https://gamma-api.polymarket.com/markets"
GNEWS        = "https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"
WIKI_API     = ("https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article"
                "/en.wikipedia/all-access/all-agents/{slug}/daily/{s}/{e}")

# ── Category-routed RSS feeds (2026-06-15: expanded from single Google News) ─
_FEEDS = {
    "crypto":   [GNEWS, "https://www.coindesk.com/arc/outboundfeeds/rss/"],
    "sports":   [GNEWS, "https://www.espn.com/espn/rss/news"],
    "politics": [GNEWS, "http://feeds.bbci.co.uk/news/rss.xml",
                 "https://www.aljazeera.com/xml/rss/all.xml",
                 "https://www.theguardian.com/world/rss"],
    "tech":     [GNEWS, "http://feeds.bbci.co.uk/news/technology/rss.xml",
                 "https://www.theguardian.com/technology/rss"],
    "general":  [GNEWS, "http://feeds.bbci.co.uk/news/rss.xml",
                 "https://www.theguardian.com/world/rss"],
}
_CRYPTO_KW  = {"bitcoin","btc","ethereum","eth","solana","sol","crypto","doge",
               "bnb","xrp","ripple","coinbase","binance","defi","nft","altcoin",
               "token","blockchain","above","below","reach","price","up or down"}
_SPORTS_KW  = {"world cup","nfl","nba","mlb","nhl","soccer","football","tennis",
               "golf","cricket","esports","champions league","premier league",
               "mls","tournament","championship","playoff","season","league",
               "match","team","player","coach","fifa","euro","olympics"}
_TECH_KW    = {"openai","chatgpt","anthropic","google","apple","microsoft","meta",
               "nvidia","startup","ipo","ai ","gpt","llm","model","tech","software"}

MIN_VOL       = 30_000    # Polymarket volume floor
POLY_PAGES    = 6         # pages × 100 markets (~600 candidates)
WORKERS       = 10        # concurrent RSS + wiki fetches
NEWS_MAX_AGE  = 48        # hours — only headlines this fresh count
WIKI_SPIKE_X  = 2.0       # view ratio to flag as attention spike
ALERT_GAP     = 0.40      # |sentiment| threshold for iMessage alert

# ── tone word lists (expand as needed) ──────────────────────────────────────
_POS = set("win wins won victory agree deal breakthrough approve pass sign "
           "surge rise rally gain record advance confirm approve peace resolve "
           "breakthrough alliance support boost recover upgrade upgrade approval "
           "leads leading leads leading".split())
_NEG = set("lose loses lost defeat collapse fail crisis resign arrest charge "
           "ban block reject crash plunge fall decline war attack bomb kill "
           "suspend veto sanction impeach indict fraud scandal delay crisis "
           "default bankrupt strike protest conflict invasion".split())

_STOP = set("will the a an of to in on for and or be is are at as from this that "
            "which who when by with have has what does do more most than its win "
            "2025 2026 2027 year above below within between reach over under into "
            "before after during market price level billion million".split())


def _get(url, timeout=10):
    try:
        req = urllib.request.Request(url, headers=UA)
        return urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "replace")
    except Exception:
        return ""


def _categorize(question):
    """Categorize a market question for RSS feed routing."""
    q = question.lower()
    if any(k in q for k in _CRYPTO_KW):
        return "crypto"
    if any(k in q for k in _SPORTS_KW):
        return "sports"
    if any(k in q for k in _TECH_KW):
        return "tech"
    if any(w in q for w in ("president","senate","congress","election","trump",
                             "vote","ukraine","russia","iran","gaza","war","ceasefire",
                             "minister","parliament","treaty","nato","sanction")):
        return "politics"
    return "general"


def _keywords(question):
    """2-4 meaningful keywords from a market question."""
    words = re.findall(r"[a-zA-Z]{4,}", question)
    out = [w for w in words if w.lower() not in _STOP]
    return " ".join(out[:4]) if out else question[:40]


def _entities(question):
    """Capitalized proper noun phrases (≥2 words or ≥6 chars)."""
    caps = re.findall(r"\b([A-Z][a-z]{3,}(?:\s+[A-Z][a-z]{3,})*)\b", question)
    stop = {"Will", "This", "When", "What", "Does", "Which", "Bitcoin", "Crypto",
            "Year", "June", "July", "August", "September", "January", "First",
            "World", "United", "States", "Market", "Price"}
    seen, out = set(), []
    for e in caps:
        if e not in stop and e not in seen and len(e) >= 5:
            seen.add(e); out.append(e)
    return out[:3]


# ── Google News RSS ──────────────────────────────────────────────────────────

def _parse_rss(xml):
    """Return list of (title, pub_timestamp) from RSS XML."""
    items = re.findall(r"<item>(.*?)</item>", xml, re.DOTALL)
    out = []
    for item in items:
        title_m = re.search(r"<title>(.*?)</title>", item, re.DOTALL)
        date_m  = re.search(r"<pubDate>(.*?)</pubDate>", item)
        if not title_m:
            continue
        title = re.sub(r"<[^>]+>", "", title_m.group(1)).strip()
        ts = 0
        if date_m:
            try:
                ts = int(time.mktime(time.strptime(
                    date_m.group(1)[:25].strip(), "%a, %d %b %Y %H:%M:%S")))
            except Exception:
                pass
        out.append((title, ts))
    return out


def _sentiment(titles):
    """Return (total, positive, negative, neutral, score) from a list of titles."""
    total = pos = neg = 0
    for title in titles:
        words = set(re.findall(r"[a-z]{4,}", title.lower()))
        p = len(words & _POS); n = len(words & _NEG)
        pos += p; neg += n; total += 1
    score = (pos - neg) / max(pos + neg, 1)
    return total, pos, neg, score


def news_signal(question):
    """Fetch category-routed RSS feeds and return combined headline sentiment."""
    cat  = _categorize(question)
    q    = urllib.parse.quote(_keywords(question))
    feeds = _FEEDS.get(cat, _FEEDS["general"])
    all_items, seen = [], set()
    for feed_url in feeds:
        url = feed_url.format(q=q) if "{q}" in feed_url else feed_url
        xml = _get(url, timeout=10)
        for title, ts in _parse_rss(xml):
            key = title[:40].lower()
            if key not in seen:
                seen.add(key); all_items.append((title, ts))
    cutoff = time.time() - NEWS_MAX_AGE * 3600
    fresh = [(t, ts) for t, ts in all_items if ts == 0 or ts >= cutoff]
    titles = [t for t, _ in fresh]
    total, pos, neg, score = _sentiment(titles)
    return {"count": len(all_items), "pos": pos, "neg": neg, "score": round(score, 3),
            "fresh_count": len(fresh), "category": cat,
            "sample": [t[:70] for t in titles[:2]]}


# ── Wikipedia attention ──────────────────────────────────────────────────────

def _wiki_views(entity, days_ago_start, days_ago_end):
    slug = urllib.parse.quote(entity.replace(" ", "_"))
    def fmt(d): return time.strftime("%Y%m%d", time.gmtime(time.time() - d * 86400))
    url = WIKI_API.format(slug=slug, s=fmt(days_ago_end), e=fmt(days_ago_start))
    d = _get(url, timeout=8)
    if not d:
        return []
    try:
        return [it["views"] for it in json.loads(d).get("items", [])]
    except Exception:
        return []


def wiki_signal(question):
    """Return best spike ratio across named entities in the question."""
    best = {"entity": "", "ratio": 1.0, "recent": 0, "prior": 0}
    for ent in _entities(question):
        recent = _wiki_views(ent, 0, 7)
        prior  = _wiki_views(ent, 7, 14)
        if not recent or not prior:
            continue
        r_avg = sum(recent) / len(recent)
        p_avg = sum(prior)  / len(prior)
        if p_avg < 300:
            continue
        ratio = r_avg / p_avg
        if ratio > best["ratio"]:
            best = {"entity": ent, "ratio": round(ratio, 2),
                    "recent": int(r_avg), "prior": int(p_avg)}
    return best


# ── Polymarket feed ──────────────────────────────────────────────────────────

def poly_markets():
    out = []
    for pg in range(POLY_PAGES):
        batch = _get(f"{GAMMA}?active=true&closed=false&order=volume&ascending=false"
                     f"&limit=100&offset={pg*100}", timeout=15)
        if not batch:
            break
        for m in (json.loads(batch) if batch else []):
            vol = float(m.get("volume") or m.get("volumeNum") or 0)
            if vol < MIN_VOL:
                continue
            op = m.get("outcomePrices")
            if isinstance(op, str):
                try: op = json.loads(op)
                except: op = None
            if not op:
                continue
            yes = float(op[0])
            if not (0.03 <= yes <= 0.97):
                continue
            out.append({"id": m.get("id"), "question": m.get("question", ""),
                        "yes": yes, "vol": vol})
    return out


# ── combined probe ───────────────────────────────────────────────────────────

def probe_market(pm):
    news = news_signal(pm["question"])
    wiki = wiki_signal(pm["question"])
    signal = abs(news["score"]) * math.log1p(news["fresh_count"]) * wiki["ratio"]
    return {**pm, "news": news, "wiki": wiki, "signal": round(signal, 4)}


def scan():
    print("[sentiment] fetching Polymarket markets...", flush=True)
    mkts = poly_markets()
    print(f"[sentiment] {len(mkts)} markets to probe", flush=True)

    results = []
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for r in ex.map(probe_market, mkts):
            results.append(r)

    results.sort(key=lambda x: -x["signal"])
    out = {"ts": int(time.time()), "markets": results, "scanned": len(results)}
    tmp = CACHE + ".tmp"
    json.dump(out, open(tmp, "w"), indent=1)
    os.replace(tmp, CACHE)
    # quick summary
    notable = [r for r in results if r["signal"] >= 0.1]
    print(f"[sentiment] {len(notable)} markets with signal>=0.1 "
          f"(top: {results[0]['question'][:45] if results else 'none'})", flush=True)
    return out


# ── Obsidian export ──────────────────────────────────────────────────────────

def export_obsidian(data):
    ts    = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(data.get("ts", 0)))
    mkts  = data.get("markets", [])
    notable = [m for m in mkts if m["signal"] >= 0.05]

    L = [
        "# Sentiment Probe — News Velocity + Wikipedia Attention",
        "",
        f"> Updated {ts} · {data.get('scanned',0)} markets scanned",
        "> **signal_strength** = |sentiment| × log(headline_count) × wiki_spike_ratio",
        "> High signal = strong directional news coverage + unusual attention spike.",
        "",
    ]

    if notable:
        L += ["## Notable Markets (signal ≥ 0.05)", "",
              "| Signal | Sentiment | News | Wiki spike | Poly YES | Market |",
              "|---|---|---|---|---|---|"]
        for m in notable[:25]:
            ns = m["news"]; ws = m["wiki"]
            sent_label = "🟢 bullish" if ns["score"] > 0.1 else ("🔴 bearish" if ns["score"] < -0.1 else "⚪ neutral")
            spike_str  = f"{ws['ratio']}× {ws['entity'][:12]}" if ws["ratio"] >= WIKI_SPIKE_X else f"{ws['ratio']}×"
            cat = ns.get("category", "")
            L.append(f"| {m['signal']:.3f} | {sent_label} | {ns['fresh_count']} hdln ({cat}) | "
                     f"{spike_str} | {m['yes']:.0%} | {m['question'][:52]} |")
        L.append("")
    else:
        L += ["## Notable Markets", "_No markets above threshold this cycle_", ""]

    # Sample headlines for the top 3
    if mkts:
        L += ["## Sample Headlines (top 3 by signal)", ""]
        for m in mkts[:3]:
            ns = m["news"]
            L.append(f"**{m['question'][:70]}** (Poly {m['yes']:.0%})")
            L.append(f"*sentiment={ns['score']:+.2f} headlines={ns['fresh_count']}*")
            for s in ns.get("sample", [])[:2]:
                L.append(f"- {s}")
            L.append("")

    L += [
        "## Interpretation",
        "",
        "- **High signal + bullish score**: positive news flow may push YES higher — "
          "check if Poly price already reflects it.",
        "- **High signal + bearish score**: negative coverage may push YES lower.",
        "- **Wiki spike (>2×) + stale Poly price**: attention event not yet priced.",
        "- **Low signal**: market is quiet / no fresh coverage — lower confidence in any move.",
    ]

    os.makedirs(os.path.dirname(VAULT), exist_ok=True)
    open(VAULT, "w").write("\n".join(L) + "\n")
    print(f"[sentiment] Obsidian → {VAULT}")


# ── iMessage alert ────────────────────────────────────────────────────────────

_OSA = ('on run argv\n'
        'tell application "Messages"\n'
        '  set s to 1st service whose service type = iMessage\n'
        '  set b to buddy (item 1 of argv) of s\n'
        '  send (item 2 of argv) to b\n'
        'end tell\n'
        'end run')

def _imsg(body):
    import subprocess
    for t in ["+918449447444"]:
        try:
            r = subprocess.run(["osascript", "-e", _OSA, t, body],
                               capture_output=True, text=True, timeout=30)
            if r.returncode != 0:
                sys.stderr.write(f"[imsg FAIL] {r.stderr.strip()}\n")
        except Exception as e:
            sys.stderr.write(f"[imsg FAIL] {e}\n")


def alert_notable(data):
    mkts = data.get("markets", [])
    lines = []
    for m in mkts[:20]:
        ns = m["news"]
        ws = m["wiki"]
        if abs(ns["score"]) >= ALERT_GAP and ns["fresh_count"] >= 3:
            dir_s = "📈 BULLISH" if ns["score"] > 0 else "📉 BEARISH"
            lines.append(f"{dir_s} ({ns['score']:+.2f}) | {m['question'][:45]}\n"
                         f"   Poly={m['yes']:.0%}  {ns['fresh_count']} headlines")
        if ws["ratio"] >= 3.0 and ws["entity"]:
            lines.append(f"⚠️ Wiki spike {ws['ratio']}× on {ws['entity'][:20]}\n"
                         f"   {m['question'][:45]} (Poly {m['yes']:.0%})")
    if lines:
        _imsg("SENTIMENT PROBE\n" + "\n".join(lines[:4]))


def _write_graphify_node(data):
    ts   = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(data.get("ts", 0)))
    mkts = data.get("markets", [])
    L = [f"# Sentiment Probe Findings — {ts}", "",
         "Signal source: category-routed RSS feeds (CoinDesk, ESPN, BBC, Al Jazeera, "
         "Google News) + Wikipedia entity attention per Polymarket market.", "",
         "## Top Sentiment Signals", ""]
    for m in mkts[:15]:
        ns = m["news"]
        ws = m.get("wiki", {})
        direction = "bullish" if ns["score"] > 0.1 else ("bearish" if ns["score"] < -0.1 else "neutral")
        L.append(f"- **{m['question']}** (Poly {m['yes']:.0%}): "
                 f"{direction} sentiment score {ns['score']:+.2f}, "
                 f"{ns['fresh_count']} fresh headlines, "
                 f"category={m.get('news',{}).get('category','?')}")
        if ws.get("ratio", 1) >= 2.0:
            L.append(f"  - Wikipedia entity **{ws['entity']}** spiked {ws['ratio']}×")
    content = "\n".join(L) + "\n"
    open(os.path.join(HERE, "sentiment_findings.md"), "w").write(content)
    vault = os.path.expanduser("~/Documents/PolymarketVault/Reports/sentiment_findings.md")
    os.makedirs(os.path.dirname(vault), exist_ok=True)
    open(vault, "w").write(content)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "once"
    if cmd == "report":
        if os.path.exists(CACHE):
            data = json.load(open(CACHE))
            export_obsidian(data)
            mkts = data.get("markets", [])
            print(f"Last scan: {time.strftime('%H:%M', time.gmtime(data['ts']))} UTC — "
                  f"{len(mkts)} markets")
            for m in mkts[:8]:
                ns = m["news"]
                print(f"  {m['signal']:.3f} | sent={ns['score']:+.2f} n={ns['fresh_count']} | "
                      f"{m['question'][:55]}")
        else:
            print("No cache yet — run without args first")
        return
    data = scan()
    export_obsidian(data)
    alert_notable(data)
    _write_graphify_node(data)


if __name__ == "__main__":
    main()
