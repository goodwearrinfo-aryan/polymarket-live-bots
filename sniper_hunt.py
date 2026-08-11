#!/usr/bin/env python3
"""
sniper_hunt.py — Continuous hunt for extreme-WR wallets (the "machines").

Consumes the wallet pool that onchain_backfill.py keeps growing, judges every
wallet it hasn't judged yet (most-active first), and flags SNIPERs:
WR >= 90%, n >= 10 closed, positive P&L. Findings are iMessaged, appended to
sniper_wallets.json, and auto-added to the insider watchlist.

First confirmed catch (2026-06-13): 0x5da802d8… — 137W/0L, +$12.4k, uniform
+$147 on BTC Up/Down 10-min markets = both-sides book arbitrage.

Resumable: judged-set persists in sniper_judged.json.

Run:  python3 sniper_hunt.py            # loop forever (sleeps when caught up)
      python3 sniper_hunt.py --once     # one pass over current unjudged pool
"""

import json, os, subprocess, sys, time

BASE      = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
from wallet_scanner import flag_wallet
from onchain_wallet_backfill import load_state, EXCHANGES

JUDGED_F  = os.path.join(BASE, "sniper_judged.json")
OUT_F     = os.path.join(BASE, "sniper_wallets.json")
WALLETS_F = os.path.join(BASE, "insider_wallets.json")
TARGETS   = ["krisharyan@icloud.com", "+918449447444"]

WR_MIN, N_MIN = 90.0, 10
PACE = 0.45               # data-api politeness (parallel = silent rate-limit)

_OSA = ('on run argv\n'
        'tell application "Messages"\n'
        '  set s to 1st service whose service type = iMessage\n'
        '  set b to buddy (item 1 of argv) of s\n'
        '  send (item 2 of argv) to b\n'
        'end tell\n'
        'end run')

def imsg(body):
    for t in TARGETS:
        try:
            r = subprocess.run(["osascript", "-e", _OSA, t, body],
                               capture_output=True, text=True, timeout=30)
            if r.returncode != 0:
                sys.stderr.write(f"[imsg FAIL] {t}: {r.stderr.strip()}\n")
        except Exception as e:
            sys.stderr.write(f"[imsg FAIL] {t}: {e}\n")

def _load(path, default):
    try:
        return json.load(open(path))
    except Exception:
        return default

def _save(path, obj):
    tmp = path + ".tmp"
    json.dump(obj, open(tmp, "w"), indent=1)
    os.replace(tmp, path)

_last_bounce = 0.0

def _bounce_watcher(force=False):
    """Watcher reads the watchlist only at startup. Bounce at most every
    10 min so a streak of adds doesn't thrash the service (sniper = force)."""
    global _last_bounce
    if not force and time.time() - _last_bounce < 600:
        return
    _last_bounce = time.time()
    plist = os.path.expanduser("~/Library/LaunchAgents/com.aryan.insider-watch.plist")
    subprocess.run(["launchctl", "unload", plist], capture_output=True)
    subprocess.run(["launchctl", "load", plist], capture_output=True)

def add_to_watchlist(r, prefix="SNIPER", force_bounce=True):
    wallets = _load(WALLETS_F, {})
    if r["addr"] in wallets:
        return False
    wl = f"{r['wins']}W{r['closed']-r['wins']}L"
    wallets[r["addr"]] = f"{prefix}-{r['win_rate']:.0f}pct-{wl}"
    _save(WALLETS_F, wallets)
    _bounce_watcher(force=force_bounce)
    return True

# curation bar for non-sniper SHARP finds (same as 2026-06-12 manual curation)
SHARP_WR, SHARP_PNL = 55.0, 500.0

def hunt_pass(judged, snipers):
    pool = load_state()["wallets"]
    # onchain_wallet_backfill stores {addr: {"fills": n, "lo":.., "hi":..}};
    # earlier format was {addr: n}. Handle both → activity = fill count.
    def _fills(v):
        return v.get("fills", 0) if isinstance(v, dict) else v
    todo = sorted(((w, _fills(v)) for w, v in pool.items()
                   if w not in EXCHANGES and w not in judged),
                  key=lambda kv: -kv[1])
    if not todo:
        return 0
    print(f"[pass] {len(todo)} unjudged wallets (pool {len(pool)})", flush=True)
    found = 0
    for i, (w, _n) in enumerate(todo):
        r = flag_wallet(w, N_MIN)
        judged[w] = 1
        # SHARP (curated bar) → quiet auto-add; SNIPER (below) → loud add
        if r and r["win_rate"] < WR_MIN and r["win_rate"] >= SHARP_WR \
             and r["realized_pnl"] >= SHARP_PNL:
            if add_to_watchlist(r, prefix="AUTO", force_bounce=False):
                print(f"  [auto-add] {r['addr']} WR={r['win_rate']}% "
                      f"P&L=${r['realized_pnl']:,.0f}", flush=True)
        if r and r["win_rate"] >= WR_MIN and r["realized_pnl"] > 0 \
             and r["closed"] >= N_MIN:
            snipers.append(r)
            found += 1
            _save(OUT_F, snipers)
            fresh = add_to_watchlist(r)
            msg = (f"🎯 SNIPER FOUND\n{r['addr']}\n"
                   f"WR {r['win_rate']}% ({r['wins']}W/{r['closed']-r['wins']}L)  "
                   f"P&L ${r['realized_pnl']:,.0f}  avgbet ${r['avg_bet']:,.0f}"
                   + ("\nadded to watchlist" if fresh else "\nalready watched"))
            print(msg, flush=True)
            imsg(msg)
        if (i + 1) % 250 == 0:
            _save(JUDGED_F, judged)
            print(f"  {i+1}/{len(todo)} judged this pass, "
                  f"{found} new snipers, {len(snipers)} total", flush=True)
        time.sleep(PACE)
    _save(JUDGED_F, judged)
    return found

def main(loop):
    judged  = _load(JUDGED_F, {})
    snipers = _load(OUT_F, [])
    # only keep real snipers from any older file format
    snipers = [s for s in snipers
               if s.get("win_rate", 0) >= WR_MIN and s.get("closed", 0) >= N_MIN]
    while True:
        found = hunt_pass(judged, snipers)
        print(f"[{time.strftime('%H:%M:%S')}] pass complete — {found} new, "
              f"{len(judged)} judged lifetime, {len(snipers)} snipers", flush=True)
        if not loop:
            break
        time.sleep(600)   # let the backfill accumulate fresh wallets

if __name__ == "__main__":
    main(loop=("--once" not in sys.argv))
