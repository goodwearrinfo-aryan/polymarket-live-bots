#!/usr/bin/env python3
"""obsidian_brain_gate.py — surface the BRAIN's live ENTRY GATE into the Obsidian vault.

"Show the brain working": the entry gate (scalp_lab._gate_entry) puts BRAIN.md + the
knowledge graph + an NVIDIA-70B skeptic in front of every NEW live entry (Phase-1 SHADOW:
it tags, it does not reject). This writer mirrors that reasoning into the vault so it's
visible — the real stamped verdicts as they accrue, the shadow A/B tally that decides
whether to ever flip to enforce, and (with --demo) a live snapshot of the brain judging
the bot's current open markets.

Modes:
  python3 obsidian_brain_gate.py          # light/keyless: real stamped feed + A/B + cached demo
                                          #   (safe for the watchdog — only reads JSON state)
  python3 obsidian_brain_gate.py --demo   # also RUN the real gate on N live open markets
                                          #   (imports scalp_lab, uses kb_query + NVIDIA-70B)

Writes: ~/Documents/PolymarketVault/Brain Live Gate.md   (+ brain_gate_demo.json cache)
"""
import os, sys, json, time, statistics

BASE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(BASE, "scalp_lab_state.json")
DEMO_CACHE = os.path.join(BASE, "brain_gate_demo.json")
NOTE = os.path.expanduser("~/Documents/PolymarketVault/Brain Live Gate.md")
DEMO_N = 4                       # live markets to judge in --demo (caps NVIDIA calls)


def _load(path, default):
    try:
        return json.load(open(path))
    except Exception:
        return default


def _gate_stamped(state):
    """Every gate-stamped entry (open + closed) across all legs. The gate only stamps NEW
    opens, so positions that predate it carry no gate_* keys."""
    opens, closed = [], []
    for leg, v in state.items():
        if not isinstance(v, dict):
            continue
        for p in v.get("open", []):
            if isinstance(p, dict) and "gate_pass" in p:
                opens.append((leg, p))
        for p in v.get("closed", []):
            if isinstance(p, dict) and "gate_pass" in p:
                closed.append((leg, p))
    return opens, closed


def _ab_tally(closed):
    """Shadow-mode A/B: did the brain's PASS entries out-earn its FAIL entries? This is the
    ONLY thing that justifies ever flipping shadow→enforce (and only at n>=30, CI>0)."""
    pas = [p["pnl_usdc"] for _, p in closed if p.get("gate_pass") is True and p.get("pnl_usdc") is not None]
    fai = [p["pnl_usdc"] for _, p in closed if p.get("gate_pass") is False and p.get("pnl_usdc") is not None]
    return pas, fai


def _verdict_icon(v):
    return {True: "✅ PASS", False: "❌ FAIL", None: "➖ unjudged"}.get(v, "?")


def run_demo(state, n=DEMO_N):
    """Import the REAL gate (no prompt duplication) and judge up to n diverse live open
    markets. Read-only — calls the judge, never opens a position. Caches to brain_gate_demo.json."""
    import scalp_lab, kb_query
    # one position per distinct leg, preferring those carrying a question + entry price
    picks, seen = [], set()
    for leg, v in state.items():
        if not isinstance(v, dict) or leg in seen:
            continue
        for p in v.get("open", []):
            if isinstance(p, dict) and (p.get("q") or p.get("question")):
                pp = dict(p)
                pp.setdefault("q", p.get("question", ""))
                pp.setdefault("strategy", leg)
                picks.append((leg, pp)); seen.add(leg); break
        if len(picks) >= n:
            break
    try:
        passages = kb_query.load_passages()
    except Exception:
        passages = None
    cfg = {"gate_min_conf": 0.6, "gate_llm_timeout": 8, "gate_kb_min_score": 3.0}
    llm_left = n
    out = []
    for leg, p in picks:
        verdict, llm_left = scalp_lab._gate_entry(p, {}, cfg, passages, llm_left)
        out.append({"leg": leg, "q": (p.get("q") or "")[:90], "side": p.get("side", "YES"),
                    "entry": round(float(p.get("entry_mid", p.get("entry", 0)) or 0), 3),
                    **verdict})
    json.dump({"ts": time.strftime("%Y-%m-%d %H:%M"), "verdicts": out},
              open(DEMO_CACHE, "w"), indent=2)
    return out


def render(state, demo):
    opens, closed = _gate_stamped(state)
    pas, fai = _ab_tally(closed)
    total_open = sum(len(v.get("open", [])) for v in state.values() if isinstance(v, dict))
    L = []
    L.append("# 🧠 BRAIN — Live Entry Gate\n")
    L.append("> [!info] What this is")
    L.append("> The BRAIN judging **every new live entry** before it counts — `kb` relevance over "
             "[[BRAIN]] + the knowledge graph, plus an NVIDIA-70B skeptic. **Phase-1 SHADOW**: it "
             "*tags* entries, it does **not** reject any. Flip to *enforce* only if PASS out-earns "
             "FAIL with CI>0 after n≥30. See [[Brain Architecture]], [[Decision Log]].\n")
    L.append(f"**Updated:** {time.strftime('%Y-%m-%d %H:%M')}  ·  **Mode:** shadow  ·  "
             f"**Stamped verdicts:** {len(opens) + len(closed)}  (open {len(opens)}, closed {len(closed)})\n")

    L.append("## How the brain decides")
    L.append("- **kb layer** (keyless, always on): lexical relevance of the market vs `BRAIN.md` + the graph.")
    L.append("- **skeptic layer** (NVIDIA-70B): *“the mid is usually well-calibrated — default to FAIL "
             "unless you can name a concrete edge.”* Returns a ≤15-word reason.")
    L.append("- **PASS** only when the skeptic returns pass=true AND confidence ≥ 0.6; "
             "otherwise FAIL (fail-closed under enforce).\n")

    L.append("## Live verdicts — real, stamped on opened entries")
    if opens or closed:
        L.append("| leg | market | side | verdict | conf | src | reason |")
        L.append("|-----|--------|------|---------|------|-----|--------|")
        for leg, p in (opens + closed)[:25]:
            q = str(p.get("q", p.get("question", "")))[:60].replace("|", "/")
            L.append(f"| {leg} | {q} | {p.get('side','')} | {_verdict_icon(p.get('gate_pass'))} | "
                     f"{p.get('gate_conf','')} | {p.get('gate_src','')} | "
                     f"{str(p.get('gate_reason','')).replace('|','/')[:70]} |")
    else:
        L.append(f"> [!note] No stamped verdicts yet — the gate was enabled today and only stamps "
                 f"**new** opens. The {total_open} current open positions predate it; this table "
                 f"fills as legs open fresh entries.")
    L.append("")

    L.append("## Shadow A/B — is the brain adding value?")
    if pas and fai:
        mp, mf = statistics.mean(pas), statistics.mean(fai)
        edge = mp - mf
        ready = "n≥30 reached — run dsr_gate-style CI test before any flip" if min(len(pas), len(fai)) >= 30 \
                else f"accumulating (need n≥30 each; have PASS {len(pas)}, FAIL {len(fai)})"
        L.append(f"- **gate_pass:** n={len(pas)}  mean ${mp:+.4f}/exit")
        L.append(f"- **gate_fail:** n={len(fai)}  mean ${mf:+.4f}/exit")
        L.append(f"- **brain edge:** ${edge:+.4f}/exit  →  {ready}")
    else:
        L.append(f"> [!note] Accumulating — need closed entries on BOTH sides (have PASS {len(pas)}, "
                 f"FAIL {len(fai)}). Until then the brain's value is unproven; **shadow stays on**.")
    L.append("")

    if demo and demo.get("verdicts"):
        L.append(f"## Illustrative — the brain reasoning on current open markets")
        L.append(f"*Snapshot {demo.get('ts','')} · live `_gate_entry` on real open markets (not booked entries).*\n")
        L.append("| leg | market | side | entry | verdict | conf | src | reason |")
        L.append("|-----|--------|------|-------|---------|------|-----|--------|")
        for d in demo["verdicts"]:
            L.append(f"| {d['leg']} | {d['q'].replace('|','/')} | {d['side']} | {d['entry']} | "
                     f"{_verdict_icon(d['pass'])} | {d['conf']} | {d['src']} | "
                     f"{str(d['reason']).replace('|','/')[:70]} |")
        L.append("")
    L.append("---")
    L.append("*Auto-generated by `obsidian_brain_gate.py`. Kill switch: `entry_gate_enabled=False` in scalp_lab.py.*")
    return "\n".join(L)


def main():
    state = _load(STATE, {})
    if not state:
        print("brain_gate: scalp_lab_state.json missing/unreadable — skipped"); return
    if "--demo" in sys.argv:
        try:
            run_demo(state)
            print("brain_gate: demo verdicts refreshed")
        except Exception as e:
            sys.stderr.write(f"brain_gate demo: {type(e).__name__}: {str(e)[:100]}\n")
    demo = _load(DEMO_CACHE, {})
    os.makedirs(os.path.dirname(NOTE), exist_ok=True)
    open(NOTE, "w").write(render(state, demo) + "\n")
    print(f"brain_gate: wrote {NOTE}")


if __name__ == "__main__":
    main()
