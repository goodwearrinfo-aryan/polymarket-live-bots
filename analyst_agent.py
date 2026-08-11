#!/usr/bin/env python3
"""
analyst_agent.py — the 24/7 LIVE analyst agent over the research paper book.

Runs every cycle (launchd com.aryan.analyst-agent, 15min). It is NOT a trader —
PAPER ONLY, read-only on the market, the only thing it WRITES is the book's own
settled-position lock (via analyst_book.mark_book) + its snapshot/Obsidian/log.

Each cycle:
  1. mark_book()  → marks every position to live, BANKS + locks any newly-resolved
     ones (gamma drops resolved markets → CLOB fallback; the stale_nodata fix).
  2. diff vs last snapshot → detect EVENTS:
       • RESOLVED   a position settled this cycle (WIN/LOSS banked)
       • ADVERSE    an open position's unrealized P&L fell ≥ MOVE_THRESH since last cycle
       • EDGE-GONE  the live market has caught up to / passed our true_prob (thesis priced out)
  3. for each EVENT position, ONE free-LLM judgment (NVIDIA, zero Claude cost) —
     {assessment: intact|weakening|broken, action: hold|re-vet|note, why}. Steady
     state = no events = no LLM calls = quiet.
  4. ALERT (iMessage + Obsidian) on events only. No events → just refresh the
     Obsidian snapshot. Correlation concentration reported for context.
  5. save snapshot for next-cycle diffing + append jsonl.

Run:  python3 analyst_agent.py once     # one cycle (watchdog/launchd mode)
      python3 analyst_agent.py          # same, with verbose stdout
"""
import os, sys, json, subprocess
from datetime import datetime, timezone

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
import analyst_book
try:
    import analyst_correlation as corr
except Exception:
    corr = None
try:
    import llm_client
except Exception:
    llm_client = None
try:
    import candela_args            # read-only CANDELA bull/bear brief (commentary, not a leg)
except Exception:
    candela_args = None

VOL_REGIME_JSON = os.path.join(BASE, "vol_regime_latest.json")

SNAP = os.path.join(BASE, "analyst_agent_state.json")
LOG = os.path.join(BASE, "analyst_agent_log.jsonl")
VAULT = os.path.expanduser("~/Documents/PolymarketVault/Reports/analyst_agent.md")
IMESSAGE_TARGETS = ["krisharyan@icloud.com", "+918449447444"]

MOVE_THRESH = 0.08          # unrealized P&L drop that counts as an ADVERSE event
EDGE_GONE_SLACK = 0.02      # market within this of our true_prob = thesis priced out

# CANDELA briefs: a read-only structured bull/bear brief attached (vault only) to
# OPEN-position events where the thesis is in question. Commentary only — never touches
# the book. Capped per cycle; killable via CANDELA_BRIEFS=0. The brief's p(YES) is the
# free LLM's view (systematically miscalibrated — see tail_miscalibration); NOT a conviction.
CANDELA_BRIEFS = os.environ.get("CANDELA_BRIEFS", "1") == "1"
MAX_CANDELA_BRIEFS = 2
BRIEF_EVENT_KINDS = {"ADVERSE", "EDGE-GONE"}


def _ts():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_vol_regime():
    """Load the latest vol_regime snapshot written by vol_regime.py.

    Returns a dict with keys: btc_dvol, eth_dvol, iv_regime, iv_rv_spread_btc,
    btc_hv_pct, eth_hv_pct — or an empty dict if the file is absent/stale/broken.
    Fail-soft: vol regime is context-only, never a gate.
    """
    try:
        with open(VOL_REGIME_JSON) as f:
            v = json.load(f)
        return v
    except Exception:
        return {}


def send_imessage(text):
    """Dual-send iMessage (matches send_report_imessage targets). Fail-soft."""
    safe = text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    ok_any = False
    for tgt in IMESSAGE_TARGETS:
        script = ('tell application "Messages"\n'
                  '  set s to id of first service whose service type = iMessage\n'
                  f'  set b to participant "{tgt}" of service id s\n'
                  f'  send "{safe}" to b\n'
                  'end tell')
        try:
            r = subprocess.run(["osascript", "-e", script],
                               capture_output=True, text=True, timeout=20)
            ok_any = ok_any or r.returncode == 0
        except Exception:
            pass
    return ok_any


def _llm_assess(p, event_kind, detail, vol_ctx=None):
    """One free-LLM judgment on an event position. Returns dict or None (fail-soft).

    vol_ctx: optional dict from _read_vol_regime() — iv_regime / dvol / spread injected
    into the system prompt so the LLM can weight near-resolution uncertainty accordingly.
    """
    if not (llm_client and llm_client.configured()):
        return None
    vol_line = ""
    if vol_ctx and vol_ctx.get("iv_regime"):
        vol_line = (
            f" Current crypto vol environment: iv_regime={vol_ctx['iv_regime']}, "
            f"BTC DVOL={vol_ctx.get('btc_dvol','?')}, ETH DVOL={vol_ctx.get('eth_dvol','?')}, "
            f"IV-RV spread BTC={vol_ctx.get('iv_rv_spread_btc','?')}. "
            "Elevated IV suggests higher market uncertainty; suppressed IV suggests crowded complacency.")
    system = (
        "You are a live prediction-market analyst monitoring an OPEN paper position. "
        "The position survived a 3-lens refutation panel at entry. An event just fired. "
        "Judge ONLY whether the original thesis still holds given the event — do not re-derive "
        "the whole edge. Be terse and concrete. Default to 'weakening' if genuinely unsure."
        + vol_line)
    user = (
        f"POSITION: {p['q']}\n"
        f"OUR SIDE: {p['side']} | entry cost {p['entry_price']} | our true P(YES)={p['true_prob']} "
        f"| confidence {p.get('confidence')}/10 | resolves {p.get('resolves')}\n"
        f"EVENT: {event_kind} — {detail}\n"
        "Does the thesis still hold?")
    shape = '{"assessment":"intact|weakening|broken","action":"hold|re-vet|note","why":"one sentence"}'
    try:
        v = llm_client.judge(system=system, user=user, schema_hint=shape)
        return {"assessment": str(v.get("assessment", "?"))[:20],
                "action": str(v.get("action", "?"))[:20],
                "why": str(v.get("why", ""))[:240]}
    except Exception as e:
        return {"assessment": "?", "action": "note", "why": f"[llm error: {e}]"[:240]}


def _candela_brief(p, ev):
    """Read-only CANDELA bull/bear brief for an open-position event (vault commentary).
    Builds a dossier from the position's real facts → candela_args (gate=False). Fail-soft.
    Never affects the book; p(YES) is the LLM's miscalibrated view, NOT a conviction."""
    if not (CANDELA_BRIEFS and candela_args and llm_client and llm_client.configured()):
        return None
    cur_yes = ev.get("mkt_now")
    dossier = {
        "our_side": p.get("side"), "our_entry_price": p.get("entry_price"),
        "our_true_prob_yes": p.get("true_prob"), "confidence_0_10": p.get("confidence"),
        "resolves": p.get("resolves"), "thesis": (p.get("thesis") or "")[:600],
        "current_market_yes": cur_yes, "event": ev.get("detail"),
    }
    try:
        a = candela_args.generate_argument(
            question=p.get("q", ""), data=dossier, category=None,
            entry_price=(cur_yes if cur_yes is not None else p.get("entry_yes")),
            market_id=p.get("market_id"), gate=False)
        return {"p": a.get("p_estimate"), "bull": a.get("bull_case", ""),
                "bear": a.get("bear_case", ""), "synthesis": a.get("synthesis", ""),
                "df": a.get("data_backed_fraction"), "tail": a.get("tail_miscalibration")}
    except Exception as e:
        return {"error": str(e)[:160]}


def detect_events(snap, prev):
    """Compare this cycle's marks to the previous snapshot → list of event dicts."""
    prev_pnl = {k: v for k, v in (prev.get("open_pnl") or {}).items()}
    events = []
    for r in snap["rows"]:
        p = r["p"]
        q = p["q"]
        if r["state"] == "newly_settled":
            events.append({"kind": "RESOLVED", "q": q, "p": p,
                           "detail": f"settled {r['resolved']} → {'WON' if r['won'] else 'LOST'} "
                                     f"(realized {r['pnl']:+.3f})"})
            continue
        if r["state"] != "open":
            continue
        # ADVERSE: unrealized P&L dropped materially vs last cycle
        was = prev_pnl.get(q)
        if was is not None and (r["pnl"] - was) <= -MOVE_THRESH:
            events.append({"kind": "ADVERSE", "q": q, "p": p, "mkt_now": r.get("mkt_now"),
                           "detail": f"unrealized P&L {was:+.3f} → {r['pnl']:+.3f} "
                                     f"(Δ{r['pnl']-was:+.3f}) this cycle"})
            continue
        # EDGE-GONE: the market has CONVERGED to our model on our side — the discount
        # we entered on is captured, so there's no edge left to hold for (take profit).
        # We bought our side BELOW its model value; edge is gone once the market cost of
        # our side rises to that model value.
        mkt = r.get("mkt_now")  # current YES price
        if mkt is not None:
            side_mkt_cost = mkt if p["side"] == "YES" else (1 - mkt)
            side_model_val = p["true_prob"] if p["side"] == "YES" else (1 - p["true_prob"])
            if side_mkt_cost >= side_model_val - EDGE_GONE_SLACK:
                events.append({"kind": "EDGE-GONE", "q": q, "p": p, "mkt_now": mkt,
                               "detail": f"our side now costs {side_mkt_cost:.2f} ≥ model value "
                                         f"{side_model_val:.2f} — converged, discount captured"})
    return events


def gate_fails(snap):
    """Open positions that did NOT survive the panel (gate.survived is False) or were
    never gated (no gate block). vet_book.py writes the gate tags. This is the standing
    enforcement: a position should only be in the book if it passed the panel — anything
    auto-booked by a parallel/autonomous session that bypassed the gate shows up here."""
    fails = []
    for r in snap["rows"]:
        if r["state"] != "open":
            continue
        g = r["p"].get("gate")
        if not g:
            fails.append({"q": r["p"]["q"], "side": r["p"]["side"], "why": "UNGATED (no panel run)"})
        elif g.get("survived") is False:
            fails.append({"q": r["p"]["q"], "side": r["p"]["side"],
                          "why": f"REFUTED {g.get('hard_refuters','?')}/3 by panel"})
    return fails


def run_once(verbose=False):
    snap = analyst_book.mark_book(persist=True)
    if not snap["rows"]:
        if verbose:
            print("no analyst_positions.json")
        return
    prev = {}
    if os.path.exists(SNAP):
        try:
            prev = json.loads(open(SNAP).read())
        except Exception:
            prev = {}

    events = detect_events(snap, prev)
    gfails = gate_fails(snap)

    # vol/IV regime covariate (keyless, fail-soft — read from vol_regime.py's JSON snapshot)
    vol_ctx = _read_vol_regime()

    # LLM-assess only event positions (steady state = 0 calls)
    for ev in events:
        ev["llm"] = _llm_assess(ev["p"], ev["kind"], ev["detail"], vol_ctx=vol_ctx)

    # CANDELA bull/bear brief for OPEN-position events (vault commentary; capped; fail-soft)
    n_brief = 0
    for ev in events:
        if ev["kind"] in BRIEF_EVENT_KINDS and n_brief < MAX_CANDELA_BRIEFS:
            ev["candela"] = _candela_brief(ev["p"], ev)
            if ev["candela"]:
                n_brief += 1

    # correlation concentration (context, deterministic)
    cluster = None
    if corr:
        try:
            drivers = {}
            for r in snap["rows"]:
                if r["state"] == "open":
                    d = corr.driver_of(r["p"]["q"])[0]  # (driver, lose) → driver
                    drivers[d] = drivers.get(d, 0) + 1
            if drivers:
                top = max(drivers.items(), key=lambda kv: kv[1])
                if top[1] >= 3:
                    cluster = f"{top[1]} open positions share driver '{top[0]}'"
        except Exception:
            cluster = None

    # NEW gate-fail since last cycle? (alert once — catches a future ungated auto-booking)
    prev_gf = set(prev.get("gate_fail_qs") or [])
    cur_gf = {g["q"] for g in gfails}
    new_gfails = [g for g in gfails if g["q"] not in prev_gf]

    total = snap["realized"] + snap["unrealized"]
    # persist snapshot for next-cycle diffing
    open_pnl = {r["p"]["q"]: r["pnl"] for r in snap["rows"] if r["state"] == "open"}
    state = {"ts": _ts(), "realized": snap["realized"], "unrealized": snap["unrealized"],
             "total": total, "n_open": snap["n_open"], "n_settled": snap["n_settled"],
             "open_pnl": open_pnl, "gate_fail_qs": sorted(cur_gf)}
    try:
        with open(SNAP + ".tmp", "w") as f:
            json.dump(state, f, indent=2)
        os.replace(SNAP + ".tmp", SNAP)
    except Exception as e:
        print(f"[snap FAIL] {e}", file=sys.stderr)

    # append-only log
    try:
        with open(LOG, "a") as f:
            f.write(json.dumps({"ts": state["ts"], **llm_client.provenance(), "total": round(total, 3),
                                "realized": round(snap["realized"], 3),
                                "unrealized": round(snap["unrealized"], 3),
                                "n_events": len(events),
                                "events": [{"kind": e["kind"], "q": e["q"][:60],
                                            "llm": e.get("llm"),
                                            "candela": ({"p": e["candela"].get("p"),
                                                         "tail": e["candela"].get("tail")}
                                                        if e.get("candela") and not e["candela"].get("error")
                                                        else None)} for e in events],
                                "cluster": cluster,
                                "gate_fails": gfails}) + "\n")
    except Exception as e:
        print(f"[log FAIL] {e}", file=sys.stderr)

    _write_vault(snap, events, cluster, total, gfails, vol_ctx=vol_ctx)

    # alert on events OR a newly-appeared gate-fail (a position bypassed the panel)
    if events or new_gfails:
        lines = [f"🧠 ANALYST AGENT — {len(events)} event(s)",
                 f"book ${total:+.3f} (real ${snap['realized']:+.3f}/unreal ${snap['unrealized']:+.3f})", ""]
        for e in events:
            lines.append(f"[{e['kind']}] {e['q'][:54]}")
            lines.append(f"  {e['detail']}")
            if e.get("llm"):
                l = e["llm"]
                lines.append(f"  → {l['assessment']}/{l['action']}: {l['why']}")
        for g in new_gfails:
            lines.append(f"[GATE-FAIL] {g['side']} {g['q'][:50]} — {g['why']}")
        if cluster:
            lines.append(f"⚠️ corr: {cluster}")
        send_imessage("\n".join(lines))

    if verbose:
        vol_str = ""
        if vol_ctx and vol_ctx.get("iv_regime"):
            vol_str = (f" | iv_regime={vol_ctx['iv_regime']} "
                       f"btc_dvol={vol_ctx.get('btc_dvol','?')} "
                       f"eth_dvol={vol_ctx.get('eth_dvol','?')} "
                       f"IV-RV_btc={vol_ctx.get('iv_rv_spread_btc','?')}")
        print(f"[{state['ts']}] analyst-agent: book ${total:+.3f} "
              f"(real ${snap['realized']:+.3f}/unreal ${snap['unrealized']:+.3f}) | "
              f"{snap['n_open']} open, {snap['n_settled']} settled | {len(events)} event(s)"
              + (f" | {len(gfails)} gate-fail" if gfails else "")
              + (f" | corr: {cluster}" if cluster else "")
              + vol_str)
        for e in events:
            print(f"  [{e['kind']}] {e['q'][:60]} — {e['detail']}")
            if e.get("llm"):
                print(f"     → {e['llm']['assessment']}/{e['llm']['action']}: {e['llm']['why']}")
        for g in gfails:
            print(f"  [GATE-FAIL] {g['side']} {g['q'][:56]} — {g['why']}")


def _write_vault(snap, events, cluster, total, gfails=None, vol_ctx=None):
    try:
        L = [f"# Analyst Agent — live book monitor", "",
             f"_Updated {_ts()} · realized ${snap['realized']:+.3f} · unrealized "
             f"${snap['unrealized']:+.3f} · **TOTAL ${total:+.3f}**_", "",
             "24/7 read-only monitor. Banks resolutions, flags adverse moves / priced-out edges. "
             "PAPER ONLY.", ""]
        # Vol/IV regime covariate block (informational; not a gate)
        if vol_ctx and vol_ctx.get("iv_regime"):
            iv = vol_ctx["iv_regime"]
            iv_icon = {"elevated": "🔴", "suppressed": "🟢", "normal": "🟡"}.get(iv, "⚪")
            L += [f"## {iv_icon} Vol / IV Regime", "",
                  f"| metric | BTC | ETH |",
                  f"|--------|-----|-----|",
                  f"| DVOL (implied vol index) | {vol_ctx.get('btc_dvol','—')} | {vol_ctx.get('eth_dvol','—')} |",
                  f"| HV20 annualized % | {vol_ctx.get('btc_hv_pct','—')} | {vol_ctx.get('eth_hv_pct','—')} |",
                  f"| IV-RV spread | {vol_ctx.get('iv_rv_spread_btc','—')} | {vol_ctx.get('iv_rv_spread_eth','—')} |",
                  f"| HV20 pct-rank (120d) | {vol_ctx.get('btc_hv_pct_120d','—')} | {vol_ctx.get('eth_hv_pct_120d','—')} |",
                  "",
                  f"> **iv_regime = {iv}** (elevated: BTC DVOL > 60; suppressed: < 30; normal otherwise)",
                  f"> _Data: {vol_ctx.get('date','?')} {vol_ctx.get('ts','')}_",
                  ""]
        if events:
            L += ["## ⚠️ Events this cycle", ""]
            for e in events:
                L.append(f"- **[{e['kind']}]** {e['q']}")
                L.append(f"  - {e['detail']}")
                if e.get("llm"):
                    l = e["llm"]
                    L.append(f"  - _agent_: **{l['assessment']}** → {l['action']} — {l['why']}")
                c = e.get("candela")
                if c and not c.get("error"):
                    df = c.get("df")
                    dfs = f"{df:.0%}" if isinstance(df, (int, float)) else "?"
                    tail = " · ⚠TAIL-MISCAL" if c.get("tail") else ""
                    L.append(f"  - _candela bull_: {c.get('bull','')}")
                    L.append(f"  - _candela bear_: {c.get('bear','')}")
                    L.append(f"  - _candela_: p(YES)={c.get('p')} · {dfs} data-backed{tail} "
                             f"— **commentary, not a conviction**")
                elif c and c.get("error"):
                    L.append(f"  - _candela_: (brief unavailable: {c['error']})")
            L.append("")
        if gfails:
            L += ["## 🚧 Gate-fails (in book but did NOT pass the panel)", "",
                  "_Not part of the gated analyst track — run vet_book.py; operator decides keep/cut._", ""]
            for g in gfails:
                L.append(f"- **{g['side']}** {g['q']} — {g['why']}")
            L.append("")
        if cluster:
            L += [f"> ⚠️ correlation: {cluster}", ""]
        L += ["## Positions", "", "| state | side | P&L | resolves | market |",
              "|-------|------|----:|----------|--------|"]
        for r in snap["rows"]:
            p = r["p"]
            if r["state"] in ("settled", "newly_settled"):
                st = f"SETTLED {r['resolved']} ({'WON' if r['won'] else 'LOST'})"
                mk = "—"
            else:
                st = "open"
                mk = f"true {p['true_prob']:.2f} vs mkt {r['mkt_now']:.2f}" if r.get("mkt_now") is not None else "no price"
            L.append(f"| {st} | {p['side']} | {r['pnl']:+.3f} | {p.get('resolves','?')} | {mk} |")
        os.makedirs(os.path.dirname(VAULT), exist_ok=True)
        open(VAULT, "w").write("\n".join(L) + "\n")
    except Exception as e:
        print(f"[vault FAIL] {e}", file=sys.stderr)


if __name__ == "__main__":
    run_once(verbose=("once" not in sys.argv) or True)
