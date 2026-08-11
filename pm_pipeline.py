#!/usr/bin/env python3
"""
pm_pipeline.py — the INTERCONNECTION layer. The pm-agents stop being 16 separate tools and
become ONE system: each agent's output feeds the next, with gates that short-circuit a dead
candidate before the expensive stages. Judgment stages use the free LLM (llm_client); math
stages are deterministic Python (never the LLM — it gets arithmetic wrong).

THE CHAIN (per candidate market):
  discover ─▶ [resolution-check] ─gate─▶ [data dossier] ─▶ [news-arb] ─▶ [refute 3-lens] ─gate─▶
             [capacity] ─▶ SURVIVOR  ─┐
                                       └─▶ portfolio: [correlation] + [dsr gate]  (run once over all survivors)

Gates (short-circuit, cheapest-first so we don't spend LLM calls on doomed candidates):
  • resolution_confirmed == False           → DROP (the #1 trap; cheapest check first)
  • refuters >= 2 at >70% confidence         → DROP (adversarial panel)
Survivors carry a full connected dossier (every stage's verdict) into the book.

Run:
  python3 pm_pipeline.py --cid 0x... --side NO --true 0.15 --reason "..."   # one candidate, full chain
  python3 pm_pipeline.py --discover [--max 5]                               # data-gate → chain each
  python3 pm_pipeline.py --portfolio                                        # correlation + dsr over the book
"""
import os, sys, json, argparse, datetime, urllib.request
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import edge_common, analyst_data_gate as adg
import check_resolution, news_arb, refute_edge, dsr_gate, analyst_correlation, vet_whale, llm_client

# CAPABILITY LANES — a candidate is ROUTED to the specialists whose capability fits its TYPE,
# not forced through one chain. Each agent contributes a distinct capability:
#   market(event)     → resolution-check · news-arb · refute(3-lens) · capacity
#   market(data-series)→ resolution-check · data-resolver(klines/PortWatch) · refute · capacity  (news skipped — data is decisive)
#   wallet            → whale-vetter (copyability; resolution/news/refute don't apply)
#   leg (paper)       → dsr-gatekeeper · correlation  (graduation, not edge-discovery)
#   portfolio         → correlation-auditor + dsr over the whole book

VAULT = os.path.expanduser("~/Documents/PolymarketVault/Reports/pm_pipeline_latest.md")
LOG = os.path.join(HERE, "pm_pipeline_log.jsonl")


def _ts():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _capacity(cid, side, true_prob):
    """Deterministic CLOB ladder walk → $ that holds the edge (the capacity-estimator agent)."""
    try:
        raw = edge_common._get(edge_common.GAMMA, {"condition_ids": cid})[0]
        toks = json.loads(raw["clobTokenIds"]); outs = json.loads(raw["outcomes"])
        idx = outs.index("No") if side == "NO" else outs.index("Yes")
        b = json.loads(urllib.request.urlopen(urllib.request.Request(
            f"https://clob.polymarket.com/book?token_id={toks[idx]}",
            headers={"User-Agent": "Mozilla/5.0"}), timeout=15).read())
        asks = sorted((float(a["price"]), float(a["size"])) for a in b.get("asks", []))
        if not asks:
            return {"depth_usd": 0, "note": "no asks"}
        depth = sum(p * s for p, s in asks)
        true_win = (1 - true_prob) if side == "NO" else true_prob
        top_edge = true_win - asks[0][0]
        # $ at which avg-fill erodes half the per-share edge
        spent = shares = 0.0; half_cap = None
        for p, s in asks:
            spent += p * s; shares += s
            if true_win - (spent / shares) < top_edge / 2:
                half_cap = round(spent); break
        return {"depth_usd": round(depth), "top_edge_per_share": round(top_edge, 3),
                "half_edge_capacity_usd": half_cap or f">{round(depth)}"}
    except Exception as e:
        return {"error": str(e)[:80]}


def run_candidate(cid, side=None, true_prob=None, reason=None, mock=False):
    trail = {"ts": _ts(), "cid": cid}
    # ── Stage 1: resolution-check (cheapest gate, the #1 trap) ──────────────
    res = check_resolution.check(cid=cid, mock=mock)
    rv = res.get("verdict", {})
    trail["q"] = res.get("q"); trail["market_yes"] = res.get("market_yes")
    trail["resolution"] = {"confirmed": rv.get("resolution_confirmed"),
                           "yes": rv.get("yes_triggers"), "traps": rv.get("traps")}
    if not rv.get("resolution_confirmed"):
        trail["status"] = "DROPPED @resolution — criteria unconfirmed/ambiguous"; return trail
    # ── Stage 2: data dossier (deterministic) — picks the LANE ──────────────
    src, params = adg.classify(trail["q"] or "")
    trail["data"] = adg.fetch_dossier(src, params, cid) if src else {"source": None}
    lane = "data-series" if src in ("crypto-threshold", "portwatch") else "event"
    trail["lane"] = lane
    # ── Stage 3: news-arb — EVENT lane only (for a data-series market the fetched
    #    series is decisive; news is noise, so we route past it) ───────────────
    if lane == "event":
        nv = news_arb.run(cid, mock=mock).get("verdict", {})
        trail["news"] = {"already_priced": nv.get("already_priced"), "edge": nv.get("edge"), "why": (nv.get("why") or "")[:160]}
    # ── Stage 4: refute 3-lens (the adversarial gate) ───────────────────────
    if side and true_prob is not None:
        ref = refute_edge.refute(cid, side, true_prob, reason or "", mock=mock)
        trail["refute"] = {"survived": ref["survived"], "hard_refuters": ref["hard_refuters"],
                           "lenses": [{"lens": v["lens"], "refuted": v["refuted"], "conf": v["confidence"]} for v in ref["verdicts"]]}
        if not ref["survived"]:
            trail["status"] = f"DROPPED @refute — {ref['hard_refuters']}/3 refuted >70%"; return trail
        # ── Stage 5: capacity (deterministic, only for survivors) ───────────
        trail["capacity"] = _capacity(cid, side, true_prob)
        trail["status"] = "SURVIVOR — candidate for book"
    else:
        trail["status"] = "VETTED (no side/true given — resolution+news only; pass --side/--true to refute)"
    return trail


def portfolio():
    """Portfolio-level connections: correlation audit + DSR gate over the existing book/legs."""
    out = {"ts": _ts()}
    try:
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            analyst_correlation.main()
        out["correlation"] = buf.getvalue().strip().splitlines()[-4:]
    except Exception as e:
        out["correlation"] = [f"err: {e}"]
    rows, meta = dsr_gate.gate()
    grads = [r for r in rows if "GRADUATE" in r["verdict"]]
    out["dsr"] = {"n_legs": meta["N_legs"], "graduates": [r["leg"] for r in grads],
                  "top": [{"leg": r["leg"], "n": r["n"], "mean": r["mean"], "verdict": r["verdict"]} for r in rows[:5]]}
    return out


def run_wallet(addr, mock=False):
    """WALLET lane — only the whale-vetter capability applies (copyability, not edge-discovery)."""
    v = vet_whale.vet(addr, mock=mock)
    s, vd = v["stats"], v["verdict"]
    return {"ts": _ts(), "kind": "wallet", "addr": addr, "lane": "wallet",
            "stats": {k: s.get(k) for k in ("realized_pnl", "win_rate", "last_trade_days", "coinflip_share", "favorite_padding_share")},
            "whale": {"archetype": vd.get("archetype"), "copyable": vd.get("copyable"), "confidence": vd.get("confidence"), "why": (vd.get("why") or "")[:160]},
            "status": f"{vd.get('archetype','?')} — copyable={vd.get('copyable')}"}


def run_leg(leg):
    """LEG lane — graduation, not discovery: the dsr-gatekeeper capability + correlation context.
    Deterministic (no LLM): a money decision must be exact."""
    rows, meta = dsr_gate.gate(target=leg)
    r = rows[0] if rows else None
    return {"ts": _ts(), "kind": "leg", "leg": leg, "lane": "leg",
            "dsr": r, "dsr_universe_N": meta["N_legs"], "control_to_beat": meta["worst_control_mean"],
            "status": (r["verdict"] if r else "no closed exits for this leg")}


def _persist(trails, port=None):
    rows = trails if isinstance(trails, list) else [trails]
    try:
        with open(LOG, "a") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
    except Exception as e:
        print(f"[log FAIL] {e}", file=sys.stderr)
    try:
        L = [f"# PM Pipeline — connected agent run", "", f"_{_ts()}_", ""]
        for r in rows:
            L += [f"## {(r.get('q') or r['cid'])[:60]}", f"**{r.get('status','?')}**", ""]
            if r.get("resolution"): L.append(f"- resolution: confirmed={r['resolution']['confirmed']}")
            if r.get("data") and r["data"].get("source"): L.append(f"- data: {json.dumps(r['data'])[:140]}")
            if r.get("news"): L.append(f"- news: priced={r['news']['already_priced']} edge={r['news']['edge']}")
            if r.get("refute"): L.append(f"- refute: survived={r['refute']['survived']} ({r['refute']['hard_refuters']}/3)")
            if r.get("capacity"): L.append(f"- capacity: {json.dumps(r['capacity'])}")
            L.append("")
        if port:
            L += ["## Portfolio", f"- DSR: {len(port['dsr']['graduates'])} graduate of {port['dsr']['n_legs']} legs"]
            L += [f"  - {x}" for x in port.get("correlation", [])]
        os.makedirs(os.path.dirname(VAULT), exist_ok=True)
        open(VAULT, "w").write("\n".join(L) + "\n")
    except Exception as e:
        print(f"[vault FAIL] {e}", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cid"); ap.add_argument("--side", choices=["YES", "NO"])
    ap.add_argument("--true", type=float, dest="true_prob"); ap.add_argument("--reason", default="")
    ap.add_argument("--discover", action="store_true"); ap.add_argument("--max", type=int, default=4)
    ap.add_argument("--portfolio", action="store_true"); ap.add_argument("--mock", action="store_true")
    ap.add_argument("--wallet", help="route a wallet 0x… to the whale-vetter lane")
    ap.add_argument("--leg", help="route a paper leg name to the dsr-gatekeeper lane (deterministic)")
    a = ap.parse_args()
    # the LEG lane is pure-deterministic → needs no LLM; everything else does (unless --mock)
    if not a.leg and not a.mock and not llm_client.configured():
        print("⚠️ No LLM endpoint (set LLM_* in .env) — or use --mock."); sys.exit(2)

    if a.leg:
        t = run_leg(a.leg); _persist(t)
        d = t["dsr"]
        print(f"\n══ LEG LANE: {a.leg} (capability: dsr-gatekeeper, deterministic) ══")
        if d: print(f"  n={d['n']} mean=${d['mean']} CI={d['ci']} DSR z={d['dsr_z']} → {d['verdict']}")
        print(f"  (must beat control mean {t['control_to_beat']}, DSR universe N={t['dsr_universe_N']})")
        print(f"\n  STATUS: {t['status']}"); return

    if a.wallet:
        t = run_wallet(a.wallet, mock=a.mock); _persist(t)
        print(f"\n══ WALLET LANE: {a.wallet[:12]}… (capability: whale-vetter) ══")
        print(f"  stats: {json.dumps(t['stats'])}")
        print(f"  → {t['whale']['archetype']} | copyable={t['whale']['copyable']} @{t['whale']['confidence']}")
        print(f"  why: {t['whale']['why']}"); return

    if a.portfolio:
        p = portfolio(); _persist([], p)
        print(f"\nPORTFOLIO: DSR {len(p['dsr']['graduates'])}/{p['dsr']['n_legs']} graduate | "
              f"correlation:\n  " + "\n  ".join(p["correlation"][-3:])); return

    if a.discover:
        print("discover → data-gate scan (this picks fetchable-resolution markets)…")
        rows = adg.scan(min_vol=80000)
        cands = [r for r in rows if r["data_ok"]][:a.max]
        trails = []
        for c in cands:
            print(f"  ▶ {c['q'][:50]} …")
            trails.append(run_candidate(c["cid"], mock=a.mock))  # resolution+news+data (no side → no refute)
        _persist(trails)
        print(f"\n{len(trails)} candidates chained. Survivors/vetted written to {VAULT}")
        for t in trails: print(f"  [{t['status'][:42]}] {(t.get('q') or '')[:44]}")
        return

    if not a.cid:
        print("need --cid (+optional --side/--true to refute), or --discover, or --portfolio"); sys.exit(2)
    t = run_candidate(a.cid, a.side, a.true_prob, a.reason, mock=a.mock)
    _persist(t)
    print(f"\n══ CONNECTED CHAIN: {(t.get('q') or a.cid)[:55]} ══")
    for stage in ("resolution", "data", "news", "refute", "capacity"):
        if stage in t: print(f"  {stage:11} → {json.dumps(t[stage])[:96]}")
    print(f"\n  STATUS: {t['status']}")


if __name__ == "__main__":
    main()
