#!/usr/bin/env python3
"""
side_systems_gate.py — gatekeeper for OUT-OF-POLYMARKET side systems.

Why this exists: dsr_gate.py is tied to scalp_lab_state.json and polymarket
leg names. The crypto trader is a SIDE system (different state file,
different scoring bar). Reading the trader's verdict from the frontmatter
of Edges/crypto-trader.md (written by obsidian_snapshot.py) keeps the two
systems decoupled.

Reads: ~/Documents/PolymarketVault/Edges/crypto-trader.md (frontmatter only)
Writes: ~/Documents/PolymarketVault/Reports/agent_side_systems_gate.md

Output is one line per side system: "🟡/🟢/🔴 verdict — n_fills, realized_pnl".

Run:  python3 side_systems_gate.py           # one-shot
       python3 side_systems_gate.py --watch  # 60s loop (for watchdog integration)
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

VAULT = Path.home() / "Documents" / "PolymarketVault"
EDGES_DIR = VAULT / "Edges"
REPORT_PATH = VAULT / "Reports" / "agent_side_systems_gate.md"


def parse_frontmatter(text: str) -> dict:
    """Tiny YAML-ish parser for the lines we control. NO external deps."""
    out: dict = {}
    m = re.search(r"^---\n(.*?)\n---", text, re.DOTALL)
    if not m:
        return out
    for line in m.group(1).splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def score_side_system(edge_note: Path) -> tuple[str, str]:
    """Return (verdict_emoji, line_for_report) for one side-system edge note."""
    try:
        text = edge_note.read_text(encoding="utf-8")
    except Exception as e:
        return ("⚠️", f"- **{edge_note.stem}** — read error: {e}")
    fm = parse_frontmatter(text)
    if fm.get("type") != "edge":
        return ("⚪", f"- **{edge_note.stem}** — not an edge (type={fm.get('type')})")

    status = fm.get("status", "?")
    verdict = fm.get("verdict", "")
    updated = fm.get("updated", "")

    # Pull n_fills out of the verdict if present (snapshot writes "n=8 exits ...")
    n_match = re.search(r"n=(\d+)", verdict)
    n = int(n_match.group(1)) if n_match else None

    if status == "accumulating":
        emoji = "🟡" if n is None or n < 30 else "🟢"
    elif status == "live":
        emoji = "🟢"
    elif status == "dead" or status == "rejected":
        emoji = "❌"
    else:
        emoji = "⚪"

    line = f"- {emoji} **{edge_note.stem}** — status={status} · verdict={verdict} · updated={updated}"
    return (emoji, line)


def render_report(lines: list[str]) -> str:
    header = [
        "---",
        f"updated: {datetime.now(timezone.utc).isoformat()[:16]}",
        "type: agent-report",
        "tags: [gatekeeper, side-systems, dsr-gate]",
        "---",
        "",
        "# Side Systems Gate",
        "",
        "> One-shot read of `Edges/*-side-system` notes. Same gate bar as ",
        "> polymarket legs (n≥30 + CI>0 + control-loses) but separate state.",
        "",
        "## Side systems",
        "",
    ]
    return "\n".join(header + lines + ["", "## Sources", "", "- Edges/crypto-trader.md (written by `obsidian_snapshot.py::snapshot_crypto_trader` every 6h)", "- Edges/*-side-system.md (other side-system edges as added)", ""])


def scan_once() -> int:
    """One pass over Edges/*.md. Returns 0 on success, 1 on error."""
    if not EDGES_DIR.exists():
        print(f"side_systems_gate: {EDGES_DIR} missing", file=sys.stderr)
        return 1
    lines: list[str] = []
    for note in sorted(EDGES_DIR.glob("*.md")):
        # The trader is tagged `side-system` in frontmatter. Other side-system
        # edges (if added later) should tag themselves the same way.
        try:
            text = note.read_text(encoding="utf-8")
        except Exception:
            continue
        fm = parse_frontmatter(text)
        if "side-system" not in fm.get("tags", ""):
            continue
        _, line = score_side_system(note)
        lines.append(line)

    if not lines:
        lines = ["_No side-system edges registered yet._"]

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(render_report(lines), encoding="utf-8")
    print(f"side_systems_gate: wrote {REPORT_PATH} ({len(lines)} side systems)")
    return 0


def main() -> None:
    p = argparse.ArgumentParser(description="Side-systems gatekeeper")
    p.add_argument("--watch", action="store_true",
                   help="Loop every 60s (for watchdog integration)")
    args = p.parse_args()

    if args.watch:
        while True:
            scan_once()
            time.sleep(60.0)
    else:
        sys.exit(scan_once())


if __name__ == "__main__":
    main()