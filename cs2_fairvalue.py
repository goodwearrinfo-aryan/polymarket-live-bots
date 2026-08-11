#!/usr/bin/env python3
"""
cs2_fairvalue.py — empirical CS match fair value from the ESTA round table.
Covariate only (logged on psconfirm/nearres entries); never a trading trigger
until it proves predictive at gate. Read-only research.

map_winprob(sA, sB)        — P(team A wins current map | round score sA-sB),
                             from 41,782 pro rounds (cs2_winprob.json), with
                             Laplace smoothing and symmetry averaging.
series_winprob(maps_a, maps_b, p_map, best_of=3)
                           — P(team A wins the series) given maps won and the
                             probability A wins each remaining map.
fair_value(maps_done, score, side_is_team1, best_of=3)
                           — plug PandaScore live_score() output straight in.

CS:GO table is the best free proxy for CS2 (same scoring structure, MR15;
CS2 is MR12 — we rescale round scores by 12/15 before lookup, a documented
approximation).
"""
import json, os, functools

TABLE_F = os.path.expanduser("~/Documents/polymarket/cs2_winprob.json")
MR_SCALE = 12 / 15   # CS2 (MR12) score -> CSGO (MR15) table coordinates


@functools.lru_cache(maxsize=1)
def _table():
    try:
        return json.load(open(TABLE_F))["table"]
    except Exception:
        return {}


def map_winprob(s_a, s_b):
    """P(A wins map | score s_a-s_b). Symmetry-averaged + Laplace smoothed.
    None if the table is missing."""
    t = _table()
    if not t:
        return None
    key, mirror = f"{s_a},{s_b}", f"{s_b},{s_a}"
    n1, w1 = t.get(key, [0, 0])
    n2, w2 = t.get(mirror, [0, 0])
    # mirror cell counts B-perspective wins; convert: A-wins there = n2 - w2
    n = n1 + n2
    w = w1 + (n2 - w2)
    if n < 30:        # too thin — fall back on neighbouring smoothing
        return None
    return round((w + 1) / (n + 2), 4)


def series_winprob(maps_a, maps_b, p_map=0.5, best_of=3):
    """P(A wins BO-n series | maps_a-maps_b, per-map win prob p_map)."""
    need = best_of // 2 + 1
    @functools.lru_cache(maxsize=None)
    def rec(a, b):
        if a >= need:
            return 1.0
        if b >= need:
            return 0.0
        return p_map * rec(a + 1, b) + (1 - p_map) * rec(a, b + 1)
    return round(rec(maps_a, maps_b), 4)


def fair_value(maps_done_score, current_map_score=None, best_of=3):
    """Fair P(team1 wins series) from PandaScore live data.
    maps_done_score: (m1, m2) maps won; current_map_score: (r1, r2) rounds or None.
    Uses the empirical map table for the live map, 0.5 for future maps."""
    m1, m2 = maps_done_score
    if current_map_score:
        r1, r2 = current_map_score
        p_live = map_winprob(round(r1 * MR_SCALE), round(r2 * MR_SCALE))
    else:
        p_live = None
    need = best_of // 2 + 1
    if p_live is None:
        return series_winprob(m1, m2, 0.5, best_of)
    # split: win live map then need series from (m1+1,m2), else from (m1,m2+1)
    pa = series_winprob(m1 + 1, m2, 0.5, best_of)
    pb = series_winprob(m1, m2 + 1, 0.5, best_of)
    return round(p_live * pa + (1 - p_live) * pb, 4)


if __name__ == "__main__":
    print("map 0-0  ->", map_winprob(0, 0), "(should be ~0.50)")
    print("map 10-5 ->", map_winprob(10, 5))
    print("map 14-8 ->", map_winprob(14, 8))
    print("BO3 1-0, map 8-4 ->", fair_value((1, 0), (8, 4)))
    print("BO3 0-1, no live ->", fair_value((0, 1)))
