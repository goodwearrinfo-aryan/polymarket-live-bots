#!/usr/bin/env python3
"""connector_light.py — live health light for ALL of the bot's data connectors,
wired into the graph by category → consuming subsystem.

For each keyless connector in data_sources.py (166 feeds across 14 categories):
  • writes Connectors/<name>.md — a graph node tagged #connector + #online/#offline
    (green=online / red=down via the graph color groups), linked to its CATEGORY HUB.
  • writes Connectors/Connectors — <category>.md — a category HUB node (tag #connector-hub)
    that links to the bot SUBSYSTEMS that consume that category (so the graph shows
    which feed powers what), plus [[Connectors]] / [[Data Sources]].
  • writes Connectors.md — the summary board.

Parallel reachability check, self-throttled (~15 min), low-churn (a node is rewritten
only when its up/down state flips). Read-only to the bot, keyless, fail-soft, atomic.

Usage: connector_light.py [force]
"""
import os, re, sys, json, time, tempfile, subprocess, urllib.request, socket
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

sys.path.insert(0, os.path.expanduser("~/Documents/polymarket"))
HERE = os.path.expanduser("~/Documents/polymarket")
VAULT = os.path.expanduser("~/Documents/PolymarketVault")
CONN_DIR = os.path.join(VAULT, "Connectors")
BOARD = os.path.join(VAULT, "Connectors.md")
STATE = os.path.join(HERE, ".connector_state.json")
LAST = os.path.join(HERE, ".connector_last")
THROTTLE = 900
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 connector-light"

# category -> bot subsystem notes that CONSUME that feed family (existing notes only)
CATEGORY_SUBSYS = {
    "crypto":                     ["crypto_touch", "funding_basis"],
    "prediction-markets":         ["basket_arb", "structural_arb", "venue_watch"],
    "sports":                     ["sports_summary"],
    "esports":                    ["sports_summary"],
    "geopolitics/world news":     ["breaking_news_agent", "news_velocity_agent"],
    "weather & natural disaster": ["ports_analysis_hub"],
    "shipping/commodities":       ["ports_analysis_hub", "Hormuz Term Structure"],
    "macro/econ/government data": ["Markets", "analyst_data_gate"],
    "commodities":                ["Markets"],
    "commodities/energy":         ["Markets"],
    "markets/commodities":        ["Markets"],
    "trade/commodities":          ["Markets"],
    "markets":                    ["Markets"],
    "general aggregator":         ["Data Sources"],
}


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def catslug(c):
    return slug(c.split("/")[0].split("&")[0])


def hub_name(cat):
    return "Connectors — " + cat.replace("/", "-").replace("&", "and")


def _curl_reachable(url, timeout):
    """Fallback probe via curl, which uses the OS TLS stack. Succeeds on hosts whose
    modern-TLS handshake this Python's urllib/OpenSSL can't negotiate (e.g. JOC, which
    rejects it with SSL TLSV1_ALERT_PROTOCOL_VERSION while curl gets HTTP 200). Any HTTP
    status = reachable; only a connection/TLS/DNS failure (curl !=0 or code 000) = down.
    Keyless, fail-soft."""
    try:
        out = subprocess.run(
            ["curl", "-sS", "-o", os.devnull, "-w", "%{http_code}", "-m", str(timeout),
             "-A", UA, url],
            capture_output=True, text=True, timeout=timeout + 3)
        return out.returncode == 0 and out.stdout.strip() not in ("", "000")
    except Exception:
        return False


def check(src, timeout=7):
    req = urllib.request.Request(src["url"], headers={"User-Agent": UA}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            r.read(1)
        return src["name"], True
    except urllib.error.HTTPError:
        return src["name"], True
    except (urllib.error.URLError, socket.timeout, ConnectionError, Exception):
        # urllib failed — often a TLS-version mismatch with a modern host this Python's
        # OpenSSL can't handshake. Confirm with curl (OS TLS stack) before declaring down.
        return src["name"], _curl_reachable(src["url"], timeout)


def _atomic(path, text):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def write_hub(cat):
    subs = CATEGORY_SUBSYS.get(cat, ["Data Sources"])
    powers = " · ".join(f"[[{s}]]" for s in subs)
    body = (f"---\ntags: [connector-hub]\n---\n\n# \U0001f50c {hub_name(cat)}\n\n"
            f"Keyless **{cat}** feeds. These power: {powers}\n\n"
            f"→ [[Connectors]] · [[Data Sources]] · [[Home]]\n")
    _atomic(os.path.join(CONN_DIR, hub_name(cat) + ".md"), body)


def main(force=False):
    try:
        if not force and (time.time() - os.path.getmtime(LAST)) < THROTTLE:
            return
    except OSError:
        pass

    import data_sources as ds
    sources = ds.SOURCES
    os.makedirs(CONN_DIR, exist_ok=True)

    results = {}
    with ThreadPoolExecutor(max_workers=32) as ex:
        for name, ok in ex.map(check, sources):
            results[name] = ok
    # retry failures once, sequentially with a longer timeout — slow feeds that time
    # out under 32-way parallel load are NOT really down; only persistent failures are.
    for s in sources:
        if not results.get(s["name"]):
            _, ok = check(s, timeout=12)
            results[s["name"]] = ok

    try:
        prev = json.load(open(STATE))
    except Exception:
        prev = {}

    by_cat = {}
    for s in sources:
        name, ok = s["name"], results.get(s["name"], False)
        by_cat.setdefault(s["category"], []).append((name, ok))
        path = os.path.join(CONN_DIR, f"{slug(name)}.md")
        if prev.get(name) == ok and os.path.exists(path):
            continue
        state = "online" if ok else "offline"
        badge = "\U0001f7e2 online" if ok else "\U0001f534 offline"
        body = (f"---\ntags: [connector, {catslug(s['category'])}, {state}]\n---\n\n"
                f"# \U0001f50c {name}\n\n**Category:** {s['category']} · **Type:** {s.get('type','?')}\n\n"
                f"**Status:** {badge}\n\n{s.get('provides','')}\n\n"
                f"`{s['url']}`\n\n→ [[{hub_name(s['category'])}]] · [[Connectors]] · [[Home]]\n")
        _atomic(path, body)

    for cat in by_cat:                       # category hubs (static; cheap to refresh)
        write_hub(cat)
    json.dump({s["name"]: results.get(s["name"], False) for s in sources}, open(STATE, "w"))

    total = len(sources)
    online = sum(1 for v in results.values() if v)
    down = total - online
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    cls = "idle" if down else "live"
    dot = '<span class="live-dot"></span>'
    L = ["---", "type: dashboard", f"cssclasses: [dashboard, activity, {cls}]", "---", "",
         f"> [!banner] {dot} Connectors — {online}/{total} online",
         f"> Live reachability of every keyless data connector · {down} down · refreshed {now}",
         "", "## \U0001f4e1 By category", ""]
    for cat in sorted(by_cat):
        items = by_cat[cat]
        on = sum(1 for _, ok in items if ok)
        flag = "" if on == len(items) else f" · ⚠️ {len(items)-on} down"
        L.append(f"- [[{hub_name(cat)}|{cat}]] — {on}/{len(items)} online{flag}")
    L += ["", "## \U0001f534 Down right now", ""]
    offline = sorted(n for n, ok in results.items() if not ok)
    L += [f"- {n}" for n in offline] if offline else ["_All connectors reachable ✅_"]
    L += ["", "> [!note]- How this works",
          "> `connector_light.py` reachability-checks every `data_sources.py` connector (~15 min, "
          "parallel, keyless). Each is a node in **Graph view** — green=online, red=down — linked to "
          "its category hub, which links to the subsystem that consumes it.",
          "", "→ [[Activity]] · [[Home]]", ""]
    _atomic(BOARD, "\n".join(L))
    with open(LAST, "w") as f:
        f.write(str(int(time.time())))


if __name__ == "__main__":
    try:
        main(force=("force" in sys.argv))
    except Exception as e:
        try:
            with open(BOARD, "a", encoding="utf-8") as f:
                f.write(f"\n<!-- connector_light error: {e} -->\n")
        except Exception:
            pass
