#!/usr/bin/env python3
"""
analyst_data_gate.py — v6 of the analyst track.

THE LESSON (BRAIN, 2026-06-16): two analyst runs, 23 markets, 0 booked under the
strict argument-only panel. The ONLY confirmed edge (Hormuz NO) came from a HARD-DATA
resolution check, not narrative. So v5 inverts the funnel: a market earns analysis ONLY
if its resolving series is FETCHABLE, and we pull the actual data FIRST. Everything else
is deferred to the structural (basket-arb) track.

v6 ADDS (2026-06-16):
  - fetch_fed(): NY Fed TGCR daily (EFFR proxy ±5bp); infers target range + counts 2026 cuts
  - fetch_lmarena(): keyless via mathewhe/chatbot-arena-elo (elo.csv) + 21d freshness guard
  - stale_warn in fetch_portwatch(): flags data >10d old (catches Hormuz-deal scenario)
  - DECAY + STALE alerts in run_once() — not just ARB
  - Classify() returns richer params for fed markets (_parse_fed_params)

Implemented sources (fetchable today):
  - crypto-threshold  -> Binance/ccxt klines (BTC/ETH/SOL/XRP/DOGE "dip to / reach $X")
  - portwatch         -> IMF PortWatch ArcGIS (Strait of Hormuz + other chokepoints, 7d-MA)
  - fed               -> NY Fed TGCR (EFFR proxy; target range inferred to ±12.5bp)
  - lmarena           -> mathewhe/chatbot-arena-elo elo.csv (keyless); auto-dark if >21d stale.
                         NOTE: that mirror froze 2025-07-18, so the driver is currently dark
                         (no live keyless arena source exists; official API is Cloudflare-walled).
                         Fetcher+dossier are correct and auto-revive if the mirror resumes.
Stubbed (recognized but not fetchable without auth or browser):
  - bls: CPI/jobs — BLS API available but not wired yet
  - espn: sports — sports_data.py exists but gate integration pending

Run:  python3 analyst_data_gate.py             # scan live feed, print dossiers
      python3 analyst_data_gate.py --json      # machine-readable (feeds workflow)
      python3 analyst_data_gate.py once        # watchdog mode (logs + alerts)
      python3 analyst_data_gate.py --min-vol 50000
"""
import sys, re, json, csv, io, datetime as _dt, urllib.request, urllib.parse

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
import edge_common  # poly_markets, _get

# Price-sanity floor for "data resolves-YES" arb signals (added 2026-06-18). If the resolving
# DATA says a market is already settled-YES but the book still prices YES BELOW this floor, the
# market is on the NO side of 50/50 — it disagrees with our read by >50pts, so the read is the
# suspect, not the price (the nearres lesson: when model and market diverge wildly, trust the
# market). Below the floor a "resolves-YES" signal is SUPPRESSED, never emitted. This caught the
# BTC "$72,500 in June" @0.065 false positive — a UTC-vs-ET month-boundary daily-candle artifact.
ARB_SANITY_FLOOR = 0.50

# ─── SOURCE CLASSIFICATION ────────────────────────────────────────────────────

_ASSET = {
    "bitcoin": "BTC", "btc": "BTC",
    "ethereum": "ETH", "eth": "ETH",
    "solana": "SOL", "sol": "SOL",
    "xrp": "XRP", "ripple": "XRP",
    "dogecoin": "DOGE", "doge": "DOGE",
}
_UP_WORDS = ("reach", "hit", "above", "exceed", "surpass", "over", "top", ">=", "all-time high", "ath")
_DOWN_WORDS = ("dip", "drop", "fall", "below", "under", "down to", "crash", "<=")


def _parse_fed_params(q):
    """Extract structured Fed-market params from the question text."""
    ql = q.lower()
    # "no rate cuts" / "no change"
    if re.search(r"\bno\b.{0,20}(?:rate\s+)?cuts?|no\s+change", ql):
        return {"action": "cuts_count", "count": 0}
    # "increase/decrease by N+ bps" (handles "50+" notation)
    m = re.search(r"(increase|decrease|cut|hike|raise|lower).*?(\d+(?:\.\d+)?)\+?\s*(?:basis points?|bps?|bp)", ql)
    if m:
        action = "increase" if m.group(1) in ("increase", "hike", "raise") else "decrease"
        return {"action": action, "bps": float(m.group(2))}
    # "rate hike" / "hike(s) rate(s)" / "raise(s) rate(s)" / "rate increase" (any hike)
    if re.search(r"\b(rate\s+hikes?|hikes?\s+(?:interest\s+)?rates?|raises?\s+(?:interest\s+)?rates?|rate\s+increase)\b", ql):
        return {"action": "increase", "bps": 25}  # any hike = at least 25bp
    # "N or more rate cuts" (e.g. "12 or more Fed rate cuts") — same >= semantics as count=N.
    # Must precede the plain "N rate cuts" pattern, which can't span the "or more" gap.
    m = re.search(r"(\d+)\s+or\s+more\s+(?:fed\s+)?rate\s+cuts?", ql)
    if m:
        return {"action": "cuts_count", "count": int(m.group(1)), "ge": True}
    # "N rate cuts"
    m = re.search(r"(\d+)\s*(?:fed\s+)?rate\s+cuts?", ql)
    if m:
        return {"action": "cuts_count", "count": int(m.group(1))}
    m = re.search(r"rate\s+cuts?.*?(\d+)\s*time", ql)
    if m:
        return {"action": "cuts_count", "count": int(m.group(1))}
    # Bare binary "(Fed) rate cut by <date>?" with no count/size → "≥1 cut" = cuts_count 1.
    # Mirrors the rate-hike rule above. Placed AFTER the counted-cut patterns so "3 rate
    # cuts" still parses as count=3 and "no rate cuts" as count=0 (both return earlier).
    if re.search(r"\brate\s+cuts?\b|\bcut\s+(?:interest\s+)?rates?\b", ql):
        return {"action": "cuts_count", "count": 1, "ge": True}
    # "upper bound … X%"
    m = re.search(r"(?:upper\s+bound|target|federal\s+funds).*?(\d+(?:\.\d+)?)\s*%", ql)
    if m:
        return {"action": "target_level", "rate": float(m.group(1))}
    return {"action": "unknown"}


def classify(q):
    """Return (source, params) or (None, None)."""
    ql = q.lower()
    # crypto price threshold
    asset = next((v for k, v in _ASSET.items() if re.search(rf"\b{k}\b", ql)), None)
    money = re.search(r"\$\s?([\d,]+(?:\.\d+)?)\s?(k|m)?", ql)
    if asset and money:
        amt = float(money.group(1).replace(",", ""))
        if money.group(2) == "k":
            amt *= 1_000
        elif money.group(2) == "m":
            amt *= 1_000_000
        up = any(w in ql for w in _UP_WORDS)
        down = any(w in ql for w in _DOWN_WORDS)
        if up == down:
            direction = "up" if up else ("down" if down else None)
        else:
            direction = "up" if up else "down"
        # NON-TOUCH resolution mechanics must NOT use the touch-based already_triggered
        # logic (in-window max/min crossing). They produced ~20 false ARB flags in the
        # full-gamma sweep (2026-06-17):
        #   • SNAPSHOT ("be above/below $X ON <date>") resolves on the price reading AT
        #     that date, not on any touch — current/max ≥ threshold ≠ resolved YES (one
        #     was flagged triggered while current sat BELOW the threshold).
        #   • RACE ("$A or $B first") needs two-threshold race logic, not a single touch.
        # Only "reach/hit/dip to $X in/by <period>" touch markets are settled-arb candidates.
        # ...EXCEPT a "be above $X AT ANY POINT / ever / at some point" market is still a
        # TOUCH (settles on a touch, not a date snapshot) — don't over-exclude it just
        # because it says "be above". Touch-window phrasing overrides the snapshot guard.
        is_touch_window = bool(re.search(r"\bat any (?:point|time)\b|\banytime\b|\bever\b|\bat some point\b", ql))
        is_snapshot = (not is_touch_window) and bool(
            re.search(r"\bbe\s+(?:above|below|over|under|at)\b", ql)
            or re.search(r"\bon\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)", ql))
        is_race = ("first" in ql) and len(re.findall(r"\$\s?[\d,]+", ql)) >= 2
        if is_snapshot or is_race:
            return None, None
        if direction:
            return "crypto-threshold", {"asset": asset, "threshold": amt, "direction": direction}
    # Commodity price-touch (oil/gold) — SAME touch mechanism as crypto, keyless Yahoo OHLC.
    # Markets: "Will Crude Oil (CL) hit (HIGH) $200 by end of June?" / "...Gold (GC) ...(LOW) $70...".
    # Direction comes from the explicit (HIGH)/(LOW) marker, NOT the verb — "hit" is an up-word so a
    # "(LOW)" dip-to market would misread as up without this. Require the (CL)/(GC) ticker so a
    # generic "gold medal" market can't false-match.
    _comm = "oil" if "(cl)" in ql else ("gold" if "(gc)" in ql else None)
    if _comm:
        cmoney = re.search(r"\$\s?([\d,]+(?:\.\d+)?)\s?(k|m)?", ql)
        if cmoney:
            camt = float(cmoney.group(1).replace(",", ""))
            if cmoney.group(2) == "k":
                camt *= 1_000
            elif cmoney.group(2) == "m":
                camt *= 1_000_000
            cdir = ("down" if "(low)" in ql else "up" if "(high)" in ql else
                    ("down" if any(w in ql for w in _DOWN_WORDS) else
                     "up" if any(w in ql for w in _UP_WORDS) else None))
            c_snapshot = (bool(re.search(r"\bbe\s+(?:above|below|over|under|at)\b", ql)
                               or re.search(r"\bon\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)", ql))
                          and not re.search(r"\bat any (?:point|time)\b|\banytime\b|\bever\b", ql))
            c_race = ("first" in ql) and len(re.findall(r"\$\s?[\d,]+", ql)) >= 2
            if cdir and not (c_snapshot or c_race):
                return "commodity", {"asset": _comm, "threshold": camt, "direction": cdir}
    # IMF PortWatch chokepoints
    if any(k in ql for k in ("strait of hormuz", "suez", "panama canal", "bab-el-mandeb",
                             "bosphorus", "malacca", "chokepoint", "transit calls")):
        port = None
        for name in ("Strait of Hormuz", "Suez Canal", "Panama Canal", "Bab el-Mandeb Strait",
                     "Bosphorus Strait", "Strait of Malacca"):
            if name.lower().split(" of ")[-1] in ql or name.lower() in ql:
                port = name
                break
        # Two resolution metrics on PortWatch chokepoint markets:
        #   Series A "Will N ships transit ... on any day" → resolves on the RAW DAILY
        #            count (a single finalized day ≥ N triggers YES).  metric=daily.
        #   Series B "traffic returns to normal"           → resolves on PortWatch's
        #            published 7-day MOVING AVERAGE ≥ 60.              metric=ma7.
        # Mapping every market to the 7-day MA (the old behavior) MISREADS Series A: a
        # market that already resolved YES on a single-day spike looks "un-triggered"
        # because the smoothed MA sits below the threshold (the Hormuz 20-ship case).
        nm = re.search(r"(\d+)\s*ships?\b", ql)
        # ma_resolution: True ONLY for genuine "returns to normal" MA markets. A Hormuz-
        # keyword market with no ship count (e.g. "Will Trump agree to Iranian transit
        # fees...") falls into ma7 for DATA DISPLAY, but its resolution is NOT the MA —
        # decided_no/impossibility must never fire on it (resolution-identity mismatch,
        # the #1 leg killer).
        if "return" in ql and "normal" in ql:
            metric, threshold, ma_res = "ma7", 60, True
        elif not nm:
            metric, threshold, ma_res = "ma7", 60, False
        else:
            metric, threshold, ma_res = "daily", int(nm.group(1)), False
        return "portwatch", {"port": port or "Strait of Hormuz",
                             "metric": metric, "threshold": threshold,
                             "ma_resolution": ma_res}
    # Fed / FOMC
    if re.search(r"\bfed\b|fomc|rate (cut|hike)|federal funds", ql):
        return "fed", _parse_fed_params(q)
    # LMArena — keyless via mathewhe/chatbot-arena-elo (fetch_lmarena)
    if ("lmarena" in ql or "best ai model" in ql or "chatbot arena" in ql
            or "#1 ai model" in ql or "top ai model" in ql):
        provider = next((name for kw, name in (
            ("google", "Google"), ("gemini", "Google"), ("openai", "OpenAI"),
            ("chatgpt", "OpenAI"), ("gpt", "OpenAI"), ("anthropic", "Anthropic"),
            ("claude", "Anthropic"), ("meta", "Meta"), ("llama", "Meta"),
            ("mistral", "Mistral"), ("deepseek", "DeepSeek"),
            ("alibaba", "Alibaba"), ("qwen", "Alibaba"),
            ("xai", "xAI"), ("x.ai", "xAI"), ("grok", "xAI"))
            if kw in ql), None)
        return "lmarena", {"provider": provider}
    # BLS (CPI / unemployment / nonfarm payrolls) — the BLS series IS the named resolver on
    # these macro markets (low resolution-identity risk). _parse_bls_params encodes the
    # release-calendar + prelim-revision guards; returns None when the question is unmappable
    # (e.g. no parseable threshold/direction) so we don't fabricate a signal.
    if re.search(r"\bcpi\b|inflation|jobs report|unemployment|nonfarm|payrolls", ql):
        p = _parse_bls_params(q)
        if p:
            return "bls", p
    # Launch-count markets ("SpaceX {N}+ launches in {yr}") — resolve on the orbital-launch
    # count, fetchable keyless via thespacedevs. Identity is MODERATE (trackers differ on what
    # counts as a launch) → fetch_launches requires count >= threshold + buffer before YES.
    # Only route a NAMED provider (SpaceX) — a generic "launches" market is too identity-risky.
    if re.search(r"\blaunch(?:es)?\b", ql) and re.search(r"\b20\d\d\b", ql) and "spacex" in ql:
        m = re.search(r"(\d+)\s*(?:or more|or above|\+|launches)", ql)
        if m:
            return "launches", {"provider": "SpaceX", "threshold": int(m.group(1)),
                                "year": int(re.search(r"\b(20\d\d)\b", ql).group(1))}
    # Recession — markets that resolve on the MECHANICAL "2 consecutive negative real-GDP
    # quarters" rule (BEA series, fetchable keyless via FRED). Identity guard lives in the
    # fetcher: it reads the description and SKIPS if the market isn't the 2-quarter rule (an
    # NBER-judgment recession is NOT this series).
    if "recession" in ql and re.search(r"\b20\d\d\b", ql):
        return "fred_recession", {"year": int(re.search(r"\b(20\d\d)\b", ql).group(1))}
    # USGS earthquakes — count/threshold markets ("M{X}+ before {yr}", "between A and B M7+
    # in {yr}") resolve on USGS, the canonical resolver (low resolution-identity risk, like
    # BLS). GUARD: exclude the MLS team "San Jose Earthquakes" and any sports phrasing so a
    # team market can't false-match. _parse_usgs_params returns None when unmappable.
    if ("earthquake" in ql or re.search(r"magnitude\s*\d", ql)) and not any(
            s in ql for s in ("mls", "cup", "win the", "san jose", "soccer", "playoff")):
        p = _parse_usgs_params(q)
        if p:
            return "usgs", p
    # ESPN sports — ATTEMPTED 2026-06-17, then DISABLED as unsafe. Even with futures excluded +
    # both-teams-on-opposite-sides required, the matcher mis-fires on RECURRING matchups: two
    # teams play many times a season, so name-only matching can tag the WRONG (past) game as a
    # market's result (a mid-price "decided" signal = proof of mismatch). Doing it safely needs
    # DATE disambiguation (market end-date ≈ ESPN game date), and Polymarket sports resolve too
    # fast for meaningful lag-arb anyway → not worth feeding the leg false positives. fetch_espn
    # + helpers are kept for a future date-aware version; classify intentionally does NOT route
    # to "espn" so nothing reaches the leg. Re-enable only with a date match + price-sanity gate.
    return None, None


# ─── DATA FETCHERS ────────────────────────────────────────────────────────────

def _market_start(cid):
    """Pull the market's real startDate from gamma to bound the price window.
    Uses createdAt for markets that say 'from creation of this market' to avoid
    pre-creation price leakage (the false-ARB bug fixed 2026-06-16)."""
    try:
        raw = edge_common._get(edge_common.GAMMA, {"condition_ids": cid}) or []
        m = raw[0] if isinstance(raw, list) and raw else (raw if isinstance(raw, dict) else {})
        desc = m.get("description") or ""
        if "creation of this market" in desc.lower() or "from the creation" in desc.lower():
            v = m.get("createdAt") or m.get("creationDate")
            if v:
                return v, desc
        for k in ("startDateIso", "startDate", "createdAt", "creationDate"):
            v = m.get(k)
            if v:
                return v[:10], desc
        return None, desc
    except Exception:
        return None, ""


_CCXT_CACHE = {}

def _get_ccxt(name):
    """Cache ccxt exchange objects module-level. Re-instantiating per call (under the
    800-market scan burst) gives each object a FRESH rate-limit clock, so they don't
    pace together and trip exchange IP throttles -> intermittent ok:False (the symptom
    the data-gate scout misread as a 'broken OKX fetcher' 2026-06-24). One reused
    instance lets enableRateLimit actually pace the burst, and skips re-instantiation."""
    ex = _CCXT_CACHE.get(name)
    if ex is None:
        import ccxt
        ex = getattr(ccxt, name)({"enableRateLimit": True})
        _CCXT_CACHE[name] = ex
    return ex


def fetch_crypto_threshold(asset, threshold, direction, start_iso=None):
    """Has the asset already crossed `threshold` since `start_iso`? + current state."""
    sym = f"{asset}/USDT"
    if start_iso:
        try:
            start_dt = _dt.datetime.fromisoformat(start_iso)
        except Exception:
            start_dt = _dt.datetime(2025, 11, 1)
    else:
        start_dt = _dt.datetime(2025, 11, 1)
    since = int(start_dt.timestamp() * 1000)

    last_err = None
    for exch in ("binance", "kraken", "coinbase", "kucoin", "okx"):
        try:
            ex = _get_ccxt(exch)
            ohlcv = ex.fetch_ohlcv(sym, timeframe="1d", since=since, limit=500)
            if not ohlcv:
                continue
            ohlcv = [c for c in ohlcv if c[0] >= since]
            if not ohlcv:
                continue
            lows = [c[3] for c in ohlcv]
            highs = [c[2] for c in ohlcv]
            closes = [c[4] for c in ohlcv]
            cur = closes[-1]
            realized_min = min(lows)
            realized_max = max(highs)
            if direction == "down":
                triggered = realized_min <= threshold
                extreme = realized_min
                dist_pct = (cur - threshold) / cur * 100
            else:
                triggered = realized_max >= threshold
                extreme = realized_max
                dist_pct = (threshold - cur) / cur * 100
            return {
                "ok": True, "source_exchange": exch, "symbol": sym,
                "current": round(cur, 2), "realized_min": round(realized_min, 2),
                "realized_max": round(realized_max, 2),
                "threshold": threshold, "direction": direction,
                "already_triggered": bool(triggered),
                "extreme_vs_threshold": round(extreme, 2),
                "distance_pct": round(dist_pct, 2),
                "bars": len(ohlcv), "window_start": start_dt.date().isoformat(),
            }
        except Exception as e:
            last_err = f"{exch}: {e}"
            continue
    return {"ok": False, "error": last_err or "all exchanges failed"}


_COMMODITY_SYM = {"oil": "CL=F", "gold": "GC=F"}

def fetch_commodity(asset, threshold, direction, start_iso=None):
    """Current oil/gold state vs `threshold` since `start_iso` — but NEVER a confirmed trigger.

    KEYLESS via the Yahoo chart API (CL=F crude, GC=F gold). UNLIKE fetch_crypto_threshold,
    this canNOT assert already_triggered, and that is deliberate (RESOLUTION-SOURCE MISMATCH,
    found 2026-06-28): these Polymarket markets resolve on the official CME *Active-Month*
    SETTLEMENT price — the description states verbatim "Intraday trades, highs, lows ... do
    not count", and under backwardation the active month settles BELOW spot. Yahoo CL=F/GC=F
    is a CONTINUOUS front-month series whose intraday high/low and last-trade close are NOT
    that settlement. The old max(high)/min(low) touch logic manufactured false triggers that,
    fed to the data-arb leg, would be fake "settled-but-mispriced" edges (the project's #1
    forbidden failure). Evidence (window from each market's startDate):
      • Gold $5,500 HIGH : intraday high 5586 ≥ 5500 → old code said TRIGGERED, but the
        settlement (close) topped out at ~5318 and the book prices YES at 0.0005 (firm NO).
      • Crude $115 HIGH  : high 117.6 ≥ 115, but max close ~112.9; book YES 0.0015 (NO).
      • Crude $105 HIGH  : even the continuous CLOSE 108.7 ≥ 105, yet book YES 0.0015 (NO) —
        the active-month settlement stayed below 105 (oil-spike backwardation + last-trade≠settle).
    So crypto trusts its feed (the exchange OHLC IS the resolver); commodities cannot. We
    report current price + settlement-proxy extremes for context, flag whether the close
    crossed (settle_crossed, INFORMATIONAL only), and hard-set already_triggered=False so no
    downstream consumer ever treats a commodity as a settled-YES off this unconfirmable feed."""
    sym = _COMMODITY_SYM.get(asset)
    if not sym:
        return {"ok": False, "error": f"no commodity symbol for '{asset}'"}
    if start_iso:
        try:
            start_dt = _dt.datetime.fromisoformat(start_iso)
        except Exception:
            start_dt = _dt.datetime(2025, 11, 1)
    else:
        start_dt = _dt.datetime(2025, 11, 1)
    since = int(start_dt.timestamp())                      # Yahoo period1/period2 = epoch SECONDS
    now = int(_dt.datetime.now(_dt.timezone.utc).timestamp())
    try:
        url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(sym)}"
               f"?period1={since}&period2={now}&interval=1d")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        d = json.loads(urllib.request.urlopen(req, timeout=20).read())["chart"]["result"][0]
        qd = d["indicators"]["quote"][0]
        highs = [h for h in qd.get("high", []) if h is not None]
        lows = [l for l in qd.get("low", []) if l is not None]
        closes = [c for c in qd.get("close", []) if c is not None]
    except Exception as e:
        return {"ok": False, "error": f"yahoo {sym} fetch failed: {e}"}
    if not highs or not lows or not closes:
        return {"ok": False, "error": f"yahoo {sym} returned no OHLC in window"}

    cur = closes[-1]
    # Settlement PROXY = daily close (Yahoo last-trade), NOT the CME official settlement — but
    # far closer to it than the intraday high/low, which the resolution rules explicitly exclude.
    settle_min = min(closes)
    settle_max = max(closes)
    if direction == "down":
        settle_crossed = settle_min <= threshold
        extreme = settle_min
        dist_pct = (cur - threshold) / cur * 100
    else:
        settle_crossed = settle_max >= threshold
        extreme = settle_max
        dist_pct = (threshold - cur) / cur * 100
    return {
        "ok": True, "source_exchange": "yahoo", "symbol": sym,
        "current": round(cur, 2), "realized_min": round(settle_min, 2),
        "realized_max": round(settle_max, 2),
        "threshold": threshold, "direction": direction,
        # Yahoo continuous OHLC ≠ CME Active-Month settlement → trigger UNCONFIRMABLE from this
        # feed, so we never claim a settled-YES (avoid manufacturing a fake data-arb edge).
        "already_triggered": False,
        "settle_crossed": bool(settle_crossed),   # close crossed thr — INFORMATIONAL, not a resolution
        "unconfirmable_source": True,
        "note": ("Yahoo CL=F/GC=F continuous-contract close is NOT the CME Active-Month "
                 "settlement these markets resolve on; trigger not confirmable from this "
                 "keyless feed → never a settled-YES."),
        "extreme_vs_threshold": round(extreme, 2),
        "distance_pct": round(dist_pct, 2),
        "bars": len(closes), "window_start": start_dt.date().isoformat(),
    }


_ARCGIS = ("https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services/"
           "Daily_Chokepoints_Data/FeatureServer/0/query")


def fetch_portwatch(port, threshold=60, metric="ma7", end_iso=None, ma_resolution=True):
    """Daily transit calls for an IMF PortWatch chokepoint.

    metric='daily' (Series A "N ships on any day")  → already_triggered when the RAW
        DAILY max in the window ≥ threshold (a single finalized day resolves YES).
    metric='ma7'   (Series B "returns to normal")   → already_triggered when the
        published 7-day MOVING AVERAGE ≥ threshold (60).
    ma7 + ma_resolution=False → the market only KEYWORD-matched a chokepoint (e.g. a
        Hormuz political question) and does NOT resolve on the traffic MA. The MA
        dossier is informational only: already_triggered=None, so no consumer can read
        a trigger off a series the market doesn't settle on (resolution-identity
        mismatch — same guard as fetch_commodity's unconfirmable_source).
    Adds stale_warn if data >10d old — catches regime-change gaps (Hormuz deal lesson).

    ma7 + end_iso → STRUCTURAL-IMPOSSIBILITY check (decided_no): an MA market can be
    decided NO *before* its deadline — with D<7 days left, the deadline MA still contains
    7-D already-published days, so even max-plausible traffic on every remaining day has a
    computable ceiling. Ceiling below threshold → YES is dead while the book may still
    price it (the settled-but-mispriced shape, NO side). Max-plausible daily is GENEROUS
    on purpose (max(2×threshold, 1.5×observed-max)) — a false IMPOSSIBLE is a bad lead,
    a late one just fires a day later. Past-deadline: decided from the published series.
    decided_no feeds find_alerts as a WATCH flag only — NEVER arb_signals (buy-YES-only
    policy stands; this does not trade the NO side)."""
    try:
        params = {
            "where": f"portname='{port}'",
            "outFields": "date,n_total", "orderByFields": "date DESC",
            "resultRecordCount": "60", "f": "json",
        }
        url = _ARCGIS + "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        data = json.loads(urllib.request.urlopen(req, timeout=25).read())
        feats = data.get("features", [])
        rows = []
        for f in feats:
            a = f.get("attributes", {})
            n = a.get("n_total")
            d = a.get("date")
            if n is not None and d is not None:
                ds = (_dt.datetime.utcfromtimestamp(d / 1000).date().isoformat()
                      if isinstance(d, (int, float)) else str(d))
                rows.append((ds, float(n)))
        rows.sort(reverse=True)
        if not rows:
            return {"ok": False, "error": "no PortWatch rows"}
        last7 = [n for _, n in rows[:7]]
        ma7 = sum(last7) / len(last7)
        daily_max = max(n for _, n in rows)
        latest_date = rows[0][0]
        # Staleness: PortWatch lags ~9 days naturally; warn past 10 days
        try:
            days_since = (_dt.date.today() - _dt.date.fromisoformat(latest_date)).days
        except Exception:
            days_since = 0
        stale_warn = (f"data {days_since}d old — check for regime-change events "
                      f"(e.g. ceasefire, sanctions, deal) before trusting gap"
                      if days_since > 10 else None)
        if metric == "daily":
            triggered = daily_max >= threshold
        elif ma_resolution:
            triggered = ma7 >= threshold
        else:
            triggered = None  # market doesn't resolve on the MA — never assert a trigger
        # ── ma7 structural impossibility (decided_no) ──────────────────────────
        decided_no, impossible_note = False, None
        if metric == "ma7" and ma_resolution and not triggered and end_iso:
            try:
                deadline = _dt.date.fromisoformat(str(end_iso)[:10])
                latest = _dt.date.fromisoformat(latest_date)
                # chronological daily values (oldest→newest) for window math
                chron = [n for _, n in sorted(rows)]
                m_cap = max(2 * threshold, 1.5 * daily_max)   # generous max-plausible daily
                if deadline <= latest:
                    # window over & data covers it: decided by the published series alone
                    # (rolling MA over the fetched range; none crossed and none can now)
                    mas = [sum(chron[i - 6:i + 1]) / 7 for i in range(6, len(chron))]
                    # ≥14d of series so "no cross" isn't an artifact of a thin fetch; an
                    # earlier cross would have resolved the market YES already (it's open).
                    if len(chron) >= 14 and mas and max(mas) < threshold:
                        decided_no = True
                        impossible_note = (f"window ended {deadline} (data through {latest}); "
                                           f"peak 7d-MA={max(mas):.1f} < {threshold} → NO decided")
                else:
                    d_left = (deadline - latest).days
                    if d_left < 7:
                        # deadline MA = D unknown days (cap m_cap) + the 7-D newest actuals
                        tail = chron[-(7 - d_left):]
                        best_ma = (sum(tail) + d_left * m_cap) / 7
                        if best_ma < threshold:
                            decided_no = True
                            impossible_note = (f"{d_left}d of data left; even {m_cap:.0f}/day "
                                               f"caps deadline 7d-MA at {best_ma:.1f} < {threshold} "
                                               f"→ YES structurally impossible")
            except Exception:
                pass  # fail-soft: no impossibility claim on any math/date error
        return {
            "ok": True, "port": port, "latest_date": latest_date,
            "metric": metric, "threshold": threshold,
            "already_triggered": None if triggered is None else bool(triggered),
            "ma_resolution": bool(ma_resolution) if metric == "ma7" else None,
            "resolution_note": (None if triggered is not None else
                                "keyword-matched market does NOT resolve on the traffic MA — "
                                "MA dossier informational only, trigger unconfirmable from this series"),
            "decided_no": decided_no, "impossible_note": impossible_note,
            "ma7": round(ma7, 2), "daily_max": round(daily_max, 2),
            "max_30d": round(daily_max, 2),  # back-compat alias = window daily max
            "n_days": len(rows), "data_lag_days": days_since,
            "feed_lag_note": "ArcGIS lags ~9d; fill blind spot with news",
            "stale_warn": stale_warn,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


_NYFED_TGCR = ("https://markets.newyorkfed.org/read?productCode=50"
               "&startDt={start}&endDt={end}&eventCodes=520&format=csv")


def fetch_fed(params):
    """Current Fed target range inferred from NY Fed TGCR daily series (no API key).
    TGCR ≈ EFFR ± 5bp; target range = floor(TGCR / 0.25) * 0.25 to +0.25.
    Counts implied 2026 cuts from total rate change vs Jan 2026 rate."""
    try:
        start = "2025-12-15"
        end = _dt.date.today().isoformat()
        url = _NYFED_TGCR.format(start=start, end=end)
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        txt = urllib.request.urlopen(req, timeout=20).read().decode()
        rows = list(csv.reader(io.StringIO(txt)))
        valid = []
        for r in rows[1:]:
            if len(r) >= 3 and r[2].strip():
                try:
                    valid.append((r[0].strip(), float(r[2].strip())))
                except ValueError:
                    pass
        if not valid:
            return {"ok": False, "error": "no NY Fed TGCR rows returned"}

        # NY Fed dates are MM/DD/YYYY — convert to YYYY-MM-DD for correct sort
        def _to_iso(d):
            parts = d.split("/")
            if len(parts) == 3:
                return f"{parts[2]}-{parts[0]:>02}-{parts[1]:>02}"
            return d
        valid.sort(key=lambda x: _to_iso(x[0]))
        cur_date, cur_rate = valid[-1]

        # Rate at start of 2026: MEDIAN of the first ~5 rows (one TGCR print carries +/-5bp noise; a
        jan_rows = [(d, r) for d, r in valid if "2026" in d]
        pre_jan = [(d, r) for d, r in valid if "2025" in d]
        base_rate = (lambda xs: sorted(xs)[len(xs) // 2])([r for _, r in jan_rows[:5]] or [r for _, r in pre_jan[-5:]] or [cur_rate])

        total_change_bps = round((cur_rate - base_rate) * 100, 1)
        implied_cuts = max(0, round(-total_change_bps / 25))
        implied_hikes = max(0, round(total_change_bps / 25))
        # Proxy-confidence: a REAL Fed move is a clean multiple of 25bp, but the TGCR/EFFR proxy
        # carries ~+/-5bp noise — so round() alone fabricates a phantom cut from a sub-cut drift
        # (2026-06-22: -13bp drift -> round(0.52)=1). Trust the count ONLY when the move sits within
        # the noise band of a clean N*25bp multiple; otherwise it is UNCERTAIN and settles nothing.
        _residual_bp = abs((-total_change_bps / 25.0) - round(-total_change_bps / 25.0)) * 25.0
        cuts_confident = _residual_bp <= 7.0

        # Target range: floor TGCR to nearest 25bp, +25bp for ceiling
        lower_bound = int(cur_rate * 4) / 4
        upper_bound = lower_bound + 0.25

        result = {
            "ok": True, "source": "NY Fed TGCR (EFFR proxy ±5bp)",
            "cur_rate": round(cur_rate, 4), "latest_date": cur_date,
            "approx_lower": round(lower_bound, 2), "approx_upper": round(upper_bound, 2),
            "base_rate_jan2026": round(base_rate, 4),
            "total_change_bps_2026": total_change_bps,
            "implied_cuts_2026": implied_cuts,
            "implied_hikes_2026": implied_hikes,
            "cuts_confident": cuts_confident,
            "change_residual_bp": round(_residual_bp, 1),
        }

        # Evaluate market-specific trigger from params
        action = params.get("action", "unknown")
        if action in ("increase", "decrease"):
            bps = params.get("bps", 0)
            triggered = (action == "increase" and total_change_bps >= bps) or \
                        (action == "decrease" and total_change_bps <= -bps)
            result.update({"trigger": f"{action} by {bps:.0f}bp",
                           "already_triggered": bool(triggered),
                           "actual_change_bps": total_change_bps})
        elif action == "cuts_count":
            count = params.get("count", 0)
            if not cuts_confident:
                # TGCR proxy move is in the noise band (>7bp off a clean 25bp multiple) -> the cut
                # count is a phantom/uncertain (2026-06-22: a -13bp drift rounded to "1 cut" while
                # the 0-bucket priced 80% no-cuts). Settle NOTHING on noise.
                triggered = None
            elif count == 0:
                # "no cuts / no change": a cut is IRREVERSIBLE, so the two sides are
                # asymmetric and resolve at different times:
                #   • implied_cuts>=1 → the claim has DEFINITIVELY FAILED (you can't un-cut)
                #     → False, callable EARLY (enables the find_alerts WATCH on a rich YES).
                #   • implied_cuts==0 → TRUE so far but REVERSIBLE (a future cut flips it)
                #     → None (provisional); find_alerts only calls it settled within ~45d
                #     of the period end, when no rate-setting meetings remain.
                triggered = False if implied_cuts >= 1 else None
            elif params.get("ge"):
                # "N or more" (or bare "a rate cut" = >=1): YES is IRREVERSIBLE once reached
                # (you can't un-cut); BELOW N it is UNDETERMINED, not failed (more cuts may come).
                triggered = True if implied_cuts >= count else None
            else:
                # EXACTLY N — a bucket in the mutually-exclusive "how many cuts" series. YES needs
                # the FINAL count == N, knowable only when the window CLOSES; while more cuts can
                # still happen it is REVERSIBLE, so NEVER YES-lock here. (2026-06-22 false ARB:
                # "1 cut" flagged YES on implied_cuts==1 while the 0-bucket priced 80%.) It IS
                # definitively NO once exceeded (implied>N, can't un-cut -> exactly-N impossible).
                triggered = False if implied_cuts > count else None
            result.update({"trigger": f"{count} cuts in 2026",
                           "already_triggered": triggered,
                           "cuts_confirmed": (implied_cuts if cuts_confident else 0)})
        elif action == "target_level":
            target = params.get("rate", 0)
            within = abs(upper_bound - target) < 0.13 or abs(lower_bound - target) < 0.13
            # "be X% at the END of <period>" is a point-in-time SNAPSHOT, not a touch — the
            # current rate matching the target does NOT lock resolution (it can move before
            # the date). Report proximity for context; None so the arb gate skips it.
            result.update({"trigger": f"upper bound = {target}% (snapshot @ period end)",
                           "already_triggered": None, "current_within_band": bool(within)})

        return result
    except Exception as e:
        return {"ok": False, "error": str(e)}


_LMARENA_ELO_URL = ("https://huggingface.co/datasets/mathewhe/chatbot-arena-elo"
                    "/resolve/main/elo.csv")
_LMARENA_COMMITS = ("https://huggingface.co/api/datasets/mathewhe/chatbot-arena-elo"
                    "/commits/main")
_LMARENA_STALE_DAYS = 21   # arena reshuffles fast; a >3wk-old table fabricates fake arbs

def _lmarena_age_days():
    """Days since elo.csv last auto-updated, or None if undeterminable. The HF mirror's
    updater can die silently (it froze 2025-07-18) — serving that as live data would
    invent divergences (a mid-2025 ranking vs a 2026 market). Guard against it."""
    try:
        req = urllib.request.Request(_LMARENA_COMMITS, headers={"User-Agent": "Mozilla/5.0"})
        data = json.loads(urllib.request.urlopen(req, timeout=15).read())
        last = data[0].get("date")
        last_dt = _dt.datetime.fromisoformat(last.replace("Z", "+00:00"))
        return (_dt.datetime.now(_dt.timezone.utc) - last_dt).days, last[:10]
    except Exception:
        return None, None

_LMARENA_URL = "https://lmarena.ai/leaderboard"
_LMARENA_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
# model display-name vendor token -> canonical org (the labs Polymarket lists in these markets)
_VENDOR_ORG = [("fable", "Anthropic"), ("claude", "Anthropic"), ("chatgpt", "OpenAI"),
               ("gpt", "OpenAI"), ("gemini", "Google"), ("gemma", "Google"), ("grok", "xAI"),
               ("deepseek", "DeepSeek"), ("glm", "Z.ai"), ("qwen", "Alibaba"), ("llama", "Meta"),
               ("mixtral", "Mistral"), ("mistral", "Mistral"), ("command", "Cohere"),
               ("kimi", "Moonshot"), ("ernie", "Baidu"), ("nova", "Amazon"), ("step", "StepFun")]

def _lmarena_org(model):
    ml = (model or "").lower()
    for tok, org in _VENDOR_ORG:
        if tok in ml:
            return org
    return ""

def fetch_lmarena(params):
    """Current LMArena (lmarena.ai) overall leaderboard — KEYLESS, live.

    The old keyless CSV mirror (mathewhe/chatbot-arena-elo) FROZE 2025-07-18 and is dead.
    lmarena.ai is an SPA but SERVER-RENDERS the ranked rows into the page HTML as ordered
    `title="<Model>"` spans (rank 1 first). We fetch the public page (no key, no browser) and
    parse that order. Org is derived from the model name (the render shows display names, not the
    org column); score is NOT in the render — these markets ('Will <lab> have the best AI model')
    resolve on RANK / who-is-#1, not score. Same return shape as before, so the gate's market
    logic is unchanged. Live page = always fresh, so the old 21-day staleness guard is retired."""
    try:
        req = urllib.request.Request(_LMARENA_URL, headers={"User-Agent": _LMARENA_UA})
        html = urllib.request.urlopen(req, timeout=25).read().decode("utf-8", "replace")
    except Exception as e:
        return {"ok": False, "error": f"lmarena page fetch failed: {e}"}
    # ranked rows render as title="<Model>"; keep model-looking DISPLAY names in document
    # (rank) order, stop when the list transitions to the lowercase-hyphen SLUG list that follows.
    vend = re.compile(r"(?i)claude|fable|gemini|gemma|gpt|chatgpt|grok|deepseek|qwen|llama|"
                      r"mistral|mixtral|glm|command|kimi|ernie|nova|step|phi|yi-")
    ordered, seen = [], set()
    for t in re.findall(r'title="([^"]{2,60})"', html):
        t = t.strip()
        if not vend.search(t):
            continue
        if re.fullmatch(r"[a-z0-9.\-]+", t):      # reached the slug list → end of rendered rows
            break
        if t in seen:
            continue
        seen.add(t)
        ordered.append(t)
    if not ordered:
        return {"ok": False, "error": "lmarena page fetched but no ranked models parsed "
                                      "(render structure changed?) — staying dark not faking"}
    models = [{"rank": str(i + 1), "model": m, "org": _lmarena_org(m), "score": 0.0}
              for i, m in enumerate(ordered)]
    top1 = models[0]
    out = {"ok": True, "source": "lmarena", "n_models": len(models),
           "top1_model": top1["model"], "top1_org": top1["org"], "top1_score": 0.0,
           "top5": models[:5], "note": "rank-only from lmarena.ai render (scores not parsed)"}
    prov = params.get("provider")
    if prov:
        pl = prov.lower().replace(".", "").strip()
        def _match(org):
            o = (org or "").lower().replace(".", "")
            return bool(o) and (pl in o or o in pl)
        prov_rows = [m for m in models if _match(m["org"])]
        out["provider"] = prov
        out["provider_is_top1"] = _match(top1["org"])
        if prov_rows:
            best = prov_rows[0]                   # rank-sorted → provider's best is first
            out["provider_best_model"] = best["model"]
            out["provider_best_rank"] = best["rank"]
            out["provider_best_score"] = 0.0
            out["provider_gap_to_top1"] = None
        else:
            out["provider_best_model"] = None
            out["provider_note"] = f"{prov} not in the rendered top {len(models)} on lmarena.ai"
    return out


# ── ESPN sports results: settled-but-mispriced SPORTS arb ─────────────────────
# A Polymarket sports market whose game is ALREADY FINAL on ESPN is decided — the only
# question is the lag between game-end and on-chain resolution. If the YES team WON but YES
# still trades cheap, that's a settled-but-mispriced arb (the dataarb edge type). Conservative
# by design: needs BOTH teams to match ONE completed game; ambiguous → no signal (better to
# miss than false-flag). Uses sports_last30d.json (sports_data.py) + market_match fuzzy teams.
import functools as _ft


@_ft.lru_cache(maxsize=1)
def _sports_events():
    """Completed games with the FULL event name + the winning team's FULL name. The winner's
    full name is only resolvable from the ESPN "{Away} at {Home}" format (US sports = the bulk);
    "vs" formats have unreliable home/away order → winner_full=None (skipped, conservative)."""
    try:
        pay = json.load(open(_os.path.join(HERE, "sports_last30d.json"), encoding="utf-8"))
    except Exception:
        return []
    out = []
    for e in pay.get("events", []):
        if not e.get("completed") or not e.get("winner") or e["winner"] == "draw":
            continue
        nm = (e.get("name") or "").strip()
        if " at " not in nm:
            continue
        away_full, home_full = [s.strip() for s in nm.split(" at ", 1)]
        winner_full = home_full if e["winner"] == "home" else away_full
        out.append({"name": nm, "winner_full": winner_full})
    return out


def _sports_team_words():
    s = set()
    for e in _sports_events():
        s.update(re.findall(r"[a-z]{4,}", e["name"].lower()))
    return s


_ESPN_VERBS = {"beat", "beats", "defeat", "defeats", "win", "wins", "vs", "versus",
               "over", "advance", "advances", "eliminate", "eliminates", "knock", "v"}


def fetch_espn(params):
    """already_triggered = the market's YES team has ALREADY WON a now-final ESPN game.
    Matches normalized market words against the full game name (extract_teams is unreliable on
    'Will the X beat the Y'); YES team = the subject (words before the verb). Conservative:
    needs >=2 team-words in ONE game's full name; ambiguous → None (better to miss than false-flag)."""
    q = params.get("q", "")
    try:
        import market_match as _mm
        toks = _mm.normalize_text(q).split()         # "wings beat aces"
    except Exception:
        return {"ok": True, "already_triggered": None, "note": "market_match unavailable"}
    vi = next((i for i, w in enumerate(toks) if w in _ESPN_VERBS), None)
    if vi is None:
        return {"ok": True, "already_triggered": None, "note": "no head-to-head verb"}
    a_words = [w for w in toks[:vi] if len(w) >= 3]      # YES = subject team A
    b_words = [w for w in toks[vi + 1:] if len(w) >= 3]  # opponent team B
    if not a_words or not b_words:
        return {"ok": True, "already_triggered": None, "note": "need two named sides"}
    for e in _sports_events():                            # name is "Away at Home"
        away, home = [s.strip().lower() for s in e["name"].split(" at ", 1)]
        a_away, a_home = any(w in away for w in a_words), any(w in home for w in a_words)
        b_away, b_home = any(w in away for w in b_words), any(w in home for w in b_words)
        # require A and B on OPPOSITE sides of the SAME game (kills wrong-opponent matches)
        if (a_away and b_home) or (a_home and b_away):
            won = any(w in e["winner_full"].lower() for w in a_words)
            return {"ok": True, "already_triggered": bool(won), "winner": e["winner_full"],
                    "matched": e["name"], "yes_team": " ".join(a_words), "decided": True}
    return {"ok": True, "already_triggered": None, "note": "no game with both teams on opposite sides"}


# ─── BLS (CPI / unemployment / nonfarm payrolls) ──────────────────────────────
# Keyless POST to the public BLS v2 API (25 queries/day without registration — fine for a
# watchdog scan, and we cache per-series so one scan = at most 3 calls). BLS is the NAMED
# resolver on these Polymarket macro markets (LOW resolution-source-identity risk), but two
# real traps the survey flagged are encoded here:
#   1. RELEASE CALENDAR — if the market's target month isn't published yet, the print IS the
#      unresolved event. already_triggered=None, released=False → never fires a signal.
#   2. PRELIMINARY vs REVISED + at-the-threshold — BLS revises (esp. payrolls). When the value
#      sits within a buffer of the threshold, set near_threshold and SUPPRESS the arb (a
#      revision could flip it). Only fire when data clears the threshold by the buffer.
_BLS_API = "https://api.bls.gov/publicAPI/v2/timeseries/data/"
_BLS_SERIES = {                       # kind → (series_id, unit, near-threshold buffer)
    "unrate":    ("LNS14000000",        "%",  0.05),   # unemployment rate (direct %)
    "cpi_yoy":   ("CUUR0000SA0",        "%",  0.10),   # CPI-U index → YoY % change
    "payrolls":  ("CES0000000001",      "k",  25.0),   # total nonfarm level → MoM change (k)
}
_MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
_bls_cache = {}                       # series_id → list[(YYYY-MM, value)] newest-first, this run


def _parse_bls_params(q):
    """Map a macro question to {kind, threshold, direction, year, month?}. None if unmappable."""
    ql = q.lower()
    if re.search(r"\bunemployment\b|jobless rate", ql):
        kind = "unrate"
    elif re.search(r"\bcpi\b|inflation", ql):
        kind = "cpi_yoy"
    elif re.search(r"nonfarm|payrolls|jobs report|jobs added|jobs in", ql):
        kind = "payrolls"
    else:
        return None
    # Pull the THRESHOLD number, not the date. Titles vary in order ("above 4% in 2026" vs
    # "in 2026 exceed 4%"), so don't blindly take the first number: prefer a number that carries
    # a unit (%/k/thousand/million), else the first number that ISN'T a bare 4-digit year.
    cands = re.findall(r"([\d,]+(?:\.\d+)?)\s*(%|k|thousand|million)?", ql)
    cands = [(v.replace(",", ""), s) for v, s in cands if v.replace(",", "")]
    pick = next((c for c in cands if c[1]), None)               # unit-bearing wins
    if pick is None:
        pick = next((c for c in cands                          # else first non-year number
                     if not re.fullmatch(r"(19|20)\d{2}", c[0])), None)
    if pick is None:
        return None
    val, suffix = float(pick[0]), pick[1]
    if kind == "payrolls":
        if suffix == "million":
            val *= 1000.0
        elif suffix in (None, "") and val >= 10000:   # "150,000 jobs" → 150 (thousands)
            val /= 1000.0                              # "150k"/"150 thousand" already in k
    up = any(w in ql for w in ("above", "exceed", "over", "more than", "greater", "higher", "at least", "or more"))
    down = any(w in ql for w in ("below", "under", "less than", "fewer", "lower", "at most", "or less"))
    direction = "above" if up and not down else ("below" if down and not up else ("above" if up else None))
    if direction is None:
        return None
    mo = re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b", ql)
    yr = re.search(r"\b(20\d{2})\b", ql)
    return {"kind": kind, "threshold": val, "direction": direction,
            "month": _MONTHS[mo.group(1)] if mo else None,
            "year": int(yr.group(1)) if yr else None}


def _bls_series(series_id):
    """Fetch a BLS series (last ~2y) keyless; cache per run. Returns [(YYYY-MM, value)] newest-first."""
    if series_id in _bls_cache:
        return _bls_cache[series_id]
    end = _dt.date.today().year
    body = json.dumps({"seriesid": [series_id], "startyear": str(end - 2), "endyear": str(end)}).encode()
    req = urllib.request.Request(_BLS_API, data=body,
                                 headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"})
    data = json.loads(urllib.request.urlopen(req, timeout=25).read())
    if data.get("status") != "REQUEST_SUCCEEDED":
        raise RuntimeError(f"BLS status {data.get('status')}: {data.get('message')}")
    ser = data["Results"]["series"][0]["data"]
    out = []
    for d in ser:
        per = d.get("period", "")
        if not per.startswith("M") or per == "M13":      # M13 = annual avg, skip
            continue
        raw = str(d.get("value", "")).replace(",", "").strip()
        try:                                              # BLS uses "-" / "" for missing/footnoted
            val = float(raw)
        except ValueError:
            continue
        out.append((f"{d['year']}-{int(per[1:]):02d}", val))
    out.sort(reverse=True)
    _bls_cache[series_id] = out
    return out


def fetch_bls(params):
    """Resolve a CPI/unemployment/payrolls market against the actual BLS print.
    already_triggered: True/False if the target month is RELEASED, None if not out yet."""
    kind = params.get("kind")
    if kind not in _BLS_SERIES:
        return {"ok": False, "error": f"unmapped BLS kind {kind}"}
    series_id, unit, buf = _BLS_SERIES[kind]
    try:
        rows = _bls_series(series_id)
    except Exception as e:
        return {"ok": False, "error": f"BLS fetch: {e}"}
    if not rows:
        return {"ok": False, "error": "no BLS rows"}
    by_month = dict(rows)

    # Year-without-month = a SPAN market (annual-average / any-month-in-YYYY). A single
    # monthly print can't resolve it, and snapping to the latest month would be a
    # resolution-identity mismatch (the #1 leg killer). Treat as unresolved — never fire.
    if params.get("year") and not params.get("month"):
        return {"ok": True, "kind": kind, "unit": unit, "released": False,
                "target_month": str(params["year"]), "threshold": params["threshold"],
                "direction": params["direction"], "already_triggered": None,
                "note": f"year {params['year']} given without a month — span/annual market, "
                        "a single monthly print can't resolve it (suppressed)"}

    # target month: explicit "in May 2026" else latest released
    tgt = None
    if params.get("month"):
        yr = params.get("year") or _dt.date.today().year
        tgt = f"{yr}-{params['month']:02d}"
    target_key = tgt if (tgt and tgt in by_month) else rows[0][0]
    released = (tgt in by_month) if tgt else True

    # compute the market's metric for target_key
    if kind == "unrate":
        value = by_month.get(target_key)
    elif kind == "cpi_yoy":
        y, m = map(int, target_key.split("-"))
        prior_key = f"{y-1}-{m:02d}"
        if target_key in by_month and prior_key in by_month:
            value = round((by_month[target_key] / by_month[prior_key] - 1) * 100, 2)
        else:
            value, released = None, False
    else:  # payrolls MoM change (k)
        keys = [k for k, _ in rows]
        if target_key in keys:
            i = keys.index(target_key)
            value = round(by_month[target_key] - by_month[keys[i + 1]], 1) if i + 1 < len(keys) else None
        else:
            value, released = None, False

    thr, direction = params["threshold"], params["direction"]
    if value is None or not released:
        return {"ok": True, "kind": kind, "unit": unit, "released": False,
                "target_month": tgt or target_key, "threshold": thr, "direction": direction,
                "already_triggered": None, "note": "target month not yet published (print = the event)"}
    triggered = (value >= thr) if direction == "above" else (value <= thr)
    near = abs(value - thr) < buf      # within revision buffer → suppress arb (prelim risk)
    return {"ok": True, "kind": kind, "unit": unit, "released": True,
            "target_month": target_key, "value": value, "threshold": thr,
            "direction": direction, "already_triggered": bool(triggered),
            "near_threshold": bool(near), "buffer": buf,
            "latest_released": rows[0][0]}


def _parse_usgs_params(q):
    """Parse an earthquake market → {minmag, start, end, kind, lo, hi} or None if unmappable.
    kind 'threshold' = "M{X}+ in/before {yr}" (YES iff >=1 occurred); 'band' = "between A and
    B M{X}+ in {yr}" or "more than/at least N" (open upper)."""
    ql = q.lower()
    mm = re.search(r"magnitude\s*(\d+(?:\.\d+)?)|(\d+(?:\.\d+)?)\s*(?:or above|or higher|or "
                   r"greater|or larger|\+)", ql)
    if not mm:
        return None
    minmag = float(mm.group(1) or mm.group(2))
    ym = re.search(r"\b(20\d\d)\b", ql)
    if not ym:
        return None
    yr = int(ym.group(1))
    if "before" in ql:                      # "before 2027" → the active year is yr-1
        start, end = f"{yr-1}-01-01", f"{yr}-01-01"
    else:                                   # "in 2026"
        start, end = f"{yr}-01-01", f"{yr+1}-01-01"
    band = re.search(r"between\s*(\d+)\s*and\s*(\d+)", ql)
    if band:
        return {"minmag": minmag, "start": start, "end": end, "kind": "band",
                "lo": int(band.group(1)), "hi": int(band.group(2))}
    cmp = re.search(r"(?:more than|over|at least)\s*(\d+)", ql)
    if cmp:
        return {"minmag": minmag, "start": start, "end": end, "kind": "band",
                "lo": int(cmp.group(1)), "hi": None}
    # plain threshold "M{X}+ before/in {yr}"
    return {"minmag": minmag, "start": start, "end": end, "kind": "threshold",
            "lo": None, "hi": None}


def fetch_usgs(params):
    """Keyless USGS earthquake count over [start,end). YES-decided only when the data
    UNAMBIGUOUSLY confirms YES (threshold: >=1 qualifying quake; band: count in-band AND the
    window is over). A count > band-hi is NO-decided (logged WATCH, never traded = FLB)."""
    import urllib.request
    mm, start, end = params["minmag"], params["start"], params["end"]
    url = (f"https://earthquake.usgs.gov/fdsnws/event/1/query?format=geojson"
           f"&starttime={start}&endtime={end}&minmagnitude={mm}")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "datagate/1.0"})
        feats = json.loads(urllib.request.urlopen(req, timeout=20).read()).get("features", [])
    except Exception as e:
        return {"ok": False, "error": f"usgs {e}"}
    cnt = len(feats)
    year_over = _dt.date.today().isoformat() >= end
    kind, lo, hi = params["kind"], params.get("lo"), params.get("hi")
    triggered = decided_no = False
    if kind == "threshold":
        triggered = cnt >= 1
        note = f"{cnt} M{mm}+ in {start[:4]} → {'occurred (YES)' if triggered else 'none yet'}"
    else:
        if hi is not None and cnt > hi:
            decided_no = True; note = f"count {cnt} > band-hi {hi} → resolves NO"
        elif lo is not None and cnt >= lo and (hi is None or cnt <= hi):
            triggered = year_over
            note = (f"count {cnt} in [{lo},{hi or '∞'}]"
                    + (" & window over → YES" if year_over else " but window open (provisional)"))
        else:
            if year_over and lo is not None and cnt < lo:
                decided_no = True; note = f"count {cnt} < band-lo {lo}, window over → NO"
            else:
                note = f"count {cnt} below band, window open (provisional)"
    return {"ok": True, "minmag": mm, "count": cnt, "already_triggered": triggered,
            "decided_no": decided_no, "year_over": year_over, "note": note,
            "window": f"{start}..{end}"}


def fetch_launches(params):
    """Keyless orbital-launch count via thespacedevs. BUFFER (+10) before YES absorbs
    cross-tracker counting differences (the moderate identity risk). NO-decided only when the
    year is over (no fragile pace-projection → no false NO)."""
    import urllib.request
    yr, thr, prov, BUF = params["year"], params["threshold"], params["provider"], 10
    url = (f"https://ll.thespacedevs.com/2.2.0/launch/?net__gte={yr}-01-01T00:00:00Z"
           f"&net__lte={yr + 1}-01-01T00:00:00Z&search={prov}&limit=1&mode=list")
    try:
        cnt = json.loads(urllib.request.urlopen(urllib.request.Request(
            url, headers={"User-Agent": "datagate/1.0"}), timeout=20).read()).get("count")
    except Exception as e:
        return {"ok": False, "error": f"thespacedevs {e}"}
    if cnt is None:
        return {"ok": False, "error": "no launch count"}
    year_over = _dt.date.today().isoformat() >= f"{yr + 1}-01-01"
    triggered = cnt >= thr + BUF
    decided_no = year_over and cnt < thr
    note = (f"{prov} {yr} launches={cnt} vs {thr} (+{BUF} buf)"
            + (" → YES" if triggered else (" → NO (year over)" if decided_no else " (provisional)")))
    return {"ok": True, "count": cnt, "threshold": thr, "already_triggered": triggered,
            "decided_no": decided_no, "year_over": year_over, "note": note}


def fetch_fred_recession(params, desc=""):
    """Mechanical recession via FRED real-GDP q/q SAAR % (keyless). IDENTITY GUARD: only
    proceeds if the market's description states the 2-consecutive-negative-quarter rule —
    else skips (an NBER-judgment recession resolves differently, would be a false signal)."""
    dl = (desc or "").lower()
    if not (("two consecutive" in dl or "2 consecutive" in dl) and "gdp" in dl):
        return {"ok": False, "error": "recession mkt not the 2Q-GDP mechanical rule (NBER?) — skipped (identity guard)"}
    import urllib.request
    yr = params["year"]
    try:
        raw = urllib.request.urlopen(urllib.request.Request(
            "https://fred.stlouisfed.org/graph/fredgraph.csv?id=A191RL1Q225SBEA",
            headers={"User-Agent": "datagate/1.0"}), timeout=15).read().decode()
    except Exception as e:
        return {"ok": False, "error": f"fred {e}"}
    qs = []
    for ln in raw.strip().splitlines()[1:]:
        parts = ln.split(",")
        if len(parts) >= 2 and parts[1] not in ("", "."):
            qs.append((parts[0], float(parts[1])))
    inwin = [(dt, v) for dt, v in qs if dt.startswith(str(yr))]
    triggered = any(inwin[i][1] < 0 and inwin[i + 1][1] < 0 for i in range(len(inwin) - 1))
    # trailing consecutive-negative streak (radar: streak==1 → ONE more print from a YES-lock)
    neg_streak = 0
    for _, v in reversed(inwin):
        if v < 0:
            neg_streak += 1
        else:
            break
    year_over = _dt.date.today().isoformat() >= f"{yr + 1}-01-01"
    decided_no = year_over and len(inwin) >= 4 and not triggered
    latest = inwin[-1] if inwin else (qs[-1] if qs else None)
    note = f"{len(inwin)}/4 {yr} GDP quarters out" + (f", latest {latest[0][:7]}={latest[1]}%" if latest else "")
    if triggered:
        note = f"2 consecutive negative GDP quarters in {yr} → recession YES ({note})"
    elif decided_no:
        note = f"all {yr} quarters out, no 2-consec-negative → NO ({note})"
    return {"ok": True, "year": yr, "already_triggered": triggered, "decided_no": decided_no,
            "year_over": year_over, "note": note, "neg_streak": neg_streak}


def radar_signals(rows):
    """NEAR-DECIDABLE radar: provisional markets that are ONE data-point from a YES-LOCK.
    The data-arb edge is the LATENCY between a series updating and the price catching up — this
    gives advance warning of WHERE the next settled-but-mispriced YES signal will fire, so you
    watch the right markets. Forward-looking; does NOT trade. Each: {q, source, distance, why}."""
    out = []
    for r in rows:
        if not r.get("data_ok"):
            continue
        d, src = r["dossier"], r["source"]
        if d.get("already_triggered") or d.get("decided_no"):
            continue  # already decided — not radar (that's arb_signals)
        why = dist = None
        if src == "launches":
            gap = d.get("threshold", 0) - d.get("count", 0)
            if 0 < gap <= 25:                       # within ~1 month of launches
                dist, why = gap, f"{d.get('count')}/{d.get('threshold')} launches — {gap} from YES-lock"
        elif src == "fred_recession":
            if d.get("neg_streak") == 1:            # one negative quarter; next print could lock YES
                dist, why = 1, "1 negative GDP quarter banked — next quarterly print could lock recession YES"
        elif src == "crypto-threshold":
            cur, thr = d.get("current"), d.get("threshold")
            if cur and thr and thr > 0:
                pct = abs(thr - cur) / thr
                if pct <= 0.05:                      # within 5% of the touch threshold
                    dist, why = round(pct, 4), f"price {cur} within {pct*100:.1f}% of threshold {thr}"
        elif src == "usgs" and d.get("count") is not None:
            lo = r.get("params", {}).get("lo")
            if lo and 0 < lo - d["count"] <= 2:      # 1-2 quakes from entering the YES band
                dist, why = lo - d["count"], f"M-count {d['count']}, {lo - d['count']} from band-low {lo}"
        if why:
            out.append({"cid": r.get("cid"), "q": r["q"], "source": src, "yes": r.get("yes"),
                        "distance": dist, "why": why, "end": r.get("end")})
    out.sort(key=lambda x: (x["source"], x["distance"] if isinstance(x["distance"], (int, float)) else 9))
    return out


def fetch_dossier(source, params, cid, end_iso=None):
    if source == "launches":
        return fetch_launches(params)
    if source == "fred_recession":
        _start, desc = _market_start(cid)
        return fetch_fred_recession(params, desc)
    if source == "usgs":
        return fetch_usgs(params)
    if source == "bls":
        return fetch_bls(params)
    if source == "crypto-threshold":
        start_iso, _desc = _market_start(cid)
        return fetch_crypto_threshold(params["asset"], params["threshold"],
                                      params["direction"], start_iso)
    if source == "commodity":
        start_iso, _desc = _market_start(cid)
        return fetch_commodity(params["asset"], params["threshold"],
                               params["direction"], start_iso)
    if source == "portwatch":
        # impossibility math only for true MA-resolution markets (end_iso gates it);
        # ma_resolution=False keyword matches get an informational dossier with
        # already_triggered=None (never a trigger off a series they don't settle on)
        return fetch_portwatch(params["port"], params.get("threshold", 60),
                               params.get("metric", "ma7"),
                               end_iso if params.get("ma_resolution") else None,
                               ma_resolution=bool(params.get("ma_resolution")))
    if source == "fed":
        return fetch_fed(params)
    if source == "lmarena":
        return fetch_lmarena(params)
    if source == "espn":
        return fetch_espn(params)
    return {"ok": False, "error": f"source '{source}' recognized but not yet fetchable (stub)"}


# ─── SCAN ─────────────────────────────────────────────────────────────────────

def scan(min_vol=20000, max_markets=None):
    # poly_markets returns a PARTIAL feed under gamma rate-limiting; the data-arb
    # markets (crypto-threshold, PortWatch) are lower-volume and fall off a short
    # feed, so a partial fetch silently yields 0 matches (silence != success).
    import time as _t
    markets = []
    for _attempt in range(4):
        markets = edge_common.poly_markets(pages=8, per_page=100, min_vol=min_vol)
        if len(markets) >= 700:
            break
        _t.sleep(3)
    if len(markets) < 700:
        print(f"[WARN] partial feed: only {len(markets)} markets (expected ~800) — "
              f"gamma rate-limited; data-arb markets likely missing, results INCOMPLETE.",
              file=sys.stderr)
    out = []
    for m in markets:
        source, params = classify(m["q"])
        if not source:
            continue
        dossier = fetch_dossier(source, params, m["id"], (m.get("end") or "")[:10])
        out.append({
            "q": m["q"], "yes": m["yes"], "vol": round(m["vol"]),
            "liquidity": round(m.get("liquidity") or 0), "end": (m.get("end") or "")[:10],
            "cid": m["id"], "source": source, "params": params, "dossier": dossier,
            "data_ok": bool(dossier.get("ok")),
        })
        if max_markets and len(out) >= max_markets:
            break
    return out


# ─── FORMATTING ───────────────────────────────────────────────────────────────

def _fmt(d):
    dos = d["dossier"]
    head = (f"[{d['source']:16}] {('DATA-OK' if d['data_ok'] else 'NO-DATA'):8} | "
            f"YES={d['yes']:.3f} | vol=${d['vol']:>10,} | {d['q'][:46]}")
    if not d["data_ok"]:
        return head + f"\n      └─ {dos.get('error', '')}"
    if d["source"] == "crypto-threshold":
        trig = "ALREADY TRIGGERED (resolves YES)" if dos["already_triggered"] else "not yet triggered"
        win = f" [window from {dos['window_start']}]" if dos.get("window_start") else ""
        return (head +
                f"\n      └─ {dos['symbol']} cur=${dos['current']:,} | "
                f"thr=${int(dos['threshold']):,} {dos['direction']} | realized "
                f"min=${dos['realized_min']:,}/max=${dos['realized_max']:,} | "
                f"{trig} | {dos['distance_pct']:+.1f}% to threshold{win}")
    if d["source"] == "portwatch":
        warn = f" ⚠️  {dos['stale_warn']}" if dos.get("stale_warn") else ""
        trig = "TRIGGERED ✓" if dos.get("already_triggered") else "open"
        if dos.get("metric") == "daily":
            metricstr = (f"daily-max={dos.get('daily_max', dos.get('max_30d'))} vs "
                         f"{dos.get('threshold')}/day [{trig}]")
        else:
            state = ("NO-DECIDED ✗ (impossible)" if dos.get("decided_no")
                     else "info-only (not MA-resolved)" if dos.get("already_triggered") is None
                     else trig)
            metricstr = (f"7d-MA={dos['ma7']} vs {dos.get('threshold', 60)} (MA) [{state}]")
        return (head +
                f"\n      └─ {dos['port']} {metricstr} (latest={dos['latest_date']}){warn}")
    if d["source"] == "fed":
        trig_str = ""
        if "already_triggered" in dos:
            trig_str = (" → TRIGGERED ✓" if dos["already_triggered"] else " → not yet")
        trigger_label = dos.get("trigger", "")
        return (head +
                f"\n      └─ TGCR={dos['cur_rate']}% ({dos['latest_date']}) | "
                f"implied target [{dos['approx_lower']:.2f}%–{dos['approx_upper']:.2f}%] | "
                f"Δ2026={dos['total_change_bps_2026']:+.0f}bp / "
                f"~{dos['implied_cuts_2026']} cut(s)"
                f"{(' | ' + trigger_label + trig_str) if trigger_label else ''}")
    if d["source"] == "bls":
        if not dos.get("released"):
            return (head + f"\n      └─ {dos['kind']} {dos['direction']} {dos['threshold']}{dos['unit']} "
                    f"for {dos.get('target_month')} — NOT YET RELEASED (print = the event)")
        trig = "RESOLVES YES" if dos["already_triggered"] else "resolves NO"
        near = " ⚠️ near-threshold (revision risk → suppressed)" if dos.get("near_threshold") else ""
        return (head + f"\n      └─ {dos['kind']} {dos['target_month']} = {dos['value']}{dos['unit']} "
                f"vs thr {dos['threshold']}{dos['unit']} {dos['direction']} → {trig}{near}")
    return head


# ─── ALERTS & WATCHDOG ────────────────────────────────────────────────────────

import os as _os

HERE = _os.path.dirname(_os.path.abspath(__file__))
LOG = _os.path.join(HERE, "analyst_data_gate_log.jsonl")
STATE = _os.path.join(HERE, "analyst_data_gate_state.json")
VAULT = _os.path.expanduser("~/Documents/PolymarketVault/Reports/analyst_data_gate.md")


def _vault_write(path, text, tries=3, backoff=2.0):
    """Vault-note writer resilient to the iCloud Desktop&Documents sync lock.

    Sync intermittently holds vault files → open() fails EDEADLK ([Errno 11]
    'Resource deadlock avoided'; the known launchd-fleet failure, 2026-07-01).
    tmp + os.replace (atomic, no torn note mid-sync) with short-backoff retries;
    raises on final failure so callers' existing [.. FAIL] logging still fires."""
    _os.makedirs(_os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    for i in range(tries):
        try:
            with open(tmp, "w") as f:
                f.write(text)
            _os.replace(tmp, path)
            return
        except OSError:
            if i == tries - 1:
                raise
            import time as _t
            _t.sleep(backoff * (i + 1))

# 2026 FOMC meeting end-dates (Fed published schedule, verified 2026-06-18). "0 cuts so far"
# SETTLES a rate market only once NO rate-setting meeting remains before resolution; if a
# meeting is still pending, THAT meeting is the unresolved event — not a settled arb. This is
# the "no change after the July 2026 meeting" @0.795 false positive: the gate read 0 cuts YTD
# + <45d as settled-YES, but the July 28-29 FOMC hadn't happened yet.
_FOMC_2026 = ("2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17",
              "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09")


def _fomc_pending(end_iso):
    """True if a scheduled 2026 FOMC meeting falls in (today, resolution_end].
    Unknown / unparseable end → True (assume a meeting could intervene; safer NOT to fire)."""
    try:
        end = _dt.date.fromisoformat((end_iso or "")[:10])
    except Exception:
        return True
    today = _dt.date.today()
    return any(today < _dt.date.fromisoformat(m) <= end for m in _FOMC_2026)


def find_alerts(rows):
    """High-value events worth an alert. PAPER/research — read-only flags, never trades.

    ARB   — data says RESOLVES-YES but book trades YES <0.95 (settled-but-mispriced)
    WATCH — YES trades very rich (>0.97) but data not yet triggered
    DECAY — PortWatch MA ≥30 (halfway to 60 threshold) → Hormuz-NO edge eroding
    STALE — PortWatch data >10d old → stale, regime change possible
    FED-ARB — Fed market: data triggered but YES <0.80 (or vice versa)
    """
    alerts = []
    for r in rows:
        if not r["data_ok"]:
            continue
        d = r["dossier"]
        src = r["source"]

        if src == "crypto-threshold":
            if d.get("already_triggered") and ARB_SANITY_FLOOR <= r["yes"] < 0.95:
                alerts.append(("ARB",
                    f"{r['q'][:60]} — data RESOLVES-YES "
                    f"(extreme ${d['extreme_vs_threshold']:,} vs thr ${int(d['threshold']):,}) "
                    f"but YES={r['yes']:.3f}"))
            elif not d.get("already_triggered") and r["yes"] > 0.97:
                alerts.append(("WATCH",
                    f"{r['q'][:60]} — YES={r['yes']:.3f} but NOT triggered "
                    f"(extreme ${d['extreme_vs_threshold']:,})"))

        elif src == "portwatch":
            thr = d.get("threshold", 60)
            if d.get("metric") == "daily":
                dm = d.get("daily_max", d.get("max_30d", 0))
                if d.get("already_triggered") and r["yes"] < 0.97:
                    alerts.append(("ARB",
                        f"{r['q'][:50]} — {d['port']} daily-max={dm} ≥ {thr}/day → YES "
                        f"determined but YES={r['yes']:.3f}"))
                elif dm >= 0.5 * thr:
                    alerts.append(("DECAY",
                        f"{r['q'][:50]} — {d['port']} daily-max={dm} climbing toward "
                        f"{thr}/day (NO edge decaying)"))
            else:
                if d.get("ma_resolution") is False:
                    # keyword-matched market (e.g. Hormuz political question) that does
                    # NOT resolve on the traffic MA — dossier is informational only;
                    # no ARB/WATCH/DECAY may fire off a series it doesn't settle on
                    # (resolution-identity mismatch, the 2026-06-28 commodity lesson)
                    pass
                elif d.get("decided_no") and r["yes"] > 0.10:
                    # structurally impossible → informational WATCH only (buy-YES-only
                    # policy: the NO side is never auto-traded, this surfaces the lead)
                    alerts.append(("WATCH",
                        f"{r['q'][:50]} — {d['port']} {d.get('impossible_note','')}; "
                        f"NO-decided but YES={r['yes']:.3f}"))
                elif d.get("ma7", 0) >= 30:
                    alerts.append(("DECAY",
                        f"{r['q'][:50]} — {d['port']} 7d-MA={d['ma7']} climbing toward 60 "
                        f"(NO edge decaying)"))
            if d.get("stale_warn") and d.get("ma_resolution") is not False:
                alerts.append(("STALE", f"{r['q'][:50]} — {d['stale_warn']}"))

        elif src == "fed":
            triggered = d.get("already_triggered")
            trigger_desc = d.get("trigger", "")
            params = r.get("params", {})
            cuts = d.get("cuts_confirmed", 0)
            end_days = 999
            if r.get("end"):
                try:
                    end_days = (_dt.date.fromisoformat(r["end"]) - _dt.date.today()).days
                except Exception:
                    pass
            is_no_cuts = (params.get("action") == "cuts_count"
                          and params.get("count") == 0)

            if is_no_cuts:
                # "no cuts / no change" — fetch_fed encodes the irreversible asymmetry:
                #   triggered is False  → a cut already happened → claim FAILED (early-callable)
                #   triggered is None   → 0 cuts so far but reversible → only settled near end
                if triggered is False and r["yes"] > 0.90:
                    alerts.append(("WATCH",
                        f"{r['q'][:60]} — YES={r['yes']:.3f} but {cuts} cut(s) already "
                        f"happened → 'no cuts' has FAILED"))
                elif (triggered is None and end_days <= 45
                        and ARB_SANITY_FLOOR <= r["yes"] < 0.80
                        and not _fomc_pending(r.get("end"))):
                    alerts.append(("ARB",
                        f"{r['q'][:60]} — 0 cuts, <45d, no FOMC pending → resolves YES, "
                        f"but YES={r['yes']:.3f}"))
                # else: 0 cuts so far but reversible & far from end → silent (provisional)
            elif triggered is True and r["yes"] < 0.80:
                alerts.append(("ARB",
                    f"{r['q'][:60]} — Fed data says TRIGGERED ({trigger_desc}) "
                    f"but YES={r['yes']:.3f}"))
            elif triggered is False and r["yes"] > 0.90:
                alerts.append(("WATCH",
                    f"{r['q'][:60]} — YES={r['yes']:.3f} but Fed data says NOT triggered "
                    f"({trigger_desc})"))

        elif src == "bls":
            # Only the RELEASED + not-near-threshold case is actionable. Near-threshold =
            # a revision could flip it → suppress (prelim risk). Unreleased = the event.
            if not d.get("released") or d.get("near_threshold"):
                continue
            if d.get("already_triggered") and ARB_SANITY_FLOOR <= r["yes"] < 0.95:
                alerts.append(("ARB",
                    f"{r['q'][:60]} — BLS {d['kind']} {d['target_month']}={d['value']}{d['unit']} "
                    f"RESOLVES-YES but YES={r['yes']:.3f}"))
            elif not d.get("already_triggered") and r["yes"] > 0.97:
                alerts.append(("WATCH",
                    f"{r['q'][:60]} — YES={r['yes']:.3f} but BLS {d['kind']}={d['value']}{d['unit']} "
                    f"says NOT triggered"))

        elif src == "usgs":
            if d.get("already_triggered") and ARB_SANITY_FLOOR <= r["yes"] < 0.95:
                alerts.append(("ARB",
                    f"{r['q'][:60]} — USGS {d['note']} but YES={r['yes']:.3f}"))
            elif d.get("decided_no") and r["yes"] > 0.10:
                alerts.append(("WATCH",
                    f"{r['q'][:55]} — USGS {d['note']}; NO-decided (FLB, not traded) YES={r['yes']:.3f}"))

        elif src in ("fred_recession", "launches"):
            if d.get("already_triggered") and ARB_SANITY_FLOOR <= r["yes"] < 0.95:
                alerts.append(("ARB",
                    f"{r['q'][:60]} — {d['note']} but YES={r['yes']:.3f}"))
            elif d.get("decided_no") and r["yes"] > 0.10:
                alerts.append(("WATCH",
                    f"{r['q'][:55]} — {d['note']}; NO-decided (FLB, not traded) YES={r['yes']:.3f}"))
    return alerts


def arb_signals(rows):
    """ACTIONABLE data-arb signals = the ARB subset of find_alerts: BUY YES ONLY, because
    the resolving DATA already confirms YES and YES is still cheap. The WATCH/buy-NO cases
    are EXCLUDED on purpose — cheap NO on a long tail is the documented FLB trap. This is
    the one edge type that ever beat this market (settled-but-mispriced). Consumed by the
    dataarb.py paper leg. Each: {cid, q, side, price, reason, source, end}."""
    sigs = []
    for r in rows:
        if not r.get("data_ok"):
            continue
        d, src, yes = r["dossier"], r["source"], r["yes"]
        side = reason = None
        if src == "crypto-threshold" and d.get("already_triggered") and yes < 0.95:
            side, reason = "YES", f"data resolves-YES (extreme vs thr {d.get('threshold')}), YES={yes:.3f}"
        elif src == "fed":
            params, trig = r.get("params", {}), d.get("already_triggered")
            end_days = 999
            if r.get("end"):
                try:
                    end_days = (_dt.date.fromisoformat(r["end"]) - _dt.date.today()).days
                except Exception:
                    pass
            no_cuts = params.get("action") == "cuts_count" and params.get("count") == 0
            if (no_cuts and trig is None and end_days <= 45 and yes < 0.80
                    and not _fomc_pending(r.get("end"))):
                side, reason = "YES", f"0 cuts, <45d, no FOMC pending → resolves YES, YES={yes:.3f}"
            elif (not no_cuts) and trig is True and yes < 0.80:
                side, reason = "YES", f"Fed TRIGGERED ({d.get('trigger','')}), YES={yes:.3f}"
        elif src == "espn" and d.get("already_triggered") and yes < 0.97:
            side, reason = "YES", f"game DECIDED ({d.get('matched')}) — {d.get('yes_team')} WON, but YES={yes:.3f}"
        elif src == "bls" and d.get("released") and not d.get("near_threshold") \
                and d.get("already_triggered") and yes < 0.95:
            side, reason = "YES", (f"BLS {d.get('kind')} {d.get('target_month')}="
                                   f"{d.get('value')}{d.get('unit')} resolves-YES, YES={yes:.3f}")
        elif src == "usgs" and d.get("already_triggered") and yes < 0.95:
            side, reason = "YES", f"USGS {d.get('note')}, YES={yes:.3f}"
        elif src == "fred_recession" and d.get("already_triggered") and yes < 0.95:
            side, reason = "YES", f"FRED {d.get('note')}, YES={yes:.3f}"
        elif src == "launches" and d.get("already_triggered") and yes < 0.95:
            side, reason = "YES", f"thespacedevs {d.get('note')}, YES={yes:.3f}"
        # Price-sanity floor: data says resolves-YES, so YES must not be on the NO side of
        # 50/50. A sub-floor YES means the book disagrees with our read by >50pts → suppress
        # (the read is the suspect). All signals here are buy-YES, so this is the single choke.
        if side and yes >= ARB_SANITY_FLOOR:
            sigs.append({"cid": r["cid"], "q": r["q"], "side": side, "price": round(yes, 4),
                         "reason": reason, "source": src, "end": r.get("end")})
    return sigs


def run_once():
    """Watchdog entry: scan, log JSONL, update vault, alert on ARB / DECAY / STALE."""
    ts = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = scan(min_vol=50000)
    ok = [r for r in rows if r["data_ok"]]
    alerts = find_alerts(rows)

    # persist structured ARB signals + a CONTROL POOL for the dataarb paper leg. Control =
    # source-matched, comparable cheap-YES markets that are NOT data-confirmed → buying YES on
    # them isolates whether the EDGE is the data signal or just "buy cheap YES" (which a
    # calibrated market prices to ~0). The leg is real only if it beats this control.
    try:
        sigs = arb_signals(rows)
        radar = radar_signals(rows)   # near-decidable: where the NEXT YES signal will likely fire
        sig_cids = {s["cid"] for s in sigs}
        ctrl = [{"cid": r["cid"], "q": r["q"], "yes": r["yes"], "end": r.get("end")}
                for r in rows if r.get("data_ok") and 0.50 <= r["yes"] <= 0.95
                and r["cid"] not in sig_cids][:40]
        json.dump({"ts": ts, "signals": sigs, "control_pool": ctrl, "radar": radar},
                  open(_os.path.join(HERE, "datagate_arbs.json"), "w"), indent=2)
        # radar vault note (forward-looking watchlist — NOT positions)
        try:
            rv = _os.path.expanduser("~/Documents/PolymarketVault/Reports/DataArb Radar.md")
            body = "\n".join(f"- **{x['why']}** · YES={x.get('yes')} · {x['q'][:55]} ({x['source']})"
                             for x in radar) or "_(nothing near-decidable right now — dormant, expected)_"
            _vault_write(rv,
                f"---\ntype: dashboard\nstatus: live\n---\n\n# 🔭 Data-Arb Radar (near-decidable)\n\n"
                f"*Markets ONE data-point from a YES-lock — advance warning of where the next "
                f"settled-but-mispriced signal will fire (the data-arb edge is the latency before "
                f"the price catches up). Auto {ts}. Watchlist, NOT positions.*\n\n{body}\n")
        except Exception as e:
            print(f"[radar vault FAIL] {e}", file=sys.stderr)
    except Exception as e:
        print(f"[arb persist FAIL] {e}", file=sys.stderr)

    # append-only JSONL
    try:
        with open(LOG, "a") as f:
            f.write(json.dumps({
                "ts": ts, "matched": len(rows), "data_ok": len(ok),
                "n_alerts": len(alerts),
                "alerts": [{"kind": k, "msg": m} for k, m in alerts],
                "rows": rows,
            }) + "\n")
    except Exception as e:
        print(f"[log FAIL] {e}", file=sys.stderr)

    # vault snapshot (overwrite latest)
    try:
        lines = ["# Analyst Data Gate — live resolving-series check", "",
                 f"_Updated {ts} · {len(ok)}/{len(rows)} matched markets carry a fetched series_",
                 "",
                 "Read-only. Markets earn analysis only if resolving series is fetchable. "
                 "Flags settled-but-mispriced (arb) and decaying NO edges.", ""]
        if alerts:
            lines += ["## ⚠️ Edge flags", ""]
            for k, m in alerts:
                lines.append(f"- **[{k}]** {m}")
            lines.append("")
        lines += ["## All data-confirmed markets", "", "| YES | source | data | market |",
                  "|----:|--------|------|--------|"]
        for r in sorted(ok, key=lambda x: -x["vol"]):
            d = r["dossier"]
            if r["source"] == "crypto-threshold":
                ds = (f"{d['symbol']} cur=${d['current']:,} "
                      f"{'≤' if d['direction']=='down' else '≥'} ${int(d['threshold']):,} | "
                      f"{'TRIG' if d['already_triggered'] else 'open'} ({d['distance_pct']:+.0f}%)")
            elif r["source"] == "portwatch":
                if d.get("metric") == "daily":
                    dm = d.get("daily_max", d.get("max_30d"))
                    ds = (f"{d['port']} daily-max={dm} (thr {d.get('threshold')}/day) "
                          f"{'TRIG' if d.get('already_triggered') else 'open'}")
                else:
                    _t = d.get("already_triggered")
                    ds = (f"{d['port']} MA7={d['ma7']} (thr {d.get('threshold', 60)}) "
                          f"{'info-only' if _t is None else ('TRIG' if _t else 'open')}")
            elif r["source"] == "fed":
                ds = (f"TGCR={d['cur_rate']}% [{d['approx_lower']:.2f}–{d['approx_upper']:.2f}%] "
                      f"Δ={d['total_change_bps_2026']:+.0f}bp ~{d['implied_cuts_2026']}cut(s)")
            elif r["source"] == "bls":
                if not d.get("released"):
                    ds = f"{d['kind']} {d.get('target_month')} NOT-RELEASED"
                else:
                    ds = (f"{d['kind']} {d['target_month']}={d['value']}{d['unit']} "
                          f"{d['direction']} {d['threshold']}{d['unit']} "
                          f"{'TRIG' if d['already_triggered'] else 'open'}"
                          f"{' near' if d.get('near_threshold') else ''}")
            else:
                ds = "—"
            lines.append(f"| {r['yes']:.3f} | {r['source']} | {ds} | {r['q'][:48]} |")
        lines += ["", "## Honest-by-construction guards (BLS)", "",
                  "- **release-calendar** — target month unpublished → `released=False`, never fires (the print IS the event).",
                  "- **near-threshold** — within unrate 0.05 / CPI 0.10 / payrolls 25k buffer → suppress (a revision could flip).",
                  "- **year-without-month** — a year-only title (\"unemployment in 2026\") is an annual/span basis a single monthly print can't resolve → `released=False`, never fires (snapping to the latest month = resolution-identity mismatch, the #1 leg killer; commit `1efe670`).",
                  "", "See [[Data Sources]] · [[Settled-but-Mispriced Arb]]."]
        _vault_write(VAULT, "\n".join(lines) + "\n")
    except Exception as e:
        print(f"[vault FAIL] {e}", file=sys.stderr)

    # Tiered alerts: ARB (highest), DECAY (Hormuz erosion), STALE (regime change risk)
    for kind, label, emoji in [
        ("ARB",   "DATA-GATE ARB — settled-but-mispriced",         "🟢"),
        ("DECAY", "PORTWATCH DECAY — NO edge eroding",             "⚠️"),
        ("STALE", "DATA STALE — check for regime-change event",    "🔄"),
    ]:
        msgs = [m for k, m in alerts if k == kind]
        if msgs:
            try:
                import wa_alert
                wa_alert.notify(f"{emoji} {label}\n" + "\n".join(msgs))
            except Exception as e:
                print(f"[{kind} alert FAIL] {e}", file=sys.stderr)

    n_arb = sum(1 for k, _ in alerts if k == "ARB")
    n_decay = sum(1 for k, _ in alerts if k == "DECAY")
    n_stale = sum(1 for k, _ in alerts if k == "STALE")
    print(f"[{ts}] data-gate: {len(ok)}/{len(rows)} data-ok, {len(alerts)} flags "
          f"({n_arb} ARB / {n_decay} DECAY / {n_stale} STALE) → {LOG}")
    return rows


def main():
    args = sys.argv[1:]
    if "once" in args:
        run_once()
        return
    as_json = "--json" in args
    min_vol = 20000
    if "--min-vol" in args:
        min_vol = int(args[args.index("--min-vol") + 1])
    rows = scan(min_vol=min_vol)
    if as_json:
        print(json.dumps(rows, indent=2))
        return
    ok = [r for r in rows if r["data_ok"]]
    print(f"\nDATA-GATE SCAN — {len(rows)} markets matched a known source, "
          f"{len(ok)} with LIVE data (min_vol=${min_vol:,})\n")
    for r in sorted(rows, key=lambda x: (not x["data_ok"], -x["vol"])):
        print(_fmt(r))
    alerts = find_alerts(rows)
    print(f"\n{'='*70}")
    print(f"GATE VERDICT: {len(ok)} markets carry a fetched resolving series → eligible for the analyst panel.")
    if alerts:
        print(f"EDGE FLAGS ({len(alerts)}):")
        for k, m in alerts:
            print(f"  [{k}] {m}")
    print("Markets with no fetchable source are EXCLUDED (deferred to structural track).")


if __name__ == "__main__":
    main()
