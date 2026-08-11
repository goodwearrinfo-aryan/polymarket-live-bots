#!/usr/bin/env python3
"""wiring_keeper.py — keeps the vault live-map (tasks + connectors) wired & self-healing.

Runs each watchdog cycle, self-throttled (~30 min). Read-mostly; the only writes are
maintenance of the live-map that activity_light / connector_light produce:

1) PRUNE ORPHANS — delete Connectors/<x>.md, Live/<x>.md and stale category hubs whose
   source / task / category no longer exists, so removed items don't leave dead graph nodes.
2) ASSERT GRAPH COLOR GROUPS — re-add any expected tag color group (#running, #task,
   #online, #offline, #connector-hub) missing from graph.json (Obsidian sometimes drops
   externally-added groups). Best-effort write, logged.
3) PROXY-HEAL BLOCKED CONNECTORS — a connector down for >=3 consecutive keeper runs whose
   r.jina.ai-proxied URL IS reachable gets repointed through the proxy in data_sources.py
   (the Kalshi pattern, generalised) so blocked feeds auto-reroute instead of staying red.
   Conservative: persistent-only, never re-proxies, capped 5/run, network-outage-safe
   (a whole-network outage makes the proxy unreachable too, so nothing repoints), logged.

Writes a Wiring Keeper.md status note. Fail-soft, atomic, keyless. Log: /tmp/wiring_keeper.log
"""
import os, re, sys, json, time, tempfile, subprocess, urllib.request, socket
from datetime import datetime

sys.path.insert(0, os.path.expanduser("~/Documents/polymarket"))
HERE = os.path.expanduser("~/Documents/polymarket")
VAULT = os.path.expanduser("~/Documents/PolymarketVault")
CONN_DIR = os.path.join(VAULT, "Connectors")
LIVE_DIR = os.path.join(VAULT, "Live")
GRAPH = os.path.join(VAULT, ".obsidian", "graph.json")
DS_FILE = os.path.join(HERE, "data_sources.py")
NOTE = os.path.join(VAULT, "Wiring Keeper.md")
KSTATE = os.path.join(HERE, ".wiring_keeper_state.json")
CSTATE = os.path.join(HERE, ".connector_state.json")
LAST = os.path.join(HERE, ".wiring_keeper_last")
LOG = "/tmp/wiring_keeper.log"
THROTTLE = 1800
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) wiring-keeper"

EXPECTED_GROUPS = [
    ("tag:#running", 2278750), ("tag:#task -tag:#running", 4674921),
    ("tag:#online", 2278750), ("tag:#offline", 15680580),
    ("tag:#connector-hub", 15067115),
]

ALERT_TARGETS = ["krisharyan@icloud.com", "+918449447444"]
_OSA = ('on run argv\ntell application "Messages"\n'
        '  set s to 1st service whose service type = iMessage\n'
        '  set b to buddy (item 1 of argv) of s\n'
        '  send (item 2 of argv) to b\nend tell\nend run')


def alert(msg):
    """Loud iMessage to the owner — subprocess argv (no shell-quoting bug). Best-effort."""
    for t in ALERT_TARGETS:
        try:
            subprocess.run(["osascript", "-e", _OSA, t, msg], capture_output=True, text=True, timeout=6)
        except Exception:
            pass


def _commit_heal(names):
    """Auto-commit the data_sources.py reroute so a self-heal is never a silent dangling edit."""
    try:
        subprocess.run(["git", "-C", HERE, "add", "data_sources.py"], capture_output=True, timeout=10)
        subprocess.run(["git", "-C", HERE, "commit", "-m",
                        "auto(wiring_keeper): reroute " + ", ".join(names) + " via r.jina.ai (direct host blocked)"],
                       capture_output=True, timeout=15)
    except Exception as e:
        log(f"git commit failed: {e}")


def log(msg):
    try:
        with open(LOG, "a") as f:
            f.write(f"[{datetime.now():%F %T}] {msg}\n")
    except Exception:
        pass


def _atomic(path, text):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def prune_orphans(ds, al, cl):
    removed = []
    expected_conn = {cl.slug(s["name"]) for s in ds.SOURCES}
    expected_hub = {cl.hub_name(s["category"]) for s in ds.SOURCES}
    if os.path.isdir(CONN_DIR):
        for fn in os.listdir(CONN_DIR):
            if not fn.endswith(".md"):
                continue
            base = fn[:-3]
            keep = base in expected_hub if base.startswith("Connectors — ") else base in expected_conn
            if not keep:
                os.remove(os.path.join(CONN_DIR, fn)); removed.append(f"Connectors/{fn}")
    expected_live = {disp for _, disp, _ in al.MONITORED}
    if os.path.isdir(LIVE_DIR):
        for fn in os.listdir(LIVE_DIR):
            if fn.endswith(".md") and fn[:-3] not in expected_live:
                os.remove(os.path.join(LIVE_DIR, fn)); removed.append(f"Live/{fn}")
    return removed


def assert_color_groups():
    try:
        g = json.load(open(GRAPH))
    except Exception:
        return []
    have = {c.get("query") for c in g.get("colorGroups", [])}
    added = []
    for q, rgb in EXPECTED_GROUPS:
        if q not in have:
            g.setdefault("colorGroups", []).append({"query": q, "color": {"a": 1, "rgb": rgb}})
            added.append(q)
    if added:
        try:
            _atomic(GRAPH, json.dumps(g, indent=2))
        except Exception as e:
            log(f"color-group write failed: {e}"); return []
    return added


def _reachable(url, timeout=12):
    try:
        urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=timeout).read(1)
        return True
    except urllib.error.HTTPError:
        return True
    except Exception:
        return False


def proxy_heal(ds):
    try:
        health = json.load(open(CSTATE))     # name -> currently-online bool
    except Exception:
        return []
    try:
        ks = json.load(open(KSTATE))
    except Exception:
        ks = {}
    counts = ks.get("down_counts", {})
    url_by_name = {s["name"]: s["url"] for s in ds.SOURCES}
    healed = []
    cap = 5
    for name, online in health.items():
        if online:
            counts[name] = 0
            continue
        counts[name] = counts.get(name, 0) + 1
        url = url_by_name.get(name, "")
        if cap <= 0 or counts[name] < 3 or not url or "r.jina.ai" in url:
            continue
        proxied = f"https://r.jina.ai/{url}"
        if not _reachable(proxied):
            continue                          # proxy can't reach it either → don't repoint
        # repoint in data_sources.py (exact, single occurrence)
        try:
            txt = open(DS_FILE, encoding="utf-8").read()
            needle = f'"url": \'{url}\''
            if txt.count(needle) == 1:
                _atomic(DS_FILE, txt.replace(needle, f'"url": \'{proxied}\''))
                healed.append(name); counts[name] = 0; cap -= 1
                log(f"PROXY-HEAL repointed '{name}' via r.jina.ai")
        except Exception as e:
            log(f"proxy-heal write failed for {name}: {e}")
    ks["down_counts"] = counts
    try:
        json.dump(ks, open(KSTATE, "w"))
    except Exception:
        pass
    return healed


def main(force=False):
    try:
        if not force and (time.time() - os.path.getmtime(LAST)) < THROTTLE:
            return
    except OSError:
        pass
    import data_sources as ds
    import connector_light as cl
    import activity_light as al

    removed = prune_orphans(ds, al, cl)
    added = assert_color_groups()
    healed = proxy_heal(ds)

    if healed:
        # loud + clean: a self-heal that rewrites source must never be silent
        _commit_heal(healed)
        alert("🧷 wiring_keeper rerouted " + ", ".join(healed) +
              " via r.jina.ai (direct host blocked) — data_sources.py auto-committed. Review when you can.")
    if removed: log(f"pruned {len(removed)} orphan nodes: {removed}")
    if added:   log(f"re-added {len(added)} dropped color groups: {added}")

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    body = (f"---\ntype: dashboard\ncssclasses: [dashboard]\n---\n\n"
            f"> [!banner] 🧷 Wiring Keeper\n> Keeps the live-map self-healed · last run {now}\n\n"
            f"## Last pass\n"
            f"- **Orphan nodes pruned:** {len(removed)}\n"
            f"- **Color groups re-asserted:** {len(added)} {added if added else ''}\n"
            f"- **Connectors proxy-healed:** {len(healed)} {healed if healed else ''}\n\n"
            f"> [!note]- What it maintains\n"
            f"> Prunes dead Connectors/Live nodes, re-adds graph color groups Obsidian drops, and "
            f"auto-reroutes persistently-blocked connectors through the r.jina.ai reader. "
            f"Runs every watchdog cycle (throttled ~30 min). Read-mostly, keyless, fail-soft.\n\n"
            f"→ [[Connectors]] · [[Activity]] · [[Home]]\n")
    _atomic(NOTE, body)
    with open(LAST, "w") as f:
        f.write(str(int(time.time())))


if __name__ == "__main__":
    try:
        main(force=("force" in sys.argv))
    except Exception as e:
        log(f"FATAL: {e}")
