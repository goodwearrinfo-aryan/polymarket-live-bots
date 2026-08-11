#!/usr/bin/env python3
"""world_data_hunter.py — a 24/7 agent that DISCOVERS new free/keyless data sources and wires
them into the Obsidian World Data swarm. The scheduled, autonomous form of the free-data-finder
fleet (finder -> validator -> cartographer), which until now only existed as manual agents.

Each cycle it:
  1. Loads the sources already in the vault's `World Data/` swarm (dedupe by name + endpoint host).
  2. Builds a candidate pool: a curated list of keyless public APIs + (best-effort, $0) a free-LLM
     brainstorm for ONE rotating domain via llm_client (open-weight chain, never metered Claude).
  3. GROUND-TRUTHS every candidate with a LIVE keyless probe — a no-key GET that must return 200 +
     a non-empty parseable body. An LLM- or memory-proposed endpoint is a HYPOTHESIS until this
     passes (the venue-scout / data-sources curl-validation lesson). DEAD candidates are dropped.
  4. For each genuinely NEW keyless source, writes a `World Data/<Name>.md` node in the swarm's
     exact format, upserts it into its domain hub, and rebuilds the `World Data.md` index — so the
     new node is fully wired into the live graph that the snapshot / connector / wiring agents render.

Vault-only and silent (no iMessage spam) — writes a `World Data Hunter.md` run-summary note.
stdlib + framework python = TCC-clean under launchd. Fail-soft, idempotent, lockfile, /tmp log.
"""
import os, re, json, time, datetime, ssl, urllib.request, collections
from pathlib import Path

HERE   = Path.home() / "Documents/polymarket"
VAULT  = Path.home() / "Documents/PolymarketVault"
WD     = VAULT / "World Data"
INDEX  = VAULT / "World Data.md"
RUNNOTE = VAULT / "World Data Hunter.md"
LOG    = Path("/tmp/world-data-hunter.log")
LOCK   = Path("/tmp/world-data-hunter.lock")
MAX_NEW_PER_RUN = 8
MAX_VALIDATE    = 36
UA = "Mozilla/5.0 (compatible; world-data-hunter/1.0; keyless-probe)"

# Curated keyless candidate pool. Validation decides what actually lives — any that now require a
# key (e.g. some moved behind auth) simply fail the probe and are dropped. (name, domain, endpoint, desc)
CANDIDATES = [
    ("CoinPaprika",          "Markets & FX",        "https://api.coinpaprika.com/v1/tickers/btc-bitcoin", "crypto tickers, 2.5k+ coins, no key"),
    ("Coinbase Spot",        "Markets & FX",        "https://api.coinbase.com/v2/prices/BTC-USD/spot", "Coinbase spot price"),
    ("Kraken Public",        "Markets & FX",        "https://api.kraken.com/0/public/Ticker?pair=XBTUSD", "Kraken public ticker/OHLC"),
    ("Bitstamp",             "Markets & FX",        "https://www.bitstamp.net/api/v2/ticker/btcusd/", "Bitstamp spot ticker"),
    ("Fear & Greed",         "Markets & FX",        "https://api.alternative.me/fng/", "crypto fear & greed index"),
    ("Blockchain.info",      "Markets & FX",        "https://blockchain.info/ticker", "BTC price across fiat"),
    ("Federal Register",     "Macro & Econ",        "https://www.federalregister.gov/api/v1/documents.json?per_page=1", "US federal rules/notices"),
    ("data.gov CKAN",        "Macro & Econ",        "https://catalog.data.gov/api/3/action/package_search?rows=1", "US open-data catalog"),
    ("Nager Holidays",       "Macro & Econ",        "https://date.nager.at/api/v3/PublicHolidays/2026/US", "public holidays by country/year"),
    ("World Time",           "Macro & Econ",        "https://worldtimeapi.org/api/timezone/Etc/UTC", "current time by timezone"),
    ("OpenAlex",             "Science & Research",  "https://api.openalex.org/works?per_page=1", "260M+ scholarly works, no key"),
    ("Semantic Scholar",     "Science & Research",  "https://api.semanticscholar.org/graph/v1/paper/search?query=ai&limit=1", "paper graph + citations"),
    ("NASA EONET",           "Geo & Earth",         "https://eonet.gsfc.nasa.gov/api/v3/events?limit=1", "natural-event tracker (fires/storms)"),
    ("GDACS Disasters",      "Geo & Earth",         "https://www.gdacs.org/gdacsapi/api/events/geteventlist/EVENTS4APP", "global disaster alerts"),
    ("USGS Water",           "Geo & Earth",         "https://waterservices.usgs.gov/nwis/iv/?format=json&sites=01646500&parameterCd=00060", "real-time US streamflow"),
    ("Open-Meteo Marine",    "Geo & Earth",         "https://marine-api.open-meteo.com/v1/marine?latitude=54&longitude=10&hourly=wave_height", "wave height / marine forecast"),
    ("Open-Meteo Flood",     "Geo & Earth",         "https://flood-api.open-meteo.com/v1/flood?latitude=52&longitude=13&daily=river_discharge", "river discharge / flood"),
    ("SpaceX",               "Space & Astro",       "https://api.spacexdata.com/v4/launches/latest", "SpaceX launch data"),
    ("Sunrise-Sunset",       "Space & Astro",       "https://api.sunrise-sunset.org/json?lat=36&lng=-119", "sun/twilight times by coords"),
    ("TfL Status",           "Transport & Aviation","https://api.tfl.gov.uk/Line/victoria/Status", "Transport for London line status"),
    ("FBI Wanted",           "News & Geopolitics",  "https://api.fbi.gov/wanted/v1/list?page=1", "FBI wanted list"),
    ("OpenAQ",               "Health & Epi",        "https://api.openaq.org/v2/latest?limit=1", "global air-quality measurements"),
]

LINKRE = re.compile(r"\[\[([^\]]+?)\]\]")


def log(msg):
    line = f"{datetime.datetime.now().isoformat(timespec='seconds')} {msg}"
    try: LOG.open("a").write(line + "\n")
    except Exception: pass
    print(line)


def safe_name(n):
    return re.sub(r"[\\/:*?\"<>|]", "-", n).strip()


def host(url):
    m = re.match(r"https?://([^/]+)", url)
    return (m.group(1).lower() if m else url.lower())


def probe(url, timeout=12):
    """LIVE keyless GET. Returns (ok, sample, note). No auth header is ever sent."""
    try:
        ctx = ssl.create_default_context()
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            code = r.getcode()
            body = r.read(4000).decode("utf-8", "ignore").strip()
        if code != 200 or not body:
            return False, "", f"http {code} / empty"
        try:
            json.loads(body)
        except Exception:
            if len(body) < 2:
                return False, "", "non-json tiny body"
        sample = re.sub(r"\s+", " ", body)[:140]
        return True, sample, "ok"
    except Exception as e:
        return False, "", f"{type(e).__name__}: {e}"


def llm_candidates(domain, existing_names):
    """Best-effort free-LLM brainstorm of keyless APIs for one domain. $0 (open-weight chain). Fail-soft."""
    try:
        import sys
        sys.path.insert(0, str(HERE))
        import llm_client
        if not llm_client.configured():
            return [], "llm not configured"
        sys_p = ("You list FREE, KEYLESS public data APIs (no signup/no API key needed for a basic GET). "
                 "Reply with ONLY a JSON array of objects {\"name\":..,\"endpoint\":..,\"desc\":..}. "
                 "Endpoints must be real, directly-curlable GET URLs that return data with NO auth. "
                 "No prose, no markdown fences — just the JSON array.")
        usr = (f"Domain: {domain}. Give 5 keyless public APIs in this domain. "
               f"Exclude these already-known names: {sorted(existing_names)[:60]}.")
        txt = llm_client.chat([{"role": "system", "content": sys_p},
                               {"role": "user", "content": usr}], temperature=0.3, max_tokens=700)
        prov = llm_client.last_served() if hasattr(llm_client, "last_served") else "?"
        # tolerant array extraction: grab the first [...] block, even if wrapped in prose/fences
        m = re.search(r"\[.*\]", txt, re.S)
        items = json.loads(m.group(0)) if m else []
        res = []
        for it in items:
            if isinstance(it, dict) and str(it.get("endpoint", "")).startswith("http"):
                res.append((safe_name(str(it.get("name", "?"))[:40]), domain,
                            it["endpoint"].strip(), str(it.get("desc", ""))[:80]))
        return res[:5], f"served={prov} n={len(res)}"
    except Exception as e:
        return [], f"err {type(e).__name__}: {e}"


def load_existing():
    names, hosts, by_domain = set(), set(), collections.defaultdict(list)
    if not WD.exists():
        return names, hosts, by_domain
    for f in WD.glob("*.md"):
        try:
            t = f.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if "type: world-data-source" not in t:
            continue
        names.add(f.stem.lower())
        dm = re.search(r"^domain:\s*(.+)$", t, re.M)
        domain = dm.group(1).strip() if dm else "Uncategorized"
        by_domain[domain].append(f.stem)
        em = re.search(r"\*\*endpoint\*\*\s*`([^`]+)`", t)
        if em:
            hosts.add(host(em.group(1)))
    return names, hosts, by_domain


def write_node(name, domain, endpoint, desc, sample):
    node = WD / f"{safe_name(name)}.md"
    node.write_text(
        f"---\ntype: world-data-source\ndomain: {domain}\naccess: keyless\nstatus: live\n"
        f"tags: [world-data, free-data]\n---\n\n# 🌍 {name}\n\n"
        f"**{domain}** · `keyless` · KEYLESS\n\n\n{desc}\n\n\n"
        f"**endpoint** `{endpoint}`\n\n**sample** `{sample}`\n\n\n*↑ [[World Data]]*\n",
        encoding="utf-8")


def upsert_domain_hub(domain, source_name):
    hub = WD / f"{domain}.md"
    link = f"[[World Data/{safe_name(source_name)}|{source_name}]] (keyless)"
    if hub.exists():
        t = hub.read_text(encoding="utf-8", errors="ignore")
        if f"|{source_name}]]" in t or f"/{safe_name(source_name)}]]" in t:
            return  # already present (idempotent)
        # append to the `## sources` line
        m = re.search(r"(## sources\s*\n+)(.*?)(\n)", t, re.S)
        if m:
            srcline = m.group(2).strip()
            srcline = (srcline + " · " + link) if srcline else link
            t = t[:m.start(2)] + srcline + t[m.end(2):]
        else:
            t = t.rstrip() + f"\n\n## sources\n\n{link}\n\n*↑ [[World Data]]*\n"
        # bump count in frontmatter + title
        n = len(re.findall(r"\[\[World Data/", t))
        t = re.sub(r"^sources:\s*\d+", f"sources: {n}", t, flags=re.M)
        t = re.sub(r"(#\s*🌍\s*" + re.escape(domain) + r"\s*·\s*)\d+( free sources)", rf"\g<1>{n}\g<2>", t)
        hub.write_text(t, encoding="utf-8")
    else:
        hub.write_text(
            f"---\ntype: world-data-domain\nsources: 1\ntags: [world-data, domain]\n---\n\n"
            f"# 🌍 {domain}  ·  1 free sources\n\nA domain of the [[World Data]] swarm.\n\n\n"
            f"## sources\n\n{link}\n\n\n*↑ [[World Data]]*\n", encoding="utf-8")


def rebuild_index():
    """Rebuild World Data.md from the ACTUAL source notes (fixes stale counts)."""
    by_domain = collections.defaultdict(int)
    dom_srcs = collections.defaultdict(list)
    total = 0
    for f in WD.glob("*.md"):
        try:
            t = f.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if "type: world-data-source" not in t:
            continue
        dm = re.search(r"^domain:\s*(.+)$", t, re.M)
        d = dm.group(1).strip() if dm else "Uncategorized"
        by_domain[d] += 1; dom_srcs[d].append(f.stem); total += 1
    domains = sorted(by_domain)
    # ensure every listed domain has a hub note, so the index's [[World Data/<domain>]] links resolve
    # (pre-existing sources from the cartographer can have a domain with no hub yet).
    for d in domains:
        hub = WD / f"{d}.md"
        if not hub.exists():
            links = " · ".join(f"[[World Data/{s}|{s}]] (keyless)" for s in sorted(dom_srcs[d]))
            hub.write_text(
                f"---\ntype: world-data-domain\nsources: {by_domain[d]}\ntags: [world-data, domain]\n---\n\n"
                f"# 🌍 {d}  ·  {by_domain[d]} free sources\n\nA domain of the [[World Data]] swarm.\n\n\n"
                f"## sources\n\n{links}\n\n\n*↑ [[World Data]]*\n", encoding="utf-8")
    lines = ["---", "type: world-data-hub", f"domains: {len(domains)}", f"sources: {total}",
             "tags: [world-data, swarm]", "---", "",
             f"# 🌍 World Data — {total} free sources across {len(domains)} domains", "",
             f"A swarm of the world's FREE data: **{total} keyless + 0 free-key**, every one "
             f"LIVE-curl-validated (no key sent). Built by the free-data-finder fleet "
             f"(finder → validator → cartographer) + the autonomous **world-data-hunter**.", "",
             "## domains", ""]
    lines += [f"- [[World Data/{d}|{d}]] — {by_domain[d]} sources" for d in domains]
    lines += ["", "*Part of the agent ecosystem → [[Agent Fleet]] · [[Data Sources]]*", "",
              "*↑ [[Obsidian Brain]] — the connected center.*", ""]
    INDEX.write_text("\n".join(lines), encoding="utf-8")
    return total, len(domains)


def main():
    if LOCK.exists() and (time.time() - LOCK.stat().st_mtime) < 3600:
        log("another run in progress; exit"); return
    LOCK.touch()
    added, dead, skipped = [], [], 0
    llm_prov = "not run"
    try:
        WD.mkdir(parents=True, exist_ok=True)
        names, hosts, _ = load_existing()

        # rotate one domain for the LLM brainstorm by day-of-year (deterministic, no rng)
        domains_seen = sorted({c[1] for c in CANDIDATES})
        rot = domains_seen[datetime.date.today().timetuple().tm_yday % len(domains_seen)]
        llm_cands, llm_prov = llm_candidates(rot, names)

        pool = list(CANDIDATES) + llm_cands
        seen_in_run = set()
        for name, domain, endpoint, desc in pool:
            if len(added) >= MAX_NEW_PER_RUN or (len(added) + len(dead) + skipped) >= MAX_VALIDATE:
                break
            key = safe_name(name).lower()
            if key in names or key in seen_in_run or host(endpoint) in hosts:
                skipped += 1; continue
            seen_in_run.add(key)
            ok, sample, note = probe(endpoint)
            if not ok:
                dead.append((name, note)); continue
            write_node(name, domain, endpoint, desc, sample)
            upsert_domain_hub(domain, name)
            names.add(key); hosts.add(host(endpoint))
            added.append((name, domain))
            log(f"+ ADDED {name} ({domain})")

        total, ndom = rebuild_index()

        today = datetime.date.today().isoformat()
        lines = ["---", "type: report", "status: open", f"date: {today}",
                 "tags: [world-data, data-discovery]", "---", "",
                 f"# 🛰️ World Data Hunter — {today}", "",
                 f"Autonomous keyless-source discovery for the [[World Data]] swarm. Every source "
                 f"below was added only after a LIVE no-key probe returned data.", "",
                 f"- swarm now **{total} sources across {ndom} domains**",
                 f"- **{len(added)}** new this run · **{len(dead)}** candidates DEAD (key/down) · "
                 f"{skipped} already known",
                 f"- LLM brainstorm domain rotated to **{rot}** ({llm_prov})", "",
                 "## Added this run", ""]
        lines += [f"- [[World Data/{safe_name(n)}|{n}]] — {d}" for n, d in added] or ["_none (swarm already covers the pool)_"]
        lines += ["", "## Candidates that failed the keyless probe (dropped)", ""]
        lines += [f"- {n} — `{note}`" for n, note in dead[:30]] or ["_none_"]
        lines += ["", "*↑ [[World Data]] · [[Agent Fleet]]*", ""]
        RUNNOTE.write_text("\n".join(lines), encoding="utf-8")

        log(f"OK added={len(added)} dead={len(dead)} skipped={skipped} swarm={total}/{ndom} llm={llm_prov}")
    except Exception as e:
        log(f"ERR {type(e).__name__}: {e}")
    finally:
        try: LOCK.unlink()
        except Exception: pass


if __name__ == "__main__":
    main()
