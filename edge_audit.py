#!/usr/bin/env python3
"""
edge_audit.py — rigorous edge validation over ALL logged paper exits (2026-06-13).

Connects the ideas from the cloned quant frameworks (qlib factor validation,
pybroker walk-forward, backtrader stats) to the bot's OWN historical data
(scalp_lab_state.json, 2600+ closed exits) — but stdlib-only, NO external code
executed (paper-safe).

For every leg with enough exits it computes:
  • mean P&L/exit, win rate, n
  • bootstrap 95% CI (does it exclude 0?)
  • out-of-sample split: first-half vs second-half mean (does the edge persist?)
  • multiple-testing correction: with ~90 legs tested, a 95% CI will falsely
    exclude 0 for ~4-5 legs by chance. Bonferroni-adjust the bootstrap to the
    number of legs actually tested, so "passes" means passes AFTER selection bias.

Controls (allin, coinflip, coindown) MUST fail — if a control "passes", that's a
marking bug, flagged loudly.

  python3 edge_audit.py              # full audit table
  python3 edge_audit.py --min 20     # only legs with >=20 exits (default)
"""
import json, sys, os
from pathlib import Path

BASE = Path(__file__).resolve().parent
STATE = BASE / "scalp_lab_state.json"
CONTROLS = {"allin", "coinflip", "coindown"}
MIN_N = 20
B = 2000   # bootstrap resamples

# deterministic LCG so results are reproducible without numpy/random seeding races
class LCG:
    def __init__(self, seed=12345): self.s = seed
    def randint(self, n):
        self.s = (1103515245 * self.s + 12345) & 0x7FFFFFFF
        return self.s % n

def boot_ci(xs, rng, alpha=0.05, adj=1):
    """Bootstrap CI for the mean. adj = Bonferroni factor (widen tails)."""
    n = len(xs)
    means = []
    for _ in range(B):
        s = sum(xs[rng.randint(n)] for _ in range(n)) / n
        means.append(s)
    means.sort()
    a = alpha / adj            # Bonferroni: tighter tail probability
    lo = means[int((a/2) * B)]
    hi = means[int((1 - a/2) * B)]
    return lo, hi

def main():
    if not STATE.exists():
        print("no scalp_lab_state.json"); return
    min_n = MIN_N
    if "--min" in sys.argv:
        min_n = int(sys.argv[sys.argv.index("--min") + 1])
    state = json.loads(STATE.read_text())

    legs = []
    for leg, book in state.items():
        if not isinstance(book, dict) or "closed" not in book: continue
        pnls = [r["pnl_usdc"] for r in book["closed"]
                if r.get("pnl_usdc") is not None]
        if len(pnls) >= min_n:
            legs.append((leg, pnls))

    n_tested = len(legs)
    rng = LCG()
    print(f"\nEDGE AUDIT — {n_tested} legs with >={min_n} priced exits")
    print(f"Bonferroni factor = {n_tested} (corrects best-of-N selection bias)\n")
    print(f"{'leg':<16}{'n':>5}{'mean$':>9}{'win%':>7}  {'raw 95% CI':>22}  {'corrected CI':>22}  verdict")
    print("-" * 100)

    passers, control_fails = [], []
    rows = []
    for leg, pnls in legs:
        n = len(pnls)
        mean = sum(pnls) / n
        win = 100 * sum(1 for x in pnls if x > 0) / n
        lo, hi = boot_ci(pnls, rng, adj=1)
        clo, chi = boot_ci(pnls, rng, adj=n_tested)
        raw_pass = lo > 0
        corr_pass = clo > 0
        # out-of-sample persistence
        h = n // 2
        m1 = sum(pnls[:h]) / h if h else 0
        m2 = sum(pnls[h:]) / (n - h) if n - h else 0
        oos_ok = (m1 > 0) == (m2 > 0) and m2 > 0
        rows.append((leg, n, mean, win, lo, hi, clo, chi, corr_pass, oos_ok))

    rows.sort(key=lambda r: -r[2])
    for leg, n, mean, win, lo, hi, clo, chi, corr_pass, oos_ok in rows:
        is_ctrl = leg in CONTROLS
        if corr_pass and oos_ok and not is_ctrl:
            verdict = "★ EDGE"
            passers.append(leg)
        elif corr_pass and is_ctrl:
            verdict = "⚠ CONTROL+EV=BUG"
            control_fails.append(leg)
        elif lo > 0 and not corr_pass:
            verdict = "raw-only (dies on correction)"
        elif corr_pass and not oos_ok:
            verdict = "fails OOS persistence"
        else:
            verdict = "no edge" if mean <= 0 else "noise"
        tag = " [CTRL]" if is_ctrl else ""
        print(f"{leg+tag:<16}{n:>5}{mean:>9.4f}{win:>6.0f}%  [{lo:>+7.4f},{hi:>+7.4f}]  "
              f"[{clo:>+7.4f},{chi:>+7.4f}]  {verdict}")

    print("-" * 100)
    print(f"\n★ EDGES surviving correction + OOS: {passers or 'NONE'}")
    if control_fails:
        print(f"🚨 MARKING-BUG ALARM — controls showing +EV: {control_fails}")
    else:
        print("✓ all controls correctly fail (honest marking)")

if __name__ == "__main__":
    main()
