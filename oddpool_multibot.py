#!/usr/bin/env python3
"""
oddpool_multibot.py — Unified Oddpool data collection across 4 tracks:

  1. SEARCH    — find active markets on Kalshi+Polymarket, surface analyst candidates
  2. ARB       — cross-venue price differences ≥2¢  (Premium plan; graceful degrade)
  3. ORDERBOOK — top-of-book history for top candidates (free tier, cached)
  4. WHALE     — $500+ trades on tracked events     (Pro plan; graceful degrade)

Run:  python3 oddpool_multibot.py [--once]
      Designed for `once` mode under watchdog, 15-min cycle.

State: oddpool_multibot_state.json  (seen alerts, last search ts)
Log:   oddpool_multibot.log
Vault: ~/Documents/PolymarketVault/Reports/oddpool_digest.md
Alert: iMessage +918449447444 on new arb ≥2¢ or new whale ≥$1000
"""

import json, os, sys, time, urllib.request, urllib.error, urllib.parse
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = "https://api.oddpool.com"
LOG  = os.path.join(HERE, "oddpool_multibot.log")
STATE_FILE = os.path.join(HERE, "oddpool_multibot_state.json")
VAULT_REPORT = os.path.expanduser(
    "~/Documents/PolymarketVault/Reports/oddpool_digest.md"
)

ALERT_RECIPIENT = "+918449447444"
SEARCH_CACHE_SEC = 1800   # 30 min — free tier: 1K req/month
ORDERBOOK_PAGES  = 2      # keep free quota low (2 × 200 snaps per market)
TOP_N_ORDERBOOK  = 3      # pull history for top-3 candidates only
MIN_ARB_CENTS    = 2.0    # alert threshold for true arb
MIN_ARB_DIFF     = 0.03   # minimum spread to log as price divergence
MIN_WHALE_USD    = 1000   # alert threshold for whale trades
MIN_CANDIDATE_VOL = 500   # minimum $ volume to surface as search candidate (0 = brand new market)

# ── Key loading ───────────────────────────────────────────────────────────────

def _key():
    k = os.environ.get("ODDPOOL_KEY", "")
    if not k:
        env_path = os.path.join(HERE, ".env")
        try:
            for line in open(env_path):
                if line.startswith("ODDPOOL_KEY="):
                    k = line.strip().split("=", 1)[1].strip()
        except FileNotFoundError:
            pass
    return k

KEY = _key()


# ── HTTP helper (429-backoff, graceful 403) ────────────────────────────────────

class PlanError(Exception):
    """Raised when the endpoint needs a higher plan (403)."""

def _get(path, tries=5):
    """Rate-limit-aware GET. Returns parsed JSON or raises PlanError/Exception."""
    url = BASE + path
    for attempt in range(tries):
        try:
            req = urllib.request.Request(
                url,
                headers={"X-API-Key": KEY, "User-Agent": "pm-multibot/1.0"},
            )
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 403:
                raise PlanError(f"plan limit: {path}")
            if e.code == 429 and attempt < tries - 1:
                wait = 4.0 * (attempt + 1)
                _log(f"  429 rate-limit on {path} — sleeping {wait:.0f}s")
                time.sleep(wait)
                continue
            raise
        except Exception:
            if attempt < tries - 1:
                time.sleep(2.0 * (attempt + 1))
                continue
            raise
    raise RuntimeError(f"_get exhausted retries: {path}")


# ── Logging ───────────────────────────────────────────────────────────────────

def _log(msg):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    try:
        with open(LOG, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ── iMessage alert (fail-soft) ────────────────────────────────────────────────

def _imessage(msg, recipient=ALERT_RECIPIENT):
    safe = msg.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    safe = "".join(c if ord(c) < 128 else " " for c in safe)
    os.system(
        f'osascript -e \'tell application "Messages" to send "{safe}" '
        f'to buddy "{recipient}" of (service 1 whose service type = iMessage)\' '
        f'2>/dev/null'
    )


# ── Vault write ───────────────────────────────────────────────────────────────

def _vault_write(content):
    try:
        os.makedirs(os.path.dirname(VAULT_REPORT), exist_ok=True)
        with open(VAULT_REPORT, "w") as f:
            f.write(content)
    except Exception as e:
        _log(f"  WARN vault write failed: {e}")


# ── State persistence ─────────────────────────────────────────────────────────

def _load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}

def _save_state(st):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(st, f, indent=2)
    except Exception as e:
        _log(f"  WARN state save failed: {e}")

def _state_key_arb(opp):
    return f"{opp.get('event_title','')}|{opp.get('label','')}"

def _state_key_whale(t):
    return f"{t.get('platform','')}|{t.get('event_title','')}|{t.get('timestamp','')}"


# ── TRACK 1: SEARCH ───────────────────────────────────────────────────────────

SEARCH_QUERIES = ["bitcoin", "ethereum", "fed", "trump", "election", "esports", "crypto"]

def track_search(state):
    """Find active high-volume markets on Kalshi+Polymarket. Free tier — cache 30min."""
    now = time.time()
    last_ts = state.get("last_search_ts", 0)
    if now - last_ts < SEARCH_CACHE_SEC:
        age = int(now - last_ts)
        _log(f"SEARCH — cached ({age}s ago), skipping to conserve free tier")
        return state.get("last_candidates", [])

    _log("SEARCH — fetching recent events + topic queries (cross-venue)…")
    seen_ids = set()
    candidates = []

    def _to_list(data):
        """Normalize API response — some endpoints return a plain list, others wrap in a dict."""
        if isinstance(data, list):
            return data
        return data.get("events", data.get("results", []))

    def _ingest(events):
        for ev in events:
            eid = ev.get("event_id", "")
            if eid in seen_ids:
                continue
            seen_ids.add(eid)
            vol = ev.get("total_volume", 0) or 0
            if vol < MIN_CANDIDATE_VOL:
                continue
            candidates.append({
                "event_id":     eid,
                "event_title":  ev.get("title") or ev.get("event_title", ""),
                "venue":        ev.get("exchange") or ev.get("venue", ""),
                "volume":       vol,
                "liquidity":    ev.get("total_liquidity", 0) or 0,
                "market_count": ev.get("market_count", 0),
                "category":     ev.get("category", ""),
            })

    # Primary: recent events (returns plain list)
    try:
        data = _get("/search/recent/events?limit=50")
        events = _to_list(data)
        _log(f"  recent events: {len(events)}")
        _ingest(events)
        time.sleep(1.0)   # rate-limit guard
    except Exception as e:
        _log(f"  WARN recent/events failed: {e}")

    # Secondary: topic queries — conserve quota (skip if already have ≥10)
    if len(candidates) < 10:
        for q in SEARCH_QUERIES[:4]:
            try:
                data = _get(f"/search/events?q={urllib.parse.quote(q)}&status=active&sort_by=volume&limit=20")
                _ingest(_to_list(data))
                time.sleep(1.2)   # heavier rate-limit guard
            except Exception as e:
                _log(f"  WARN search/{q} failed: {e}")

    candidates.sort(key=lambda x: x["volume"], reverse=True)
    _log(f"  candidates ≥${MIN_CANDIDATE_VOL:,}: {len(candidates)}")

    state["last_search_ts"] = now
    state["last_candidates"] = candidates
    return candidates


# ── TRACK 2: ARB ─────────────────────────────────────────────────────────────

def track_arb(state):
    """Cross-venue arb + price differences. Premium plan — degrade gracefully."""
    _log("ARB — fetching cross-venue opportunities…")
    new_arbs = []
    seen_arb = set(state.get("seen_arb", []))

    # True arb (requires Premium)
    try:
        data = _get("/arbitrage/current")
        opps = data.get("opportunities", [])
        _log(f"  arb opportunities: {len(opps)}")
        for o in opps:
            net = o.get("net_cents", 0)
            key = _state_key_arb(o)
            if net >= MIN_ARB_CENTS and key not in seen_arb:
                new_arbs.append({"type": "true_arb", "net_cents": net, **o})
                seen_arb.add(key)
                _log(f"  NEW ARB: {o.get('event_title','')} | {o.get('label','')} | net={net:.1f}¢")
    except PlanError:
        _log("  SKIP true-arb (Premium plan required)")
    except Exception as e:
        _log(f"  WARN ARB true-arb failed: {e}")

    # Price differences (may be free-tier accessible)
    diff_opps = []
    try:
        data = _get("/arbitrage/current/difference")
        diffs = data.get("differences", [])
        _log(f"  price differences: {len(diffs)}")
        for d in diffs:
            spread = abs(d.get("spread", 0))
            if spread >= MIN_ARB_DIFF:
                key = _state_key_arb(d)
                diff_opps.append({"type": "price_diff", "spread": spread, **d})
        _log(f"  notable divergences (≥{MIN_ARB_DIFF:.0%}): {len(diff_opps)}")
    except PlanError:
        _log("  SKIP price-diff (Premium plan required)")
    except Exception as e:
        _log(f"  WARN ARB price-diff failed: {e}")

    state["seen_arb"] = list(seen_arb)
    return new_arbs, diff_opps


# ── TRACK 3: ORDERBOOK ────────────────────────────────────────────────────────

def track_orderbook(candidates):
    """Pull 5m top-of-book history for top-N candidates. Free tier."""
    if not candidates:
        _log("ORDERBOOK — no candidates, skipping")
        return {}

    top = candidates[:TOP_N_ORDERBOOK]
    _log(f"ORDERBOOK — pulling history for top-{len(top)} candidates…")
    results = {}

    for ev in top:
        eid = ev.get("event_id", "")
        if not eid:
            continue
        # Get markets under this event first
        try:
            mdata = _get(f"/search/events/{eid}/markets")
            # endpoint may return plain list or {"markets": [...]}
            markets = mdata if isinstance(mdata, list) else mdata.get("markets", [])
            if not markets:
                continue
            # pick the YES-outcome market (or first)
            mkt = markets[0]
            market_id = mkt.get("market_id", "")
            if not market_id:
                continue

            snaps = []
            pk = None
            for page in range(ORDERBOOK_PAGES):
                path = (f"/historical/polymarket/top-of-book"
                        f"?market_id={urllib.parse.quote(market_id)}"
                        f"&granularity=5m&limit=200")
                if pk:
                    path += f"&pagination_key={pk}"
                try:
                    d = _get(path)
                    snaps.extend(d.get("snapshots", []))
                    pg = d.get("pagination") or {}
                    pk = pg.get("pagination_key")
                    if not pg.get("has_more") or not pk:
                        break
                    time.sleep(0.6)
                except Exception:
                    break

            if not snaps:
                continue

            # compute spread stats from YES-token snaps
            yes_snaps = [s for s in snaps if s.get("side") in ("yes", None)]
            spreads = [s["spread"] for s in yes_snaps if s.get("spread") is not None]
            mids    = [s["mid"]    for s in yes_snaps if s.get("mid")    is not None]

            stats = {
                "market_id":   market_id,
                "n_snaps":     len(yes_snaps),
                "spread_mean": round(sum(spreads)/len(spreads), 4) if spreads else None,
                "spread_min":  round(min(spreads), 4) if spreads else None,
                "mid_latest":  round(mids[-1], 4) if mids else None,
                "mid_oldest":  round(mids[0],  4) if mids else None,
            }
            results[eid] = stats
            _log(f"  {ev.get('event_title', eid)[:50]} — "
                 f"mid {stats['mid_oldest']}→{stats['mid_latest']}  "
                 f"spread~{stats['spread_mean']}")
            time.sleep(0.4)

        except PlanError as e:
            _log(f"  WARN ORDERBOOK plan error for {eid}: {e}")
        except Exception as e:
            _log(f"  WARN ORDERBOOK failed for {eid}: {e}")

    return results


# ── TRACK 4: WHALE ────────────────────────────────────────────────────────────

def track_whale(state):
    """Pull $500+ whale trade feed. Pro plan — degrade gracefully."""
    _log("WHALE — fetching recent large trades…")
    new_whales = []
    seen_whale = set(state.get("seen_whale", []))

    try:
        data = _get("/whales/user/feed?limit=50")
        trades = data.get("trades", [])
        _log(f"  whale trades in feed: {len(trades)}")
        for t in trades:
            size = t.get("trade_size_usd", 0)
            key  = _state_key_whale(t)
            if size >= MIN_WHALE_USD and key not in seen_whale:
                new_whales.append(t)
                seen_whale.add(key)
                _log(f"  NEW WHALE: ${size:,.0f} {t.get('taker_side','?').upper()} "
                     f"@ {t.get('price',0):.2f}  — {t.get('event_title','')[:50]}")
    except PlanError:
        _log("  SKIP whale feed (Pro plan required)")
    except Exception as e:
        _log(f"  WARN WHALE failed: {e}")

    state["seen_whale"] = list(seen_whale)
    return new_whales


# ── Digest builder ────────────────────────────────────────────────────────────

def _build_digest(candidates, new_arbs, diff_opps, ob_stats, new_whales):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        f"# Oddpool Multibot Digest",
        f"*Updated: {ts}*",
        "",
        "---",
        "",
    ]

    # Arb
    lines += ["## Cross-Venue Arb", ""]
    if new_arbs:
        lines += ["| Event | Label | Net ¢ | Buy YES | Buy NO |",
                  "|-------|-------|-------|---------|--------|"]
        for o in new_arbs:
            lines.append(
                f"| {o.get('event_title','')[:40]} "
                f"| {o.get('label','')[:20]} "
                f"| **{o.get('net_cents',0):.1f}¢** "
                f"| {o.get('buy_yes_market','-')} "
                f"| {o.get('buy_no_market','-')} |"
            )
    else:
        lines.append("*No true arb ≥2¢ this cycle.*")
    lines.append("")

    # Price differences
    lines += ["## Price Divergences (≥3¢ spread)", ""]
    if diff_opps:
        lines += ["| Event | Spread | Polymarket | Kalshi |",
                  "|-------|--------|------------|--------|"]
        for d in diff_opps[:10]:
            lines.append(
                f"| {d.get('event_title','')[:40]} "
                f"| {d.get('spread',0):.2%} "
                f"| {d.get('polymarket',{}).get('yes_ask','-')} "
                f"| {d.get('kalshi',{}).get('yes_ask','-')} |"
            )
    else:
        lines.append("*No notable divergences this cycle.*")
    lines.append("")

    # Whale
    lines += ["## Whale Trades (≥$1,000)", ""]
    if new_whales:
        lines += ["| Platform | Event | Side | Price | Size |",
                  "|----------|-------|------|-------|------|"]
        for t in new_whales[:10]:
            lines.append(
                f"| {t.get('platform','-')} "
                f"| {t.get('event_title','')[:35]} "
                f"| **{t.get('taker_side','?').upper()}** "
                f"| {t.get('price',0):.2f} "
                f"| ${t.get('trade_size_usd',0):,.0f} |"
            )
    else:
        lines.append("*No new whale trades ≥$1,000 this cycle.*")
    lines.append("")

    # Search candidates
    lines += ["## Top Active Markets", ""]
    if candidates:
        lines += ["| Event | Venue | Volume | Markets |",
                  "|-------|-------|--------|---------|"]
        for c in candidates[:15]:
            ob = ob_stats.get(c.get("event_id", ""), {})
            mid_str = ""
            if ob.get("mid_latest"):
                mid_str = f" mid={ob['mid_latest']:.2f}"
            lines.append(
                f"| {c['event_title'][:40]} "
                f"| {c.get('venue','-')} "
                f"| ${c['volume']:,.0f} "
                f"| {c['market_count']}{mid_str} |"
            )
    else:
        lines.append("*No candidates found.*")
    lines.append("")

    return "\n".join(lines)


# ── Alert builder ─────────────────────────────────────────────────────────────

def _build_alert(new_arbs, new_whales):
    parts = []
    if new_arbs:
        for o in new_arbs:
            parts.append(
                f"[ODDPOOL ARB] {o.get('net_cents',0):.1f}c net | "
                f"{o.get('event_title','')[:40]} | "
                f"YES@{o.get('buy_yes_market','-')} "
                f"NO@{o.get('buy_no_market','-')}"
            )
    if new_whales:
        for t in new_whales[:3]:
            parts.append(
                f"[WHALE] ${t.get('trade_size_usd',0):,.0f} "
                f"{t.get('taker_side','?').upper()} "
                f"@{t.get('price',0):.2f} | "
                f"{t.get('event_title','')[:40]}"
            )
    return "\n".join(parts)


# ── Main ──────────────────────────────────────────────────────────────────────

def run():
    _log("=" * 60)
    _log("oddpool_multibot — starting 4-track cycle")

    if not KEY:
        _log("ERROR: ODDPOOL_KEY not found in .env or environment — aborting")
        return

    state = _load_state()

    # Track 1 — Search
    candidates = track_search(state)

    # Track 2 — Arb
    new_arbs, diff_opps = track_arb(state)

    # Track 3 — Orderbook (top candidates only)
    ob_stats = track_orderbook(candidates)

    # Track 4 — Whale
    new_whales = track_whale(state)

    # Persist updated state
    _save_state(state)

    # Vault digest
    digest = _build_digest(candidates, new_arbs, diff_opps, ob_stats, new_whales)
    _vault_write(digest)
    _log("  digest → Vault/Reports/oddpool_digest.md")

    # Alert if actionable
    alert_body = _build_alert(new_arbs, new_whales)
    if alert_body:
        _log(f"  ALERT → iMessage:\n{alert_body}")
        _imessage(alert_body)
    else:
        _log("  no alert-worthy events this cycle")

    _log("oddpool_multibot — done")


if __name__ == "__main__":
    run()
