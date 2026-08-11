#!/usr/bin/env python3
"""liquidity_scanner_agent.py — CLOB sweep for informed-positioning signals.

Scans the order book across live Polymarket markets looking for structural
liquidity signals that precede price moves:

  THIN_BOOK  — spread >8¢ on a high-volume market (event risk loading, book pulled)
  BID_HEAVY  — bid depth 3x ask depth (informed buying, YES should rise)
  ASK_HEAVY  — ask depth 3x bid depth (informed selling, YES should fall)
  SPREAD_ARB — best-bid + best-ask < 1.00 (structural arb opportunity)

Claude's tool-use loop:
  scan_orderbooks(topic) → list_markets → fetch_market for context
  Synthesizes: which imbalances are informative vs routine noise?

Output: liquidity_signals.json  +  iMessage on STRONG signals

Usage:
  python3 liquidity_scanner_agent.py           # scan across all major topic areas
  python3 liquidity_scanner_agent.py --topic X # scan a specific topic
  python3 liquidity_scanner_agent.py --once    # quiet watchdog mode
  python3 liquidity_scanner_agent.py --show    # show past signals
"""
import os, json, time, argparse
from pathlib import Path
from agent_toolkit import run_agent, TOOL_DEFS

HERE    = Path(__file__).resolve().parent
SIGNALS = HERE / "liquidity_signals.json"

SYSTEM = """You are a liquidity microstructure analyst for prediction markets. You read
order book depth and spread data to detect INFORMED POSITIONING — situations where
smart money is loading one side before the price moves.

Your tools:
- scan_orderbooks(topic, max_markets): CLOB sweep — spread, bid depth, ask depth, imbalance flags
- list_markets(topic): find markets to scan
- fetch_market(query): get price + resolution criteria for context
- check_open_positions(): avoid flagging what's already in the book

SIGNAL TYPES you are looking for:
1. THIN_BOOK + high volume market: book has been pulled = event risk, someone knows something
2. BID_HEAVY (imbalance > 3x): informed buyers are loading YES quietly
3. ASK_HEAVY (imbalance < 0.33x): informed sellers are loading NO quietly
4. SPREAD_ARB: best_bid + best_ask < 0.995 across related markets (structural)
5. SIZE_CLUSTER: unusually large orders at a specific price level (a wall = signal)

SCAN TOPICS (always scan ALL of these):
- bitcoin crypto ethereum
- geopolitics war iran israel
- elections president fed rate
- sports championship winner
- AI regulation technology

For each flagged market: use fetch_market to read resolution criteria and check if the
imbalance makes sense given what's at stake. Ignore routine thin books on tiny-volume markets.

SIGNAL FORMAT:
---LIQ SIGNAL---
TYPE: THIN_BOOK | BID_HEAVY | ASK_HEAVY | SPREAD_ARB
STRENGTH: STRONG | MODERATE | WEAK
MARKET: <question>
SLUG: <slug>
YES_PRICE: <current>
SPREAD: <spread in cents>
BID_DEPTH: <$>
ASK_DEPTH: <$>
IMBALANCE: <ratio>
INTERPRETATION: <1 sentence — what this book shape implies about informed positioning>
ACTION: WATCH | ENTER_WITH_NEWS | PASS
---END---"""

SCAN_TOPICS = [
    "bitcoin crypto", "ethereum", "geopolitics war",
    "iran israel conflict", "election president",
    "federal reserve rate", "AI artificial intelligence",
    "sports championship",
]


def scan(topic: str = None, verbose: bool = True) -> list:
    topics = [topic] if topic else SCAN_TOPICS
    topic_str = ", ".join(topics[:4]) + (f" + {len(topics)-4} more" if len(topics) > 4 else "")
    user = (f"Scan order books across these topics: {topic_str}\n\n"
            f"For each topic: use scan_orderbooks() to get the CLOB data, then identify "
            f"markets with anomalous spread/imbalance. Use fetch_market() on the top 2-3 "
            f"flagged markets to understand what's at stake. "
            f"Only produce ---LIQ SIGNAL--- blocks for STRONG or MODERATE signals "
            f"(not routine wide spreads on tiny markets). Check open positions to skip duplicates.")
    if verbose:
        print(f"[liquidity_scanner] scanning {len(topics)} topic(s)...")
    response = run_agent(SYSTEM, user, verbose=verbose, max_tokens=1400, max_iters=16)

    signals = []
    blocks = response.split("---LIQ SIGNAL---")[1:]
    for block in blocks:
        body = block.split("---END---")[0].strip()
        s = {"ts": time.strftime("%Y-%m-%d %H:%MZ", time.gmtime()),
             "type": None, "strength": None, "market": None, "slug": None,
             "yes_price": None, "spread": None, "bid_depth": None, "ask_depth": None,
             "imbalance": None, "interpretation": None, "action": None}
        for line in body.splitlines():
            line = line.strip()
            for field, key in [("TYPE:", "type"), ("STRENGTH:", "strength"),
                                ("MARKET:", "market"), ("SLUG:", "slug"),
                                ("YES_PRICE:", "yes_price"), ("SPREAD:", "spread"),
                                ("BID_DEPTH:", "bid_depth"), ("ASK_DEPTH:", "ask_depth"),
                                ("IMBALANCE:", "imbalance"),
                                ("INTERPRETATION:", "interpretation"),
                                ("ACTION:", "action")]:
                if line.startswith(field):
                    s[key] = line[len(field):].strip()
        if s["market"] and s["type"]:
            signals.append(s)
    return signals


def _load():
    if SIGNALS.exists():
        try: return json.loads(SIGNALS.read_text())
        except Exception: pass
    return {"signals": []}


def _save(st):
    tmp = SIGNALS.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=2))
    tmp.replace(SIGNALS)


def _imessage(msg, recipient="+918449447444"):
    safe = msg.replace("\\","\\\\").replace('"','\\"').replace("\n","\\n")
    safe = "".join(c if ord(c) < 128 else " " for c in safe)
    os.system(f'osascript -e \'tell application "Messages" to send "{safe}" to buddy "{recipient}" of (service 1 whose service type is iMessage)\' 2>/dev/null')


def run(topic: str = None, verbose: bool = True):
    signals = scan(topic=topic, verbose=verbose)
    st = _load()
    st["signals"].extend(signals)
    st["last_scan"] = time.strftime("%Y-%m-%d %H:%MZ", time.gmtime())
    _save(st)

    strong = [s for s in signals if s.get("strength") == "STRONG"]
    if verbose:
        print(f"\n[liquidity_scanner] {len(signals)} signal(s), {len(strong)} STRONG → {SIGNALS}")
        for s in signals:
            print(f"  {s.get('strength','?'):8} {s.get('type','?'):12} "
                  f"spread={s.get('spread','?')} imbal={s.get('imbalance','?')} "
                  f"'{s.get('market','?')[:48]}'")

    if strong:
        lines = [f"[liquidity] {len(strong)} STRONG signal(s):"]
        for s in strong[:3]:
            lines.append(f"{s['type']} {s.get('market','?')[:45]} — {s.get('interpretation','?')[:60]}")
        _imessage("\n".join(lines))


def show():
    st = _load()
    sigs = st.get("signals", [])
    print(f"\n  Liquidity signals: {len(sigs)}\n")
    for s in sigs[-12:]:
        print(f"  [{s.get('ts','')}] {s.get('strength','?'):8} {s.get('type','?'):12} "
              f"spread={s.get('spread','?')} im={s.get('imbalance','?')}  "
              f"'{s.get('market','?')[:45]}'")
        if s.get("interpretation"):
            print(f"     {s['interpretation'][:80]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--topic", metavar="T", help="specific topic to scan (default: all)")
    ap.add_argument("--once", action="store_true", help="quiet watchdog mode")
    ap.add_argument("--show", action="store_true")
    a = ap.parse_args()
    if a.show: show(); return
    run(topic=a.topic, verbose=not a.once)


if __name__ == "__main__":
    main()
