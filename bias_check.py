#!/usr/bin/env python3
"""
bias_check.py — pm-bias-detective as a standalone tool on the free LLM.
Audits the PROJECT'S OWN live reasoning for self-deception: over-confidence, thin-positive-
looks-like-edge, narrative-fitting, correlated-bets-treated-as-independent, sunk-cost on dead
legs, relitigating settled dead-ends, over-claiming on a single data point. Every other agent
audits the data; this audits the person reading it. Zero Claude-Code cost.

Reads the live analyst book (analyst_positions.json) + BRAIN hard rules/lessons, hands them to
the LLM, gets back named self-deceptions + correctives. Run: python3 bias_check.py [--mock]
"""
import os, sys, json, datetime, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import llm_client

BASE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(BASE, "bias_check_log.jsonl")
VAULT = os.path.expanduser("~/Documents/PolymarketVault/Reports/bias_check_latest.md")

SHAPE = ('{"findings": [{"target": "the specific claim/position", "bias": "confirmation|thin-positive|'
         'narrative-fit|correlation-blindness|sunk-cost|over-claim|relitigation", '
         '"why": "why it is self-deception", "corrective": "the specific fix"}], '
         '"overall": "one-line honest read of the book\'s epistemic health"}')

SYSTEM = (
    "You are a skeptical meta-auditor of a PAPER prediction-market operator's OWN reasoning. The "
    "project's hard rules: optimize DOLLARS not win-rate; an edge needs >=30 exits + bootstrap CI>0 "
    "+ DSR + controls-lose before it's real; favorite-longshot bias is structurally DEAD on this "
    "venue (gap-through + spread eat it); correlated bets are NOT independent draws. Given the live "
    "analyst book below, name where the operator is FOOLING HIMSELF RIGHT NOW. Look hard for: "
    "treating a single high-conviction candidate as a proven edge; correlated positions counted as "
    "independent (inflating effective n); narrative-fitting a thesis to a position already taken; "
    "over-confidence vs the evidence quality; capital-inefficient tail-sells dressed as edges. Be "
    "specific and unsparing — cite the exact position. If the book is honest, say so plainly; do not "
    "manufacture findings."
)


def gather():
    book = {}
    try:
        book = json.load(open(os.path.join(BASE, "analyst_positions.json")))
    except Exception:
        pass
    positions = book.get("positions", [])
    # compact each position to the bias-relevant fields
    compact = [{k: p.get(k) for k in ("q", "side", "entry_price", "true_prob", "edge_pct",
                                      "confidence", "risk", "resolves", "thesis")} for p in positions]
    return {"n_positions": len(compact), "book": compact}


def run(mock=False):
    ctx = gather()
    user = ("LIVE ANALYST BOOK (paper positions, each with thesis + claimed edge + confidence):\n"
            + json.dumps(ctx["book"], indent=1)[:6000])
    if mock:
        verdict = {"findings": [{"target": "[mock]", "bias": "over-claim", "why": "[mock]",
                                 "corrective": "[mock]"}], "overall": "[mock] pipeline test"}
    else:
        try:
            verdict = llm_client.judge(system=SYSTEM, user=user, schema_hint=SHAPE, temperature=0.2)
        except Exception as e:
            verdict = {"error": str(e), "findings": []}
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    result = {"ts": ts, "n_positions": ctx["n_positions"], "mock": mock, "verdict": verdict}
    _persist(result)
    return result


def _persist(r):
    try:
        open(LOG, "a").write(json.dumps({**r, **llm_client.provenance()}) + "\n")
    except Exception as e:
        print(f"[log FAIL] {e}", file=sys.stderr)
    v = r["verdict"]
    try:
        lines = [f"# Bias check — analyst book ({r['n_positions']} positions)", "",
                 f"_{r['ts']}_" + ("  _(mock)_" if r["mock"] else ""), "",
                 f"**Overall:** {v.get('overall','')}", "", "## Findings"]
        for f in (v.get("findings") or []):
            lines += [f"- **[{f.get('bias','')}]** {f.get('target','')}",
                      f"    - why: {f.get('why','')}",
                      f"    - fix: {f.get('corrective','')}"]
        if not (v.get("findings")):
            lines.append("- (none — book reads honest, or check failed)")
        os.makedirs(os.path.dirname(VAULT), exist_ok=True)
        open(VAULT, "w").write("\n".join(lines) + "\n")
    except Exception as e:
        print(f"[vault FAIL] {e}", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--mock", action="store_true")
    a = ap.parse_args()
    if not a.mock and not llm_client.configured():
        print("⚠️ No LLM endpoint (set LLM_* in .env) — or use --mock."); sys.exit(2)
    r = run(mock=a.mock)
    v = r["verdict"]
    print(f"\nBIAS CHECK — {r['n_positions']} positions  [{'mock' if r['mock'] else 'live'}]")
    print(f"Overall: {v.get('overall','')}")
    for f in (v.get("findings") or []):
        print(f"  [{f.get('bias','')}] {str(f.get('target',''))[:55]}")
        print(f"     fix: {str(f.get('corrective',''))[:80]}")


if __name__ == "__main__":
    main()
