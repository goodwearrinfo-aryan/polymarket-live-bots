#!/usr/bin/env python3
"""multiarb.py — the STRUCTURAL-ARB MULTI-LEG: combines the project's only two
non-predictive +EV sources into ONE gated track with ONE combined CI.

WHY THIS AND NOT "combine the profitable scalp legs": the profitable scalp legs are
lucky correlated longshot tails (BRAIN: jackknife → top-3 trades = 101% of profit, 90%
one geopolitical cluster). Combining THOSE stacks variance, not edge. The genuinely
+EV-by-construction sources are:
  • basket  — locked combinatorial arb (Σask<1), profit independent of outcome (basket_paper)
  • data    — settled-but-mispriced data arb, hold-to-resolution (dataarb)
They ride DIFFERENT drivers (book structure vs resolving data series), so combining them
DIVERSIFIES — the combined CI is more robust than either alone. This is read-only over the
two engines' existing books (no re-entry, no double-booking, no race): the multi-leg is the
PORTFOLIO of the two arbs, scored on the same bar as everything else.

GATE (same as every leg): combined real n>=30 AND bootstrap CI>0 AND mean>control AND control<=0.

Run:  python3 multiarb.py            # print the combined scoreboard
      python3 multiarb.py once       # + write Vault/Reports/multiarb.md (watchdog mode)
"""
import os, sys, json, time

BASE = os.path.dirname(os.path.abspath(__file__))
DATAARB = os.path.join(BASE, "dataarb_state.json")
BASKET = os.path.join(BASE, "basket_paper_book.json")
BASKETRAND = os.path.join(BASE, "basketrand_book.json")   # legacy/phantom (nothing writes it)
BASKETLOCK = os.path.join(BASE, "basketlock_state.json")  # executable lock engine: real + control
MONOARB = os.path.join(BASE, "monoarb_state.json")        # monotonicity/consistency arb
VAULT = os.path.expanduser("~/Documents/PolymarketVault/Reports/multiarb.md")


def _load(p):
    try:
        return json.load(open(p))
    except Exception:
        return {}


def _boot_ci(xs, iters=4000):
    """Deterministic bootstrap 95% CI of the mean (seeded — reproducible)."""
    if len(xs) < 2:
        return (None, None)
    import random
    rnd = random.Random(42)
    n = len(xs)
    means = sorted(sum(xs[rnd.randrange(n)] for _ in range(n)) / n for _ in range(iters))
    return (round(means[int(0.025 * iters)], 4), round(means[int(0.975 * iters)], 4))


def _stat(xs):
    if not xs:
        return {"n": 0, "mean": None, "total": 0.0, "ci": (None, None)}
    return {"n": len(xs), "mean": round(sum(xs) / len(xs), 4),
            "total": round(sum(xs), 2), "ci": _boot_ci(xs)}


def _q(rec):
    """Best-effort market question/title for theme clustering."""
    return rec.get("q") or rec.get("title") or rec.get("slug") or rec.get("question") or ""


def collect():
    """Pull realized exits from both arb engines, split real vs control by sub-leg.
    Also returns real_pos = per-REAL-position {q, realized_pnl} so the gate can cluster the
    combined CI BY THEME (45 correlated election locks must count as ~1 draw, not 45 — the
    green-book-concentration lesson applied to the arb track itself, 2026-06-20)."""
    legs = {"basket_real": [], "data_real": [], "mono_real": [],
            "data_ctrl": [], "basket_ctrl": [], "mono_ctrl": []}
    real_pos = []  # {status, realized_pnl, q} for analyst_correlation.cluster_block_bootstrap_ci

    def add_real(pnl, rec):
        real_pos.append({"status": "settled", "realized_pnl": pnl, "q": _q(rec)})

    d = _load(DATAARB)
    for p in d.get("resolved", []):
        if p.get("pnl") is None:
            continue
        if p.get("kind") == "control":
            legs["data_ctrl"].append(p["pnl"])
        else:
            legs["data_real"].append(p["pnl"]); add_real(p["pnl"], p)

    mo = _load(MONOARB)
    for p in mo.get("resolved", []):
        if p.get("pnl") is None:
            continue
        if p.get("kind") == "control":
            legs["mono_ctrl"].append(p["pnl"])
        else:
            legs["mono_real"].append(p["pnl"]); add_real(p["pnl"], p)

    b = _load(BASKET)
    for rec in (b.get("closed", []) if isinstance(b, dict) else []):
        if rec.get("status") == "dropped_unresolved" or rec.get("realized_pnl") is None:
            continue
        legs["basket_real"].append(rec["realized_pnl"]); add_real(rec["realized_pnl"], rec)

    r = _load(BASKETRAND)
    for rec in (r.get("closed", []) if isinstance(r, dict) else []):
        if rec.get("realized_pnl") is not None and rec.get("status") != "dropped_unresolved":
            legs["basket_ctrl"].append(rec["realized_pnl"])

    # basketlock = the EXECUTABLE complete-set lock engine. It resolves BOTH real locks and
    # its basketrand control into one "resolved" list, tagged by kind. multiarb previously
    # ignored it entirely AND read its control from a phantom basketrand_book.json that nothing
    # writes — so the combined control was permanently empty (false-graduate risk). Wire the
    # real source here: kind=="control" → basket_ctrl (the must-lose comparator), else basket_real.
    bl = _load(BASKETLOCK)
    for rec in (bl.get("resolved", []) if isinstance(bl, dict) else []):
        if rec.get("realized_pnl") is None:
            continue
        if rec.get("kind") == "control":
            legs["basket_ctrl"].append(rec["realized_pnl"])
        else:
            legs["basket_real"].append(rec["realized_pnl"]); add_real(rec["realized_pnl"], rec)
    return legs, real_pos


def build():
    legs, real_pos = collect()
    real = legs["basket_real"] + legs["data_real"] + legs["mono_real"]   # the combined multi-leg
    ctrl = legs["data_ctrl"] + legs["basket_ctrl"] + legs["mono_ctrl"]   # combined control (must lose)
    rs, cs = _stat(real), _stat(ctrl)
    sub = {k: _stat(v) for k, v in legs.items()}

    # THEME-CLUSTER CI (the honest gate): the naive _boot_ci treats every position as an
    # independent draw — but the arb track is ~73% election-themed (arb-allocator 2026-06-20),
    # so correlated locks would over-graduate. cluster_block_bootstrap_ci resamples whole theme
    # blocks → correlated locks count as ~1 draw. GATE ON THE CLUSTER CI, and a single-theme
    # book is DEGENERATE (≈1 effective draw) → cannot graduate.
    clus = {}
    try:
        import analyst_correlation as _ac
        clus = _ac.cluster_block_bootstrap_ci(real_pos)
    except Exception as e:
        clus = {"error": str(e)}
    ci_pos = bool(clus.get("ci_positive_cluster") and not clus.get("degenerate"))

    gate = {
        "n_ok": rs["n"] >= 30,
        "ci_pos": ci_pos,   # theme-clustered CI>0 AND >=2 themes (NOT the naive per-lock CI)
        "beats_control": (rs["mean"] is not None and
                          (cs["mean"] is None or rs["mean"] > cs["mean"])),
        "control_loses": (cs["mean"] is None or cs["mean"] <= 0),
    }
    gate["GRADUATE"] = all(gate.values())
    return {"ts": time.strftime("%Y-%m-%d %H:%M"), "combined_real": rs, "cluster": clus,
            "combined_control": cs, "sub_legs": sub, "gate": gate}


def fmt(s):
    L = ["=" * 66,
         "  multiarb — STRUCTURAL-ARB MULTI-LEG (basket + data, hold-to-resolution)",
         "=" * 66]
    def line(name, st):
        if not st["n"]:
            return f"  {name:16} n=0  (accumulating)"
        lo, hi = st["ci"]
        ci = f"  CI[{lo:+.3f},{hi:+.3f}]" if lo is not None else "  CI(n<2)"
        return f"  {name:16} n={st['n']:>3}  ${st['mean']:+.4f}/exit  total ${st['total']:+.2f}{ci}"
    L.append("  -- sub-legs --")
    for k in ("basket_real", "data_real", "mono_real"):
        L.append(line(k, s["sub_legs"][k]))
    L.append("  -- combined --")
    L.append(line("REAL (combined)", s["combined_real"]))
    L.append(line("control", s["combined_control"]))
    g = s["gate"]
    if s["combined_real"]["n"] == 0:
        L.append("  VERDICT: accumulating — both arbs hold to resolution; 0 exits booked yet.")
    elif g["GRADUATE"]:
        L.append("  VERDICT: ✅ GRADUATE — combined arb clears n>=30 + CI>0 + beats losing control.")
    else:
        miss = [k for k in ("n_ok", "ci_pos", "beats_control", "control_loses") if not g[k]]
        L.append(f"  VERDICT: not yet ({', '.join(miss)} unmet) — keep accumulating, do NOT scale.")
    L.append("  NOTE: combine is at the BOOK level (portfolio of two arbs). basket = structural")
    L.append("  lock (outcome-independent); data = settled-mispricing. Different drivers = real")
    L.append("  diversification, not stacked variance. Entry/settlement stay in basket_paper + dataarb.")
    return "\n".join(L)


def main():
    s = build()
    out = fmt(s)
    print(out)
    if len(sys.argv) > 1 and sys.argv[1] == "once":
        try:
            os.makedirs(os.path.dirname(VAULT), exist_ok=True)
            with open(VAULT, "w") as f:
                f.write("# multiarb — structural-arb multi-leg\n\n```\n" + out + "\n```\n")
        except Exception:
            pass


if __name__ == "__main__":
    main()
