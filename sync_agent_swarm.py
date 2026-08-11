#!/usr/bin/env python3
"""sync_agent_swarm.py — regenerate the Obsidian agent swarm to match the LIVE fleet.

Idempotent: scans ~/.claude/agents/*.md (the live subagent defs) and rebuilds, under
~/Documents/PolymarketVault/, the per-agent notes (handoff links), cluster hubs, the
Agent Fleet catalog, and the Data Sources layer (which feed each agent pulls). Prunes
stale notes for removed agents and path-qualifies any name that collides with a graphify
Graph/ node so links resolve to the swarm note, not the stale mirror.

Run anytime the fleet drifts: `python3 sync_agent_swarm.py`. Read-only to ~/.claude/agents;
only writes the vault Agents/ + Data Sources/ + the two index notes.
"""
import os, glob, re
from collections import defaultdict

AG = os.path.expanduser("~/.claude/agents")
V = os.path.expanduser("~/Documents/PolymarketVault")
OUTD, DSD = V + "/Agents", V + "/Data Sources"


def cat(n):
    n = n.lower()
    if n.startswith("free-judge"): return "Free-LLM panel"
    if n.startswith("free-data"): return "Free-data finder fleet"
    if n.startswith("key-"): return "Key-connection fleet"
    if n.startswith("nv-"): return "NVIDIA worker fleet"
    if n == "spend-watch": return "Sentinels & wardens"
    if "peek" in n: return "Thrift peeks"
    if n.startswith("edge-"): return "Edge-hunt squad"
    if "verifier" in n: return "Verifiers"
    if n.startswith("bug-"): return "Bug-heal fleet"
    if any(x in n for x in ("sentinel", "warden", "watchdog", "guardian", "service")): return "Sentinels & wardens"
    if "scribe" in n or n == "pm-committer": return "Write / persistence"
    if n.startswith("audit-") or n in ("cro-auditor", "product-copywriter", "copy-writer", "creative-strategist", "visual-designer", "format-adapter", "video-maker", "retention-analyst"): return "Good Wearr / ads"
    if n in ("claude", "general-purpose", "explore", "plan", "bash", "statusline-setup", "claude-code-guide", "free-router", "calc", "find-code", "brain-lookup", "memory-scribe", "geo-intel"): return "Meta / utility"
    if "oddpool" in n or "venue" in n or "whale" in n or "stock" in n: return "Cross-venue / wallet"
    if any(x in n for x in ("backtest", "dsr", "quant", "correlation", "calibration", "capacity", "gate-eta", "attribution")): return "Quant / stats gates"
    if n.startswith("pm-") or "arb" in n or "leg" in n: return "Edge pipeline (pm-*)"
    return "Other"


SRC = {
 "Ollama floor": ("LLM", "keyless", "localhost:11434 — open-weight floor", [r"(?i)ollama", r"11434"]),
 "NVIDIA NIM": ("LLM", "free-key", "integrate.api.nvidia.com — 70B", [r"(?i)nvidia"]),
 "Groq": ("LLM", "free-key", "api.groq.com — llama-3.3-70b", [r"(?i)\bgroq\b"]),
 "Cerebras": ("LLM", "free-key", "cerebras — gpt-oss-120b", [r"(?i)cerebras"]),
 "SambaNova": ("LLM", "free-key", "sambanova — llama-3.3-70b", [r"(?i)sambanova"]),
 "Polymarket CLOB": ("Venue", "keyless", "clob.polymarket.com — executable book", [r"\bCLOB\b"]),
 "Polymarket Gamma": ("Venue", "keyless", "gamma-api.polymarket.com", [r"(?i)gamma\s*(api|/|endpoint|market|slug|createdAt)"]),
 "Polymarket on-chain CTF": ("Chain", "keyless", "CTF payoutNumerators (Polygon)", [r"payoutNumerators", r"ctf_resolution", r"(?i)CTF contract", r"(?i)on-chain resolution"]),
 "drpc.org RPC": ("Chain", "keyless", "drpc.org — Polygon RPC", [r"(?i)drpc"]),
 "Kalshi": ("Venue", "keyless", "trading-api.kalshi.com", [r"(?i)\bKalshi\b"]),
 "Oddpool API": ("Venue", "keyed", "Kalshi↔Polymarket — 1k/mo quota", [r"(?i)oddpool"]),
 "hermes RSS": ("Geo/News", "keyless", "localhost:48580 — geopolitics RSS", [r"(?i)hermes", r"48580"]),
 "GDELT": ("Geo/News", "keyless", "gdeltproject.org", [r"GDELT"]),
 "IMF PortWatch": ("Geo/Data", "keyless", "portwatch.imf.org", [r"(?i)PortWatch"]),
 "Binance klines": ("Crypto", "keyless", "api.binance.com — OHLCV", [r"(?i)klines", r"(?i)\bBinance\b"]),
 "Deribit": ("Crypto vol", "keyless", "deribit.com — IV/skew", [r"(?i)Deribit"]),
 "Perp funding & basis": ("Crypto", "keyless", "exchange funding/basis", [r"(?i)funding rate", r"(?i)perp(etual)? funding"]),
 "BLS": ("Macro", "keyless", "bls.gov — CPI/jobs", [r"\bBLS\b"]),
 "Open-Meteo": ("Weather", "keyless", "open-meteo.com", [r"(?i)open[\s\-]?meteo"]),
 "ESPN": ("Sports", "keyless", "site.api.espn.com — scores", [r"(?i)\bESPN\b"]),
 "lmarena leaderboard": ("AI", "keyless", "lmarena.ai", [r"(?i)lmarena", r"(?i)AI leaderboard"]),
 "yfinance": ("Equities", "keyless", "Yahoo Finance", [r"(?i)yfinance"]),
 "Shopify (Good Wearr)": ("Commerce", "keyed", "Good Wearr Admin", [r"(?i)Shopify"]),
 "Pexels": ("Media", "keyed", "pexels.com — stock footage", [r"(?i)Pexels"]),
 "last30days": ("Web/Social", "keyless", "Reddit/X/YT/HN/Polymarket", [r"(?i)last30days"]),
 "Tavily": ("Web", "free-key", "tavily.com — search", [r"(?i)tavily"]),
 "data_sources.py registry": ("Registry", "keyless", "166 keyless sources", [r"data_sources"]),
}


def main():
    os.makedirs(OUTD, exist_ok=True); os.makedirs(DSD, exist_ok=True)
    agents = {}
    for p in sorted(glob.glob(AG + "/*.md")):
        s = open(p).read()
        if not s.startswith("---") or "\n---\n" not in s: continue
        fm = s.split("\n---\n", 1)[0]
        f = lambda k: (re.search(rf'^{k}:\s*(.*)$', fm, re.M) or [None, ""])[1].strip()
        name = f("name") or os.path.basename(p)[:-3]
        dm = re.search(r'^description:\s*(.*)$', fm, re.M); desc = ""
        if dm:
            first = dm.group(1).strip()
            if first in (">", "|", ">-", "|-", ""):
                for ln in fm[dm.end():].split("\n"):
                    if ln.startswith("  "): desc += " " + ln.strip()
                    elif ln.strip() == "": continue
                    else: break
            else: desc = first
        desc = re.split(r'(?<=[.!?]) ', desc.strip())[0][:160]
        agents[name] = {"text": s, "model": f("model") or "inherit", "mt": f("maxTurns") or "—",
                        "cap": "HEAVY" if "heavy scanning" in s else ("LITE" if "BUDGET DISCIPLINE" in s else "—"), "desc": desc}
    names = sorted(agents, key=len, reverse=True)
    edges = {a: set() for a in agents}
    for a in agents:
        for b in names:
            if b != a and re.search(r'(?<![\w-])' + re.escape(b) + r'(?![\w-])', agents[a]["text"]):
                edges[a].add(b)
    keep = set(agents) | {"Squad - " + cat(a).replace("/", "-").replace("*", "").strip() for a in agents}
    removed = 0
    for p in glob.glob(OUTD + "/*.md"):
        if os.path.basename(p)[:-3] not in keep: os.remove(p); removed += 1
    for a in sorted(agents):
        d = agents[a]; conns = sorted(edges[a])
        fm = (f"---\ntype: agent\nmodel: {d['model']}\nmaxTurns: {d['mt']}\ncap: {d['cap']}\n"
              f"connections: {len(conns)}\ntags: [agent, fleet]\n---\n")
        body = [f"# `{a}`\n", d["desc"] + "\n",
                f"\n**model** {d['model']} · **maxTurns** {d['mt']} · **cap** {d['cap']} · **{len(conns)} connections**\n"]
        if conns:
            body.append("\n## → connects to / hands off to\n"); body.append(" · ".join(f"[[{c}]]" for c in conns))
        body.append("\n\n*Part of [[Agent Fleet]] — the interconnected swarm.*\n")
        open(OUTD + f"/{a}.md", "w").write(fm + "\n".join(body))
    groups = defaultdict(list)
    for a in agents: groups[cat(a)].append(a)
    SAN = lambda s: s.replace("/", "-").replace("*", "").strip()
    for g, mem in groups.items():
        open(OUTD + f"/Squad - {SAN(g)}.md", "w").write("\n".join([
            f"---\ntype: agent-cluster\nmembers: {len(mem)}\ntags: [agent, swarm, cluster]\n---\n",
            f"# 🧩 {g}  ·  {len(mem)} agents\n", "A cluster of the [[Agent Fleet]] swarm.\n",
            "\n## members\n", " · ".join(f"[[{m}]]" for m in sorted(mem)), "\n\n*↑ [[Agent Fleet]]*\n"]))
    agent_src = {}; cons = {s: [] for s in SRC}
    for a in agents:
        hits = [s for s, (c, k, e, pats) in SRC.items() if any(re.search(pt, agents[a]["text"]) for pt in pats)]
        if hits: agent_src[a] = hits
        for s in hits: cons[s].append(a)
    kept = {s: c for s, c in cons.items() if c}
    for p in glob.glob(DSD + "/*.md"):
        if os.path.basename(p)[:-3] not in kept: os.remove(p)
    for s, c in kept.items():
        ca, key, ep, _ = SRC[s]
        open(DSD + f"/{s}.md", "w").write("\n".join([
            f"---\ntype: data-source\ncategory: {ca}\naccess: {key}\nconsumers: {len(c)}\ntags: [data-source, swarm, feed]\n---\n",
            f"# 📡 {s}\n", f"**{ca}** · `{key}` · {ep}\n", f"\nConsumed by {len(c)} agent(s) in the [[Agent Fleet]] swarm.\n",
            "\n## consumed by\n", " · ".join(f"[[Agents/{a}|{a}]]" for a in sorted(c)), "\n\n*↑ [[Data Sources]] · [[Agent Fleet]]*\n"]))
    bycat = defaultdict(list)
    for s in kept: bycat[SRC[s][0]].append(s)
    idx = ["---\ntype: data-source-hub\nsources: %d\ntags: [data-source, swarm]\n---\n" % len(kept),
           f"# 📡 Data Sources — {len(kept)} live feeds\n", "The substrate of the [[Agent Fleet]] swarm.\n"]
    for c in sorted(bycat): idx.append(f"\n## {c}\n" + " · ".join(f"[[Data Sources/{s}|{s}]] ({len(kept[s])})" for s in sorted(bycat[c])))
    idx.append("\n\n*↑ [[Agent Fleet]]*\n")
    open(V + "/Data Sources.md", "w").write("\n".join(idx))
    for a, srcs in agent_src.items():
        p = OUTD + f"/{a}.md"; s = open(p).read()
        open(p, "w").write(s.rstrip() + "\n\n## 📡 live data sources\n" + " · ".join(f"[[Data Sources/{x}|{x}]]" for x in sorted(srcs)) + "\n")
    collide = set()
    for p in glob.glob(V + "/**/*.md", recursive=True):
        if "/Agents/" in p or "/Data Sources" in p: continue
        b = os.path.basename(p)[:-3]
        if b in agents: collide.add(b)
    fixed = 0
    for p in glob.glob(OUTD + "/*.md"):
        s = open(p).read(); o = s
        for nm in collide: s = s.replace(f"[[{nm}]]", f"[[Agents/{nm}|{nm}]]")
        if s != o: open(p, "w").write(s); fixed += 1
    rows = [(cat(a), a, agents[a]["model"], agents[a]["mt"], agents[a]["cap"], agents[a]["desc"]) for a in agents]
    gg = defaultdict(list)
    for r in rows: gg[r[0]].append(r)
    out = ["---\ntype: reference\nstatus: active\ntags: [agents, fleet, infra]\nagent_count: %d\n---\n" % len(rows),
           f"# Agent Fleet — {len(rows)} agents\n",
           f"Live catalog of `~/.claude/agents/` ({len(rows)} agents). Every agent carries a BUDGET DISCIPLINE cap + a hard maxTurns ceiling. Source of truth; Graph/ mirrors are stale.\n"]
    for g in sorted(gg, key=lambda k: -len(gg[k])):
        out.append(f"\n## {g}  ({len(gg[g])})\n")
        out.append("| Agent | Model | maxTurns | Cap | Purpose |\n|---|---|---|---|---|")
        for _, n, m, mt, cp, ds in sorted(gg[g]): out.append(f"| `{n}` | {m} | {mt} | {cp} | {ds} |")
    open(V + "/Agent Fleet.md", "w").write("\n".join(out) + "\n")
    print(f"synced {len(agents)} agents · {len(groups)} squads · removed {removed} stale · {len(kept)} feeds · {fixed} collision-fixed")
    print("squads:", ", ".join(f"{g}({len(m)})" for g, m in sorted(groups.items(), key=lambda kv: -len(kv[1]))))


if __name__ == "__main__":
    main()
