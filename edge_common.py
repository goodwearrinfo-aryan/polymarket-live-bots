"""
edge_common.py — shared keyless fetch helpers for the 5 edge scanners.

Read-only. No keys, no funds, no Postgres. Pure HTTP against public APIs:
  - Polymarket Gamma (markets, grouped events)
  - Polymarket CLOB (live book)
  - Kalshi public events
Each scanner imports from here so the probes stay DRY but independent.
"""
import json, urllib.request, urllib.parse, time, re

UA = {"User-Agent": "edge-research/1.0"}
GAMMA = "https://gamma-api.polymarket.com/markets"
GAMMA_EVENTS = "https://gamma-api.polymarket.com/events"
KALSHI_EVENTS = "https://api.elections.kalshi.com/trade-api/v2/events"


def _get(url, params=None, tries=3, timeout=20):
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            return json.load(urllib.request.urlopen(req, timeout=timeout))
        except Exception as e:
            if i == tries - 1:
                return None
            time.sleep(1.5 * (i + 1))
    return None


def poly_markets(pages=8, per_page=100, min_vol=0):
    """Open binary markets, most-liquid first. Returns dicts with yes/bid/ask/end/vol."""
    out, seen = [], set()
    for p in range(pages):
        raw = _get(GAMMA, {
            "closed": "false", "active": "true",
            "order": "volumeNum", "ascending": "false",
            "limit": per_page, "offset": p * per_page,
        }) or []
        if not raw:
            break
        for m in raw:
            try:
                cid = str(m.get("conditionId") or m.get("id"))
                if cid in seen:
                    continue
                prices = m.get("outcomePrices")
                if isinstance(prices, str):
                    prices = json.loads(prices)
                if not prices or len(prices) < 2:
                    continue
                vol = float(m.get("volumeNum") or m.get("volume") or 0)
                if vol < min_vol:
                    continue
                seen.add(cid)
                bid = m.get("bestBid"); ask = m.get("bestAsk")
                out.append({
                    "id": cid,
                    "q": (m.get("question") or "")[:120],
                    "yes": float(prices[0]),
                    "no": float(prices[1]),
                    "vol": vol,
                    "end": m.get("endDate"),
                    "start": m.get("gameStartTime"),
                    "liquidity": float(m.get("liquidityNum") or m.get("liquidity") or 0),
                    "bid": (float(bid) if bid not in (None, "") else None),
                    "ask": (float(ask) if ask not in (None, "") else None),
                    "slug": m.get("slug") or "",
                })
            except Exception:
                continue
        if len(raw) < per_page:
            break
    return out


def poly_events(pages=8, per_page=100):
    """Grouped events with nested markets — for basket/combinatorial arb."""
    out = []
    for p in range(pages):
        raw = _get(GAMMA_EVENTS, {
            "closed": "false", "active": "true",
            "limit": per_page, "offset": p * per_page,
        }) or []
        if not raw:
            break
        for ev in raw:
            mkts = ev.get("markets") or []
            rows = []
            for m in mkts:
                try:
                    prices = m.get("outcomePrices")
                    if isinstance(prices, str):
                        prices = json.loads(prices)
                    if not prices or len(prices) < 2:
                        continue
                    toks = m.get("clobTokenIds")
                    if isinstance(toks, str):
                        toks = json.loads(toks)
                    rows.append({
                        "q": (m.get("question") or "")[:100],
                        "yes": float(prices[0]),
                        "no": float(prices[1]),
                        "bid": (float(m["bestBid"]) if m.get("bestBid") not in (None, "") else None),
                        "ask": (float(m["bestAsk"]) if m.get("bestAsk") not in (None, "") else None),
                        "vol": float(m.get("volumeNum") or 0),
                        "yes_token": (str(toks[0]) if toks else None),
                        "no_token": (str(toks[1]) if toks and len(toks) > 1 else None),
                        "condition_id": str(m.get("conditionId") or m.get("id") or ""),
                    })
                except Exception:
                    continue
            if rows:
                # n_markets_total = the FULL live outcome set of the negRisk pool, including
                # inactive/unpriced placeholder legs (e.g. "Person A".."Other") that have no
                # outcomePrices and are dropped from `rows`. Excludes only CLOSED (resolved/
                # withdrawn) sub-markets. A basket is a real lock only if rows covers ALL of
                # these — else the priced legs are a partial field (a directional bet, not a
                # lock). Consumed by basket_arb.scan_baskets()'s completeness check.
                n_total = sum(1 for m in mkts if not m.get("closed"))
                out.append({
                    "title": (ev.get("title") or "")[:120],
                    "slug": ev.get("slug") or "",
                    "markets": rows,
                    "n_markets_total": n_total,
                    "mutually_exclusive": bool(ev.get("negRisk")),  # negRisk = mutually-exclusive multi-outcome
                })
        if len(raw) < per_page:
            break
    return out


def kalshi_markets(pages=8):
    """Open Kalshi markets (entity-level), mid price + liquidity."""
    out, cursor = [], None
    for _ in range(pages):
        url = f"{KALSHI_EVENTS}?limit=200&status=open&with_nested_markets=true"
        if cursor:
            url += f"&cursor={cursor}"
        d = _get(url)
        if not d:
            break
        for ev in d.get("events", []):
            etitle = ev.get("title", "")
            mk = ev.get("markets", [])
            for m in mk:
                if str(m.get("ticker", "")).startswith("KXMVE"):
                    continue
                yb = m.get("yes_bid_dollars"); ya = m.get("yes_ask_dollars")
                last = m.get("last_price_dollars")
                yb = float(yb) if yb not in (None, "") else None
                ya = float(ya) if ya not in (None, "") else None
                last = float(last) if last not in (None, "") else None
                mid = None
                if yb is not None and ya is not None and ya > 0:
                    mid = (yb + ya) / 2
                elif last is not None:
                    mid = last
                if mid is None or not (0.02 <= mid <= 0.98):
                    continue
                ent = m.get("yes_sub_title") or m.get("subtitle") or ""
                title = f"{etitle} — {ent}" if (ent and len(mk) > 1) else etitle
                out.append({"ticker": m.get("ticker"), "title": title,
                            "yes": round(mid, 3),
                            "liq": float(m.get("liquidity_dollars") or 0)})
        cursor = d.get("cursor")
        if not cursor:
            break
    return out


# --- fuzzy title matching for cross-venue ---
_STOP = set("will the a an of to in on at by for and or is be win 2026 2025".split())

def tokens(s):
    return set(w for w in re.findall(r"[a-z0-9]+", (s or "").lower()) if w not in _STOP and len(w) > 2)

def jaccard(a, b):
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)
