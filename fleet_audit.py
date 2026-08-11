#!/usr/bin/env python3
"""
fleet_audit.py — governance drift check (read-only).

Diffs the agents actually on disk (~/.claude/agents/*.md) and the launchd com.aryan.*
workers against the documented registry (AGENT_FLEET.md). Surfaces UNTRACKED additions
and MISSING entries so the registry stays honest — untracked edits are how a fleet rots
(this session a co-developer process kept adding agents: basket-peek, pm-lock-auditor, …).
Run: python3 fleet_audit.py
"""
import os, re, glob, subprocess

AGENTS_DIR = os.path.expanduser("~/.claude/agents")
REGISTRY = os.path.expanduser("~/Documents/polymarket/AGENT_FLEET.md")

# the agent families this fleet's registry is responsible for tracking
SESSION_PREFIXES = ("edge-", "price-", "market-", "news-", "chain-", "research-", "venue-",
                    "vol-", "funding-", "events-", "whale-", "oddpool-", "basket-", "peek-")


def disk_agents():
    out = {}
    for f in glob.glob(os.path.join(AGENTS_DIR, "*.md")):
        t = open(f).read()
        m = re.search(r'^name:\s*(\S+)', t, re.M)
        out[m.group(1) if m else os.path.basename(f)[:-3]] = os.path.getmtime(f)
    return out


def registry_names():
    if not os.path.exists(REGISTRY):
        return set()
    return set(re.findall(r'`([a-z][a-z0-9-]+)`', open(REGISTRY).read()))


def workers_loaded():
    try:
        out = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=15).stdout
        return set(re.findall(r'com\.aryan\.([a-z0-9-]+)', out))
    except Exception:
        return set()


def main():
    disk = disk_agents()
    reg = registry_names()
    tracked_workers = set(re.findall(r'com\.aryan\.([a-z0-9-]+)', open(REGISTRY).read())) if os.path.exists(REGISTRY) else set()

    # in-scope agents on disk that the registry does NOT mention = untracked drift
    untracked = sorted(a for a in disk if a not in reg and a.startswith(SESSION_PREFIXES))
    # session-worker jobs running but not in the registry's worker list
    worker_jobs = {w for w in workers_loaded() if w in ("night-brief", "catalyst-watch",
                   "survivor-watch", "edge-scan")}
    untracked_workers = sorted(worker_jobs - tracked_workers)

    print("=== fleet drift audit ===")
    print(f"agents on disk: {len(disk)} | names in registry: {len(reg)}")
    if untracked:
        print(f"\n⚠️ UNTRACKED agents (on disk, not in AGENT_FLEET.md) — {len(untracked)}:")
        for a in untracked:
            print(f"   + {a}")
        print("   → add them to the registry or remove them.")
    if untracked_workers:
        print(f"\n⚠️ UNTRACKED workers (loaded, not in registry) — {len(untracked_workers)}:")
        for w in untracked_workers:
            print(f"   + com.aryan.{w}")
    if not untracked and not untracked_workers:
        print("\n✓ no drift — every in-scope agent and worker is documented in AGENT_FLEET.md.")


if __name__ == "__main__":
    main()
