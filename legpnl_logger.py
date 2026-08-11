#!/usr/bin/env python3
"""legpnl_logger.py — READ-ONLY analysis logger (places NO trades). Per-leg P&L trajectory: how each leg's realized + open mark-to-market drifts over time. Paper-only, stdlib only.
Usage: python3 legpnl_logger.py once | report"""
import sys
import ports_analysis_common as C
NAME = "legpnl"


def once():
    d, age = C.load_ports()
    if d is None:
        print(NAME + ": no ports_data.json snapshot — skipped"); return
    positions = d.get("positions") or []
    legs = d.get("legs") or []
    by = C.group_by(positions, "strat")
    legrecs = {}
    total_realized = 0.0
    total_unreal = 0.0
    for l in legs:
        name = l.get("name") or "?"
        leg_unreal = sum(p["unreal"] for p in by.get(name, []) if p.get("unreal") is not None)
        realized = round(float(l.get("realized") or 0.0), 2)
        legrecs[name] = {
            "open": l.get("open") or 0,
            "closed": l.get("closed") or 0,
            "realized": realized,
            "wr": l.get("wr"),
            "unreal": round(leg_unreal, 2),
        }
        total_realized += realized
        total_unreal += leg_unreal
    rec = {
        "ts": C.ts_iso(),
        "gen": d.get("generated_at"),
        "n_legs": len(legs),
        "total_realized": round(total_realized, 2),
        "total_unreal": round(total_unreal, 2),
        "legs": legrecs,
    }
    C.append_jsonl(NAME, rec, dedup_key="gen")   # one sample per distinct snapshot (no drift double-count)
    report()
    print(NAME + ": " + str(len(legs)) + " legs · realized " + C.usd(total_realized)
          + " · open MTM " + C.usd(total_unreal))


def report():
    rows = C.read_jsonl(NAME)
    lines = ["# Per-Leg P&L Trajectory",
             "",
             "> READ-ONLY logger (no trades). Snapshots every leg's realized P&L + open mark-to-market each run, to watch drift.",
             "> Updated " + C.ts_iso() + " · " + str(len(rows)) + " samples",
             ""]
    if not rows:
        lines += ["_No samples yet._"]
        C.write_report(NAME, lines)
        return
    last = rows[-1]
    legs = last.get("legs") or {}
    # markdown table of legs sorted by closed desc
    lines += ["## Latest snapshot · " + str(last.get("gen") or last.get("ts")), "",
              "Total realized **" + C.usd(last.get("total_realized")) + "** · open MTM **"
              + C.usd(last.get("total_unreal")) + "** · " + str(last.get("n_legs") or len(legs)) + " legs",
              "",
              "| Leg | Open | Closed | Realized | WR | Open MTM |",
              "|-----|-----:|-------:|---------:|---:|---------:|"]
    for name, l in sorted(legs.items(), key=lambda kv: (kv[1].get("closed") or 0), reverse=True):
        marker = " ⚠️ctrl" if name in C.CONTROLS else ""
        wr = l.get("wr")
        wr_s = (str(wr) + "%") if wr is not None else "—"
        lines.append("| " + name + marker
                     + " | " + str(l.get("open") or 0)
                     + " | " + str(l.get("closed") or 0)
                     + " | " + C.usd(l.get("realized"))
                     + " | " + wr_s
                     + " | " + C.usd(l.get("unreal")) + " |")
    # drift vs first sample
    if len(rows) >= 2:
        first = rows[0]
        drift = round((last.get("total_realized") or 0) - (first.get("total_realized") or 0), 2)
        lines += ["",
                  "## Drift",
                  "",
                  "- total realized: " + C.usd(first.get("total_realized")) + " (first) → "
                  + C.usd(last.get("total_realized")) + " (now) · **Δ " + C.usd(drift) + "** over "
                  + str(len(rows)) + " samples"]
    lines += ["",
              "_⚠️ctrl = marking-honesty control leg (must stay negative; positive realized => marking bug)._"]
    C.write_report(NAME, lines)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "once"
    if cmd == "once":
        once()
    elif cmd == "report":
        report()
    else:
        print("usage: legpnl_logger.py once|report")


if __name__ == "__main__":
    main()
