#!/usr/bin/env python3
"""fade_checkpoint.py — the ONE gate that decides if the fade is forward-confirmed.

Distills leg_health's honest method into a single PASS / NOT-YET verdict for the
fade, so nobody has to babysit it. The fade graduates from "candidate" to
"confirmed" ONLY when ALL hold on fresh, honestly-marked exits:

  1. >=30 priced exits        (stale_nodata $0 closes excluded — they hide losses)
  2. bootstrap 95% CI > 0     (point estimates lie)
  3. NOT survivorship         (has real stop/time losses, not just parked winners)
  4. holds OUTSIDE sports      (pricing error, not a sports NO-skew artifact)
  5. controls stay negative   (allin/coinflip CI<0 => accounting sane)

Writes PASS/NOTYET to .fade_gate so the watchdog can alert only on the rising edge.
Read-only, paper. Real-money sizing is a HUMAN decision, never automatic.
"""
import os, sys, json
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import honest_report as hr
from leg_health import boot_ci, LEGS
import psr_dsr as P   # deflated Sharpe — the best-of-9 selection-bias check

GATE_N = 30
# The leg this gate JUDGES. Switched "fade" -> "truefade" 2026-06-07, then
# truefade was KILLED 2026-06-10 (6% WR — long-dated politics never moved in
# 72h). Its replacement nearresfade trades the same band (NO on YES[0.22,0.52])
# but 1-30d to resolution, so the forward gate now watches nearresfade.
TARGET = "nearresfade"
MARKER = os.path.join(HERE, ".fade_gate")


def is_sports(q):
    """q-only mirror of scalp_lab.category_of precedence (crypto/politics first,
    then sports). Approximate: trades store the truncated question, not tags."""
    b = (q or "").lower()
    if any(k in b for k in ("bitcoin", "ethereum", "crypto", "btc", "eth", "solana")):
        return False
    if any(k in b for k in ("election", "president", "senate", "congress", "minister", "vote", "poll")):
        return False
    if any(k in b for k in ("nba", "nfl", "soccer", "tennis", "ufc", "mlb", "match", "vs.", "game")):
        return True
    return False


def priced(leg, state):
    c = state.get(leg, {}).get("closed", [])
    return [t for t in c if t.get("reason") in ("target", "stop", "time")]


def ci_neg(leg, state):
    p = priced(leg, state)
    if len(p) < 10:
        return None
    _, lo, hi = boot_ci([hr.honest_pnl(leg, t) for t in p])
    return hi < 0


def main():
    state = json.load(open(hr.find_state()))
    book = state.get(TARGET, {})
    n_open = len(book.get("open", []))
    info = priced(TARGET, state)
    pnls = [hr.honest_pnl(TARGET, t) for t in info]
    n = len(pnls)
    n_nodata = sum(1 for t in book.get("closed", []) if t.get("reason") == "stale_nodata")
    n_realloss = sum(1 for t in info if t.get("reason") in ("stop", "time"))

    mean = lo = hi = float("nan")
    if n >= 2:
        mean, lo, hi = boot_ci(pnls)

    # non-sports subset — prefer the tag-based category logged at entry (forward
    # trades carry "cat"); fall back to the q-only heuristic for legacy trades.
    def trade_sports(t):
        cat = t.get("cat")
        return (cat == "sports") if cat else is_sports(t.get("q", ""))
    ns = [hr.honest_pnl(TARGET, t) for t in info if not trade_sports(t)]
    ns_mean = ns_lo = ns_hi = float("nan")
    if len(ns) >= 2:
        ns_mean, ns_lo, ns_hi = boot_ci(ns)

    survivor = (n > 0 and (n_open > 0 or n_nodata > 0) and n_realloss / n <= 0.10)
    allin_neg, coin_neg = ci_neg("allin", state), ci_neg("coinflip", state)
    controls_ok = (allin_neg is True and coin_neg is True)

    g1 = n >= GATE_N
    g2 = (lo == lo and lo > 0)              # not NaN and >0
    g3 = not survivor
    g4 = (len(ns) >= 10 and ns_lo == ns_lo and ns_lo > 0)
    g5 = controls_ok
    passed = g1 and g2 and g3 and g4 and g5

    def mark(ok): return "PASS" if ok else "----"
    print(f"FADE FORWARD GATE — leg `{TARGET}` (paper; honest priced exits only, stale-no-data excluded)")
    print(f"  [{mark(g1)}] 1. priced exits      : {n} / {GATE_N}")
    if n >= 2:
        print(f"  [{mark(g2)}] 2. 95% CI excludes 0 : {mean:+.4f}  [{lo:+.3f}, {hi:+.3f}]")
    else:
        print("  [----] 2. 95% CI excludes 0 : n/a (need >=2 exits)")
    print(f"  [{mark(g3)}] 3. not survivorship  : {n_realloss}/{n} real-loss exits"
          f"  ({n_open} open, {n_nodata} no-data)")
    if len(ns) >= 2:
        print(f"  [{mark(g4)}] 4. holds non-sports  : n={len(ns)}  {ns_mean:+.4f}  [{ns_lo:+.3f}, {ns_hi:+.3f}]")
    else:
        print(f"  [----] 4. holds non-sports  : n={len(ns)} (need >=10 non-sports exits)")
    print(f"  [{mark(g5)}] 5. controls negative : allin={allin_neg} coinflip={coin_neg}")
    # Deflated-Sharpe context (best-of-9 selection-bias corrected). Not a hard
    # gate (the 5 above are), but the honest "is it just the luckiest leg?" bar:
    all_sr = []
    for L in LEGS:
        try:
            all_sr.append(P.sharpe([hr.honest_pnl(L, t)
                                    for t in state.get(L, {}).get("closed", [])
                                    if t.get("reason") in ("target", "stop", "time")]))
        except ValueError:
            pass   # leg not in target_mid map — skip from DSR context
    sr, psr_p = P.psr(pnls)
    dsr_p, _ = P.dsr(pnls, all_sr, N=len(LEGS))
    if sr is not None and psr_p is not None:
        print(f"        fade Sharpe {sr:+.3f}  PSR(>0) {psr_p:.3f}  "
              f"DSR(>max9) {dsr_p:.3f}  (deflated bar = 0.95)")
    print("  " + "-" * 56)
    if passed:
        print("  VERDICT: ✅ GATE CLEARED — fade is forward-confirmed.")
        print("           Next: a TINY real-money test (¼-Kelly, never full) — a HUMAN")
        print("           decision, not the bot's. Re-verify before sizing up.")
    else:
        need = max(0, GATE_N - n)
        why = []
        if not g1: why.append(f"{need} more priced exits")
        if g1 and not g2: why.append("CI still includes 0")
        if not g3: why.append("survivorship (too few real losses)")
        if g1 and not g4: why.append("non-sports not yet confirmed")
        if not g5: why.append("controls not both negative")
        print(f"  VERDICT: NOT YET — {', '.join(why) or 'criteria unmet'}.")
    open(MARKER, "w").write("PASS" if passed else "NOTYET")
    print(f"\n  Network: {hr.gamma_status()}")

    # ── Birdeye / liqcrush bootstrap checkpoint (prints after main gate) ──────
    for _be_leg in ("birdeye", "liqcrush"):
        _bk = state.get(_be_leg, {})
        _closed = [t for t in _bk.get("closed", []) if t.get("reason") in ("target", "stop", "time")]
        _n = len(_closed)
        if _n == 0:
            print(f"\n  {_be_leg}: 0 priced exits — accumulating data")
            continue
        _pnls = [hr.honest_pnl(_be_leg, t) for t in _closed]
        _mean = sum(_pnls) / _n
        if _n >= 30:
            _, _lo, _hi = boot_ci(_pnls)
            _verdict = "REAL +EV" if _lo > 0 else ("REAL LOSING" if _hi < 0 else "NOISE (CI includes 0)")
            print(f"\n  {_be_leg}: {_n} exits  mean={_mean:+.4f}  CI [{_lo:+.3f},{_hi:+.3f}]  → {_verdict}")
            if _lo <= 0:
                print(f"  !! {_be_leg} CI ≤ 0 at 30 exits — KILL or retune thresholds")
        else:
            print(f"\n  {_be_leg}: {_n}/30 exits  mean={_mean:+.4f}  ({30 - _n} more to CI check)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
