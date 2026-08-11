#!/usr/bin/env python3
"""leg_health.py — is each leg's result REAL or a lucky streak? (read-only)

For every scalp_lab leg: honest realized P&L (capped), exit count, and a
bootstrap 95% CI on mean P&L-per-exit. A leg is only "graduated" (callable)
when it has >=10 exits AND its P&L CI excludes 0. Also reads the latest ML
retrain's bootstrap edge CI from ml/model_meta.json. Run: python3 leg_health.py
"""
import os, sys, json, glob, random
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import honest_report as hr
import psr_dsr as P   # Probabilistic + Deflated Sharpe (skew + best-of-N correction)

LEGS = ("dip", "scalp", "fade", "fastfade", "allin", "momentum", "favyes", "coinflip", "midfade",
        "truefade", "deepfade", "nsfade", "wangfade",
        "manifold", "metaculus",
        "endgame", "lowliq", "contrarian", "catpol",
        "coinup", "coindown",
        "diverg", "feargreed", "lateprox",
        "fgcrypto",
        "nearterm",
        "birdeye", "liqcrush",
        "newscrypto", "newsesports")
CONTROLS = {"allin", "coinflip"}

def boot_ci(vals, n=3000, seed=0):
    if len(vals) < 2:
        return (float('nan'), float('nan'), float('nan'))
    rng = random.Random(seed); N = len(vals); means = []
    for _ in range(n):
        s = sum(vals[rng.randrange(N)] for _ in range(N)) / N
        means.append(s)
    means.sort()
    return (sum(vals)/N, means[int(n*0.025)], means[int(n*0.975)])

def main():
    state = json.load(open(hr.find_state()))
    print("LEG HEALTH — realized (exit) P&L with bootstrap significance")
    print(f"{'leg':9} {'exits':>5} {'mean/exit':>10} {'95% CI':>20} {'verdict'}")
    for leg in LEGS:
        book = state.get(leg, {})
        c = book.get("closed", [])
        n_open = len(book.get("open", []))
        # No-price "stale_nodata" exits are booked at entry_fill ($0 P&L) because
        # there is no market to mark against. They are NOT informative trades:
        # counting them as breakeven dilutes the mean with zeros and (worse) lets
        # parked losers masquerade as graduated exits, defeating the survivorship
        # guard. Exclude them from the bootstrap, the >=10 gate, and the verdict —
        # only report how many were dropped.
        informative = [t for t in c if t.get("reason") in ("target", "stop", "time")]
        n_nodata = sum(1 for t in c if t.get("reason") == "stale_nodata")
        pnls = [hr.honest_pnl(leg, t) for t in informative]
        n = len(pnls)
        nd = f" +{n_nodata} no-data excl" if n_nodata else ""
        if n == 0:
            print(f"{leg:9} {0:>5} {'--':>10} {'--':>20} no priced exits yet  (open={n_open}{nd})"); continue
        mean, lo, hi = boot_ci(pnls)
        tag = "control" if leg in CONTROLS else ""
        if n < 10:
            verdict = f"too few (<10) {tag}".strip()
        elif lo > 0:
            verdict = f"REAL +EV (CI excludes 0) {tag}".strip()
        elif hi < 0:
            verdict = f"REAL losing (CI excludes 0) {tag}".strip()
        else:
            verdict = f"noise (CI includes 0) {tag}".strip()
        # ── SURVIVORSHIP GUARD ───────────────────────────────────────────
        # A +EV verdict is only trustworthy if winners are matched by REAL
        # (price-based) losses. stale_nodata is NOT a real loss — it's a $0
        # no-price close. Count only stop/time as loss-exits. If a non-control
        # leg has essentially no real losses while positions are still parked
        # open OR a pile of no-data closes exists, its winners are survivorship.
        n_realloss = sum(1 for t in informative if t.get("reason") in ("stop", "time"))
        if leg not in CONTROLS and (n_open > 0 or n_nodata > 0) and n_realloss / n <= 0.10:
            verdict = (f"!! SURVIVORSHIP ({n_realloss}/{n} real-loss exits; "
                       f"{n_open} open, {n_nodata} no-data) — UNRELIABLE")
        print(f"{leg:9} {n:>5} {mean:>+10.4f} {f'[{lo:+.3f},{hi:+.3f}]':>20} {verdict}  (open={n_open}{nd})")
    # ── EXIT-REASON BREAKDOWN ─────────────────────────────────────────────
    # Surfaces survivorship at a glance: a leg with target wins but ~zero
    # stop/time (real-loss) exits is suspect, regardless of how many losers
    # were force-closed at $0 via stale_nodata. wins/losses are over PRICED
    # exits only (no-data closes are $0 and excluded from both).
    print("\nEXIT-REASON BREAKDOWN (priced exits only; no-data shown separately):")
    print(f"{'leg':9} {'target':>6} {'stop':>5} {'time':>5} {'nodata':>7} {'realloss':>9} {'wins':>5} {'losses':>7}")
    for leg in LEGS:
        c = state.get(leg, {}).get("closed", [])
        rc = {r: sum(1 for t in c if t.get("reason") == r) for r in ("target", "stop", "time", "stale_nodata")}
        priced = [t for t in c if t.get("reason") in ("target", "stop", "time")]
        wins = sum(1 for t in priced if hr.honest_pnl(leg, t) > 0)
        losses = sum(1 for t in priced if hr.honest_pnl(leg, t) < 0)
        realloss = rc["stop"] + rc["time"]
        print(f"{leg:9} {rc['target']:>6} {rc['stop']:>5} {rc['time']:>5} "
              f"{rc['stale_nodata']:>7} {realloss:>9} {wins:>5} {losses:>7}")
    # ── PROBABILISTIC & DEFLATED SHARPE ──────────────────────────────────────
    # PSR corrects the Sharpe test for the fade's skew/fat tails (a plain CI
    # overstates skewed edges). DSR deflates for SELECTION BIAS: we A/B-test 9
    # legs and crown the best, so the bar is "beat the luckiest of 9 noise legs",
    # not "beat 0". A non-control leg is only believable when BOTH are high.
    print("\nPROBABILISTIC / DEFLATED SHARPE (skew + best-of-9 selection correction):")
    print(f"{'leg':9} {'n':>4} {'Sharpe':>8} {'PSR(>0)':>9} {'DSR(>max9)':>11} {'verdict'}")
    leg_pnls = {}
    for leg in LEGS:
        c = state.get(leg, {}).get("closed", [])
        leg_pnls[leg] = [hr.honest_pnl(leg, t) for t in c
                         if t.get("reason") in ("target", "stop", "time")]
    all_sr = [P.sharpe(v) for v in leg_pnls.values()]
    for leg in LEGS:
        pn = leg_pnls[leg]; n = len(pn)
        sr, psr_p = P.psr(pn)
        dsr_p, _ = P.dsr(pn, all_sr, N=len(LEGS))
        if sr is None or psr_p is None:
            print(f"{leg:9} {n:>4} {'--':>8} {'--':>9} {'--':>11} too few"); continue
        if leg in CONTROLS:
            v = "control"
        elif dsr_p is not None and dsr_p > 0.95:
            v = "REAL (survives deflation)"
        elif psr_p > 0.95:
            v = "PSR ok but FAILS deflation (best-of-9 luck?)"
        else:
            v = "not significant"
        dp = f"{dsr_p:.3f}" if dsr_p is not None else "--"
        print(f"{leg:9} {n:>4} {sr:>+8.3f} {psr_p:>9.3f} {dp:>11} {v}")
    print("  Read: PSR>0.95 = edge beats 0 after skew; DSR>0.95 = it ALSO beats the")
    print("  best of 9 lucky noise-legs. The fade must clear DSR, not just PSR.")

    # ML edge significance from the Mac's latest retrain
    print("\nML model (from ml/model_meta.json, last Mac retrain):")
    try:
        meta = json.load(open(os.path.join(HERE, "ml", "model_meta.json")))
        d = meta.get("direction", {})
        ci = d.get("edge_ci95"); p = d.get("edge_p_gt0")
        if ci:
            verdict = "REAL (CI excludes 0)" if ci[0] and ci[0] > 0 else "NOISE (CI includes 0)"
            print(f"  edge {d.get('edge')}  CI {ci}  P(edge>0)={p}  -> {verdict}")
        else:
            print(f"  edge {d.get('edge')} (no bootstrap CI yet — retrain on the Mac to populate it)")
    except Exception as e:
        print(f"  model_meta.json unreadable: {e}")
    print(f"\n  Network (Mac->Polymarket): {hr.gamma_status()}")

if __name__ == "__main__":
    main()
