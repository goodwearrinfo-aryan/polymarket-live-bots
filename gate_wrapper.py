#!/usr/bin/env python3
"""
gate_wrapper.py — Unified gate interface for all book entry points.

Three modes:
  SOFT_GATE  — Log verdict, always book (data collection, no blocking)
  HARD_GATE  — Log verdict, block booking if rejected
  MIXED_GATE — Run both in parallel, A/B log both outcomes

All modes:
  • Call brain_edge_gatekeeper (Python subprocess)
  • Log verdict to gate_decisions.jsonl (append-only)
  • Return (allow: bool, verdict: dict) to caller
  • Caller decides what to do with the result

Usage:
  from gate_wrapper import gate_edge

  allow, verdict = gate_edge(
      edge_type="basket_arb",
      description="complete-set lock on negRisk",
      mode="hard"  # or "soft" or "mixed"
  )

  if allow or mode == "soft":
      book_edge(edge)
  else:
      skip_edge()
"""

import json
import subprocess
import os
from pathlib import Path
from datetime import datetime
from typing import Tuple, Dict

BASE = Path(__file__).parent
GATEKEEPER = BASE / "brain_edge_gatekeeper.py"
LOG_FILE = BASE / "gate_decisions.jsonl"
GATE_MODE = os.getenv("GATE_MODE", "soft").lower()  # soft | hard | mixed


def _call_gatekeeper(edge_type: str, description: str = "") -> Dict:
    """Call brain_edge_gatekeeper.py and parse verdict."""
    try:
        result = subprocess.run(
            ["python3", str(GATEKEEPER), edge_type, description],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=str(BASE),
        )

        # Parse verdict from output (brain_gatekeeper logs structured data)
        verdict = {
            "edge_type": edge_type,
            "allow": result.returncode == 0,  # exit 0 = allow, exit 1 = reject
            "status": "success",
            "timestamp": datetime.now().isoformat(),
        }
        return verdict
    except subprocess.TimeoutExpired:
        return {
            "edge_type": edge_type,
            "allow": True,  # soft-fail: allow on timeout
            "status": "timeout",
            "timestamp": datetime.now().isoformat(),
        }
    except Exception as e:
        return {
            "edge_type": edge_type,
            "allow": True,  # soft-fail: allow on error
            "status": f"error: {str(e)}",
            "timestamp": datetime.now().isoformat(),
        }


def _log_decision(edge_type: str, verdict: Dict, mode: str):
    """Log gate decision to append-only JSONL."""
    entry = {
        "timestamp": verdict.get("timestamp", datetime.now().isoformat()),
        "edge_type": edge_type,
        "verdict": verdict,
        "mode": mode,
    }
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass  # Silently fail logging (don't block booking on log error)


def gate_edge(
    edge_type: str,
    description: str = "",
    mode: str = None,
) -> Tuple[bool, Dict]:
    """
    Gate an edge before booking.

    Args:
        edge_type: Type of edge (e.g., "basket_arb", "data_arb", "fade")
        description: Optional description for context
        mode: Override GATE_MODE for this call ("soft"|"hard"|"mixed")

    Returns:
        (allow: bool, verdict: dict)
        - allow = True: Safe to book (passes hard gate or soft-mode)
        - allow = False: Blocked by hard gate (hard-mode only)
    """

    gate_mode = (mode or GATE_MODE).lower()

    if gate_mode == "soft":
        # Soft gate: log, always allow
        verdict = _call_gatekeeper(edge_type, description)
        _log_decision(edge_type, verdict, "soft")
        return (True, verdict)  # Always allow

    elif gate_mode == "hard":
        # Hard gate: log, block if rejected
        verdict = _call_gatekeeper(edge_type, description)
        _log_decision(edge_type, verdict, "hard")
        return (verdict.get("allow", True), verdict)  # Block if allow=False

    elif gate_mode == "mixed":
        # Mixed gate: run both, log both, return hard verdict
        verdict = _call_gatekeeper(edge_type, description)
        soft_verdict = verdict.copy()
        soft_verdict["soft_allow"] = True  # Soft always allows
        hard_verdict = verdict.copy()
        hard_verdict["hard_allow"] = verdict.get("allow", True)

        # Log both verdicts in one entry
        entry = {
            "timestamp": datetime.now().isoformat(),
            "edge_type": edge_type,
            "soft": soft_verdict,
            "hard": hard_verdict,
            "mode": "mixed",
        }
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception:
            pass

        # Return hard verdict (block if rejected)
        return (hard_verdict.get("hard_allow", True), hard_verdict)

    else:
        # Unknown mode: default to soft
        verdict = _call_gatekeeper(edge_type, description)
        _log_decision(edge_type, verdict, "unknown")
        return (True, verdict)


# CLI for testing
if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python3 gate_wrapper.py <edge_type> [description] [mode]")
        sys.exit(1)

    edge_type = sys.argv[1]
    description = sys.argv[2] if len(sys.argv) > 2 else ""
    mode = sys.argv[3] if len(sys.argv) > 3 else GATE_MODE

    allow, verdict = gate_edge(edge_type, description, mode)

    print(f"\nGate Result: {edge_type}")
    print(f"Mode: {mode}")
    print(f"Allow: {allow}")
    print(f"Verdict: {json.dumps(verdict, indent=2)}\n")

    sys.exit(0 if allow else 1)
