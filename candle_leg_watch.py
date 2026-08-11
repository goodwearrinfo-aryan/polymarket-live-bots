#!/usr/bin/env python3
"""candle_leg_watch.py — iMessage alert when candlesig/coinrev opens or closes
a trade, or when bots/DB go down. Runs every 10 min via launchd."""
import json, os, subprocess, sys

STATE_FILE = os.path.expanduser("~/Documents/polymarket/.candle_watch_state.json")
TARGET = "krisharyan@icloud.com"

def imsg(text):
    script = ('tell application "Messages"\n'
              '  set s to 1st service whose service type = iMessage\n'
              f'  set b to buddy "{TARGET}" of s\n'
              f'  send "{text}" to b\n'
              'end tell')
    subprocess.run(["osascript", "-e", script], capture_output=True, timeout=15)

def main():
    import psycopg2
    alerts = []

    # bot processes
    for pat, name in (("watchdog_loop.sh", "watchdog"), ("polymarket_bot.py", "copy-bot")):
        r = subprocess.run(["pgrep", "-f", pat], capture_output=True)
        if r.returncode != 0:
            alerts.append(f"🚨 {name} DOWN")

    # db + leg counts
    try:
        con = psycopg2.connect(dbname="aryanagarwal")
        cur = con.cursor()
        cur.execute("SELECT leg, status, COUNT(*) FROM lab_trades "
                    "WHERE leg IN ('candlesig','coinrev') GROUP BY leg, status")
        counts = {f"{r[0]}_{r[1]}": r[2] for r in cur.fetchall()}
        con.close()
    except Exception as e:
        alerts.append(f"🚨 PostgreSQL unreachable: {e}")
        counts = None

    prev = {}
    if os.path.exists(STATE_FILE):
        try: prev = json.load(open(STATE_FILE))
        except Exception: pass

    if counts is not None:
        for k, v in counts.items():
            if v != prev.get(k, 0):
                leg, status = k.rsplit("_", 1)
                alerts.append(f"📊 {leg}: {status} count {prev.get(k,0)} → {v}")
        json.dump(counts, open(STATE_FILE, "w"))

    if alerts:
        imsg("CANDLE WATCH\n" + "\n".join(alerts))
        print("\n".join(alerts))
    else:
        print("ok")

if __name__ == "__main__":
    main()
