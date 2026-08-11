#!/usr/bin/env python3
"""position_defender_agent.py — adversarial agentic stress-test of open positions.

Triggered when position_monitor fires INVESTIGATE or EXIT. Claude autonomously:
  • Fetches the current market price and resolution criteria
  • Searches hermes for breaking news that could affect resolution
  • Lists any related markets that might be pricing differently
  • Attacks the position's original thesis with fresh evidence
  • Returns a structured HOLD / REDUCE / EXIT verdict with full reasoning

This closes the loop: monitor detects a signal → defender investigates → actionable verdict.

Usage:
  python3 position_defender_agent.py                     # defend all INVESTIGATE positions
  python3 position_defender_agent.py --position "Iran"   # defend specific position by keyword
  python3 position_defender_agent.py --all               # defend all open positions
  python3 position_defender_agent.py --show              # show past verdicts
"""
import os, sys, json, time, argparse
from pathlib import Path
from agent_toolkit import run_agent, TOOL_DEFS

HERE    = Path(__file__).resolve().parent
VERDICTS = HERE / "defender_verdicts.json"

SYSTEM = """You are an adversarial risk manager whose job is to CHALLENGE open prediction-market
positions. You are NOT trying to confirm the original thesis — you are trying to FIND REASONS TO EXIT.

Default assumption: the position is wrong until proven otherwise.

Your tools:
- fetch_market: get live price, volume, current resolution criteria
- search_news: find breaking news that might INVALIDATE the thesis
- list_markets: find related markets pricing the same event differently
- check_open_positions: see the full position context and original thesis

INVESTIGATION PROTOCOL:
1. Fetch the market to get current price vs entry (has it moved against us?)
2. Read the original thesis from check_open_positions
3. Search news from at least 2-3 angles that could REFUTE the thesis
4. Look for related markets that price the event inconsistently
5. Synthesize: does the evidence support HOLDING or does something have changed?

VERDICT FORMAT (always end with this):
---DEFENDER VERDICT---
POSITION: <side> on <question>
ENTRY: <entry_yes>  CURRENT: <current_yes>  DRIFT: <delta>
THESIS_STATUS: INTACT | WEAKENED | INVALIDATED
ACTION: HOLD | REDUCE | EXIT
CONFIDENCE: <1-10>/10
KEY_FINDING: <the single most important thing you found>
REASONING: <2-3 sentences — what specifically changed or didn't>"""


def defend_position(pos: dict, verbose: bool = True) -> dict:
    q       = pos.get("q", "?")
    side    = pos.get("side", "?")
    entry   = pos.get("entry_yes", "?")
    thesis  = pos.get("thesis", "")
    resolves = pos.get("resolves","?")
    slug    = pos.get("slug", "") or pos.get("market_id","")

    user = (f"Challenge this open position — find reasons to EXIT:\n\n"
            f"Position: {side} on '{q}'\n"
            f"Entry YES price: {entry}  Resolves: {resolves}\n"
            f"Original thesis: {thesis}\n"
            f"Market slug/ID: {slug}\n\n"
            f"Use tools to: (1) check current price vs entry, "
            f"(2) search news for anything that REFUTES the thesis, "
            f"(3) find related markets pricing the event differently. "
            f"Be adversarial — default to EXIT unless evidence is strongly contrary.")
    if verbose:
        print(f"\n[defender] challenging: {side} '{q[:55]}'")
    response = run_agent(SYSTEM, user, verbose=verbose, max_tokens=900, max_iters=10)
    # Parse verdict
    result = {"q": q, "side": side, "entry_yes": entry,
              "ts": time.strftime("%Y-%m-%d %H:%MZ", time.gmtime()),
              "response": response, "action": "HOLD",
              "thesis_status": "INTACT", "confidence": None,
              "key_finding": None, "reasoning": None}
    if "---DEFENDER VERDICT---" in response:
        block = response.split("---DEFENDER VERDICT---")[-1]
        for line in block.splitlines():
            line = line.strip()
            for field, key in [("THESIS_STATUS:", "thesis_status"),
                                ("ACTION:", "action"),
                                ("CONFIDENCE:", "confidence"),
                                ("KEY_FINDING:", "key_finding"),
                                ("REASONING:", "reasoning")]:
                if line.startswith(field):
                    result[key] = line[len(field):].strip()
    return result


def _load_verdicts():
    if VERDICTS.exists():
        try:
            return json.loads(VERDICTS.read_text())
        except Exception:
            pass
    return {"verdicts": []}


def _save_verdicts(st):
    tmp = VERDICTS.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=2))
    tmp.replace(VERDICTS)


def _imessage(msg, recipient="+918449447444"):
    safe = msg.replace("\\","\\\\").replace('"','\\"').replace("\n","\\n")
    safe = "".join(c if ord(c) < 128 else " " for c in safe)
    os.system(f'osascript -e \'tell application "Messages" to send "{safe}" to buddy "{recipient}" of (service 1 whose service type is iMessage)\' 2>/dev/null')


def run(mode="investigate", keyword=None, verbose=True):
    book = HERE / "analyst_positions.json"
    if not book.exists():
        print("no analyst_positions.json"); return
    positions = json.loads(book.read_text()).get("positions", [])

    # Filter positions to defend
    if keyword:
        positions = [p for p in positions
                     if keyword.lower() in p.get("q","").lower()]
    elif mode == "investigate":
        # Only positions that triggered INVESTIGATE/EXIT in position_monitor
        mlog = HERE / "position_monitor.log"
        if not mlog.exists():
            print("no monitor log — run position_monitor.py first"); return
        alerted = set()
        for line in open(mlog):
            try:
                r = json.loads(line)
                if r.get("action") in ("INVESTIGATE","EXIT"):
                    alerted.add(r["q"])
            except Exception:
                continue
        positions = [p for p in positions if p.get("q","") in alerted]
    # else mode=="all" → defend everything

    if not positions:
        print(f"no positions to defend (mode={mode})"); return

    vst = _load_verdicts()
    exits, reduces = [], []

    for pos in positions[:3]:   # cap 3/run
        result = defend_position(pos, verbose=verbose)
        vst["verdicts"].append(result)
        action = result.get("action","HOLD")
        if verbose:
            print(f"\n  {action:8} {result.get('thesis_status','?'):12} "
                  f"conf={result.get('confidence','?')}  "
                  f"finding: {(result.get('key_finding') or '')[:70]}")
        if action == "EXIT":
            exits.append(result)
        elif action == "REDUCE":
            reduces.append(result)

    _save_verdicts(vst)
    print(f"\n[defender] {len(positions[:3])} defended — "
          f"{len(exits)} EXIT, {len(reduces)} REDUCE → {VERDICTS}")

    if exits or reduces:
        lines = ["[position_defender]"]
        for r in exits:
            lines.append(f"EXIT {r['side']} '{r['q'][:40]}' — {r.get('key_finding','?')[:60]}")
        for r in reduces:
            lines.append(f"REDUCE {r['side']} '{r['q'][:40]}' — {r.get('key_finding','?')[:60]}")
        _imessage("\n".join(lines))


def show_verdicts():
    st = _load_verdicts()
    vs = st.get("verdicts",[])
    print(f"\n  Defender verdicts: {len(vs)}\n")
    for v in vs[-10:]:
        print(f"  [{v.get('ts','')}] {v.get('action','?'):8} {v.get('thesis_status','?'):12} "
              f"conf={v.get('confidence','?')}  {v.get('side','?')} '{v.get('q','?')[:45]}'")
        if v.get("key_finding"):
            print(f"     finding: {v['key_finding'][:80]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="defend all open positions")
    ap.add_argument("--position", metavar="KW",
                    help="defend positions matching keyword")
    ap.add_argument("--once", action="store_true",
                    help="quiet mode — defend INVESTIGATE positions (for watchdog)")
    ap.add_argument("--show", action="store_true", help="show past verdicts")
    a = ap.parse_args()

    if a.show:
        show_verdicts(); return
    if a.position:
        run(mode="keyword", keyword=a.position, verbose=True); return
    if a.all:
        run(mode="all", verbose=True); return
    # default / --once: defend INVESTIGATE positions
    run(mode="investigate", verbose=not a.once)


if __name__ == "__main__":
    main()
