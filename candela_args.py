#!/usr/bin/env python3
"""
candela_args.py — structured bull/bear argument generation for the analyst track.

WHAT (and what it is NOT)
-------------------------
This is the CONSTRUCTIVE counterpart to the refutation-heavy analyst pipeline
(refute_edge.py / judge_panel.py both ATTACK a thesis; nothing built it in a
disciplined way). It ports the *method* of the ACL'19 CANDELA argument-generation
model — its **retrieve -> plan -> realize** decomposition — onto YOUR free LLM
(llm_client.py / NVIDIA-70B / Ollama), NOT the stale 2019 PyTorch code/weights.

  1. RETRIEVE  — extract concrete evidence points, each flagged data_backed
                 (from the provided dossier / market resolution text) vs the
                 model's own un-sourced world-knowledge.
  2. PLAN      — select the key BULL points and BEAR points (steelman BOTH sides)
                 before any prose. The bear case is MANDATORY (anti-confirmation-bias).
  3. REALIZE   — write the bull case, bear case, a synthesis, a probability, and
                 an explicit "what would change my mind".

HONESTY GUARD (the project's own scar — do not remove)
------------------------------------------------------
"argument-only hunts = 23-market null" (analyst_data_gate v5). Generated prose is
NOT an edge. So this tool:
  - computes data_backed_fraction; if the case rests mostly on un-sourced
    world-knowledge it LOUDLY flags ARGUMENT-ONLY (not an edge).
  - never trades, never claims edge. Its output is a cleaner `thesis` + `conviction`
    that STILL must pass the data gate and judge_panel before anything is bet.

Usage:
  from candela_args import generate_argument
  arg = generate_argument(question, resolution=..., data={...}, category="crypto",
                          entry_price=0.30, market_id="0x...", gate=True)

CLI:
  python3 candela_args.py --demo
  python3 candela_args.py --question "Will BTC dip to $90k before Aug 1?" \
      --resolution "Resolves YES if Binance BTCUSDT prints <= 90000 ..." \
      --data dossier.json --entry 0.30 --market-id 0x... --gate
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from llm_client import judge, configured, last_served  # free LLM (NVIDIA/Groq/Ollama)

# data_backed_fraction below this = the case is mostly un-sourced assertion -> not an edge
DATA_FLOOR = 0.34

# tail-sanity: a market this close to 0/1 is "near-certain"; if the LLM's p disagrees with
# it by more than TAIL_GAP, that's the free-LLM tail-miscalibration the 2026-06-18 scan proved
# (it compresses p to ~0.2-0.55 and can't price tails), NOT an edge. Closes the judge_panel
# hole where a hallucinated fat-tail conviction computed a fake "edge" and got rubber-stamped.
TAIL_EXTREME = 0.05
TAIL_GAP = 0.10


def _tail_check(p, entry):
    """Return (is_miscalibration, reason). Only fires on EXTREME markets (<=0.05 or >=0.95),
    so it cannot kill a legitimate data-backed contrarian edge in the normal 0.05-0.95 range."""
    if entry is None or p is None:
        return False, ""
    try:
        p, entry = float(p), float(entry)
    except (TypeError, ValueError):
        return False, ""
    if entry <= TAIL_EXTREME and p - entry > TAIL_GAP:
        return True, (f"market says near-impossible (YES={entry:.2f}) but LLM p={p:.2f} — the free "
                      f"LLM can't price tails (scan 2026-06-18); LLM error, not edge")
    if entry >= 1 - TAIL_EXTREME and entry - p > TAIL_GAP:
        return True, (f"market says near-certain (YES={entry:.2f}) but LLM p={p:.2f} — the free "
                      f"LLM can't price tails (scan 2026-06-18); LLM error, not edge")
    return False, ""


# free-LLM tail-shrinkage (calibration regularizer, 2026-06-22). The free LLM gives
# overconfident point estimates without uncertainty (2026-06-18 scan + the calibration audit:
# its p(YES) skews to the extremes ~79% more than the market). This pulls a tail conviction
# TOWARD 0.5 (the no-information prior) ONLY when it is both extreme AND far from the market —
# softening the conviction the gate prices, never inflating it (the transform can only ever move
# p toward 0.5, so it cannot manufacture an edge). DISTINCT from _tail_check above, which
# DETECTS+REJECTS on the MARKET's extremeness; this TRANSFORMS on the LLM's extremeness. They
# target opposite ends and never fight.
# CAVEAT: factor 0.4 is a PRIOR/placeholder, NOT measured calibration — refute_edge_log has zero
# resolved outcomes yet, so this is a structural regularizer against a known LLM behaviour, not a
# validated calibration. Revisit the factor when >=20 distinct triggered+resolved cases exist.
SHRINK_FACTOR = 0.4    # weight pulled toward 0.5
SHRINK_TAIL_LO = 0.20  # p <= this is a low tail
SHRINK_TAIL_HI = 0.80  # p >= this is a high tail
SHRINK_GAP = 0.20      # |p - market_yes| must EXCEED this to shrink (free LLM agreeing with a
                       # longshot market is not overconfidence — leave it alone)


def _shrink_llm_tail(p, entry):
    """Return (effective_p, shrank, reason). `p` MUST be P(YES) — the _realize schema guarantees
    it ("your probability the market resolves YES"). Shrinks a free-LLM tail conviction toward 0.5
    ONLY when it is both extreme (<=SHRINK_TAIL_LO or >=SHRINK_TAIL_HI) AND far from the market
    (|p-entry| > SHRINK_GAP). Otherwise returns p unchanged. Structural regularizer, not scored."""
    if p is None or entry is None:
        return p, False, ""
    try:
        p, entry = float(p), float(entry)
    except (TypeError, ValueError):
        return p, False, ""
    is_tail = (p <= SHRINK_TAIL_LO or p >= SHRINK_TAIL_HI)
    big_gap = abs(p - entry) > SHRINK_GAP
    if is_tail and big_gap:
        eff = SHRINK_FACTOR * 0.5 + (1 - SHRINK_FACTOR) * p
        return eff, True, (f"free-LLM tail p={p:.2f} vs mkt_yes={entry:.2f} "
                           f"(gap>{SHRINK_GAP}) -> shrunk to {eff:.2f}")
    return p, False, ""


def _dossier_text(data) -> str:
    if data is None:
        return "(no structured data dossier provided)"
    if isinstance(data, str):
        return data.strip() or "(empty)"
    try:
        return json.dumps(data, indent=2, default=str)[:4000]
    except Exception:
        return str(data)[:4000]


# ---------------------------------------------------------------- stage 1: RETRIEVE
def _retrieve(question, resolution, data):
    system = (
        "You are an evidence extractor for a prediction-market analyst. From the market "
        "question, its resolution criteria, and any DATA DOSSIER, list the concrete pieces "
        "of evidence that bear on the outcome. For each, set data_backed=true ONLY if it "
        "comes from the dossier or the resolution text provided; set data_backed=false if it "
        "is your own general/world knowledge (which may be stale or wrong). Be honest about "
        "this flag — it is the whole point. Prefer specific numbers, dates, thresholds."
    )
    user = (
        f"MARKET QUESTION:\n{question}\n\n"
        f"RESOLUTION CRITERIA:\n{resolution or '(none provided)'}\n\n"
        f"DATA DOSSIER:\n{_dossier_text(data)}\n\n"
        "Extract 4-8 evidence points."
    )
    schema = ('{"evidence":[{"point":str,"data_backed":bool,'
              '"source":"dossier|resolution|world_knowledge"}]}')
    out = judge(system, user, schema_hint=schema, temperature=0.1)
    ev = out.get("evidence", []) if isinstance(out, dict) else []
    # normalize
    norm = []
    for e in ev:
        if isinstance(e, dict) and e.get("point"):
            norm.append({"point": str(e["point"]),
                         "data_backed": bool(e.get("data_backed", False)),
                         "source": str(e.get("source", "world_knowledge"))})
    return norm


# ---------------------------------------------------------------- stage 2: PLAN
def _plan(question, evidence):
    ev_lines = "\n".join(f"[{i}] ({'DATA' if e['data_backed'] else 'wk'}) {e['point']}"
                         for i, e in enumerate(evidence)) or "(no evidence)"
    system = (
        "You are a content planner. Given the evidence (each tagged DATA = data-backed or "
        "wk = world-knowledge), select the key talking points for BOTH sides BEFORE writing "
        "prose. The BEAR case is mandatory even if the bull case looks strong — steelman it. "
        "Each point must reference the evidence index/indices it rests on. Prefer DATA-backed "
        "points; a side built only on 'wk' points is weak and you should say so."
    )
    user = f"QUESTION (arguing the YES side):\n{question}\n\nEVIDENCE:\n{ev_lines}"
    schema = ('{"bull":[{"point":str,"evidence_idx":[int]}],'
              '"bear":[{"point":str,"evidence_idx":[int]}]}')
    out = judge(system, user, schema_hint=schema, temperature=0.2)
    if not isinstance(out, dict):
        return {"bull": [], "bear": []}
    return {"bull": out.get("bull", []) or [], "bear": out.get("bear", []) or []}


# ---------------------------------------------------------------- stage 3: REALIZE
def _realize(question, resolution, evidence, plan):
    ev_lines = "\n".join(f"[{i}] ({'DATA' if e['data_backed'] else 'wk'}) {e['point']}"
                         for i, e in enumerate(evidence)) or "(no evidence)"
    system = (
        "You are a prediction-market analyst writing a balanced brief. Using ONLY the evidence "
        "and the plan, write a tight bull case and bear case (2-4 sentences each), then a "
        "synthesis. Give p_estimate = your probability the market resolves YES (0..1), a "
        "confidence 0-100, and an explicit what_would_change_my_mind. Do not invent facts "
        "beyond the evidence; if the evidence is thin, the confidence must reflect that."
    )
    user = (f"QUESTION:\n{question}\n\nRESOLUTION:\n{resolution or '(none)'}\n\n"
            f"EVIDENCE:\n{ev_lines}\n\nPLAN:\n{json.dumps(plan)[:1500]}")
    schema = ('{"bull_case":str,"bear_case":str,"synthesis":str,'
              '"p_estimate":number,"confidence":number,"what_would_change_my_mind":str}')
    out = judge(system, user, schema_hint=schema, temperature=0.3)
    return out if isinstance(out, dict) else {}


def _gate_thesis(arg):
    """Thesis handed to judge_panel. Its `correctness` lens refutes a thesis with no
    number/date (it wants evidence-anchoring, not bare prose). The synthesis is prose,
    so we append the DATA-backed evidence points that carry numbers — a faithful anchor,
    NOT gaming: we only add it when real data-backed numbers exist. A pure argument-only
    case (no numeric data-backed evidence) is left as prose so correctness correctly refutes it."""
    syn = (arg.get("synthesis", "") or "").strip()
    nums = [e["point"] for e in arg.get("evidence", [])
            if e.get("data_backed") and re.search(r"\d", e.get("point", ""))]
    if nums:
        return (syn + " Key data: " + "; ".join(nums[:4])).strip()
    return syn


def _maybe_gate(arg, category, entry_price, market_id):
    try:
        from judge_panel import JudgePanel
    except Exception as e:
        return {"error": f"judge_panel unavailable: {e}"}
    raw_p = float(arg.get("p_estimate") or 0.0)
    # free-LLM calibration shrink: pull an overconfident free-LLM TAIL conviction toward 0.5
    # BEFORE the source-agnostic gate prices it. Guards:
    #  - provenance: only shrink a conviction the FREE LLM produced this run. candela always
    #    produces p_estimate via _realize's judge() (the most recent LLM call before this gate),
    #    so last_served() reflects it; the guard is belt-and-suspenders for a future caller that
    #    hands a market-implied p with no attributable judge() call (leave that raw).
    #  - polarity: p_estimate is P(YES) by the _realize schema. If a future caller hands a P(side)
    #    value (the side=NO & p>=0.80 & mkt<0.20 signature that corrupts refute_edge_log), do NOT
    #    shrink the ambiguous value — warn and leave it raw.
    served = last_served()
    polarity_flip = (str(arg.get("side", "YES")).upper() == "NO" and raw_p >= SHRINK_TAIL_HI
                     and entry_price is not None and float(entry_price) < SHRINK_TAIL_LO)
    if polarity_flip:
        eff_p, shrank, shrink_reason = raw_p, False, ("possible polarity flip (p>=0.80, NO side, "
                                                      "mkt_yes<0.20) — shrink skipped, p left raw")
        sys.stderr.write(f"[candela_args] {shrink_reason}\n")
    elif served is not None:
        eff_p, shrank, shrink_reason = _shrink_llm_tail(raw_p, entry_price)
    else:
        eff_p, shrank, shrink_reason = raw_p, False, "conviction not free-LLM-produced; shrink skipped"
    survived, verdicts = JudgePanel().evaluate_bet(
        thesis=_gate_thesis(arg),
        conviction=eff_p,
        category=category, entry_price=entry_price, side="YES", market_id=market_id)
    verdicts["calibration_shrink"] = {
        # NOT a refutation lens — it transforms the conviction, never rejects — so it ALWAYS
        # "survives". Carrying survived_refutation keeps this audit entry schema-compatible with
        # every consumer that iterates verdicts (candela_args._print, any all()/sum over verdicts),
        # so the heterogeneous entry can never KeyError a gated-run printout.
        "survived_refutation": True,
        "applied": shrank, "raw_p": raw_p, "effective_p": eff_p,
        "reasoning": shrink_reason or "tail+gap not both met; conviction unchanged",
        "served_fallback": bool(served[2]) if served else None}
    # tail-sanity overlay: judge_panel has NO calibration check, so it rubber-stamps a
    # hallucinated fat-tail conviction on a near-impossible market (the gate hole the scan found).
    # Judges the RAW p_estimate (NOT the shrunk eff_p) — the overlay must keep judging the model's
    # actual claim, or it would mask the very miscalibration it exists to catch.
    miscal, reason = _tail_check(arg.get("p_estimate"), entry_price)
    verdicts["tail_sanity"] = {"survived_refutation": not miscal,
                               "reasoning": reason or "no tail miscalibration"}
    if miscal:
        survived = False
    return {"survived": survived, "verdicts": verdicts}


def generate_argument(question, resolution=None, data=None, category=None,
                      entry_price=None, market_id=None, gate=False):
    """Run retrieve -> plan -> realize on the free LLM. Returns a structured dict.
    Honest-by-construction: flags ARGUMENT-ONLY when the case is mostly un-sourced."""
    if not configured():
        raise RuntimeError("free LLM not configured — see llm_client.py header "
                           "(set LLM_PROVIDER=nvidia + NVIDIA_API_KEY, or Ollama).")
    evidence = _retrieve(question, resolution, data)
    plan = _plan(question, evidence)
    real = _realize(question, resolution, evidence, plan)

    n = len(evidence)
    n_data = sum(1 for e in evidence if e["data_backed"])
    frac = (n_data / n) if n else 0.0
    flag = ("ARGUMENT-ONLY (not an edge): case rests mostly on un-sourced world-knowledge; "
            "Lesson — argument-only hunts = 23-market null. Needs a fetchable resolving series."
            if frac < DATA_FLOOR else
            f"data-grounded ({n_data}/{n} points data-backed) — still must pass the data gate + judge_panel")

    arg = {
        "question": question,
        "evidence": evidence,
        "plan": plan,
        "bull_case": real.get("bull_case", ""),
        "bear_case": real.get("bear_case", ""),
        "synthesis": real.get("synthesis", ""),
        "p_estimate": real.get("p_estimate"),
        "confidence": real.get("confidence"),
        "what_would_change_my_mind": real.get("what_would_change_my_mind", ""),
        "data_backed_fraction": round(frac, 2),
        "honesty_flag": flag,
        "is_edge_candidate": frac >= DATA_FLOOR,
    }
    # tail-sanity: an LLM p that disagrees with a near-certain market is miscalibration, not edge.
    miscal, tail_reason = _tail_check(arg["p_estimate"], entry_price)
    arg["tail_miscalibration"] = miscal
    if miscal:
        arg["is_edge_candidate"] = False
        arg["honesty_flag"] = f"⚠ TAIL MISCALIBRATION — {tail_reason}  |  {flag}"
    if gate:
        arg["gate"] = _maybe_gate(arg, category, entry_price, market_id)
    return arg


def _print(arg):
    print("=" * 80)
    print("CANDELA-STYLE ARGUMENT  (retrieve -> plan -> realize, free LLM)")
    print("=" * 80)
    print(f"\nQ: {arg['question']}")
    print(f"\nEVIDENCE ({arg['data_backed_fraction']:.0%} data-backed):")
    for i, e in enumerate(arg["evidence"]):
        tag = "DATA" if e["data_backed"] else "wk  "
        print(f"  [{i}] {tag}  {e['point']}  ({e['source']})")
    print(f"\nBULL CASE:\n  {arg['bull_case']}")
    print(f"\nBEAR CASE:\n  {arg['bear_case']}")
    print(f"\nSYNTHESIS:\n  {arg['synthesis']}")
    print(f"\np(YES)={arg['p_estimate']}   confidence={arg['confidence']}")
    print(f"change-my-mind: {arg['what_would_change_my_mind']}")
    print(f"\n>> {arg['honesty_flag']}")
    if "gate" in arg:
        g = arg["gate"]
        if "error" in g:
            print(f"\njudge_panel: {g['error']}")
        else:
            print(f"\njudge_panel: {'SURVIVED' if g['survived'] else 'REJECTED'}")
            for lens, v in g["verdicts"].items():
                print(f"   [{'PASS' if v['survived_refutation'] else 'REFUTE':6s}] {lens:11s} {v['reasoning']}")


def main():
    ap = argparse.ArgumentParser(description="CANDELA-style structured argument generation (free LLM)")
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--question")
    ap.add_argument("--resolution")
    ap.add_argument("--data", help="path to a JSON data dossier")
    ap.add_argument("--category")
    ap.add_argument("--entry", type=float)
    ap.add_argument("--market-id")
    ap.add_argument("--gate", action="store_true", help="also run judge_panel on the result")
    ap.add_argument("--json", dest="out")
    args = ap.parse_args()

    if args.demo:
        q = "Will Bitcoin dip to $90,000 or below before August 1, 2026?"
        res = ("Resolves YES if the Binance BTCUSDT 1-minute low is at or below 90000 at any "
               "point before 2026-08-01 00:00 UTC; otherwise NO.")
        data = {"current_btc": 104300, "30d_low": 98200, "30d_realized_vol_annualized": 0.46,
                "distance_to_strike_pct": -13.7, "days_to_deadline": 44}
        arg = generate_argument(q, resolution=res, data=data, category="crypto",
                                entry_price=0.30, market_id=None, gate=args.gate)
    else:
        if not args.question:
            ap.error("need --question (or --demo)")
        data = json.load(open(args.data)) if args.data else None
        arg = generate_argument(args.question, resolution=args.resolution, data=data,
                                category=args.category, entry_price=args.entry,
                                market_id=args.market_id, gate=args.gate)
    _print(arg)
    if args.out:
        json.dump(arg, open(args.out, "w"), indent=2)
        print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
