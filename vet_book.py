#!/usr/bin/env python3
"""
vet_book.py — re-vet EVERY open analyst position through the 3-lens refutation panel
and TAG each with its gate status. Honest-by-construction:

  • It NEVER removes a position. Pruning losers before resolution = survivorship bias
    (Lesson 13). The June-30 scoreboard must see every call that was made.
  • It records WHICH positions passed the panel (`gate.survived`) so the scoreboard can
    report the GATED analyst track separately from anything that was booked un-vetted.
  • A position that was booked with no panel and is now REFUTED is a finding, not a
    delete — it gets tagged refuted and the operator decides.

Writes the gate verdict back into analyst_positions.json (adds a `gate` block per
position; positions themselves untouched) + a consolidated Vault report.

Run:  python3 vet_book.py            # live (needs LLM_* / NVIDIA configured)
      python3 vet_book.py --mock     # pipeline shape w/o an endpoint
"""
import os, sys, json
from datetime import datetime, timezone

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
import refute_edge

BOOK = os.path.join(BASE, "analyst_positions.json")
VAULT = os.path.expanduser("~/Documents/PolymarketVault/Reports/analyst_book_vet.md")


def _ts():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def vet_all(mock=False, refreeze=False):
    book = json.loads(open(BOOK).read())
    positions = book.get("positions", [])
    results = []
    for p in positions:
        if p.get("status") == "settled":
            continue  # done; don't re-judge a banked position
        reason = p.get("thesis") or (
            f"booked {p['side']} at {p['entry_price']}, model true P(YES)={p.get('true_prob')}, "
            f"edge {p.get('edge_pct')}pts, confidence {p.get('confidence')}/10")
        try:
            r = refute_edge.refute(p["market_id"], p["side"], p.get("true_prob"),
                                   reason, mock=mock)
        except Exception as e:
            r = {"survived": None, "hard_refuters": None, "verdicts": [],
                 "error": str(e)}
        block = {
            "ts": _ts(),
            "survived": r.get("survived"),
            "hard_refuters": r.get("hard_refuters"),
            "lenses": [{"lens": v["lens"], "refuted": v["refuted"],
                        "confidence": v["confidence"]} for v in r.get("verdicts", [])],
            "mock": mock,
        }
        # `gate` is the FROZEN entry-equivalent verdict the scorecard reads for cohort
        # assignment — set ONCE (first vet) and never overwritten by a scheduled re-vet,
        # because re-judging near resolution can peek at outcome-correlated data = look-ahead
        # that would retro-reassign the milestone cohort. `gate_live` always refreshes (the
        # agent can surface drift). `--refreeze` is the explicit manual override.
        if not p.get("gate") or refreeze:
            p["gate"] = block
        p["gate_live"] = block
        results.append((p, r))

    # write the gate tags back (no position removed/reordered)
    tmp = BOOK + ".tmp"
    with open(tmp, "w") as f:
        json.dump(book, f, indent=2)
    os.replace(tmp, BOOK)
    return results


def _report(results, mock):
    surv = [p for p, r in results if r.get("survived") is True]
    refu = [p for p, r in results if r.get("survived") is False]
    err = [p for p, r in results if r.get("survived") is None]
    L = ["# Analyst Book — gate vet", "",
         f"_{_ts()} · {len(results)} open positions vetted through the 3-lens panel"
         + ("  _(mock)_" if mock else "") + "_", "",
         f"**{len(surv)} survived · {len(refu)} REFUTED · {len(err)} error**", "",
         "No position is removed (survivorship). REFUTED = booked without surviving the "
         "panel; operator decides. The scoreboard should weight only `gate.survived` calls.", "",
         "| verdict | side | position | refuters | conf |",
         "|---------|------|----------|:--------:|:----:|"]
    for p, r in results:
        v = ("✅ SURVIVED" if r.get("survived") is True else
             "❌ REFUTED" if r.get("survived") is False else "⚠️ ERROR")
        hr = r.get("hard_refuters")
        L.append(f"| {v} | {p['side']} | {p['q'][:50]} | {hr if hr is not None else '?'}/3 "
                 f"| {p.get('confidence','?')}/10 |")
    L.append("")
    for p, r in results:
        if r.get("survived") is False or r.get("survived") is None:
            L.append(f"### {'❌' if r.get('survived') is False else '⚠️'} {p['q']}")
            if r.get("error"):
                L.append(f"- error: {r['error']}")
            for v in r.get("verdicts", []):
                if v["refuted"]:
                    L.append(f"- **{v['lens']}** @{v['confidence']}: {v['why']}")
            L.append("")
    os.makedirs(os.path.dirname(VAULT), exist_ok=True)
    open(VAULT, "w").write("\n".join(L) + "\n")
    return surv, refu, err


if __name__ == "__main__":
    mock = "--mock" in sys.argv
    res = vet_all(mock=mock, refreeze=("--refreeze" in sys.argv))
    surv, refu, err = _report(res, mock)
    print(f"[{_ts()}] vet_book: {len(res)} open vetted — "
          f"{len(surv)} survived, {len(refu)} REFUTED, {len(err)} error"
          + ("  (mock)" if mock else ""))
    for p, r in res:
        tag = ("SURVIVED" if r.get("survived") is True else
               "REFUTED " if r.get("survived") is False else "ERROR   ")
        print(f"  {tag} {r.get('hard_refuters','?')}/3  {p['side']:<3} {p['q'][:56]}")
    print(f"  → tags written to analyst_positions.json · report → {VAULT}")
