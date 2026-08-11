#!/usr/bin/env python3
"""confluence_agent.py — highest-confidence signals: news-accel + CLOB-imbalance agree.

A single signal from news or liquidity is noisy. When BOTH agree — news is accelerating
AND the order book shows informed positioning in the SAME direction — that's a confluence
signal with the highest real-world hit rate.

Claude's tool-use loop:
  1. news_velocity → find ACCELERATING topics
  2. scan_orderbooks → find BID_HEAVY / ASK_HEAVY on same topic
  3. fetch_market → verify resolution criteria + current price
  4. search_news → find the specific catalyst headline
  Synthesizes: is this double-signal genuine or do the indicators contradict?

Confluence definition:
  - news ACCELERATING (accel > 2x) + BID_HEAVY (informed buying) → YES underpriced
  - news ACCELERATING + ASK_HEAVY (informed selling) → NO underpriced
  - THIN_BOOK + accelerating news → event risk window, position ahead of reprice
  - Book NORMAL despite accelerating news → you are EARLIEST, best entry window

Output: confluence_signals.json  +  ALWAYS iMessage (these are the highest-quality signals)

Usage:
  python3 confluence_agent.py         # run full confluence scan
  python3 confluence_agent.py --once  # quiet watchdog mode
  python3 confluence_agent.py --show  # show past signals
"""
import os, json, time, argparse
from pathlib import Path
from agent_toolkit import run_agent, TOOL_DEFS

HERE    = Path(__file__).resolve().parent
SIGNALS = HERE / "confluence_signals.json"

SYSTEM = """You are a confluence signal analyst for prediction markets. You look for the
HIGHEST-CONFIDENCE opportunities: situations where BOTH news acceleration AND order book
imbalance point in the same direction simultaneously.

Your tools:
- news_velocity(query): measure news acceleration for a topic
- scan_orderbooks(topic): get CLOB imbalance + spread data across markets on that topic
- fetch_market(query): verify resolution criteria and current price
- search_news(query): find the specific catalyst headline driving the acceleration
- list_markets(topic): discover the relevant markets
- check_open_positions(): skip what's already in the book

CONFLUENCE PROTOCOL:
1. Run news_velocity() for the key topics (crypto, geopolitics, macro, elections)
2. For any ACCELERATING/ELEVATED topic: immediately run scan_orderbooks() on the same topic
3. Look for ALIGNMENT:
   a. News ACCELERATING + BID_HEAVY → YES underpriced (informed buyers loading before re-price)
   b. News ACCELERATING + ASK_HEAVY → NO underpriced (informed sellers loading before re-price)
   c. News ACCELERATING + THIN_BOOK → event risk window (book pulled = someone positioning off-CLOB)
   d. News ACCELERATING + NORMAL book → earliest entry, maximum edge window
4. For each confluence: fetch_market() to verify the market actually resolves on this news
5. search_news() to get the specific catalyst headline

ANTI-PATTERNS (do NOT flag):
- News QUIET but book imbalanced (routine maker activity, not information-driven)
- News ACCELERATING but book also thick/normal on a low-volume market (noise)
- Any market where you can't identify a clear resolution path from the news

CONFLUENCE SIGNAL FORMAT:
---CONFLUENCE---
QUALITY: TIER1 | TIER2 | TIER3
  (TIER1 = news ACCEL>2x + BID/ASK_HEAVY + resolution clear
   TIER2 = news ACCEL>1.5x + THIN_BOOK or book imbalanced
   TIER3 = news ELEVATED + any book signal)
NEWS_ACCEL: <multiplier>
BOOK_SIGNAL: BID_HEAVY | ASK_HEAVY | THIN_BOOK | NORMAL
ALIGNMENT: AGREE | CONTRADICT | NEUTRAL
MARKET: <question>
SLUG: <slug>
SIDE: YES or NO
YES_PRICE: <current>
ESTIMATED_FAIR: <your estimate given both signals>
EDGE_PTS: <estimated_fair - yes_price, signed, e.g. +0.14>
CATALYST: <specific headline or event driving news acceleration>
BOOK_DETAIL: <spread, bid_depth, ask_depth, imbalance>
ENTRY_WINDOW: <how long before this gets priced in, e.g. "1-4h">
ACTION: ENTER_NOW | ENTER_SOON | WATCH | PASS
---END---"""

SCAN_TOPICS = [
    "bitcoin ethereum crypto",
    "iran nuclear hormuz",
    "israel gaza palestine",
    "federal reserve inflation",
    "trump election republican",
    "china taiwan tech",
    "ukraine russia war",
    "oil energy OPEC",
]


def scan(verbose: bool = True) -> list:
    user = (f"Run the full confluence scan across these topics:\n"
            + "\n".join(f"- {t}" for t in SCAN_TOPICS)
            + "\n\nFor each topic: news_velocity() first, then scan_orderbooks() for the SAME topic. "
            f"Only proceed to fetch_market + search_news if BOTH signals are active. "
            f"Skip topics where news is QUIET or book is NORMAL with no news. "
            f"Produce ---CONFLUENCE--- blocks only for GENUINE double-signals. "
            f"Be specific about the catalyst and entry window.")
    if verbose:
        print(f"[confluence] running cross-signal scan across {len(SCAN_TOPICS)} topics...")
    response = run_agent(SYSTEM, user, verbose=verbose, max_tokens=1800, max_iters=20)

    signals = []
    blocks = response.split("---CONFLUENCE---")[1:]
    for block in blocks:
        body = block.split("---END---")[0].strip()
        s = {"ts": time.strftime("%Y-%m-%d %H:%MZ", time.gmtime()),
             "quality": None, "news_accel": None, "book_signal": None,
             "alignment": None, "market": None, "slug": None, "side": None,
             "yes_price": None, "estimated_fair": None, "edge_pts": None,
             "catalyst": None, "book_detail": None, "entry_window": None, "action": None}
        for line in body.splitlines():
            line = line.strip()
            # skip comment lines
            if line.startswith("(") or line.startswith("#"):
                continue
            for field, key in [("QUALITY:", "quality"), ("NEWS_ACCEL:", "news_accel"),
                                ("BOOK_SIGNAL:", "book_signal"), ("ALIGNMENT:", "alignment"),
                                ("MARKET:", "market"), ("SLUG:", "slug"), ("SIDE:", "side"),
                                ("YES_PRICE:", "yes_price"),
                                ("ESTIMATED_FAIR:", "estimated_fair"),
                                ("EDGE_PTS:", "edge_pts"), ("CATALYST:", "catalyst"),
                                ("BOOK_DETAIL:", "book_detail"),
                                ("ENTRY_WINDOW:", "entry_window"), ("ACTION:", "action")]:
                if line.startswith(field):
                    s[key] = line[len(field):].strip()
        if s["market"] and s["quality"]:
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


def run(verbose: bool = True):
    signals = scan(verbose=verbose)
    st = _load()
    st["signals"].extend(signals)
    st["last_scan"] = time.strftime("%Y-%m-%d %H:%MZ", time.gmtime())
    _save(st)

    tier1 = [s for s in signals if s.get("quality") == "TIER1"]
    enter = [s for s in signals if s.get("action") in ("ENTER_NOW","ENTER_SOON")]
    if verbose:
        print(f"\n[confluence] {len(signals)} signal(s), {len(tier1)} TIER1, "
              f"{len(enter)} ENTER → {SIGNALS}")
        for s in signals:
            print(f"  {s.get('quality','?'):6} {s.get('alignment','?'):10} "
                  f"news={s.get('news_accel','?')} book={s.get('book_signal','?'):12} "
                  f"edge={s.get('edge_pts','?')} {s.get('side','?')} "
                  f"'{s.get('market','?')[:40]}'")
            if s.get("catalyst"):
                print(f"     catalyst: {s['catalyst'][:75]}")

    # Always iMessage on any confluence signal (these are the highest quality)
    if signals:
        lines = [f"[confluence] {len(signals)} signal(s) ({len(tier1)} TIER1):"]
        for s in (tier1 or signals)[:3]:
            lines.append(f"{s.get('quality','?')} {s.get('side','?')} edge={s.get('edge_pts','?')} "
                         f"win={s.get('entry_window','?')} '{s.get('market','?')[:38]}'")
            if s.get("catalyst"):
                lines.append(f"  catalyst: {s['catalyst'][:60]}")
        _imessage("\n".join(lines))


def show():
    st = _load()
    sigs = st.get("signals", [])
    print(f"\n  Confluence signals: {len(sigs)}\n")
    for s in sigs[-10:]:
        print(f"  [{s.get('ts','')}] {s.get('quality','?'):6} {s.get('alignment','?'):10} "
              f"news={s.get('news_accel','?')} book={s.get('book_signal','?'):12} "
              f"{s.get('side','?')} edge={s.get('edge_pts','?')} "
              f"'{s.get('market','?')[:42]}'")
        if s.get("catalyst"):
            print(f"     catalyst: {s['catalyst'][:80]}")
        if s.get("entry_window"):
            print(f"     window: {s['entry_window']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="quiet watchdog mode")
    ap.add_argument("--show", action="store_true")
    a = ap.parse_args()
    if a.show: show(); return
    run(verbose=not a.once)


if __name__ == "__main__":
    main()
