"""
edge3_stale.py — STALE-RESOLUTION LATENCY.

Thesis: a game is OVER (ESPN has the final), but the Polymarket market hasn't
resolved and still trades at 0.97 / 0.03 instead of 1.00 / 0.00. The last few
cents are free if you can confirm the result faster than the market closes.
A latency edge, not a predictive one — you don't out-predict, you out-poll.

Cross sports_data.py (ESPN final results, last 30d) against live open gamma
markets whose title names a team that already has a final. Flag any market
trading >3¢ away from the result-implied 0/1 while the game is decided.

Read-only. Paper research. No funds.
"""
import re, sys
from pathlib import Path
import edge_common as ec

PM = Path(__file__).resolve().parent

def load_results():
    """ESPN finals -> list of (winner, loser, league, completed)."""
    try:
        import sports_data
        pay = sports_data.load()
    except Exception as e:
        print(f"  ⚠️ sports_data load failed: {e}")
        return []
    finals = []
    for ev in pay.get("events", []):
        if not ev.get("completed"):
            continue
        teams = ev.get("teams") or []
        # winner is a SIDE LABEL ("home"/"away"/"draw"); resolve to the actual
        # team name, else we match the word "home" against market questions forever.
        winner = None
        if ev.get("winner") == "home":
            winner = ev.get("home")
        elif ev.get("winner") == "away":
            winner = ev.get("away")
        if winner:
            finals.append({
                "winner": winner,
                "loser": ev.get("away") if ev.get("winner") == "home" else ev.get("home"),
                "league": ev.get("league", ""),
                "teams": teams,
                "date": ev.get("date", ""),
            })
    return finals

def team_tokens(name):
    return set(w for w in re.findall(r"[a-z0-9]+", (name or "").lower()) if len(w) > 2)

def scan():
    print("="*80)
    print("  EDGE 3 — STALE-RESOLUTION LATENCY (ESPN final vs live Poly price)")
    print("="*80)
    finals = load_results()
    print(f"  ESPN finals (30d, completed): {len(finals)}")
    if not finals:
        print("  No finals available — sports_data may need a refresh (5:55am pull).")
        return []
    mkts = ec.poly_markets(pages=10, min_vol=2000)
    print(f"  Live Poly markets scanned: {len(mkts)}\n")

    # Build winner token index
    flags = []
    for m in mkts:
        yes = m["yes"]
        # only interesting if market is near-decided but not resolved
        if not (0.03 < yes < 0.97):
            # already at the rail -> nothing to capture
            continue
        qtok = team_tokens(m["q"])
        if not qtok:
            continue
        for f in finals:
            wtok = team_tokens(f["winner"])
            ltok = team_tokens(f["loser"])
            if not wtok or not ltok:
                continue
            # REQUIRE BOTH teams of the completed game in the question: this is a
            # GAME-level market ("will <winner> beat <loser>?"), not a season
            # futures/prop that merely names one team. Both-team match is what
            # rules out the false positives (2026 MLS Cup futures etc).
            if wtok <= qtok and ltok <= qtok:
                # market is about THIS completed game -> should be ~1.00 if decided
                # capture if it's trading meaningfully below 0.97
                gap = 1.0 - yes
                if 0.03 < gap < 0.97:
                    flags.append((gap, m, f))
                break

    flags.sort(key=lambda t: t[0], reverse=True)
    print(f"  Markets for a COMPLETED game, still off-rail: {len(flags)}\n")
    print(f"  {'gap':>5} {'YES':>5} {'league':>8}  question  /  winner  /  loser")
    print("  " + "-"*74)
    for gap, m, f in flags[:25]:
        print(f"  {gap:>5.2f} {m['yes']:>5.2f} {f['league']:>8}  {m['q'][:34]:34} / {f['winner'][:12]:12} / {f['loser'][:12]}")
    print(f"\n  ⚠️ Each flag MUST be hand-verified: the question may not be 'will <winner> win'")
    print(f"    (could be a prop, a different game, or future meeting). Latency edge is real")
    print(f"    only if the SAME completed game maps to an UNRESOLVED market. Candidates: {len(flags)}")
    return flags

if __name__ == "__main__":
    scan()
