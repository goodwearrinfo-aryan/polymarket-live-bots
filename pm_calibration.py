#!/usr/bin/env python3
"""
pm_calibration.py — prediction-market calibration + risk-premium (Wang lambda),
ported method-only (NO 36GiB download) from two external repos and applied to a
LIVE-fetched resolved-market sample:

  * Jon-Becker/prediction-market-analysis — decile-bucket calibration curve + ECE,
    winner label from outcome_prices (p>0.99 => that side won). The honest method,
    just run on a live sample instead of their 36GiB parquet store.
  * YichengYang-Ethan/prediction-market-pricing — the Wang(2000) transform
        p_mkt = Phi( Phi^-1(p_true) + lambda )
    fit by MLE:  lambda_hat = argmax Σ[ y ln Phi(z-λ) + (1-y) ln(1-Phi(z-λ)) ],
    z = Phi^-1(p_mkt). External benchmark: lambda_hat = 0.176 (2,460 Polymarket
    contracts) / 0.183 pooled (291,309 contracts, 6 platforms). Aryan's BRAIN
    claims lambda ~= 0.22 in fade markets — this tool checks that against a live
    sample. Theorem: lambda>0 => favorite-longshot bias is structural risk
    pricing, not behavioral (all events overpriced, ratio decreasing in p*).

Stdlib only (urllib, json, math, statistics.NormalDist). Paper-only research.
  python3 pm_calibration.py            # synthetic self-test + live sample
  python3 pm_calibration.py --n 200    # bigger live sample
"""
from __future__ import annotations
import json, math, sys, urllib.request, urllib.parse
from statistics import NormalDist

N = NormalDist()
PHI = N.cdf          # standard normal CDF  (Phi)
PHIINV = N.inv_cdf   # standard normal quantile (Phi^-1)
EPS = 1e-9
GAMMA = "https://gamma-api.polymarket.com/markets"
CLOB = "https://clob.polymarket.com/prices-history"

# external benchmarks (from the Wang-transform paper) + Aryan's in-sample claim
LAMBDA_POLY = 0.176     # 2,460 resolved Polymarket contracts, p<1e-10
LAMBDA_POOL = 0.183     # 291,309 contracts across 6 real-money platforms
LAMBDA_BRAIN = 0.22     # BRAIN.md: "Wang transform λ≈0.22 in fade markets"


# ---------------------------------------------------------------- Wang lambda
def _negloglik(lam: float, z: list[float], y: list[int]) -> float:
    s = 0.0
    for zi, yi in zip(z, y):
        p = min(1 - EPS, max(EPS, PHI(zi - lam)))
        s += yi * math.log(p) + (1 - yi) * math.log(1 - p)
    return -s


def wang_lambda_mle(p_mkt: list[float], y: list[int]) -> float:
    """MLE of the Wang risk-premium lambda. Golden-section on the concave LL."""
    z = [PHIINV(min(1 - EPS, max(EPS, p))) for p in p_mkt]
    lo, hi = -2.0, 2.0
    gr = (math.sqrt(5) - 1) / 2
    c = hi - gr * (hi - lo)
    d = lo + gr * (hi - lo)
    fc, fd = _negloglik(c, z, y), _negloglik(d, z, y)
    for _ in range(80):
        if fc < fd:
            hi, d, fd = d, c, fc
            c = hi - gr * (hi - lo); fc = _negloglik(c, z, y)
        else:
            lo, c, fc = c, d, fd
            d = lo + gr * (hi - lo); fd = _negloglik(d, z, y)
    return (lo + hi) / 2


# ----------------------------------------------------- Jon-Becker calibration
def calibration_by_bucket(p_mkt: list[float], y: list[int], nb: int = 10):
    """Decile buckets: (lo, hi, n, mean_pred, realized, gap). Plus ECE."""
    buckets = []
    total = len(y)
    ece = 0.0
    for b in range(nb):
        lo, hi = b / nb, (b + 1) / nb
        idx = [i for i, p in enumerate(p_mkt) if (lo <= p < hi or (b == nb - 1 and p == 1.0))]
        if not idx:
            buckets.append((lo, hi, 0, None, None, None)); continue
        pred = sum(p_mkt[i] for i in idx) / len(idx)
        real = sum(y[i] for i in idx) / len(idx)
        buckets.append((lo, hi, len(idx), pred, real, real - pred))
        ece += (len(idx) / total) * abs(real - pred)
    return buckets, ece


def favorite_longshot(p_mkt, y):
    """Summary stat: do longshots (p<0.3) underperform and favorites (p>0.7)
    overperform their price? Positive 'fl_spread' => favorite-longshot bias.

    INPUT CONTRACT (FL-premium audit 2026-06-15): p_mkt MUST be an EARLY/ENTRY
    or median price — NEVER a lifetime-average fill price. Lifetime-avg is pulled
    toward the outcome (winners up, losers down) and manufactures a phantom FL
    spread (+0.18 contaminated vs +0.002 clean on gap-immune on-chain data). If
    you feed lifetime-avg here the 'spread' is an artifact, not an edge."""
    lo_pred = [p for p in p_mkt if p < 0.3]; lo_y = [y[i] for i, p in enumerate(p_mkt) if p < 0.3]
    hi_pred = [p for p in p_mkt if p > 0.7]; hi_y = [y[i] for i, p in enumerate(p_mkt) if p > 0.7]
    out = {}
    if lo_pred:
        out["longshot"] = (len(lo_y), sum(lo_pred) / len(lo_pred), sum(lo_y) / len(lo_y))
    if hi_pred:
        out["favorite"] = (len(hi_y), sum(hi_pred) / len(hi_pred), sum(hi_y) / len(hi_y))
    if lo_pred and hi_pred:
        # bias = (favorite realized-minus-pred) - (longshot realized-minus-pred)
        out["fl_spread"] = (out["favorite"][2] - out["favorite"][1]) - (out["longshot"][2] - out["longshot"][1])
    return out


def report(p_mkt, y, title):
    print(f"\n=== {title}  (n={len(y)}) ===")
    buckets, ece = calibration_by_bucket(p_mkt, y)
    print(f"  {'bucket':>10} {'n':>5} {'pred':>7} {'realized':>9} {'gap':>7}")
    for lo, hi, n, pred, real, gap in buckets:
        if n == 0: continue
        print(f"  {lo:.1f}-{hi:.1f}  {n:>5} {pred:>7.3f} {real:>9.3f} {gap:>+7.3f}")
    print(f"  ECE (expected calibration error): {ece:.4f}")
    lam = wang_lambda_mle(p_mkt, y)
    print(f"  Wang lambda_hat (MLE): {lam:+.3f}   "
          f"[paper Polymarket {LAMBDA_POLY}, pooled {LAMBDA_POOL}, BRAIN {LAMBDA_BRAIN}]")
    fl = favorite_longshot(p_mkt, y)
    if "fl_spread" in fl:
        ls, fv = fl["longshot"], fl["favorite"]
        print(f"  longshot(<0.3): pred {ls[1]:.3f} realized {ls[2]:.3f} (n={ls[0]})  |  "
              f"favorite(>0.7): pred {fv[1]:.3f} realized {fv[2]:.3f} (n={fv[0]})")
        print(f"  favorite-longshot spread: {fl['fl_spread']:+.3f}  (positive => structural FL bias, lambda>0)")
        print("  ⚠️ CONTAMINATION CAVEAT (2026-06-15 FL-premium audit): this FL spread is computed on a")
        print("     lifetime/pre-24h price PROXY, which is pulled TOWARD the outcome (winners' avg up, losers'")
        print("     down) and inflates the spread. On the gap-immune fully-on-chain 726-mkt truth, clean FL")
        print("     = +0.002 (favorites carry NO real premium). Any FL/edge claim MUST use early/entry-median")
        print("     price, never lifetime-average. The harvest is DEAD taker+maker — see BRAIN.md retraction.")
    return lam, ece


# ------------------------------------------------------------- synthetic test
def _det_rand(seed):
    s = seed & 0xFFFFFFFF
    while True:
        s ^= (s << 13) & 0xFFFFFFFF; s ^= s >> 17; s ^= (s << 5) & 0xFFFFFFFF
        yield (s & 0xFFFFFF) / 0xFFFFFF


def synthetic_test(n=4000, true_lambda=0.18):
    """Draw p_true ~ U(0,1), set p_mkt = Phi(Phi^-1(p_true)+lambda), resolve YES
    with prob p_true. A correct MLE must recover ~true_lambda; calibration curve
    must sag below the diagonal (market overprices). Verifies the tool itself."""
    rng = _det_rand(20260614)
    p_mkt, y = [], []
    for _ in range(n):
        p_true = min(1 - EPS, max(EPS, next(rng)))
        pm = PHI(PHIINV(p_true) + true_lambda)
        p_mkt.append(pm)
        y.append(1 if next(rng) < p_true else 0)
    lam, ece = report(p_mkt, y, f"SYNTHETIC self-test (injected lambda={true_lambda})")
    ok = abs(lam - true_lambda) < 0.03
    print(f"  -> tool {'VERIFIED' if ok else 'FAILED'}: recovered {lam:+.3f} vs injected {true_lambda}")
    return ok


# ----------------------------------------------------------- live real sample
def _get(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": "pm-calibration/1.0"})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())


def _price_proxy(token_id, end_ts):
    """Representative pre-resolution YES price: time-average of CLOB history over
    the market's life, EXCLUDING the last 24h (avoids the resolution 0/1 spike)."""
    try:
        q = urllib.parse.urlencode({"market": token_id, "interval": "max", "fidelity": "180"})
        h = _get(f"{CLOB}?{q}").get("history", [])
        pts = [pt["p"] for pt in h if end_ts is None or pt.get("t", 0) < end_ts - 86400]
        if len(pts) >= 3:
            return sum(pts) / len(pts)
    except Exception:
        pass
    return None


def fetch_live_sample(n=150):
    """Pull resolved Polymarket markets from gamma; label YES outcome from
    outcome_prices (Jon-Becker rule), price proxy from CLOB history. Robust:
    skips anything that doesn't yield a clean (price, outcome)."""
    import datetime as _dt
    p_mkt, y, scanned = [], [], 0
    offset = 0
    print(f"\n[live] fetching resolved markets from gamma (target {n})...", flush=True)
    while len(y) < n and offset < 4000:
        try:
            batch = _get(f"{GAMMA}?closed=true&limit=100&offset={offset}&order=endDate&ascending=false")
        except Exception as e:
            print("  gamma error:", e); break
        offset += 100
        if not batch:
            break
        for m in batch:
            scanned += 1
            try:
                op = m.get("outcomePrices")
                prices = json.loads(op) if isinstance(op, str) else op
                if not prices or len(prices) != 2:
                    continue
                p0, p1 = float(prices[0]), float(prices[1])
                if p0 > 0.99 and p1 < 0.01:   won = 0
                elif p0 < 0.01 and p1 > 0.99: won = 1
                else:                          continue   # not cleanly resolved
                toks = m.get("clobTokenIds")
                toks = json.loads(toks) if isinstance(toks, str) else toks
                if not toks or len(toks) != 2:
                    continue
                end_ts = None
                if m.get("endDate"):
                    try: end_ts = int(_dt.datetime.fromisoformat(m["endDate"].replace("Z", "+00:00")).timestamp())
                    except Exception: end_ts = None
                pm = _price_proxy(toks[0], end_ts)   # YES-token price proxy
                if pm is None or not (0.0 < pm < 1.0):
                    continue
                p_mkt.append(pm); y.append(1 if won == 0 else 0)   # YES wins iff outcome0
            except Exception:
                continue
        print(f"  ...{len(y)} usable / {scanned} scanned", flush=True)
    return p_mkt, y


def main():
    n = 150
    if "--n" in sys.argv:
        try: n = int(sys.argv[sys.argv.index("--n") + 1])
        except Exception: pass
    print("=" * 64)
    print("  PM CALIBRATION  — Jon-Becker buckets + Wang lambda (no 36GiB DL)")
    print("=" * 64)
    synthetic_test()
    p_mkt, y = fetch_live_sample(n)
    if len(y) >= 30:
        report(p_mkt, y, "LIVE resolved Polymarket sample (price proxy = pre-24h CLOB avg)")
        print("\n  NOTE: small live sample + price proxy (not trade-level). Direction/"
              "sign is indicative; the paper's lambda~0.18 stands on 291k contracts.")
    else:
        print(f"\n  live sample too small (n={len(y)}) — gamma drops old resolved markets "
              "(BRAIN: 6/8->1/8 at 14d). Tool is verified on synthetic; for a large honest\n"
              "  sample you'd need the on-chain fill history (collect_onchain) + ctf_resolution labels.")


if __name__ == "__main__":
    main()
