#!/usr/bin/env python3
"""trending_probe.py — CoinGecko trending + Hacker News frontpage signal.

Two attention signals that neither GDELT nor Google News captures well:
  1. CoinGecko Trending (top 7 coins by search in last 24h): if a Polymarket
     crypto market's underlying coin is trending, there's unusual community
     attention — a precursor to a price move on Polymarket.
  2. Hacker News frontpage (top 20 stories): if an HN story matches a tech/AI
     Polymarket market, that signals informed-community attention before broader
     media picks it up. HN breaks tech news 4-8h before mainstream outlets.

Read-only (no trades). Run every 2h via watchdog. Obsidian export each run.

Usage:
    python3 trending_probe.py        # scan + export
    python3 trending_probe.py report # reprint cached results
"""

import json, os, re, sys, time, urllib.request, urllib.parse
from concurrent.futures import ThreadPoolExecutor

HERE   = os.path.dirname(os.path.abspath(__file__))
CACHE  = os.path.join(HERE, "trending_cache.json")
VAULT  = os.path.expanduser("~/Documents/PolymarketVault/Reports/trending_probe.md")
UA     = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}

COINGECKO_TRENDING = "https://api.coingecko.com/api/v3/search/trending"
HN_RSS             = "https://hnrss.org/frontpage"
GAMMA              = "https://gamma-api.polymarket.com/markets"

MIN_VOL   = 20_000
POLY_PAGES = 5
WORKERS    = 8

_COIN_KW = {"bitcoin": "BTC", "btc": "BTC", "ethereum": "ETH", "eth": "ETH",
            "solana": "SOL", "sol": "SOL", "doge": "DOGE", "dogecoin": "DOGE",
            "bnb": "BNB", "xrp": "XRP", "ripple": "XRP", "cardano": "ADA",
            "ada": "ADA", "avalanche": "AVAX", "avax": "AVAX", "link": "LINK",
            "chainlink": "LINK", "dot": "DOT", "polkadot": "DOT", "shib": "SHIB",
            "pepe": "PEPE", "sui": "SUI", "near": "NEAR", "ton": "TON",
            "pengu": "PENGU", "trump": "TRUMP", "melania": "MELANIA"}

_TECH_STOP = {"will", "with", "from", "that", "this", "have", "what", "when",
              "your", "into", "than", "they", "been", "their", "make", "more",
              "some", "after", "about", "which", "other", "could", "would"}


def _get(url, timeout=10):
    try:
        req = urllib.request.Request(url, headers=UA)
        return urllib.request.urlopen(req, timeout=timeout).read()
    except Exception:
        return b""


# ── CoinGecko trending ───────────────────────────────────────────────────────

def fetch_trending_coins():
    """Return set of trending coin symbols (top 7 by CoinGecko search)."""
    raw = _get(COINGECKO_TRENDING)
    if not raw:
        return {}
    try:
        d = json.loads(raw)
        out = {}
        for c in d.get("coins", []):
            it = c.get("item", {})
            sym  = it.get("symbol", "").upper()
            name = it.get("name", "").lower()
            rank = it.get("market_cap_rank") or 9999
            score = it.get("score", 0)
            out[sym] = {"name": it.get("name"), "rank": rank, "score": score}
            # also index by name for fuzzy matching
            out[name] = {"name": it.get("name"), "rank": rank, "score": score, "sym": sym}
        return out
    except Exception:
        return {}


def coin_trending(question, trending):
    """Return trending info if market's underlying coin is in CoinGecko trending."""
    q = question.lower()
    for kw, sym in _COIN_KW.items():
        if kw in q:
            if sym in trending:
                return {"coin": sym, **trending[sym]}
            # check by name too
            name = trending.get(kw, {})
            if name:
                return {"coin": sym, **name}
    return None


# ── Hacker News frontpage ────────────────────────────────────────────────────

def fetch_hn_stories():
    """Return list of (title, link) from HN frontpage RSS."""
    raw = _get(HN_RSS, timeout=8).decode("utf-8", "replace")
    items = re.findall(r"<item>(.*?)</item>", raw, re.DOTALL)
    stories = []
    for it in items:
        title_m = re.search(r"<title>(.*?)</title>", it, re.DOTALL)
        link_m  = re.search(r"<link>(.*?)</link>", it)
        if title_m:
            title = re.sub(r"<!\[CDATA\[|\]\]>", "", title_m.group(1)).strip()
            link  = link_m.group(1).strip() if link_m else ""
            stories.append({"title": title, "link": link,
                            "words": set(re.findall(r"[a-z]{4,}", title.lower())) - _TECH_STOP})
    return stories


def hn_match(question, stories):
    """Return matching HN stories for a tech/AI market."""
    q_words = set(re.findall(r"[a-z]{4,}", question.lower())) - _TECH_STOP
    matches = []
    for s in stories:
        overlap = q_words & s["words"]
        if len(overlap) >= 2:
            matches.append({"title": s["title"][:80], "overlap": sorted(overlap),
                            "link": s["link"]})
    return matches[:3]


# ── Polymarket feed ──────────────────────────────────────────────────────────

def poly_markets():
    out = []
    for pg in range(POLY_PAGES):
        raw = _get(f"{GAMMA}?active=true&closed=false&order=volume&ascending=false"
                   f"&limit=100&offset={pg*100}", timeout=15)
        if not raw:
            break
        for m in (json.loads(raw) if raw else []):
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


# ── main scan ────────────────────────────────────────────────────────────────

def scan():
    print("[trending] fetching CoinGecko trending + HN...", flush=True)
    trending = fetch_trending_coins()
    hn_stories = fetch_hn_stories()
    print(f"[trending] {len(trending)//2} trending coins, {len(hn_stories)} HN stories", flush=True)

    mkts = poly_markets()
    print(f"[trending] checking {len(mkts)} markets", flush=True)

    coin_hits, hn_hits = [], []
    for m in mkts:
        ct = coin_trending(m["question"], trending)
        if ct:
            coin_hits.append({**m, "trending": ct})
        hm = hn_match(m["question"], hn_stories)
        if hm:
            hn_hits.append({**m, "hn_matches": hm})

    out = {"ts": int(time.time()), "coin_hits": coin_hits, "hn_hits": hn_hits,
           "trending_coins": {k: v for k, v in trending.items() if len(k) <= 5},
           "hn_count": len(hn_stories)}
    tmp = CACHE + ".tmp"
    json.dump(out, open(tmp, "w"), indent=1)
    os.replace(tmp, CACHE)
    print(f"[trending] {len(coin_hits)} coin-trending markets, {len(hn_hits)} HN-matched markets")
    return out


# ── Obsidian export ──────────────────────────────────────────────────────────

def export_obsidian(data):
    ts  = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(data.get("ts", 0)))
    tc  = {k: v for k, v in data.get("trending_coins", {}).items() if len(k) <= 6}
    L = [
        "# Trending Probe — CoinGecko Trending + Hacker News Signal",
        "",
        f"> Updated {ts}",
        "> **CoinGecko**: top 7 coins by 24h search volume. Market on a trending coin = "
          "attention precursor.",
        "> **HN frontpage**: tech/AI stories. HN breaks 4-8h before mainstream — "
          "early signal for AI/tech markets.",
        "",
    ]

    trending_line = ", ".join(f"{k}(#{v.get('rank','?')})" for k, v in tc.items())
    L += [f"## CoinGecko Trending Now: {trending_line}", ""]

    if data.get("coin_hits"):
        L += ["### Polymarket Markets on Trending Coins", "",
              "| Coin | Rank | Poly YES | Market |",
              "|---|---|---|---|"]
        for m in data["coin_hits"]:
            ct = m["trending"]
            L.append(f"| {ct['coin']} | #{ct.get('rank','?')} | {m['yes']:.0%} | "
                     f"{m['question'][:60]} |")
        L.append("")
    else:
        L += ["### Polymarket Markets on Trending Coins", "_None right now_", ""]

    if data.get("hn_hits"):
        L += ["## HN Frontpage × Polymarket Matches", "",
              "| Market | Poly YES | HN Story |",
              "|---|---|---|"]
        for m in data["hn_hits"]:
            for hm in m["hn_matches"][:1]:
                L.append(f"| {m['question'][:50]} | {m['yes']:.0%} | "
                         f"{hm['title'][:60]} |")
        L.append("")
    else:
        L += ["## HN Frontpage × Polymarket Matches", "_No overlaps right now_", ""]

    L += ["## How to use",
          "- **Trending coin + Poly price stale** → community is moving on this coin, "
            "Poly may lag.",
          "- **HN match on AI/tech market** → HN breaks news early; check if Poly "
            "price has moved since story posted.",
          "- **Both signals** → highest attention; feed to analyst for review."]

    os.makedirs(os.path.dirname(VAULT), exist_ok=True)
    open(VAULT, "w").write("\n".join(L) + "\n")
    print(f"[trending] Obsidian → {VAULT}")


_OSA = ('on run argv\ntell application "Messages"\n'
        '  set s to 1st service whose service type = iMessage\n'
        '  set b to buddy (item 1 of argv) of s\n'
        '  send (item 2 of argv) to b\nend tell\nend run')

def _imsg(body):
    import subprocess
    try:
        subprocess.run(["osascript", "-e", _OSA, "+918449447444", body],
                       capture_output=True, text=True, timeout=30)
    except Exception:
        pass


def _write_graphify_node(data):
    ts = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(data.get("ts", 0)))
    tc = {k: v for k, v in data.get("trending_coins", {}).items() if len(k) <= 6}
    L = [f"# Trending Probe Findings — {ts}", "",
         "Signal source: CoinGecko top-7 trending coins (24h search volume) + "
         "Hacker News frontpage matched against Polymarket markets.", "",
         f"## CoinGecko Trending Coins: {', '.join(tc.keys())}", ""]
    for m in data.get("coin_hits", [])[:10]:
        ct = m["trending"]
        L.append(f"- **{m['question']}** (Poly {m['yes']:.0%}): "
                 f"coin **{ct['coin']}** is trending rank #{ct.get('rank','?')} on CoinGecko")
    L += ["", "## Hacker News Frontpage Matches", ""]
    for m in data.get("hn_hits", [])[:10]:
        for hm in m["hn_matches"][:1]:
            L.append(f"- **{m['question']}** (Poly {m['yes']:.0%}): "
                     f"HN story \"{hm['title']}\" overlaps on words {hm['overlap']}")
    content = "\n".join(L) + "\n"
    open(os.path.join(HERE, "trending_findings.md"), "w").write(content)
    vault = os.path.expanduser("~/Documents/PolymarketVault/Reports/trending_findings.md")
    os.makedirs(os.path.dirname(vault), exist_ok=True)
    open(vault, "w").write(content)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "once"
    if cmd == "report":
        if os.path.exists(CACHE):
            data = json.load(open(CACHE))
            export_obsidian(data)
            print(f"coin_hits={len(data.get('coin_hits',[]))}  "
                  f"hn_hits={len(data.get('hn_hits',[]))}")
        else:
            print("No cache — run without args first")
        return
    data = scan()
    export_obsidian(data)
    _write_graphify_node(data)
    if data.get("coin_hits"):
        names = ", ".join(m["trending"]["coin"] for m in data["coin_hits"][:3])
        _imsg(f"📈 TRENDING COIN MARKETS: {names}")


if __name__ == "__main__":
    main()
