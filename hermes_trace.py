#!/usr/bin/env python3
"""hermes_trace.py — READ-ONLY tracer for the ~/.hermes autonomous agents.

Traces the running hermes agents (liveness, models-in-use, health) and mirrors the trace
into BOTH the Obsidian vault (Reports/Hermes Agents.md) and a durable JSONL. This is the
"connect the hermes fleet to Claude + Obsidian" wiring:
  • Obsidian  — the vault note (picked up by the Agent Fleet / graph; links [[Agent Fleet]]).
  • Claude    — runs as com.aryan.hermes-trace, so it shows up in the launchd Agent Fleet
                dashboard alongside the rest of the bot's workers, and the JSONL is brain-readable.

HARD GUARANTEES (paper-safe, like every logger here): places NO trades, touches NO hermes
process, and mirrors NO secrets — it NEVER reads ~/.hermes/.env or auth.json, and it extracts
ONLY the model field from request dumps, never the prompt body content. Atomic writes, fail-soft.
Usage: python3 hermes_trace.py
"""
import os
import re
import json
import glob
import subprocess
import datetime

HOME = os.path.expanduser("~")
HERMES = os.path.join(HOME, ".hermes")
SESSIONS = os.path.join(HERMES, "sessions")
AGENTLOG = os.path.join(HERMES, "logs", "agent.log")
VAULT = os.path.join(HOME, "Documents/PolymarketVault/Reports/Hermes Agents.md")
TRACE = os.path.join(HOME, "Documents/polymarket/hermes_trace.jsonl")
_SECRET = re.compile(r"(api[_-]?key|secret|token|password|bearer|authorization|sk-|nvapi)", re.I)


def _utc():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def detect_agents():
    """Running hermes agent processes — pid + uptime. ps only, read-only, never touches them."""
    try:
        out = subprocess.run(["ps", "-axo", "pid,etime,command"],
                             capture_output=True, text=True, timeout=15).stdout
    except Exception:
        return []
    agents = []
    for ln in out.splitlines():
        if ".hermes/hermes-agent" in ln and "/bin/hermes" in ln and "grep" not in ln and "hermes_trace" not in ln:
            parts = ln.split(None, 2)
            if len(parts) >= 2 and parts[0].isdigit():
                agents.append({"pid": parts[0], "uptime": parts[1]})
    return agents


def _newest_dumps(limit=8):
    try:
        return sorted(glob.glob(os.path.join(SESSIONS, "request_dump_*.json")),
                      key=os.path.getmtime, reverse=True)[:limit]
    except Exception:
        return []


def recent_models(limit=8):
    """Models in use, from the newest session request-dumps (best-effort, fail-soft).
    Reads ONLY the `model` field of the request body — never the prompt content."""
    models = {}
    for f in _newest_dumps(limit):
        try:
            d = json.load(open(f))
            body = (d.get("request") or {}).get("body")
            b = json.loads(body) if isinstance(body, str) else (body if isinstance(body, dict) else {})
            m = b.get("model") or b.get("model_name")
            if m and not _SECRET.search(str(m)):
                models[m] = models.get(m, 0) + 1
        except Exception:
            continue
    return models


def recent_health(limit=8):
    """Recent request outcomes (dump `reason`) + agent.log ERROR lines (secrets scrubbed)."""
    reasons = {}
    for f in _newest_dumps(limit):
        try:
            r = json.load(open(f)).get("reason")
            if r:
                reasons[r] = reasons.get(r, 0) + 1
        except Exception:
            continue
    errs = []
    try:
        tail = subprocess.run(["tail", "-40", AGENTLOG], capture_output=True, text=True, timeout=10).stdout
        for ln in tail.splitlines():
            if " ERROR " in ln:
                msg = ln.split(" ERROR ", 1)[-1].strip()[:90]
                if not _SECRET.search(msg):
                    errs.append(msg)
    except Exception:
        pass
    return reasons, errs[-3:]


def write_vault(rec):
    os.makedirs(os.path.dirname(VAULT), exist_ok=True)
    a, models, reasons, errs = rec["agents"], rec["models"], rec["reasons"], rec["errors"]
    L = ["---", "type: dashboard", "status: live", f"updated: {rec['ts'][:10]}",
         "tags: [agent, hermes]", "---", "",
         "# 🜂 Hermes Agents (traced)", "",
         f"*Read-only trace of the `~/.hermes` autonomous agents — auto {rec['ts']}. "
         f"Open-weight models only; no secrets mirrored. Traced by `hermes_trace.py` "
         f"(com.aryan.hermes-trace), so this fleet shows up next to the bot's own.*", "",
         f"**Running:** {len(a)} agent(s)", "", "| pid | uptime |", "|--|--|"]
    for x in a:
        L.append(f"| {x['pid']} | {x['uptime']} |")
    L += ["", "**Models in use:** "
          + (", ".join(f"`{m}` ×{n}" for m, n in models.items()) or "_(none parsed this cycle)_"), ""]
    if reasons:
        L += ["**Recent request outcomes:** " + ", ".join(f"{k} ×{v}" for k, v in reasons.items()), ""]
    if errs:
        L += ["**Recent errors (gateway/agent log):**"] + [f"- `{e}`" for e in errs] + [""]
    L += ["", "→ part of the [[Agent Fleet]] · traced into [[Status Hub]]", ""]
    tmp = VAULT + ".tmp"
    open(tmp, "w").write("\n".join(L))
    os.replace(tmp, VAULT)   # atomic — crash/race-safe


def main():
    a = detect_agents()
    models = recent_models()
    reasons, errs = recent_health()
    rec = {"ts": _utc(), "n_agents": len(a), "agents": a,
           "models": models, "reasons": reasons, "errors": errs}
    write_vault(rec)
    try:
        with open(TRACE, "a") as f:
            f.write(json.dumps({"ts": rec["ts"], "n_agents": rec["n_agents"],
                                "pids": [x["pid"] for x in a],
                                "models": list(models.keys()),
                                "reasons": list(reasons.keys())}) + "\n")
    except Exception as e:
        print(f"hermes-trace: trace-jsonl write failed (non-fatal): {e}")
    print(f"hermes-trace: {len(a)} agent(s) | models={list(models.keys())} "
          f"| reasons={list(reasons.keys())} -> vault + jsonl")


if __name__ == "__main__":
    main()
