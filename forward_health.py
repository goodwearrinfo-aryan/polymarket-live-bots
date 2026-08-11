#!/usr/bin/env python3
"""
forward_health.py — autonomous "is the wait paying off?" sentinel.

The project is in accumulate-forward-data mode: the only un-faked edge test left is
forward resolved data (all past datasets audited NULL 2026-06-20). That only works if
the forward loggers are ACTUALLY appending. "Silence ≠ success" — a logger can sit at
exit 0 while silently dead. This checks the GATE-RELEVANT forward datasets for freshness
against each one's real cadence, and alarms (iMessage + Obsidian) only when one goes stale.

Read-only. Never truncates/edits a logger.
"""
import json, os, subprocess, sys, time
from datetime import datetime, timezone

PM   = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.expanduser("~")
VAULT = os.path.join(HOME, "Documents/PolymarketVault/Reports/Forward Health.md")

# (path, friendly, max_age_min before STALE, expected_absent)
DATASETS = [
    (f"{PM}/basketlock_state.json",                 "basketlock (arb)",   120, False),
    (f"{PM}/dataarb_state.json",                    "dataarb (arb)",      120, False),
    (f"{PM}/monoarb_state.json",                    "monoarb (arb)",      120, False),
    (f"{PM}/candle_knowledge_log.jsonl",            "candle KB",          120, False),
    (f"{PM}/newsmove_log.jsonl",                    "newsmove KB",        360, False),
    (f"{HOME}/Documents/futures-bot/paper_state.json","futures tape",      90, False),
    (f"{HOME}/Documents/TradingAgents/gapfill_forward_log.jsonl","India gap-fill", 1800, False),  # daily+wknd
    (f"{HOME}/Documents/TradingAgents/dhan_feed_log.jsonl","Dhan feed",   1e9, True),   # scaffold: absent OK
]

def imessage(body):
    for tgt in ("krisharyan@icloud.com",):
        s = (f'tell application "Messages"\n set s to 1st service whose service type = iMessage\n'
             f' send "{body}" to buddy "{tgt}" of s\nend tell')
        try: subprocess.run(["osascript","-e",s], capture_output=True, timeout=10)
        except Exception: pass

def main():
    now = time.time()
    rows, stale = [], []
    for path, name, maxage, absent_ok in DATASETS:
        if not os.path.exists(path):
            state = "·idle (scaffold)" if absent_ok else "🔴 MISSING"
            if not absent_ok: stale.append(f"{name}: missing")
            rows.append((name, state, "—"))
            continue
        age = (now - os.path.getmtime(path)) / 60
        try:
            n = sum(1 for _ in open(path)) if path.endswith(".jsonl") else "json"
        except Exception:
            n = "?"
        if age > maxage:
            state = f"🔴 STALE {age/60:.1f}h"
            stale.append(f"{name}: {age/60:.1f}h (>{maxage/60:.0f}h)")
        else:
            state = f"🟢 {age:.0f}m"
        rows.append((name, state, n))
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    body = "\n".join(f"  {nm:18} {st:16} rows={n}" for nm, st, n in rows)
    note = (f"---\ntype: dashboard\nstatus: live\n---\n\n# 🩺 Forward-Logger Health\n\n"
            f"*Are the datasets we're waiting on actually growing? Auto {ts} by forward_health.py. "
            f"All past data is NULL — forward accumulation is the only live edge test.*\n\n"
            f"```\n{body}\n```\n\n"
            + ("✅ all gate loggers fresh\n" if not stale else
               "🔴 STALE — forward accumulation interrupted:\n" + "\n".join(f"- {s}" for s in stale) + "\n"))
    try:
        os.makedirs(os.path.dirname(VAULT), exist_ok=True); open(VAULT,"w").write(note)
    except Exception as e:
        print(f"vault write failed: {e}", file=sys.stderr)
    if stale:
        imessage("🩺 Forward-logger STALE: " + "; ".join(stale)[:240])
        print("STALE:", stale)
    else:
        print(f"all {len(rows)} gate loggers fresh")
    sys.exit(0)

if __name__ == "__main__":
    main()
