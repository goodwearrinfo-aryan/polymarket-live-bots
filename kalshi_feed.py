#!/usr/bin/env python3
"""
kalshi_feed.py — read-only Kalshi market ingester + Poly↔Kalshi arb finder.

Kalshi market data is PUBLIC (no key). Same fail-soft pattern as hermes/gdelt.
Fetches open Kalshi markets, then matches them to live Polymarket questions by
title-word overlap and reports cross-market price DIVERGENCE — the structural
mispricing poly-kalshi-arb chases. This is the data feed a real cross-market
arb leg needs; until it exists, run standalone to see if the edge is real.

PAPER/research only — never trades, never keys, never funds.

  python3 kalshi_feed.py             # fetch + write kalshi_markets.json + show top divergences
  python3 kalshi_feed.py --cache     # just refresh the cache (for launchd)
"""
import json, re, sys, time, urllib.request
from pathlib import Path

try:
    from market_match import similarity as mm_sim, categorize as mm_cat
except Exception:                      # fail-soft: token-overlap still works alone
    mm_sim = None
    mm_cat = None

# fuzzy-match confirmation gate (market_match): token overlap + qtype agreement
# can still pair unrelated questions that share generic entities; requiring a
# minimum fuzzy similarity AND same category cuts those false positives.
MM_MIN_SIM = 0.40

BASE = Path(__file__).resolve().parent
CACHE = BASE / "kalshi_markets.json"
KALSHI = "https://api.elections.kalshi.com/trade-api/v2/markets"
GAMMA = "https://gamma-api.polymarket.com/markets"
MIN_OVERLAP = 2          # entity-token overlap (after stripping generic domain words)
MIN_DIVERGENCE = 0.08   # |Kalshi_yes - Poly_yes| to flag
STOP = set("the a an of to in on for and or is are was will be at by with from as it "
           "this that has have new will above below or vs".split())
# generic domain words that create false matches across DIFFERENT races/events —
# overlap on these alone is not an entity match. The real entity (a person/country/
# team name) must be shared, not just "presidential election win".
GENERIC = set("presidential election win winner elected nominee nomination primary "
              "governor senate senator house race election2026 election2028 vote "
              "candidate party democratic republican president us usa american national "
              "state federal runoff general by date out before after reach hit above over "
              "under between number average cases percent margin victory "
              "announce announces officially ipo 2025 2026 2027 2028 2029 2030 "
              "lifetime visit pass degrees colonize land human".split())


def toks(s):
    return {w for w in re.findall(r"[a-z0-9']+", str(s).lower()) if len(w) > 2 and w not in STOP}


def qtype(s):
    """Classify the question's semantic type so we only arb like-for-like.
    Same entities + DIFFERENT type (win vs vote% vs margin vs run) is NOT an arb —
    that's the false-positive that sinks naive cross-market matching."""
    t = str(s).lower()
    if "vote percent" in t or "vote %" in t or "% of the vote" in t: return "votepct"
    if "margin of victory" in t or "margin" in t: return "margin"
    if "run for" in t or "will run" in t or "announce" in t or "declare" in t: return "run"
    if "how many" in t or "number of" in t or "average number" in t or "at least" in t: return "count"
    if "win" in t or "winner" in t or "elected" in t or "nominee" in t or "nomination" in t: return "win"
    if "before" in t or " by " in t or "reach" in t or "hit " in t or "above" in t or "over " in t: return "threshold"
    return "other"


def political_scope(s):
    """(office, stage) for political questions. Same person at different office
    or stage is NOT the same contract — the false positive that survives token
    overlap + qtype + fuzzy similarity (AOC senate vs AOC president, etc.)."""
    t = str(s).lower()
    office = "pres" if any(x in t for x in ("presidential", "president", "white house")) else \
             "vp" if ("vice president" in t or " vp " in t or "vp nominee" in t) else \
             "senate" if "senate" in t or "senator" in t else \
             "house" if "house " in t or "congress" in t else \
             "gov" if "governor" in t or "gubernatorial" in t else \
             "mayor" if "mayor" in t else "any"
    stage = "nominee" if any(x in t for x in ("nominee", "nomination", "primary")) else \
            "general" if any(x in t for x in ("win the", "election winner", "general election",
                                              "elected", "next president")) else "any"
    return office, stage


def get(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": "kalshi-feed/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


EVENTS = "https://api.elections.kalshi.com/trade-api/v2/events"

def _norm(x):
    if x is None: return None
    x = float(x)
    return x / 100 if x > 1.5 else x   # cents→dollars if needed

def kalshi_markets(pages=8):
    """Use /events?with_nested_markets — the firehose /markets endpoint is
    swamped with MVE parlay bundles (KXMVE*, price 0). Events give clean titles.
    For a single-market event, the event title IS the question. For multi-market
    events (e.g. 'next Pope'), each nested market is an entity with its own price;
    we emit one row per entity, title = '<event> — <entity>'."""
    out, cursor = [], None
    for _ in range(pages):
        url = f"{EVENTS}?limit=200&status=open&with_nested_markets=true"
        if cursor: url += f"&cursor={cursor}"
        try:
            d = get(url)
        except Exception as e:
            print(f"[kalshi] fetch failed: {e}"); break
        for ev in d.get("events", []):
            etitle = ev.get("title", "")
            mk = ev.get("markets", [])
            for m in mk:
                if str(m.get("ticker", "")).startswith("KXMVE"): continue
                mid = None
                yb, ya = _norm(m.get("yes_bid_dollars")), _norm(m.get("yes_ask_dollars"))
                last = _norm(m.get("last_price_dollars"))
                if yb is not None and ya is not None and ya > 0: mid = (yb + ya) / 2
                elif last is not None: mid = last
                if mid is None or not (0.02 <= mid <= 0.98): continue
                ent = m.get("yes_sub_title") or m.get("subtitle") or ""
                title = f"{etitle} — {ent}" if (ent and len(mk) > 1) else etitle
                out.append({"ticker": m.get("ticker"), "title": title,
                            "yes": round(mid, 3),
                            "liq": float(m.get("liquidity_dollars") or 0)})
        cursor = d.get("cursor")
        if not cursor: break
    return out


def poly_markets(pages=6):
    out = []
    for p in range(pages):
        try:
            batch = get(f"{GAMMA}?closed=false&active=true&order=volumeNum"
                        f"&ascending=false&limit=100&offset={p*100}")
        except Exception:
            break
        if not batch: break
        for m in batch:
            try:
                yes = float(json.loads(m.get("outcomePrices", "[]"))[0])
                vol = float(m.get("volumeNum") or 0)
            except Exception:
                continue
            if vol < 20_000 or not (0.02 <= yes <= 0.98): continue
            out.append({"q": m.get("question", ""), "yes": yes, "vol": vol})
        if len(batch) < 100: break
    return out


def main():
    cache_only = "--cache" in sys.argv
    kms = kalshi_markets()
    CACHE.write_text(json.dumps({"ts": time.time(), "markets": kms}))
    print(f"[kalshi] cached {len(kms)} open markets")
    if cache_only:
        return
    pms = poly_markets()
    print(f"[poly] {len(pms)} live markets")
    # match by word overlap, report divergences
    # entity tokens = meaningful words MINUS generic domain words (the actual names)
    pm_tok = [(m, toks(m["q"]) - GENERIC) for m in pms]
    hits = []
    for k in kms:
        kt = toks(k["title"]) - GENERIC
        if len(kt) < MIN_OVERLAP: continue
        best, bestm, shared = 0, None, set()
        for m, mt in pm_tok:
            ov = kt & mt
            if len(ov) > best:
                best, bestm, shared = len(ov), m, ov
        if best >= MIN_OVERLAP and bestm:
            # SEMANTIC GATE: same TYPE of question (win vs vote% vs margin vs run)
            kt_type, pt_type = qtype(k["title"]), qtype(bestm["q"])
            if kt_type != pt_type or kt_type == "other":
                continue
            # FUZZY CONFIRMATION GATE (market_match): same category + min similarity.
            # Kills "shared generic tokens, different prediction" false pairs.
            sim = 1.0
            if mm_sim is not None:
                if mm_cat(k["title"]) != mm_cat(bestm["q"]):
                    continue
                sim = mm_sim(k["title"], bestm["q"])
                if sim < MM_MIN_SIM:
                    continue
                if mm_cat(k["title"]) == "politics":
                    ko, ks = political_scope(k["title"])
                    po, ps = political_scope(bestm["q"])
                    if (ko != "any" and po != "any" and ko != po) or \
                       (ks != "any" and ps != "any" and ks != ps):
                        continue   # same person, different office/stage = not an arb
            div = abs(k["yes"] - bestm["yes"])
            if div >= MIN_DIVERGENCE:
                hits.append((div, k, bestm, best, kt_type, shared, sim))
    hits.sort(key=lambda x: x[0], reverse=True)
    print(f"\n🔀 Poly↔Kalshi divergences (>= {MIN_DIVERGENCE}):  {len(hits)} found")
    for div, k, m, ov, qt, shared, sim in hits[:12]:
        edge = "buy Poly YES" if k["yes"] > m["yes"] else "buy Poly NO"
        print(f"  Δ{div:.2f}  K={k['yes']:.2f} P={m['yes']:.2f}  sim={sim:.2f}  [{edge}]  "
              f"type={qt} on={sorted(shared)}")
        print(f"     K: {k['title'][:58]}")
        print(f"     P: {m['q'][:58]}")


if __name__ == "__main__":
    main()
