#!/usr/bin/env python3
"""launchd_health.py — DETERMINISTIC launchd-job health sentinel (no LLM).

Why this exists: the copybot watchdog silently went `exit 1` (2026-06-17, a vanished
copy-bot dir) and NOTHING caught it — it only surfaced on a manual health check. This
closes that gap: each cycle it reads `launchctl list`, flags every `com.aryan.*` job
whose last exit is a HARD FAULT, dedupes against a baseline, and iMessages on a NEW
fault. Resolved faults drop out (so a recurrence re-alerts).

HARD FAULT = a positive exit code (a real error, like the copybot exit-1) or 126/127
(TCC "Operation not permitted" / missing binary — the launchd-can't-reach-~/Documents
class). NEGATIVE codes are signal kills (KeepAlive restarts, manual stops) — logged,
not alerted, because they're usually benign.

Why DETERMINISTIC, not an LLM agent: "is the exit code non-zero" is a grep, not a
judgment — an LLM here would just burn tokens. The LLM layer (the `pm-watchdog-warden`
agent) is for DEEP diagnosis AFTER this flags something. Detection = cheap + always-on;
judgment = on-demand + expensive. Same split as dsr_gate (deterministic) vs the analyst
agents (LLM).

Known-benign jobs go one-per-line in `.launchd_health_allow` (e.g. a decoy plist that
is expected to fail). PAPER/ops infra, read-only. Stdlib only.
Run: python3 launchd_health.py
"""
import subprocess, os, json, time

HERE = os.path.dirname(os.path.abspath(__file__))
SEEN = os.path.join(HERE, ".launchd_health_seen.json")
ALLOW = os.path.join(HERE, ".launchd_health_allow")
LOG = os.path.join(HERE, "launchd_health.log")
RECIPIENTS = ["krisharyan@icloud.com", "+918449447444"]


def _allow():
    try:
        return {l.strip() for l in open(ALLOW) if l.strip() and not l.startswith("#")}
    except Exception:
        return set()


def scan():
    """Return [{label, code, pid}] for com.aryan.* jobs with a HARD FAULT exit code."""
    out = subprocess.run(["launchctl", "list"], capture_output=True, text=True).stdout
    allow, faults = _allow(), []
    for line in out.splitlines():
        p = line.split("\t")
        if len(p) < 3:
            continue
        pid, code, label = p[0], p[1], p[2]
        if not label.startswith("com.aryan.") or label in allow:
            continue
        try:
            c = int(code)
        except ValueError:
            continue
        if c > 0 or c in (126, 127):          # positive error, or TCC/missing-binary
            faults.append({"label": label, "code": c, "pid": pid})
    return faults


def _imsg(msg):
    for t in RECIPIENTS:
        subprocess.run(["osascript", "-e",
            f'tell application "Messages" to send "{msg}" to buddy "{t}" '
            f'of (1st service whose service type = iMessage)'],
            capture_output=True)


def main():
    faults = scan()
    cur = {f["label"]: f["code"] for f in faults}
    now = time.strftime("%Y-%m-%d %H:%M")
    first_run = not os.path.exists(SEEN)
    try:
        seen = json.load(open(SEEN))
    except Exception:
        seen = {}
    new = [f for f in faults if seen.get(f["label"]) != f["code"]]

    open(LOG, "a").write(f"[{now}] {len(faults)} fault(s): "
                         + (", ".join(f'{f["label"]}={f["code"]}' for f in faults) or "none")
                         + (" (baseline)" if first_run else "") + "\n")

    if new and not first_run:               # first run = establish baseline silently
        msg = "🚨 launchd fault: " + "; ".join(
            f'{f["label"].replace("com.aryan.", "")}={f["code"]}' for f in new)
        _imsg(msg[:200])
        open(LOG, "a").write(f"[{now}] ALERTED: {msg}\n")

    json.dump(cur, open(SEEN, "w"))         # drop resolved faults so recurrence re-alerts
    tag = "baseline established" if first_run else (f"{len(new)} NEW → iMessaged" if new else "no new")
    print(f"{len(faults)} hard-fault job(s) · {tag}")
    for f in faults:
        kind = "TCC/missing" if f["code"] in (126, 127) else "error"
        print(f"  ⚠️ {f['label']} exit {f['code']} ({kind})")
    if not faults:
        print("  ✅ all com.aryan.* jobs healthy")


if __name__ == "__main__":
    main()
