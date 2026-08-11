#!/usr/bin/env python3
"""
fade_sports_split.py — Is the favorite-longshot fade a REAL pricing edge, or
just a sports structural NO-skew? Splits the fade backtest by category and puts
a bootstrap 95% CI on each bucket. Pure offline on ml/dataset.csv — no network.

THE TEST (from the bot-brain caveat): ~62% of dataset is sports. If the fade's
edge lives only in sports, it's plausibly a structural artifact (sports YES
markets skew to NO outcomes), NOT a pricing error you can exploit broadly.
Verdict rule: the fade is a REAL edge only if NON-SPORTS $/trade > 0 with a
bootstrap 95% CI that EXCLUDES zero. Sports-only edge = treat as artifact.

Usage:  python3 fade_sports_split.py [lo hi]      # default band 0.20 0.55
"""
import sys, os, csv, collections, random, statistics as st

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "ml", "dataset.csv")
random.seed(7)


def load_rows():
    rows = list(csv.DictReader(open(DATA)))
    # one row per market — pseudo-replication inflated AUC here before; don't repeat it
    seen = collections.OrderedDict()
    for r in rows:
        seen.setdefault(r["condition_id"], r)
    out = []
    for r in seen.values():
        try:
            out.append((float(r["prob"]),
                        int(float(r["final_outcome"])),
                        (r.get("category") or "other").lower()))
        except Exception:
            pass
    return out


def fade_payoffs(rows, lo, hi, spread=0.02):
    """Per-trade payoff list for: buy NO on markets priced in [lo,hi], hold to
    resolution, charge spread. NO pays 1 if final outcome is NO (y==0)."""
    pays = []
    for price, y, _cat in rows:
        if lo <= price <= hi:
            entry = (1 - price) + spread / 2
            pays.append((1.0 if y == 0 else 0.0) - entry)
    return pays


def boot_ci(xs, n=10000, lo=2.5, hi=97.5):
    if len(xs) < 2:
        return (float("nan"), float("nan"))
    means = []
    k = len(xs)
    for _ in range(n):
        s = sum(xs[random.randrange(k)] for _ in range(k))
        means.append(s / k)
    means.sort()
    return (means[int(n * lo / 100)], means[int(n * hi / 100)])


def report(name, pays):
    if not pays:
        print(f"  {name:<12} n=0   (no trades in band)")
        return None
    mean = st.mean(pays)
    wr = 100 * sum(1 for p in pays if p > 0) / len(pays)
    lo, hi = boot_ci(pays)
    excl0 = (lo > 0) or (hi < 0)
    flag = "CI excludes 0" if excl0 else "CI includes 0 → noise"
    print(f"  {name:<12} n={len(pays):<4} win={wr:4.1f}%  "
          f"${mean:+.4f}/trade  95%CI[{lo:+.4f}, {hi:+.4f}]  {flag}")
    return (len(pays), mean, lo, hi, excl0)


def live_report():
    """Forward test: run the SAME sports-split CI on the live fade leg's closed
    trades that were OPENED after the .fade_forward_start marker (i.e. after
    category tagging went in). This is the confirmation that gates real money."""
    import json
    state_p = os.path.join(HERE, "scalp_lab_state.json")
    marker_p = os.path.join(HERE, ".fade_forward_start")
    cutoff = open(marker_p).read().strip() if os.path.exists(marker_p) else None
    closed = json.load(open(state_p)).get("fade", {}).get("closed", [])

    forward = [r for r in closed
               if "cat" in r and (cutoff is None or str(r.get("opened_at", "")) >= cutoff)]
    pre = len(closed) - len(forward)
    print("=== FADE forward test (LIVE) — sports-split on real paper trades ===")
    print(f"forward marker: {cutoff}")
    print(f"fade closed total {len(closed)}  | forward (tagged, post-marker) {len(forward)}"
          f"  | excluded pre-marker/untagged {pre}\n")
    if not forward:
        print(" No forward fade trades yet. The leg needs to accumulate closed exits")
        print(" opened after the marker. Re-run this in a week or two.")
        return

    def pays(rs): return [float(r.get("pnl_usdc", 0) or 0) for r in rs]
    sports = [r for r in forward if r.get("cat") == "sports"]
    nonsp  = [r for r in forward if r.get("cat") != "sports"]
    report("ALL",        pays(forward))
    report("SPORTS",     pays(sports))
    nsr = report("NON-SPORTS", pays(nonsp))

    print("\n=== VERDICT (forward) ===")
    if nsr is None or nsr[0] < 30:
        n = 0 if nsr is None else nsr[0]
        print(f" TOO EARLY: only {n} non-sports forward fades (<30). Keep accumulating;")
        print(" do NOT judge or size money yet.")
    else:
        _, m_ns, _, _, excl = nsr
        if m_ns > 0 and excl:
            print(f" CONFIRMED forward: non-sports fade +${m_ns:.4f}/trade, CI>0, n>=30.")
            print(" This is the gate for a TINY real-money test (VPS, 1/4-Kelly). Not before.")
        else:
            print(" FAILED forward: non-sports edge is noise/negative out-of-sample.")
            print(" The in-sample result did not hold. Retire the fade.")
    print("\n NOTE: live pnl_usdc is the stored mark; honest_report.py caps target")
    print(" exits. For the money decision, re-mark with the honest cap first.")


def main():
    if "--live" in sys.argv:
        live_report()
        return
    lo, hi = (float(sys.argv[1]), float(sys.argv[2])) if len(sys.argv) >= 3 else (0.20, 0.55)
    rows = load_rows()
    sports = [r for r in rows if r[2] == "sports"]
    nonsport = [r for r in rows if r[2] != "sports"]
    print(f"=== FADE sports-split — band [{lo}, {hi}], spread 0.02, hold-to-resolution ===")
    print(f"markets (deduped): {len(rows)}  | sports {len(sports)}  non-sports {len(nonsport)}\n")

    allr = report("ALL",        fade_payoffs(rows, lo, hi))
    spr  = report("SPORTS",     fade_payoffs(sports, lo, hi))
    nsr  = report("NON-SPORTS", fade_payoffs(nonsport, lo, hi))

    print("\n=== VERDICT ===")
    if nsr is None:
        print(" NON-SPORTS: no trades in band — cannot confirm a broad edge. Treat fade as sports-only.")
        return
    n_ns, m_ns, lo_ns, hi_ns, excl_ns = nsr
    if m_ns > 0 and excl_ns and n_ns >= 30:
        print(f" REAL broad edge: non-sports fade is +${m_ns:.4f}/trade with CI>0 over {n_ns} markets.")
        print(" The edge is NOT just sports structure. Worth a forward confirmation.")
    elif m_ns > 0 and excl_ns:
        print(f" PROMISING but THIN: non-sports CI>0 but only {n_ns} markets (<30). Need more before trusting.")
    else:
        print(" SPORTS ARTIFACT: outside sports the fade is noise (CI includes 0) or negative.")
        print(" The 'edge' is the sports NO-skew, not a pricing error. Do NOT size real money on it.")
    print("\n NOTE: in-sample, single collection era. Even a CI>0 here must still be")
    print(" confirmed FORWARD on markets that resolve after today before any money.")


if __name__ == "__main__":
    main()
