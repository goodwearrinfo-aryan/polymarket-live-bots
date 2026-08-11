#!/usr/bin/env python3
"""
Edge Gatekeeper — Autonomous BRAIN check before any new edge enters the book.

Before scalp_lab books a new leg or edge-hunt proposes a lock:
    >>> verdict = check_edge_against_brain(edge_type="basket_arb", description="...")
    >>> if verdict['allow']:
    ...     book_edge(edge)
    ... else:
    ...     log_rejection(verdict['reason'])

Queries the BRAIN knowledge graph to catch patterns killed before.
"""

import json
import subprocess
from pathlib import Path
from typing import TypedDict

VAULT = Path.home() / "Documents" / "PolymarketVault"
GRAPH_JSON = VAULT / "graphify-out" / "graph.json"


class EdgeVerdict(TypedDict):
    allow: bool
    reason: str
    confidence: float
    graveyard_match: str
    payer: str
    structural: bool


def query_brain(question: str) -> str:
    """Run graphify query and return result."""
    try:
        result = subprocess.run(
            ["graphify", "query", question],
            cwd=str(VAULT),
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.stdout or result.stderr
    except Exception as e:
        return f"Error querying: {e}"


def load_graph() -> dict:
    """Load the BRAIN graph locally for fast checks."""
    try:
        return json.loads(GRAPH_JSON.read_text(encoding="utf-8"))
    except Exception:
        return {"nodes": [], "links": []}


def find_graveyard_match(edge_type: str, description: str) -> tuple[str, float]:
    """Check if this pattern has been killed before."""
    graph = load_graph()
    nodes = {n["id"]: n for n in graph.get("nodes", [])}

    # Look for retraction nodes that match this edge family
    retractions = [
        n
        for n in graph.get("nodes", [])
        if n.get("type") == "retraction"
        and any(
            keyword.lower() in n.get("label", "").lower()
            for keyword in [edge_type, "retract"]
        )
    ]

    if retractions:
        ret = retractions[0]
        return (
            ret.get("label", "Unknown retraction"),
            ret.get("confidence", 0.8),
        )

    return ("No match in graveyard", 0.0)


def check_payer(edge_type: str) -> tuple[str, bool]:
    """Verify this edge has a named structural payer (uses BRAIN query + optional NVIDIA judgment)."""

    # First: Query BRAIN graph for graveyard knowledge
    brain_result = query_brain(f"Who structurally pays for {edge_type}? Why?")

    # Check BRAIN result keywords
    has_payer_brain = any(
        keyword in brain_result.lower()
        for keyword in [
            "payer",
            "structural",
            "settled",
            "data",
            "arb",
            "complete-set",
            "lock",
        ]
    )

    # Optional: Use NVIDIA nv-judge for deeper reasoning (free tier)
    try:
        nvidia_judgment = query_nvidia_payer(edge_type)
        has_payer_nvidia = nvidia_judgment.get("has_payer", False)
        payer_reason = nvidia_judgment.get("reason", "")

        # Combine BRAIN + NVIDIA verdicts
        has_payer = has_payer_brain or has_payer_nvidia
        combined_result = f"BRAIN: {brain_result[:100]} | NVIDIA: {payer_reason[:100]}"

        return (combined_result[:300], has_payer)
    except Exception:
        # Fallback to BRAIN-only if NVIDIA unavailable
        return (brain_result[:200], has_payer_brain)


def query_nvidia_payer(edge_type: str) -> dict:
    """Optional: Use NVIDIA nv-judge to assess payer structurally (free tier)."""
    try:
        import subprocess
        import json

        prompt = f"""Assess structurally: Does {edge_type} have a NAMED STRUCTURAL PAYER?

For example:
- Basket arb: Σask < 1 locks profit → payer is arbitrage mechanism itself
- Data-arb: Settled data determines outcome → payer is market inefficiency
- Taker-directional: Taker pays spread → payer is your forecast, not structure

Return JSON: {{"has_payer": bool, "reason": "structural payer or none"}}"""

        # Call nv-judge via llm_client if available
        result = subprocess.run(
            ["llm_client", "judge", "--schema", '{"has_payer": "bool", "reason": "string"}'],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=5,
        )

        if result.returncode == 0:
            return json.loads(result.stdout)
    except Exception:
        pass

    return {"has_payer": False, "reason": "unavailable"}


def is_structural(edge_type: str) -> tuple[bool, str]:
    """Is this structural (survives calibrated mids) or predictive (dies)?"""
    structural_keywords = [
        "basket",
        "arb",
        "complete-set",
        "data-arb",
        "settled",
        "data",
    ]
    predictive_keywords = [
        "fade",
        "momentum",
        "ta",
        "technical",
        "forecasting",
        "directional",
        "whale",
    ]

    edge_lower = edge_type.lower()
    is_struct = any(kw in edge_lower for kw in structural_keywords)
    is_pred = any(kw in edge_lower for kw in predictive_keywords)

    if is_pred:
        return False, f"{edge_type} is predictive (dies on calibrated mids)"

    if is_struct:
        return True, f"{edge_type} is structural (may survive)"

    return False, f"{edge_type} type unclear (assume predictive until proven)"


def check_edge_against_brain(edge_type: str, description: str = "") -> EdgeVerdict:
    """
    Comprehensive BRAIN check before booking an edge.

    Returns: {allow, reason, confidence, graveyard_match, payer, structural}
    """

    # 1. Check graveyard
    graveyard_match, match_conf = find_graveyard_match(edge_type, description)

    # 2. Check payer
    payer_text, has_payer = check_payer(edge_type)

    # 3. Check structural vs predictive
    is_struct, struct_reason = is_structural(edge_type)

    # 4. Render verdict
    verdict: EdgeVerdict = {
        "allow": False,
        "reason": "",
        "confidence": 0.0,
        "graveyard_match": graveyard_match,
        "payer": payer_text,
        "structural": is_struct,
    }

    # Decision logic
    if match_conf > 0.7:  # High confidence match to retraction
        verdict["allow"] = False
        verdict[
            "reason"
        ] = f"Graveyard match: {graveyard_match} (conf: {match_conf:.2f})"
        verdict["confidence"] = 1.0 - match_conf

    elif not is_struct:
        verdict["allow"] = False
        verdict["reason"] = struct_reason
        verdict["confidence"] = 0.9

    elif not has_payer:
        verdict["allow"] = False
        verdict["reason"] = f"No named payer: {edge_type}"
        verdict["confidence"] = 0.8

    else:
        # Passed all checks
        verdict["allow"] = True
        verdict["reason"] = f"{edge_type} cleared: structural + named payer"
        verdict["confidence"] = 0.7

    return verdict


def log_edge_evaluation(edge_type: str, verdict: EdgeVerdict, result: str):
    """Append edge evaluation to log."""
    log_path = Path.home() / "Documents" / "polymarket" / "edge_evaluations.jsonl"
    from datetime import datetime

    entry = {
        "timestamp": datetime.now().isoformat(),
        "edge_type": edge_type,
        "verdict": verdict,
        "result": result,
    }
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python brain_edge_gatekeeper.py <edge_type> [description]")
        sys.exit(1)

    edge_type = sys.argv[1]
    description = sys.argv[2] if len(sys.argv) > 2 else ""

    verdict = check_edge_against_brain(edge_type, description)

    print(f"\n{'='*60}")
    print(f"EDGE EVALUATION: {edge_type}")
    print(f"{'='*60}\n")
    print(f"Allow: {verdict['allow']}")
    print(f"Reason: {verdict['reason']}")
    print(f"Confidence: {verdict['confidence']:.2f}\n")
    print(f"Graveyard: {verdict['graveyard_match']}")
    print(f"Payer: {verdict['payer'][:100]}...\n")
    print(f"Structural: {verdict['structural']}\n")
    print(f"{'='*60}\n")

    result = "APPROVED" if verdict["allow"] else "REJECTED"
    log_edge_evaluation(edge_type, verdict, result)
    print(f"Logged to edge_evaluations.jsonl\n")

    sys.exit(0 if verdict["allow"] else 1)
