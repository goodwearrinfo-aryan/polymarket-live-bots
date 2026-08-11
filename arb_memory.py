#!/usr/bin/env python3
"""
arb_memory.py — the ARB legs' self-learning loop (procedural-memory analog for the
profitable structural-arb track: basket_paper + dataarb, the only +EV sources).

procedural_memory.py learns the ANALYST track's CALIBRATION. This learns the ARB track's
REALIZATION + REGIME — two honest things from resolved arb exits:

  1. REALIZATION ratio = realized / expected. Does the arb actually pay what entry promised?
     This is the gap-through / execution-erosion guard that nearres failed (Lesson 13):
     structural locks SHOULDN'T gap (ratio ≈ 1.0); if it drifts <1 as n grows, execution is
     eroding the edge. Surfaced from n=1 as a SANITY signal (not an edge claim).
  2. REGIME: per-feature realized edge (which arb conditions convert) — basket: n_outcomes /
     edge-band / lock_held; dataarb: source / kind. DORMANT until n>=MIN_N: overfitting a
     small sample is this project's #1 failure, so below the bar it generalizes NOTHING.

Plus the controls-must-lose check (dataarb control + basketrand) — a positive control = a
marking bug, halt before trusting anything.

Reads:  dataarb_state.json (resolved[]) · basket_paper_book.json (closed[]) · basketrand_book.json (control)
Writes: Vault/Reports/arb_memory.md + arb_memory.json (learned lessons/filter; dormant until data)
Run:    python3 arb_memory.py            # print
        python3 arb_memory.py once       # + write vault/json (watchdog mode)
"""
import os, sys, json, time

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
DATAARB = os.path.join(BASE, "dataarb_state.json")
BASKET = os.path.join(BASE, "basket_paper_book.json")
BASKETRAND = os.path.join(BASE, "basketrand_book.json")
MONOARB = os.path.join(BASE, "monoarb_state.json")   # monotonicity/consistency-violation locks
VAULT = os.path.expanduser("~/Documents/PolymarketVault/Reports/arb_memory.md")
OUT = os.path.join(BASE, "arb_memory.json")

MIN_N = 30   # edge bar — per-feature regime learning is DORMANT below this (no overfit)
HINT_N = 5   # below this: realization sanity only, zero generalization


def _load(p, d=None):
    try:
        return json.load(open(p))
    except Exception:
        return d


def _boot_ci(xs, iters=4000):
    """Deterministic (seeded) bootstrap 95% CI of the mean. None if n<2."""
    n = len(xs)
    if n < 2:
        return (None, None)
    seed, means = 12345, []
    for _ in range(iters):
        s = 0.0
        for _ in range(n):
            seed = (1103515245 * seed + 12345) & 0x7FFFFFFF
            s += xs[seed % n]
        means.append(s / n)
    means.sort()
    return (round(means[int(0.025 * iters)], 4), round(means[int(0.975 * iters)], 4))


def _src(q, reason):
    """Best-effort data source for a dataarb trade (reuses the gate's classifier)."""
    try:
        import analyst_data_gate as adg
        s, _ = adg.classify(q or "")
        if s:
            return s
    except Exception:
        pass
    r = (reason or "").lower()
    for k in ("portwatch", "hormuz", "crypto", "fed", "btc", "eth"):
        if k in r:
            return k
    return "data"


# ─── collect resolved arb trades with realized + expected + features ───────────

def collect():
    trades = []   # {leg, realized, expected, kind, feat:{...}}
    ctrl = {"dataarb": [], "basketrand": [], "monoarb": []}

    d = _load(DATAARB, {}) or {}
    for p in d.get("resolved", []):
        if p.get("pnl") is None:
            continue
        if p.get("kind") == "control":
            ctrl["dataarb"].append(p["pnl"])
            continue
        entry = float(p.get("entry", 0) or 0)
        size = float(p.get("size", 0) or 0)
        side = (p.get("side") or "YES").upper()
        # a data-CONFIRMED real entry expects the full win (data says it resolves our way)
        expected = (1 - entry) * size if side == "YES" else entry * size
        trades.append({"leg": "dataarb", "realized": p["pnl"], "expected": expected,
                       "kind": "real",
                       "feat": {"source": _src(p.get("q"), p.get("reason")), "side": side}})

    b = _load(BASKET, {}) or {}
    for rec in (b.get("closed", []) if isinstance(b, dict) else []):
        if rec.get("status") == "dropped_unresolved" or rec.get("realized_pnl") is None:
            continue
        edge = float(rec.get("edge", 0) or 0)
        spread = round(float(rec.get("sum_ask", 0) or 0) - float(rec.get("sum_bid", 0) or 0), 4)
        trades.append({"leg": "basket", "realized": rec["realized_pnl"],
                       "expected": rec.get("expected_pnl"), "kind": "real",
                       "feat": {"n_outcomes": rec.get("n_outcomes"),
                                "edge_band": ("hi" if edge >= 0.03 else "lo"),
                                "spread_band": ("wide" if spread >= 0.02 else "tight"),
                                "lock_held": bool(rec.get("lock_held"))}})

    # monoarb — monotonicity/consistency-violation locks (the 3rd +EV arb, in multiarb too).
    # A real lock pays $1 at resolution for cost<$1, so expected = (1-cost)*SIZE*(1-FEE).
    mfee, msize = 0.02, 1.0
    try:
        import monoarb as _mono
        mfee, msize = _mono.FEE, _mono.SIZE
    except Exception:
        pass
    m = _load(MONOARB, {}) or {}
    for p in m.get("resolved", []):
        if p.get("pnl") is None:
            continue
        if p.get("kind") == "control":
            ctrl["monoarb"].append(p["pnl"])
            continue
        cost = float(p.get("cost", 1.0) or 1.0)
        expected = max(0.0, 1.0 - cost) * msize * (1 - mfee)
        trades.append({"leg": "monoarb", "realized": p["pnl"], "expected": expected,
                       "kind": "real",
                       "feat": {"asset": (p.get("key", "") or "").split("-")[0] or "?",
                                "gap_band": ("hi" if abs(float(p.get("gap", 0) or 0)) >= 0.03 else "lo")}})

    r = _load(BASKETRAND, {}) or {}
    for rec in (r.get("closed", []) if isinstance(r, dict) else []):
        if rec.get("realized_pnl") is not None and rec.get("status") != "dropped_unresolved":
            ctrl["basketrand"].append(rec["realized_pnl"])
    return trades, ctrl


# ─── the three learnings ───────────────────────────────────────────────────────

def realization(trades):
    """realized/expected per leg + overall — the gap-through / execution-erosion guard."""
    out = {}
    for leg in ("basket", "dataarb", "monoarb", "ALL"):
        ts = [t for t in trades if leg == "ALL" or t["leg"] == leg]
        ts = [t for t in ts if t.get("expected")]
        if not ts:
            out[leg] = {"n": 0}
            continue
        exp = sum(t["expected"] for t in ts)
        rea = sum(t["realized"] for t in ts)
        out[leg] = {"n": len(ts), "expected": round(exp, 3), "realized": round(rea, 3),
                    "ratio": round(rea / exp, 3) if exp else None}
    return out


def regime(trades):
    """Per-feature realized edge — DORMANT until MIN_N (no small-sample generalization)."""
    real = [t for t in trades if t["kind"] == "real"]
    if len(real) < MIN_N:
        return {"dormant": True, "n": len(real), "need": MIN_N, "buckets": {}}
    buckets = {}
    for t in real:
        for fk, fv in t["feat"].items():
            key = f"{t['leg']}.{fk}={fv}"
            buckets.setdefault(key, []).append(t["realized"])
    learned = {}
    for key, xs in buckets.items():
        if len(xs) >= 8:   # per-bucket floor
            lo, hi = _boot_ci(xs)
            learned[key] = {"n": len(xs), "mean": round(sum(xs) / len(xs), 4),
                            "ci": [lo, hi], "positive": (lo is not None and lo > 0)}
    return {"dormant": False, "n": len(real), "buckets": learned}


def controls(ctrl):
    out = {}
    for name, xs in ctrl.items():
        if not xs:
            out[name] = {"n": 0}
        else:
            m = sum(xs) / len(xs)
            out[name] = {"n": len(xs), "mean": round(m, 4), "bug": m > 0}
    return out


def lessons(trades, rz, rg, ctl):
    """Honest learnings (the procedural_memory.reflection analog). Empty when nothing earned."""
    L = []
    nreal = sum(1 for t in trades if t["kind"] == "real")
    if nreal == 0:
        return ["No resolved arb exits yet (n=0). DORMANT — the loop learns nothing until the "
                "arbs resolve. Correct honest state (basket+dataarb hold to resolution)."]
    # 1. realization (the gap-through guard) — meaningful early
    a = rz.get("ALL", {})
    if a.get("n"):
        ratio = a.get("ratio")
        if ratio is not None:
            if nreal < HINT_N:
                L.append(f"Realization {ratio:.2f} over {a['n']} exit(s) (realized ${a['realized']}/"
                         f"expected ${a['expected']}). n<{HINT_N}: SANITY only, no generalization. "
                         f"Watch the ratio as n grows — structural arb should hold ≈1.0; a drift "
                         f"below 1 = execution eroding the edge (the nearres failure mode).")
            elif ratio >= 0.9:
                L.append(f"Arb REALIZES {ratio:.2f} of expected over {a['n']} exits — the lock pays "
                         f"what entry promised (no gap-through). This is the structural edge holding.")
            else:
                L.append(f"⚠️ Arb realizes only {ratio:.2f} of expected over {a['n']} exits — execution "
                         f"is eroding {(1-ratio)*100:.0f}% of the edge. Investigate fills/slippage.")
    # 2. regime (dormant until MIN_N)
    if rg.get("dormant"):
        L.append(f"Regime learning DORMANT ({rg['n']}/{rg['need']} real exits). No per-feature edge "
                 f"claim until n≥{rg['need']} (overfitting small-n is this project's #1 failure).")
    else:
        pos = [k for k, v in rg["buckets"].items() if v["positive"]]
        if pos:
            L.append("Regime: realized-positive arb conditions (CI>0) → " + ", ".join(pos[:6]))
        else:
            L.append(f"Regime: {rg['n']} real exits, no feature bucket clears CI>0 yet — keep accumulating.")
    # 3. controls must lose
    for name, c in ctl.items():
        if c.get("bug"):
            L.append(f"🛑 CONTROL BUG: {name} control mean {c['mean']:+.4f} POSITIVE over {c['n']} — "
                     f"a control must LOSE; the P&L pipeline is suspect. Halt before trusting the arb.")
    return L


def build():
    trades, ctrl = collect()
    rz, rg, ctl = realization(trades), regime(trades), controls(ctrl)
    return {"ts": time.strftime("%Y-%m-%d %H:%M"), "n_real": sum(1 for t in trades if t["kind"] == "real"),
            "realization": rz, "regime": rg, "controls": ctl,
            "lessons": lessons(trades, rz, rg, ctl)}


def write_outputs(s):
    try:
        json.dump(s, open(OUT, "w"), indent=2)
    except Exception as e:
        print(f"[json FAIL] {e}", file=sys.stderr)
    try:
        L = ["---", "type: logger", f"updated: {s['ts']}", "tags: [arb, learning]", "---", "",
             "# Arb Memory — what the profitable legs have taught (self-learning)", "",
             "Learns REALIZATION (does the arb pay what entry promised?) + REGIME (which "
             "conditions convert) from resolved basket_paper + dataarb exits. Honest-by-"
             "construction: regime learning is DORMANT until n≥30. PAPER, read-only.", "",
             f"**{s['n_real']} real arb exit(s) resolved.**", "", "## Lessons", ""]
        for ln in s["lessons"]:
            L.append(f"- {ln}")
        L += ["", "## Realization (realized / expected)", "",
              "| leg | n | expected | realized | ratio |", "|-----|--:|--:|--:|--:|"]
        for leg in ("basket", "dataarb", "monoarb", "ALL"):
            r = s["realization"].get(leg, {})
            if r.get("n"):
                L.append(f"| {leg} | {r['n']} | ${r['expected']} | ${r['realized']} | {r['ratio']} |")
        if not s["regime"].get("dormant"):
            L += ["", "## Regime — realized edge by feature (n≥30)", "",
                  "| condition | n | mean$ | CI | +EV |", "|---|--:|--:|--|:--:|"]
            for k, v in s["regime"]["buckets"].items():
                L.append(f"| {k} | {v['n']} | {v['mean']:+.4f} | [{v['ci'][0]},{v['ci'][1]}] | "
                         f"{'✅' if v['positive'] else '—'} |")
        os.makedirs(os.path.dirname(VAULT), exist_ok=True)
        open(VAULT, "w").write("\n".join(L) + "\n")
    except Exception as e:
        print(f"[vault FAIL] {e}", file=sys.stderr)


def main():
    s = build()
    print(f"[{s['ts']}] arb_memory: {s['n_real']} real arb exit(s)")
    for ln in s["lessons"]:
        print(f"  • {ln}")
    rz = s["realization"].get("ALL", {})
    if rz.get("n"):
        print(f"  realization (ALL): {rz['ratio']}  (realized ${rz['realized']} / expected ${rz['expected']})")
    if "once" in sys.argv:
        write_outputs(s)
        print(f"  → {OUT} + {VAULT}")


if __name__ == "__main__":
    main()
