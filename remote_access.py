#!/usr/bin/env python3
"""remote_access.py — persistent sshx web terminal so the fleet can be operated from a phone.

Starts `sshx -q` (a secure web-based terminal), captures the session URL, PUBLISHES it
to the vault (Remote Access.md) and texts it to the owner via iMessage, then stays in the
foreground holding the session alive. Run under launchd with KeepAlive so a dropped session
auto-restarts (a fresh URL is captured + re-published each time).

SECURITY: anyone with the URL gets a full shell on this Mac. The URL is unguessable and is
sent ONLY to the owner (vault + owner iMessage). To STOP remote access:
    launchctl bootout gui/$(id -u)/com.aryan.remote-access
"""
import os, sys, subprocess
from datetime import datetime

SSHX = os.path.expanduser("~/.local/bin/sshx")
VAULT = os.path.expanduser("~/Documents/PolymarketVault")
NOTE = os.path.join(VAULT, "Remote Access.md")
ALERT_TARGETS = ["krisharyan@icloud.com", "+918449447444"]
_OSA = ('on run argv\ntell application "Messages"\n'
        '  set s to 1st service whose service type = iMessage\n'
        '  set b to buddy (item 1 of argv) of s\n'
        '  send (item 2 of argv) to b\nend tell\nend run')


def imsg(msg):
    for t in ALERT_TARGETS:
        try:
            subprocess.run(["osascript", "-e", _OSA, t, msg], capture_output=True, text=True, timeout=8)
        except Exception:
            pass


def publish(url):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    body = (f"---\ntype: dashboard\ncssclasses: [dashboard, activity, live]\n---\n\n"
            f"> [!banner] <span class=\"live-dot\"></span> Remote access LIVE\n"
            f"> sshx web terminal · started {now}\n\n"
            f"## \U0001f517 Open on your phone\n\n[{url}]({url})\n\n`{url}`\n\n"
            f"> [!warning] Anyone with this URL gets a full shell on this Mac.\n"
            f"> The URL is unguessable and changes whenever the session restarts.\n"
            f"> Stop remote access: `launchctl bootout gui/$(id -u)/com.aryan.remote-access`\n\n"
            f"→ [[Activity]] · [[Home]]\n")
    try:
        with open(NOTE, "w", encoding="utf-8") as f:
            f.write(body)
    except Exception:
        pass


def main():
    if not os.path.exists(SSHX):
        sys.exit("sshx not installed at " + SSHX)
    p = subprocess.Popen([SSHX, "-q", "--name", "polymarket-mac"],
                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    url = ""
    for _ in range(30):
        line = p.stdout.readline()
        if not line:
            break
        line = line.strip()
        if line.startswith("http"):
            url = line
            break
    if url:
        publish(url)
        imsg(f"\U0001f517 Polymarket Mac remote terminal LIVE: {url}\n(anyone with this link gets a shell — it's only sent to you)")
    p.wait()   # hold the session; launchd KeepAlive restarts us if it drops


if __name__ == "__main__":
    main()
