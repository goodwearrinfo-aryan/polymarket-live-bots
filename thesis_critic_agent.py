#!/usr/bin/env python3
"""thesis_critic_agent.py — pre-entry adversarial quality gate (3-lens attack).

Before a scout candidate or edge-hunter dossier gets promoted to the analyst book,
this agent attacks it from 3 independent critical lenses:
  1. STATISTICIAN — base rate, sample size, survivorship, selection bias
  2. CONTRARIAN   — what do bearish market participants know that bulls don't?
  3. RESOLUTION   — is the resolution criteria actually unambiguous? who decides?

Claude's tool-use loop per candidate:
  search_news → find disconfirming evidence
  fetch_market → check resolution language carefully
  list_markets → find related markets pricing the same event differently

Output: PROMOTE if ≥2/3 lenses survive attack, HOLD if 1/3, KILL if 0/3.
Saves to thesis_critic_verdicts.json.

Usage:
  python3 thesis_critic_agent.py --candidate N   # attack Nth gate-passing scout
  python3 thesis_critic_agent.py --dossier N     # attack Nth edge-hunter dossier
  python3 thesis_critic_agent.py --auto          # attack all unreviewed candidates
  python3 thesis_critic_agent.py --once          # quiet watchdog mode
  python3 thesis_critic_agent.py --show          # show past verdicts
"""
import os, json, time, argparse
from pathlib import Path
from agent_toolkit import run_agent, TOOL_DEFS

HERE     = Path(__file__).resolve().parent
VERDICTS = HERE / "thesis_critic_verdicts.json"

SYSTEM = """You are a rigorous pre-entry thesis critic. Your job is to DESTROY the thesis
before any real money (or paper bet) rides on it.

You attack from 3 independent lenses — each must independently attempt to REFUTE the thesis:

LENS 1 — STATISTICIAN: Is the edge real or illusory?
- Is the base rate for this event type actually supportive?
- Could this be survivorship bias (we only see the cases where the signal showed up)?
- Is the sample too small / time period cherry-picked?
- Does the Wang risk premium explain the entire "edge"?

LENS 2 — CONTRARIAN: What do the other side know?
- Why is the market at this price? Are there informed sellers?
- Search for disconfirming news — evidence that contradicts the thesis
- Find related markets that price the same event inconsistently (which is MORE right?)
- Who takes the other side and why might they be right?

LENS 3 — RESOLUTION: Can this actually pay out?
- Read the resolution criteria word for word — is it genuinely unambiguous?
- Who is the resolver? Can they delay, redefine, or VOID this market?
- Is there a scenario where the thesis is RIGHT but the market resolves AGAINST us?
- Are there precedents of this resolver acting unexpectedly?

Your tools:
- fetch_market: get EXACT resolution criteria text
- search_news: find DISCONFIRMING evidence (not confirming)
- list_markets: find related markets pricing the same event differently
- check_open_positions: context on existing book

VERDICT FORMAT (always end with this):
---CRITIC VERDICT---
LENS1_STATISTICIAN: SURVIVES | REFUTED
LENS1_REASON: <key finding>
LENS2_CONTRARIAN: SURVIVES | REFUTED
LENS2_REASON: <key finding>
LENS3_RESOLUTION: SURVIVES | REFUTED
LENS3_REASON: <key finding>
OVERALL: PROMOTE (>=2 survive) | HOLD (1 survives) | KILL (0 survive)
FATAL_FLAW: <the single biggest reason NOT to enter, or "none found">"""


def criticize(thesis_text: str, market_q: str, slug: str = "",
              side: str = "?", verbose: bool = True) -> dict:
    user = (f"Attack this thesis from all 3 lenses — your goal is REFUTATION:\n\n"
            f"Market: '{market_q}'\n"
            f"Side: {side}\n"
            f"Slug: {slug}\n"
            f"Thesis: {thesis_text}\n\n"
            f"Use tools to find DISCONFIRMING evidence. "
            f"For LENS 1: check base rates and look for selection bias. "
            f"For LENS 2: search news for evidence that CONTRADICTS the thesis; find "
            f"related markets that might price this differently. "
            f"For LENS 3: fetch the market and READ the resolution criteria word-for-word "
            f"— flag any ambiguity. Produce a ---CRITIC VERDICT--- block.")
    if verbose:
        print(f"\n[thesis_critic] attacking: {side} '{market_q[:55]}'")
    response = run_agent(SYSTEM, user, verbose=verbose, max_tokens=1100, max_iters=12)

    result = {"q": market_q, "side": side, "slug": slug,
              "ts": time.strftime("%Y-%m-%d %H:%MZ", time.gmtime()),
              "response": response, "overall": "HOLD",
              "lens1": None, "lens1_reason": None,
              "lens2": None, "lens2_reason": None,
              "lens3": None, "lens3_reason": None,
              "fatal_flaw": None}
    if "---CRITIC VERDICT---" in response:
        block = response.split("---CRITIC VERDICT---")[-1]
        for line in block.splitlines():
            line = line.strip()
            for field, key in [
                ("LENS1_STATISTICIAN:", "lens1"),
                ("LENS1_REASON:", "lens1_reason"),
                ("LENS2_CONTRARIAN:", "lens2"),
                ("LENS2_REASON:", "lens2_reason"),
                ("LENS3_RESOLUTION:", "lens3"),
                ("LENS3_REASON:", "lens3_reason"),
                ("OVERALL:", "overall"),
                ("FATAL_FLAW:", "fatal_flaw"),
            ]:
                if line.startswith(field):
                    result[key] = line[len(field):].strip()
    return result


def _load():
    if VERDICTS.exists():
        try:
            return json.loads(VERDICTS.read_text())
        except Exception:
            pass
    return {"verdicts": []}


def _save(st):
    tmp = VERDICTS.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=2))
    tmp.replace(VERDICTS)


def _imessage(msg, recipient="+918449447444"):
    safe = msg.replace("\\","\\\\").replace('"','\\"').replace("\n","\\n")
    safe = "".join(c if ord(c) < 128 else " " for c in safe)
    os.system(f'osascript -e \'tell application "Messages" to send "{safe}" to buddy "{recipient}" of (service 1 whose service type is iMessage)\' 2>/dev/null')


def auto_run(verbose: bool = True):
    """Attack all unreviewed gate-passing scout candidates + edge hunter dossiers."""
    already = set()
    vst = _load()
    for v in vst["verdicts"]:
        already.add(v.get("q",""))

    targets = []

    # Gate-passing scout candidates
    sc = HERE / "scout_candidates.json"
    if sc.exists():
        for c in json.loads(sc.read_text()).get("candidates", []):
            if c.get("gate_pass") and not c.get("promoted") and c.get("q","") not in already:
                d = c.get("draft") or {}
                targets.append({
                    "q": c.get("q",""), "slug": c.get("slug",""),
                    "side": d.get("side","?"),
                    "thesis": d.get("thesis","") or d.get("why",""),
                })

    # Edge hunter dossiers
    ef = HERE / "edge_hunter_dossiers.json"
    if ef.exists():
        for d in json.loads(ef.read_text()).get("dossiers", []):
            if d.get("market","") not in already and d.get("signal_confirmed") in ("YES","PARTIAL"):
                targets.append({
                    "q": d.get("market",""), "slug": d.get("slug",""),
                    "side": d.get("side","?"),
                    "thesis": d.get("thesis",""),
                })

    if not targets:
        print("no unreviewed candidates to criticize")
        _save(_load())   # heartbeat: refresh output mtime so the watchdog health-monitor sees a
        return           # live (not infinitely-stale) idle agent; a real crash still goes stale.

    promotes, kills = [], []
    for t in targets[:3]:   # cap 3/run
        result = criticize(t["thesis"], t["q"], t["slug"], t["side"], verbose=verbose)
        vst["verdicts"].append(result)
        overall = result.get("overall","HOLD")
        if verbose:
            surviving = sum(1 for k in ("lens1","lens2","lens3") if result.get(k) == "SURVIVES")
            print(f"\n  {overall:8} ({surviving}/3 lenses survive)  "
                  f"fatal_flaw: {(result.get('fatal_flaw') or 'none')[:60]}")
        if overall == "PROMOTE":
            promotes.append(result)
        elif overall == "KILL":
            kills.append(result)

    _save(vst)
    print(f"\n[thesis_critic] {len(targets[:3])} reviewed — "
          f"{len(promotes)} PROMOTE, {len(kills)} KILL → {VERDICTS}")

    if promotes:
        lines = ["[thesis_critic] PROMOTE:"]
        for r in promotes:
            lines.append(f"{r['side']} '{r['q'][:45]}'")
        _imessage("\n".join(lines))
    if kills:
        lines = ["[thesis_critic] KILL (fatal flaw):"]
        for r in kills:
            lines.append(f"{r['side']} '{r['q'][:42]}' — {(r.get('fatal_flaw') or '')[:55]}")
        _imessage("\n".join(lines))


def show():
    st = _load()
    vs = st.get("verdicts", [])
    print(f"\n  Thesis critic verdicts: {len(vs)}\n")
    for v in vs[-10:]:
        surv = sum(1 for k in ("lens1","lens2","lens3") if v.get(k) == "SURVIVES")
        print(f"  [{v.get('ts','')}] {v.get('overall','?'):8} {surv}/3  "
              f"{v.get('side','?'):3}  {v.get('q','?')[:50]}")
        if v.get("fatal_flaw") and v["fatal_flaw"] != "none found":
            print(f"     FATAL: {v['fatal_flaw'][:80]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidate", type=int, metavar="N",
                    help="criticize Nth gate-passing scout candidate")
    ap.add_argument("--dossier", type=int, metavar="N",
                    help="criticize Nth edge-hunter dossier")
    ap.add_argument("--auto", action="store_true",
                    help="attack all unreviewed candidates (max 3/run)")
    ap.add_argument("--once", action="store_true", help="quiet watchdog mode")
    ap.add_argument("--show", action="store_true")
    a = ap.parse_args()

    if a.show:
        show(); return
    if a.auto or a.once:
        auto_run(verbose=not a.once); return

    if a.candidate is not None:
        sc = HERE / "scout_candidates.json"
        cands = [c for c in json.loads(sc.read_text()).get("candidates",[])
                 if c.get("gate_pass")]
        if a.candidate >= len(cands):
            print(f"only {len(cands)} gate-passing candidates"); return
        c = cands[a.candidate]; d = c.get("draft") or {}
        result = criticize(d.get("thesis",""), c.get("q",""), c.get("slug",""), d.get("side","?"))
        vst = _load(); vst["verdicts"].append(result); _save(vst)
        return

    if a.dossier is not None:
        ef = HERE / "edge_hunter_dossiers.json"
        dossiers = json.loads(ef.read_text()).get("dossiers",[])
        if a.dossier >= len(dossiers):
            print(f"only {len(dossiers)} dossiers"); return
        d = dossiers[a.dossier]
        result = criticize(d.get("thesis",""), d.get("market",""), d.get("slug",""), d.get("side","?"))
        vst = _load(); vst["verdicts"].append(result); _save(vst)
        return

    ap.print_help()


if __name__ == "__main__":
    main()
