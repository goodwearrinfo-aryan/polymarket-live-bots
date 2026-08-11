"""
sports_data.py — keyless all-sports results + news feed for the lab.

Source: ESPN's public site.api.espn.com scoreboard JSON (no key) + ESPN RSS news feeds.
Pulls the last N days across all major leagues, enriches events with team news snippets,
and writes a normalized results file the bot can read for resolution / favorite-outcome
confirmation plus news sentiment.

ESPN covers traditional sports (NBA/NFL/MLB/NHL/soccer/MMA/...). It does NOT cover
esports — that stays on PandaScore (the proven nearres edge). This is the
complementary general-sports + news feed.

  python3 sports_data.py            # last 30 days, all leagues -> sports_last30d.json
  python3 sports_data.py --days 7   # shorter window
  python3 sports_data.py --league nba   # one league
"""
import json, sys, time, urllib.request, xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
import re

BASE = "https://site.api.espn.com/apis/site/v2/sports"

# (key, sport, league-path) — keyless ESPN scoreboard endpoints.
LEAGUES = [
    ("nba",        "basketball", "nba"),
    ("wnba",       "basketball", "wnba"),
    ("ncaab",      "basketball", "mens-college-basketball"),
    ("nfl",        "football",   "nfl"),
    ("ncaaf",      "football",   "college-football"),
    ("mlb",        "baseball",   "mlb"),
    ("nhl",        "hockey",     "nhl"),
    ("ufc",        "mma",        "ufc"),
    ("boxing",     "boxing",     "boxing"),
    ("epl",        "soccer",     "eng.1"),
    ("laliga",     "soccer",     "esp.1"),
    ("seriea",     "soccer",     "ita.1"),
    ("bundesliga", "soccer",     "ger.1"),
    ("ligue1",     "soccer",     "fra.1"),
    ("ucl",        "soccer",     "uefa.champions"),
    ("mls",        "soccer",     "usa.1"),
]


def _get(url, tries=3):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as r:
                return json.loads(r.read())
        except Exception:
            if i == tries - 1:
                return None
            time.sleep(1.5)


def _normalize(event, league, news_by_team=None):
    comp = (event.get("competitions") or [{}])[0]
    cs = comp.get("competitors") or []
    home = next((c for c in cs if c.get("homeAway") == "home"), {})
    away = next((c for c in cs if c.get("homeAway") == "away"), {})
    status = event.get("status", {}).get("type", {})
    completed = bool(status.get("completed"))
    hs = _num(home.get("score"))
    as_ = _num(away.get("score"))
    winner = None
    if completed and hs is not None and as_ is not None:
        winner = "home" if hs > as_ else "away" if as_ > hs else "draw"
    # ESPN sometimes ships a moneyline/spread under competitions[0].odds
    odds = ((comp.get("odds") or [{}])[0]) or {} if comp.get("odds") else {}
    home_abbr = home.get("team", {}).get("abbreviation")
    away_abbr = away.get("team", {}).get("abbreviation")
    news_by_team = news_by_team or {}
    news = []
    for abbr in (home_abbr, away_abbr):
        if abbr and abbr in news_by_team:
            news.extend(news_by_team[abbr][:2])  # top 2 per team
    return {
        "league": league,
        "date": event.get("date", "")[:10],
        "id": event.get("id"),
        "name": event.get("name"),
        "home": home_abbr,
        "away": away_abbr,
        "home_score": hs,
        "away_score": as_,
        "status": status.get("name"),
        "completed": completed,
        "winner": winner,
        "fav_spread": odds.get("details"),          # e.g. "NYK -4.5"
        "over_under": odds.get("overUnder"),
        "news": news,  # [{headline, link}, ...]
    }


def _num(x):
    try:
        return int(x)
    except (TypeError, ValueError):
        try:
            return float(x)
        except (TypeError, ValueError):
            return None


def _fetch_rss(url):
    """Fetch and parse RSS feed. Returns list of (title, pub_date, link) or empty."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            root = ET.fromstring(r.read())
            items = []
            for item in root.findall(".//item"):
                title = item.findtext("title") or ""
                pubdate = item.findtext("pubDate") or ""
                link = item.findtext("link") or ""
                items.append((title, pubdate, link))
            return items
    except Exception:
        return []


def _get_news(league_key, days=3):
    """Fetch ESPN news headlines for a league via public API endpoint.
    Returns {team -> [news items]}. Gracefully returns {} on API unavailability."""
    # ESPN exposes news via the public site.api.espn.com/news endpoints
    news_urls = {
        "nba": f"{BASE}/basketball/nba/news",
        "wnba": f"{BASE}/basketball/wnba/news",
        "nfl": f"{BASE}/football/nfl/news",
        "mlb": f"{BASE}/baseball/mlb/news",
        "nhl": f"{BASE}/hockey/nhl/news",
        "ufc": f"{BASE}/mma/ufc/news",
        "epl": f"{BASE}/soccer/eng.1/news",
        "mls": f"{BASE}/soccer/usa.1/news",
    }
    url = news_urls.get(league_key)
    if not url:
        return {}
    try:
        data = _get(url)
        if not data or "articles" not in data:
            return {}
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        by_team = {}
        for article in data.get("articles", [])[:50]:  # top 50 articles
            headline = article.get("headline", "")
            links = article.get("links", [{}])
            link = links[0].get("href", "") if links else ""
            pubdate_str = article.get("published", "")
            # Extract team abbrevs from headline (heuristic: all-caps 2-4 letter abbrevs)
            abbrevs = re.findall(r"\b([A-Z]{2,4})\b", headline)
            for abbrev in abbrevs:
                by_team.setdefault(abbrev, []).append({
                    "headline": headline[:100],
                    "link": link[:200] if link else "",
                    "published": pubdate_str[:10] if pubdate_str else "",
                })
        return by_team
    except Exception:
        return {}  # graceful degradation


def collect(days=30, only=None):
    today = datetime.now(timezone.utc).date()
    out = []
    leagues = [l for l in LEAGUES if (only is None or l[0] == only)]
    # Pre-fetch news once per league to avoid redundant fetches
    news_cache = {}
    for key, sport, path in leagues:
        news_cache[key] = _get_news(key, days=3)
        time.sleep(0.1)
    for d in range(days):
        day = today - timedelta(days=d)
        ymd = day.strftime("%Y%m%d")
        for key, sport, path in leagues:
            data = _get(f"{BASE}/{sport}/{path}/scoreboard?dates={ymd}")
            if not data:
                continue
            for ev in data.get("events", []):
                out.append(_normalize(ev, key, news_cache.get(key, {})))
            time.sleep(0.1)
    # dedup by event id (multi-day events can repeat)
    seen, deduped = set(), []
    for r in out:
        if r["id"] in seen:
            continue
        seen.add(r["id"])
        deduped.append(r)
    return deduped


def load(path="sports_last30d.json"):
    """Bot-facing loader. Returns the payload dict (events under ['events'])."""
    import os
    if not os.path.exists(path):
        return {"events": [], "stale": True}
    return json.load(open(path, encoding="utf-8"))


def result_index(path="sports_last30d.json"):
    """{(league, home, away, date) -> winner} for O(1) resolution lookup, plus a
    looser {team -> [wins, losses]} form tally the bot can use as a covariate."""
    pay = load(path)
    idx, form = {}, {}
    for e in pay.get("events", []):
        if not e.get("completed") or not e.get("winner"):
            continue
        idx[(e["league"], e["home"], e["away"], e["date"])] = e["winner"]
        hw = e["winner"] == "home"
        for team, won in ((e["home"], hw), (e["away"], not hw and e["winner"] != "draw")):
            if not team:
                continue
            f = form.setdefault(team, [0, 0])
            f[0 if won else 1] += 1
    return idx, form


if __name__ == "__main__":
    days = 30
    only = None
    if "--days" in sys.argv:
        days = int(sys.argv[sys.argv.index("--days") + 1])
    if "--league" in sys.argv:
        only = sys.argv[sys.argv.index("--league") + 1]
    rows = collect(days, only)
    completed = [r for r in rows if r["completed"]]
    by_league = {}
    for r in rows:
        by_league.setdefault(r["league"], 0)
        by_league[r["league"]] += 1
    payload = {
        "source": "espn-site-api (keyless)",
        "pulled_at": datetime.now(timezone.utc).isoformat(),
        "window_days": days,
        "total_events": len(rows),
        "completed_events": len(completed),
        "by_league": by_league,
        "events": rows,
    }
    out_f = "sports_last30d.json" if only is None else f"sports_{only}_{days}d.json"
    json.dump(payload, open(out_f, "w"), indent=1)
    print(f"wrote {out_f}: {len(rows)} events ({len(completed)} completed) across "
          f"{len(by_league)} leagues")
    for k, n in sorted(by_league.items(), key=lambda kv: -kv[1]):
        print(f"  {k:12s} {n}")
