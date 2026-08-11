#!/usr/bin/env python3
"""
dsr_gate.py — pm-dsr-gatekeeper as a DETERMINISTIC tool (NO LLM, zero cost).
The graduation gate for a paper leg: GRADUATE iff n>=30 AND bootstrap CI>0 AND beats the
controls AND DSR>0. Optimizes DOLLARS, not win-rate.

WHY NOT AN LLM: this is arithmetic, not judgment. The free-LLM agent (pm_agents_24x7
dsr-gatekeeper) on local 8B inverted the core test live ("CI (-1.0, 0.667) does not include
zero" — it does). A gate that decides whether real money is at stake must be exact, so it's
pure Python over psr_dsr.py (DSR) + edge_screen_logger (bootstrap CI). Reads leg closed exits
from scalp_lab_state.json.

Run:  python3 dsr_gate.py              # gate every leg with n>=10, sorted by readiness
      python3 dsr_gate.py <leg>        # one leg, full detail
"""
import os, sys, json, math, statistics
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import psr_dsr
from edge_screen_logger import _bootstrap_ci

STATE = os.path.join(HERE, "scalp_lab_state.json")
CONTROLS = {"allin", "coinflip", "coindown", "basketrand", "windowshutrand", "allyes"}
N_MIN = 30


def _pnls(leg_book):
    return [t["pnl_usdc"] for t in leg_book.get("closed", []) if t.get("pnl_usdc") is not None]


def load_legs():
    s = json.load(open(STATE))
    legs = {}
    for k, v in s.items():
        if isinstance(v, dict) and "closed" in v:
            p = _pnls(v)
            if p:
                legs[k] = p
    return legs


def _z_dsr(pnls, star):
    """The BRAIN-style DSR z-score: (Sharpe - benchmark)*sqrt(n-1)/skew-kurt-denom."""
    sr = psr_dsr.sharpe(pnls)
    n = len(pnls)
    if sr is None or n < 3:
        return None, None
    skew, kurt = psr_dsr._skew_kurt(pnls)
    denom = math.sqrt(max(1e-12, 1.0 - skew * sr + (kurt - 1.0) / 4.0 * sr * sr))
    z = (sr - star) * math.sqrt(n - 1) / denom
    return z, psr_dsr.phi(z)


def gate(target=None):
    legs = load_legs()
    # DSR deflation universe: only legs with enough exits for a STABLE Sharpe, and exclude
    # degenerate near-zero-variance legs (|Sharpe|>5 is a marking artifact, not a real noise
    # trial — including them inflated the benchmark to ~40 and made every DSR z ≈ -600).
    all_sharpes = []
    for leg, p in legs.items():
        if len(p) < 20:
            continue
        sr = psr_dsr.sharpe(p)
        if sr is not None and abs(sr) < 5:
            all_sharpes.append(sr)
    N = len(all_sharpes)                      # selection universe for DSR deflation
    star = psr_dsr.expected_max_sharpe(all_sharpes, N)
    control_means = [sum(legs[c]) / len(legs[c]) for c in CONTROLS if c in legs]
    worst_control = max(control_means) if control_means else 0.0   # leg must beat the BEST control

    rows = []
    for leg, pnls in legs.items():
        if leg in CONTROLS:
            continue
        n = len(pnls)
        mean = sum(pnls) / n
        ci_lo, ci_hi = _bootstrap_ci(pnls)
        z, p = _z_dsr(pnls, star)
        beats_ctrl = mean > worst_control
        if n < N_MIN:
            verdict = "ACCUMULATING"
        elif (ci_lo is not None and ci_lo > 0) and beats_ctrl and (z is not None and z > 0):
            verdict = "GRADUATE ✅"
        else:
            verdict = "REJECT"
        rows.append({"leg": leg, "n": n, "mean": round(mean, 4),
                     "ci": (ci_lo, ci_hi), "dsr_z": round(z, 3) if z is not None else None,
                     "dsr_p": round(p, 3) if p is not None else None,
                     "fdr_pval": psr_dsr.psr_pvalue(pnls, 0.0), "fdr_q": None,
                     "beats_ctrl": beats_ctrl, "verdict": verdict})

    # Benjamini-Hochberg FDR across the family of tested legs (REPORTING ONLY — does
    # not change the GRADUATE bar). Complements DSR's expected-max: "of all legs I
    # tested, which survive multiplicity correction." Family = GATE-ELIGIBLE legs only
    # (n>=N_MIN): a sub-threshold leg with a lucky streak (e.g. n=8, 8/8 wins) otherwise
    # shows q=0.000 and reads as a false "discovery". n<N_MIN keeps fdr_q=None -> "—",
    # consistent with its ACCUMULATING verdict.
    fam = [r for r in rows if r["fdr_pval"] is not None and r["n"] >= N_MIN]
    if fam:
        qs, _ = psr_dsr.benjamini_hochberg([r["fdr_pval"] for r in fam])
        for r, q in zip(fam, qs):
            r["fdr_q"] = round(q, 4)

    rows.sort(key=lambda r: (r["verdict"] != "GRADUATE ✅", -r["n"]))
    if target:
        rows = [r for r in rows if r["leg"] == target]
    return rows, {"N_legs": N, "dsr_benchmark_sharpe": round(star, 4),
                  "worst_control_mean": round(worst_control, 4), "n_min": N_MIN,
                  "fdr_family": len(fam)}


def main():
    target = sys.argv[1] if len(sys.argv) > 1 else None
    rows, meta = gate(target)
    print(f"\nDSR GATE — DOLLARS not WR | bar: n>={meta['n_min']} ∧ CI>0 ∧ beats control ∧ DSR z>0")
    print(f"  DSR universe N={meta['N_legs']} legs | benchmark Sharpe*={meta['dsr_benchmark_sharpe']} | "
          f"best-control mean to beat={meta['worst_control_mean']}")
    print(f"  BH-FDR family={meta['fdr_family']} legs (n>={meta['n_min']} only) | q = FDR-adjusted p vs noise (reporting only, not gated)\n")
    print(f"  {'leg':<16}{'n':>5}{'mean$':>9}{'CI':>22}{'DSR z':>8}{'DSR p':>7}{'FDR q':>8}  verdict")
    for r in rows:
        ci = f"[{r['ci'][0]}, {r['ci'][1]}]" if r['ci'][0] is not None else "—"
        q = f"{r['fdr_q']:.3f}" if r.get('fdr_q') is not None else "—"
        print(f"  {r['leg']:<16}{r['n']:>5}{r['mean']:>9.4f}{ci:>22}"
              f"{(r['dsr_z'] if r['dsr_z'] is not None else 0):>8.2f}"
              f"{(r['dsr_p'] if r['dsr_p'] is not None else 0):>7.2f}{q:>8}  {r['verdict']}")
    grads = [r for r in rows if "GRADUATE" in r["verdict"]]
    print(f"\n  → {len(grads)} leg(s) GRADUATE." if grads else
          "\n  → 0 legs graduate (honest null — no proven edge; this is a finding, not a failure).")


if __name__ == "__main__":
    main()
