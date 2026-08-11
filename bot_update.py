#!/usr/bin/env python3
"""
bot_update.py — send a concise iMessage performance update to the owner.

Usage:
  python3 bot_update.py            # full update
  python3 bot_update.py --test     # send a test ping
"""
import os, sys, json, subprocess
from datetime import datetime

DIR  = os.path.dirname(os.path.abspath(__file__))
LOG  = os.path.join(DIR, "trade_log.json")
IMESSAGE_TARGET = "krisharyan@icloud.com"

# ── legs that are controls (expected negative) ──────────────────────────────
CONTROLS = {"allin", "coinflip"}
DEAD     = {"dip", "midfade", "momentum", "endgame"}  # killed legs


def send_imessage(text: str) -> tuple[bool, str]:
    safe = text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    script = (
        'tell application "Messages"\n'
        '  set targetService to id of first service whose service type = iMessage\n'
        f'  set targetBuddy to participant "{IMESSAGE_TARGET}" of service id targetService\n'
        f'  send "{safe}" to targetBuddy\n'
        'end tell'
    )
    r = subprocess.run(["osascript", "-e", script],
                       capture_output=True, text=True, timeout=15)
    return r.returncode == 0, r.stderr.strip()


def load_log():
    try:
        return json.load(open(LOG))
    except Exception:
        return {"open": [], "closed": []}


def summarise():
    data   = load_log()
    opens  = data.get("open", [])
    closed = data.get("closed", [])

    # ── per-leg stats ────────────────────────────────────────────────────────
    leg_pnl   = {}   # leg -> [pnl, ...]
    leg_open  = {}   # leg -> count
    for p in closed:
        strat = p.get("strategy", "?")
        pnl   = p.get("pnl_usdc", p.get("pnl", 0)) or 0
        leg_pnl.setdefault(strat, []).append(pnl)
    for p in opens:
        strat = p.get("strategy", "?")
        leg_open[strat] = leg_open.get(strat, 0) + 1

    # ── totals (exclude controls) ────────────────────────────────────────────
    all_pnl   = [p for leg, ps in leg_pnl.items() for p in ps
                 if leg not in CONTROLS and leg not in DEAD]
    ctrl_pnl  = [p for leg, ps in leg_pnl.items() for p in ps
                 if leg in CONTROLS]
    total_pnl = sum(all_pnl)
    ctrl_total = sum(ctrl_pnl)
    n_closed  = len(all_pnl)
    n_open    = sum(v for k, v in leg_open.items()
                    if k not in CONTROLS and k not in DEAD)
    win_pct   = (sum(1 for p in all_pnl if p > 0) / n_closed * 100) if n_closed else 0

    # ── per-leg summary (non-control, non-dead, ≥1 exit) ────────────────────
    leg_lines = []
    for leg, ps in sorted(leg_pnl.items(), key=lambda x: sum(x[1]), reverse=True):
        if leg in CONTROLS or leg in DEAD:
            continue
        n    = len(ps)
        tot  = sum(ps)
        wr   = sum(1 for p in ps if p > 0) / n * 100
        sign = "+" if tot >= 0 else ""
        flag = " ✅" if tot > 0 and n >= 5 else (" ⚠️" if tot < 0 and n >= 5 else "")
        leg_lines.append(f"  {leg:<12} {n:>3}ex  {sign}{tot:+.2f}$  {wr:.0f}%{flag}")

    # ── build message ────────────────────────────────────────────────────────
    now   = datetime.now().strftime("%d %b %H:%M")
    emoji = "📈" if total_pnl >= 0 else "📉"
    sign  = "+" if total_pnl >= 0 else ""

    lines = [
        f"{emoji} Polymarket Bot — {now}",
        f"P&L: {sign}{total_pnl:.2f}$ ({n_closed} trades, {win_pct:.0f}% win)",
        f"Open: {n_open} positions",
        "",
        "── Per leg ──",
    ]
    if leg_lines:
        lines += leg_lines
    else:
        lines.append("  no closed trades yet")

    # controls sanity check
    ctrl_ok = ctrl_total < 0
    lines += [
        "",
        f"Controls: {ctrl_total:+.2f}$ {'✅ negative (accounting OK)' if ctrl_ok else '❌ POSITIVE — check accounting!'}",
        "Mode: paper (fund wallet to go live)",
    ]

    return "\n".join(lines)


def main():
    if "--test" in sys.argv:
        ok, err = send_imessage("✅ Polymarket bot iMessage test — working!")
        print("Sent" if ok else f"Failed: {err}")
        return

    msg = summarise()
    print(msg)
    print()
    ok, err = send_imessage(msg)
    if ok:
        print("✅ iMessage sent")
    else:
        print(f"❌ Failed: {err}")


if __name__ == "__main__":
    main()
