#!/usr/bin/env python3
"""fleet_exit_warden.py — catches SILENTLY-failing launchd bots.

The scar: com.aryan.insider-watch sat dead for a long time (exit 1, ~4.8k tracebacks,
a 2.2MB error log) and NOTHING alarmed — a manual launchctl sweep found it. This sweeps
the com.aryan.* fleet for (a) a non-zero last exit and (b) a bloated error log (a job
crash-looping). It iMessages + writes an Obsidian report ONLY when a NEW/changed failure
appears (deduped via state — no per-cycle spam), and silently drops jobs that recovered.
'silence != success' applied to the fleet itself. Read-only: never restarts/kills a job.

Usage: python3 fleet_exit_warden.py [--dry-run]   (--dry-run: report only, no iMessage)
"""
import json, os, subprocess, sys, plistlib
from datetime import datetime, timezone

HOME = os.path.expanduser("~")
PM = os.path.dirname(os.path.abspath(__file__))
LA = os.path.join(HOME, "Library/LaunchAgents")
STATE = os.path.join(PM, ".fleet_exit_warden_state.json")
VAULT = os.path.join(HOME, "Documents/PolymarketVault/Reports/Fleet Exit Warden.md")
BENIGN = {"0", "-", "-15"}   # 0=clean, -=running/never-exited, -15=SIGTERM (usually a benign cap/kickstart)
BLOAT_MB = 1.0               # an error log this big = a job crash-looping


def imessage(body):
    for tgt in ("krisharyan@icloud.com",):
        s = ('tell application "Messages"\n set s to 1st service whose service type = iMessage\n'
             f' send "{body}" to buddy "{tgt}" of s\nend tell')
        try:
            subprocess.run(["osascript", "-e", s], capture_output=True, timeout=10)
        except Exception:
            pass


def fleet_exits():
    out = {}
    try:
        txt = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=15).stdout
    except Exception:
        return out
    for ln in txt.splitlines():
        p = ln.split("\t")
        if len(p) >= 3 and p[2].startswith("com.aryan."):
            out[p[2]] = p[1]
    return out


def err_log_size(label):
    try:
        d = plistlib.load(open(os.path.join(LA, label + ".plist"), "rb"))
    except Exception:
        return 0
    p = d.get("StandardErrorPath") or d.get("StandardOutPath")
    return os.path.getsize(p) if (p and os.path.exists(p)) else 0


def main():
    dry = "--dry-run" in sys.argv
    exits = fleet_exits()
    failing = {}
    for label, code in exits.items():
        reasons = []
        if code not in BENIGN:
            reasons.append(f"exit {code}")
        sz = err_log_size(label)
        if sz > BLOAT_MB * 1e6:
            reasons.append(f"err-log {sz/1e6:.1f}MB (crash-loop)")
        if reasons:
            failing[label] = "; ".join(reasons)

    prev = {}
    try:
        prev = json.load(open(STATE))
    except Exception:
        pass
    new = {l: r for l, r in failing.items() if prev.get(l) != r}   # new or changed
    recovered = [l for l in prev if l not in failing]

    if new and not dry:
        lines = "\n".join(f"  {l.replace('com.aryan.', '')}: {r}" for l, r in sorted(new.items()))
        imessage(f"⚠️ fleet-warden: {len(new)} failing job(s)\n{lines}")

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    L = ["# Fleet Exit Warden", "",
         f"> Sweeps com.aryan.* for non-zero exit / bloated crash-log. Updated {ts}.",
         f"> {len(exits)} jobs · **{len(failing)} failing** · {len(new)} new this cycle.", ""]
    if failing:
        L += ["| job | problem |", "|---|---|"] + [f"| {l.replace('com.aryan.', '')} | {r} |" for l, r in sorted(failing.items())]
    else:
        L += ["✅ all com.aryan jobs healthy — zero non-zero exits, no bloated logs."]
    if recovered:
        L += ["", "_recovered since last sweep: " + ", ".join(x.replace('com.aryan.', '') for x in recovered) + "_"]
    try:
        os.makedirs(os.path.dirname(VAULT), exist_ok=True)
        open(VAULT, "w").write("\n".join(L) + "\n")
    except Exception:
        pass

    if not dry:
        try:
            json.dump(failing, open(STATE + ".tmp", "w")); os.replace(STATE + ".tmp", STATE)
        except Exception:
            pass

    print(f"fleet-warden: {len(exits)} jobs · {len(failing)} failing · {len(new)} new-alert · {len(recovered)} recovered"
          + (" [dry-run]" if dry else ""))
    for l, r in sorted(failing.items()):
        print(f"  FAIL {l}: {r}")


if __name__ == "__main__":
    main()
