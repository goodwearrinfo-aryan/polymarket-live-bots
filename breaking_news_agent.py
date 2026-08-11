#!/usr/bin/env python3
"""breaking_news_agent.py — real-time news-to-market impact agent.

Scans the hermes feed (52 sources, ~800 items) for major breaking news every hour,
finds open Polymarket markets that would be materially affected, and flags urgent
opportunities BEFORE the market re-prices.

Claude's tool-use loop:
  1. search_news for recent high-impact headlines
  2. list_markets to find markets on the affected topic
  3. fetch_market to get current price + resolution criteria
  4. check_open_positions to avoid double-counting what's already in the book
  Synthesizes into SIGNAL blocks: urgency + implied move + recommended action

Output: breaking_news_signals.json  +  iMessage on HIGH/CRITICAL urgency

Usage:
  python3 breaking_news_agent.py         # scan now
  python3 breaking_news_agent.py --once  # quiet watchdog mode
  python3 breaking_news_agent.py --show  # show past signals
"""
import os, json, time, argparse
from pathlib import Path
from agent_toolkit import run_agent, TOOL_DEFS

HERE    = Path(__file__).resolve().parent
SIGNALS = HERE / "breaking_news_signals.json"

SYSTEM = """You are a breaking-news prediction-market analyst. Your job is to find breaking
news that hasn't yet been priced into open Polymarket markets — and flag the ones that should move.

You are NOT looking for old news the market already knows. You want stories from the LAST FEW HOURS
that are materially relevant to a live market's resolution criteria.

Your tools:
- search_news(query): scan the hermes live feed (52 sources, updated continuously)
- list_markets(topic): find open markets matching a topic
- fetch_market(query): get current YES price + resolution criteria
- check_open_positions(): see what's already in the analyst book

SCAN PROTOCOL:
1. search_news for several high-impact categories: geopolitics, central bank, crypto regulation,
   elections, economic data releases, war/conflict, corporate earnings, tech regulation
2. For each major breaking story: list_markets to find affected open markets
3. fetch_market for each candidate to check: current price vs what it SHOULD be given the news
4. Skip anything already in open positions (check_open_positions)
5. Flag only genuine gaps — where news directly bears on resolution criteria AND market hasn't moved

SIGNAL FORMAT (one per actionable opportunity):
---SIGNAL---
URGENCY: CRITICAL | HIGH | MEDIUM
SIDE: YES or NO
YES_PRICE_NOW: <current>
IMPLIED_MOVE: <how many pts the market should move, e.g. +15>
NEWS_HEADLINE: <the specific headline driving this>
NEWS_SOURCE: <where it appeared>
MARKET: <question>
SLUG: <slug>
WHY_NOT_PRICED: <1 sentence — why the market hasn't updated yet>
ACTION: ENTER_NOW | MONITOR | PASS
---END---"""


def scan(verbose: bool = True) -> list:
    user = ("Scan the hermes news feed for breaking news in the last few hours that "
            "materially affects open Polymarket markets. Focus on: geopolitics, "
            "central bank decisions, crypto/regulation, elections, war/conflict, "
            "economic data. For each story that has market implications: find the "
            "market, check the current price, determine if it's mispriced. "
            "Produce ---SIGNAL--- blocks only for genuine gaps (market not yet updated). "
            "Skip anything already in the analyst book. Urgency = CRITICAL if market "
            "should move >15pts, HIGH if 5-15pts, MEDIUM if 2-5pts.")
    if verbose:
        print("[breaking_news] scanning hermes feed...")
    response = run_agent(SYSTEM, user, verbose=verbose, max_tokens=1400, max_iters=14)

    signals = []
    blocks = response.split("---SIGNAL---")[1:]
    for block in blocks:
        body = block.split("---END---")[0].strip()
        s = {"ts": time.strftime("%Y-%m-%d %H:%MZ", time.gmtime()),
             "urgency": None, "side": None, "yes_price_now": None,
             "implied_move": None, "news_headline": None, "news_source": None,
             "market": None, "slug": None, "why_not_priced": None, "action": None}
        for line in body.splitlines():
            line = line.strip()
            for field, key in [("URGENCY:", "urgency"), ("SIDE:", "side"),
                                ("YES_PRICE_NOW:", "yes_price_now"),
                                ("IMPLIED_MOVE:", "implied_move"),
                                ("NEWS_HEADLINE:", "news_headline"),
                                ("NEWS_SOURCE:", "news_source"),
                                ("MARKET:", "market"), ("SLUG:", "slug"),
                                ("WHY_NOT_PRICED:", "why_not_priced"),
                                ("ACTION:", "action")]:
                if line.startswith(field):
                    s[key] = line[len(field):].strip()
        if s["market"] and s["urgency"]:
            signals.append(s)
    return signals


def _load():
    if SIGNALS.exists():
        try:
            return json.loads(SIGNALS.read_text())
        except Exception:
            pass
    return {"signals": []}


def _save(st):
    tmp = SIGNALS.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=2))
    tmp.replace(SIGNALS)


def _imessage(msg, recipient="+918449447444"):
    safe = msg.replace("\\","\\\\").replace('"','\\"').replace("\n","\\n")
    safe = "".join(c if ord(c) < 128 else " " for c in safe)
    os.system(f'osascript -e \'tell application "Messages" to send "{safe}" to buddy "{recipient}" of (service 1 whose service type is iMessage)\' 2>/dev/null')


def run(verbose: bool = True):
    signals = scan(verbose=verbose)
    st = _load()
    st["signals"].extend(signals)
    st["last_scan"] = time.strftime("%Y-%m-%d %H:%MZ", time.gmtime())
    _save(st)

    hot = [s for s in signals if s.get("urgency") in ("CRITICAL","HIGH")]
    if verbose:
        print(f"\n[breaking_news] {len(signals)} signal(s), {len(hot)} hot → {SIGNALS}")
        for s in signals:
            print(f"  {s.get('urgency','?'):8} {s.get('side','?')} {s.get('implied_move','?'):>5}pt "
                  f"'{s.get('market','?')[:50]}'")
            print(f"    news: {s.get('news_headline','?')[:70]}")

    if hot:
        lines = [f"[news_agent] {len(hot)} hot signal(s):"]
        for s in hot[:3]:
            lines.append(f"{s['urgency']} {s.get('side','?')} {s.get('implied_move','?')}pt "
                         f"'{s.get('market','?')[:40]}' — {s.get('news_headline','?')[:55]}")
        _imessage("\n".join(lines))


def show():
    st = _load()
    sigs = st.get("signals", [])
    print(f"\n  Breaking news signals: {len(sigs)}\n")
    for s in sigs[-12:]:
        print(f"  [{s.get('ts','')}] {s.get('urgency','?'):8} {s.get('action','?'):12} "
              f"{s.get('side','?'):3} {s.get('implied_move','?'):>5}pt  "
              f"{s.get('market','?')[:45]}")
        if s.get("news_headline"):
            print(f"     {s['news_headline'][:80]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="quiet watchdog mode")
    ap.add_argument("--show", action="store_true", help="show past signals")
    a = ap.parse_args()
    if a.show:
        show(); return
    run(verbose=not a.once)


if __name__ == "__main__":
    main()
