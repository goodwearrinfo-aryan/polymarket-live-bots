#!/usr/bin/env python3
"""activity_light.py — live "activity light" for the Obsidian vault.

Two outputs, both refreshed every watchdog cycle (~60s); pure ps/launchctl detection,
read-only to the bot, keyless, fail-soft, atomic/low-churn writes:

1) PolymarketVault/Activity.md — a note with a pulsing green dot per task performing
   now (CSS in .obsidian/snippets/aesthetic.css). Banner glows green when active.

2) PolymarketVault/Live/<task>.md — one node per monitored task. When the task is
   running it carries the `#running` tag, so the graph color group `tag:running`
   renders its node BRIGHT GREEN; idle tasks fall back to the dim `tag:task` colour.
   These notes are rewritten ONLY when a task's running-state flips (low churn /
   no graph flicker). This is how running tasks "light up" in Graph view.
"""
import os, re, json, subprocess, tempfile
from datetime import datetime

HERE = os.path.expanduser("~/Documents/polymarket")
VAULT = os.path.expanduser("~/Documents/PolymarketVault")
OUT = os.path.join(VAULT, "Activity.md")
LIVE_DIR = os.path.join(VAULT, "Live")
STATE = os.path.join(HERE, ".activity_graph_state.json")
SELF = "activity_light.py"

# Curated fleet shown as graph nodes: (key matched in process cmd, display, emoji)
MONITORED = [
    ("scalp_engine.py",     "scalp_engine",     "⚙️"),
    ("polymarket_bot.py",   "polymarket_bot",   "\U0001f916"),
    ("scalp_lab.py",        "scalp_lab",        "\U0001f9ea"),
    ("watchdog_loop.sh",    "watchdog",         "\U0001f6e1️"),
    ("insider_watch.py",    "insider_watch",    "\U0001f40b"),
    ("sybil_watch.py",      "sybil_watch",      "\U0001f441️"),
    ("sniper_hunt.py",      "sniper_hunt",      "\U0001f3af"),
    ("hermes_news.py",      "hermes_news",      "\U0001f4f0"),
    ("ws_basket_watch.py",  "ws_basket_watch",  "\U0001f9fa"),
    ("ws_pricefeed.py",     "ws_pricefeed",     "\U0001f4c8"),
    ("lab_api.py",          "lab_api",          "\U0001f50c"),
    ("agents_dashboard.py", "agents_dashboard", "\U0001f4df"),
    ("signal_hub.py",       "signal_hub",       "\U0001f4e1"),
    ("pm_agents_24x7.py",   "pm_agents",        "\U0001f9e0"),
    ("oddpool",             "oddpool",          "\U0001f30a"),
    ("connector_light.py",  "connector_light",  "\U0001f50c"),  # autonomous connector-liveness agent
    ("wiring_keeper.py",    "wiring_keeper",    "\U0001f9f7"),  # autonomous live-map self-heal agent
    ("keyed_light.py",      "keyed_light",      "\U0001f511"),  # autonomous keyed-services liveness agent
    ("remote_command_agent.py", "remote_command", "\U0001f4f1"),  # autonomous remote-command listener
    ("remote_access.py",    "remote_access",    "\U0001f517"),  # persistent sshx remote terminal
    ("world_read.py",       "world_read",       "\U0001f30d"),  # 24/7 keyless world brief (geo/sports/markets)
    ("fleet_self_upgrade.py", "fleet_self_upgrade", "\U0001f527"),  # tiered fleet self-upgrade engine
    # edge-bots fleet (~/Documents/edge-bots, separate repo) — live = launchd label loaded
    ("funding_carry_bot.py", "edge_funding",  "\U0001f4b0"),  # 💰 funding carry
    ("basis_arb_bot.py",     "edge_basis",    "⚖️"),   # ⚖️ cross-venue basis
    ("vol_vrp_bot.py",       "edge_vol",      "\U0001f32a️"),  # 🌪️ vol/VRP
    ("pairs_statarb_bot.py", "edge_pairs",    "\U0001f517"),  # 🔗 stat-arb pairs
    ("edge_gate.py",         "edge_gate",     "\U0001f6aa"),  # 🚪 graduation gate
    ("edge_watchdog.py",     "edge_watchdog", "\U0001f415"),  # 🐕 reliability heal
    ("edge_analyst.py",      "edge_analyst",  "\U0001f4cb"),  # 📋 daily brief
    ("discover.py",          "edge_discover", "\U0001f52d"),  # 🔭 mispricing hunter
    ("edge_tick_guard.py",   "edge_tickguard","\U0001f9fc"),  # 🧼 data-integrity sentinel
    ("edge_capacity_auditor.py", "edge_capacity", "\U0001f4cf"),  # 📏 capacity-realism sentinel
    ("edge_spend_warden.py", "edge_spendwarden", "\U0001f6dc"),  # 🛜 free-chain health sentinel
    ("control_arms.py",      "edge_control",  "\U0001f3b2"),  # 🎲 random-entry control twins
    ("profit_monitor.py",    "edge_profit",   "\U0001f4b5"),  # 💵 multi-channel precision profit monitor
    ("xfund_arb_bot.py",     "edge_xfund",    "\U0001f501"),  # 🔁 cross-venue funding arb
]


def running_procs():
    """(script, etime) for bot python/bash processes executing right now."""
    found = {}
    try:
        ps = subprocess.run(["ps", "-axo", "pid,etime,command"],
                            capture_output=True, text=True, timeout=10).stdout
        for line in ps.splitlines():
            if "/Documents/polymarket/" not in line or "grep" in line:
                continue
            m = re.search(r"/Documents/polymarket/([\w./-]+\.(?:py|sh))", line)
            if not m:
                continue
            script = os.path.basename(m.group(1))
            if script == SELF:
                continue
            parts = line.split(None, 2)
            found.setdefault(script, parts[1] if len(parts) > 1 else "?")
    except Exception:
        pass
    return sorted(found.items())


def launchd_running():
    jobs = []
    try:
        out = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=10).stdout
        for line in out.splitlines():
            cols = line.split("\t")
            if len(cols) >= 3 and cols[2].startswith("com.aryan.") and cols[0] not in ("-", "PID"):
                jobs.append(cols[2].replace("com.aryan.", ""))
    except Exception:
        pass
    return sorted(set(jobs))


def launchd_loaded():
    """All com.aryan.* labels REGISTERED with launchd (loaded), even if idle (PID '-').
    Distinct from launchd_running() which lists only jobs executing this instant."""
    labels = set()
    try:
        out = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=10).stdout
        for line in out.splitlines():
            cols = line.split("\t")
            if len(cols) >= 3 and cols[2].startswith("com.aryan."):
                labels.add(cols[2].replace("com.aryan.", ""))
    except Exception:
        pass
    return labels


def write_activity_note(procs, jobs):
    now = datetime.now().strftime("%H:%M:%S")
    n = len(procs)
    cls = "live" if n > 0 else "idle"
    dot = '<span class="live-dot"></span>'
    idot = '<span class="idle-dot"></span>'
    L = ["---", "type: dashboard", f"cssclasses: [dashboard, activity, {cls}]", "---", ""]
    if n > 0:
        L += [f"> [!banner] {dot} {n} task{'s' if n != 1 else ''} performing now",
              f"> Live process activity · refreshed {now} · dots pulse while work runs"]
    else:
        L += [f"> [!banner] {idot} Idle — nothing executing this second",
              f"> Live process activity · refreshed {now}"]
    L += ["", "## ⚡ Performing now", ""]
    if procs:
        for s, e in procs:
            L.append(f"{dot} **{s}** — running {e}<br>")
    else:
        L.append("_No bot script executing this instant._")
    L += ["", "## \U0001f6f0 Supervised services (live PID)", ""]
    if jobs:
        for j in jobs:
            L.append(f"{dot} {j}<br>")
    else:
        L.append("_No launchd service reporting a live PID._")
    L += ["", "> [!note]- How this works",
          "> Regenerated every watchdog cycle by `activity_light.py` (pure `ps` + `launchctl`, "
          "read-only). Running tasks also glow green in **Graph view** via the `Live/` nodes.",
          "", "→ [[Home]] · [[Status Hub]] · [[Dashboards]]", ""]
    _atomic_write(OUT, "\n".join(L))


def update_graph_nodes(running_map):
    """Maintain Live/<task>.md nodes, tagging #running. Write only on state flip."""
    os.makedirs(LIVE_DIR, exist_ok=True)
    try:
        prev = json.load(open(STATE))
    except Exception:
        prev = {}
    for key, display, emoji in MONITORED:
        run = running_map.get(display, False)
        path = os.path.join(LIVE_DIR, f"{display}.md")
        if prev.get(display) == run and os.path.exists(path):
            continue  # unchanged → no rewrite (avoids graph flicker)
        tags = "[task, running]" if run else "[task]"
        badge = "\U0001f7e2 **running**" if run else "⚪ idle"
        body = (f"---\ntags: {tags}\n---\n\n# {emoji} {display}\n\n"
                f"Status: {badge}\n\nLive fleet node — lights up green in Graph view while running.\n"
                f"\n→ [[Activity]] · [[Home]]\n")
        _atomic_write(path, body)
    json.dump({d: running_map.get(d, False) for _, d, _ in MONITORED}, open(STATE, "w"))


def _atomic_write(path, text):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


# Periodic launchd agents that ARE autonomously alive whenever their job is loaded,
# even though each execution is brief (so process-detection alone would show them idle).
# display -> launchd label suffix (com.aryan.<suffix>).
LAUNCHD_AUTONOMOUS = {"connector_light": "connector-light", "wiring_keeper": "wiring-keeper",
                      "keyed_light": "keyed-light", "remote_command": "remote-command",
                      "world_read": "world-read", "fleet_self_upgrade": "fleet-self-upgrade",
                      "edge_funding": "edge-funding", "edge_basis": "edge-basis",
                      "edge_vol": "edge-vol", "edge_pairs": "edge-pairs",
                      "edge_gate": "edge-gate", "edge_watchdog": "edge-watchdog",
                      "edge_analyst": "edge-analyst", "edge_discover": "edge-discover",
                      "edge_tickguard": "edge-tickguard", "edge_capacity": "edge-capacity",
                      "edge_spendwarden": "edge-spendwarden", "edge_control": "edge-control",
                      "edge_profit": "edge-profit", "edge_xfund": "edge-xfund"}


def main():
    procs = running_procs()
    jobs = launchd_running()
    names = [s for s, _ in procs]
    loaded = launchd_loaded()   # registered (autonomous) jobs, incl. idle-between-runs
    running_map = {}
    for key, display, _ in MONITORED:
        live = any(key in n for n in names)
        lbl = LAUNCHD_AUTONOMOUS.get(display)
        if lbl and lbl in loaded:   # autonomous agent: loaded launchd job = live
            live = True
        running_map[display] = live
    write_activity_note(procs, jobs)
    update_graph_nodes(running_map)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        try:
            with open(OUT, "a", encoding="utf-8") as f:
                f.write(f"\n<!-- activity_light error: {e} -->\n")
        except Exception:
            pass
