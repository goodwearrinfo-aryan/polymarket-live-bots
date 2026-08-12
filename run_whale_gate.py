#!/usr/bin/env python3
"""run_whale_gate.py — one-shot driver for the full whale smart-money gate (paper, read-only).
Runs, in order:
  1. whale_drift_backtest.py   — per-whale + dollar-weighted copy edge on ALL fresh wallets
  2. oos_copy_backtest.py      — point-in-time OOS split on the SAME wallet roster
Both prints are captured to logs/whale_gate_YYYYMMDD_HHMM.*.txt in CWD for the brain record.
"""
import os, sys, time, subprocess, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
PY = "/Library/Frameworks/Python.framework/Versions/3.14/bin/python3"
WALLETS = os.path.join(HERE, "whale_candidates_active.json")
OUTDIR = os.path.join(HERE, "whale_gate_logs")
os.makedirs(OUTDIR, exist_ok=True)
stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M")


def run(name, argv, out):
    print(f"\n{'='*70}\n>>> {name}  ({stamp})\n{'='*70}", flush=True)
    t0 = time.time()
    p = subprocess.run(argv, capture_output=True, text=True)
    with open(out, "w") as f:
        f.write(f"# {name} — {stamp} rc={p.returncode} ({time.time()-t0:.0f}s)\n\nSTDOUT\n" + "-"*60 + "\n" + p.stdout)
        if p.stderr:
            f.write("\nSTDERR\n" + "-"*60 + "\n" + p.stderr)
    print(p.stdout)
    if p.stderr:
        print("STDERR:", p.stderr[-800:], flush=True)
    print(f"rc={p.returncode} in {time.time()-t0:.0f}s → {out}", flush=True)
    return p.returncode


if __name__ == "__main__":
    rc1 = run("DRIFT", [PY, os.path.join(HERE, "whale_drift_backtest.py"),
                        "--wallets", WALLETS, "--haircut", "0.03",
                        "--min-usd", "1000", "--pages", "10"],
              os.path.join(OUTDIR, f"drift_{stamp}.txt"))
    rc2 = run("OOS", [PY, os.path.join(HERE, "oos_copy_backtest.py"),
                      "--wallets", WALLETS, "--days", "21", "--pages", "4",
                      "--haircut", "0.03", "--band", "0.15,0.85",
                      "--sel-min-n", "8", "--sel-min-wr", "55",
                      "--now", str(int(time.time()))],
              os.path.join(OUTDIR, f"oos_{stamp}.txt"))
    print(f"\nDONE rc1={rc1} rc2={rc2}")
    sys.exit(0 if (rc1 == 0 and rc2 == 0) else 1)