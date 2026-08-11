#!/usr/bin/env python3
"""goodwearr_news_digest.py — India D2C / apparel news digest for the Good Wearr store.

NOT a trading bot. A keyless business-intelligence digest: it watches India
e-commerce / D2C / activewear-apparel news relevant to Good Wearr (goodwearr.in —
India activewear + swimwear D2C), dedupes, scores each headline for relevance,
mirrors a rolling digest into Obsidian, and iMessage-alerts ONLY the high-relevance
items (competitor moves, sale-event windows, COD/logistics-cost shifts, ad-platform
changes) so a genuinely actionable headline reaches Aryan while he's offline.

Keyless: Google News RSS keyword search (change q= for any topic) + Bing News RSS as
a second aggregator. stdlib only. Read-only to the store — it never touches products,
orders, or spend.

Discipline: relevance is a crude keyword score, NOT a judgement of impact. A high score
is "worth a look", not "do something". Dedup per link so nothing re-alerts.

Usage: python3 goodwearr_news_digest.py once | report
"""
import os, sys, json, time, re, html, subprocess, urllib.request, urllib.error
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "goodwearr_news_state.json")
LOG = os.path.join(HERE, "goodwearr_news_log.jsonl")
VAULT = os.path.expanduser("~/Documents/PolymarketVault/Reports/goodwearr_news.md")
UA = {"User-Agent": "Mozilla/5.0 (goodwearr-news-digest; keyless research)"}

ALERT_TARGETS = ("krisharyan@icloud.com", "+918449447444")
ALERT_MIN_SCORE = 4          # only genuinely-relevant items page; digest keeps the rest

# search topics -> Google News RSS query. Broad enough to catch the sector, narrow
# enough to stay on Good Wearr's world (India activewear/swimwear D2C).
QUERIES = [
    "India activewear brand",
    "India athleisure market",
    "India D2C apparel funding",
    "Blissclub OR Kica OR Cultsport OR Snitch OR Bewakoof",
    "Myntra OR Ajio OR Nykaa fashion sale",
    "India apparel cotton price",
    "India ecommerce COD return RTO",
    "India festive sale ecommerce",
]

# relevance lexicon: each hit adds weight. Competitor / direct-threat terms weigh most.
WEIGHTS = [
    (re.compile(r"\b(blissclub|kica|cultsport|snitch|bewakoof|the souled store|"
                r"jockey|decathlon india)\b", re.I), 3),
    (re.compile(r"\b(activewear|athleisure|leggings|sportswear|swimwear|gym wear)\b", re.I), 2),
    (re.compile(r"\b(d2c|direct.to.consumer|shopify|goKwik)\b", re.I), 2),
    (re.compile(r"\b(cotton|fabric|textile|logistics|freight|shipping cost)\b", re.I), 2),
    (re.compile(r"\b(cod|cash on delivery|rto|return to origin|prepaid)\b", re.I), 2),
    (re.compile(r"\b(sale|discount|festive|eors|big billion|great indian)\b", re.I), 1),
    (re.compile(r"\b(meta ads|instagram ads|cpm|performance marketing|roas)\b", re.I), 2),
    (re.compile(r"\b(funding|raises?|series [abc]|valuation|acquire)\b", re.I), 1),
    (re.compile(r"\bindia\b", re.I), 1),
]


def _get(url, timeout=20):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _gnews(q):
    from urllib.parse import quote
    return ("https://news.google.com/rss/search?q=" + quote(q)
            + "&hl=en-IN&gl=IN&ceid=IN:en")


def _bing(q):
    from urllib.parse import quote
    return "https://www.bing.com/news/search?q=" + quote(q) + "&format=rss"


def _parse_feed(url):
    try:
        root = ET.fromstring(_get(url))
    except Exception:
        return []
    out = []
    for item in root.iter():
        if item.tag.split("}")[-1] not in ("item", "entry"):
            continue
        title = link = pub = None
        for ch in item:
            ctag = ch.tag.split("}")[-1]
            if ctag == "title":
                title = (ch.text or "").strip()
            elif ctag == "link":
                link = (ch.text or "").strip() or ch.attrib.get("href", "")
            elif ctag in ("pubDate", "published", "updated"):
                pub = (ch.text or "").strip()
        if not title or not link:
            continue
        epoch = None
        if pub:
            try:
                epoch = int(parsedate_to_datetime(pub).timestamp())
            except Exception:
                epoch = None
        out.append((html.unescape(re.sub(r"\s+", " ", title)), link, epoch))
    return out


def _score(title):
    return sum(w for rx, w in WEIGHTS if rx.search(title))


def _load_state():
    try:
        return json.load(open(STATE))
    except Exception:
        return {"seen": []}


def _save_state(st):
    st["seen"] = st["seen"][-6000:]
    tmp = STATE + ".tmp"
    json.dump(st, open(tmp, "w"))
    os.replace(tmp, STATE)


def _append_log(rec):
    with open(LOG, "a") as f:
        f.write(json.dumps(rec) + "\n")


def alert(msg):
    subprocess.run(["osascript", "-e",
                    f'display notification "{msg}" with title "Good Wearr news LEAD"'],
                   capture_output=True)
    for t in ALERT_TARGETS:
        subprocess.run(["osascript", "-e",
                        f'tell application "Messages" to send "{msg}" to buddy "{t}" '
                        'of (service 1 whose service type is iMessage)'],
                       capture_output=True)


def run_once():
    st = _load_state()
    seen = set(st["seen"])
    now = int(time.time())
    fresh = []
    for q in QUERIES:
        for feed in (_gnews(q), _bing(q)):
            for title, link, epoch in _parse_feed(feed):
                if link in seen:
                    continue
                seen.add(link)
                sc = _score(title)
                if sc <= 0:
                    continue
                rec = {"t": now, "pub": epoch, "score": sc,
                       "title": title[:240], "link": link, "q": q}
                fresh.append(rec)
                _append_log(rec)

    fresh.sort(key=lambda r: r["score"], reverse=True)
    alerted = 0
    for r in fresh:
        if r["score"] >= ALERT_MIN_SCORE:
            alert(f"[{r['score']}] {r['title'][:150]}")
            alerted += 1
            if alerted >= 5:                 # cap pages per run so a busy hour can't spam
                break
    st["seen"] = list(seen)
    _save_state(st)
    write_report()
    print(f"goodwearr_news once: +{len(fresh)} new relevant, {alerted} alerted -> {VAULT}")


def _read_log():
    rows = []
    try:
        for line in open(LOG):
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    except FileNotFoundError:
        pass
    return rows


def write_report():
    rows = _read_log()
    rows.sort(key=lambda r: r["t"], reverse=True)
    os.makedirs(os.path.dirname(VAULT), exist_ok=True)
    lines = [
        "# Good Wearr — India D2C / Apparel News Digest",
        "",
        "type: report",
        "status: intel",
        "",
        f"_Keyless Google/Bing News. {len(rows)} logged items. Relevance is a crude "
        "keyword score (worth-a-look, not impact). Read-only to the store._",
        "",
        "## Most recent (top 60)",
        "",
        "| when | score | headline |",
        "| --- | --- | --- |",
    ]
    from datetime import datetime
    for r in rows[:60]:
        when = datetime.fromtimestamp(r["t"]).strftime("%m-%d %H:%M")
        title = r["title"].replace("|", "\\|")
        lines.append(f"| {when} | {r['score']} | [{title}]({r['link']}) |")
    lines.append("")
    tmp = VAULT + ".tmp"
    open(tmp, "w").write("\n".join(lines))
    os.replace(tmp, VAULT)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "once"
    if cmd == "report":
        write_report()
        print(f"goodwearr_news report: {len(_read_log())} items -> {VAULT}")
    else:
        run_once()


if __name__ == "__main__":
    main()
