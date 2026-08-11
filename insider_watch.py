#!/usr/bin/env python3
"""
insider_watch.py — Watch SHARP wallets and alert on every new trade via iMessage.

Polls data-api.polymarket.com/trades for each tracked wallet every 60s.
New trades (unseen txHash) fire an iMessage alert immediately.

Usage:
  python3 insider_watch.py            # run watcher loop
  python3 insider_watch.py --add 0x...  # add a wallet to watchlist
  python3 insider_watch.py --list       # show watchlist
  python3 insider_watch.py --remove 0x... # remove a wallet

Watchlist persists in ~/Documents/polymarket/insider_wallets.json
Seen-trades cache in ~/Documents/polymarket/insider_seen.json
"""

import json, os, sys, time, subprocess, urllib.request, urllib.parse, argparse

BASE        = os.path.expanduser("~/Documents/polymarket")
WALLETS_F   = os.path.join(BASE, "insider_wallets.json")
SEEN_F      = os.path.join(BASE, "insider_seen.json")
TARGETS     = ["krisharyan@icloud.com", "+918449447444"]
POLL_SEC    = 60
UA          = "Mozilla/5.0"

# Per-wallet alert floor (USD). High-frequency whales (~100+ trades/day) would
# flood iMessage; only their conviction-size trades are worth a ping.
# WHALE_FLOOR applies to the 20 on-chain-harvested $100k+ event whales added
# 2026-06-13 — their typical bets clear $5k, so this only kills micro-noise.
WHALE_FLOOR = 5000
_WHALES_2026_06_13 = [
    "0x9703676286b93c2eca71ca96e8757104519a69c2", "0x2a2c53bd278c04da9962fcf96490e17f3dfb9bc1",
    "0xe40ea00e74059c76c0035c919ef6b99c3e25a94d", "0xa3ad70cf48a2f2b44163e798223c01a2c2536866",
    "0x16cbe223607a6513ae76d1e3751c78e4eabc2704", "0xe83cc0c82d009a1c349c1a89feaadfcee9655e8f",
    "0xb687f00464e33934f5d591f224e71c3559ecaee5", "0x9106cf79b02f85d8f1e546802ed916648f44afcb",
    "0x2c335066fe58fe9237c3d3dc7b275c2a034a0563", "0xdd572169e741c72227589c5cb56b9ddd638d694d",
    "0x9d84ce0306f8551e02efef1680475fc0f1dc1344", "0x96489abcb9f583d6835c8ef95ffc923d05a86825",
    "0x27abdfc9393c72a6330a3be987da4b46c726e521", "0x4e25605fd905e9972efc0f4d8814530c655cd7a7",
    "0xa022ba0a68e11a78348382ff168601012d4d77f8", "0xde7be6d489bce070a959e0cb813128ae659b5f4b",
    "0xf5198df69e13937a40d1c76d6f72d9aa067d906b", "0xd1acd3925d895de9aec98ff95f3a30c5279d08d5",
    "0x54525ee78bd513b0bf75f94e560158f6fc35d448", "0x52ecea7b3159f09db589e4f4ee64872fd0bba6f3",
    "0x6e1d5040d0ac73709b0621f620d2a60b80d2d0fa", "0xcb3143ee858e14d0b3fe40ffeaea78416e646b02",  # V1-discovered 2026-06-13
    # 6 ACTIVE deep-V1 whales (last trade <7d), added 2026-06-13:
    "0x594edb9112f526fa6a80b8f858a6379c8a2c1c11", "0x7fea691e28d169a33c500607279f2acb49058f74",
    "0xb6bed94e75c333dae24eb9c80b3fef47ef3cfcfe", "0xc02147dee42356b7a4edbb1c35ac4ffa95f61fa8",
    "0xfc2f4f50ce2f6045d35558a5e2d8d4b2ac6610c7", "0x8575467a6743c81ef0a8d06acc76e99b454a87fe",
]
ALERT_MIN_USD = {
    "0xbd0477e08d82d35a855ff19644a88a9213d8fbb0": 5000,   # iran whale, ~110 trades/day
}
ALERT_MIN_USD.update({a: WHALE_FLOOR for a in _WHALES_2026_06_13})

# Noise control (watchlist grew to 100 wallets, ~30 bots + many untagged).
# Beyond the per-wallet USD floors above, only ping when the blended
# insider_score clears SCORE_FLOOR (NOTABLE tier+), OR the trade is large
# enough that size alone is worth seeing regardless of score.
SCORE_FLOOR = 40      # insider_score tiers: STRONG>=65, NOTABLE>=40, noise<40
ALWAYS_USD  = 1000    # any trade >= $1k pings even if it scores low

# Seed with the 4 SHARP wallets found in the scan
DEFAULT_WALLETS = {
    "0x84cfffc3f16dcc353094de30d4a45226eccd2f63": "SHARP-80pct-$112k",
    "0xee55214ee3a9ee22a404663c76ca832577df7b04": "SHARP-62pct-$23k",
    "0xa2fc0448a1dce9258ebb91b2f3abcddb98a0b1b4": "SHARP-46pct-$1.4k",
    "0x493cef47ca12db5c08fb55747d6cd279ebdb953e": "SHARP-57pct-$199",
}

def load_wallets():
    if os.path.exists(WALLETS_F):
        try:
            return json.load(open(WALLETS_F))
        except (json.JSONDecodeError, ValueError, OSError):
            pass   # corrupt watchlist -> fall back to defaults (fail-soft, not crash)
    json.dump(DEFAULT_WALLETS, open(WALLETS_F, "w"), indent=2)
    return DEFAULT_WALLETS

def save_wallets(w):
    json.dump(w, open(WALLETS_F, "w"), indent=2)

def load_seen():
    if os.path.exists(SEEN_F):
        try:
            return json.load(open(SEEN_F))
        except (json.JSONDecodeError, ValueError, OSError):
            pass   # corrupt/empty state file -> start clean (fail-soft, not crash)
    return {}

def save_seen(s):
    json.dump(s, open(SEEN_F, "w"), indent=2)

def fetch_trades(addr):
    url = f"https://data-api.polymarket.com/trades?user={addr}&limit=50"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=12) as r:
            return json.loads(r.read())
    except Exception:
        return []   # network/timeout/bad-JSON -> no trades this cycle (fail-soft, not crash)

_OSA = ('on run argv\n'
        'tell application "Messages"\n'
        '  set s to 1st service whose service type = iMessage\n'
        '  set b to buddy (item 1 of argv) of s\n'
        '  send (item 2 of argv) to b\n'
        'end tell\n'
        'end run')

def send_imessage(body):
    """Body passed via argv — quotes AND newlines in market titles can't break
    the AppleScript (a literal newline inside a quoted AS string is a syntax
    error, which is why multi-alert sends were dying silently). Synchronous,
    stderr captured: failures are LOUD in the log."""
    ok = True
    for target in TARGETS:
        try:
            r = subprocess.run(["osascript", "-e", _OSA, target, body],
                               capture_output=True, text=True, timeout=30)
            if r.returncode != 0:
                ok = False
                sys.stderr.write(f"[imsg FAIL] {target}: {r.stderr.strip()}\n")
        except Exception as e:
            ok = False
            sys.stderr.write(f"[imsg FAIL] {target}: {e}\n")
    return ok

def format_alert(addr, label, trade, scored=None):
    side    = trade.get("side", "?")
    outcome = trade.get("outcome", "")
    price   = float(trade.get("price", 0))
    size    = float(trade.get("size", 0))
    usd     = round(price * size, 2)
    title   = trade.get("title", "")          # full title — no truncation
    slug    = trade.get("eventSlug") or trade.get("slug") or ""
    short   = f"{addr[:8]}...{addr[-5:]}"
    ts      = time.strftime("%H:%M", time.localtime(int(trade.get("timestamp", 0))))
    icon    = "🟩" if side == "BUY" else "🟥"
    lines = [f"{icon} [{ts}] {short} ({label})",
             f"{side} {outcome} @ {price:.2f}  ${usd:,.0f} ({size:,.0f} shares)",
             f"{title}"]
    # score every trade: wallet quality + size conviction + entry band +
    # category fit + news heat (insider_score.py; fails open).
    # scored may be passed in pre-computed (loop gates on it) to avoid double work.
    if scored is None:
        try:
            import insider_score as _isc
            scored = _isc.score_trade(addr, label, trade)
        except Exception:
            scored = None
    if scored:
        tier, sc, tags = scored
        lines.append(f"{tier} {sc}/100 — " + "; ".join(tags[:3]))
    if slug:
        lines.append(f"polymarket.com/event/{slug}")
    return "\n".join(lines)

def watch():
    wallets = load_wallets()
    seen    = load_seen()
    print(f"Watching {len(wallets)} insider wallets. Poll every {POLL_SEC}s. Ctrl-C to stop.")
    print(f"Wallets: {list(wallets.keys())}\n")

    while True:
        wallets = load_wallets()   # reload: insider_finder auto-adds candidates
        alerts = []
        for addr, label in wallets.items():
            try:
                trades = fetch_trades(addr)
                first_run = addr not in seen          # seed silently — no alerts
                addr_seen = seen.get(addr, set())
                if isinstance(addr_seen, list):
                    addr_seen = set(addr_seen)
                new = [t for t in trades if t.get("transactionHash") not in addr_seen]
                new.sort(key=lambda t: int(t.get("timestamp", 0)))   # chronological order
                for t in new:
                    tx = t.get("transactionHash", "")
                    if tx:
                        addr_seen.add(tx)
                    usd = float(t.get("price", 0)) * float(t.get("size", 0))
                    if first_run or usd < ALERT_MIN_USD.get(addr, 0):
                        continue
                    # noise gate: score the trade, ping only if it clears the
                    # floor OR is big enough to matter on size alone
                    scored = None
                    try:
                        import insider_score as _isc
                        scored = _isc.score_trade(addr, label, t)
                    except Exception:
                        pass
                    sc = scored[1] if scored else 0
                    if sc < SCORE_FLOOR and usd < ALWAYS_USD:
                        continue
                    msg = format_alert(addr, label, t, scored=scored)
                    alerts.append(msg)
                    print(f"[NEW] {msg}\n")
                seen[addr] = list(addr_seen)
                if first_run:
                    print(f"[seed] {addr[:10]}... seeded {len(new)} historical trades silently")
            except Exception as e:
                sys.stderr.write(f"[{addr[:8]}] fetch error: {e}\n")

        save_seen(seen)

        if alerts:
            header = f"🔍 INSIDER TRADES ({len(alerts)})"
            body = header + "\n\n" + "\n\n".join(alerts)   # all trades, in order
            if send_imessage(body):
                print(f"[iMessage] sent {len(alerts)} alert(s)")
            else:
                print(f"[iMessage] FAILED for {len(alerts)} alert(s) — see stderr")

        ts = time.strftime("%H:%M:%S")
        print(f"[{ts}] polled {len(wallets)} wallets — {len(alerts)} new trades", flush=True)
        time.sleep(POLL_SEC)

def cmd_list():
    wallets = load_wallets()
    print(f"\n{'='*60}")
    print(f"  {'ADDRESS':<44} LABEL")
    print(f"  {'-'*56}")
    for addr, label in wallets.items():
        print(f"  {addr}  {label}")
    print(f"{'='*60}\n")

def cmd_add(addr, label="MANUAL"):
    addr = addr.lower()
    wallets = load_wallets()
    wallets[addr] = label
    save_wallets(wallets)
    print(f"Added {addr} ({label})")

def cmd_remove(addr):
    addr = addr.lower()
    wallets = load_wallets()
    if addr in wallets:
        del wallets[addr]
        save_wallets(wallets)
        print(f"Removed {addr}")
    else:
        print(f"Not found: {addr}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Watch SHARP Polymarket wallets")
    parser.add_argument("--add",    metavar="ADDR", help="Add wallet to watchlist")
    parser.add_argument("--label",  default="MANUAL", help="Label for --add")
    parser.add_argument("--remove", metavar="ADDR", help="Remove wallet from watchlist")
    parser.add_argument("--list",   action="store_true", help="Show watchlist")
    args = parser.parse_args()

    if args.list:
        cmd_list()
    elif args.add:
        cmd_add(args.add, args.label)
    elif args.remove:
        cmd_remove(args.remove)
    else:
        watch()
