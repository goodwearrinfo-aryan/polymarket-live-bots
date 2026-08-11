#!/usr/bin/env python3
"""
council.py — multi-perspective agree/disagree council for the brain (2026-06-15).

Give it a CLAIM. A panel of distinct perspectives each votes AGREE / DISAGREE /
UNSURE with a one-line reason. The council records WHERE they agree and where
they DISAGREE, and returns a verdict:
  CONSENSUS-FOR / CONSENSUS-AGAINST / CONTESTED.

Votes come from an agent runtime (Claude Code) running each perspective; this
module defines the panel, aggregates the votes, and writes the verdict note into
the brain. Writes ONLY under <vault>/brain/Council/ (hard safety guard) — never
touches BRAIN.md / analyst.json or anything in ~/Documents/polymarket.

  python3 council.py "<claim>" --verdicts verdicts.json
  # verdicts.json: {"Quant":["AGREE","reason"], "Skeptic":["DISAGREE","reason"], ...}
"""
import json, os, re, sys
from datetime import datetime, timezone
from pathlib import Path

VAULT = Path(os.path.expanduser("~/Documents/PolymarketVault"))
BRAIN = VAULT / "brain"
COUNCIL = BRAIN / "Council"

# Five distinct perspectives, chosen to actually pull in different directions.
PERSPECTIVES = {
    "Quant":   "statistics, sample size, bootstrap CI, calibration — agree only if statistically defensible",
    "Skeptic": "default to null / no-edge; agree only if the claim survives hard refutation",
    "Trader":  "execution reality — spread, slippage, fills, liquidity, gap-through",
    "Bull":    "opportunity & upside — the strongest honest case FOR acting",
    "Ops":     "safety, correlation, downside, hard rules (paper-only, >=30 exits, controls lose)",
}


def aggregate(verdicts):
    votes = {p: (verdicts.get(p) or ["UNSURE", "(no vote)"]) for p in PERSPECTIVES}
    ag = [p for p, (v, _) in votes.items() if str(v).upper() == "AGREE"]
    dis = [p for p, (v, _) in votes.items() if str(v).upper() == "DISAGREE"]
    n = len(PERSPECTIVES)
    if len(ag) >= n - 1:
        decision = "CONSENSUS-FOR"
    elif len(dis) >= n - 1:
        decision = "CONSENSUS-AGAINST"
    else:
        decision = "CONTESTED"
    return decision, votes, ag, dis


def write_note(claim, decision, votes, ag, dis):
    COUNCIL.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", claim.lower()).strip("-")[:60] or "claim"
    p = COUNCIL / f"{slug}.md"
    if not str(p.resolve()).startswith(str(BRAIN.resolve())):
        raise RuntimeError(f"REFUSED unsafe write: {p}")
    L = ["---", "type: council-verdict", "tags: [council]",
         f"decided_at: {datetime.now(timezone.utc).isoformat()}", "---", "",
         f"# Council: {claim}", "",
         f"**Verdict: {decision}**  ({len(ag)} agree / {len(dis)} disagree / "
         f"{len(PERSPECTIVES)-len(ag)-len(dis)} unsure)", "",
         "| Perspective | Vote | Reason |", "|---|---|---|"]
    for pers, (v, r) in votes.items():
        L.append(f"| {pers} | {str(v).upper()} | {r} |")
    L += ["", "## Where they agree", "- " + (", ".join(ag) or "—"),
          "", "## Where they disagree", "- " + (", ".join(dis) or "—"),
          "", "_Related:_ [[index]]"]
    p.write_text("\n".join(L))
    return p


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    claim = args[0] if args else "(no claim provided)"
    verdicts = {}
    if "--verdicts" in sys.argv:
        vf = sys.argv[sys.argv.index("--verdicts") + 1]
        if os.path.exists(vf):
            verdicts = json.load(open(vf))
    decision, votes, ag, dis = aggregate(verdicts)
    p = write_note(claim, decision, votes, ag, dis)
    print(f"{decision}  ({len(ag)} agree / {len(dis)} disagree)  -> {p}")
    for pers, (v, r) in votes.items():
        print(f"  {pers:8s} {str(v).upper():8s} {r}")


if __name__ == "__main__":
    main()
