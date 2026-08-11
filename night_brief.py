#!/usr/bin/env python3
"""
night_brief.py — 24/7 worker #1 (pm-night-analyst, as a launchd script).

Synthesizes the WHOLE live state into ONE dollars-first brief on the FREE model
(minimax-m3 -> ollama floor). Read-only, paper. No iMessage (it's a periodic
brief, not an alert) — writes vault + /tmp log. Run: python3 night_brief.py once
"""
import os, sys
import worker_lib as W

STEM = "night_brief"


def gather():
    """Deterministic, $0 — no LLM here."""
    parts = []
    # 1) live book (open positions + realized/unrealized)
    try:
        import analyst_book
        snap = analyst_book.mark_book(persist=False)
        rows = snap.get("rows", [])
        opens = [r for r in rows if r.get("state") == "open"]
        parts.append(f"BOOK: {len(rows)} positions, {len(opens)} open | "
                     f"realized={snap.get('realized')} unrealized={snap.get('unrealized')}")
        for r in opens[:12]:   # cap prompt weight so the free NIM call stays under its read timeout
            p = r.get("p", {})
            parts.append(f"  open: {str(p.get('q','?'))[:80]} side={p.get('side','?')} "
                         f"true_prob={p.get('true_prob','?')} mkt_now={r.get('mkt_now','?')}")
    except Exception as e:
        parts.append(f"BOOK: unavailable ({e})")
    # 2) leg health (realized P&L + CI + DSR) — the scalp track honest null check
    lh = W.run_capture([sys.executable, "leg_health.py"], timeout=150)
    if lh and not lh.startswith("[run_capture FAIL"):
        # keep the per-leg verdict table (first block) — it's the signal. Trimmed 40→18 lines
        # to keep the free-NIM request light enough to beat its read timeout (prev: fell to Ollama).
        head = "\n".join(lh.splitlines()[:18])
        parts.append("LEG HEALTH:\n" + head)
    else:
        parts.append(f"LEG HEALTH: unavailable ({lh[:80]})")
    # 3) arb track locks (the only +EV family) if a summary file exists
    for f in ("basket_locks.json", "multiarb_state.json"):
        p = os.path.join(os.path.dirname(__file__), f)
        if os.path.exists(p):
            try:
                import json
                d = json.load(open(p))
                n = len(d) if isinstance(d, (list, dict)) else "?"
                parts.append(f"ARB {f}: {n} entries")
            except Exception:
                pass
    return "\n".join(parts)


def main():
    state = gather()
    system = (
        "You are the night-shift analyst for a PAPER-ONLY Polymarket bot. Optimize DOLLARS, "
        "not win-rate. Hard priors: a positive point estimate on small n is NOISE; controls "
        "(allin/coinflip/coindown/coinup) MUST be losing (if positive at n>=30 that's a marking "
        "BUG, flag it); the scalp legs are a confirmed honest null; only structural arb has ever "
        "been +EV. Be terse and concrete. Do NOT invent numbers beyond what is given.")
    prompt = (
        "Here is the current live state. Write a tight brief with exactly three sections:\n"
        "1) WHAT CHANGED (or 'steady-state null' if nothing material)\n"
        "2) SINGLE MOST ACTIONABLE THING (one item, dollars-first; 'nothing actionable' is a valid answer)\n"
        "3) NOISE TO IGNORE (the tempting-but-not-real)\n"
        "Max ~180 words.\n\n=== STATE ===\n" + state)

    brief = W.free_chat(prompt, system=system, max_tokens=500)
    if not brief:
        brief = ("(free LLM unavailable this cycle — raw state only)\n\n" + state)
        served = "n/a (LLM down)"
    else:
        served = W.prov()

    body = f"# Night Brief\n\n_Served by: {served}_\n\n{brief}\n\n---\n<details><summary>raw state</summary>\n\n```\n{state}\n```\n</details>"
    W.vault_write(STEM, body)
    W.log(STEM, f"brief written | served={served} | state_chars={len(state)}")


if __name__ == "__main__":
    main()
