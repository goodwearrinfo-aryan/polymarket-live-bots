#!/usr/bin/env python3
"""
Self-healing supervisor for the lawyer fleet.
Checks all services are running, restarts dead ones.
Runs as a launchd job every 5 minutes.
"""
import subprocess, sys, os, urllib.request, datetime, json

SERVICES = [
    {"name": "lawyer-ui", "port": 8765, "script": "lawyer_ui.py", "plist": "com.aryan.lawyer-ui"},
    {"name": "lawyer-fleet", "port": 8766, "script": "lawyer_fleet.py", "plist": "com.aryan.lawyer-fleet"},
]

# Process-only services (no health port — just check if process is running)
PROC_SERVICES = [
    {"name": "lawyer-whatsapp", "script": "lawyer_whatsapp.py", "plist": "com.aryan.lawyer-whatsapp"},
]

LOG = "/tmp/lawyer_heal.log"
DOCS = os.path.expanduser("~/Documents/polymarket")

def log(msg):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG, "a") as f:
        f.write(line + "\n")

def check_port(port, timeout=3):
    try:
        with urllib.request.urlopen(f"http://localhost:{port}/health", timeout=timeout) as r:
            return r.status == 200
    except:
        return False

def is_running(script_name):
    try:
        r = subprocess.run(["pgrep", "-f", script_name], capture_output=True, text=True)
        return bool(r.stdout.strip())
    except:
        return False

def launch(service):
    script = os.path.join(DOCS, service["script"])
    log(f"Starting {service['name']} (port {service['port']})")
    subprocess.Popen(
        [sys.executable, script],
        stdout=open(f"/tmp/{service['name']}.log", "a"),
        stderr=subprocess.STDOUT,
        cwd=DOCS,
    )

def heal():
    log("=== Lawyer Fleet Health Check ===")
    all_ok = True
    for svc in SERVICES:
        port_ok = check_port(svc["port"])
        proc_ok = is_running(svc["script"])

        if port_ok and proc_ok:
            log(f"  OK: {svc['name']} port={svc['port']} pid=running")
        else:
            all_ok = False
            log(f"  DEAD: {svc['name']} port_ok={port_ok} proc_ok={proc_ok} -> RESTARTING")
            if not proc_ok:
                launch(svc)
            else:
                log(f"  Process running but port dead — killing and restarting {svc['name']}")
                subprocess.run(["pkill", "-f", svc["script"]], check=False)
                import time; time.sleep(2)
                launch(svc)

    # Check process-only services (no port health check)
    for svc in PROC_SERVICES:
        proc_ok = is_running(svc["script"])
        if proc_ok:
            log(f"  OK: {svc['name']} pid=running")
        else:
            # Only restart if OpenWA is up (WhatsApp bot is useless without it)
            owa_up = check_port(2785, timeout=2) if svc["name"] == "lawyer-whatsapp" else True
            if owa_up:
                all_ok = False
                log(f"  DEAD: {svc['name']} -> RESTARTING")
                script = os.path.join(DOCS, svc["script"])
                subprocess.Popen(
                    [sys.executable, script],
                    stdout=open(f"/tmp/{svc['name']}.log", "a"),
                    stderr=subprocess.STDOUT,
                    cwd=DOCS,
                )
            else:
                log(f"  SKIP: {svc['name']} — OpenWA not running, skip restart")

    if all_ok:
        log("All services healthy")
    return all_ok

if __name__ == "__main__":
    heal()
