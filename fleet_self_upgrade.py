#!/usr/bin/env python3
"""fleet_self_upgrade.py — the agent fleet maintains + improves ITSELF, safely tiered.

The honest answer to "let them upgrade themselves": full autonomy WHERE a change is
verifiable/reversible; a human gate WHERE it isn't. A miscalibrated free model rewriting
the fleet unverified is the exact silent-degradation failure this project guards against,
so that one path is the ONLY thing kept behind a gate.

  AUTO (deterministic, reversible): enforce fleet STANDARDS on every agent — a BUDGET
    DISCIPLINE cap + a hard maxTurns ceiling — so any agent another process adds inherits
    the standard automatically. Then re-sync the Obsidian swarm to the live fleet. Every
    change is git-trackable / reversible.
  AUTO + ROLLBACK: verified code fixes are the bug-heal fleet's job (find->fix->verify->
    gate->land) — composed, not duplicated here.
  PROPOSE (free-LLM, GATED): the free chain reviews a sample of agents and writes concrete
    upgrade suggestions to a vault REVIEW QUEUE — never applied. Nothing the free model
    writes touches the fleet without a human saying "land it".

Run: python3 fleet_self_upgrade.py   (launchd, daily). $0 metered. Fail-soft throughout.
"""
import os, glob, re, sys, subprocess
from datetime import datetime

AG = os.path.expanduser("~/.claude/agents")
POLY = os.path.expanduser("~/Documents/polymarket")
VAULT = os.path.expanduser("~/Documents/PolymarketVault")
QUEUE = os.path.join(VAULT, "Fleet Upgrades Queue.md")
sys.path.insert(0, POLY)

HEAVY = ('> ⛔ **BUDGET DISCIPLINE — be decisive, do NOT stall to null.** You do heavy scanning '
  '(web + market probes). Do at most ~8 quick probes, then STOP and give your verdict. You will be '
  'CUT OFF mid-scan and return NOTHING if you over-explore. A fast honest "nothing found" / NULL beats '
  'a timeout that loses all your work. If a keyless probe 429s or errors, MOVE ON — never retry it. '
  'Reach a conclusion and report it; do not exhaustively walk the book.')
LITE = ('> ⛔ **BUDGET DISCIPLINE.** Be decisive — do your job within your turn budget and RETURN '
  'your result. Never stall to null, loop, or run unbounded; a fast honest answer (including "nothing" / '
  'NULL) beats a timeout that loses all your work.')


def enforce_standards():
    """AUTO tier: every agent gets a cap + a hard maxTurns. Idempotent, additive, reversible."""
    added_cap = added_mt = 0
    for p in sorted(glob.glob(AG + "/*.md")):
        try:
            s = open(p, encoding="utf-8").read()
        except Exception:
            continue
        if not s.startswith("---") or "\n---\n" not in s:
            continue
        fm, body = s.split("\n---\n", 1)
        is_web = bool(re.search(r'^tools:.*Web', fm, re.M))
        is_peek = ("peek" in os.path.basename(p)) or bool(re.search(r'^model:\s*haiku', fm, re.M))
        changed = False
        if "maxTurns:" not in fm:
            mt = 10 if is_peek else (24 if is_web else 16)
            fm = fm + f"\nmaxTurns: {mt}"; changed = True; added_mt += 1
        if "BUDGET DISCIPLINE" not in body:
            body = "\n" + (HEAVY if is_web else LITE) + "\n\n" + body.lstrip("\n")
            changed = True; added_cap += 1
        if changed:
            try:
                open(p, "w", encoding="utf-8").write(fm + "\n---\n" + body)
            except Exception:
                pass
    return added_cap, added_mt


def sync_swarm():
    """AUTO tier: mirror the live fleet into the Obsidian swarm."""
    try:
        r = subprocess.run([sys.executable, os.path.join(POLY, "sync_agent_swarm.py")],
                           capture_output=True, text=True, timeout=180)
        line = (r.stdout.strip().splitlines() or ["synced"])[0]
        return line
    except Exception as e:
        return f"sync skipped: {str(e)[:50]}"


def propose():
    """PROPOSE tier: free-LLM upgrade suggestions -> vault review queue (GATED, never applied)."""
    try:
        import agent_secrets; agent_secrets.load()
        import llm_client
    except Exception:
        return 0, None
    files = sorted(glob.glob(AG + "/*.md"))
    if not files:
        return 0, None
    sample = files[:: max(1, len(files) // 6)][:6]
    summaries = []
    for p in sample:
        s = open(p, encoding="utf-8").read()
        name = (re.search(r'^name:\s*(\S+)', s, re.M) or [None, os.path.basename(p)[:-3]])[1]
        dm = re.search(r'description:\s*>?\s*(.*)', s)
        desc = re.sub(r'\s+', ' ', (dm.group(1) if dm else ""))[:180]
        summaries.append(f"- {name}: {desc}")
    prompt = ("You audit AI sub-agent definitions for a PAPER trading bot (no real money). For EACH agent, "
              "suggest AT MOST ONE concrete, safe upgrade (a sharper instruction, a missing guard, a stale "
              "reference) or say 'none'. Terse. Never suggest anything that places real orders / touches keys. "
              "Format: <agent>: <suggestion or none>.\n\n" + "\n".join(summaries))
    try:
        out = llm_client.chat([{"role": "user", "content": prompt}], max_tokens=600, temperature=0.3)
        served = llm_client.provenance().get("served_by")
    except Exception:
        return 0, None
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    hdr = ("---\ntype: review-queue\ntags: [fleet, self-upgrade, gated]\n---\n\n# \U0001f527 Fleet Upgrades Queue\n\n"
           "Free-LLM upgrade proposals for the agent fleet. **Gated — nothing here is applied automatically.** "
           "Review, then tell me which to land (the bug-heal fleet verifies + commits the ones you pick).\n")
    entry = f"\n## {now} — proposals (model: {served}, GATED)\n\n{out.strip()}\n"
    try:
        prev = open(QUEUE, encoding="utf-8").read() if os.path.exists(QUEUE) else hdr
        open(QUEUE, "w", encoding="utf-8").write(prev + entry)
    except Exception:
        return 0, served
    return 1, served


def main():
    cap, mt = enforce_standards()
    syncline = sync_swarm()
    nprop, served = propose()
    print(f"fleet_self_upgrade: AUTO (+{cap} caps, +{mt} maxTurns) | {syncline} | "
          f"PROPOSE {nprop} batch via {served or 'n/a'} -> Fleet Upgrades Queue.md (gated)")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"fleet_self_upgrade: error {str(e)[:80]}")
