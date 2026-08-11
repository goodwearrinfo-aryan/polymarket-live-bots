#!/usr/bin/env python3
"""news_velocity_agent.py — news acceleration vs market price lag detector.

Measures news velocity (items per hour) per topic across the hermes feed and
compares it against current Polymarket YES prices. When news is ACCELERATING
but the market price hasn't moved, there's a temporary information gap.

Claude's tool-use loop:
  news_velocity(topic) → list_markets(topic) → fetch_market → scan_orderbooks
  Flags: news_accel > 2x + market price < fair value + thin book = ENTER

Runs every 2h; saves news_velocity_signals.json.

Usage:
  python3 news_velocity_agent.py          # scan all topics
  python3 news_velocity_agent.py --once   # quiet watchdog mode
  python3 news_velocity_agent.py --show   # show past signals
"""
import os, json, time, argparse
from pathlib import Path
from agent_toolkit import run_agent, TOOL_DEFS

HERE    = Path(__file__).resolve().parent
SIGNALS = HERE / "news_velocity_signals.json"

SYSTEM = """You are a news-to-market-price lag detector. Your job is to find situations
where news coverage of a topic is ACCELERATING but the corresponding Polymarket price
has NOT yet adjusted — a temporary information inefficiency.

Your tools:
- news_velocity(query, window_hours): measure how fast news on a topic is coming in
- list_markets(topic): find open markets on that topic
- fetch_market(query): get current YES price + resolution criteria
- scan_orderbooks(topic): check if the book is thin (event risk loading) or imbalanced
- check_open_positions(): skip what's already in the book

SCAN PROTOCOL:
1. Run news_velocity() for each major topic category
2. Focus on topics where trend = ACCELERATING or ELEVATED (accel > 1.5x)
3. For accelerating topics: list_markets() to find the relevant market
4. fetch_market() to get current price and resolution criteria
5. scan_orderbooks() to see if the book is already thin (others positioning) or still normal
6. Only flag markets where: news is accelerating AND price looks stale AND book still normal

Key insight: if book is ALREADY thin/imbalanced → informed players already positioned,
the gap may be closed. If book is STILL NORMAL → you are early.

TOPICS to check (always check all):
- bitcoin price reach high
- ethereum crypto regulation
- iran nuclear deal strait hormuz
- israel ceasefire gaza
- ukraine russia war
- fed interest rate cut
- trump election policy
- china taiwan tech AI
- oil price energy
- inflation CPI jobs

VELOCITY SIGNAL FORMAT:
---VEL SIGNAL---
TOPIC: <topic>
ACCEL: <multiplier, e.g. 3.2x>
TREND: ACCELERATING | ELEVATED | STEADY | QUIET
MARKET: <question>
SLUG: <slug>
YES_PRICE: <current>
BOOK_STATUS: NORMAL | THIN | BID_HEAVY | ASK_HEAVY
NEWS_LEAD: <key headline driving the acceleration>
ESTIMATED_FAIR: <your fair YES price given the accelerating news>
GAP: <estimated_fair minus current YES, e.g. +0.12 = YES underpriced>
ACTION: ENTER | WATCH | PASS
---END---"""

TOPICS = [
    "bitcoin price", "ethereum crypto", "iran nuclear",
    "israel gaza ceasefire", "ukraine russia", "federal reserve rate cut",
    "trump election", "china taiwan", "oil energy", "inflation CPI",
]


def scan(verbose: bool = True) -> list:
    topic_str = ", ".join(TOPICS)
    user = (f"Check news velocity for these topics and find markets where "
            f"news is accelerating but price hasn't moved yet:\n{topic_str}\n\n"
            f"For each topic: news_velocity() first. Skip QUIET/STEADY topics. "
            f"For ACCELERATING/ELEVATED: list_markets() → fetch_market() → scan_orderbooks(). "
            f"Flag only markets where the book is still NORMAL (you are early) AND "
            f"the price looks like it hasn't digested the news. "
            f"Be concrete: state the estimated fair price and the gap.")
    if verbose:
        print(f"[news_velocity] scanning {len(TOPICS)} topics for acceleration...")
    response = run_agent(SYSTEM, user, verbose=verbose, max_tokens=1600, max_iters=18)

    signals = []
    blocks = response.split("---VEL SIGNAL---")[1:]
    for block in blocks:
        body = block.split("---END---")[0].strip()
        s = {"ts": time.strftime("%Y-%m-%d %H:%MZ", time.gmtime()),
             "topic": None, "accel": None, "trend": None, "market": None,
             "slug": None, "yes_price": None, "book_status": None,
             "news_lead": None, "estimated_fair": None, "gap": None, "action": None}
        for line in body.splitlines():
            line = line.strip()
            for field, key in [("TOPIC:", "topic"), ("ACCEL:", "accel"),
                                ("TREND:", "trend"), ("MARKET:", "market"),
                                ("SLUG:", "slug"), ("YES_PRICE:", "yes_price"),
                                ("BOOK_STATUS:", "book_status"),
                                ("NEWS_LEAD:", "news_lead"),
                                ("ESTIMATED_FAIR:", "estimated_fair"),
                                ("GAP:", "gap"), ("ACTION:", "action")]:
                if line.startswith(field):
                    s[key] = line[len(field):].strip()
        if s["market"] and s["trend"] in ("ACCELERATING","ELEVATED"):
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

    actionable = [s for s in signals if s.get("action") == "ENTER"]
    if verbose:
        print(f"\n[news_velocity] {len(signals)} vel-signal(s), {len(actionable)} ENTER → {SIGNALS}")
        for s in signals:
            print(f"  {s.get('trend','?'):13} {s.get('accel','?'):>5}x  "
                  f"book={s.get('book_status','?'):10} gap={s.get('gap','?'):>6}  "
                  f"'{s.get('market','?')[:45]}'")

    if actionable:
        lines = [f"[news_velocity] {len(actionable)} ENTER signal(s):"]
        for s in actionable[:3]:
            lines.append(f"{s.get('accel','?')}x '{s.get('market','?')[:42]}' "
                         f"gap={s.get('gap','?')} — {s.get('news_lead','?')[:55]}")
        _imessage("\n".join(lines))


def show():
    st = _load()
    sigs = st.get("signals", [])
    print(f"\n  News velocity signals: {len(sigs)}\n")
    for s in sigs[-12:]:
        print(f"  [{s.get('ts','')}] {s.get('trend','?'):13} {s.get('accel','?'):>5}x "
              f"act={s.get('action','?'):6}  '{s.get('market','?')[:48]}'")
        if s.get("news_lead"):
            print(f"     {s['news_lead'][:80]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="quiet watchdog mode")
    ap.add_argument("--show", action="store_true")
    a = ap.parse_args()
    if a.show: show(); return
    run(verbose=not a.once)


if __name__ == "__main__":
    main()
