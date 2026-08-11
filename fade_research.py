#!/usr/bin/env python3
"""fade_research.py — the honest, rigorous fade analysis, all offline steps in one
place, on resolved markets (ml/dataset.csv). Adopts the survey's best ideas AND
adds an original test. PAPER research only.

Steps:
  1. WANG-TRANSFORM fit (Yang 2026): is YES distorted on OUR data, and by how much
     (lambda)? Validates the fade's premise + the ~0.6 sign-flip.
  2. COST-HAIRCUT fade backtest: buy NO at the ASK (price + spread/2) + optional
     fee, hold to resolution. Bootstrap $ CI + Probabilistic Sharpe.
  3. CALIBRATION-SLOPE bucket gate (Le 2026): per (category) does the de-distorted
     price under/over-predict — i.e. where does fading actually have +EV?
  4. *** MY OWN: RISK-PREMIUM vs RESIDUAL-ALPHA decomposition. ***
     Yang warns lambda may be a fair RISK PREMIUM, not free alpha. So split the raw
     fade edge into (a) the premium you'd earn even if the de-distorted price were
     perfectly calibrated, and (b) RESIDUAL ALPHA = (fair prob - realized outcome).
     If residual CI includes 0, the fade is just harvesting the premium — real, but
     compensation for risk, not a mispricing you're exploiting. THIS is the question.
  5. KELLY sizing fed the LOWER CI bound — defined, GATED to off until edge CI>0.

Honest caveats printed inline. lambda is fit in-sample (circular) — a preview, not proof.
"""
import os, sys, csv, math, random, collections
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import psr_dsr as P
from psr_dsr import boot_ci   # canonical bootstrap CI (was duplicated here)

DATA = os.path.join(HERE, "ml", "dataset.csv")
SPREAD = 0.02          # total bid-ask; pay half on entry
FEE_BPS = 0.0          # Polymarket trading fee ~0 now; param kept for realism sweeps
BAND = (0.20, 0.55)    # the live fade band
random.seed(11)


def load():
    rows = list(csv.DictReader(open(DATA)))
    seen = collections.OrderedDict()
    for r in rows:
        seen.setdefault(r["condition_id"], r)
    out = []
    for r in seen.values():
        try:
            out.append({"p": float(r["prob"]), "y": int(float(r["final_outcome"])),
                        "cat": (r.get("category") or "other").lower(),
                        "ttr": float(r.get("time_to_res_h") or 0)})
        except Exception:
            pass
    return out


def clip(x, lo=1e-4, hi=1 - 1e-4):
    return max(lo, min(hi, x))


def entry_cost_no(p):
    """Cost to buy the NO outcome at the ask, incl. spread + fee haircut."""
    fee = FEE_BPS / 10000.0 * min(p, 1 - p)
    return (1 - p) + SPREAD / 2 + fee


# ── 1. WANG TRANSFORM ─────────────────────────────────────────────────────────
def fit_lambda(data):
    """Best single lambda for p_market = Phi(Phi^-1(p_true) + lambda), found by
    minimizing the Brier score of the de-distorted price vs realized outcomes."""
    best = (None, 1e9)
    lam = -0.60
    while lam <= 0.80:
        brier = sum((P.phi(P.phi_inv(clip(d["p"])) - lam) - d["y"]) ** 2 for d in data) / len(data)
        if brier < best[1]:
            best = (lam, brier)
        lam += 0.01
    return best


def wang_table(data):
    print("1. WANG-TRANSFORM distortion (is YES overpriced, and where it sign-flips):")
    print(f"   {'price bin':12} {'n':>4} {'avg price':>9} {'realized':>9} {'lambda':>8}")
    bins = [(0.0, .2), (.2, .35), (.35, .5), (.5, .65), (.65, .8), (.8, 1.0)]
    for lo, hi in bins:
        g = [d for d in data if lo <= d["p"] < hi]
        if len(g) < 5:
            continue
        ap = sum(d["p"] for d in g) / len(g)
        rr = sum(d["y"] for d in g) / len(g)
        lam = P.phi_inv(clip(ap)) - P.phi_inv(clip(rr))
        flag = "YES overpriced->fade" if lam > 0.03 else ("YES underpriced" if lam < -0.03 else "~fair")
        print(f"   [{lo:.2f},{hi:.2f}) {len(g):>4} {ap:>9.3f} {rr:>9.3f} {lam:>+8.3f}  {flag}")
    lam, brier = fit_lambda(data)
    print(f"   -> best-fit global lambda = {lam:+.3f}  (Yang 2026 reports ~+0.18 on Polymarket; "
          f"in-sample Brier {brier:.4f})")
    return lam


# ── 2 & 4. FADE BACKTEST + RISK-PREMIUM DECOMPOSITION ─────────────────────────
def fade_analysis(data, lam):
    fades = [d for d in data if BAND[0] <= d["p"] <= BAND[1]]
    raw, premium, resid = [], [], []
    for d in fades:
        cost = entry_cost_no(d["p"])
        pf = P.phi(P.phi_inv(clip(d["p"])) - lam)        # de-distorted "fair" YES prob
        raw.append((1 - d["y"]) - cost)                  # realized NO P&L after cost
        premium.append((d["p"] - pf) - SPREAD / 2)       # edge if true prob == fair (the premium)
        resid.append(pf - d["y"])                        # MY metric: alpha beyond the premium
    print(f"\n2. COST-HAIRCUT fade backtest  (band {BAND}, buy NO at ask, spread {SPREAD})")
    m, lo, hi = boot_ci(raw)
    sr, psr = P.psr(raw)
    print(f"   n={len(raw)}  ${m:+.4f}/trade  95% CI [{lo:+.4f}, {hi:+.4f}]  "
          f"PSR(>0)={psr:.3f}" if psr is not None else f"   n={len(raw)} too few")
    print(f"   verdict: {'REAL after costs (CI>0)' if lo>0 else 'NOT significant after costs (CI includes 0)'}")

    print("\n4. *** RISK-PREMIUM vs RESIDUAL-ALPHA (my decomposition) ***")
    pm, plo, phi_ = boot_ci(premium)
    rm, rlo, rhi = boot_ci(resid)
    print(f"   risk-premium component : {pm:+.4f}/trade  [{plo:+.4f}, {phi_:+.4f}]   "
          f"(what you'd earn even if the de-distorted price were perfectly calibrated)")
    print(f"   RESIDUAL ALPHA         : {rm:+.4f}/trade  [{rlo:+.4f}, {rhi:+.4f}]   "
          f"(fair prob - realized outcome; >0 = genuine mispricing beyond the premium)")
    if rlo > 0:
        print("   -> Residual CI EXCLUDES 0: there IS alpha beyond the risk premium. (Verify out-of-sample!)")
    elif rhi < 0:
        print("   -> Residual CI < 0: outcomes BEAT fair value — fading is worse than the premium suggests.")
    else:
        print("   -> Residual CI INCLUDES 0: the fade is (statistically) JUST harvesting the risk")
        print("      premium, not exploiting a mispricing. Real, but it's compensation for risk —")
        print("      and on thin sub-0.55 books the spread can eat most of it. THIS is the honest answer.")
    return raw


# ── 3. CALIBRATION-SLOPE BUCKET GATE ──────────────────────────────────────────
def bucket_gate(data, lam):
    print("\n3. CALIBRATION-SLOPE by category (where does fading have +EV after costs?)")
    print(f"   {'category':10} {'n':>4} {'fade $/trade':>12} {'95% CI':>20} {'gate'}")
    cats = sorted(set(d["cat"] for d in data))
    for cat in cats:
        g = [d for d in data if d["cat"] == cat and BAND[0] <= d["p"] <= BAND[1]]
        if len(g) < 8:
            print(f"   {cat:10} {len(g):>4} {'--':>12} {'(too few)':>20}")
            continue
        pnl = [(1 - d["y"]) - entry_cost_no(d["p"]) for d in g]
        m, lo, hi = boot_ci(pnl)
        gate = "FADE (CI>0)" if lo > 0 else ("skip (loses)" if hi < 0 else "skip (noise)")
        print(f"   {cat:10} {len(g):>4} {m:>+12.4f} {f'[{lo:+.3f},{hi:+.3f}]':>20} {gate}")


# ── 5. KELLY (gated off until the edge is confirmed) ──────────────────────────
def kelly_demo(raw):
    print("\n5. SIZING (fractional Kelly fed the LOWER CI bound — GATED off until CI>0)")
    _, lo, _ = boot_ci(raw)
    if lo > 0:
        # NO bet: edge=lo per $1 risked; conservative 1/4 Kelly on the lower bound
        f = 0.25 * lo
        print(f"   edge CI lower bound {lo:+.4f} > 0  ->  1/4-Kelly fraction {f:.4f} of bankroll. "
              f"(Still a HUMAN decision; re-verify out-of-sample first.)")
    else:
        print(f"   edge CI lower bound {lo:+.4f} <= 0  ->  size = 0. NOT confirmed; do not bet real money.")
        print("   (This gate is the whole point: no sizing before the CI clears zero.)")


def main():
    data = load()
    print(f"FADE RESEARCH — {len(data)} distinct resolved markets (ml/dataset.csv), PAPER only\n")
    lam = wang_table(data)
    raw = fade_analysis(data, lam)
    bucket_gate(data, lam)
    kelly_demo(raw)
    print("\nCAVEATS: lambda is fit IN-SAMPLE (circular) and this is one selection-biased era of")
    print("~365 markets. A positive read here is necessary, not sufficient — the real test is the")
    print("same analysis on thousands of OUT-OF-SAMPLE resolved markets (the Jon-Becker pull / VPS).")


if __name__ == "__main__":
    main()
