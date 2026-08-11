#!/usr/bin/env python3
"""moneyflow_watchdog.py — liveness guard for the 3 moneyflow detector jobs ("silence != success").

ALERT-ONLY (heal-not-kill): it never restarts or kills anything. Each run checks that every
detector job's /tmp log is FRESH (mtime within ~3x the job's own interval) and that its last
launchd run exited 0. It iMessages ONLY when a job is stale or failed, stays SILENT when all
healthy, and sends a one-shot RECOVERED note when a previously-stale job is producing fresh
output again. Dedup'd (re-nags at most every 6h per job). Keyless, fail-soft, atomic state.

Usage: python3 moneyflow_watchdog.py [check]
Env: MONEYFLOW_NOALERT=1 — classify + print but don't deliver (for testing).
"""
import os, sys, json, time, subprocess

POLY = "/Users/aryanagarwal/Documents/polymarket"
JOBS = {
    "crypto":     {"log": "/tmp/moneyflow-crypto.log",     "max_age": 360,  "label": "com.aryan.moneyflow-crypto"},
    "stocks":     {"log": "/tmp/moneyflow-stocks.log",     "max_age": 7800, "label": "com.aryan.moneyflow-stocks"},
    "polymarket": {"log": "/tmp/moneyflow-polymarket.log", "max_age": 360,  "label": "com.aryan.moneyflow-polymarket"},
}
STATE = "/tmp/moneyflow_watchdog_state.json"
RE_ALERT = 6 * 3600   # re-nag cooldown per job while it stays stale


def _exit_code(label):
    """Last launchd exit status for a job label, or '?' if unknown (treated as healthy)."""
    try:
        out = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=8).stdout
        for line in out.splitlines():
            if line.strip().endswith(label):
                return line.split()[1]
    except Exception:
        pass
    return "?"


def _load():
    try:
        return json.load(open(STATE))
    except Exception:
        return {}


def _save(st):
    tmp = STATE + ".tmp"
    json.dump(st, open(tmp, "w"))
    os.replace(tmp, STATE)


def _alert(text):
    if os.environ.get("MONEYFLOW_NOALERT") == "1":
        return
    try:
        sys.path.insert(0, POLY)
        import wa_alert
        wa_alert.notify(text)
    except Exception as e:
        sys.stderr.write(f"[mf-watchdog] alert deliver failed: {e}\n")


def check():
    st = _load()
    now = int(time.time())
    problems, recovered = [], []
    for name, cfg in JOBS.items():
        try:
            age = (now - int(os.path.getmtime(cfg["log"]))) if os.path.exists(cfg["log"]) else None
        except Exception:
            age = None
        ec = _exit_code(cfg["label"])
        stale = (age is None) or (age > cfg["max_age"])
        failed = ec not in ("0", "?")
        bad = stale or failed
        prev = st.get(name, {})
        if bad:
            reason = []
            if age is None:
                reason.append("NO LOG")
            elif stale:
                reason.append(f"stale {age}s (>{cfg['max_age']}s)")
            if failed:
                reason.append(f"launchd exit={ec}")
            # page on transition to bad, or re-nag after the cooldown
            if (not prev.get("bad")) or (now - prev.get("alerted", 0) >= RE_ALERT):
                problems.append(f"{name}: {', '.join(reason)}")
                st[name] = {"bad": True, "alerted": now}
            else:
                st[name] = {"bad": True, "alerted": prev.get("alerted", now)}
        else:
            if prev.get("bad"):
                recovered.append(f"{name}: fresh again ({age}s)")
            st[name] = {"bad": False, "alerted": 0}
    _save(st)

    if problems:
        _alert("⚠️ MoneyFlow watchdog — STALE/FAILED:\n" + "\n".join(problems) +
               "\n(a detector job stopped producing fresh output)")
    if recovered:
        _alert("✅ MoneyFlow watchdog — recovered:\n" + "\n".join(recovered))

    status = "ALL FRESH" if not problems and not recovered else \
             ("PROBLEMS " + "; ".join(problems) if problems else "RECOVERED " + "; ".join(recovered))
    print(f"[mf-watchdog] {status} | " + " ".join(f"{n}={'bad' if st[n]['bad'] else 'ok'}" for n in JOBS))


if __name__ == "__main__":
    check()
