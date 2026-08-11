#!/usr/bin/env python3
"""cricket_markets.py — read-only KEYLESS logger: Polymarket CRICKET markets → Obsidian.

Pulls the Polymarket feed (the bot's keyless edge_common — no API key), strict-filters CRICKET
markets (IPL / T20 / ODI / Test / internationals), and logs each market's YES price, volume,
liquidity, and end date. Writes Obsidian + JSONL history every run.

EVENT-DRIVEN BY DESIGN: cricket markets only list around cricket events (IPL ~Mar-May, T20/ODI
World Cups, big series), so this is DORMANT between events and AUTO-ACTIVATES when they appear —
the same dormant-rare honesty as dataarb. 0 markets in the off-season is correct, not broken.

Strict filter excludes other sports (FIFA/football/NBA/...) — better to log nothing than to
mislabel a football market as cricket (the ESPN recurring-game lesson). Read-only; NOT a leg.
Run: cricket_markets.py
"""
import os, sys, json, re, datetime as _dt

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import edge_common  # poly_markets (keyless gamma feed)

LOG = os.path.join(HERE, "cricket_markets_log.jsonl")
VAULT = os.path.expanduser("~/Documents/PolymarketVault/Reports/cricket_markets.md")

INC = re.compile(r"\b(cricket|ipl|t20i?|odi|test match|the ashes|bbl|psl|ranji|super over|"
                 r"duckworth|wicket|run chase|county championship|hundred ball)\b", re.I)
EXC = re.compile(r"\b(fifa|football|soccer|nfl|nba|tennis|nhl|mlb|f1|formula|rugby|hockey|golf|"
                 r"basketball|baseball)\b", re.I)


def collect():
    mk = edge_common.poly_markets(pages=10, per_page=100, min_vol=0)
    cric = [m for m in mk
            if INC.search(m.get("q", "")) and not EXC.search(m.get("q", ""))]
    cric.sort(key=lambda m: -(m.get("vol") or 0))
    return cric, len(mk)


def render_md(cric, feed_n, ts):
    L = ["---", "type: cricket_markets", f"updated: {ts}", "---", "",
         "# Polymarket cricket markets (keyless)", "",
         f"_Updated {ts}. Cricket markets on Polymarket via the keyless feed ({feed_n} markets "
         f"scanned). EVENT-DRIVEN: dormant between cricket events, auto-activates during IPL / "
         f"World Cups / major series. Read-only._", ""]
    if not cric:
        L += ["## Status: DORMANT (no live cricket markets)",
              "- 0 cricket markets currently listed on Polymarket (off-season / no live event).",
              "- This logger auto-fills the moment cricket markets appear — nothing to do."]
        return "\n".join(L) + "\n"
    L += [f"## {len(cric)} live cricket market(s)", "",
          "| YES | vol | end | market |", "|----:|----:|-----|--------|"]
    for m in cric:
        L.append(f"| {m.get('yes')} | ${round(m.get('vol') or 0):,} | "
                 f"{(m.get('end') or '')[:10]} | {m.get('q','')[:70]} |")
    return "\n".join(L) + "\n"


def main():
    ts = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        cric, feed_n = collect()
    except Exception as e:
        cric, feed_n = [], 0
        print(f"[feed FAIL] {e}")
    rec = {"ts": ts, "feed_n": feed_n, "n_cricket": len(cric),
           "markets": [{"q": m.get("q"), "yes": m.get("yes"), "vol": round(m.get("vol") or 0),
                        "end": (m.get("end") or "")[:10], "cid": m.get("id")} for m in cric]}
    try:
        open(LOG, "a").write(json.dumps(rec) + "\n")
    except Exception as e:
        print(f"[log FAIL] {e}")
    try:
        os.makedirs(os.path.dirname(VAULT), exist_ok=True)
        open(VAULT, "w").write(render_md(cric, feed_n, ts))
    except Exception as e:
        print(f"[vault FAIL] {e}")
    print(f"[{ts}] cricket_markets: {len(cric)} cricket / {feed_n} scanned "
          f"({'DORMANT — off-season' if not cric else 'LIVE'}) -> {VAULT}")


if __name__ == "__main__":
    main()
