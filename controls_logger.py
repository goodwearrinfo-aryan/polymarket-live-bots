#!/usr/bin/env python3
"""controls_logger.py — READ-ONLY analysis logger (places NO trades). Are the marking-honesty
controls still bleeding? A control going positive signals a MARKING BUG, not an edge — BUT only
once it has enough exits to be meaningful. A tiny open-MTM wiggle at n=2 is noise, not a bug, so
the alarm is GATED at the project's own graduation threshold (>=30 closed exits). Paper-only, stdlib only.
Usage: python3 controls_logger.py once | report"""
import sys
import ports_analysis_common as C

NAME = "controls"
MIN_N = 30   # graduation threshold: a control's sign only "counts" with >=30 closed exits


def status():
    """Pure marking-honesty status (NO side effects) — returns the rec dict, or None if no snapshot.

    Extracted from once() so other modules (e.g. the dsr-gatekeeper graduation gate) can ask the
    AUTHORITATIVE question — "is any GRADED honesty-control (allin/coinflip…, n>=MIN_N) positive?" —
    without a per-leg comparison baseline standing in for it (that mistake cried MARKING-BUG every run)."""
    d, age = C.load_ports()
    if d is None:
        return None
    legs = {l.get("name"): l for l in (d.get("legs") or []) if l.get("name")}
    by = C.group_by(d.get("positions") or [], "strat")
    controls = []
    for c in C.CONTROLS:
        if c not in legs:
            continue
        l = legs[c]
        closed = l.get("closed") or 0
        unreal = round(sum(p["unreal"] for p in by.get(c, []) if p.get("unreal") is not None), 2)
        realized = round(l.get("realized") or 0.0, 2)
        total = round(realized + unreal, 2)
        n_ok = closed >= MIN_N
        note = "untraded" if closed == 0 else ("insufficient_n" if not n_ok else "")
        controls.append({
            "name": c, "realized": realized, "unreal": unreal, "total": total,
            "wr": l.get("wr"), "closed": closed, "n_ok": n_ok, "note": note,
            "positive_raw": total > 0,
            "flag_positive": (total > 0) and n_ok,   # GATED alarm: only a real bug if n>=MIN_N
        })
    graded = [c for c in controls if c["n_ok"]]       # controls that actually count
    any_positive = any(c["flag_positive"] for c in controls)
    return {
        "ts": C.ts_iso(), "gen": d.get("generated_at"), "min_n": MIN_N,
        "controls": controls, "any_positive": any_positive,
        "n_graded": len(graded), "graded_sum": round(sum(c["total"] for c in graded), 2),
    }


def once():
    rec = status()
    if rec is None:
        print(NAME + ": no ports_data.json snapshot — skipped"); return
    controls = rec["controls"]
    graded = [c for c in controls if c["n_ok"]]       # controls that actually count
    any_positive = rec["any_positive"]
    C.append_jsonl(NAME, rec, dedup_key="gen")
    report()
    if graded:
        worst = sorted(graded, key=lambda c: c["total"])[0]
        summary = ("%d graded controls (n>=%d), sum %s, worst %s %s"
                   % (len(graded), MIN_N, C.usd(rec["graded_sum"]), worst["name"], C.usd(worst["total"])))
        if any_positive:
            offenders = ", ".join(c["name"] + " " + C.usd(c["total"]) + " (n=" + str(c["closed"]) + ")"
                                  for c in controls if c["flag_positive"])
            print(NAME + ": ⚠️ MARKING-BUG ALARM — graded control(s) POSITIVE: " + offenders + " · " + summary)
        else:
            noisy = [c for c in controls if c["positive_raw"] and not c["n_ok"]]
            tail = ("  (ignored " + str(len(noisy)) + " sub-n>0 noise control(s))" if noisy else "")
            print(NAME + ": OK — all graded controls bleeding as required (marking honest). " + summary + tail)
    else:
        print(NAME + ": no control has >=" + str(MIN_N) + " exits yet — nothing to grade")


def report():
    rows = C.read_jsonl(NAME)
    lines = ["# Controls Honesty Monitor", "",
             "> READ-ONLY logger (no trades). Tracks the marking-honesty controls — legs that MUST lose. "
             "A control going positive is not an edge; it signals a MARKING BUG. The alarm is GATED at "
             "n>=" + str(MIN_N) + " closed exits so open-MTM noise on a tiny control can't cry wolf.",
             "> Updated " + C.ts_iso() + " · " + str(len(rows)) + " samples", ""]
    if not rows:
        lines += ["_No samples yet._"]; C.write_report(NAME, lines); return
    latest = rows[-1]
    controls = latest.get("controls", [])
    graded = [c for c in controls if c.get("n_ok")]

    lines += ["**Hard rule:** the controls MUST lose. If a control with a real sample (n>=" + str(MIN_N) +
              ") goes positive → it's a marking bug, not a signal."]
    if latest.get("any_positive"):
        offenders = [c for c in controls if c.get("flag_positive")]
        lines += ["", "## ⚠️⚠️ MARKING-BUG ALARM ⚠️⚠️", "",
                  "> **A GRADED CONTROL (n>=" + str(MIN_N) + ") HAS GONE POSITIVE.** Per the hard rule, controls "
                  "that win are a MARKING BUG (e.g. stale_nodata booking held markets at $0, or a censored/un-priced "
                  "exit). NOT a tradable edge. Investigate marking before trusting any leg P&L.", ""]
        for c in offenders:
            lines += ["- **" + c["name"] + "** total " + C.usd(c["total"]) + " (realized " + C.usd(c["realized"]) +
                      " + unreal " + C.usd(c["unreal"]) + ", wr " + str(c.get("wr")) + "%, " +
                      str(c.get("closed")) + " closed) — POSITIVE, must be negative"]
        lines += [""]
    else:
        noisy = [c for c in controls if c.get("positive_raw") and not c.get("n_ok")]
        lines += ["", "✅ **All graded controls bleeding as required — marking honest.** Every control with a "
                  "real sample (n>=" + str(MIN_N) + ") is net-negative, as a loser-by-design leg must be.", ""]
        if noisy:
            lines += ["> Note: " + ", ".join(c["name"] + " " + C.usd(c["total"]) + " (n=" + str(c["closed"]) + ")"
                      for c in noisy) + " show positive but are BELOW the n-gate — open-MTM noise, not graded, "
                      "alarm correctly suppressed.", ""]

    lines += ["## Controls (latest snapshot)", "",
              "snapshot `" + str(latest.get("gen")) + "` · logged " + str(latest.get("ts")) + " · gate n>=" + str(MIN_N), "",
              "| Control | Realized | Unreal | Total | WR% | Closed | Status |",
              "|---|---|---|---|---|---|---|"]
    for c in sorted(controls, key=lambda x: x["total"]):
        if c.get("note") == "untraded":
            status = "untraded (excluded)"
        elif c.get("flag_positive"):
            status = "⚠️ POSITIVE (BUG, n>=" + str(MIN_N) + ")"
        elif c.get("positive_raw"):
            status = "positive · n<" + str(MIN_N) + " noise"
        else:
            status = "bleeding ✓"
        lines += ["| " + c["name"] + " | " + C.usd(c["realized"]) + " | " + C.usd(c["unreal"]) +
                  " | " + C.usd(c["total"]) + " | " + str(c.get("wr")) + " | " + str(c.get("closed")) +
                  " | " + status + " |"]
    lines += ["| **GRADED (n>=" + str(MIN_N) + ")** |  |  | **" + C.usd(latest.get("graded_sum")) + "** |  | " +
              str(latest.get("n_graded")) + " | " +
              ("⚠️ check" if latest.get("any_positive") else "all negative ✓") + " |"]

    flagged = sum(1 for r in rows if r.get("any_positive"))
    lines += ["", "## History", "",
              "- Samples: " + str(len(rows)),
              "- Samples with a graded positive control (alarm fired): " + str(flagged),
              "- Graded controls now: " + (", ".join(c["name"] for c in graded) or "none yet") +
              "  ·  untraded/excluded: " + (", ".join(c["name"] for c in controls if c.get("note") == "untraded") or "none"),
              "- Latest: " + ("⚠️ ALARM" if latest.get("any_positive") else "clean (all graded controls negative)")]
    C.write_report(NAME, lines)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "once"
    if cmd == "once":
        once()
    elif cmd == "report":
        report()
    else:
        print("usage: controls_logger.py once|report")


if __name__ == "__main__":
    main()
