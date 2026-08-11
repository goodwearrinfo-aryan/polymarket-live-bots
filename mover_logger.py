#!/usr/bin/env python3
"""mover_logger.py — READ-ONLY analysis logger (places NO trades). Which open positions are
moving (mark-to-market) and do movers persist? Paper-only, stdlib only.
Usage: python3 mover_logger.py once | report"""
import sys
import statistics
import ports_analysis_common as C
NAME = "mover"


def _slim(p):
    """A compact mover row: who, how it's positioned, and the live move."""
    return {
        "sym": p.get("sym"),
        "strat": p.get("strat"),
        "side": p.get("side"),
        "entry": round(p["entry"], 4) if p.get("entry") is not None else None,
        "cur": round(p["cur"], 4) if p.get("cur") is not None else None,
        "unreal": round(p["unreal"], 2) if p.get("unreal") is not None else None,
        "unreal_pct": round(p["unreal_pct"], 2) if p.get("unreal_pct") is not None else None,
    }


def once():
    d, age = C.load_ports()
    if d is None:
        print(NAME + ": no ports_data.json snapshot — skipped"); return
    # mark-to-market universe = positions with a live unreal (skip None: unpriced/stale)
    m = [p for p in (d.get("positions") or []) if p.get("unreal") is not None]
    ups = sum(1 for p in m if p["unreal"] > 0)
    downs = sum(1 for p in m if p["unreal"] < 0)
    flat = len(m) - ups - downs
    breadth = {"up": ups, "down": downs, "flat": flat}
    median_unreal = round(statistics.median([p["unreal"] for p in m]), 3) if m else 0.0
    ranked = sorted(m, key=lambda p: p["unreal"])
    top_losers = [_slim(p) for p in ranked[:8]]
    top_winners = [_slim(p) for p in ranked[::-1][:8]]
    rec = {
        "ts": C.ts_iso(),
        "gen": d.get("generated_at"),
        "breadth": breadth,
        "median_unreal": median_unreal,
        "top_winners": top_winners,
        "top_losers": top_losers,
    }
    C.append_jsonl(NAME, rec, dedup_key="gen")   # one sample per distinct snapshot
    report()
    ratio = round(ups / downs, 2) if downs else (float("inf") if ups else 0.0)
    print(NAME + ": n=" + str(len(m)) + " up/down=" + str(ups) + "/" + str(downs) +
          " (ratio " + str(ratio) + ") median_unreal=" + C.usd(median_unreal))


def _tbl(rows):
    """Markdown rows for a winners/losers table."""
    out = ["| sym | strat | side | entry | cur | unreal | unreal% |",
           "|---|---|---|---|---|---|---|"]
    for r in rows:
        cur = "—" if r.get("cur") is None else f"{r['cur']:.4f}"
        upct = "—" if r.get("unreal_pct") is None else f"{r['unreal_pct']:.2f}%"
        out.append("| " + " | ".join([
            str(r.get("sym") or "?"), str(r.get("strat") or "?"), str(r.get("side") or "?"),
            f"{(r.get('entry') or 0):.4f}", cur, C.usd(r.get("unreal")), upct]) + " |")
    return out


def report():
    rows = C.read_jsonl(NAME)
    lines = ["# Movers — mark-to-market snapshot",
             "",
             "> READ-ONLY logger (no trades). Snapshots which open positions are moving "
             "mark-to-market (winners/losers, breadth). Persistence is studied later by "
             "comparing successive log lines — this logger only snapshots.",
             "> Updated " + C.ts_iso() + " · " + str(len(rows)) + " samples", ""]
    if not rows:
        lines += ["_No samples yet._"]
        C.write_report(NAME, lines)
        return
    last = rows[-1]
    b = last.get("breadth") or {}
    up, down, flat = b.get("up", 0), b.get("down", 0), b.get("flat", 0)
    ratio = round(up / down, 2) if down else (float("inf") if up else 0.0)
    lines += ["## Latest breadth (gen " + str(last.get("gen")) + ")", "",
              "- up: **" + str(up) + "** · down: **" + str(down) + "** · flat: " + str(flat),
              "- up/down ratio: **" + str(ratio) + "**",
              "- median unreal: **" + C.usd(last.get("median_unreal")) + "**", "",
              "## Top winners", ""]
    lines += _tbl(last.get("top_winners") or [])
    lines += ["", "## Top losers", ""]
    lines += _tbl(last.get("top_losers") or [])
    lines += ["", "## Breadth history (up/down/flat per sample)", ""]
    for r in rows[-20:]:
        rb = r.get("breadth") or {}
        lines.append("- " + str(r.get("ts")) + " · up=" + str(rb.get("up", 0)) +
                     " down=" + str(rb.get("down", 0)) + " flat=" + str(rb.get("flat", 0)) +
                     " · median=" + C.usd(r.get("median_unreal")))
    C.write_report(NAME, lines)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "once"
    if cmd == "once":
        once()
    elif cmd == "report":
        report()
    else:
        print("usage: mover_logger.py once|report")


if __name__ == "__main__":
    main()
