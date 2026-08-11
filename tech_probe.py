#!/usr/bin/env python3
"""tech_probe.py — GitHub Trending + Guardian RSS × Polymarket tech/AI markets.

Two attention signals for tech and AI Polymarket markets:
  1. GitHub Trending (top 15 repos today): if a repo's name/desc overlaps with
     a tech/AI Polymarket market, it signals developer-community attention before
     mainstream media catches up.
  2. Guardian RSS (tech + world + science, last 24h): structured news that often
     correlates with real-money prediction markets earlier than crowd pricing.

Cross-reference against live Polymarket markets filtered to tech/AI/science
keywords. Report matches for analyst review.

Read-only, no trades, no API keys. Paper research only.

Usage:
    python3 tech_probe.py          # scan + export
    python3 tech_probe.py report   # reprint cached results
"""

import json, os, re, sys, time, urllib.request, urllib.parse
from concurrent.futures import ThreadPoolExecutor

HERE  = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "tech_cache.json")
VAULT = os.path.expanduser("~/Documents/PolymarketVault/Reports/tech_probe.md")

FINDINGS_LOCAL = os.path.join(HERE, "tech_findings.md")
FINDINGS_VAULT = os.path.expanduser("~/Documents/PolymarketVault/Reports/tech_findings.md")

UA        = {"User-Agent": "Mozilla/5.0"}
GAMMA     = "https://gamma-api.polymarket.com/markets"
GH_TREND  = "https://github.com/trending"

GUARDIAN_FEEDS = [
    "https://www.theguardian.com/technology/rss",
    "https://www.theguardian.com/world/rss",
    "https://www.theguardian.com/science/rss",
]

TECH_KEYWORDS = [
    "ai", "artificial intelligence", "gpt", "openai", "anthropic", "google",
    "apple", "microsoft", "meta", "nvidia", "spacex", "tesla", "github",
    "model", "llm", "robot", "quantum", "chip", "semiconductor", "startup", "ipo",
]

POLY_PAGES = 2   # 200 markets (limit=200 in single call per spec)
WORKERS    = 6

_STOP = {
    "will", "the", "a", "an", "of", "to", "in", "on", "for", "and", "or",
    "be", "is", "are", "at", "as", "from", "this", "that", "which", "who",
    "when", "by", "with", "have", "has", "what", "does", "do", "more", "most",
    "than", "its", "win", "wins", "hit", "reach", "end", "2025", "2026", "2027",
    "year", "over", "under", "about", "into", "them", "they", "their", "been",
    "after", "before", "such", "some", "would", "could", "should", "then",
    "than", "also", "even", "both", "each",
}


# ── HTTP helpers ──────────────────────────────────────────────────────────────

def _get_raw(url, timeout=15):
    """Fetch raw bytes. Returns b'' on any error."""
    try:
        req = urllib.request.Request(url, headers=UA)
        return urllib.request.urlopen(req, timeout=timeout).read()
    except Exception:
        return b""


def _get_json(url, timeout=15):
    """Fetch + decode JSON. Returns None on error."""
    raw = _get_raw(url, timeout)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


# ── Source 1: GitHub Trending ─────────────────────────────────────────────────

def fetch_github_trending(top=15):
    """Parse github.com/trending HTML for top repos. Returns list of dicts."""
    html = _get_raw(GH_TREND, timeout=20).decode("utf-8", "replace")
    if not html:
        print("[tech] WARN: GitHub trending fetch failed", flush=True)
        return []

    repos = []
    # Split on article blocks
    articles = re.split(r'<article[^>]*class="[^"]*Box-row[^"]*"', html)
    for block in articles[1:]:  # skip pre-first-article prefix
        # Repo name: href="/owner/repo" inside h2.h3.lh-condensed
        name_m = re.search(
            r'<h2[^>]*class="[^"]*lh-condensed[^"]*".*?href="(/[^"]+)"',
            block, re.DOTALL
        )
        if not name_m:
            # fallback: any href with exactly two path segments (owner/repo)
            name_m = re.search(r'href="(/[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+)"', block)
        if not name_m:
            continue
        name = name_m.group(1).lstrip("/")

        # Description: <p class="col-9 color-fg-muted ...">
        desc_m = re.search(
            r'<p[^>]*class="[^"]*col-9[^"]*color-fg-muted[^"]*"[^>]*>(.*?)</p>',
            block, re.DOTALL
        )
        desc = ""
        if desc_m:
            desc = re.sub(r"<[^>]+>", "", desc_m.group(1)).strip()

        # Stars today: "N stars today" near float-sm-right span
        stars_m = re.search(
            r'<span[^>]*class="[^"]*float-sm-right[^"]*"[^>]*>.*?'
            r'([\d,]+)\s*stars\s*today',
            block, re.DOTALL | re.IGNORECASE
        )
        # Fallback — just find any "N stars today" in the block
        if not stars_m:
            stars_m = re.search(r'([\d,]+)\s*stars\s*today', block, re.IGNORECASE)
        stars_today = 0
        if stars_m:
            try:
                stars_today = int(stars_m.group(1).replace(",", ""))
            except ValueError:
                stars_today = 0

        if name and "/" in name:
            repos.append({"name": name, "desc": desc, "stars_today": stars_today})

        if len(repos) >= top:
            break

    print(f"[tech] GitHub trending: {len(repos)} repos", flush=True)
    return repos


# ── Source 2: Guardian RSS ────────────────────────────────────────────────────

_RSS_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _parse_rfc822(date_str):
    """Parse RFC-822 pubDate to Unix timestamp. Returns 0 on failure."""
    # Example: Mon, 16 Jun 2026 12:34:00 GMT
    try:
        m = re.search(
            r'(\d{1,2})\s+([A-Za-z]{3})\s+(\d{4})\s+(\d{2}):(\d{2}):(\d{2})',
            date_str
        )
        if not m:
            return 0
        day, mon_s, yr, hh, mm, ss = m.groups()
        mon = _RSS_MONTHS.get(mon_s.lower(), 0)
        if not mon:
            return 0
        import calendar
        t = calendar.timegm((int(yr), mon, int(day), int(hh), int(mm), int(ss), 0, 0, 0))
        return t
    except Exception:
        return 0


def fetch_guardian_items(max_age_h=24):
    """Fetch tech + world + science Guardian RSS feeds. Return last max_age_h items."""
    cutoff = time.time() - max_age_h * 3600
    seen_titles = set()
    items = []

    for url in GUARDIAN_FEEDS:
        raw = _get_raw(url, timeout=12).decode("utf-8", "replace")
        if not raw:
            print(f"[tech] WARN: Guardian feed failed: {url}", flush=True)
            continue

        # Extract <item> blocks
        for block in re.findall(r"<item>(.*?)</item>", raw, re.DOTALL):
            title_m = re.search(r"<title>(.*?)</title>", block, re.DOTALL)
            date_m  = re.search(r"<pubDate>(.*?)</pubDate>", block, re.DOTALL)
            if not title_m:
                continue
            title = re.sub(r"<!\[CDATA\[|\]\]>|<[^>]+>", "", title_m.group(1)).strip()
            # Dedup by first 40 chars of title
            key = title[:40].lower()
            if key in seen_titles:
                continue
            seen_titles.add(key)
            ts = _parse_rfc822(date_m.group(1)) if date_m else 0
            if ts and ts < cutoff:
                continue  # too old
            items.append({"title": title, "ts": ts})

    # Sort newest first
    items.sort(key=lambda x: -x["ts"])
    print(f"[tech] Guardian items (last 24h): {len(items)}", flush=True)
    return items


# ── Polymarket feed (tech/AI filter) ─────────────────────────────────────────

def _yes_price(m):
    op = m.get("outcomePrices")
    if isinstance(op, str):
        try:
            op = json.loads(op)
        except Exception:
            return None
    return float(op[0]) if op else None


def poly_tech_markets():
    """Fetch Polymarket markets filtered to tech/AI/science topics."""
    # Single call with limit=200 per spec
    batch = _get_json(
        f"{GAMMA}?active=true&closed=false&order=volume&ascending=false&limit=200",
        timeout=20
    )
    if not batch:
        print("[tech] WARN: Polymarket gamma fetch failed", flush=True)
        return []

    kw_set = set(TECH_KEYWORDS)
    out = []
    for m in batch:
        q = m.get("question", "").lower()
        # Accept if any tech keyword appears in the question
        if not any(kw in q for kw in kw_set):
            continue
        yes = _yes_price(m)
        if yes is None:
            continue
        vol = float(m.get("volume") or m.get("volumeNum") or 0)
        out.append({
            "id":       m.get("id"),
            "question": m.get("question", ""),
            "yes":      yes,
            "vol":      vol,
        })

    out.sort(key=lambda x: -x["vol"])
    print(f"[tech] Polymarket tech/AI markets: {len(out)}", flush=True)
    return out


# ── Cross-matching ────────────────────────────────────────────────────────────

def _extract_words(text):
    """4+ char lower-case words, stop-word filtered."""
    return set(re.findall(r"[a-z]{4,}", text.lower())) - _STOP


def github_match(market_question, repos):
    """Return repos with ≥2 word overlap against market question."""
    q_words = _extract_words(market_question)
    hits = []
    for repo in repos:
        haystack = f"{repo['name'].replace('/', ' ')} {repo['desc']}"
        r_words = _extract_words(haystack)
        overlap = q_words & r_words
        if len(overlap) >= 2:
            hits.append({
                "repo_name":   repo["name"],
                "desc":        repo["desc"],
                "stars_today": repo["stars_today"],
                "overlap":     sorted(overlap),
            })
    return hits


def guardian_match(market_question, items):
    """Return Guardian items with ≥2 word overlap against market question."""
    q_words = _extract_words(market_question)
    hits = []
    for item in items:
        i_words = _extract_words(item["title"])
        overlap = q_words & i_words
        if len(overlap) >= 2:
            hits.append({
                "title":   item["title"],
                "ts":      item["ts"],
                "overlap": sorted(overlap),
            })
    return hits


# ── Main scan ────────────────────────────────────────────────────────────────

def scan():
    print("[tech] fetching GitHub trending...", flush=True)
    repos = fetch_github_trending(top=15)

    print("[tech] fetching Guardian RSS feeds...", flush=True)
    guardian_items = fetch_guardian_items(max_age_h=24)

    print("[tech] fetching Polymarket tech/AI markets...", flush=True)
    mkts = poly_tech_markets()

    github_hits   = []
    guardian_hits = []

    for m in mkts:
        gh = github_match(m["question"], repos)
        for hit in gh:
            github_hits.append({
                "question":   m["question"],
                "yes":        round(m["yes"], 3),
                "vol":        int(m["vol"]),
                "repo_name":  hit["repo_name"],
                "desc":       hit["desc"],
                "stars_today": hit["stars_today"],
                "overlap":    hit["overlap"],
            })

        gd = guardian_match(m["question"], guardian_items)
        for hit in gd:
            guardian_hits.append({
                "question":  m["question"],
                "yes":       round(m["yes"], 3),
                "vol":       int(m["vol"]),
                "title":     hit["title"],
                "item_ts":   hit["ts"],
                "overlap":   hit["overlap"],
            })

    # Dedup github_hits by (question, repo_name)
    seen_gh = set()
    deduped_gh = []
    for h in github_hits:
        k = (h["question"][:50], h["repo_name"])
        if k not in seen_gh:
            seen_gh.add(k)
            deduped_gh.append(h)

    # Dedup guardian_hits by (question, title prefix)
    seen_gd = set()
    deduped_gd = []
    for h in guardian_hits:
        k = (h["question"][:50], h["title"][:40])
        if k not in seen_gd:
            seen_gd.add(k)
            deduped_gd.append(h)

    # Sort by volume desc
    deduped_gh.sort(key=lambda x: (-x.get("stars_today", 0), -x["vol"]))
    deduped_gd.sort(key=lambda x: (-x.get("item_ts", 0), -x["vol"]))

    out = {
        "ts":              int(time.time()),
        "trending_repos":  repos,
        "guardian_items":  [{"title": i["title"], "ts": i["ts"]} for i in guardian_items],
        "github_hits":     deduped_gh,
        "guardian_hits":   deduped_gd,
        "poly_scanned":    len(mkts),
    }

    # Atomic write
    tmp = CACHE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(out, f, indent=1)
    os.replace(tmp, CACHE)
    print(f"[tech] {len(deduped_gh)} GitHub hits, {len(deduped_gd)} Guardian hits "
          f"across {len(mkts)} tech markets")
    return out


# ── Obsidian export ───────────────────────────────────────────────────────────

def export_obsidian(data):
    ts     = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(data.get("ts", time.time())))
    repos  = data.get("trending_repos", [])
    g_items = data.get("guardian_items", [])
    gh_hits = data.get("github_hits", [])
    gd_hits = data.get("guardian_hits", [])

    L = [
        "# Tech Probe — GitHub Trending + Guardian RSS × Polymarket",
        "",
        f"> Updated {ts} · {len(repos)} trending repos · "
        f"{len(g_items)} Guardian items (24h) · "
        f"{data.get('poly_scanned', 0)} tech markets scanned",
        "",
        "**GitHub**: repos trending today on developer attention. "
        "Overlap with a Polymarket question = community has eyes on this topic.",
        "**Guardian**: structured journalism (tech/world/science last 24h). "
        "Overlap = real-world event may not be priced yet.",
        "",
    ]

    # ── GitHub Trending Repos ──
    L += ["## GitHub Trending Today (top 15)", "",
          "| Repo | Stars Today | Description |",
          "|---|---|---|"]
    for r in repos:
        desc_short = r["desc"][:70] if r["desc"] else "_no description_"
        L.append(f"| [{r['name']}](https://github.com/{r['name']}) "
                 f"| ⭐ {r['stars_today']:,} | {desc_short} |")
    L.append("")

    # ── GitHub × Polymarket hits ──
    if gh_hits:
        L += ["## GitHub × Polymarket Matches", "",
              "| Market | YES | Stars Today | Repo | Overlap |",
              "|---|---|---|---|---|"]
        for h in gh_hits[:20]:
            L.append(f"| {h['question'][:55]} | {h['yes']:.0%} | "
                     f"⭐ {h['stars_today']:,} | {h['repo_name']} | "
                     f"`{', '.join(h['overlap'][:4])}` |")
        L.append("")
    else:
        L += ["## GitHub × Polymarket Matches", "_No overlapping markets today_", ""]

    # ── Guardian items ──
    L += ["## Guardian Headlines (last 24h)", "",
          "| Time | Headline |",
          "|---|---|"]
    for item in g_items[:20]:
        t = time.strftime("%H:%M", time.gmtime(item["ts"])) if item["ts"] else "?"
        L.append(f"| {t} UTC | {item['title'][:90]} |")
    L.append("")

    # ── Guardian × Polymarket hits ──
    if gd_hits:
        L += ["## Guardian × Polymarket Matches", "",
              "| Market | YES | Headline | Overlap |",
              "|---|---|---|---|"]
        for h in gd_hits[:20]:
            t = time.strftime("%H:%M", time.gmtime(h["item_ts"])) if h.get("item_ts") else "?"
            L.append(f"| {h['question'][:50]} | {h['yes']:.0%} | "
                     f"{h['title'][:65]} ({t}) | "
                     f"`{', '.join(h['overlap'][:4])}` |")
        L.append("")
    else:
        L += ["## Guardian × Polymarket Matches", "_No overlapping articles today_", ""]

    L += [
        "## How to use",
        "",
        "1. **GitHub + Guardian both match the same market** → strongest signal: "
           "developer community attention + journalistic coverage. Feed to analyst.",
        "2. **GitHub trending repo overlaps a market, YES price stale** → "
           "community has eyes on this; check if Poly price reflects recent news.",
        "3. **Guardian article from last 2h overlaps a market** → event freshly "
           "reported; most likely to be un-priced.",
        "4. **Stars today > 200 on matched repo** → unusually high developer "
           "attention; stronger signal quality.",
        "5. **Overlap of 3+ words** → tighter semantic match; prefer these over "
           "2-word overlaps.",
    ]

    os.makedirs(os.path.dirname(VAULT), exist_ok=True)
    tmp = VAULT + ".tmp"
    with open(tmp, "w") as f:
        f.write("\n".join(L) + "\n")
    os.replace(tmp, VAULT)
    print(f"[tech] Obsidian → {VAULT}")


# ── Graphify dual-write ───────────────────────────────────────────────────────

def _write_graphify_node(data):
    ts      = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(data.get("ts", 0)))
    gh_hits = data.get("github_hits", [])
    gd_hits = data.get("guardian_hits", [])
    repos   = data.get("trending_repos", [])

    L = [
        f"# Tech Probe Findings — {ts}",
        "",
        "Signal source: GitHub Trending (developer attention) + Guardian RSS "
        "(tech/world/science journalism) cross-referenced against Polymarket "
        "tech/AI/science markets.",
        "",
        f"## GitHub Trending Repos: {', '.join(r['name'] for r in repos[:8])}",
        "",
    ]

    if gh_hits:
        L += ["## GitHub × Polymarket Overlaps", ""]
        for h in gh_hits[:15]:
            L.append(
                f"- **{h['question']}** (Poly {h['yes']:.0%}, vol ${h['vol']:,}): "
                f"repo **{h['repo_name']}** trending with ⭐ {h['stars_today']:,} stars today "
                f"— overlap: {', '.join(h['overlap'][:4])}"
            )
    else:
        L += ["## GitHub × Polymarket Overlaps", "_No matches_"]

    L += ["", "## Guardian × Polymarket Overlaps", ""]
    if gd_hits:
        for h in gd_hits[:15]:
            t = time.strftime("%H:%M UTC", time.gmtime(h["item_ts"])) if h.get("item_ts") else ""
            L.append(
                f"- **{h['question']}** (Poly {h['yes']:.0%}): "
                f"Guardian: \"{h['title']}\" {t} — overlap: {', '.join(h['overlap'][:4])}"
            )
    else:
        L += ["_No matches_"]

    content = "\n".join(L) + "\n"

    # Dual-write: local + vault
    for path in [FINDINGS_LOCAL, FINDINGS_VAULT]:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            f.write(content)
        os.replace(tmp, path)

    print(f"[tech] graphify → {FINDINGS_LOCAL} + {FINDINGS_VAULT}")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "once"

    if cmd == "report":
        if not os.path.exists(CACHE):
            print("No cache yet — run without args first")
            return
        data = json.load(open(CACHE))
        export_obsidian(data)
        gh = data.get("github_hits", [])
        gd = data.get("guardian_hits", [])
        ts = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(data.get("ts", 0)))
        print(f"Cached {ts} — {len(gh)} GitHub hits, {len(gd)} Guardian hits")
        for h in gh[:5]:
            print(f"  GH  {h['yes']:.0%} | {h['repo_name']} | {h['question'][:60]}")
        for h in gd[:5]:
            print(f"  GD  {h['yes']:.0%} | {h['title'][:50]} | {h['question'][:50]}")
        return

    data = scan()
    export_obsidian(data)
    _write_graphify_node(data)


if __name__ == "__main__":
    main()
