#!/usr/bin/env python3
"""remote_command_agent.py — TCC-free remote command channel for the agent fleet.

Incoming iMessage as a trigger needs Full Disk Access (reading chat.db is TCC-blocked),
which only the owner can grant in System Settings. Until then this gives remote control
WITHOUT that: it watches a vault note for commands you tick off — reachable from your phone
via Obsidian sync, or from anywhere via the sshx terminal.

It reads PolymarketVault/Remote/Commands.md for unchecked `- [ ] <cmd>` lines, runs ONLY
allowlisted commands (fixed keyword -> fixed action; NEVER arbitrary shell), marks each line
done with a short result, appends full output to Results.md, and replies via iMessage.
Owner-scoped: the command surface is the owner's vault, and the allowlist bounds what can run.

Allowlisted: status | health | connectors | keyed | agents | refresh | remote | help
Self-throttled by launchd cadence; idempotent (only UNCHECKED lines run). Atomic, fail-soft.
"""
import os, sys, re, json, subprocess
from datetime import datetime

sys.path.insert(0, os.path.expanduser("~/Documents/polymarket"))
import agent_secrets

HERE = os.path.expanduser("~/Documents/polymarket")
VAULT = os.path.expanduser("~/Documents/PolymarketVault")
REMOTE = os.path.join(VAULT, "Remote")
CMD = os.path.join(REMOTE, "Commands.md")
RES = os.path.join(REMOTE, "Results.md")
UID = os.getuid()
ALERT_TARGETS = ["krisharyan@icloud.com", "+918449447444"]
_OSA = ('on run argv\ntell application "Messages"\n'
        '  set s to 1st service whose service type = iMessage\n'
        '  set b to buddy (item 1 of argv) of s\n'
        '  send (item 2 of argv) to b\nend tell\nend run')
AUTONOMOUS = ["connector-light", "wiring-keeper", "keyed-light", "activity-light",
              "remote-command", "remote-access"]


def imsg(msg):
    for t in ALERT_TARGETS:
        try:
            subprocess.run(["osascript", "-e", _OSA, t, msg], capture_output=True, text=True, timeout=8)
        except Exception:
            pass


def _launchctl_table():
    """label -> last_exit (int) for com.aryan.* jobs (loaded; '-' pid allowed)."""
    out = {}
    try:
        txt = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=10).stdout
        for line in txt.splitlines():
            c = line.split("\t")
            if len(c) >= 3 and c[2].startswith("com.aryan."):
                try:
                    out[c[2].replace("com.aryan.", "")] = int(c[1])
                except ValueError:
                    out[c[2].replace("com.aryan.", "")] = None
    except Exception:
        pass
    return out


def _json(path):
    try:
        return json.load(open(os.path.join(HERE, path)))
    except Exception:
        return {}


def cmd_connectors():
    d = _json(".connector_state.json")
    on = sum(1 for v in d.values() if v)
    off = [k for k, v in d.items() if not v]
    s = f"connectors {on}/{len(d)} online"
    return s + (" · down: " + ", ".join(off[:8]) if off else " · all up")


def cmd_keyed():
    d = _json(".keyed_state.json")
    keyed = [k for k, v in d.items() if isinstance(v, dict) and v.get("keyed")]
    miss = [k for k, v in d.items() if isinstance(v, dict) and not v.get("keyed")]
    return f"keyed services {len(keyed)}/{len(d)} configured" + (" · missing: " + ", ".join(miss) if miss else "")


def cmd_agents():
    t = _launchctl_table()
    rows = [f"{a}={'ok' if t.get(a) == 0 else t.get(a)}" for a in AUTONOMOUS if a in t]
    miss = [a for a in AUTONOMOUS if a not in t]
    return "agents: " + ", ".join(rows) + (" · NOT LOADED: " + ", ".join(miss) if miss else "")


def cmd_health():
    t = _launchctl_table()
    bad = [f"{k}({v})" for k, v in t.items() if v not in (0, None)]
    return (f"fleet: {len(t)} com.aryan jobs loaded · " +
            (f"⚠️ nonzero-exit: {', '.join(bad[:10])}" if bad else "all last-exit 0") +
            " · " + cmd_connectors() + " · " + cmd_keyed())


def cmd_status():
    return cmd_health()


def cmd_refresh():
    done = []
    for lbl in ["connector-light", "wiring-keeper", "keyed-light", "activity-light"]:
        try:
            subprocess.run(["launchctl", "kickstart", "-k", f"gui/{UID}/com.aryan.{lbl}"],
                           capture_output=True, timeout=15)
            done.append(lbl)
        except Exception:
            pass
    return "kickstarted live-map agents: " + ", ".join(done)


def cmd_remote():
    try:
        subprocess.run(["launchctl", "kickstart", "-k", f"gui/{UID}/com.aryan.remote-access"],
                       capture_output=True, timeout=15)
        return "remote-access (sshx) (re)started — URL will appear in Remote Access.md + texted to you"
    except Exception as e:
        return f"remote start failed: {e}"


def cmd_help():
    return "commands: status | health | connectors | keyed | agents | refresh | remote | help"


HANDLERS = {
    "status": cmd_status, "health": cmd_health, "connectors": cmd_connectors,
    "keyed": cmd_keyed, "keys": cmd_keyed, "agents": cmd_agents,
    "refresh": cmd_refresh, "remote": cmd_remote, "help": cmd_help,
}

TEMPLATE = """---
type: dashboard
cssclasses: [dashboard]
---

> [!banner] \U0001f4f1 Remote command channel
> Tick a box (`- [ ]` -> run it) from your phone (Obsidian sync) or the sshx terminal.
> Only the allowlisted commands below run — nothing else. Results go to [[Results]] + iMessage.

## Run a command (uncheck = pending)

- [ ] status
- [ ] help

## Allowlist
`status` `health` `connectors` `keyed` `agents` `refresh` `remote` `help`

> [!note]- How
> `remote_command_agent.py` (launchd, ~45s) reads this note, runs ONLY the allowlisted keyword,
> marks the line done with a short result, and texts you. Incoming-iMessage triggering needs
> Full Disk Access (System Settings) — not enabled.

→ [[Results]] · [[Remote Access]] · [[Home]]
"""


def ensure_template():
    os.makedirs(REMOTE, exist_ok=True)
    if not os.path.exists(CMD):
        with open(CMD, "w", encoding="utf-8") as f:
            f.write(TEMPLATE)


CHATDB = os.path.expanduser("~/Library/Messages/chat.db")
SEEN = os.path.join(HERE, ".remote_imsg_seen.json")
OWNER_HANDLES = ALERT_TARGETS  # inbound iMessages run ONLY from the owner's own handles


def _seen_load():
    try:
        return int(json.load(open(SEEN)).get("rowid", 0))
    except Exception:
        return 0


def _seen_save(rowid):
    try:
        tmp = SEEN + ".tmp"
        json.dump({"rowid": rowid}, open(tmp, "w"))
        os.replace(tmp, SEEN)
    except Exception:
        pass


def scan_imessage():
    """FDA-gated inbound channel: run allowlisted cmds the OWNER texts in.
    Fully fail-soft — without Full Disk Access chat.db is unreadable, so this
    returns [] silently and the vault-note channel keeps working unchanged.
    Loop-safe: only a bare allowlist keyword as the FIRST word runs, so the
    bot's own emoji-prefixed replies (and every other alert) are ignored."""
    if not os.path.exists(CHATDB):
        return []
    import sqlite3
    try:
        con = sqlite3.connect(f"file:{CHATDB}?mode=ro", uri=True, timeout=4)
        cur = con.cursor()
        cur.execute("SELECT COALESCE(MAX(ROWID), 0) FROM message")
        maxid = cur.fetchone()[0]
    except Exception:
        return []  # locked / FDA not granted
    last = _seen_load()
    if last == 0:  # first run: anchor to now, never replay history
        _seen_save(maxid)
        con.close()
        return []
    ran = []
    try:
        qmarks = ",".join("?" for _ in OWNER_HANDLES)
        cur.execute(
            "SELECT m.ROWID, m.text FROM message m JOIN handle h ON m.handle_id = h.ROWID "
            "WHERE m.ROWID > ? AND m.text IS NOT NULL "
            f"AND h.id IN ({qmarks}) ORDER BY m.ROWID",
            [last, *OWNER_HANDLES])
        rows = cur.fetchall()
    except Exception:
        con.close()
        return []
    con.close()
    newmax = last
    for rowid, txt in rows:
        newmax = max(newmax, rowid)
        words = (txt or "").strip().lower().split()
        cmd = words[0] if words else ""
        if cmd in HANDLERS:
            try:
                result = HANDLERS[cmd]()
            except Exception as e:
                result = f"error: {e}"
            ran.append((cmd, result))
    if newmax > last:
        _seen_save(newmax)
    return ran


def main():
    agent_secrets.load()
    ensure_template()
    try:
        text = open(CMD, encoding="utf-8").read()
    except Exception:
        return
    lines = text.splitlines()
    ran = []
    for i, ln in enumerate(lines):
        m = re.match(r"^(\s*[-*]\s*)\[ \]\s*([A-Za-z]+)\s*$", ln)
        if not m:
            continue
        cmd = m.group(2).lower()
        if cmd not in HANDLERS:
            lines[i] = f"{m.group(1)}[x] {cmd} — ⛔ not in allowlist ({datetime.now():%H:%M})"
            ran.append((cmd, "not allowed"))
            continue
        try:
            result = HANDLERS[cmd]()
        except Exception as e:
            result = f"error: {e}"
        ts = datetime.now().strftime("%H:%M:%S")
        short = (result[:80] + "…") if len(result) > 80 else result
        lines[i] = f"{m.group(1)}[x] {cmd} — {short} ({ts})"
        ran.append((cmd, result))
    # write vault commands back (atomic) only if any ran
    if ran:
        tmp = CMD + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        os.replace(tmp, CMD)
    # inbound iMessage channel (FDA-gated; fail-soft / [] without it)
    allran = ran + [("imsg:" + c, r) for c, r in scan_imessage()]
    if not allran:
        return
    # append full results
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(RES, "a", encoding="utf-8") as f:
        for cmd, result in allran:
            f.write(f"- **{now} · {cmd}** — {result}\n")
    imsg("\U0001f4f1 remote cmd:\n" + "\n".join(f"• {c}: {r}" for c, r in allran))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
