#!/usr/bin/env python3
"""anchor_hunter_agent.py — psychological price anchor detection + snap prediction.

Behavioral finance finding: prediction markets cluster prices at round numbers
(0.10, 0.20, 0.25, 0.33, 0.50, 0.67, 0.75, 0.90) under genuine uncertainty.
This "anchoring" is especially strong when:
  - The true probability is hard to compute
  - The market has been open a long time without new information
  - The resolver is ambiguous or slow

When news arrives that SHOULD move the price but the anchor holds, it creates a
COILED SPRING: the anchor eventually snaps and the market moves 5-15¢ quickly.

Strategy: find anchored markets WITH an active news signal and enter BEFORE the snap.

Signs of a ripe anchor snap:
  1. Price within ±1¢ of a round number (0.50, 0.25, 0.75, 0.10, 0.90...)
  2. News velocity is ELEVATED or ACCELERATING on the topic
  3. Order book is starting to thin (early movers loading before snap)
  4. Market has been at this price for a while (stale = anchored, not just stable)

Claude's tool-use loop:
  list_markets → check YES price proximity to anchors → news_velocity → scan_orderbooks
  Synthesizes: which anchored markets have enough pressure to snap, and which direction?

Output: anchor_signals.json  +  iMessage on HIGH-confidence anchors with news pressure

Usage:
  python3 anchor_hunter_agent.py          # scan for anchored markets
  python3 anchor_hunter_agent.py --once   # quiet watchdog mode
  python3 anchor_hunter_agent.py --show   # show past signals
"""
import os, json, time, argparse
from pathlib import Path
from agent_toolkit import run_agent, TOOL_DEFS

HERE    = Path(__file__).resolve().parent
SIGNALS = HERE / "anchor_signals.json"

ANCHORS = [0.10, 0.15, 0.20, 0.25, 0.30, 0.33, 0.40, 0.50, 0.60, 0.67, 0.70, 0.75, 0.80, 0.85, 0.90]

SYSTEM = """You are a behavioral price-anchor specialist for prediction markets. You find
markets where the price is "stuck" at a psychological round number — and identify which
ones are about to SNAP to their true value because news pressure has built up.

KEY INSIGHT: When a market's true probability is genuinely uncertain, traders anchor to
round numbers (50%, 25%, 75%, etc.) as "neutral" or "fair" placeholders. This isn't
random — it's systematic and predictable. The anchor eventually breaks when:
  a) News pressure accumulates to one side
  b) The order book starts thinning (early movers positioning for the snap)
  c) A nearby deadline approaches (time pressure resolves anchoring)

Your tools:
- list_markets(topic): find candidate markets — look at YES prices
- fetch_market(query): get current price, volume, days to resolution, resolution criteria
- news_velocity(query): measure news pressure — is there a catalyst building?
- scan_orderbooks(topic): is book thinning? (thinning = snap coming)
- search_news(query): what is the specific news pressure if any?
- check_open_positions(): avoid duplicates

ROUND NUMBER ANCHORS to hunt (within ±1¢):
  0.10, 0.15, 0.20, 0.25, 0.30, 0.33, 0.50, 0.67, 0.70, 0.75, 0.80, 0.85, 0.90

ANCHOR QUALITY FACTORS:
- STRONG anchor: price exactly at round number, news accelerating, book thinning → snap imminent
- MODERATE anchor: price within 1¢, elevated news, book normal
- WEAK anchor: price within 2¢, quiet news (may stay anchored indefinitely)

SNAP DIRECTION: use news_velocity + search_news to determine WHICH WAY it snaps.
  - If news is bullish (story developing positively): snap UP → buy YES
  - If news is bearish (story developing negatively): snap DOWN → buy NO
  - If no clear news direction: WATCH only

SCAN BROAD: use list_markets on multiple topics to find markets priced at round numbers:
- "election president" — many markets cluster at 0.50
- "bitcoin crypto" — threshold markets often anchor at 0.25/0.75
- "war ceasefire" — geopolitical markets often at 0.20/0.30
- "deal agreement" — negotiation markets cluster at 0.50
- "sports championship" — tournament markets at 0.25/0.33/0.50

ANCHOR SIGNAL FORMAT:
---ANCHOR---
ANCHOR_PRICE: <the round number, e.g. 0.50>
CURRENT_YES: <actual current price>
DISTANCE_FROM_ANCHOR: <in cents, e.g. 0.5¢>
ANCHOR_QUALITY: STRONG | MODERATE | WEAK
NEWS_PRESSURE: <velocity and trend, e.g. "2.4x ACCELERATING toward YES">
SNAP_DIRECTION: YES | NO | UNCLEAR
SNAP_MAGNITUDE: <estimated ¢ move when anchor breaks>
BOOK_STATUS: THINNING | NORMAL | THICK
MARKET: <question>
SLUG: <slug>
DAYS_TO_RESOLUTION: <number>
TIME_PRESSURE: HIGH (≤7d) | MEDIUM (7-21d) | LOW (>21d)
CONFIDENCE: HIGH | MEDIUM | LOW
ACTION: BUY_YES | BUY_NO | WATCH | PASS
CATALYST: <specific news driving pressure, or "none — time pressure only">
---END---"""

SCAN_TOPICS = [
    "election president", "bitcoin crypto reach",
    "war ceasefire deal", "trade agreement",
    "sports championship winner",
    "AI regulation tech",
]


def scan(verbose: bool = True) -> list:
    anchor_list = ", ".join(str(a) for a in ANCHORS)
    user = (f"Find prediction markets with prices anchored at psychological round numbers "
            f"({anchor_list}) that are under news pressure and about to snap.\n\n"
            f"Scan these topics: {', '.join(SCAN_TOPICS)}\n\n"
            f"For each topic: list_markets() to get YES prices. Flag any market within "
            f"±1¢ of a round number. For each anchor candidate: "
            f"(1) news_velocity() — is there news pressure building? "
            f"(2) scan_orderbooks() — is the book thinning? "
            f"(3) fetch_market() — read resolution criteria + days remaining. "
            f"(4) search_news() if velocity is elevated — what's the specific catalyst? "
            f"Determine snap direction from news. Only produce ---ANCHOR--- signals "
            f"for MODERATE or STRONG anchor quality. Skip WEAK anchors with no news pressure.")
    if verbose:
        print(f"[anchor_hunter] scanning {len(SCAN_TOPICS)} topic areas for price anchors...")
    response = run_agent(SYSTEM, user, verbose=verbose, max_tokens=1600, max_iters=18)

    signals = []
    blocks = response.split("---ANCHOR---")[1:]
    for block in blocks:
        body = block.split("---END---")[0].strip()
        s = {"ts": time.strftime("%Y-%m-%d %H:%MZ", time.gmtime()),
             "anchor_price": None, "current_yes": None, "distance_from_anchor": None,
             "anchor_quality": None, "news_pressure": None, "snap_direction": None,
             "snap_magnitude": None, "book_status": None, "market": None, "slug": None,
             "days_to_resolution": None, "time_pressure": None,
             "confidence": None, "action": None, "catalyst": None}
        for line in body.splitlines():
            line = line.strip()
            for field, key in [("ANCHOR_PRICE:", "anchor_price"),
                                ("CURRENT_YES:", "current_yes"),
                                ("DISTANCE_FROM_ANCHOR:", "distance_from_anchor"),
                                ("ANCHOR_QUALITY:", "anchor_quality"),
                                ("NEWS_PRESSURE:", "news_pressure"),
                                ("SNAP_DIRECTION:", "snap_direction"),
                                ("SNAP_MAGNITUDE:", "snap_magnitude"),
                                ("BOOK_STATUS:", "book_status"),
                                ("MARKET:", "market"), ("SLUG:", "slug"),
                                ("DAYS_TO_RESOLUTION:", "days_to_resolution"),
                                ("TIME_PRESSURE:", "time_pressure"),
                                ("CONFIDENCE:", "confidence"), ("ACTION:", "action"),
                                ("CATALYST:", "catalyst")]:
                if line.startswith(field):
                    s[key] = line[len(field):].strip()
        if s["market"] and s["anchor_quality"] in ("STRONG","MODERATE"):
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

    strong = [s for s in signals if s.get("anchor_quality") == "STRONG"
              and s.get("confidence") == "HIGH"
              and s.get("action") in ("BUY_YES","BUY_NO")]
    if verbose:
        print(f"\n[anchor_hunter] {len(signals)} anchor(s), {len(strong)} STRONG+HIGH → {SIGNALS}")
        for s in signals:
            print(f"  {s.get('anchor_quality','?'):8} anchor={s.get('anchor_price','?'):>4} "
                  f"curr={s.get('current_yes','?'):>5} snap={s.get('snap_direction','?'):4} "
                  f"+{s.get('snap_magnitude','?')}  '{s.get('market','?')[:40]}'")
            if s.get("catalyst") and s["catalyst"] != "none — time pressure only":
                print(f"     catalyst: {s['catalyst'][:75]}")

    if strong:
        lines = [f"[anchor_hunter] {len(strong)} STRONG snap(s) coming:"]
        for s in strong[:3]:
            lines.append(f"anchor@{s.get('anchor_price','?')} → snap {s.get('snap_direction','?')} "
                         f"+{s.get('snap_magnitude','?')} '{s.get('market','?')[:38]}'")
            if s.get("catalyst"):
                lines.append(f"  catalyst: {s['catalyst'][:60]}")
        _imessage("\n".join(lines))


def show():
    st = _load()
    sigs = st.get("signals", [])
    print(f"\n  Anchor signals: {len(sigs)}\n")
    for s in sigs[-12:]:
        print(f"  [{s.get('ts','')}] {s.get('anchor_quality','?'):8} "
              f"anchor={s.get('anchor_price','?'):>4} curr={s.get('current_yes','?'):>5} "
              f"snap={s.get('snap_direction','?'):4} {s.get('snap_magnitude','?')}  "
              f"'{s.get('market','?')[:45]}'")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="quiet watchdog mode")
    ap.add_argument("--show", action="store_true")
    a = ap.parse_args()
    if a.show: show(); return
    run(verbose=not a.once)


if __name__ == "__main__":
    main()
