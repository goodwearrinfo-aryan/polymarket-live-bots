#!/usr/bin/env python3
"""
pmterm.py — Polymarket "Bloomberg terminal": one consolidated live screen.
Read-only. Fuses lab state + a market's order flow + liquidity walls + insider
flow into a single panel. Refreshes in place.

Panels:
  HEADER  — P&L since reset, nearres gate, controls, watchdog health (lab_api)
  TAPE    — most recent insider-watch trades (scored)
  FLOW    — order-flow summary for the focus market (footprint POC + delta)
  BOOK    — liquidity walls around mid (CLOB depth)

Run: python3 pmterm.py [conditionId]   (no arg → top-24h market)
     loops every ~20s; Ctrl-C to quit.  --once for a single frame.
"""
import json, os, sys, time, urllib.request
from collections import defaultdict

UA = {"User-Agent": "Mozilla/5.0"}
BASE = os.path.expanduser("~/Documents/polymarket")
G = "\033[32m"; R = "\033[31m"; Y = "\033[33m"; C = "\033[36m"; B = "\033[1m"; X = "\033[0m"


def get(u, t=10):
    return json.loads(urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=t).read())


def lab(path):
    try:
        return get(f"http://127.0.0.1:8787/v1/{path}", 4)
    except Exception:
        return None


def gate_from_state():
    try:
        s = json.load(open(os.path.join(BASE, "scalp_lab_state.json")))
        n = len([t for t in s["nearres"]["closed"]
                 if t.get("reason") in ("target", "stop", "time", "resolve", "resolved")])
        return n
    except Exception:
        return None


def header():
    st = lab("status") or {}
    print(f"{B}{C}╔═ POLYMARKET TERMINAL ═══════════════════════════ {time.strftime('%H:%M:%S')} ═╗{X}")
    pnl = st.get("signal_pnl_since_reset")
    if pnl is None:
        pnl = 0.0
    ctrl = st.get("controls_lifetime_pnl", 0) or 0
    col = G if pnl >= 0 else R
    ch = G if st.get("controls_honest") else R
    print(f"  signal P&L {col}{pnl:+.2f}{X}   controls {ch}{ctrl:+.2f}{X}"
          f" {'✓' if st.get('controls_honest') else '⚠ BROKEN'}   open {st.get('open_positions','?')}")
    g = st.get("nearres_gate", {})
    n = g.get("exits", gate_from_state() or 0); need = g.get("needed", 30)
    gc = G if n >= need else Y
    wr = g.get("win_rate_pct")
    print(f"  nearres gate {gc}{n}/{need}{X}"
          f"{f'  WR {wr:.0f}%' if wr else ''}   "
          f"{'🎓 GRADUATED' if n >= need else 'accumulating'}")
    age = st.get("mirror_age_sec")
    alive = age is not None and age < 180
    print(f"  watchdog {G+'UP' if alive else R+'STALE'}{X}   mirror {age}s")


def tape(n=6):
    try:
        seen = json.load(open(os.path.join(BASE, "insider_seen.json")))
    except Exception:
        seen = {}
    print(f"\n{B}{Y}── TAPE · recent insider trades ──{X}")
    # pull the freshest few from the most-active watched wallets
    try:
        wl = json.load(open(os.path.join(BASE, "insider_wallets.json")))
    except Exception:
        wl = {}
    rows = []
    for addr, label in list(wl.items())[:8]:
        try:
            for t in get(f"https://data-api.polymarket.com/trades?user={addr}&limit=2", 6):
                rows.append((int(t.get("timestamp", 0)), addr, label, t))
        except Exception:
            pass
    for ts, addr, label, t in sorted(rows, key=lambda r: r[0], reverse=True)[:n]:
        side = t.get("side", "?"); usd = float(t.get("price", 0)) * float(t.get("size", 0))
        ic = G if side == "BUY" else R
        print(f"  {time.strftime('%H:%M', time.localtime(ts))} {ic}{side:4}{X} "
              f"${usd:>7,.0f} {t.get('outcome','')[:4]:4} {(t.get('title') or '')[:38]}")


def flow_and_book(cid, q):
    tr = get(f"https://data-api.polymarket.com/trades?market={cid}&limit=500", 12)
    if tr:
        ass = defaultdict(float)
        for t in tr: ass[t.get("asset")] += float(t.get("size") or 0)
        main = max(ass, key=ass.get)
        buy = sum(float(t["size"]) for t in tr if t.get("asset") == main and t.get("side") == "BUY")
        sell = sum(float(t["size"]) for t in tr if t.get("asset") == main and t.get("side") == "SELL")
        foot = defaultdict(float)
        for t in tr:
            if t.get("asset") == main: foot[round(float(t["price"]), 2)] += float(t["size"])
        poc = max(foot, key=foot.get) if foot else 0
        tot = buy + sell
        dcol = G if buy >= sell else R
        print(f"\n{B}{C}── FLOW · {q[:42]} ──{X}")
        print(f"  POC {poc:.2f}   vol {tot:,.0f}   delta {dcol}{buy-sell:+,.0f}{X} "
              f"({100*buy/tot:.0f}% buy)" if tot else "  no flow")
    # book walls
    try:
        m = get(f"https://gamma-api.polymarket.com/markets?condition_ids={cid}")[0]
        tok = json.loads(m.get("clobTokenIds", "[]"))[0]
        bk = get(f"https://clob.polymarket.com/book?token_id={tok}")
        bids = [(float(x["price"]), float(x["size"])) for x in bk.get("bids", [])]
        asks = [(float(x["price"]), float(x["size"])) for x in bk.get("asks", [])]
        bb = max((p for p, _ in bids), default=0); ba = min((p for p, _ in asks), default=1)
        mid = (bb + ba) / 2
        near = [(p, s, "A") for p, s in asks if abs(p - mid) <= 0.1] + \
               [(p, s, "B") for p, s in bids if abs(p - mid) <= 0.1]
        near.sort(key=lambda x: -x[1])
        print(f"  spread {bb:.3f}/{ba:.3f}   biggest walls:")
        for p, s, side in near[:4]:
            print(f"    {(G if side=='B' else R)}{side}{X} {p:.3f}  {s:>9,.0f}")
    except Exception as e:
        print(f"  book unavailable ({e})")


def frame(cid, q):
    sys.stdout.write("\033[2J\033[H")
    header(); tape(); flow_and_book(cid, q)
    print(f"\n{B}{C}╚════════════════════════════════════════════════════════════╝{X}")


def main():
    once = "--once" in sys.argv
    cid = next((a for a in sys.argv[1:] if a.startswith("0x")), None)
    if cid:
        q = (get(f"https://gamma-api.polymarket.com/markets?condition_ids={cid}")[0].get("question") or "")[:45]
    else:
        m = get("https://gamma-api.polymarket.com/markets?closed=false&order=volume24hr&ascending=false&limit=1")[0]
        cid, q = m["conditionId"], (m.get("question") or "")[:45]
    while True:
        frame(cid, q)
        if once:
            break
        time.sleep(20)


if __name__ == "__main__":
    main()
