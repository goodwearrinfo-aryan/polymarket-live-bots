#!/usr/bin/env python3
"""
sybil_watch.py — Coordinated-insider (multi-account) detector.

Thesis: insiders who split across fresh wallets can't hide the COORDINATION:
N low-history wallets buying the SAME side of the SAME market within a short
window, each with real size. Single-wallet WR scans miss this entirely.

Cluster rule (all must hold):
  - >= MIN_WALLETS distinct wallets on the same (market, outcome)
  - each trade >= MIN_USD and within WINDOW_MIN of each other
  - >= FRESH_RATIO of those wallets are "fresh" (< FRESH_POS lifetime positions)

Polls data-api global trades every 120s, iMessages confirmed clusters.
Read-only, paper project. State in sybil_seen.json (alerted clusters).

Run:  python3 sybil_watch.py once     (single scan — cron/launchd friendly)
      python3 sybil_watch.py          (loop)
"""

import json, os, subprocess, sys, time, urllib.parse, urllib.request

BASE        = os.path.dirname(os.path.abspath(__file__))
SEEN_F      = os.path.join(BASE, "sybil_seen.json")
TARGETS     = ["krisharyan@icloud.com", "+918449447444"]
UA          = "Mozilla/5.0"

MIN_WALLETS = 3        # distinct wallets on same market+side
MIN_USD     = 500      # per-trade floor — insiders size up
WINDOW_MIN  = 15       # coordination window
FRESH_POS   = 8        # "fresh" = fewer lifetime positions than this
FRESH_RATIO = 0.6      # share of cluster wallets that must be fresh
POLL_SEC    = 120

_pos_cache = {}        # wallet → lifetime position count (cached per run)


def _get(path, params=None):
    url = f"https://data-api.polymarket.com/{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def lifetime_positions(wallet):
    if wallet in _pos_cache:
        return _pos_cache[wallet]
    try:
        n = len(_get("positions", {"user": wallet, "limit": FRESH_POS + 1,
                                   "sizeThreshold": 0}))
    except Exception:
        n = FRESH_POS + 1          # unknown → treat as NOT fresh (fail-safe)
    _pos_cache[wallet] = n
    return n


_OSA = ('on run argv\n'
        'tell application "Messages"\n'
        '  set s to 1st service whose service type = iMessage\n'
        '  set b to buddy (item 1 of argv) of s\n'
        '  send (item 2 of argv) to b\n'
        'end tell\n'
        'end run')

def send_imessage(body):
    for target in TARGETS:
        try:
            r = subprocess.run(["osascript", "-e", _OSA, target, body],
                               capture_output=True, text=True, timeout=30)
            if r.returncode != 0:
                sys.stderr.write(f"[imsg FAIL] {target}: {r.stderr.strip()}\n")
        except Exception as e:
            sys.stderr.write(f"[imsg FAIL] {target}: {e}\n")


def load_seen():
    try:
        return set(json.load(open(SEEN_F)))
    except Exception:
        return set()

def save_seen(seen):
    json.dump(sorted(seen), open(SEEN_F, "w"))


def scan(seen):
    trades = _get("trades", {"limit": 500})
    now = time.time()
    # bucket: (conditionId, outcome) → [(wallet, usd, ts, title)]
    buckets = {}
    for t in trades:
        try:
            ts = int(t.get("timestamp", 0))
            if now - ts > WINDOW_MIN * 60:
                continue
            usd = float(t.get("size", 0)) * float(t.get("price", 0))
            if usd < MIN_USD:
                continue
            if t.get("side") != "BUY":
                continue
            key = (str(t.get("conditionId")), str(t.get("outcome")))
            buckets.setdefault(key, []).append(
                (t.get("proxyWallet", "").lower(), usd, ts, t.get("title", "")))
        except Exception:
            continue

    alerts = []
    for (cid, outcome), rows in buckets.items():
        wallets = {}
        for w, usd, ts, title in rows:
            if w:
                wallets.setdefault(w, 0)
                wallets[w] += usd
        if len(wallets) < MIN_WALLETS:
            continue
        ck = f"{cid}:{outcome}"
        if ck in seen:
            continue
        fresh = [w for w in wallets if lifetime_positions(w) <= FRESH_POS]
        if len(fresh) / len(wallets) < FRESH_RATIO:
            continue
        seen.add(ck)
        total = sum(wallets.values())
        title = rows[0][3][:60]
        lines = [f"🚨 SYBIL CLUSTER — {len(wallets)} wallets, "
                 f"{len(fresh)} fresh, ${total:,.0f} total",
                 f"BUY {outcome} — {title}"]
        for w, usd in sorted(wallets.items(), key=lambda kv: -kv[1])[:5]:
            tag = "FRESH" if w in fresh else "known"
            lines.append(f"  {w[:10]}...{w[-4:]} ${usd:,.0f} ({tag})")
        alerts.append("\n".join(lines))
        print(f"[CLUSTER] {lines[0]} | {title}", flush=True)

    if alerts:
        send_imessage("\n\n".join(alerts))
    return len(alerts)


def main(loop):
    seen = load_seen()
    while True:
        try:
            n = scan(seen)
            save_seen(seen)
            print(f"[{time.strftime('%H:%M:%S')}] scan done — {n} new clusters",
                  flush=True)
        except Exception as e:
            sys.stderr.write(f"[scan error] {e}\n")
        if not loop:
            break
        _pos_cache.clear()
        time.sleep(POLL_SEC)


if __name__ == "__main__":
    main(loop=(len(sys.argv) < 2 or sys.argv[1] != "once"))
