#!/usr/bin/env python3
"""pm_chain.py — the auto-GAUNTLET: run a candidate through the vet pipeline in one
pass and collect a combined verdict. (PAPER, read-only.)

Instead of 15 isolated agents, this chains the verifiers on ONE concrete target:
  pick a target wallet (newest ACTIVE sharp whale, or arg) →
    whale-vetter (copyable?) → [its latest slow market] resolution-checker (traps?)
    → counterparty-profiler (are you the dumb money?) → capacity-estimator ($ ceiling?)
  → a 3-line GO / NO-GO synthesis.
Target is picked DETERMINISTICALLY in Python (not parsed from LLM prose) so the chain
is robust. Each step uses the per-agent model override if configured.

Usage: python3 pm_chain.py [wallet_addr]
"""
import os, sys, json, time, urllib.request, urllib.parse
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pm_agents_24x7 as A
import pm_agent_llm as L
VAULT = os.path.expanduser("~/Documents/PolymarketVault/Reports")
UA = {"User-Agent": "Mozilla/5.0"}
COINFLIP = ("up or down", "up/down", " et?", "intraday")


def latest_slow_market(addr):
    """the wallet's most recent non-coinflip BUY conditionId (the chain's market target)."""
    try:
        u = "https://data-api.polymarket.com/trades?" + urllib.parse.urlencode({"user": addr, "limit": 40})
        rows = json.load(urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=12))
        for r in rows:
            if r.get("side") == "BUY" and not any(k in (r.get("title") or "").lower() for k in COINFLIP):
                return r.get("conditionId"), (r.get("title") or "")[:60]
    except Exception:
        pass
    return None, None


def step(name, target):
    """run one agent in the chain, return its verdict text (uses model override if set)."""
    a = A.AGENTS[name]
    data = a["g"](target)
    out, err = L.call_llm(a["s"], a["t"] + "\n\n=== DATA ===\n" + data, agent=name, max_tokens=500)
    return (out or f"(skipped: {err})").strip()


def main():
    wallet = sys.argv[1] if len(sys.argv) > 1 else (A._active_whales(1) or [None])[0]
    if not wallet:
        print("pm_chain: no target wallet"); return
    cid, title = latest_slow_market(wallet)
    log = [f"# pm-chain gauntlet — {time.strftime('%Y-%m-%d %H:%M')}",
           f"\n**target wallet:** `{wallet}`  ·  **market:** {title or '(none found)'}\n"]

    log.append("## 1 · whale-vetter (copyable?)\n" + step("whale-vetter", wallet))
    if cid:
        log.append("\n## 2 · resolution-checker (traps?)\n" + step("resolution-checker", cid))
        log.append("\n## 3 · counterparty-profiler (dumb money?)\n" + step("counterparty-profiler", cid))
    log.append("\n## 4 · capacity-estimator ($ ceiling?)\n" + step("capacity-estimator", None))
    log.append("\n## 5 · pm-quant (sizing / EV / exits-to-verdict?)\n" + step("quant", None))

    # final synthesis over the collected verdicts
    chain_so_far = "\n".join(log)
    synth, err = L.call_llm(
        "You are the gauntlet judge. Given the 4 vet-stage verdicts on one candidate, output a 3-line GO / NO-GO / NEEDS-DATA call: which stage is the binding constraint and why. Terse, no reprinting.",
        chain_so_far[:6000], agent="edge-refuter", max_tokens=300)
    log.append("\n## VERDICT\n" + (synth if not err else f"_(synthesis skipped: {err})_"))

    try:
        os.makedirs(VAULT, exist_ok=True)
        open(os.path.join(VAULT, "agent_chain.md"), "w").write("\n".join(log))
    except Exception:
        pass
    print(f"pm-chain: ran gauntlet on {wallet[:10]} ({'market ok' if cid else 'no market'}) → agent_chain.md")


if __name__ == "__main__":
    main()
