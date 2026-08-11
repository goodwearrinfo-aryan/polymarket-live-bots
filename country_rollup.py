#!/usr/bin/env python3
"""country_rollup.py — READ-ONLY aggregator. Reads every logger's *_log.jsonl,
classifies each event by country (country_tag.py), and breaks the signal down
BY COUNTRY so you can see which geographies carry the edge. Writes one combined
Obsidian report. Touches no logger; re-derives country from the stored title.

PAPER ONLY, read-only. Usage: python3 country_rollup.py
"""
import os, sys, json, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from country_tag import country_of
VAULT = os.path.expanduser("~/Documents/PolymarketVault/Reports/country_breakdown.md")

# logger -> (log filename, metric label, metric extractor). Metric is the per-event
# signal in CENTS where positive = good (or just a value to average). None = skip.
def _drift48(r):     # sharpdrift: want >0
    m = r.get("marks", {}).get("48")
    return None if m is None else round(100 * (m - r["mid0"]), 2)
def _exitdrift(r):   # whalexit: want <0 (price fell after sharp sold)
    m = r.get("marks", {}).get("48")
    return None if m is None else round(100 * (m - r["mid0"]), 2)
def _atask(r):       # latticesnap
    return None if r.get("atask_net") is None else round(100 * r["atask_net"], 2)
def _reversion(r):   # tapeshock: want >0
    m = r.get("marks", {}).get("360")
    return None if m is None else round(100 * (-(m - r["shock_mid"]) * r.get("side", 1) - (r.get("spread") or 0)), 2)
def _takeno(r):
    return None if r.get("takeno_pnl") is None else round(100 * r["takeno_pnl"], 2)
def _favpnl(r):
    return None if r.get("fav_pnl") is None else round(100 * r["fav_pnl"], 2)
def _disc(r):
    return None if r.get("discount") is None else round(100 * r["discount"], 2)
def _overround(r):
    return None if r.get("overround_mid") is None else round(100 * r["overround_mid"], 2)

LOGGERS = [
    ("sharpdrift", "sharpdrift_log.jsonl", "drift48¢", _drift48),
    ("whalexit",   "whalexit_log.jsonl",   "exitdrift¢", _exitdrift),
    ("latticesnap","latticesnap_log.jsonl","atask¢",   _atask),
    ("tapeshock",  "tapeshock_log.jsonl",  "revert¢",  _reversion),
    ("deadline",   "deadline_log.jsonl",   "takeNO¢",  _takeno),
    ("favwatch",   "favwatch_log.jsonl",   "favPnL¢",  _favpnl),
    ("redeemdisc", "redeemdisc_log.jsonl", "disc¢",    _disc),
    ("overround",  "overround_log.jsonl",  "overrnd%", _overround),
]


def _read(fn):
    p = os.path.join(HERE, fn)
    return [json.loads(l) for l in open(p)] if os.path.exists(p) else []


def rollup():
    # per (country, logger) -> {n, vals[]}
    grid = {}
    totals = {}                                  # country -> total events
    for name, fn, label, mx in LOGGERS:
        for r in _read(fn):
            c = country_of(r.get("title") or r.get("event") or "")
            totals[c] = totals.get(c, 0) + 1
            cell = grid.setdefault((c, name), {"n": 0, "vals": []})
            cell["n"] += 1
            v = mx(r)
            if v is not None:
                cell["vals"].append(v)
    return grid, totals


def export(grid, totals):
    L = ["# Country Breakdown — edge by geography across all loggers",
         "",
         "> READ-ONLY rollup of every logger's events tagged by country. Shows where the",
         "> signal concentrates. n = events logged; metric = mean per-event signal (¢, +=good).",
         f"> Updated {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}",
         ""]
    if not totals:
        L += ["_No logged events yet across any logger — country breakdown populates as logs mature._"]
        os.makedirs(os.path.dirname(VAULT), exist_ok=True)
        open(VAULT, "w").write("\n".join(L) + "\n")
        return 0
    order = sorted(totals, key=lambda c: -totals[c])
    L += ["## Most active geographies", "", "| country | total events | loggers firing |", "|---|--:|---|"]
    for c in order:
        firing = [name for (cc, name) in grid if cc == c]
        L.append(f"| {c} | {totals[c]} | {', '.join(sorted(set(firing)))} |")
    L += ["", "## Per-country signal", ""]
    for c in order:
        L.append(f"### {c}  ({totals[c]} events)")
        for name, fn, label, mx in LOGGERS:
            cell = grid.get((c, name))
            if not cell:
                continue
            if cell["vals"]:
                mean = round(sum(cell["vals"]) / len(cell["vals"]), 2)
                L.append(f"- **{name}**: n={cell['n']} · mean {label} = {mean:+.2f}")
            else:
                L.append(f"- {name}: n={cell['n']} (none matured yet)")
        L.append("")
    L += ["**Read:** a country where one logger shows a consistent positive mean over many "
          "events is where to look first — but it still needs n≥30 + CI>0 within that country "
          "before it's an edge, not just a favorable slice (beware of slicing into noise)."]
    os.makedirs(os.path.dirname(VAULT), exist_ok=True)
    open(VAULT, "w").write("\n".join(L) + "\n")
    return len(order)


if __name__ == "__main__":
    g, t = rollup()
    ncountries = export(g, t)
    print(f"country_rollup: {sum(t.values())} events across {ncountries} countries -> {VAULT}")
    for c in sorted(t, key=lambda x: -t[x])[:8]:
        print(f"  {c:14s} {t[c]} events")
