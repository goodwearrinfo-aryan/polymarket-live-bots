#!/usr/bin/env python3
"""
analyst_hunt.py — REAL-DATA candidate surfacer for the analyst track.

Pulls LIVE Polymarket markets (gamma-api, keyless, read-only), filters for
research-worthiness, dedups against BOTH analyst books, and writes a review
queue (analyst_candidates.json). It does NOT auto-deploy and does NOT invent
theses — a human (or the daily edge-analyst task) attaches a real thesis and
runs judge_panel.py before anything enters a live book.

Why a queue, not auto-deploy:
  The analyst track exists to measure whether HUMAN-researched, judge-gated
  theses have an information edge (>=30 closed exits + bootstrap CI>0). Auto-
  deploying machine-picked markets would just recreate the algo null and
  contaminate that statistic. So the hunt surfaces; humans decide.

Runs every 6h. Surfaces up to TOP_N fresh candidates per cycle.

  python3 analyst_hunt.py            # run one discovery cycle
  python3 analyst_hunt.py --show     # print current candidate queue
"""

import json, os, sys, urllib.request, urllib.parse
from datetime import datetime, timezone

BASE = os.path.dirname(os.path.abspath(__file__))
GAMMA = "https://gamma-api.polymarket.com/markets"
BOOK1 = os.path.join(BASE, "analyst_positions.json")   # analyst_book.py
BOOK2 = os.path.join(BASE, "analyst.json")             # this session
QUEUE = os.path.join(BASE, "analyst_candidates.json")  # review queue (output)

# ---- research-worthiness filters -------------------------------------------
YES_BAND      = (0.15, 0.85)   # genuine uncertainty; skip near-certain mids
MIN_VOLUME    = 50_000         # liquid enough to be a real market
MIN_LIQUIDITY = 5_000          # tradable depth
DAYS_MIN      = 1              # resolves soon enough to be event-driven
DAYS_MAX      = 120            # not a multi-year macro bet
TOP_N         = 8             # cap fresh candidates per cycle (no flooding)
SCAN_LIMIT    = 500          # how many live markets to pull (paginated)


def _norm(q):
    """Normalize a question for fuzzy dedup."""
    return "".join(c for c in (q or "").lower() if c.isalnum())[:60]


def fetch_live_markets(limit=SCAN_LIMIT):
    """Pull active, open markets from gamma-api (keyless). Paginated by offset."""
    out, offset, page = [], 0, 100
    while len(out) < limit:
        params = urllib.parse.urlencode({
            "active": "true", "closed": "false",
            "order": "volume", "ascending": "false",
            "limit": page, "offset": offset,
        })
        try:
            req = urllib.request.Request(f"{GAMMA}?{params}",
                                         headers={"User-Agent": "analyst-hunt/2.0"})
            d = json.loads(urllib.request.urlopen(req, timeout=15).read())
        except Exception as e:
            print(f"  [warn] gamma fetch failed at offset {offset}: {e}")
            break
        batch = d if isinstance(d, list) else d.get("data", [])
        if not batch:
            break
        out.extend(batch)
        offset += page
    return out[:limit]


def load_seen():
    """conditionIds + normalized questions already in either book or queued."""
    seen_ids, seen_q = set(), set()
    # Book 1
    if os.path.exists(BOOK1):
        try:
            b1 = json.load(open(BOOK1))
            for p in b1.get("positions", []):
                if p.get("market_id"): seen_ids.add(str(p["market_id"]))
                seen_q.add(_norm(p.get("q") or p.get("question")))
        except Exception:
            pass
    # Book 2
    if os.path.exists(BOOK2):
        try:
            b2 = json.load(open(BOOK2))
            for bucket in ("open", "closed"):
                for p in b2.get(bucket, []):
                    if p.get("id"): seen_ids.add(str(p["id"]))
                    seen_q.add(_norm(p.get("q")))
        except Exception:
            pass
    # Already-queued candidates (don't re-surface every 6h)
    prev = []
    if os.path.exists(QUEUE):
        try:
            prev = json.load(open(QUEUE)).get("candidates", [])
            for c in prev:
                if c.get("market_id"): seen_ids.add(str(c["market_id"]))
                seen_q.add(_norm(c.get("question")))
        except Exception:
            pass
    return seen_ids, seen_q, prev


def categorize(q):
    ql = (q or "").lower()
    if any(k in ql for k in ("bitcoin", "btc", "eth", "crypto", "solana", "token")):
        return "crypto"
    if any(k in ql for k in ("election", "president", "senate", "congress", "vote")):
        return "politics"
    if any(k in ql for k in ("war", "ceasefire", "iran", "israel", "russia", "ukraine", "china", "taiwan")):
        return "geopolitics"
    if any(k in ql for k in ("fed", "rate", "cpi", "inflation", "gdp", "unemployment", "recession")):
        return "macro"
    if any(k in ql for k in ("nba", "nfl", "ufc", "match", "win the", "champion", "cup")):
        return "sports"
    return "other"


def score_candidate(yes, volume, days):
    """Research-worthiness: reward mid-band uncertainty, volume, near resolution."""
    mid_dist = 1.0 - abs(yes - 0.5) * 2          # 1.0 at 0.50, 0.0 at 0/1
    vol_score = min(volume / 1_000_000, 1.0)     # caps at $1M
    time_score = max(0.0, 1.0 - days / DAYS_MAX) # sooner = higher
    return round(0.5 * mid_dist + 0.3 * vol_score + 0.2 * time_score, 4)


def discover():
    seen_ids, seen_q, prev = load_seen()
    markets = fetch_live_markets()
    print(f"  [scan] pulled {len(markets)} live markets from gamma-api")

    now = datetime.now(timezone.utc)
    fresh = []
    for m in markets:
        cid = str(m.get("conditionId") or m.get("id") or "")
        q = m.get("question", "")
        if not cid or not q:
            continue
        if cid in seen_ids or _norm(q) in seen_q:
            continue
        # price band
        try:
            yes = float(json.loads(m["outcomePrices"])[0])
        except Exception:
            continue
        if not (YES_BAND[0] <= yes <= YES_BAND[1]):
            continue
        # liquidity / volume
        try:
            vol = float(m.get("volume") or 0)
            liq = float(m.get("liquidity") or 0)
        except Exception:
            continue
        if vol < MIN_VOLUME or liq < MIN_LIQUIDITY:
            continue
        # resolution window
        end = m.get("endDate") or m.get("end_date_iso")
        if not end:
            continue
        try:
            end_dt = datetime.fromisoformat(end.replace("Z", "+00:00"))
            days = (end_dt - now).days
        except Exception:
            continue
        if not (DAYS_MIN <= days <= DAYS_MAX):
            continue

        fresh.append({
            "market_id": cid,
            "question": q,
            "yes_price": round(yes, 3),
            "volume": round(vol),
            "liquidity": round(liq),
            "end_date": end,
            "days_to_resolve": days,
            "category": categorize(q),
            "score": score_candidate(yes, vol, days),
            "surfaced_at": now.isoformat(),
            "research_status": "needs_thesis",
            "note": "Human: research, write thesis + conviction, run judge_panel.py before deploying.",
        })

    fresh.sort(key=lambda c: c["score"], reverse=True)
    return fresh[:TOP_N], prev


def run():
    print(f"[analyst_hunt] real-data discovery cycle {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}")
    fresh, prev = discover()
    if not fresh:
        print("[analyst_hunt] no new research-worthy candidates this cycle")
        _persist(prev, added=0)
        return

    print(f"[analyst_hunt] {len(fresh)} new candidates surfaced:")
    for c in fresh:
        print(f"    [{c['score']:.2f}] {c['category']:11s} YES={c['yes_price']:.2f} "
              f"vol=${c['volume']:>9,} {c['days_to_resolve']:>3}d  {c['question'][:46]}")

    # prepend fresh to queue (newest first), cap total stored at 50
    combined = (fresh + prev)[:50]
    _persist(combined, added=len(fresh))


def _persist(candidates, added):
    state = {
        "candidates": candidates,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "pending_review": len([c for c in candidates if c.get("research_status") == "needs_thesis"]),
        "note": "REVIEW QUEUE — not live bets. Attach thesis + run judge_panel.py to deploy.",
    }
    with open(QUEUE, "w") as f:
        json.dump(state, f, indent=2)
    print(f"[analyst_hunt] queue: {len(candidates)} total, +{added} new "
          f"({state['pending_review']} pending review) -> {QUEUE}")


def show():
    if not os.path.exists(QUEUE):
        print("no candidate queue yet — run `python3 analyst_hunt.py` first")
        return
    s = json.load(open(QUEUE))
    print(f"\nANALYST CANDIDATE QUEUE — {s.get('pending_review',0)} pending "
          f"(updated {s.get('updated_at','?')})")
    print("=" * 78)
    for c in s.get("candidates", []):
        print(f"  [{c.get('score',0):.2f}] {c.get('category','?'):11s} "
              f"YES={c.get('yes_price',0):.2f} {c.get('days_to_resolve','?'):>3}d  "
              f"{c.get('question','')[:50]}")


if __name__ == "__main__":
    if "--show" in sys.argv:
        show()
    else:
        run()
