#!/usr/bin/env python3
"""psr_dsr.py — Probabilistic & Deflated Sharpe Ratio (Bailey & López de Prado),
ported as ~stdlib-only math from the survey of open-source quant tooling.

Why this project needs it (two known caveats it directly addresses):
  • The fade's P&L is skewed (many small wins, occasional full loss). A plain
    mean-bootstrap CI OVERSTATES significance for skewed/fat-tailed returns. PSR
    corrects the Sharpe test for skewness & kurtosis -> P(true Sharpe > 0).
  • We A/B-test ~9 legs and crown the best. "Best of 9 by luck" is selection
    bias. DSR sets the benchmark to the EXPECTED MAXIMUM Sharpe of N trials, so a
    leg only passes if it beats what the luckiest of N noise-legs would show.

No numpy/scipy. Phi via erf; inverse-Phi via Acklam's rational approximation.
"""
import math, statistics, random

EULER = 0.5772156649015329  # Euler-Mascheroni


def boot_ci(vals, n=5000, seed=None):
    """Bootstrap (mean, 2.5%ile, 97.5%ile) of the sample mean. seed=None uses the
    global RNG so a caller's random.seed(...) governs reproducibility; pass an int
    for a private deterministic RNG. Canonical home for the project's bootstrap CI."""
    L = len(vals)
    if L < 2:
        return (float("nan"),) * 3
    rng = random.Random(seed) if seed is not None else random
    m = sum(vals) / L
    means = [sum(vals[rng.randrange(L)] for _ in range(L)) / L for _ in range(n)]
    means.sort()
    return (m, means[int(.025 * n)], means[int(.975 * n)])


def phi(z):
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def phi_inv(p):
    """Inverse standard-normal CDF (Acklam). Good to ~1e-9 on (0,1)."""
    if p <= 0.0:
        return -math.inf
    if p >= 1.0:
        return math.inf
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
               ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
               ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    q = p - 0.5; r = q*q
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / \
           (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)


def sharpe(x):
    """Per-trade Sharpe (mean/std). Returns None if undefined."""
    n = len(x)
    if n < 2:
        return None
    m = sum(x) / n
    sd = (sum((v - m) ** 2 for v in x) / n) ** 0.5
    return None if sd == 0 else m / sd


def _skew_kurt(x):
    n = len(x); m = sum(x) / n
    sd = (sum((v - m) ** 2 for v in x) / n) ** 0.5
    if sd == 0:
        return 0.0, 3.0
    skew = sum(((v - m) / sd) ** 3 for v in x) / n
    kurt = sum(((v - m) / sd) ** 4 for v in x) / n   # Pearson (normal = 3)
    return skew, kurt


def psr(pnls, sr_benchmark=0.0):
    """P(true per-trade Sharpe > sr_benchmark), skew/kurtosis-corrected.
    Returns (observed_sharpe, probability) or (None, None) if undefined."""
    n = len(pnls)
    sr = sharpe(pnls)
    if sr is None or n < 3:
        return (sr, None)
    skew, kurt = _skew_kurt(pnls)
    denom = math.sqrt(max(1e-12, 1.0 - skew * sr + (kurt - 1.0) / 4.0 * sr * sr))
    z = (sr - sr_benchmark) * math.sqrt(n - 1) / denom
    return (sr, phi(z))


def expected_max_sharpe(sr_list, N=None):
    """Expected maximum Sharpe of N independent noise trials (variance from the
    observed leg Sharpes). This is the DSR benchmark."""
    sr_list = [s for s in sr_list if s is not None]
    N = N or len(sr_list)
    if N < 2 or len(sr_list) < 2:
        return 0.0
    var = statistics.pvariance(sr_list)
    if var <= 0:
        return 0.0
    g = math.sqrt(var)
    return g * ((1 - EULER) * phi_inv(1 - 1.0 / N) + EULER * phi_inv(1 - 1.0 / (N * math.e)))


def dsr(pnls, all_leg_sharpes, N=None):
    """Deflated Sharpe: PSR with the benchmark = expected-max-Sharpe of N trials.
    P(this leg's true Sharpe beats the luckiest of N noise legs)."""
    star = expected_max_sharpe(all_leg_sharpes, N)
    _, p = psr(pnls, sr_benchmark=star)
    return p, star


def psr_pvalue(pnls, sr_benchmark=0.0):
    """One-sided p-value that a leg is NOISE (true Sharpe <= benchmark), skew/kurt
    corrected = 1 - PSR(>benchmark). Small => leg looks genuinely +Sharpe. Returns
    None when the Sharpe is undefined (n<3 or zero variance). This is the per-leg
    input to benjamini_hochberg() below for the multi-leg FDR view."""
    _, prob = psr(pnls, sr_benchmark=sr_benchmark)
    return None if prob is None else 1.0 - prob


def benjamini_hochberg(pvals, alpha=0.05):
    """Benjamini-Hochberg FDR control over a FAMILY of p-values (one per leg tested).
    Returns (q_values, reject) aligned to the input order; reject[i] is True iff
    leg i is a discovery at false-discovery-rate <= alpha.

    Why this sits ALONGSIDE the DSR star, not instead of it: DSR's expected-max
    benchmark asks "did this beat the luckiest of N noise legs" (controls FWER-style
    selection bias on the WINNER). BH-FDR asks the complementary question — "across
    ALL legs I tested, which survive once I correct for testing many" — and controls
    the expected fraction of false graduations. Two independent multiplicity lenses.

    Correct step-up + monotonicity. (The 19.8k-star AI-Trader reference this was
    learned from returned a single row instead of the list and never enforced
    monotonicity at the input order — both fixed here.)"""
    m = len(pvals)
    if m == 0:
        return [], []
    order = sorted(range(m), key=lambda i: pvals[i])     # ascending p
    q = [1.0] * m
    running = 1.0
    for rank in range(m, 0, -1):                          # step-up: largest rank first
        idx = order[rank - 1]
        running = min(running, pvals[idx] * m / rank)
        q[idx] = min(running, 1.0)
    reject = [q[i] <= alpha for i in range(m)]            # q<=alpha <=> BH rejection
    return q, reject


if __name__ == "__main__":
    import random
    random.seed(0)
    win = [0.3] * 66 + [-0.6] * 34          # fade-shaped: skewed
    sr, p = psr(win)
    print(f"demo fade-shaped sample: Sharpe={sr:+.3f}  PSR(>0)={p:.3f}")
