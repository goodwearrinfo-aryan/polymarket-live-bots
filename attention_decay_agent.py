#!/usr/bin/env python3
"""attention_decay_agent.py — fade markets priced on dying news stories.

The INVERSE of news_velocity_agent. Finds markets where:
  - The catalyst story is DYING (news_velocity trend = QUIET or FADING)
  - But the market price is STILL ELEVATED as if the story is live
  - The "fear premium" or "excitement premium" hasn't unwound yet

This is a behavioral edge: markets overshoot on news arrival and undershoot on decay.
When the story dies but price stays high, fade it.

Examples:
  - Iran nuclear deal news peaked 3 days ago, NO coverage since → YES still at 0.65 (fade to NO)
  - Crypto regulatory panic story faded → still priced at 0.15 YES (overstated fear)
  - Election endorsement hype faded → candidate still priced 5¢ too high

Claude's tool-use loop:
  1. Load prior velocity signals (which topics were recently accelerating)
  2. news_velocity for each — has acceleration STOPPED?
  3. list_markets + fetch_market — is price still elevated?
  4. scan_orderbooks — is book thinning (others are fading too) or thick (room to fade)?
  Synthesizes: which markets have stale attention premium not yet unwound?

Output: attention_decay_signals.json  +  iMessage on HIGH-CONFIDENCE fades

Usage:
  python3 attention_decay_agent.py         # scan for decays
  python3 attention_decay_agent.py --once  # quiet watchdog mode
  python3 attention_decay_agent.py --show  # show past signals
"""
import os, json, time, argparse
from pathlib import Path
from agent_toolkit import run_agent, TOOL_DEFS

HERE    = Path(__file__).resolve().parent
SIGNALS = HERE / "attention_decay_signals.json"

SYSTEM = """You are an attention-decay trader. You look for markets where the NEWS STORY
that drove the price UP has gone QUIET — but the market price hasn't come back down yet.

This is the INVERSE of chasing news. You are fading the attention premium after it's stale.

The psychological mechanism: when a scary/exciting story breaks, markets overshoot.
When coverage dries up and nothing new happens, the implied probability slowly bleeds
back to base rate. You want to enter EARLY in that bleed — before it's priced.

Your tools:
- news_velocity(query, window_hours=3): measure RECENT news flow — is the story DYING?
- list_markets(topic): find markets elevated on this topic
- fetch_market(query): current price + resolution criteria + days remaining
- scan_orderbooks(topic): is book thick (room to fade) or thin (others already fading)?
- search_news(query): what was the ORIGINAL catalyst? is there ANY new development?
- check_open_positions(): avoid what's already in book

DECAY DETECTION PROTOCOL:
1. Check the following topics that were recently in the news (check news_velocity):
   - iran nuclear strait hormuz deal
   - israel ceasefire gaza hamas
   - bitcoin crypto crash regulation
   - trump tariff trade war
   - ukraine russia war ceasefire
   - fed rate cut inflation
   - china taiwan invasion
   - election polling swing state

2. For QUIET/FADING topics (accel < 0.5x or trend=QUIET): find the markets
3. fetch_market to see the current YES price — is it still elevated?
4. scan_orderbooks — a THICK book means others haven't faded yet (good entry)
5. search_news to confirm: is there really no new development? (don't fade if new news)

WHAT IS "ELEVATED"? The market price vs base rate for that event class:
- War/military events: base rate ~10-20% for major escalation
- Nuclear deals: base rate ~15-25% for completion
- Regulatory actions: base rate ~20-35% for implementation
- If current YES >> base rate and news has gone quiet → fade candidate

DECAY SIGNAL FORMAT:
---DECAY---
TOPIC: <the dying story>
VELOCITY_NOW: <current accel, e.g. 0.2x (QUIET)>
VELOCITY_PEAK: <what it was at peak if known, or "unknown">
MARKET: <question>
SLUG: <slug>
CURRENT_YES: <current price>
ESTIMATED_FAIR: <your base-rate estimate now that attention has faded>
OVERPRICING: <current - fair, e.g. +0.12 = 12¢ overpriced>
SIDE: NO (we are selling the stale attention premium — almost always NO)
LAST_CATALYST: <the original story that drove it up>
NEW_DEVELOPMENTS: YES | NO (if YES → do NOT fade, don't produce signal)
BOOK_STATUS: THICK | NORMAL | THIN (THICK = good, others haven't faded)
CONFIDENCE: HIGH | MEDIUM | LOW
ACTION: FADE_NOW | FADE_SOON | WATCH | PASS
DAYS_TO_RESOLUTION: <number>
---END---"""


def _load_prior_velocity():
    """Get topics that were recently accelerating from news_velocity_signals.json."""
    vf = HERE / "news_velocity_signals.json"
    if not vf.exists():
        return []
    st = json.loads(vf.read_text())
    cutoff = time.time() - 7 * 86400  # last 7 days
    topics = set()
    for s in st.get("signals", []):
        try:
            ts = time.mktime(time.strptime(s.get("ts","")[:16], "%Y-%m-%d %H:%M"))
        except Exception:
            continue
        if ts > cutoff and s.get("trend") in ("ACCELERATING","ELEVATED"):
            t = s.get("topic","")
            if t:
                topics.add(t)
    return list(topics)


def scan(verbose: bool = True) -> list:
    prior = _load_prior_velocity()
    prior_str = "\n".join(f"- {t}" for t in prior[:8]) if prior else "(none in cache)"

    user = (f"Scan for attention-decay fade opportunities. These topics were recently "
            f"accelerating in the news (from prior velocity scans):\n{prior_str}\n\n"
            f"Also check these default topics regardless:\n"
            f"- iran nuclear ceasefire\n- israel gaza hamas\n- bitcoin regulation\n"
            f"- ukraine russia war\n- trump tariff policy\n- fed inflation\n\n"
            f"For EACH topic: news_velocity() to see if coverage has DIED (accel <0.5x). "
            f"For QUIET/FADING topics: list_markets → fetch_market to check if price is "
            f"still elevated above base rate. scan_orderbooks to see if book is still thick. "
            f"CRITICAL: search_news to confirm there is NO new development that justifies "
            f"the elevated price. Only produce ---DECAY--- signals where: "
            f"(1) news is clearly quiet, (2) price is above base rate, (3) no new news, "
            f"(4) book is THICK or NORMAL (others haven't faded yet).")
    if verbose:
        print(f"[attention_decay] scanning for fading stories with stale prices...")
    response = run_agent(SYSTEM, user, verbose=verbose, max_tokens=1600, max_iters=16)

    signals = []
    blocks = response.split("---DECAY---")[1:]
    for block in blocks:
        body = block.split("---END---")[0].strip()
        s = {"ts": time.strftime("%Y-%m-%d %H:%MZ", time.gmtime()),
             "topic": None, "velocity_now": None, "market": None, "slug": None,
             "current_yes": None, "estimated_fair": None, "overpricing": None,
             "side": "NO", "last_catalyst": None, "new_developments": None,
             "book_status": None, "confidence": None, "action": None,
             "days_to_resolution": None}
        for line in body.splitlines():
            line = line.strip()
            for field, key in [("TOPIC:", "topic"), ("VELOCITY_NOW:", "velocity_now"),
                                ("MARKET:", "market"), ("SLUG:", "slug"),
                                ("CURRENT_YES:", "current_yes"),
                                ("ESTIMATED_FAIR:", "estimated_fair"),
                                ("OVERPRICING:", "overpricing"), ("SIDE:", "side"),
                                ("LAST_CATALYST:", "last_catalyst"),
                                ("NEW_DEVELOPMENTS:", "new_developments"),
                                ("BOOK_STATUS:", "book_status"),
                                ("CONFIDENCE:", "confidence"), ("ACTION:", "action"),
                                ("DAYS_TO_RESOLUTION:", "days_to_resolution")]:
                if line.startswith(field):
                    s[key] = line[len(field):].strip()
        # only keep if no new developments and market found
        if s["market"] and s.get("new_developments") != "YES":
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

    high_conf = [s for s in signals if s.get("confidence") == "HIGH"
                 and s.get("action") in ("FADE_NOW","FADE_SOON")]
    if verbose:
        print(f"\n[attention_decay] {len(signals)} decay signal(s), "
              f"{len(high_conf)} HIGH-confidence FADE → {SIGNALS}")
        for s in signals:
            print(f"  {s.get('confidence','?'):6} {s.get('action','?'):12} "
                  f"vel={s.get('velocity_now','?'):8} overpricing={s.get('overpricing','?'):>6}  "
                  f"'{s.get('market','?')[:45]}'")

    if high_conf:
        lines = [f"[decay] {len(high_conf)} HIGH-conf fade(s):"]
        for s in high_conf[:3]:
            lines.append(f"NO '{s.get('market','?')[:42]}' over={s.get('overpricing','?')} "
                         f"vel={s.get('velocity_now','?')}")
            if s.get("last_catalyst"):
                lines.append(f"  was: {s['last_catalyst'][:60]}")
        _imessage("\n".join(lines))


def show():
    st = _load()
    sigs = st.get("signals", [])
    print(f"\n  Attention decay signals: {len(sigs)}\n")
    for s in sigs[-10:]:
        print(f"  [{s.get('ts','')}] {s.get('confidence','?'):6} {s.get('action','?'):12} "
              f"over={s.get('overpricing','?'):>6}  '{s.get('market','?')[:48]}'")
        if s.get("last_catalyst"):
            print(f"     was: {s['last_catalyst'][:80]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="quiet watchdog mode")
    ap.add_argument("--show", action="store_true")
    a = ap.parse_args()
    if a.show: show(); return
    run(verbose=not a.once)


if __name__ == "__main__":
    main()
