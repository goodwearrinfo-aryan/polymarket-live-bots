#!/usr/bin/env python3
"""
edge_judge.py — FREE ($0, no LLM), autonomous fake-edge judge for the two copy/settle
experiments. Runs the deterministic core of the fakeness lenses each cycle and writes an
honest verdict, so a positive result can NEVER be mistaken for a real edge without gates.
Defaults every verdict to NOT-PROVEN. Read-only. Never trades, never edits the experiments.

Judges:
  A. whale_copy_paper  → whale_copy_state.json   (held-to-resolution copy)
  B. sports_settle_arb → sports_settle_arb.log   (settlement candidates)
Verdict log: edge_judge.log   (+ mirrors a one-line summary to stdout)
"""
from __future__ import annotations
import json, os, statistics as st, random
from datetime import datetime, timezone

BASE = os.path.expanduser("~/polymarket-live")
LOG  = os.path.join(BASE, "edge_judge.log")
N_GATE = 30

def boot_ci(xs, iters=2000):
    """95% bootstrap CI of the mean; returns (lo, hi)."""
    if len(xs) < 2:
        return (0.0, 0.0)
    means = []
    n = len(xs)
    for _ in range(iters):
        s = [xs[random.randrange(n)] for _ in range(n)]
        means.append(sum(s) / n)
    means.sort()
    return (means[int(0.025 * iters)], means[int(0.975 * iters)])

def judge_copy():
    p = os.path.join(BASE, "whale_copy_state.json")
    try:
        s = json.load(open(p))
    except Exception:
        return "COPY: no state yet"
    closed = [c for c in s.get("closed", []) if isinstance(c, dict) and "pnl" in c]
    n = len(closed)
    openn = len(s.get("open", {}))
    if n < N_GATE:
        return f"COPY: NOT-PROVEN (accumulating) closed={n}/{N_GATE} open={openn}"
    pnls = [c["pnl"] for c in closed]
    mean = sum(pnls) / n
    lo, hi = boot_ci(pnls)
    # lucky-tail: drop biggest winner
    drop = sorted(pnls)[:-1]
    mean_drop = sum(drop) / len(drop) if drop else 0
    ci_excl_0 = lo > 0
    survives_tail = mean_drop > 0
    verdict = "REAL-EDGE" if (ci_excl_0 and survives_tail and mean > 0) else "FAKE/NULL"
    # ANOMALY INSPECTOR — catch bug patterns (like the 2026-07-30 all-losses resolution bug):
    # a uniform outcome (every copy identical win/loss, or all exits exactly 0/1) at n>=6
    # is far more likely a resolution/marking bug than real signal. Flag for human inspection.
    anomaly = ""
    if n >= 6:
        exits = [c.get("exit") for c in closed if "exit" in c]
        if exits and len(set(exits)) == 1:
            anomaly = f"  ⚠️ANOMALY: all {n} exits identical ({exits[0]}) — likely resolution bug, INSPECT"
        elif all(p < 0 for p in pnls):
            anomaly = f"  ⚠️ANOMALY: all {n} copies lost — suspicious (data-api hides winners; verify on-chain resolve)"
    # per-wallet breakdown — which whale is actually copyable (copying is per-wallet)
    byw = {}
    for c in closed:
        w = (c.get("wallet") or "?")[:8]
        byw.setdefault(w, []).append(c["pnl"])
    wallet_str = " ".join(f"{w}={sum(v):+.0f}/{len(v)}" for w, v in
                          sorted(byw.items(), key=lambda kv: -sum(kv[1]))[:4])
    return (f"COPY: {verdict} n={n} mean={mean:.3f} CI=({lo:.3f},{hi:.3f}) "
            f"drop1_mean={mean_drop:.3f} ci_excl_0={ci_excl_0} tail_ok={survives_tail}"
            f" | by_wallet[{wallet_str}]{anomaly}")

def judge_settle():
    p = os.path.join(BASE, "sports_settle_arb.log")
    try:
        lines = [json.loads(l) for l in open(p) if l.strip()]
    except Exception:
        return "SETTLE: no log yet"
    if not lines:
        return "SETTLE: no runs yet"
    recent = lines[-20:]
    total_hits = sum(r.get("candidates", 0) for r in recent)
    # deterministic false-positive smell: gap≈1.0 = almost certainly already-resolved/mismatch
    suspicious = 0
    real_like = 0
    for r in recent:
        for h in r.get("hits", []):
            if h.get("gap", 0) >= 0.95:      # gap~1 → resolved/mismatch, not a live edge
                suspicious += 1
            elif 0.06 <= h.get("gap", 0) < 0.6:  # plausible live mispricing band
                real_like += 1
    if total_hits == 0:
        return "SETTLE: NOT-PROVEN (no candidates in last 20 runs — calibrated)"
    return (f"SETTLE: {total_hits} raw hits/20runs → {suspicious} suspicious(gap≈1, likely "
            f"resolved/mismatch) {real_like} plausible. "
            f"{'NEEDS-DEEP-JUDGE' if real_like else 'FAKE (all artifacts)'}")

def main():
    stamp = datetime.now(timezone.utc).isoformat()
    a = judge_copy()
    b = judge_settle()
    line = f"[{stamp}] {a} || {b}"
    open(LOG, "a").write(line + "\n")
    print(line)

if __name__ == "__main__":
    main()
