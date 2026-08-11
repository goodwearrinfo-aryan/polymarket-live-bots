#!/usr/bin/env python3
"""edge_screen_logger.py — READ-ONLY analysis logger (places NO trades). THE goal logger.

The project defines an edge as: a leg with >=30 priced exits AND a bootstrap CI on $/exit that
clears 0 AND it is not a control. The other loggers snapshot the live book; none of them screen
for THAT. This one does — straight off the true per-exit pnl_usdc series in scalp_lab_state.json
(not the downsampled spark), so the CI is real, not an approximation.

For every leg with >=MIN_N closed exits it logs: mean $/exit, a 2000-resample bootstrap 95% CI on
the mean, the worst-point drawdown of the realized equity curve, and a PASS/FAIL =
(ci_low > 0 AND leg not in CONTROLS). Any leg that PASSES is checked against BRAIN's retracted list —
if it's there (e.g. nearres), the "edge" is the known gap-through artifact and the PASS is a prompt
to re-price stops gap-honest, not a green light. Paper-only, stdlib only.

NOTE: this is a bootstrap CI + drawdown screen, NOT a full DSR (Deflated Sharpe) — it does not
correct for best-of-N leg-selection bias. A PASS here is a CANDIDATE to validate, not a graduation.
Usage: python3 edge_screen_logger.py once | report"""
import sys
import random
import statistics
import ports_analysis_common as C

NAME = "edge_screen"
MIN_N = 30          # the project's graduation threshold
BOOT_ITERS = 2000
SEED = 1234         # fixed seed -> CI is deterministic given the data (no run-to-run jitter)


def _bootstrap_ci(pnls):
    """95% bootstrap CI on the mean $/exit. Deterministic for a given pnl series."""
    n = len(pnls)
    if n < 2:
        return (None, None)
    rng = random.Random(SEED)
    means = []
    for _ in range(BOOT_ITERS):
        s = 0.0
        for _ in range(n):
            s += pnls[rng.randrange(n)]
        means.append(s / n)
    means.sort()
    return (round(means[int(0.025 * BOOT_ITERS)], 4), round(means[int(0.975 * BOOT_ITERS)], 4))


def _max_drawdown(pnls):
    """Worst peak-to-trough of the realized cumulative equity curve."""
    cum = peak = dd = 0.0
    for p in pnls:
        cum += p
        peak = max(peak, cum)
        dd = min(dd, cum - peak)
    return round(dd, 2)


def once():
    lab = C.load_json("scalp_lab_state.json")
    if not isinstance(lab, dict):
        print(NAME + ": scalp_lab_state.json missing/unreadable — skipped"); return
    # memoize: the screen only changes when an exit closes. Skip the bootstrap (the only heavy
    # step, ~0.6s) when the total closed-exit count is unchanged from the last sample — re-render
    # the report from the cached record so high-cadence runs cost ~nothing.
    fp = sum(len(v.get("closed") or []) for v in lab.values() if isinstance(v, dict))
    prev = C.last_record(NAME)   # O(1) tail-read; cheap early-out to skip the ~0.6s bootstrap
    if prev and prev.get("fp") == fp and prev.get("legs"):
        report()
        print(NAME + ": unchanged (fp=" + str(fp) + ", " + str(prev.get("n_screened")) + " legs) — bootstrap skipped")
        return
    legs = []
    for name, v in lab.items():
        if not isinstance(v, dict):
            continue
        closed = v.get("closed") or []
        pnls = []
        for c in closed:
            if not isinstance(c, dict):
                continue
            pv = c.get("pnl_usdc")
            if pv is None:
                continue
            try:
                pnls.append(float(pv))   # tolerate a malformed (non-numeric) pnl_usdc instead of crashing the screen
            except (TypeError, ValueError):
                continue
        n = len(pnls)
        if n < MIN_N:
            continue
        mean = statistics.mean(pnls)
        lo, hi = _bootstrap_ci(pnls)
        is_ctrl = name in C.CONTROLS
        passes = (lo is not None and lo > 0 and not is_ctrl)
        wr = round(100 * sum(1 for p in pnls if p > 0) / n, 1)
        # rule #1 (optimise DOLLARS not win rate): high win rate but losing money = the
        # favourite-buying trap, where one loss erases many small wins.
        vanity = (not is_ctrl) and wr >= 60.0 and mean < 0
        legs.append({
            "name": name, "n": n, "mean_per_exit": round(mean, 4),
            "ci_low": lo, "ci_high": hi, "max_dd": _max_drawdown(pnls),
            "total": round(sum(pnls), 2), "is_control": is_ctrl,
            "wr": wr, "vanity": vanity,
            "retracted": C.RETRACTED.get(name),
            "verdict": "PASS" if passes else ("control" if is_ctrl else "fail"),
        })
    legs.sort(key=lambda x: -x["mean_per_exit"])
    passing = [l for l in legs if l["verdict"] == "PASS"]
    rec = {
        "ts": C.ts_iso(), "min_n": MIN_N, "boot_iters": BOOT_ITERS, "fp": fp,
        "n_screened": len(legs), "n_pass": len(passing),
        "pass_names": [l["name"] for l in passing], "legs": legs,
    }
    C.append_jsonl(NAME, rec, dedup_key="fp")   # authoritative race-safe dedup (atomic in the helper)
    report()
    if passing:
        tags = []
        for l in passing:
            t = l["name"] + " " + C.usd(l["mean_per_exit"]) + "/exit (CI " + str(l["ci_low"]) + ".." + str(l["ci_high"]) + ", n=" + str(l["n"]) + ")"
            if l["retracted"]:
                t += " ⚠️RETRACTED"
            tags.append(t)
        print(NAME + ": " + str(len(legs)) + " legs n>=" + str(MIN_N) + " · " + str(len(passing)) +
              " PASS bootstrap-CI>0: " + "; ".join(tags))
    else:
        print(NAME + ": " + str(len(legs)) + " legs n>=" + str(MIN_N) + " screened · 0 clear bootstrap CI>0 (no candidate edge)")


def report():
    rows = C.read_jsonl(NAME)
    lines = ["# Edge Screen — per-exit expectancy + bootstrap CI", "",
             "> READ-ONLY logger (no trades). The goal screen: for every leg with >=" + str(MIN_N) +
             " closed exits, mean $/exit + a 2000-resample bootstrap 95% CI on the mean + worst drawdown. "
             "PASS = CI clears 0 AND not a control. Optimises DOLLARS, not win rate (hard rule).",
             "> Updated " + C.ts_iso() + " · " + str(len(rows)) + " samples", ""]
    if not rows:
        lines += ["_No samples yet._"]; C.write_report(NAME, lines); return
    last = rows[-1]
    legs = last.get("legs", [])
    passing = [l for l in legs if l.get("verdict") == "PASS"]

    if passing:
        lines += ["## ✅ Candidate(s) clearing bootstrap CI>0", ""]
        for l in passing:
            warn = ("  —  ⚠️ **" + l["retracted"] + "** → this PASS is the known artifact, NOT a green light; "
                    "re-price stops gap-honest before believing it.") if l.get("retracted") else ""
            lines += ["- **" + l["name"] + "** · " + C.usd(l["mean_per_exit"]) + "/exit · CI [" +
                      str(l["ci_low"]) + ", " + str(l["ci_high"]) + "] · n=" + str(l["n"]) +
                      " · maxDD " + C.usd(l["max_dd"]) + warn]
        lines += ["", "> A PASS is a CANDIDATE to validate (not a DSR-corrected graduation — this screen does "
                  "not deflate for best-of-N leg selection). Run the full validation before sizing anything.", ""]
    else:
        lines += ["## No candidate clears bootstrap CI>0", "",
                  "_Every leg with >=" + str(MIN_N) + " exits has a 95% bootstrap CI on $/exit that includes 0 "
                  "(or is a control). Consistent with 'no proven algo edge'._", ""]

    vain = [l for l in legs if l.get("vanity")]
    if vain:
        lines += ["## ⚠️ Vanity-trap legs (rule #1: high win rate, losing dollars)", ""]
        for l in vain:
            lines.append("- **" + l["name"] + "** · WR " + str(l.get("wr")) + "% but " +
                         C.usd(l["mean_per_exit"]) + "/exit (n=" + str(l["n"]) +
                         ") — buying favourites; one loss erases many wins.")
        lines += [""]
    lines += ["## All screened legs (n>=" + str(MIN_N) + ", by mean $/exit)", "",
              "| leg | n | WR% | $/exit | CI low | CI high | maxDD | total | verdict |",
              "|-----|--:|----:|-------:|-------:|--------:|------:|------:|---------|"]
    for l in legs:
        tag = (l.get("name") + (" ⚠️ctrl" if l.get("is_control") else "")
               + (" ⚠️retr" if l.get("retracted") else "")
               + (" ⚠️vanity" if l.get("vanity") else ""))
        v = {"PASS": "✅ PASS", "control": "control (excl)", "fail": "fail"}.get(l.get("verdict"), l.get("verdict"))
        lines.append("| " + tag + " | " + str(l["n"]) + " | " +
                     (str(l["wr"]) if l.get("wr") is not None else "—") + " | " +
                     C.usd(l["mean_per_exit"]) + " | " +
                     (str(l["ci_low"]) if l["ci_low"] is not None else "—") + " | " +
                     (str(l["ci_high"]) if l["ci_high"] is not None else "—") + " | " +
                     C.usd(l["max_dd"]) + " | " + C.usd(l["total"]) + " | " + v + " |")
    lines += ["", "_Bootstrap: " + str(last.get("boot_iters")) + " resamples, fixed seed (CI deterministic per "
              "snapshot). Controls excluded from PASS by rule. Retracted legs (BRAIN) flagged ⚠️retr._"]
    C.write_report(NAME, lines)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "once"
    if cmd == "once":
        once()
    elif cmd == "report":
        report()
    else:
        print("usage: edge_screen_logger.py once|report")


if __name__ == "__main__":
    main()
