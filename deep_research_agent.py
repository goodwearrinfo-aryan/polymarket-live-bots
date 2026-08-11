#!/usr/bin/env python3
"""deep_research_agent.py — agentic deep-diver for a single scout candidate.

Uses Claude tool-use to autonomously research a candidate before committing it:
  • Fetches LIVE market details and resolution criteria
  • Checks order book spread (does it eat the edge?)
  • Searches hermes for breaking news on the topic
  • Reads open positions to avoid duplicating a bet
  Claude decides which tools to call and iterates until confident.

Output: enriched candidate record saved to deep_research_reports.json + print.

Usage:
  python3 deep_research_agent.py --slug <market-slug>
  python3 deep_research_agent.py --candidate N      # N-th scout candidate
  python3 deep_research_agent.py --auto             # auto-research all gate-passing candidates
"""
import os, sys, json, time, argparse
from pathlib import Path
from agent_toolkit import run_agent, TOOL_DEFS

HERE    = Path(__file__).resolve().parent
REPORTS = HERE / "deep_research_reports.json"

SYSTEM = """You are a rigorous prediction-market research analyst. Your job is to deeply
research a specific market and determine whether an information-edge bet is warranted.

You have tools to:
- fetch_market: get live price, volume, resolution criteria
- search_news: check current news on the topic
- get_order_book: verify the spread doesn't eat the claimed edge
- check_open_positions: avoid duplicating an existing book position

RESEARCH PROTOCOL:
1. Always start with fetch_market to get the REAL resolution criteria
2. Always check search_news for at least 2 different keyword angles
3. Check get_order_book to verify tradeable spread
4. Check check_open_positions for conflicts
5. Only after gathering evidence, give your VERDICT

VERDICT FORMAT (always end with this):
---VERDICT---
SIDE: YES or NO
TRUE_PROB: <your estimate 0.01-0.99>
EDGE: <abs(true_prob - market_yes) * 100>pts
CONFIDENCE: <1-10>/10
ACTION: PURSUE | PASS | NEEDS_MORE_INFO
THESIS: <2-3 sentences grounded in the evidence you found>
RISK_FLAGS: <specific risks the evidence surfaced>"""


def research_market(slug_or_query: str, verbose: bool = True) -> dict:
    user = (f"Research this Polymarket market and give me a VERDICT: '{slug_or_query}'\n\n"
            f"Check: (1) real resolution criteria, (2) current YES price vs fair value, "
            f"(3) order book spread vs claimed edge, (4) live news on topic, "
            f"(5) any conflict with existing book positions.\n\n"
            f"Use tools to gather evidence BEFORE concluding.")
    if verbose:
        print(f"\n[deep_research] researching: {slug_or_query}")
    response = run_agent(SYSTEM, user, verbose=verbose, max_tokens=900, max_iters=10)
    # Parse verdict block
    result = {"query": slug_or_query, "ts": time.strftime("%Y-%m-%d %H:%MZ", time.gmtime()),
              "response": response, "action": "NEEDS_MORE_INFO",
              "side": None, "true_prob": None, "edge": None, "confidence": None,
              "thesis": None, "risk_flags": None}
    if "---VERDICT---" in response:
        verdict_block = response.split("---VERDICT---")[-1]
        for line in verdict_block.splitlines():
            line = line.strip()
            for field, key in [("SIDE:", "side"), ("TRUE_PROB:", "true_prob"),
                                ("EDGE:", "edge"), ("CONFIDENCE:", "confidence"),
                                ("ACTION:", "action"), ("THESIS:", "thesis"),
                                ("RISK_FLAGS:", "risk_flags")]:
                if line.startswith(field):
                    val = line[len(field):].strip()
                    result[key] = float(val) if key in ("true_prob",) else val
    return result


def _load_reports():
    if REPORTS.exists():
        try:
            return json.loads(REPORTS.read_text())
        except Exception:
            pass
    return {"reports": []}


def _save_reports(st):
    tmp = REPORTS.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=2))
    tmp.replace(REPORTS)


def auto_research(verbose: bool = True):
    """Auto-research all gate-passing scout candidates not yet researched."""
    sc = HERE / "scout_candidates.json"
    if not sc.exists():
        print("no scout_candidates.json")
        _save_reports(_load_reports())   # heartbeat: refresh mtime so health-monitor sees alive
        return
    st = json.loads(sc.read_text())
    candidates = [c for c in st.get("candidates", [])
                  if c.get("gate_pass") and not c.get("promoted") and not c.get("deep_researched")]
    if not candidates:
        print("no gate-passing unresearched candidates")
        _save_reports(_load_reports())   # heartbeat: refresh mtime so health-monitor sees alive
        return
    rep_st = _load_reports()
    for c in candidates[:3]:   # cap at 3 per run (~$0.30 in opus calls)
        slug = c.get("slug") or c.get("q", "?")
        result = research_market(slug, verbose=verbose)
        rep_st["reports"].append(result)
        # Mark candidate as researched
        for orig in st["candidates"]:
            if orig.get("slug") == c.get("slug"):
                orig["deep_researched"] = True
                orig["deep_action"] = result.get("action")
                orig["deep_confidence"] = result.get("confidence")
        if verbose:
            print(f"\n  ACTION: {result['action']}  conf={result['confidence']}  "
                  f"thesis: {(result.get('thesis') or '')[:80]}")
    _save_reports(rep_st)
    # save enriched scout state
    tmp = sc.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=2))
    tmp.replace(sc)
    print(f"\n[deep_research] {len(candidates[:3])} researched → {REPORTS}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", help="market slug or search phrase to research")
    ap.add_argument("--candidate", type=int, metavar="N",
                    help="research N-th gate-passing scout candidate")
    ap.add_argument("--auto", action="store_true",
                    help="research all unresearched gate-passing candidates (max 3/run)")
    ap.add_argument("--once", action="store_true", help="quiet watchdog mode (implies --auto)")
    ap.add_argument("--show", action="store_true", help="show past research reports")
    a = ap.parse_args()

    if a.show:
        st = _load_reports()
        reps = st.get("reports", [])
        print(f"\n  Deep research reports: {len(reps)}\n")
        for r in reps[-10:]:
            print(f"  [{r.get('ts','')}] {r.get('action','?'):15} conf={r.get('confidence','?')} "
                  f"  {r.get('query','?')[:50]}")
            if r.get("thesis"):
                print(f"     thesis: {r['thesis'][:80]}")
        return

    if a.auto or a.once:
        auto_research(verbose=not a.once); return

    if a.candidate is not None:
        sc = HERE / "scout_candidates.json"
        if not sc.exists():
            print("no scout_candidates.json"); return
        cands = [c for c in json.loads(sc.read_text()).get("candidates",[])
                 if c.get("gate_pass")]
        if a.candidate >= len(cands):
            print(f"only {len(cands)} gate-passing candidates"); return
        c = cands[a.candidate]
        slug = c.get("slug") or c.get("q","?")
        result = research_market(slug)
        rep_st = _load_reports()
        rep_st["reports"].append(result)
        _save_reports(rep_st)
        print(f"\n  ACTION: {result['action']}  conf={result['confidence']}")
        return

    if a.slug:
        result = research_market(a.slug)
        rep_st = _load_reports()
        rep_st["reports"].append(result)
        _save_reports(rep_st)
        return

    ap.print_help()


if __name__ == "__main__":
    main()
