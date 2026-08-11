#!/usr/bin/env python3
"""edge_hunter_agent.py — agentic opportunity hunter driven by hypothesis seeds.

Takes hypothesis seeds from hypothesis_lab.py and autonomously hunts for matching
Polymarket markets, researches them with live data, and produces ranked opportunity
dossiers — ready to fast-track into analyst_positions.json.

Claude's tool-use loop per seed:
  1. list_markets — find markets matching the seed's search query
  2. fetch_market — get resolution criteria for each candidate
  3. search_news  — verify the signal is live in current news cycle
  4. check_open_positions — avoid duplicates
  Then synthesize: rank the matches and draft dossiers for the top ones.

Runs weekly (same cadence as hypothesis_lab), produces edge_hunter_dossiers.json.

Usage:
  python3 edge_hunter_agent.py           # hunt all active seeds
  python3 edge_hunter_agent.py --seed hyp-01-03    # hunt one specific seed
  python3 edge_hunter_agent.py --show    # show past dossiers
  python3 edge_hunter_agent.py --once    # quiet watchdog mode
"""
import os, sys, json, time, argparse
from pathlib import Path
from agent_toolkit import run_agent, TOOL_DEFS

HERE     = Path(__file__).resolve().parent
DOSSIERS = HERE / "edge_hunter_dossiers.json"

SYSTEM = """You are an opportunity-hunting prediction-market analyst. You have a hypothesis
about a type of market where the crowd systematically misprice — now you must FIND specific
live markets where this hypothesis applies today, research them, and rank them.

Your tools:
- list_markets(topic): find live markets matching a theme
- fetch_market(query): get current price + resolution criteria for a specific market
- search_news(query): check if the signal is live in current news
- check_open_positions(): see what's already in the book (avoid duplicates)
- read_hypothesis_seeds(): re-read all active seeds for context

HUNT PROTOCOL:
1. Start with read_hypothesis_seeds() to understand the full context
2. Use list_markets() with the seed's search query to find candidates
3. For the top 2-3 candidates: fetch_market() for criteria, search_news() for signal confirmation
4. Eliminate markets where: (a) resolution is ambiguous, (b) the signal isn't confirmed in news,
   (c) the market already prices in the edge, (d) already in open positions
5. Rank survivors and produce a dossier for each

DOSSIER FORMAT (for each viable market, include this block):
---DOSSIER---
MARKET: <question>
SLUG: <slug>
HYPOTHESIS_ID: <seed id>
SIDE: YES or NO
YES_PRICE: <current>
EDGE: <estimated edge in pts>
SIGNAL_CONFIRMED: YES / PARTIAL / NO
NEWS_EVIDENCE: <headline or "none found">
THESIS: <2 sentences — what the crowd gets wrong + why this resolves in our favour>
RISK: Low / Medium / High
---END---"""


def hunt_seed(seed: dict, verbose: bool = True) -> list:
    sid   = seed.get("id","?")
    query = seed.get("market_search_query","?")
    sig   = seed.get("signal","?")
    direc = seed.get("direction","?")
    why   = seed.get("why_crowd_wrong","?")

    user = (f"Hunt for live Polymarket opportunities matching this hypothesis seed:\n\n"
            f"Seed ID: {sid}\n"
            f"Direction: {direc}\n"
            f"Signal: {sig}\n"
            f"Why crowd is wrong: {why}\n"
            f"Search query to start: '{query}'\n\n"
            f"Use tools to: (1) find matching live markets, (2) verify the signal is "
            f"active in today's news, (3) check resolution criteria are clear, "
            f"(4) avoid duplicating open positions. "
            f"Produce a ---DOSSIER--- block for each viable opportunity found.")
    if verbose:
        print(f"\n[edge_hunter] hunting seed {sid}: {query}")
    response = run_agent(SYSTEM, user, verbose=verbose, max_tokens=1200, max_iters=12)

    # Parse all DOSSIER blocks
    dossiers = []
    blocks = response.split("---DOSSIER---")[1:]
    for block in blocks:
        body = block.split("---END---")[0].strip()
        d = {"seed_id": sid, "ts": time.strftime("%Y-%m-%d %H:%MZ", time.gmtime()),
             "market": None, "slug": None, "side": None, "yes_price": None,
             "edge": None, "signal_confirmed": None, "news_evidence": None,
             "thesis": None, "risk": None}
        for line in body.splitlines():
            line = line.strip()
            for field, key in [("MARKET:", "market"), ("SLUG:", "slug"),
                                ("SIDE:", "side"), ("YES_PRICE:", "yes_price"),
                                ("EDGE:", "edge"), ("SIGNAL_CONFIRMED:", "signal_confirmed"),
                                ("NEWS_EVIDENCE:", "news_evidence"),
                                ("THESIS:", "thesis"), ("RISK:", "risk")]:
                if line.startswith(field):
                    d[key] = line[len(field):].strip()
        if d["market"]:
            dossiers.append(d)
    return dossiers


def _load_dossiers():
    if DOSSIERS.exists():
        try:
            return json.loads(DOSSIERS.read_text())
        except Exception:
            pass
    return {"dossiers": [], "last_hunt": None}


def _save_dossiers(st):
    tmp = DOSSIERS.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=2))
    tmp.replace(DOSSIERS)


def _imessage(msg, recipient="+918449447444"):
    safe = msg.replace("\\","\\\\").replace('"','\\"').replace("\n","\\n")
    safe = "".join(c if ord(c) < 128 else " " for c in safe)
    os.system(f'osascript -e \'tell application "Messages" to send "{safe}" to buddy "{recipient}" of (service 1 whose service type is iMessage)\' 2>/dev/null')


def mark_seed_used(seed_id: str):
    sf = HERE / "hypothesis_seeds.json"
    if not sf.exists():
        return
    st = json.loads(sf.read_text())
    for s in st.get("seeds",[]):
        if s.get("id") == seed_id:
            s["used"] = True
    tmp = sf.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=2))
    tmp.replace(sf)


def run(seed_filter: str = None, verbose: bool = True):
    sf = HERE / "hypothesis_seeds.json"
    if not sf.exists():
        print("no hypothesis seeds — run hypothesis_lab.py first"); return
    seeds = [s for s in json.loads(sf.read_text()).get("seeds",[])
             if not s.get("used")]
    if seed_filter:
        seeds = [s for s in seeds if s.get("id") == seed_filter]
    if not seeds:
        print("no active seeds to hunt"); return

    dst = _load_dossiers()
    all_dossiers = []

    for seed in seeds[:3]:   # cap 3 seeds/run (~$0.30 in opus calls)
        dossiers = hunt_seed(seed, verbose=verbose)
        all_dossiers.extend(dossiers)
        dst["dossiers"].extend(dossiers)
        mark_seed_used(seed["id"])
        if verbose:
            print(f"\n  Seed {seed['id']}: {len(dossiers)} dossier(s) found")
            for d in dossiers:
                print(f"    {d.get('side','?')} '{d.get('market','?')[:50]}' "
                      f"edge={d.get('edge','?')} risk={d.get('risk','?')}")

    dst["last_hunt"] = time.strftime("%Y-%m-%d %H:%MZ", time.gmtime())
    _save_dossiers(dst)
    print(f"\n[edge_hunter] {len(seeds[:3])} seeds hunted → {len(all_dossiers)} dossiers → {DOSSIERS}")

    if all_dossiers:
        lines = [f"[edge_hunter] {len(all_dossiers)} opportunity dossier(s):"]
        for d in all_dossiers[:3]:
            lines.append(f"{d.get('side','?')} '{d.get('market','?')[:42]}' "
                         f"edge={d.get('edge','?')} signal={d.get('signal_confirmed','?')}")
        _imessage("\n".join(lines))


def show():
    st = _load_dossiers()
    ds = st.get("dossiers",[])
    print(f"\n  Edge hunter dossiers: {len(ds)}\n")
    for d in ds[-10:]:
        print(f"  [{d.get('ts','')}] seed={d.get('seed_id','?')} {d.get('side','?'):3} "
              f"edge={d.get('edge','?'):>5}  sig={d.get('signal_confirmed','?'):8} "
              f"risk={d.get('risk','?'):6}  {d.get('market','?')[:45]}")
        if d.get("thesis"):
            print(f"     {d['thesis'][:85]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", metavar="ID", help="hunt a specific seed ID")
    ap.add_argument("--show", action="store_true", help="show past dossiers")
    ap.add_argument("--once", action="store_true",
                    help="quiet watchdog mode (hunts active seeds)")
    a = ap.parse_args()
    if a.show:
        show(); return
    run(seed_filter=a.seed, verbose=not a.once)


if __name__ == "__main__":
    main()
