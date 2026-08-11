#!/usr/bin/env python3
"""procedural_memory.py — the agents' SELF-CALIBRATION loop (the missing memory tier).

The OSS-brain tiers this project already has: CORE (BRAIN.md), SEMANTIC (Lessons/Edges),
EPISODIC (Journal/loggers). The one it didn't: PROCEDURAL — agents that adjust their own
behaviour from their OWN resolution-verified track record (Mem0/LangMem call this
"procedural memory"; A-Mem calls the update step "memory evolution"). This closes the loop:
read what the analyst track actually got RIGHT vs WRONG (scorecard.json resolved bets),
distill an HONEST reflection, and inject it into the judgment agents' system prompt so they
see their own calibration before judging. Wired in pm_agents_24x7.run().

THE HONESTY GATE (the whole point — without it this re-creates the project's #1 failure,
overfitting noise):
  • n == 0       → return ""  (inject NOTHING; a fabricated track record is worse than none)
  • 0 < n < MIN  → a WEAK-PRIOR caveat only ("do not generalize")
  • n >= MIN     → real calibration + at most ONE derived lesson
It never lets an agent change its judgment off a small sample.

PAPER / research only. Read-only over scorecard.json. Stdlib only.
Run:  python3 procedural_memory.py          # print the current reflection
      python3 procedural_memory.py --md      # also write the vault inspection note
"""
import os, json, sys, time

PM = os.path.dirname(os.path.abspath(__file__))
SCORECARD = os.path.join(PM, "scorecard.json")
VAULT_MD = os.path.expanduser("~/Documents/PolymarketVault/Reports/procedural_memory.md")
MIN_N = 20   # below this: no generalization. (Edge bar is n>=30; 20 to even hint at miscalibration.)


def _resolved():
    """Resolved analyst bets with both an outcome and a stated conviction (= P(YES) at entry)."""
    try:
        d = json.load(open(SCORECARD))
    except Exception:
        return []
    return [b for b in d.get("resolved", [])
            if b.get("outcome") in ("YES", "NO") and isinstance(b.get("conviction"), (int, float))]


def _stats(bets):
    n = len(bets)
    convs = [float(b["conviction"]) for b in bets]            # P(YES) at entry
    outs = [1.0 if b["outcome"] == "YES" else 0.0 for b in bets]
    hits = sum(1 for c, o in zip(convs, outs) if (c > 0.5) == (o > 0.5))   # predicted side matched
    brier = sum((c - o) ** 2 for c, o in zip(convs, outs)) / n
    avg_conv = sum(convs) / n
    avg_out = sum(outs) / n
    return dict(n=n, hit_rate=hits / n, brier=brier,
                avg_conv=avg_conv, avg_out=avg_out, calib_gap=avg_conv - avg_out)


def reflection():
    """Return a SHORT system-prompt block summarising the agents' own track record,
    or '' to inject nothing. Honest about sample size by construction."""
    bets = _resolved()
    n = len(bets)
    if n == 0:
        return ""                       # nothing resolved → no injection (honest no-op)
    s = _stats(bets)
    # Framed as the ANALYST TRACK's shared calibration (the scorecard isn't per-agent), and
    # explicitly as background CONTEXT — not an output instruction — so it can't be mistaken
    # for the agent's own terse-output rules.
    head = (f"## ANALYST-TRACK CALIBRATION (resolution-verified prior, n={n})\n"
            f"Background context for your judgment — NOT an output instruction. The analyst book's "
            f"own track record so far: hit-rate {s['hit_rate']:.0%} · mean Brier {s['brier']:.3f} · "
            f"calibration gap {s['calib_gap']:+.2f} (avg conf {s['avg_conv']:.2f} vs realized {s['avg_out']:.2f}).")
    if n < MIN_N:
        return (head + f"\n⚠️ n<{MIN_N}: WEAK PRIOR only — do NOT generalize or shift your judgment from it "
                f"(overfitting a small sample is this project's #1 failure). Note gross miscalibration, nothing finer.")
    if s["calib_gap"] > 0.15:
        lesson = "The analyst track has been systematically OVER-confident — shade convictions toward 0.5."
    elif s["calib_gap"] < -0.15:
        lesson = "The analyst track has been systematically UNDER-confident — calls resolve in its favour more than priced."
    else:
        lesson = "The analyst track is reasonably calibrated — hold the discipline; do not chase the last miss."
    return head + f"\nPRIOR FROM THE RECORD: {lesson}"


def write_md():
    bets = _resolved(); n = len(bets)
    stamp = time.strftime("%Y-%m-%d %H:%M")
    lines = ["---", "type: logger", f"updated: {time.strftime('%Y-%m-%d')}", "tags: [analyst, agents]", "---", "",
             "# Procedural Memory — the agents' self-calibration", "",
             f"_run {stamp} · the [[Brain Architecture]] procedural tier; injected into judgment agents by `pm_agents_24x7`_", ""]
    if n == 0:
        lines += ["**No resolved analyst bets yet (n=0).** The loop is DORMANT and injects nothing —",
                  "a fabricated track record would be worse than none. It activates automatically as the",
                  "analyst book resolves (`scorecard.json` → resolved). This is the correct honest state."]
    else:
        s = _stats(bets)
        lines += [f"- n resolved: **{n}**" + ("" if n >= MIN_N else f"  ⚠️ <{MIN_N}: weak prior, no generalization"),
                  f"- hit-rate: {s['hit_rate']:.0%} · mean Brier: {s['brier']:.3f}",
                  f"- calibration gap: {s['calib_gap']:+.2f} (avg conf {s['avg_conv']:.2f} vs realized {s['avg_out']:.2f})",
                  "", "### Injected reflection", "```", reflection(), "```"]
    os.makedirs(os.path.dirname(VAULT_MD), exist_ok=True)
    open(VAULT_MD, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    return VAULT_MD


if __name__ == "__main__":
    r = reflection()
    print("=== reflection() injected into judgment agents ===")
    print(r if r else "(empty — no resolved bets yet, dormant by design)")
    if "--md" in sys.argv:
        print(f"\nwrote {write_md()}")
