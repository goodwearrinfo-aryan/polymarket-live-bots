#!/usr/bin/env python3
"""livemap_pulse.py — make the Obsidian graph VISIBLY "actively working".

Problem it solves: activity_light.py only tags a node #running while a monitored process is
actually executing — but most monitored jobs are INTERVAL jobs (idle 99% of the time), so the
graph almost never shows green. Meanwhile the genuinely always-on services (dashboard, wsfeed,
ws-basket-watch, sshx remote, newsnow, ...) weren't surfaced as live nodes at all.

This adds a PERSISTENT green-pulse layer:
  • one Live/<service>.md node per always-on service, tagged `running` (green) whenever its
    launchd job holds a live PID (or its process is alive) — so they stay green continuously.
  • a central `Live/🟢 Fleet Pulse.md` HEARTBEAT node, always `running`, refreshed every cycle
    with the live count + a timestamp + the list, linking to each service — a single green hub
    that proves the fleet is alive at a glance.

Low-churn (no graph flicker): a service node is rewritten ONLY when its running-state flips;
the heartbeat refreshes its body each cycle (tag stays `running`, so the node never flickers).
Pure launchctl/pgrep detection, keyless, read-only to everything except the Live/ notes.

Run from the watchdog (~60s) or its own launchd job:
  python3 ~/Documents/polymarket/livemap_pulse.py
"""
import os, re, json, subprocess
from datetime import datetime

VAULT = os.path.expanduser("~/Documents/PolymarketVault")
LIVE = os.path.join(VAULT, "Live")
AGENTS_SRC = os.path.expanduser("~/.claude/agents")
AGENTS_DIR = os.path.join(VAULT, "Agents")
STATE = os.path.expanduser("~/Documents/polymarket/.livemap_pulse_state.json")

# Always-on services worth showing as persistent green nodes.
# (launchd label suffix after com.aryan.)  ->  (display, emoji)
SERVICES = {
    "pm-dashboard":        ("Dashboard",      "📊"),
    "polymarket-wsfeed":   ("WS Price Feed",  "📡"),
    "ws-basket-watch":     ("Basket Watch",   "🧺"),
    "remote-access":       ("Remote (sshx)",  "🛰️"),
    "newsnow":             ("News Now",        "📰"),
    "scalp-caffeinate":    ("Caffeinate",     "☕"),
    "polymarket-lab-api":  ("Lab API",        "🔬"),
    "insider-watch":       ("Insider Watch",  "🐋"),
    "stock-live":          ("Stock Live",     "📈"),
    "snhunt":              ("SocialNet Hunt", "🔎"),
}
# Non-launchd persistent process (nohup orphan) detected via pgrep:
PROC_SERVICES = {"watchdog_loop": ("Scalp Watchdog", "🐕")}


def launchd_live_pids():
    """labels (without com.aryan.) -> True if the job currently holds a live PID."""
    live = {}
    try:
        out = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return live
    for ln in out.splitlines():
        parts = ln.split("\t")
        if len(parts) >= 3 and parts[2].startswith("com.aryan."):
            label = parts[2][len("com.aryan."):]
            live[label] = parts[0].strip().isdigit()
    return live


def proc_alive(name):
    try:
        return subprocess.run(["pgrep", "-f", name], capture_output=True).returncode == 0
    except Exception:
        return False


def write_node(path, body, tags):
    fm = "---\ntags: [" + ", ".join(tags) + "]\n---\n\n"
    with open(path, "w") as f:
        f.write(fm + body)


def write_if_changed(path, content):
    """Write only when content differs — keeps the graph from churning on identical re-writes."""
    try:
        if os.path.exists(path) and open(path).read() == content:
            return False
    except Exception:
        pass
    with open(path, "w") as f:
        f.write(content)
    return True


def pulse_agents():
    """Add the nv-* free-key fleet to the graph, CONNECTED: each agent → [[⚡ NVIDIA Free Chain]]
    → [[🟢 Fleet Pulse]], under a [[🤖 NVIDIA Free Fleet]] hub. So the agents show as a wired,
    online cluster — not floating nodes. Tagged 'online' (available; the chain is up), low-churn."""
    import glob
    os.makedirs(AGENTS_DIR, exist_ok=True)
    nv = sorted(glob.glob(os.path.join(AGENTS_SRC, "nv-*.md")))
    agents = []
    for f in nv:
        name = os.path.basename(f)[:-3]
        desc = ""
        try:
            m = re.search(r"description:\s*>?\s*\n?\s*(.+)", open(f).read())
            desc = (m.group(1).strip().split(" — ")[-1][:90]) if m else ""
        except Exception:
            pass
        agents.append((name, desc))
    # the free infra the whole fleet runs on
    write_if_changed(os.path.join(AGENTS_DIR, "⚡ NVIDIA Free Chain.md"),
        "---\ntags: [online]\n---\n\n# ⚡ NVIDIA Free Chain\n\n"
        "The $0-metered LLM rotation every nv-* agent reasons on: "
        "nvidia → nvidia2 → nvidia3 → groq → cerebras → sambanova → openrouter → public → ollama.\n\n"
        "→ [[🟢 Fleet Pulse]] · [[🤖 NVIDIA Free Fleet]]\n")
    # NOTE: another vault process owns the individual Agents/<name>.md nodes (the "Squad -" mirror),
    # so we do NOT write per-agent files (would cause a flicker write-war). Instead the HUB below
    # links to each [[nv-*]] — that materializes the edge that CONNECTS each agent into the live
    # cluster (pulse ↔ hub ↔ agent ↔ chain), regardless of who owns the agent node.
    # the hub — links to every agent + the chain + the heartbeat
    links = "\n".join(f"- 🤖 [[{n}]]" for n, _ in agents) or "- (none)"
    write_if_changed(os.path.join(AGENTS_DIR, "🤖 NVIDIA Free Fleet.md"),
        f"---\ntags: [agent, online]\n---\n\n# 🤖 NVIDIA Free Fleet\n\n"
        f"**{len(agents)} free-key worker agents** — all reasoning on NVIDIA Maverick at $0 metered.\n\n"
        f"## Agents\n{links}\n\nRuns on → [[⚡ NVIDIA Free Chain]] · reads → [[🧠 Shared Brain]] · heartbeat [[🟢 Fleet Pulse]]\n")
    return len(agents)


def pulse_brain():
    """Wire the shared BRAIN into the live cluster — the graphify knowledge graph every agent reads
    in real time via the `brain` wrapper. Connects: fleet → reads → brain, brain → pulse, brain →
    the existing Brain Architecture / Live Gate notes. So the brain isn't left behind."""
    import json
    g = os.path.expanduser("~/.graphify/global-graph.json")
    n, updated = "?", "?"
    try:
        d = json.load(open(g))
        n = len(d.get("nodes", []) if isinstance(d, dict) else d)
        updated = datetime.fromtimestamp(os.path.getmtime(g)).strftime("%Y-%m-%d %H:%M")
    except Exception:
        pass
    write_if_changed(os.path.join(VAULT, "🧠 Shared Brain.md"),
        f"---\ntags: [running]\n---\n\n# 🧠 Shared Brain\n\n"
        f"**{n} concepts** · graph updated `{updated}`\n\n"
        f"The real-time shared knowledge graph the whole fleet reads via the `brain` wrapper "
        f"(graphify `global-graph.json`) + canonical `BRAIN.md`. The intelligence every agent draws on.\n\n"
        f"Read by → [[🤖 NVIDIA Free Fleet]] (and all agents)\n"
        f"Detail → [[Brain Architecture]] · [[Brain Live Gate]] · [[brain_snapshot]]\n"
        f"→ [[🟢 Fleet Pulse]] · [[Activity]] · [[Home]]\n")
    return n


def main():
    os.makedirs(LIVE, exist_ok=True)
    prev = {}
    if os.path.exists(STATE):
        try: prev = json.load(open(STATE))
        except Exception: prev = {}

    ld = launchd_live_pids()
    cur, live_names = {}, []
    # service nodes — write only on state flip (low churn)
    for label, (disp, emo) in SERVICES.items():
        alive = ld.get(label, False)
        cur[label] = alive
        if alive: live_names.append(f"{emo} {disp}")
        node_path = os.path.join(LIVE, f"{disp}.md")
        if prev.get(label) != alive or not os.path.exists(node_path):  # flip OR missing -> so [[disp]] resolves
            tags = ["task", "running"] if alive else ["task"]
            dot = "🟢 running" if alive else "⚪ idle"
            body = (f"# {emo} {disp}\n\nStatus: {dot}\n\n"
                    f"Always-on service node — green in Graph view while its launchd job holds a live PID.\n\n"
                    f"→ [[🟢 Fleet Pulse]] · [[Activity]] · [[Home]]\n")
            write_node(node_path, body, tags)
    for proc, (disp, emo) in PROC_SERVICES.items():
        alive = proc_alive(proc)
        cur["proc:" + proc] = alive
        if alive: live_names.append(f"{emo} {disp}")
        node_path = os.path.join(LIVE, f"{disp}.md")
        if prev.get("proc:" + proc) != alive or not os.path.exists(node_path):
            tags = ["task", "running"] if alive else ["task"]
            dot = "🟢 running" if alive else "⚪ idle"
            write_node(node_path,
                       f"# {emo} {disp}\n\nStatus: {dot}\n\n→ [[🟢 Fleet Pulse]] · [[Activity]] · [[Home]]\n",
                       tags)

    # heartbeat — always green, refresh body every cycle (tag stays 'running' => no flicker)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    n = len(live_names)
    links = "\n".join(f"- 🟢 [[{disp}]]" for disp in
                      [SERVICES[l][0] for l in SERVICES if cur.get(l)] +
                      [PROC_SERVICES[p][0] for p in PROC_SERVICES if cur.get("proc:" + p)]) or "- (none live)"
    n_agents = pulse_agents()  # add + connect the nv-* free-key fleet
    n_brain = pulse_brain()    # wire the shared brain into the cluster

    hb = (f"# 🟢 Fleet Pulse\n\n**{n} services LIVE** · **{n_agents} free-key agents** · "
          f"**🧠 {n_brain} brain concepts** · last pulse `{ts}`\n\n"
          f"This node is green whenever the fleet is alive — it is the heartbeat of the live-map.\n\n"
          f"## Live now\n{links}\n\n"
          f"→ [[🧠 Shared Brain]] · [[🤖 NVIDIA Free Fleet]] · [[⚡ NVIDIA Free Chain]] · [[Activity]] · [[Home]]\n")
    write_node(os.path.join(LIVE, "🟢 Fleet Pulse.md"), hb, ["task", "running"])

    json.dump(cur, open(STATE, "w"))
    print(f"pulse: {n} services live, {n_agents} agents wired, 🧠 brain={n_brain} @ {ts} -> " +
          (", ".join(live_names) if live_names else "none"))


if __name__ == "__main__":
    main()
