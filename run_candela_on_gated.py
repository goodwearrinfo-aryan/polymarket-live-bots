#!/usr/bin/env python3
"""
run_candela_on_gated.py — point candela_args at the REAL data-gated markets.

Closes the loop: analyst_data_gate.py already finds markets with a fetchable resolving
series + builds a `dossier`; this pulls those rows and runs candela_args.generate_argument
on them with the REAL question, dossier (as retrieved evidence), live YES price (entry),
and market_id (so judge_panel fetches the real spread/resolution). Read-only, paper-safe.

  python3 run_candela_on_gated.py --list                 # list the gated markets
  python3 run_candela_on_gated.py --match "hormuz end"   # run candela_args on matches
  python3 run_candela_on_gated.py --top 3                # run the 3 highest-volume gated mkts
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
from candela_args import generate_argument, _print

LOG = HERE / "analyst_data_gate_log.jsonl"

SOURCE_CATEGORY = {"crypto-threshold": "crypto", "fed": "macro",
                   "portwatch": "geopolitics", "bls": "macro"}


def latest_rows():
    rec = None
    for line in LOG.read_text().splitlines():
        if line.strip():
            rec = json.loads(line)  # keep the last
    rows = [r for r in (rec or {}).get("rows", []) if r.get("data_ok")]
    return rows, (rec or {}).get("ts")


def pick(rows, match=None, top=None):
    if match:
        m = match.lower()
        sel = [r for r in rows if m in (r.get("q", "").lower())]
    else:
        sel = sorted(rows, key=lambda r: -(r.get("vol") or 0))[: (top or 1)]
    return sel


def run_row(r, gate=True):
    src = r.get("source", "?")
    cat = SOURCE_CATEGORY.get(src, "other")
    yes = r.get("yes")
    arg = generate_argument(
        question=r.get("q", ""),
        resolution=None,                     # judge_panel will fetch the real text via market_id
        data=r.get("dossier"),               # the data gate's real fetched series = retrieved evidence
        category=cat,
        entry_price=yes,
        market_id=r.get("cid"),
        gate=gate,
    )
    _print(arg)
    # data-vs-price divergence note (the actual point of the data-arb track)
    p = arg.get("p_estimate")
    if isinstance(p, (int, float)) and isinstance(yes, (int, float)):
        diff = p - yes
        tag = ("LLM sees YES UNDERPRICED" if diff > 0.05 else
               "LLM sees YES OVERPRICED" if diff < -0.05 else "≈ in line with market")
        print(f"\nDIVERGENCE: model p(YES)={p}  vs  market YES={yes}  -> {diff:+.3f}  [{tag}]")
        print("  (reminder: divergence is a LEAD, not an edge — needs the data gate's settled-series"
              " confirmation + judge_panel + DSR/control before any size.)")
    return arg


def scan(rows, n):
    """Run candela_args on the top-n gated markets; print ONE ranked divergence table.
    Divergence (model p vs market YES) is a LEAD to check against data, never a trade."""
    sel = sorted(rows, key=lambda r: -(r.get("vol") or 0))[:n]
    out = []
    for r in sel:
        cat = SOURCE_CATEGORY.get(r.get("source"), "other")
        yes = r.get("yes")
        try:
            a = generate_argument(question=r.get("q", ""), resolution=None, data=r.get("dossier"),
                                  category=cat, entry_price=yes, market_id=r.get("cid"), gate=True)
            p = a.get("p_estimate")
            diff = (p - yes) if isinstance(p, (int, float)) and isinstance(yes, (int, float)) else None
            g = a.get("gate", {})
            out.append({"q": r.get("q", ""), "src": r.get("source"), "yes": yes, "p": p,
                        "diff": diff, "df": a.get("data_backed_fraction"),
                        "edge_cand": a.get("is_edge_candidate"), "tail": a.get("tail_miscalibration"),
                        "gate": ("SURVIVED" if g.get("survived") else "rejected") if "survived" in g else "?"})
        except Exception as e:
            out.append({"q": r.get("q", ""), "src": r.get("source"), "yes": yes, "p": None,
                        "diff": None, "df": None, "edge_cand": None, "gate": f"ERR:{str(e)[:30]}"})
    out.sort(key=lambda x: -(abs(x["diff"]) if isinstance(x["diff"], (int, float)) else -1))
    print(f"\n{'|Δ|':>5} {'p(YES)':>7} {'mkt':>6} {'diff':>7} {'data%':>6} {'gate':>9}  question")
    print("-" * 96)
    for x in out:
        d = x["diff"]
        flag = ""
        if isinstance(d, (int, float)):
            flag = " ⚠UNDERPRICED?" if d > 0.05 else " ⚠OVERPRICED?" if d < -0.05 else ""
        if x.get("tail"):
            flag += " [TAIL-MISCAL✗]"
        ad = f"{abs(d):.3f}" if isinstance(d, (int, float)) else "  -"
        ps = f"{x['p']:.2f}" if isinstance(x["p"], (int, float)) else "  -"
        ys = f"{x['yes']:.2f}" if isinstance(x["yes"], (int, float)) else "  -"
        ds = f"{d:+.3f}" if isinstance(d, (int, float)) else "   -"
        df = f"{x['df']:.0%}" if isinstance(x["df"], (int, float)) else " -"
        print(f"{ad:>5} {ps:>7} {ys:>6} {ds:>7} {df:>6} {x['gate']:>9}  {x['q'][:46]}{flag}")
    print("\nHONEST READ: a big |Δ| usually means the free LLM is WRONG, not the market (calibrated mids;")
    print("argument-only = 23-mkt null). Each ⚠ is a QUESTION for the data gate, never a trade. The real")
    print("Hormuz edge was NO (traffic stays disrupted) — the LLM leaning YES there is the trap, not a signal.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--match")
    ap.add_argument("--top", type=int)
    ap.add_argument("--scan", type=int, help="run top-N gated markets, print one ranked divergence table")
    ap.add_argument("--no-gate", action="store_true")
    args = ap.parse_args()

    rows, ts = latest_rows()
    print(f"latest data-gate run {ts}: {len(rows)} markets with a fetchable resolving series\n")
    if args.scan:
        scan(rows, args.scan)
        return
    if args.list or (not args.match and not args.top):
        for r in sorted(rows, key=lambda r: -(r.get("vol") or 0)):
            print(f"  yes={r.get('yes')!s:<7} vol={r.get('vol',0):>10}  [{r.get('source')}]  {r.get('q','')[:64]}")
        if args.list:
            return
        print("\n(pass --match \"<text>\" or --top N to run candela_args on them)")
        return

    sel = pick(rows, match=args.match, top=args.top)
    if not sel:
        print("no gated market matched."); return
    print(f"running candela_args on {len(sel)} market(s)...\n")
    for r in sel:
        run_row(r, gate=not args.no_gate)
        print("\n" + "#" * 80 + "\n")


if __name__ == "__main__":
    main()
