#!/usr/bin/env python3
"""consensus_probe.py — Multi-venue consensus vs Polymarket (read-only, no trades).

Sources:
  • Manifold Markets  — play-money but directional signal (free, no key)
  • Wikipedia pageviews — entity attention spikes (+2x rolling avg = headline event)

For each Polymarket market, we:
  1. Keyword-search Manifold for a matching question.
  2. If |prob_poly - prob_manifold| >= MIN_DIVERGE: log as divergence.
  3. Extract entities from the question, check Wikipedia 7d avg views vs prior 7d.
     View spike >WIKI_SPIKE_X = something happened (arrest, win, death, controversy).

Divergence = Polymarket is potentially mis-priced vs crowd consensus.
Attention spike = external event the market may not have priced yet.

NOTE: Manifold uses play-money (Mana), not real USDC. Treat it as "soft signal"
only — useful when divergence is large (>20%) on a market with substantial
Manifold liquidity (>M500). PredictIt/Metaculus/Kalshi all require auth.

Run: python3 consensus_probe.py        # scan + Obsidian export
     python3 consensus_probe.py once   # same
     python3 consensus_probe.py report # print last cached results
"""

import json, os, re, time, urllib.request, urllib.parse, sys
from concurrent.futures import ThreadPoolExecutor

HERE  = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "consensus_cache.json")
VAULT = os.path.expanduser("~/Documents/PolymarketVault/Reports/consensus_probe.md")
UA    = {"User-Agent": "Mozilla/5.0"}

GAMMA         = "https://gamma-api.polymarket.com/markets"
MANIFOLD_SRCH = "https://manifold.markets/api/v0/search-markets"
WIKI_API      = "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia/all-access/all-agents/{}/daily/{}/{}"

MIN_VOL_POLY  = 50_000    # Polymarket USD volume floor
MIN_LIQ_MAN   = 500       # Manifold Mana liquidity floor (below = noise)
MIN_DIVERGE   = 0.10      # |poly_prob - manifold_prob| to flag
WIKI_SPIKE_X  = 2.0       # views must be >2x prior-7d avg to flag
POLY_PAGES    = 5         # pages of 100 markets to scan (~500 markets)
MANIFOLD_MAX  = 5         # search results per market query (speed vs coverage)
WORKERS       = 8         # concurrent manifold searches


# ── helpers ──────────────────────────────────────────────────────────────────

def _get(url, timeout=12):
    try:
        r = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout)
        return json.loads(r.read())
    except Exception:
        return None


def _yes_price(m):
    op = m.get("outcomePrices")
    if isinstance(op, str):
        try:
            op = json.loads(op)
        except Exception:
            return None
    return float(op[0]) if op else None


# ── Polymarket feed ───────────────────────────────────────────────────────────

def poly_markets(pages=POLY_PAGES):
    out = []
    for pg in range(pages):
        batch = _get(f"{GAMMA}?active=true&closed=false&order=volume&ascending=false"
                     f"&limit=100&offset={pg*100}")
        if not batch:
            break
        for m in batch:
            vol = float(m.get("volume") or m.get("volumeNum") or 0)
            if vol < MIN_VOL_POLY:
                continue
            yes = _yes_price(m)
            if yes is None or not (0.03 <= yes <= 0.97):
                continue
            out.append({"id": m.get("id"), "cid": m.get("conditionId"),
                        "question": m.get("question", ""),
                        "yes": yes, "vol": vol})
    return out


# ── Manifold cross-check ─────────────────────────────────────────────────────

def _keywords(question):
    """Extract 2-4 meaningful keywords from a market question."""
    stop = {"will", "the", "a", "an", "of", "to", "in", "on", "for", "and",
            "or", "be", "is", "are", "at", "as", "from", "this", "that", "which",
            "who", "when", "by", "with", "have", "has", "what", "does", "do",
            "more", "most", "than", "its", "win", "wins", "hit", "reach", "end",
            "2025", "2026", "2027", "year"}
    words = re.findall(r"[a-zA-Z]{4,}", question.lower())
    kws = [w for w in words if w not in stop]
    return " ".join(kws[:4]) if kws else question[:40]


def manifold_match(poly_mkt):
    q = _keywords(poly_mkt["question"])
    url = f"{MANIFOLD_SRCH}?{urllib.parse.urlencode({'term': q, 'limit': MANIFOLD_MAX})}"
    results = _get(url, timeout=8) or []
    best = None
    best_sim = 0
    for m in results:
        if m.get("isResolved") or m.get("outcomeType") != "BINARY":
            continue
        liq = m.get("totalLiquidity", 0)
        if liq < MIN_LIQ_MAN:
            continue
        prob = m.get("probability")
        if prob is None:
            continue
        # simple word overlap sim
        poly_words = set(re.findall(r"[a-z]{4,}", poly_mkt["question"].lower()))
        man_words  = set(re.findall(r"[a-z]{4,}", m.get("question", "").lower()))
        if not (poly_words and man_words):
            continue
        sim = len(poly_words & man_words) / len(poly_words | man_words)
        if sim > best_sim and sim >= 0.25:
            best_sim = sim
            best = {"question": m.get("question"), "prob": float(prob),
                    "liq": liq, "url": m.get("url"), "sim": round(sim, 2)}
    return best


# ── Wikipedia attention ───────────────────────────────────────────────────────

_ENTITY_STOP = {"will", "year", "2025", "2026", "trump", "president", "election",
                "winner", "first", "next", "under", "over", "reach", "have",
                "with", "from", "this", "that", "more", "most", "than", "what",
                "does", "crypto", "bitcoin", "price", "above", "below", "within"}


def extract_entities(question):
    """Extract capitalized proper noun candidates from a question."""
    caps = re.findall(r"\b([A-Z][a-z]{3,}(?:\s+[A-Z][a-z]{3,})*)\b", question)
    seen, out = set(), []
    for e in caps:
        low = e.lower()
        if low not in _ENTITY_STOP and e not in seen:
            seen.add(e); out.append(e)
    return out[:3]


def _wiki_views(entity, days_ago_start, days_ago_end):
    """Fetch daily pageviews for entity from days_ago_end to days_ago_start."""
    slug = urllib.parse.quote(entity.replace(" ", "_"))
    now = time.gmtime()
    def fmt(d): return time.strftime("%Y%m%d", time.gmtime(time.time() - d * 86400))
    url = WIKI_API.format(slug, fmt(days_ago_end), fmt(days_ago_start))
    d = _get(url, timeout=8)
    if not d:
        return []
    return [it["views"] for it in d.get("items", [])]


def wiki_attention(question):
    """Return list of (entity, spike_ratio, recent_avg, prior_avg) for notable spikes."""
    entities = extract_entities(question)
    spikes = []
    for ent in entities:
        recent = _wiki_views(ent, 0, 7)    # last 7 days
        prior  = _wiki_views(ent, 7, 14)   # prior 7 days
        if not recent or not prior:
            continue
        r_avg = sum(recent) / len(recent)
        p_avg = sum(prior)  / len(prior)
        if p_avg < 500:
            continue  # too obscure to be meaningful
        ratio = r_avg / p_avg if p_avg > 0 else 1.0
        if ratio >= WIKI_SPIKE_X:
            spikes.append({"entity": ent, "ratio": round(ratio, 1),
                           "recent_avg": int(r_avg), "prior_avg": int(p_avg)})
    return spikes


# ── main scan ─────────────────────────────────────────────────────────────────

def scan():
    print(f"[consensus] fetching Polymarket markets...", flush=True)
    mkts = poly_markets()
    print(f"[consensus] {len(mkts)} markets to probe", flush=True)

    divergences, attentions = [], []

    def probe(pm):
        divs = []
        # Manifold check
        man = manifold_match(pm)
        if man:
            gap = pm["yes"] - man["prob"]
            if abs(gap) >= MIN_DIVERGE:
                divs.append({
                    "type": "manifold",
                    "question": pm["question"],
                    "poly_yes": round(pm["yes"], 3),
                    "manifold_yes": round(man["prob"], 3),
                    "gap": round(gap, 3),
                    "manifold_liq": man["liq"],
                    "manifold_sim": man["sim"],
                    "manifold_q": man["question"],
                    "manifold_url": man["url"],
                    "poly_vol": int(pm["vol"]),
                })
        # Wikipedia attention
        spikes = wiki_attention(pm["question"])
        for sp in spikes:
            divs.append({
                "type": "wiki_attention",
                "question": pm["question"],
                "poly_yes": round(pm["yes"], 3),
                "entity": sp["entity"],
                "ratio": sp["ratio"],
                "recent_avg": sp["recent_avg"],
                "prior_avg": sp["prior_avg"],
            })
        return divs

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for results in ex.map(probe, mkts):
            for r in results:
                if r["type"] == "manifold":
                    divergences.append(r)
                else:
                    attentions.append(r)

    divergences.sort(key=lambda x: -abs(x["gap"]))
    attentions.sort(key=lambda x: -x["ratio"])

    out = {"ts": int(time.time()), "divergences": divergences, "attentions": attentions,
           "poly_scanned": len(mkts)}
    tmp = CACHE + ".tmp"
    json.dump(out, open(tmp, "w"), indent=1)
    os.replace(tmp, CACHE)
    print(f"[consensus] {len(divergences)} price divergences, {len(attentions)} attention spikes")
    return out


# ── Obsidian export ───────────────────────────────────────────────────────────

def export_obsidian(data):
    ts = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(data.get("ts", time.time())))
    nd = len(data.get("divergences", []))
    na = len(data.get("attentions", []))
    L = [
        "# Consensus Probe — Polymarket vs Manifold + Wikipedia Attention",
        "",
        "> **Signal quality**: Manifold = play-money (soft); Wikipedia spikes = hard attention signal.",
        "> A big divergence + a spike together = strong case for analyst review.",
        f"> Updated {ts} · scanned {data.get('poly_scanned',0)} Poly markets · "
        f"{nd} price divergences · {na} attention spikes",
        "",
    ]

    if data.get("divergences"):
        L += ["## Price Divergences (|Poly − Manifold| ≥ 10%)",
              "",
              "| Poly YES | Manifold YES | Gap | Liq(M) | Sim | Market |",
              "|---|---|---|---|---|---|"]
        for d in data["divergences"][:20]:
            direction = "Poly OVER" if d["gap"] > 0 else "Poly UNDER"
            L.append(f"| {d['poly_yes']:.0%} | {d['manifold_yes']:.0%} | "
                     f"{d['gap']:+.0%} ({direction}) | M{d['manifold_liq']:,.0f} | "
                     f"{d['manifold_sim']} | {d['question'][:55]} |")
        L.append("")
    else:
        L += ["## Price Divergences", "_None above 10% threshold_", ""]

    if data.get("attentions"):
        L += ["## Wikipedia Attention Spikes (>2× prior 7d avg)",
              "",
              "| Entity | Spike | Recent | Prior | Poly YES | Market |",
              "|---|---|---|---|---|---|"]
        for a in data["attentions"][:15]:
            L.append(f"| {a['entity']} | {a['ratio']}× | {a['recent_avg']:,}/day | "
                     f"{a['prior_avg']:,}/day | {a['poly_yes']:.0%} | {a['question'][:50]} |")
        L.append("")
    else:
        L += ["## Wikipedia Attention Spikes", "_None above 2× threshold_", ""]

    L += [
        "## How to use",
        "",
        "1. **Large divergence + high Manifold liquidity** → Polymarket may be wrong; feed to analyst.",
        "2. **Attention spike (entity 3×+) + stale Poly price** → news event not yet priced.",
        "3. **Both signals together** → highest confidence analyst candidate.",
        "4. **Manifold alone, thin liquidity** → soft signal only; do not trade on it solo.",
    ]

    os.makedirs(os.path.dirname(VAULT), exist_ok=True)
    open(VAULT, "w").write("\n".join(L) + "\n")
    print(f"[consensus] Obsidian → {VAULT}")


# ── iMessage on big divergences ───────────────────────────────────────────────

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
                sys.stderr.write(f"[imsg FAIL] {t}: {r.stderr.strip()}\n")
        except Exception as e:
            sys.stderr.write(f"[imsg FAIL] {t}: {e}\n")


def alert_if_notable(data):
    """iMessage only the strongest signals (>20% gap or 3x+ wiki spike)."""
    lines = []
    for d in data.get("divergences", []):
        if abs(d["gap"]) >= 0.20 and d["manifold_liq"] >= 1000:
            direction = "OVER" if d["gap"] > 0 else "UNDER"
            lines.append(f"📊 Poly {direction} {abs(d['gap']):.0%}: {d['question'][:45]}\n"
                         f"   Poly {d['poly_yes']:.0%} vs Manifold {d['manifold_yes']:.0%} "
                         f"(M{d['manifold_liq']:,.0f} liq)")
    for a in data.get("attentions", []):
        if a["ratio"] >= 3.0:
            lines.append(f"⚠️ Wikipedia spike {a['ratio']}× for {a['entity']}\n"
                         f"   {a['question'][:50]} (Poly {a['poly_yes']:.0%})")
    if lines:
        body = "CONSENSUS PROBE\n" + "\n".join(lines[:5])
        _imsg(body)


def _write_graphify_node(data):
    ts   = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(data.get("ts", 0)))
    divs = data.get("divergences", [])
    atts = data.get("attentions", [])
    L = [f"# Consensus Probe Findings — {ts}", "",
         "Signal source: Manifold Markets vs Polymarket price comparison + Wikipedia entity attention.",
         "", "## Manifold–Polymarket Divergences", ""]
    for d in divs[:12]:
        L.append(f"- **{d['question']}**: Polymarket {d['poly_yes']:.0%} vs "
                 f"Manifold {d['manifold_yes']:.0%} (gap {d['gap']:+.0%}, "
                 f"Manifold liquidity M{d.get('manifold_liq',0):.0f})")
    L += ["", "## Wikipedia Attention Spikes", ""]
    for a in atts[:12]:
        L.append(f"- Entity **{a['entity']}** spiked {a['ratio']}× average views "
                 f"on market: {a['question']}")
    content = "\n".join(L) + "\n"
    open(os.path.join(HERE, "consensus_findings.md"), "w").write(content)
    vault = os.path.expanduser("~/Documents/PolymarketVault/Reports/consensus_findings.md")
    os.makedirs(os.path.dirname(vault), exist_ok=True)
    open(vault, "w").write(content)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "once"
    if cmd == "report":
        if os.path.exists(CACHE):
            data = json.load(open(CACHE))
            export_obsidian(data)
            divs = data.get("divergences", [])
            atts = data.get("attentions", [])
            print(f"Cached at {time.strftime('%H:%M', time.gmtime(data['ts']))} UTC — "
                  f"{len(divs)} divergences, {len(atts)} spikes")
            for d in divs[:5]:
                print(f"  {d['gap']:+.0%} | {d['question'][:60]}")
            for a in atts[:5]:
                print(f"  {a['ratio']}× {a['entity']} | {a['question'][:55]}")
        else:
            print("No cache yet — run without args first")
        return
    data = scan()
    export_obsidian(data)
    alert_if_notable(data)
    _write_graphify_node(data)


if __name__ == "__main__":
    main()
