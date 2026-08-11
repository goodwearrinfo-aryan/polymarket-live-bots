#!/usr/bin/env python3
"""internal_consistency_agent.py — logical price constraint violations across related markets.

Finds groups of Polymarket markets where LOGIC demands a price relationship but
the prices violate it. These are pure structural arbitrage with no directional risk:

  MONOTONE VIOLATION: "BTC above $70k" priced HIGHER than "BTC above $60k"
    (higher threshold must cost ≤ lower threshold — impossible otherwise)

  ELECTION UNDERSUM: "A wins" + "B wins" + "C wins" sum to < $0.88
    (one of them MUST win → basket arbitrage: buy all three)

  TEMPORAL INVERSION: "happens before June 30" priced HIGHER than "happens before July 31"
    (earlier deadline is a SUBSET of the later → must cost ≤)

  COMPLEMENT GAP: YES + NO for same market quoted at < $0.96 on CLOB
    (buy both sides, collect guaranteed $1 minus spread)

Claude's tool-use loop:
  list_markets(topic) → fetch_market for each → find logical groupings → flag violations
  scan_orderbooks to confirm the violation exists at executable prices

Output: consistency_violations.json  +  iMessage on STRONG violations (>2¢ gap)

Usage:
  python3 internal_consistency_agent.py         # full scan
  python3 internal_consistency_agent.py --once  # quiet watchdog mode
  python3 internal_consistency_agent.py --show  # show past violations
"""
import os, json, time, argparse
from pathlib import Path
from agent_toolkit import run_agent, TOOL_DEFS

HERE       = Path(__file__).resolve().parent
VIOLATIONS = HERE / "consistency_violations.json"

SYSTEM = """You are a logical consistency auditor for prediction markets. Your job is to find
situations where two or more related markets MUST obey a logical price constraint — and violate it.

These are NOT directional bets. They are pure structural arbitrage: the laws of probability
dictate a price relationship, and when it's violated, you can profit regardless of the outcome.

CONSTRAINT TYPES you search for:

1. MONOTONE CHAIN (threshold markets):
   "BTC reaches $70k by July" YES price MUST be ≤ "BTC reaches $60k by July" YES price
   because $70k can only happen if $60k was also reached first.
   VIOLATION: if $70k price > $60k price → buy $70k YES, sell $60k YES (or buy $60k NO)

   Look for: BTC dip/reach markets at different thresholds, election polling thresholds,
   "above X%" price targets, "more than N seats" election outcomes

2. ELECTION UNDERSUM (mutual exclusivity):
   "A wins" + "B wins" + "C wins" MUST sum to ≥ 1.00 (if MECE — exactly one wins)
   If sum < $0.95 → buy all legs, guaranteed $1 payout at resolution
   Look for: primary elections, championship brackets, multi-candidate races

3. TEMPORAL INVERSION:
   "X happens before date A" YES ≥ "X happens before date B" YES when A < B
   (earlier window is a subset → must be less likely or equal)
   VIOLATION: earlier deadline priced HIGHER than later deadline

4. COMPLEMENT GAP:
   YES_ask + NO_ask < $0.99 on CLOB → buy both sides, collect guaranteed spread
   (different from basket arb: this is within a single market's YES+NO)

Your tools:
- list_markets(topic, max_days=90): find candidate markets
- fetch_market(query): get YES price + resolution criteria
- scan_orderbooks(topic): verify executability at CLOB prices

SCAN TOPICS (cover all):
- bitcoin BTC price reach dip
- ethereum ETH price
- election president senate seats
- championship winner series

VIOLATION FORMAT:
---VIOLATION---
TYPE: MONOTONE | UNDERSUM | TEMPORAL | COMPLEMENT
SEVERITY: STRONG (>3¢ gap) | MODERATE (1-3¢) | WEAK (<1¢)
MARKET_A: <first market question>
PRICE_A: <YES price>
MARKET_B: <second market question>
PRICE_B: <YES price>
CONSTRAINT: <the logical rule being violated, e.g. "A must be ≤ B">
GAP: <price gap in cents, e.g. 5¢>
EXECUTABLE: YES | NO (can you actually trade both sides at CLOB?)
ACTION: <what to buy and sell to capture the arb>
RISK: <any scenario where this might not pay out — resolution ambiguity, etc>
---END---"""

TOPICS = [
    "bitcoin BTC price reach", "ethereum ETH price",
    "election president senate", "championship winner",
    "oil price barrel", "interest rate fed",
]


def scan(verbose: bool = True) -> list:
    user = (f"Scan for logical price constraint violations across these topic areas:\n"
            + "\n".join(f"- {t}" for t in TOPICS)
            + "\n\nFor each topic: list_markets() to find candidate markets. "
            f"Look for groups of markets that are logically related (same event, "
            f"different thresholds/dates/candidates). Check the prices for violations. "
            f"Use fetch_market() to read the exact resolution criteria — make sure "
            f"the logical relationship actually holds (read the words carefully). "
            f"Use scan_orderbooks() to verify the gap is executable at CLOB prices. "
            f"Only flag violations where: (a) the logical constraint is clear, "
            f"(b) the gap is real at executable prices, (c) you can articulate the "
            f"specific arb trade. Skip theoretical violations that can't be traded.")
    if verbose:
        print(f"[internal_consistency] scanning {len(TOPICS)} topic areas...")
    response = run_agent(SYSTEM, user, verbose=verbose, max_tokens=1600, max_iters=18)

    violations = []
    blocks = response.split("---VIOLATION---")[1:]
    for block in blocks:
        body = block.split("---END---")[0].strip()
        v = {"ts": time.strftime("%Y-%m-%d %H:%MZ", time.gmtime()),
             "type": None, "severity": None,
             "market_a": None, "price_a": None,
             "market_b": None, "price_b": None,
             "constraint": None, "gap": None,
             "executable": None, "action": None, "risk": None}
        for line in body.splitlines():
            line = line.strip()
            for field, key in [("TYPE:", "type"), ("SEVERITY:", "severity"),
                                ("MARKET_A:", "market_a"), ("PRICE_A:", "price_a"),
                                ("MARKET_B:", "market_b"), ("PRICE_B:", "price_b"),
                                ("CONSTRAINT:", "constraint"), ("GAP:", "gap"),
                                ("EXECUTABLE:", "executable"), ("ACTION:", "action"),
                                ("RISK:", "risk")]:
                if line.startswith(field):
                    v[key] = line[len(field):].strip()
        if v["type"] and v["market_a"]:
            violations.append(v)
    return violations


def _load():
    if VIOLATIONS.exists():
        try: return json.loads(VIOLATIONS.read_text())
        except Exception: pass
    return {"violations": []}


def _save(st):
    tmp = VIOLATIONS.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=2))
    tmp.replace(VIOLATIONS)


def _imessage(msg, recipient="+918449447444"):
    safe = msg.replace("\\","\\\\").replace('"','\\"').replace("\n","\\n")
    safe = "".join(c if ord(c) < 128 else " " for c in safe)
    os.system(f'osascript -e \'tell application "Messages" to send "{safe}" to buddy "{recipient}" of (service 1 whose service type is iMessage)\' 2>/dev/null')


def run(verbose: bool = True):
    violations = scan(verbose=verbose)
    st = _load()
    st["violations"].extend(violations)
    st["last_scan"] = time.strftime("%Y-%m-%d %H:%MZ", time.gmtime())
    _save(st)

    strong = [v for v in violations if v.get("severity") == "STRONG"
              and v.get("executable") == "YES"]
    if verbose:
        print(f"\n[internal_consistency] {len(violations)} violation(s), "
              f"{len(strong)} STRONG+executable → {VIOLATIONS}")
        for v in violations:
            print(f"  {v.get('severity','?'):8} {v.get('type','?'):12} "
                  f"gap={v.get('gap','?'):>5}  exec={v.get('executable','?'):3}  "
                  f"'{v.get('market_a','?')[:42]}'")
            if v.get("action"):
                print(f"     trade: {v['action'][:80]}")

    if strong:
        lines = [f"[consistency] {len(strong)} STRONG arb violation(s):"]
        for v in strong[:3]:
            lines.append(f"{v['type']} gap={v.get('gap','?')} '{v.get('market_a','?')[:40]}'")
            if v.get("action"):
                lines.append(f"  trade: {v['action'][:65]}")
        _imessage("\n".join(lines))


def show():
    st = _load()
    vs = st.get("violations", [])
    print(f"\n  Consistency violations: {len(vs)}\n")
    for v in vs[-10:]:
        print(f"  [{v.get('ts','')}] {v.get('severity','?'):8} {v.get('type','?'):12} "
              f"gap={v.get('gap','?'):>5} exec={v.get('executable','?')}  "
              f"'{v.get('market_a','?')[:48]}'")
        if v.get("constraint"):
            print(f"     rule: {v['constraint'][:80]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="quiet watchdog mode")
    ap.add_argument("--show", action="store_true")
    a = ap.parse_args()
    if a.show: show(); return
    run(verbose=not a.once)


if __name__ == "__main__":
    main()
