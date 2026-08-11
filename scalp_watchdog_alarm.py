#!/usr/bin/env python3
"""scalp_watchdog_alarm.py — INDEPENDENT liveness alarm for the scalp watchdog.

Closes the one real silent-failure hole: watchdog_loop.sh is an UNSUPERVISED nohup-orphan
(it must be — launchd bash hits TCC EPERM on ~/Documents), and the existing sentinels
(health_sentinel.py / sentinel_sweep.py) are run BY that watchdog, so they die WITH it and
can never report its death. If watchdog_loop.sh dies (reboot, crash), the scalp lab silently
stops producing and nobody is told — the exact "silence != success" failure.

This alarm runs from its OWN launchd job (com.aryan.scalp-watchdog-alarm), INDEPENDENT of the
watchdog, on the TCC-clear framework python. Liveness proxy = freshness of scalp_lab_state.json:
the watchdog rewrites it every ~60s, so >STALE_S without an update means the watchdog is dead
or stuck. ALERT-ONLY by design — it does NOT relaunch (a launchd relauncher would hit the same
TCC wall that forced the nohup-orphan; heal-not-kill). On a NEW problem it iMessages the relaunch
recipe; you relaunch manually. Dedup'd: fires once on GREEN->RED (and once on RED->GREEN recovery),
never re-nags a still-red state.

Read-only to bot state (only stat's the file); its own dedup state lives in /tmp.
Usage: python3 scalp_watchdog_alarm.py            # one check (launchd calls this ~15min)
       python3 scalp_watchdog_alarm.py --test     # send one [TEST] iMessage (reachability check)
"""
import os, sys, json, time, subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(HERE, "scalp_lab_state.json")   # the watchdog's heartbeat-by-proxy
DEDUP_FILE = "/tmp/scalp_watchdog_alarm.json"             # our OWN state (out of ~/Documents)
RECIPIENT = "+918449447444"                               # same endpoint as health_sentinel.py
STALE_S = 300                                            # 5 missed ~60s cycles => watchdog dead/stuck
RELAUNCH = "cd ~/Documents/polymarket && nohup bash watchdog_loop.sh &"


def _imessage(msg, recipient=RECIPIENT):
    # No shell: pass the AppleScript as a single argv to osascript so NO quote in msg
    # (apostrophe OR double-quote) can break the command. Escape only for the AS string literal.
    safe = msg.replace("\\", "\\\\").replace('"', '\\"')
    script = ('tell application "Messages" to send "%s" to buddy "%s" '
              'of (service 1 whose service type is iMessage)' % (safe, recipient))
    try:
        subprocess.run(["osascript", "-e", script], capture_output=True, timeout=20)
    except Exception:
        pass


def _proc_running():
    try:
        return subprocess.run(["pgrep", "-f", "watchdog_loop.sh"],
                              capture_output=True).returncode == 0
    except Exception:
        return None      # unknown


def status():
    """Returns (is_red: bool, detail: str)."""
    proc = _proc_running()
    proc_s = "running" if proc else ("absent" if proc is False else "proc-unknown")
    try:
        age = time.time() - os.path.getmtime(STATE_FILE)
    except Exception:
        return True, f"scalp_lab_state.json missing/unreadable (watchdog_loop.sh {proc_s})"
    if age > STALE_S:
        return True, f"scalp_lab_state.json stale {age/60:.1f}m (watchdog_loop.sh {proc_s})"
    return False, f"state fresh {age:.0f}s ago (watchdog_loop.sh {proc_s})"


def _load_dedup():
    try:
        return json.load(open(DEDUP_FILE))
    except Exception:
        return {"status": "GREEN", "since": 0}


def _save_dedup(d):
    tmp = DEDUP_FILE + ".tmp"
    json.dump(d, open(tmp, "w"))
    os.replace(tmp, DEDUP_FILE)


def run(send=True):
    is_red, detail = status()
    now = int(time.time())
    prev = _load_dedup()
    new = "RED" if is_red else "GREEN"
    fired = None
    if new != prev.get("status"):
        if is_red:
            msg = f"🔴 SCALP WATCHDOG DOWN: {detail}. Relaunch: {RELAUNCH}"
        else:
            down_for = now - prev.get("since", now)
            msg = f"🟢 scalp watchdog recovered ({detail}); was red ~{down_for//60}m"
        fired = msg
        if send:
            _imessage(msg)
        else:
            print("[DRY] would send:", msg)
        _save_dedup({"status": new, "since": now})
    else:
        # no transition; keep original 'since'
        _save_dedup({"status": new, "since": prev.get("since", now)})
    print(f"scalp-watchdog-alarm: {new} — {detail}" + (" — ALERTED" if fired else " — quiet (no transition)"))
    return {"status": new, "detail": detail, "fired": fired}


def main():
    if "--test" in sys.argv:
        _imessage("🧪 [TEST] scalp-watchdog-alarm reachability check — ignore. "
                  "If you see this, the alarm's iMessage path works.")
        print("sent [TEST] iMessage to", RECIPIENT)
        return
    run(send=True)


if __name__ == "__main__":
    main()
