#!/usr/bin/env python3
"""arb_track.py — DETERMINISTIC every-cycle read of the structural-arb multi-leg track.

The cheap, no-LLM twin of the pm-arb-track agent: every watchdog cycle it reports the
+EV family (basket + data + mono → multiarb) and fires a LOUD deduped alert ONLY on an
actionable transition. The LLM agent (pm-arb-track) is reserved for when you ask for a
reasoned read. No CLOB calls (reads cached basket_locks.jsonl) so it's watchdog-cheap.

Actionable alerts (the only things that interrupt you):
  • GRADUATE — combined real n>=30 + CI>0 + beats losing control
  • MARKING BUG — a control track goes POSITIVE at n>=10 (controls MUST lose)
  • REALIZATION DROP — arb_memory realized/expected < 0.8 (locks underpaying = gap-through)
  • MILESTONE — combined real n first crosses 30 (gate becomes checkable)
Everything else (live edges, dormant data/mono, accumulating) → digest only, quiet.

Run:  python3 arb_track.py            # print + write Vault digest
      python3 arb_track.py once       # + deduped alert on fresh actionable (watchdog mode)
"""
import os, sys, json, time, collections

BASE = os.path.dirname(os.path.abspath(__file__))
LOCKS = os.path.join(BASE, "basket_locks.jsonl")
DATAARB = os.path.join(BASE, "dataarb_state.json")
MONOARB = os.path.join(BASE, "monoarb_state.json")
ARBMEM = os.path.join(BASE, "arb_memory.json")
SEEN = os.path.join(BASE, ".arb_track_seen.json")
VAULT = os.path.expanduser("~/Documents/PolymarketVault/Reports/arb_track.md")


def _load(p, d=None):
    try:
        return json.load(open(p))
    except Exception:
        return d if d is not None else {}


def _live_locks(n=8):
    """Latest edge per slug from the cached basket_locks tape (cheap — no CLOB call)."""
    try:
        lines = [json.loads(x) for x in open(LOCKS) if x.strip()][-400:]
    except Exception:
        return []
    latest = {}
    for r in lines:
        s = r.get("slug")
        if s:
            latest[s] = r           # last write wins = most recent
    out = []
    for r in latest.values():
        # ws_new/ws_jump events omit 'kind' — infer it: Σ<1 = LONG (buy set cheap), Σ>1 = SHORT.
        if not r.get("kind"):
            sm = r.get("sum_ask")
            r = {**r, "kind": ("SHORT" if (sm is not None and sm > 1) else "LONG")}
        out.append(r)
    return sorted(out, key=lambda r: -r.get("edge", 0))[:n]


def read():
    import multiarb
    s = multiarb.build()
    r, c = s["combined_real"], s["combined_control"]
    da, mo = _load(DATAARB), _load(MONOARB)
    mem = _load(ARBMEM)
    realization = None
    for k in ("realization", "realization_all", "ratio"):
        v = mem.get(k) if isinstance(mem, dict) else None
        if isinstance(v, (int, float)):
            realization = v; break
    return {
        "gate": s["gate"], "real": r, "control": c, "sub": s["sub_legs"],
        "locks": _live_locks(),
        "data_open": len(da.get("open", [])), "data_resolved": len(da.get("resolved", [])),
        "mono_open": len(mo.get("open", [])), "mono_resolved": len(mo.get("resolved", [])),
        "realization": realization,
    }


def actionable(d):
    """Return [(key, line)] of alert-worthy transitions. Empty = quiet steady state."""
    out = []
    g, r, c = d["gate"], d["real"], d["control"]
    if g.get("GRADUATE"):
        out.append(("graduate", f"🎓 ARB TRACK GRADUATED — combined n={r['n']} mean ${r['mean']:+.3f} "
                                f"CI[{r['ci'][0]:+.3f},{r['ci'][1]:+.3f}] beats losing control → pm-dsr-gatekeeper"))
    # control MUST lose; positive at n>=10 = marking bug (highest priority)
    if c["n"] >= 10 and c["mean"] is not None and c["mean"] > 0:
        out.append(("ctrl_bug", f"⚠️ MARKING BUG: arb control POSITIVE ${c['mean']:+.3f}/exit at n={c['n']} "
                                f"(controls must lose) — investigate marking"))
    if d["realization"] is not None and r["n"] >= 3 and d["realization"] < 0.8:
        out.append(("realization", f"⚠️ realization {d['realization']:.2f} (<0.8) — locks underpaying vs entry; "
                                   f"gap-through erosion? (the nearres failure mode) — pm-backtest-skeptic"))
    if r["n"] >= 30:
        out.append(("n30", f"📊 arb track combined n={r['n']} (≥30) — gate now checkable → pm-dsr-gatekeeper"))
    return out


def digest(d):
    L = ["=" * 60, "  arb_track — structural-arb multi-leg (deterministic read)", "=" * 60]
    if d["locks"]:
        L.append("  live basket locks (cached, top):")
        for lk in d["locks"][:6]:
            L.append(f"    {lk.get('edge',0):+.1%} {lk.get('kind','?'):5} Σ{lk.get('sum_ask','?')}  {str(lk.get('slug',''))[:40]}")
    L.append(f"  data:  open {d['data_open']} / resolved {d['data_resolved']}  | "
             f"mono: open {d['mono_open']} / resolved {d['mono_resolved']}  (0 real = dormant, expected)")
    r, c = d["real"], d["control"]
    ci = f"CI[{r['ci'][0]:+.3f},{r['ci'][1]:+.3f}]" if r["ci"][0] is not None else "CI(n<2)"
    L.append(f"  combined REAL n={r['n']} mean ${r['mean'] if r['mean'] is not None else 0:+.4f} {ci}  "
             f"| control n={c['n']}")
    if d["realization"] is not None:
        L.append(f"  realization (realized/expected): {d['realization']:.2f}")
    g = d["gate"]
    verdict = "✅ GRADUATE" if g.get("GRADUATE") else ("accumulating" if r["n"] == 0 else "not yet")
    need = max(0, 30 - r["n"])
    L.append(f"  GATE: {verdict}  (need {need} more resolved exits + CI>0 + beats control)")
    return "\n".join(L)


def main():
    d = read()
    body = digest(d)
    print(body)
    try:
        os.makedirs(os.path.dirname(VAULT), exist_ok=True)
        open(VAULT, "w").write("# arb_track — structural-arb multi-leg\n\n```\n" + body + "\n```\n")
    except Exception:
        pass

    if len(sys.argv) > 1 and sys.argv[1] == "once":
        acts = actionable(d)
        if not acts:
            print("  [quiet — no actionable transition]")
            return
        seen = _load(SEEN, {})
        now = time.time()
        seen = {k: t for k, t in seen.items() if now - t < 86400}   # 24h dedup
        fresh = [(k, line) for k, line in acts if k not in seen]
        if not fresh:
            print("  [actionable present but already alerted]")
            return
        msg = "🔗 ARB TRACK:\n" + "\n".join(line for _, line in fresh)
        try:
            import wa_alert
            wa_alert.notify(msg)
        except Exception as e:
            print(f"  [alert failed: {e}]", file=sys.stderr)
        for k, _ in fresh:
            seen[k] = now
        try:
            json.dump(seen, open(SEEN, "w"))
        except Exception:
            pass
        print(f"  [alerted {len(fresh)} fresh actionable]")


if __name__ == "__main__":
    main()
