#!/usr/bin/env python3
"""
esta_extract.py — build an empirical CS map win-probability table from the
ESTA dataset (41,782 pro rounds; Xenopoulos KDD 2022). Read-only research.

For every round in every demo: state = (rounds_A, rounds_B) at round start,
where A = alphabetically-first team name (symmetric, no label leakage);
label = whether A won the map (higher final score). Output:

  cs2_winprob.json: {"<sA>,<sB>": [n_rounds, n_A_won_map], ...}

Run: python3 esta_extract.py [esta_data_dir]   (~1558 demos, streaming lzma)
"""
import json, lzma, os, sys, glob, collections

ESTA = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser(
    "~/Documents/Codex/2026-05-28/is-git-installed/oss-bots/esta/data")
OUT = os.path.expanduser("~/Documents/polymarket/cs2_winprob.json")

counts = collections.defaultdict(lambda: [0, 0])   # (sA,sB) -> [n, A_wins]
n_demos = n_bad = 0

files = sorted(glob.glob(os.path.join(ESTA, "*", "*.json.xz")))
print(f"{len(files)} demos found under {ESTA}")

for i, f in enumerate(files):
    try:
        d = json.load(lzma.open(f))
        rounds = d.get("gameRounds") or []
        if len(rounds) < 16:
            n_bad += 1
            continue
        # final score per team name
        last = rounds[-1]
        finals = {last["tTeam"]: last["endTScore"], last["ctTeam"]: last["endCTScore"]}
        if len(finals) != 2 or last["endTScore"] == last["endCTScore"]:
            n_bad += 1
            continue
        team_a = min(finals)                      # alphabetical, symmetric
        a_won = finals[team_a] > finals[max(finals)]
        score = {t: 0 for t in finals}
        ok = True
        for r in rounds:
            t, ct = r.get("tTeam"), r.get("ctTeam")
            if t not in score or ct not in score:
                ok = False
                break
            key = f"{score[team_a]},{score[max(finals)]}"
            c = counts[key]
            c[0] += 1
            c[1] += 1 if a_won else 0
            w = r.get("winningSide")
            winner = t if w == "T" else ct
            score[winner] += 1
        if not ok:
            n_bad += 1
            continue
        n_demos += 1
    except Exception:
        n_bad += 1
    if (i + 1) % 100 == 0:
        print(f"  {i+1}/{len(files)} parsed ({n_demos} ok, {n_bad} skipped)", flush=True)

json.dump({"n_demos": n_demos, "n_bad": n_bad, "table": dict(counts)},
          open(OUT, "w"))
print(f"done: {n_demos} maps, {sum(v[0] for v in counts.values())} round-states "
      f"-> {OUT}")
