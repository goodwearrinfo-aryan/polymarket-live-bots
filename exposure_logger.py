#!/usr/bin/env python3
"""exposure_logger.py — READ-ONLY analysis logger (places NO trades). How is the open paper book concentrated by side (YES/NO) and category? Paper-only, stdlib only.
Usage: python3 exposure_logger.py once | report"""
import sys
import ports_analysis_common as C
NAME = "exposure"


def _bucket(positions):
    """Accumulate {n, stake, unreal} over a list of positions, guarding None."""
    n = len(positions)
    stake = 0.0
    unreal = 0.0
    for p in positions:
        stake += p.get("stake") or 0.0
        u = p.get("unreal")
        if u is not None:
            unreal += u
    return {"n": n, "stake": round(stake, 2), "unreal": round(unreal, 2)}


def once():
    d, age = C.load_ports()
    if d is None:
        print(NAME + ": no ports_data.json snapshot — skipped"); return
    positions = d.get("positions") or []
    headline = d.get("headline") or {}

    total_open = len(positions)
    gross_exp = round(sum((p.get("stake") or 0.0) for p in positions), 2)
    total_unreal = round(sum((p.get("unreal") or 0.0) for p in positions if p.get("unreal") is not None), 2)
    realized = headline.get("realized_pnl")

    by_side = {}
    for side in ("YES", "NO"):
        by_side[side] = _bucket([p for p in positions if (p.get("side") or "?") == side])

    by_cat = {}
    for cat, plist in C.group_by(positions, "cat").items():
        by_cat[cat] = _bucket(plist)

    rec = {
        "ts": C.ts_iso(),
        "gen": d.get("generated_at"),
        "total_open": total_open,
        "gross_exp": gross_exp,
        "total_unreal": total_unreal,
        "realized": realized,
        "by_side": by_side,
        "by_cat": by_cat,
    }
    C.append_jsonl(NAME, rec, dedup_key="gen")   # one sample per distinct snapshot
    report()
    yes, no = by_side["YES"], by_side["NO"]
    top = max(by_cat.items(), key=lambda kv: kv[1]["stake"]) if by_cat else ("?", {"stake": 0.0, "n": 0})
    print(NAME + ": " + str(total_open) + " open · gross " + C.usd(gross_exp)
          + " · unreal " + C.usd(total_unreal)
          + " · YES " + str(yes["n"]) + "/" + C.usd(yes["stake"])
          + " vs NO " + str(no["n"]) + "/" + C.usd(no["stake"])
          + " · top cat " + str(top[0]) + " " + C.usd(top[1]["stake"]))


def report():
    rows = C.read_jsonl(NAME)
    lines = ["# Portfolio Exposure & Concentration",
             "",
             "> READ-ONLY logger (no trades). Tracks open-book concentration by side (YES/NO) and category.",
             "> Updated " + C.ts_iso() + " · " + str(len(rows)) + " samples",
             ""]
    if not rows:
        lines += ["_No samples logged yet._"]
        C.write_report(NAME, lines)
        return

    last = rows[-1]
    by_side = last.get("by_side") or {}
    by_cat = last.get("by_cat") or {}

    lines += ["## Latest snapshot",
              "",
              "- Generated: " + str(last.get("gen")),
              "- Open positions: " + str(last.get("total_open")),
              "- Gross exposure (stake): " + C.usd(last.get("gross_exp")),
              "- Total unrealized: " + C.usd(last.get("total_unreal")),
              "- Realized P&L: " + C.usd(last.get("realized")),
              "",
              "## YES vs NO split",
              "",
              "| Side | n | Stake | Unreal |",
              "|------|---|-------|--------|"]
    for side in ("YES", "NO"):
        b = by_side.get(side) or {"n": 0, "stake": 0.0, "unreal": 0.0}
        lines.append("| " + side + " | " + str(b.get("n", 0)) + " | "
                     + C.usd(b.get("stake")) + " | " + C.usd(b.get("unreal")) + " |")

    lines += ["",
              "## Categories (by stake desc)",
              "",
              "| Category | n | Stake | Unreal |",
              "|----------|---|-------|--------|"]
    cats_sorted = sorted(by_cat.items(), key=lambda kv: kv[1].get("stake", 0.0), reverse=True)
    for cat, b in cats_sorted:
        lines.append("| " + str(cat) + " | " + str(b.get("n", 0)) + " | "
                     + C.usd(b.get("stake")) + " | " + C.usd(b.get("unreal")) + " |")

    lines += [""]
    if cats_sorted:
        top_cat, top_b = cats_sorted[0]
        gross = last.get("gross_exp") or 0.0
        pct = (100.0 * (top_b.get("stake") or 0.0) / gross) if gross else 0.0
        lines += ["> **Biggest-exposure category: `" + str(top_cat) + "`** — "
                  + C.usd(top_b.get("stake")) + " across " + str(top_b.get("n", 0))
                  + " positions (" + f"{pct:.1f}" + "% of gross)."]

    C.write_report(NAME, lines)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "once"
    if cmd == "once": once()
    elif cmd == "report": report()
    else: print("usage: exposure_logger.py once|report")


if __name__ == "__main__":
    main()
