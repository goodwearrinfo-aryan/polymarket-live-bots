#!/usr/bin/env python3
"""
agents_dashboard.py — live status board for the whole com.aryan.* worker fleet.

Reads `launchctl list` (PID + last exit code) and each plist's schedule, then
classifies every job and writes a self-contained HTML dashboard.

Classification (encodes the project's hard-won exit-code lessons):
  RUNNING   — has a live PID right now
  OK        — last exit 0 (ran clean, idle between cycles)
  BENIGN    — exit -15 (SIGTERM: perl-alarm cap / sleep-wake — usually fine)
  CONFIG    — exit 78 (EX_CONFIG: StandardOut path under ~/Documents = TCC)
  ERROR     — any other non-zero exit

No side effects beyond writing the HTML. Read-only on the fleet (heal-not-kill).
"""
import subprocess, os, html, re, json
from datetime import datetime, timezone

LA_DIR   = os.path.expanduser("~/Library/LaunchAgents")
OUT_HTML = os.path.expanduser("~/Documents/polymarket/agents_dashboard.html")
VAULT    = os.path.expanduser("~/Documents/PolymarketVault")
OUT_MD   = os.path.join(VAULT, "Reports", "Agent Fleet.md")
HUB_MD   = os.path.join(VAULT, "Status Hub.md")
PM_DIR   = os.path.dirname(os.path.abspath(__file__))
FUT_STATE = os.path.expanduser("~/Documents/futures-bot/paper_state.json")

# Workers that feed the Obsidian vault (highlighted + cross-checked vs vault freshness)
VAULT_WORKERS = {"obsidian-enrich", "obsidian-snapshot", "obsidian-live-feed",
                 "brain-ingest", "stock-obsidian", "market-indices", "basket-watch",
                 "vault-librarian", "vault-connector"}

def plist_get(path, key):
    try:
        return subprocess.run(["/usr/libexec/PlistBuddy", "-c", f"Print :{key}", path],
                              capture_output=True, text=True).stdout.strip()
    except Exception:
        return ""

def schedule(label):
    f = os.path.join(LA_DIR, f"{label}.plist")
    if not os.path.exists(f):
        return "—"
    si = plist_get(f, "StartInterval")
    if si.isdigit():
        s = int(si)
        if s % 3600 == 0: return f"every {s//3600}h"
        if s % 60   == 0: return f"every {s//60}m"
        return f"every {s}s"
    cal = subprocess.run(["/usr/libexec/PlistBuddy", "-c", "Print :StartCalendarInterval",
                          f], capture_output=True, text=True).stdout
    hrs = re.findall(r"Hour = (\d+)", cal)
    if hrs:
        return "daily @ " + ",".join(f"{int(h):02d}h" for h in sorted(set(hrs), key=int))
    return "on-demand"

def classify(pid, ec):
    if pid not in ("-", "", None):  return ("RUNNING", "live")
    if ec == "0":                   return ("OK",      "clean")
    if ec == "-15":                 return ("BENIGN",  "SIGTERM (likely fine)")
    if ec == "78":                  return ("CONFIG",  "exit-78: log path under ~/Documents (TCC)")
    return ("ERROR", f"exit {ec}")

def collect():
    out = subprocess.run(["launchctl", "list"], capture_output=True, text=True).stdout
    rows = []
    for line in out.splitlines():
        p = line.split("\t")
        if len(p) < 3 or "com.aryan." not in p[2]:
            continue
        pid, ec, label = p[0], p[1], p[2]
        status, note = classify(pid, ec)
        rows.append(dict(label=label.replace("com.aryan.", ""), pid=pid, ec=ec,
                         status=status, note=note, sched=schedule(label)))
    rows.sort(key=lambda r: ({"ERROR":0,"CONFIG":1,"RUNNING":2,"BENIGN":3,"OK":4}[r["status"]], r["label"]))
    return rows

COLORS = {"RUNNING":"#00d4ff","OK":"#2ecc71","BENIGN":"#f0b429","CONFIG":"#e67e22","ERROR":"#ff4d4d"}

def vault_health():
    """Freshest .md mtime in the vault + status. Stale (>2h) = the writers stalled."""
    newest, age_min = 0.0, None
    for root, _, files in os.walk(VAULT):
        if "/.obsidian" in root or "/.trash" in root:
            continue
        for fn in files:
            if fn.endswith(".md"):
                try:
                    m = os.path.getmtime(os.path.join(root, fn))
                    if m > newest: newest = m
                except OSError:
                    pass
    if newest:
        age_min = (datetime.now().timestamp() - newest) / 60
    return newest, age_min

def render(rows, vnewest, vage):
    counts = {}
    for r in rows: counts[r["status"]] = counts.get(r["status"], 0) + 1
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    chips = "".join(
        f'<span class="chip" style="border-color:{COLORS[k]};color:{COLORS[k]}">{k} {counts.get(k,0)}</span>'
        for k in ["RUNNING","OK","BENIGN","CONFIG","ERROR"])
    # vault panel
    if vage is None:
        vcol, vtxt = COLORS["ERROR"], "no .md found"
    elif vage <= 120:
        vcol, vtxt = COLORS["OK"], f"fresh — last write {vage:.0f}m ago"
    else:
        vcol, vtxt = COLORS["ERROR"], f"STALE — last write {vage/60:.1f}h ago (writers stalled?)"
    vwork = [r for r in rows if r["label"] in VAULT_WORKERS]
    vbad  = [r for r in vwork if r["status"] in ("ERROR","CONFIG")]
    vline = (f'<div class="chip" style="border-color:{vcol};color:{vcol}">VAULT {vtxt}</div>'
             f'<span class=muted style="margin-left:10px">{len(vwork)} vault-writers · '
             f'{"all healthy" if not vbad else "⚠ "+", ".join(r["label"] for r in vbad)}</span>')
    trs = "".join(
        f'<tr{" style=background:#0c1620" if r["label"] in VAULT_WORKERS else ""}>'
        f'<td class="dot"><span style="background:{COLORS[r["status"]]}"></span></td>'
        f'<td class="lbl">{html.escape(r["label"])}{" 📓" if r["label"] in VAULT_WORKERS else ""}</td>'
        f'<td style="color:{COLORS[r["status"]]}">{r["status"]}</td>'
        f'<td class="muted">{html.escape(r["sched"])}</td>'
        f'<td class="muted">{html.escape(r["pid"]) if r["pid"]!="-" else "—"}</td>'
        f'<td class="muted">{html.escape(r["note"])}</td></tr>'
        for r in rows)
    return f"""<!doctype html><html><head><meta charset=utf-8>
<meta http-equiv=refresh content=30>
<title>Agent Fleet</title><style>
body{{background:#0a0e14;color:#cdd6e4;font:13px/1.5 ui-monospace,Menlo,monospace;margin:0;padding:24px}}
h1{{font-size:18px;margin:0 0 4px;color:#fff}}
.sub{{color:#5c6b7a;margin-bottom:16px}}
.chip{{display:inline-block;border:1px solid;border-radius:12px;padding:2px 10px;margin-right:8px;font-weight:600}}
table{{border-collapse:collapse;width:100%;margin-top:16px}}
th{{text-align:left;color:#5c6b7a;font-weight:600;border-bottom:1px solid #1d2733;padding:6px 10px}}
td{{padding:6px 10px;border-bottom:1px solid #11161d}}
.lbl{{color:#fff;font-weight:600}} .muted{{color:#5c6b7a}}
.dot span{{display:inline-block;width:9px;height:9px;border-radius:50%}}
tr:hover td{{background:#0e141c}}
</style></head><body>
<h1>🤖 Agent Fleet — {len(rows)} workers</h1>
<div class=sub>com.aryan.* launchd jobs · auto-refresh 30s · generated {now}</div>
<div>{chips}</div>
<div style="margin-top:14px">{vline}</div>
<table><tr><th></th><th>worker</th><th>status</th><th>schedule</th><th>pid</th><th>note</th></tr>
{trs}</table>
<div class=sub style="margin-top:16px">RUNNING=live now · OK=ran clean · BENIGN=SIGTERM (usually fine) · CONFIG=exit-78 TCC log path · ERROR=needs a look</div>
</body></html>"""

def render_md(rows, vage):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    counts = {}
    for r in rows: counts[r["status"]] = counts.get(r["status"], 0) + 1
    vtxt = "no data" if vage is None else (f"fresh ({vage:.0f}m)" if vage <= 120 else f"STALE ({vage/60:.1f}h)")
    head = (f"---\ntype: dashboard\nstatus: live\n---\n\n"
            f"# 🤖 Agent Fleet\n\n*Auto-generated {now} · {len(rows)} workers · "
            f"do not edit (regenerated every 60s by agents_dashboard.py)*\n\n"
            f"**Status:** " + " · ".join(f"{k} {counts.get(k,0)}"
            for k in ["RUNNING","OK","BENIGN","CONFIG","ERROR"] if counts.get(k)) +
            f"\n\n**Vault freshness:** {vtxt}\n\n"
            "| | worker | status | schedule | note |\n|--|--|--|--|--|\n")
    body = "".join(
        f'| {"📓" if r["label"] in VAULT_WORKERS else ""} | {r["label"]} | {r["status"]} '
        f'| {r["sched"]} | {r["note"]} |\n' for r in rows)
    return head + body

def _load(fn):
    try:
        return json.load(open(os.path.join(PM_DIR, fn)))
    except Exception:
        return {}

def bot_tracks():
    """Top-line numbers for each money track — for the merged hub."""
    t = {}
    # structural-arb track (the only +EV family) — combined gate distance
    arb_n = 0
    for fn, real in (("basketlock_state.json", False), ("dataarb_state.json", True),
                     ("monoarb_state.json", False)):
        res = _load(fn).get("resolved", [])
        arb_n += sum(1 for p in res if (not real or p.get("kind") == "real"))
        t[fn.split("_")[0]] = {"open": len(_load(fn).get("open", []) or _load(fn).get("captured", [])),
                               "resolved": len(res)}
    t["arb_gate"] = arb_n
    # analyst book
    pos = _load("analyst_positions.json").get("positions", [])
    t["analyst_open"] = sum(1 for p in pos if not p.get("settled"))
    # futures bot
    try:
        s = json.load(open(FUT_STATE))
        t["futures"] = {"eq": s.get("equity", 1000.0), "n": len(s.get("closed", [])),
                        "runner": len(s.get("runner_closed", [])), "lev": len(s.get("lev_closed", []))}
    except Exception:
        t["futures"] = None
    return t

def render_hub(rows, vage, t):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    counts = {}
    for r in rows: counts[r["status"]] = counts.get(r["status"], 0) + 1
    bad = [r["label"] for r in rows if r["status"] in ("ERROR", "CONFIG")]
    fleet_line = " · ".join(f"{k} {counts.get(k,0)}" for k in
                            ["RUNNING","OK","BENIGN","CONFIG","ERROR"] if counts.get(k))
    fleet_health = "🟢 all healthy" if not bad else "🔴 " + ", ".join(bad)
    vtxt = "🔴 no data" if vage is None else (f"🟢 fresh ({vage:.0f}m ago)" if vage <= 120
            else f"🔴 STALE ({vage/60:.1f}h ago)")
    f = t.get("futures")
    fut = ("no state" if not f else
           f"main eq ${f['eq']:.2f} (n={f['n']}) · runner n={f['runner']} · lev n={f['lev']}")
    return f"""---
type: dashboard
status: live
updated: {now}
---

# 🛰 Status Hub

*One merged at-a-glance view. Auto-generated {now} — do not edit (regenerated every 60s by `agents_dashboard.py`). Drill into the linked notes for detail.*

## 🤖 Agent Fleet — {len(rows)} workers
**{fleet_line}** · {fleet_health}
→ full board: [[Reports/Agent Fleet|Agent Fleet]]

## 📓 Vault
Freshness: {vtxt} · {len([r for r in rows if r['label'] in VAULT_WORKERS])} vault-writers
→ [[Home]] · [[Dashboard]]

## 💰 Money tracks
- **Structural-arb (only +EV family):** gate {t['arb_gate']}/30 combined exits
  - basketlock: {t['basketlock']['open']} open / {t['basketlock']['resolved']} resolved
  - dataarb: {t['dataarb']['open']} open / {t['dataarb']['resolved']} resolved (real)
  - monoarb: {t['monoarb']['open']} open / {t['monoarb']['resolved']} resolved
  → [[basketlock]] · [[The Gate]]
- **Analyst book:** {t['analyst_open']} open positions (settles at resolution)
- **Futures bot (paper, separate):** {fut}

## 🔗 Drill-down
[[hot]] · [[BRAIN]] · [[Edge Map]] · [[Strategy Graveyard]] · [[Lessons/Lessons index|Lessons]]
"""

if __name__ == "__main__":
    rows = collect()
    vnewest, vage = vault_health()
    with open(OUT_HTML, "w") as f:
        f.write(render(rows, vnewest, vage))
    try:
        os.makedirs(os.path.dirname(OUT_MD), exist_ok=True)
        with open(OUT_MD, "w") as f:
            f.write(render_md(rows, vage))
        with open(HUB_MD, "w") as f:
            f.write(render_hub(rows, vage, bot_tracks()))
        mirror = "+ vault mirror + status hub"
    except Exception as e:
        mirror = f"(vault mirror FAILED: {e})"
    print(f"wrote {OUT_HTML} — {len(rows)} workers {mirror}; vault age={vage}")
    for r in rows:
        if r["status"] in ("ERROR", "CONFIG"):
            print(f"  ⚠ {r['label']}: {r['note']}")
