"""
analyst_scorecard.py — weekly/monthly edge tracking scorecard.

Aggregates:
  - belief_ledger calibration (n, Brier, DSR per leg)
  - analyst_verdicts.csv (3-lens gate verdicts)
  - control legs WR (sanity check)
  - live P&L by leg (dollars, not %)
  - edge mortality (legs retired, reason, date)

Outputs scorecard.plist (launchd iPhone widget) + scorecard.md (vault).

Usage:
  python3 analyst_scorecard.py        # run now
  python3 analyst_scorecard.py --weekly   # weekly snapshot
"""
import json, csv, sys, os
from pathlib import Path
from datetime import datetime, timezone, timedelta
import plistlib

PM = Path.home() / "Documents/polymarket"
VAULT = Path.home() / "Documents/PolymarketVault/Bot"
VAULT.mkdir(parents=True, exist_ok=True)

def load_belief_calibration():
    """Load belief_ledger calibration (from belief_ledger.py)."""
    try:
        import belief_ledger
        return belief_ledger.from_state()
    except Exception as e:
        return {"error": str(e)}

def load_state():
    """Load scalp_lab_state.json for live P&L."""
    try:
        return json.load(open(PM / "scalp_lab_state.json"))
    except Exception as e:
        return {}

def load_analyst_verdicts():
    """Load latest analyst gate verdicts."""
    try:
        rows = list(csv.DictReader(open(PM / "analyst_verdicts.csv")))
        return rows
    except Exception as e:
        return []

def load_control_legs():
    """Extract control leg stats (allin, coinflip, coindown)."""
    state = load_state()
    controls = {}

    for leg_name in ["allin", "coinflip", "coindown"]:
        book = state.get(leg_name, {})
        closed = book.get("closed", [])
        n = len(closed)
        if n == 0:
            controls[leg_name] = {"n": 0, "wr": None, "pnl": 0}
            continue

        wins = sum(1 for r in closed if (r.get("pnl_usdc") or 0) > 0)
        pnl = sum(r.get("pnl_usdc", 0) or 0 for r in closed)
        wr = (wins / n * 100) if n > 0 else 0

        controls[leg_name] = {"n": n, "wr": wr, "pnl": pnl}

    return controls

def build_scorecard(weekly=False):
    """Assemble scorecard: edge status, control health, live performance."""
    belief = load_belief_calibration()
    verdicts = load_analyst_verdicts()
    controls = load_control_legs()
    state = load_state()

    now = datetime.now(timezone.utc).isoformat()[:16]

    # Sort legs by n (sample size)
    legs_sorted = sorted(
        belief.items(),
        key=lambda kv: -kv[1].get("n", 0)
    ) if "error" not in belief else []

    # Gate status
    passed = sum(1 for _, cal in legs_sorted if cal.get("n", 0) >= 30)
    ci_positive = sum(1 for _, cal in legs_sorted if cal.get("n", 0) >= 30 and
                      cal.get("ci", [None, None])[0] is not None and
                      cal.get("ci", [None, None])[0] > 0)

    # P&L by leg
    pnl_by_leg = {}
    for leg_name, leg_data in state.items():
        if isinstance(leg_data, dict) and "closed" in leg_data:
            pnl = sum((r.get("pnl_usdc") or 0) for r in leg_data.get("closed", []))
            pnl_by_leg[leg_name] = pnl

    total_pnl = sum(pnl_by_leg.values())

    # Control sanity
    control_ok = all((controls[c].get("wr") or 51) <= 52 for c in controls)

    # Analyst-track CI — cluster-block bootstrap (honest, corrects pseudo-replication)
    analyst_track = {}
    try:
        import analyst_correlation
        book = json.load(open(PM / "analyst_positions.json"))
        analyst_track = analyst_correlation.cluster_block_bootstrap_ci(book.get("positions", []))
    except Exception as e:
        analyst_track = {"error": str(e)}

    scorecard = {
        "timestamp": now,
        "edge_status": {
            "legs_with_n30": passed,
            "ci_positive": ci_positive,
            "gate_ready": passed >= 1 and ci_positive >= 1 and control_ok,
        },
        "analyst_track": analyst_track,
        "control_health": controls,
        "control_ok": control_ok,
        "live_pnl": {
            "total": round(total_pnl, 2),
            "by_leg": {k: round(v, 2) for k, v in sorted(pnl_by_leg.items(), key=lambda kv: -abs(kv[1]))[:10]},
        },
        "top_legs": [
            {
                "name": name.upper(),
                "n": cal.get("n", 0),
                "brier": round(cal.get("brier", 0), 4),
                "gap": round(cal.get("calibration_gap", 0), 3),
                "ci": [round(x, 3) for x in cal.get("ci", [0, 0])] if cal.get("ci") else None,
                "dsr": round(cal.get("dsr", 0), 2),
            }
            for name, cal in legs_sorted[:8]
        ],
    }

    return scorecard

def write_scorecard_plist(scorecard):
    """Write scorecard.plist for iOS widget."""
    plist_data = {
        "timestamp": scorecard["timestamp"],
        "gate_ready": scorecard["edge_status"]["gate_ready"],
        "control_ok": scorecard["control_ok"],
        "total_pnl": scorecard["live_pnl"]["total"],
        "top_legs": str([l["name"] for l in scorecard["top_legs"][:3]]),
    }

    out_path = PM / "scorecard.plist"
    with open(out_path, "wb") as f:
        plistlib.dump(plist_data, f)

    return out_path

def write_scorecard_md(scorecard):
    """Write scorecard.md for Obsidian vault."""
    lines = [
        "# Analyst Scorecard",
        f"_Last update: {scorecard['timestamp']}_",
        "",
        "## Edge Gate Status",
        f"- **Legs n≥30**: {scorecard['edge_status']['legs_with_n30']}",
        f"- **CI>0**: {scorecard['edge_status']['ci_positive']}",
        f"- **Gate Ready**: {'✅ YES' if scorecard['edge_status']['gate_ready'] else '❌ NO'}",
        "",
        "## Analyst Track — Cluster-Block Bootstrap CI",
    ]

    at = scorecard.get("analyst_track", {})
    if at.get("error"):
        lines.append(f"- _unavailable: {at['error']}_")
    elif at.get("n_settled", 0) == 0:
        lines.append(f"- _{at.get('note', 'no settled positions yet')}_")
    else:
        lines.append(f"- **settled n**: {at['n_settled']} across {at['n_clusters']} driver cluster(s) | "
                     f"mean P&L ${at['mean_pnl']:+.4f}")
        lines.append(f"- **naive CI** (rows i.i.d., too tight): "
                     f"[{at['naive_ci'][0]:+.4f}, {at['naive_ci'][1]:+.4f}] | CI>0={at['ci_positive_naive']}")
        if at.get("degenerate"):
            lines.append(f"- **cluster CI**: ⚠️ {at['note']}")
        else:
            lines.append(f"- **cluster CI** (HONEST, blocks): "
                         f"[{at['cluster_ci'][0]:+.4f}, {at['cluster_ci'][1]:+.4f}] | CI>0={at['ci_positive_cluster']}")
            lines.append(f"- **pseudo-replication**: cluster CI {at['inflation']}× wider | "
                         f"effective n≈{at['effective_n']} vs {at['n_settled']} nominal")
    lines.extend([
        "",
        "## Control Legs (Sanity Check)",
    ])

    for cname, cstats in scorecard["control_health"].items():
        n = cstats.get("n", 0)
        wr = cstats.get("wr")
        pnl = cstats.get("pnl", 0)
        flag = "✅ LOSING" if wr is not None and wr <= 52 else ("⚠️ WINNING" if wr else "—")
        wr_str = f"{wr:.1f}%" if wr is not None else "—"
        lines.append(f"- **{cname}**: n={n}, WR={wr_str}, P&L=${pnl:+.2f} {flag}")

    lines.extend([
        "",
        "## Live P&L",
        f"**Total**: ${scorecard['live_pnl']['total']:+.2f}",
        "",
        "**Top 10 legs**:",
    ])

    for leg, pnl in list(scorecard["live_pnl"]["by_leg"].items())[:10]:
        lines.append(f"- {leg}: ${pnl:+.2f}")

    lines.extend([
        "",
        "## Top Legs by Sample Size (n)",
        "",
    ])

    for leg in scorecard["top_legs"]:
        ci_str = f"[{leg['ci'][0]:+.3f}, {leg['ci'][1]:+.3f}]" if leg["ci"] else "—"
        lines.append(f"### {leg['name']}")
        lines.append(f"- **n={leg['n']}** | Brier={leg['brier']:.4f} | gap={leg['gap']:+.3f} | CI {ci_str} | DSR {leg['dsr']:.2f}")

    lines.append("")

    (VAULT / "scorecard.md").write_text("\n".join(lines), encoding="utf-8")
    return VAULT / "scorecard.md"

if __name__ == "__main__":
    weekly = "--weekly" in sys.argv

    print("[analyst_scorecard] building...", end=" ", flush=True)
    scorecard = build_scorecard(weekly=weekly)

    try:
        plist_path = write_scorecard_plist(scorecard)
        print(f"✓ plist written")
    except Exception as e:
        print(f"⚠️ plist write failed: {e}")

    try:
        md_path = write_scorecard_md(scorecard)
        print(f"✓ markdown written to vault")
    except Exception as e:
        print(f"⚠️ markdown write failed: {e}")

    print(f"\nGate ready: {scorecard['edge_status']['gate_ready']}")
    print(f"Total P&L: ${scorecard['live_pnl']['total']:+.2f}")
    print(f"Control OK: {scorecard['control_ok']}")
