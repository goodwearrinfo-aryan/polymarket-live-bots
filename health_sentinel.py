#!/usr/bin/env python3
"""health_sentinel.py — DETERMINISTIC 24/7 health sentinel (no LLM, free, watchdog-run).

The always-on, zero-cost form of the pm-launchd-warden + pm-service-sentinel agents: it runs
their checks every cycle as pure Python and iMessages ONLY on a NEW real problem (silence =
healthy). The Claude Code agents stay the on-demand DEEP-DIVE when this fires — same split as
dsr_gate.py (deterministic) vs pm-dsr-gatekeeper (judgment). Runs free on the watchdog so it
never burns Claude Code credits (the "token-free conversions" discipline).

Checks (all deterministic):
  1. launchd fleet — flag exit-78 (TCC log thrash) or any UNEXPECTED non-zero exit; the
     known benign-by-design -15 jobs (ws-basket-watch / datagen-report / newsnow) are whitelisted.
  2. services — dashboard :8077/:8800 HTTP 200; ws-basket-watch process+heartbeat fresh;
     watchdog_loop.sh nohup-orphan alive (the bot supervisor — loudest if down).
  3. marking honesty — a control leg POSITIVE at n>=30 = a marking bug (the integrity check).

Alerts are DEDUP'd: iMessage fires only on a NEW red (state transition), re-alerts a still-red
issue once/day, and auto-clears when resolved. All-clear = a log line, no message.

Run:  python3 health_sentinel.py          # one sweep (the watchdog calls this ~30min)
      python3 health_sentinel.py --test    # print full status, no alert / no dedup
"""
import os, sys, json, time, subprocess, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "health_sentinel_state.json")
RECIPIENT = "+918449447444"
BENIGN_15 = {"com.aryan.ws-basket-watch", "com.aryan.datagen-report", "com.aryan.newsnow"}
CONTROLS = {"allin", "coinflip", "coindown", "basketrand", "windowshutrand", "allyes"}
RE_ALERT_SEC = 86400          # re-alert a still-red issue once a day
HB_STALE_SEC = 1800           # ws-basket heartbeat older than this = stale


def _imessage(msg, recipient=RECIPIENT):
    safe = msg.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    safe = "".join(c if ord(c) < 128 else " " for c in safe)
    os.system(f'osascript -e \'tell application "Messages" to send "{safe}" to buddy "{recipient}" of (service 1 whose service type is iMessage)\' 2>/dev/null')


def _sh(cmd):
    try:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=15).stdout
    except Exception:
        return ""


def _http_ok(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=4) as r:
            return r.status == 200
    except Exception:
        return False


def check_launchd():
    out = []
    for line in _sh("launchctl list | grep aryan").splitlines():
        p = line.split()
        if len(p) < 3:
            continue
        ex, label = p[1], p[2]
        if ex in ("0", "-"):
            continue
        if ex == "-15" and label in BENIGN_15:        # benign by design
            continue
        out.append(f"launchd {label}: {'exit-78 (TCC/thrash)' if ex == '78' else 'exit ' + ex}")
    return out


def check_services():
    out = []
    for port in (8077, 8800):
        if not _http_ok(port):
            out.append(f"service dashboard:{port} not responding 200")
    if not _sh("ps aux | grep '[w]s_basket_watch'").strip():
        out.append("service ws-basket-watch process not running")
    else:
        wlog = "/tmp/ws-basket-watch.log"
        if os.path.exists(wlog) and time.time() - os.path.getmtime(wlog) > HB_STALE_SEC:
            out.append(f"service ws-basket-watch heartbeat STALE ({(time.time()-os.path.getmtime(wlog))/60:.0f}m)")
    if not _sh("ps aux | grep '[w]atchdog_loop'").strip():
        out.append("CRITICAL: watchdog_loop.sh NOT running (bot supervisor down)")
    return out


def check_marking():
    out = []
    try:
        s = json.load(open(os.path.join(HERE, "scalp_lab_state.json")))
    except Exception:
        return ["marking: scalp_lab_state.json unreadable"]
    for leg, v in s.items():
        if leg in CONTROLS and isinstance(v, dict):
            cl = [t["pnl_usdc"] for t in v.get("closed", []) if isinstance(t, dict) and t.get("pnl_usdc") is not None]
            if len(cl) >= 30 and sum(cl) > 0:
                out.append(f"MARKING BUG: control '{leg}' POSITIVE ${sum(cl):+.2f} at n={len(cl)}")
    return out


def sweep():
    return check_launchd() + check_services() + check_marking()


def main():
    issues = sweep()
    if "--test" in sys.argv:
        print("HEALTH SENTINEL —", time.strftime("%Y-%m-%d %H:%M"))
        print("\n".join("  🔴 " + i for i in issues) if issues
              else "  ✅ all clear (launchd fleet · services · marking honesty)")
        return
    try:
        st = json.load(open(STATE))
    except Exception:
        st = {"alerted": {}}
    now = time.time()
    fresh = []
    for i in issues:
        if now - st["alerted"].get(i, 0) > RE_ALERT_SEC:   # new, or stale enough to re-alert
            fresh.append(i)
            st["alerted"][i] = now
    st["alerted"] = {k: v for k, v in st["alerted"].items() if k in issues}   # auto-clear resolved
    st["last_sweep"] = time.strftime("%Y-%m-%d %H:%M")
    st["open_issues"] = issues
    json.dump(st, open(STATE, "w"), indent=2)
    if fresh:
        _imessage("🔴 BOT HEALTH: " + " | ".join(fresh[:5]))
        print("[health_sentinel] ALERTED:", "; ".join(fresh))
    else:
        print(f"[health_sentinel] {time.strftime('%H:%M')} — " +
              (f"{len(issues)} known issue(s), no new alert" if issues else "all clear"))


if __name__ == "__main__":
    main()
