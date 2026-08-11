#!/usr/bin/env python3
"""
graduation_watch.py — the 4-guard audit, made AUTONOMOUS (the free twin of today's audit run).

Today's all-datasets-null audit was 5 Claude subagents (expensive). This is its purpose as a
cheap recurring Python worker: re-run the GRADUATION GATE (n>=30 ∧ bootstrap CI>0 ∧ beats
control ∧ DSR-aware) over every forward-accumulating track, and ALERT ONLY when the null
finally BREAKS — i.e. a track graduates for the first time. The whole project is "wait for
forward data"; this watches for the moment that wait pays off, so you don't have to.

Tracks: scalp legs (dsr_gate), structural-arb combined (multiarb, theme-cluster honest),
futures main/runner/lev. Pure stats — NO LLM, zero Claude tokens. Steady-state null = SILENT
(only a NEW graduation pings). State in .graduation_watch_state.json so it alarms once, not
every run. Read-only; never trades. paper.
"""
import os, sys, json, subprocess
from datetime import datetime, timezone

PM = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PM)
STATE = os.path.join(PM, ".graduation_watch_state.json")
VAULT = os.path.expanduser("~/Documents/PolymarketVault/Reports/Graduation Watch.md")
FUT = os.path.expanduser("~/Documents/futures-bot/paper_state.json")
TARGETS = ["krisharyan@icloud.com"]


def _boot_ci(xs, iters=4000, seed=42):
    if len(xs) < 2:
        return (None, None)
    import random
    rnd = random.Random(seed); n = len(xs)
    m = sorted(sum(xs[rnd.randrange(n)] for _ in range(n)) / n for _ in range(iters))
    return (round(m[int(0.025 * iters)], 4), round(m[int(0.975 * iters)], 4))


def scalp_track():
    """Any scalp leg GRADUATE? (reuse dsr_gate's verdicts)."""
    try:
        import dsr_gate
        rows = dsr_gate.gate()
        if not isinstance(rows, list):
            return []
        return [f"scalp:{r['leg']}" for r in rows if "GRADUATE" in str(r.get("verdict", ""))]
    except Exception as e:
        return [f"scalp:ERROR({str(e)[:40]})"]


def arb_track():
    try:
        import multiarb
        b = multiarb.build()
        return ["arb:multiarb"] if b.get("gate", {}).get("GRADUATE") else []
    except Exception as e:
        return [f"arb:ERROR({str(e)[:40]})"]


def futures_track():
    grads = []
    try:
        s = json.load(open(FUT))
        fee = 0.016
        for book, key in (("main", "closed"), ("runner", "runner_closed"), ("lev", "lev_closed")):
            cl = s.get(key, [])
            pnls = [t.get("pnl", t.get("pnl_pct")) for t in cl if t.get("pnl") is not None]
            pnls = [p - fee for p in pnls if p is not None]   # net of one taker round-trip
            if len(pnls) >= 30:
                lo, hi = _boot_ci(pnls)
                if lo is not None and lo > 0:
                    grads.append(f"futures:{book}")
    except Exception as e:
        grads.append(f"futures:ERROR({str(e)[:40]})")
    return grads


SKEPTIC_SYS = (
    "You are an adversarial backtest skeptic for a paper prediction-market bot. A track just "
    "PASSED the stats gate (n>=30, bootstrap CI>0, beats control, DSR). The project's history is "
    "that EVERY apparent edge here was an ARTIFACT until proven otherwise. Default to ARTIFACT. "
    "Judge which single known killer is the MOST likely culprit and the ONE check a human must "
    "run before trusting it: gap-through/clean-fill fills (Lesson 13), survivorship (conditioning "
    "on resolve), best-of-N selection (needs DSR deflation), lifetime-avg-price contamination, "
    "regime (one short window), or theme-correlation (effective-n « raw n). Be terse.")
SKEPTIC_SCHEMA = '{"verdict":"REAL|ARTIFACT|UNSURE","top_killer":"<one>","check":"<the one check to run>"}'

def free_skeptic(track):
    """Free-LLM (NVIDIA NIM) adversarial check on a fresh graduation — agent-quality judgment at
    ZERO Claude cost. Fires only when the null breaks (rare). Fail-soft → None if no free endpoint
    or the call fails. ADVISORY ONLY: the stats gate is authoritative; this flags artifact risk."""
    try:
        import llm_client
        if not llm_client.configured():
            return None
        v = llm_client.judge(SKEPTIC_SYS, f"Track that just graduated: {track}. Which killer is "
                             f"most likely and what is the one check to run before trusting it?",
                             schema_hint=SKEPTIC_SCHEMA)
        if isinstance(v, dict) and v.get("verdict"):
            return f"{track}: free-LLM skeptic={v['verdict']} (likely {v.get('top_killer','?')}; check: {v.get('check','?')})"
    except Exception:
        return None
    return None


def imessage(body):
    for t in TARGETS:
        sc = (f'tell application "Messages"\n set s to 1st service whose service type = iMessage\n'
              f' send "{body}" to buddy "{t}" of s\nend tell')
        try:
            subprocess.run(["osascript", "-e", sc], capture_output=True, timeout=15)
        except Exception:
            pass


def main():
    grads = [g for g in (scalp_track() + arb_track() + futures_track()) if "ERROR" not in g]
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    try:
        prev = set(json.load(open(STATE)).get("graduated", []))
    except Exception:
        prev = set()
    now = set(grads)
    new = now - prev

    status = ("🎓 GRADUATED: " + ", ".join(sorted(now))) if now else "still NULL across all tracks (expected — keep accumulating)"
    note = (f"---\ntype: dashboard\nstatus: live\n---\n\n# 🎓 Graduation Watch\n\n"
            f"*Autonomous 4-guard re-audit (free twin of the all-datasets-null agent run). "
            f"Alerts only when the null BREAKS. Auto {ts}.*\n\n**{status}**\n\n"
            f"Tracks checked: scalp legs (dsr_gate) · structural-arb (multiarb, theme-cluster) · "
            f"futures main/runner/lev (net-of-fee CI). Gate = n≥30 ∧ CI>0 ∧ beats control ∧ DSR.\n")
    try:
        os.makedirs(os.path.dirname(VAULT), exist_ok=True); open(VAULT, "w").write(note)
    except Exception as e:
        print(f"vault write failed: {e}", file=sys.stderr)

    if new:
        # fire the FREE-LLM adversarial skeptic on each new graduation (zero Claude tokens)
        skeptic = [s for s in (free_skeptic(t) for t in sorted(new)) if s]
        sk_txt = (" | " + " ; ".join(skeptic)) if skeptic else ""
        imessage(f"🎓 EDGE EMERGED — first graduation: {', '.join(sorted(new))}. Null broke; "
                 f"verify before trusting.{sk_txt}")
        if skeptic:
            with open(VAULT, "a") as f:
                f.write("\n## ⚔️ free-LLM skeptic (advisory, NIM)\n" + "\n".join(f"- {s}" for s in skeptic) + "\n")
        print(f"NEW GRADUATION: {sorted(new)}{sk_txt}")
    else:
        print(status)
    json.dump({"ts": ts, "graduated": sorted(now)}, open(STATE, "w"), indent=2)
    sys.exit(0)


if __name__ == "__main__":
    main()
