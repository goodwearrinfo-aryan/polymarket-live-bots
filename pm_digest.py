#!/usr/bin/env python3
"""pm_digest.py — daily synthesis + alerting over the 15 pm-agent vault reports.

The 15 agents each write Reports/agent_<name>.md. This (a) DETERMINISTICALLY scans
them for critical markers (marking bug, graduation, dead leg, strong anomaly, regime
break) and fires an iMessage if any fire — push, not pull; (b) asks the LLM for a
≤12-line "what matters today" synthesis → Reports/agent_digest.md. PAPER, read-only.

Usage: python3 pm_digest.py
"""
import os, sys, glob, subprocess, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pm_agent_llm as L
VAULT = os.path.expanduser("~/Documents/PolymarketVault/Reports")
RECIPIENTS = ["krisharyan@icloud.com", "+918449447444"]

# Affirmative critical signals. Matched PER LINE with a negation guard so
# "No MARKING BUGs detected" / "controls all negative ✅" do NOT false-fire.
CRITICAL = [
    ("control is positive", "🚨 a control went positive (marking bug)"),
    ("marking-bug-halt", "🚨 marking-bug halt"),
    ("graduated", "🏆 a leg GRADUATED"),
    ("verdict: graduate", "🏆 a leg GRADUATED"),
    ("stale_nodata", "⚠️ stale_nodata settlement bug"),
    ("regime shifted", "⚠️ regime SHIFTED under a leg"),
    ("🚨", "🚨 an agent raised a critical flag"),
]
# if any of these appear on the SAME line, it's a negation/all-clear → not critical
NEGATION = ("no ", "not ", "none", "negative", "✅", "honest", "clean", "accumulating",
            "all controls", "no marking", "false", "n/a", "do not", "must lose", "if you")


def imsg(text):
    for t in RECIPIENTS:
        try:
            subprocess.run(["osascript", "-e",
                f'tell application "Messages" to send "{text}" to buddy "{t}" of (1st service whose service type = iMessage)'],
                capture_output=True, timeout=20)
        except Exception:
            pass


def main():
    files = sorted(glob.glob(os.path.join(VAULT, "agent_*.md")))
    blobs, crit = [], []
    for f in files:
        name = os.path.basename(f)
        try:
            txt = open(f).read()
        except Exception:
            continue
        if name == "agent_digest.md":
            continue
        blobs.append(f"### {name}\n{txt.strip()[:900]}")
        for line in txt.splitlines():
            ll = line.lower().strip()
            if not ll or any(neg in ll for neg in NEGATION):
                continue                                  # negation/all-clear line → skip
            for marker, msg in CRITICAL:
                if marker in ll:
                    crit.append((name, msg))
    # dedupe critical hits
    seen = set(); crit = [(n, m) for n, m in crit if (m not in seen and not seen.add(m))]

    # LLM synthesis (best-effort; falls back to a plain concat if no LLM)
    joined = "\n\n".join(blobs)
    out, err = L.call_llm(
        "You are a terse desk-editor for a Polymarket PAPER research bot. Given the latest reports from 15 watcher agents, write a ≤12-line 'what matters today' digest: lead with anything critical (marking bug, graduation, dead/regime/strong-anomaly), then 2-4 noteworthy items, then 'all quiet' if nothing. NEVER reprint a report; synthesize. Bullets only.",
        f"The 15 agent reports follow. Synthesize today's digest.\n\n{joined}", max_tokens=600)
    stamp = time.strftime("%Y-%m-%d %H:%M")
    crit_block = ("\n".join(f"- {m} ({n})" for n, m in crit) or "- none")
    body = (f"# pm-digest — {stamp}\n\n## 🚨 critical flags\n{crit_block}\n\n## synthesis\n"
            + (out if not err else f"_(LLM digest skipped: {err})_\n\n" + "\n".join(f"- {os.path.basename(f)}" for f in files)))
    try:
        os.makedirs(VAULT, exist_ok=True)
        open(os.path.join(VAULT, "agent_digest.md"), "w").write(body)
    except Exception:
        pass

    # PUSH only on a real critical flag (throttle: skip if same flags as last run)
    sentinel = os.path.join(HERE, ".pm_digest_lastcrit")
    last = open(sentinel).read().strip() if os.path.exists(sentinel) else ""
    now_sig = "|".join(sorted(m for _, m in crit))
    if crit and now_sig != last:
        imsg("pm-digest: " + "; ".join(m for _, m in crit)[:300])
        open(sentinel, "w").write(now_sig)
    elif not crit:
        open(sentinel, "w").write("")
    print(f"pm-digest: {len(files)} reports, {len(crit)} critical flag(s){' → iMessaged' if (crit and now_sig!=last) else ''}")


if __name__ == "__main__":
    main()
