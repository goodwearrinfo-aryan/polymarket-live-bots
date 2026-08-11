#!/usr/bin/env python3
"""pm_grade.py — self-grading review of the pm-agents' track record. (PAPER, read-only.)

Reads pm_agent_ledger.jsonl (every agent run's verdict, appended by run_agent) and
produces a reliability scorecard: which agents fire consistently, which FLIP-FLOP
(same input, opposite verdicts = unreliable), which never produce a real finding
(maybe noise), and the error rate (ok=false runs). This is a meta-grade of the
AGENTS THEMSELVES — not market outcomes (those resolve slowly) — so you learn which
verdicts to trust. Writes Reports/agent_scorecard.md.

Usage: python3 pm_grade.py
"""
import os, sys, json, time, collections
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pm_agent_llm as L
LEDGER = os.path.join(HERE, "pm_agent_ledger.jsonl")
VAULT = os.path.expanduser("~/Documents/PolymarketVault/Reports")


def main():
    if not os.path.exists(LEDGER):
        print("pm_grade: no ledger yet"); return
    rows = []
    for ln in open(LEDGER).read().splitlines()[-200:]:
        try:
            rows.append(json.loads(ln))
        except Exception:
            pass
    if not rows:
        print("pm_grade: empty ledger"); return
    # deterministic stats per agent
    by = collections.defaultdict(list)
    for r in rows:
        by[r.get("agent", "?")].append(r)
    stats = []
    for ag, rs in sorted(by.items()):
        n = len(rs); errs = sum(1 for r in rs if not r.get("ok"))
        last = rs[-1].get("summary", "")[:120]
        stats.append(f"  {ag:18s} runs={n:<3} errors={errs:<3} last: {last}")
    det = "PER-AGENT RUN STATS (deterministic, from the ledger):\n" + "\n".join(stats)

    # LLM meta-review of reliability (best-effort)
    recent = "\n".join(f"{r.get('ts','')} {r.get('agent','')}: {r.get('summary','')[:150]}" for r in rows[-60:])
    out, err = L.call_llm(
        "You grade the RELIABILITY of watcher agents from their run history (not market outcomes). Flag: agents that FLIP-FLOP (contradict their own recent verdicts without new data = unreliable), agents that NEVER produce a concrete finding (possible noise), high error rates. ≤10 lines, bullets, name the agent. If all look consistent, say so. NEVER reprint the log.",
        f"Agent run history (recent):\n{recent}", max_tokens=500)
    stamp = time.strftime("%Y-%m-%d %H:%M")
    body = f"# pm-agent scorecard — {stamp}\n\n{det}\n\n## reliability review\n" + (out if not err else f"_(LLM review skipped: {err})_")
    try:
        os.makedirs(VAULT, exist_ok=True)
        open(os.path.join(VAULT, "agent_scorecard.md"), "w").write(body)
    except Exception:
        pass
    print(f"pm_grade: {len(rows)} ledger rows, {len(by)} agents → agent_scorecard.md")


if __name__ == "__main__":
    main()
