#!/usr/bin/env python3
"""
basket_optimize.py — optimal capital allocation across the open basket-arb locks.
─────────────────────────────────────────────────────────────────────────────
The cuOpt skill applied to the ONE real optimization in this project: given a
small paper bankroll and the open complete-set locks, which to FUND to maximize
total locked edge ($) — WITHOUT over-concentrating in one theme (the green-book
concentration lesson: confluence in one theme = one bet wearing many hats).

MILP:
  decision  fund_i ∈ {0,1}  for each open lock i
  maximize  Σ expected_pnl_i · fund_i           (total locked $ edge)
  s.t.      Σ capital_i · fund_i ≤ BUDGET        (bankroll)
            Σ_{i in theme t} fund_i ≤ MAX_PER_THEME   (anti-concentration)

Modeled in cuOpt's Python API (Problem/addVariable/addConstraint/setObjective).
cuOpt needs an NVIDIA GPU / NIM — not on this Mac — so it auto-falls back to
scipy.optimize.milp (CPU, identical model). Provenance is printed honestly.
Read-only over the paper book; allocates nothing live. paper only.

  python3 basket_optimize.py                 # budget $100, max 3 per theme
  python3 basket_optimize.py --budget 50 --per-theme 2
"""
import os, sys, json, re

PM = os.path.dirname(os.path.abspath(__file__))
BOOK = os.path.join(PM, "basket_paper_book.json")

THEMES = [
    ("corp",     r"acquisition|merger|warner|\bipo\b|\bceo\b|bankrupt"),
    ("election", r"governor|senate|election|primary|midterm|president|prime-minister|\bpm\b|balance-of-power"),
    ("crypto",   r"\bbtc\b|bitcoin|\beth\b|ethereum|crypto|solana"),
    ("sports",   r"\blck\b|world-cup|\bnba\b|\bnfl\b|league|season-winner|\bcup\b"),
    ("geopol",   r"hormuz|israel|ukraine|russia|\bwar\b|ceasefire|peace"),
]
def theme_of(slug):
    for name, pat in THEMES:
        if re.search(pat, slug, re.I):
            return name
    return "other"


def load_locks():
    b = json.load(open(BOOK))
    out = []
    for slug, v in b.get("open", {}).items():
        edge = v.get("edge"); notion = v.get("notional", 50.0)
        if not edge:
            continue
        cost = (v.get("lock_cost", 1.0)) * notion          # capital to deploy
        epnl = v.get("expected_pnl", edge * notion)         # locked $ edge
        out.append({"slug": slug, "title": (v.get("title") or slug)[:40],
                    "edge": edge, "capital": round(cost, 2), "pnl": round(epnl, 4),
                    "theme": theme_of(slug)})
    return out


def solve_cuopt(locks, budget, per_theme):
    from cuopt.linear_programming.problem import Problem, INTEGER, MAXIMIZE
    from cuopt.linear_programming.solver_settings import SolverSettings
    p = Problem("BasketAlloc")
    y = [p.addVariable(lb=0, ub=1, vtype=INTEGER, name=f"f{i}") for i in range(len(locks))]
    p.addConstraint(sum(locks[i]["capital"] * y[i] for i in range(len(locks))) <= budget, name="budget")
    themes = {}
    for i, L in enumerate(locks):
        themes.setdefault(L["theme"], []).append(y[i])
    for t, ys in themes.items():
        p.addConstraint(sum(ys) <= per_theme, name=f"theme_{t}")
    p.setObjective(sum(locks[i]["pnl"] * y[i] for i in range(len(locks))), sense=MAXIMIZE)
    s = SolverSettings(); s.set_parameter("time_limit", 30); s.set_parameter("mip_relative_gap", 0.01)
    p.solve(s)
    if p.Status.name not in ("Optimal", "FeasibleFound"):
        raise RuntimeError(f"cuOpt status {p.Status.name}")
    return [i for i in range(len(locks)) if y[i].getValue() > 0.5], p.ObjValue, "cuOpt (GPU)"


def solve_scipy(locks, budget, per_theme):
    import numpy as np
    from scipy.optimize import milp, LinearConstraint, Bounds
    n = len(locks)
    c = -np.array([L["pnl"] for L in locks])          # minimize -pnl = maximize pnl
    A = [[L["capital"] for L in locks]]; ub = [budget]
    themes = {}
    for i, L in enumerate(locks):
        themes.setdefault(L["theme"], []).append(i)
    for t, idxs in themes.items():
        row = [1.0 if i in idxs else 0.0 for i in range(n)]
        A.append(row); ub.append(per_theme)
    cons = LinearConstraint(np.array(A), -np.inf, np.array(ub))
    res = milp(c=c, constraints=cons, integrality=np.ones(n),
               bounds=Bounds(0, 1))
    if not res.success:
        raise RuntimeError(f"scipy milp: {res.message}")
    sel = [i for i in range(n) if res.x[i] > 0.5]
    return sel, -res.fun, "scipy.optimize.milp (CPU fallback)"


def main():
    budget = float(sys.argv[sys.argv.index("--budget")+1]) if "--budget" in sys.argv else 100.0
    per_theme = int(sys.argv[sys.argv.index("--per-theme")+1]) if "--per-theme" in sys.argv else 3
    locks = load_locks()
    print(f"\n{'='*70}\n  BASKET-ARB ALLOCATION (cuOpt skill · MILP)\n{'='*70}")
    print(f"  {len(locks)} open locks · budget ${budget:.0f} · max {per_theme}/theme (anti-concentration)\n")

    try:
        sel, obj, solver = solve_cuopt(locks, budget, per_theme)
    except Exception as e:
        if "cuopt" not in str(e).lower() and "No module" not in str(e):
            print(f"  (cuOpt errored: {str(e)[:60]} — falling back)")
        sel, obj, solver = solve_scipy(locks, budget, per_theme)

    chosen = sorted((locks[i] for i in sel), key=lambda L: -L["pnl"])
    cap = sum(L["capital"] for L in chosen)
    from collections import Counter
    tc = Counter(L["theme"] for L in chosen)
    print(f"  Solver: {solver}")
    print(f"  → FUND {len(chosen)} locks · capital ${cap:.2f}/{budget:.0f} · locked edge ${obj:.2f}\n")
    print(f"  {'lock':<42}{'theme':<10}{'edge':>6}{'cap$':>8}{'pnl$':>7}")
    for L in chosen:
        print(f"  {L['title']:<42}{L['theme']:<10}{L['edge']*100:>5.1f}%{L['capital']:>8.2f}{L['pnl']:>7.2f}")
    print(f"\n  theme spread: {dict(tc)}  (≤{per_theme} each = no single-theme lever)")
    print(f"  ⚠️ Allocation math only — edges are real but capacity-microscopic; paper. "
          f"Solve on GPU cuOpt unchanged when available (swap fallback→solve_cuopt).\n")


if __name__ == "__main__":
    main()
