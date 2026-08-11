#!/usr/bin/env python3
"""bandcal_logger.py — READ-ONLY analysis logger (places NO trades). Entry-price BAND realized-P&L.

Bins every CLOSED exit by its YES-equivalent entry price (deciles) and tracks REALIZED P&L
(pnl_usdc, gap-honest) per band. This is the favorite-longshot / fade-band detector — the one
edge type that could still survive after gap-through killed taker mispricing on calibrated mids,
because it is measured on ACTUAL realized money, not on mids (so a band that's +EV here is a real
realized edge, not a clean-fill backtest artifact). The shape of $/exit across bands IS the
favorite-longshot calibration curve for this book.

A band edge = a YES-equivalent-price decile whose bootstrap 95% CI on $/exit clears 0 over
NON-CONTROL exits with n>=MIN_N. (Controls buy indiscriminately, so they're tallied but excluded
from the flag.) NOT a true outcome-calibration (exits close on target/stop/time, not only at
resolution) — for Brier/resolution calibration see belief_ledger. Paper-only, stdlib only.
Usage: python3 bandcal_logger.py once | report"""
import sys, random, statistics
import ports_analysis_common as C

NAME = "bandcal"
MIN_N = 30
BOOT_ITERS = 2000
SEED = 1234


def _ci(pnls):
    """95% bootstrap CI on the mean $/exit. Deterministic for a given series."""
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


def once():
    lab = C.load_json("scalp_lab_state.json")
    if not isinstance(lab, dict):
        print(NAME + ": scalp_lab_state.json missing/unreadable — skipped"); return
    # memoize: bands only change when an exit closes -> recompute only when the closed-count moves
    fp = sum(len(v.get("closed") or []) for v in lab.values() if isinstance(v, dict))
    prev = C.last_record(NAME)
    if prev and prev.get("fp") == fp and prev.get("bands"):
        report()
        print(NAME + ": unchanged (fp=" + str(fp) + ") — recompute skipped")
        return

    bins = {b: {"pnl": [], "pnl_real": [], "wins": 0, "n": 0} for b in range(10)}
    total = 0
    for name, v in lab.items():
        if not isinstance(v, dict):
            continue
        is_ctrl = name in C.CONTROLS
        for c in (v.get("closed") or []):
            if not isinstance(c, dict):
                continue
            ef = c.get("entry_fill", c.get("entry_mid"))
            pv = c.get("pnl_usdc")
            side = (c.get("side") or "").upper()
            if ef is None or pv is None:
                continue
            try:
                ef = float(ef); pv = float(pv)
            except (TypeError, ValueError):
                continue
            yp = ef if side == "YES" else (1.0 - ef)     # YES-equivalent entry price
            if yp < 0 or yp > 1:
                continue
            b = min(9, int(yp * 10))
            slot = bins[b]
            slot["n"] += 1
            slot["pnl"].append(pv)
            if pv > 0:
                slot["wins"] += 1
            if not is_ctrl:
                slot["pnl_real"].append(pv)
            total += 1

    bands = []
    candidates = []
    for b in range(10):
        slot = bins[b]
        if slot["n"] == 0:
            continue
        n_real = len(slot["pnl_real"])
        lo, hi = _ci(slot["pnl_real"]) if n_real >= MIN_N else (None, None)
        flag = (lo is not None and lo > 0)
        bands.append({
            "band": b, "lo": round(b / 10, 1), "hi": round((b + 1) / 10, 1),
            "n": slot["n"], "n_real": n_real,
            "mean_pnl": round(statistics.mean(slot["pnl"]), 4),
            "mean_pnl_real": round(statistics.mean(slot["pnl_real"]), 4) if slot["pnl_real"] else None,
            "wr": round(100 * slot["wins"] / slot["n"], 1),
            "ci_low": lo, "ci_high": hi,
            "total_pnl": round(sum(slot["pnl"]), 2), "flag": flag,
        })
        if flag:
            candidates.append(b)

    rec = {"ts": C.ts_iso(), "fp": fp, "min_n": MIN_N, "n_exits": total,
           "n_candidate_bands": len(candidates), "candidate_bands": candidates, "bands": bands}
    C.append_jsonl(NAME, rec, dedup_key="fp")
    report()
    if candidates:
        print(NAME + ": " + str(total) + " exits · CANDIDATE band edge(s): " +
              ", ".join("[%.1f,%.1f)" % (b / 10, b / 10 + 0.1) for b in candidates))
    else:
        print(NAME + ": " + str(total) + " exits across " + str(len(bands)) +
              " bands · 0 band clears bootstrap CI>0 (no realized band edge)")


def report():
    rows = C.read_jsonl(NAME)
    lines = ["# Band Calibration — realized P&L by entry-price decile", "",
             "> READ-ONLY logger (no trades). Bins every CLOSED exit by its **YES-equivalent entry price** "
             "(deciles) and tracks REALIZED P&L (pnl_usdc, gap-honest) per band — the favorite-longshot / "
             "fade-band detector. A band whose bootstrap 95% CI on $/exit clears 0 over non-control exits "
             "(n>=" + str(MIN_N) + ") is a real realized edge. The $/exit shape across bands IS the "
             "favorite-longshot curve. (Not outcome-calibration — see [[belief_calibration]].)",
             "> Updated " + C.ts_iso() + " · " + str(len(rows)) + " samples", ""]
    if not rows:
        lines += ["_No samples yet._"]; C.write_report(NAME, lines); return
    last = rows[-1]
    bands = last.get("bands", [])

    if last.get("candidate_bands"):
        lines += ["## ✅ Candidate band edge(s) — CI>0, non-control, n>=" + str(MIN_N), ""]
        for b in bands:
            if b.get("flag"):
                lines.append("- **[%.1f, %.1f)** YES-price · %s/exit · CI [%s, %s] · n_real=%d"
                             % (b["lo"], b["hi"], C.usd(b["mean_pnl_real"]), b["ci_low"], b["ci_high"], b["n_real"]))
        lines += ["", "> Measured on actual pnl_usdc, so it survives gap-through. Validate before sizing — "
                  "not DSR-corrected; check it isn't a single-leg or single-category confound.", ""]
    else:
        lines += ["## No band clears bootstrap CI>0", "",
                  "_Consistent with gap-through killing taker mispricing on calibrated mids — no entry-price "
                  "band is a realized edge._", ""]

    lines += ["## Realized P&L by YES-equivalent entry band", "",
              "| band | n | n_real | WR% | $/exit (all) | $/exit (real) | CI (real) | total$ | edge |",
              "|------|--:|-------:|----:|-------------:|--------------:|-----------|-------:|------|"]
    for b in bands:
        ci = "—" if b["ci_low"] is None else ("[%s, %s]" % (b["ci_low"], b["ci_high"]))
        mr = "—" if b["mean_pnl_real"] is None else C.usd(b["mean_pnl_real"])
        lines.append("| [%.1f,%.1f) | %d | %d | %.1f | %s | %s | %s | %s | %s |"
                     % (b["lo"], b["hi"], b["n"], b["n_real"], b["wr"],
                        C.usd(b["mean_pnl"]), mr, ci, C.usd(b["total_pnl"]),
                        ("✅" if b.get("flag") else "")))
    lines += ["", "_YES-equivalent price = entry_fill for YES exits, 1−entry_fill for NO. 'real' = non-control "
              "legs only (controls buy indiscriminately). WR% = share of exits with pnl>0 (not a resolution "
              "rate). Bootstrap: " + str(BOOT_ITERS) + " resamples, fixed seed._",
              "", "See [[edge_screen]] · [[belief_calibration]]."]
    C.write_report(NAME, lines)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "once"
    if cmd == "once":
        once()
    elif cmd == "report":
        report()
    else:
        print("usage: bandcal_logger.py once|report")


if __name__ == "__main__":
    main()
