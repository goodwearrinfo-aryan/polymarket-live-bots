#!/usr/bin/env python3
"""venue_watch.py — read-only watchlist sentinel for the VENUE SEARCH (2026-06-18).

WHY THIS EXISTS
---------------
A venue-edge scout (4-category research workflow) asked: since Polymarket is too calibrated
for the bot's non-predictive structural arb (complete-set / negRisk basket, monotonicity),
is there a THINNER venue where that structure exists and persists?  Ground-truthed answer:

  • Kalshi↔Polymarket cross-venue  → ALREADY DEAD (Lesson 23, unrealizable; 0-div null paid
    for).  Deliberately NOT watched here — we do not relitigate settled dead-ends.
  • Limitless Exchange (Base)      → keyless read works, BUT the live book is 100% short-term
    crypto coinflips ("X Up or Down - 5 Min", "X above $K?") with 0 negRisk groups = the
    bot's deadest leg type (coinup/coindown).  Nothing to capture TODAY.
  • Hyperliquid HIP-4              → best keyless real-time CLOB, but multi-outcome/negRisk
    not yet shipped (probe 2026-06-18: 0 PM tokens across 866 spot).  NOW AUTO-WATCHED here
    via scan_hyperliquid() — fires the day a real YES/NO / event token appears.

So there is NO venue switch to make right now.  This sentinel watches — keylessly, read-only —
for the day that changes: the moment Limitless lists a REAL multi-outcome / event market, OR
Hyperliquid HIP-4 ships a prediction-market token, the existing basketlock complete-set engine
can be pointed at it (depth-walking the book, never the mid — Lesson 13).  It NEVER trades.
Fail-soft on every error.

Manual re-check target (no clean keyless emergence-probe yet, so noted not auto-watched):
Novig (new CFTC DCM, approved 2026-06-16 — re-probe ~2026-07-18 for a keyless market-data read).
"""
import json, os, sys, re, subprocess, urllib.request
from datetime import datetime, timezone

HERE       = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(HERE, "venue_watch_state.json")
LOG_FILE   = os.path.join(HERE, "venue_watch_log.jsonl")
TARGETS    = ["krisharyan@icloud.com", "+918449447444"]

LIMITLESS_ACTIVE = "https://api.limitless.exchange/markets/active"
HL_INFO          = "https://api.hyperliquid.xyz/info"   # POST {"type":"spotMeta"} — keyless

# A market is a "coinflip" (= the bot's dead type, NOT a structural-arb candidate) if its
# title is an Up/Down or an intraday threshold question. Anything else on a thin venue is a
# potential event/multi-outcome market worth a look.
_COINFLIP = re.compile(r"(up or down|\babove \$|\bbelow \$|\b\d+\s*min\b|hourly)", re.I)

# Hyperliquid HIP-4 emergence: a prediction-market token (paired YES/NO outcome, or an event
# market) appearing in the spot universe. Probe 2026-06-18: 0 shipped across 866 spot tokens
# (the "TRUMP"/"NOT" hits are memecoins, not PM tokens). We watch the token names for a PM
# signature and EXCLUDE known memecoins. Quiet until HIP-4 actually lists real outcome tokens.
_HL_PM   = re.compile(r"\b(yes|no|win|pred|vote|elect)\b|->|\bvs\b|market\?", re.I)
_HL_MEME = {"TRUMP", "NOT", "PNUT", "WIF", "BONK", "POPCAT", "MOODENG", "HPOS", "FARM", "GUP", "PURR"}


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "venue-watch/1.0"})
    return json.loads(urllib.request.urlopen(req, timeout=15).read())


def _post(url, body):
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data,
                                 headers={"User-Agent": "venue-watch/1.0",
                                          "Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=15).read())


def _markets(payload):
    """Normalize Limitless's response shape to a list of market dicts."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for k in ("data", "markets"):
            if isinstance(payload.get(k), list):
                return payload[k]
    return []


def scan_limitless():
    """Return (total, qualifying) where qualifying = live markets that are NOT coinflips,
    OR carry a negRisk/group hook = the complete-set structure the bot wants. Fail-soft → (0, [])."""
    try:
        mk = _markets(_get(LIMITLESS_ACTIVE))
    except Exception as e:
        print(f"limitless fetch failed (fail-soft): {type(e).__name__}: {e}", file=sys.stderr)
        return 0, []
    qual = []
    for m in mk:
        title = str(m.get("title") or m.get("proxyTitle") or "")
        has_group = bool(m.get("negRiskRequestId") or m.get("groupId"))
        if has_group or not _COINFLIP.search(title):
            qual.append({
                "id":    str(m.get("id") or m.get("conditionId") or title),
                "title": title[:80],
                "negRisk": bool(m.get("negRiskRequestId")),
                "group":  m.get("groupId"),
                "volume": m.get("volume", 0),
                "reason": "negRisk/group" if has_group else "non-coinflip",
            })
    return len(mk), qual


def scan_hyperliquid():
    """Watch the Hyperliquid spot universe for an emergent HIP-4 prediction market — a paired
    YES/NO or event token — excluding known memecoins. Fail-soft → (0, []). Returns
    (total_tokens, qualifying). NOTE: this only DETECTS emergence; when a real market appears,
    evaluate the lock with POST {"type":"l2Book","coin":<name>} and DEPTH-WALK the asks
    (never the mid — Lesson 13 gap-through guard), then point basketlock at it."""
    try:
        meta = _post(HL_INFO, {"type": "spotMeta"})
    except Exception as e:
        print(f"hyperliquid fetch failed (fail-soft): {type(e).__name__}: {e}", file=sys.stderr)
        return 0, []
    toks = meta.get("tokens") if isinstance(meta, dict) else None
    if not isinstance(toks, list):
        return 0, []
    qual = []
    for t in toks:
        name = str(t.get("name") or "")
        if not name or name.upper() in _HL_MEME:
            continue
        if _HL_PM.search(name):
            qual.append({
                "id":     name,
                "title":  f"HL spot token: {name}",
                "negRisk": False,
                "group":  None,
                "volume": 0,
                "reason": "hyperliquid PM-pattern token (verify HIP-4 + depth-walk l2Book)",
            })
    return len(toks), qual


# Venues this sentinel watches. Each scan_*() returns (total, qualifying) with the same
# qualifying-dict shape; main() loops over them, namespacing seen-ids by venue.
VENUES = [("limitless", scan_limitless), ("hyperliquid", scan_hyperliquid)]


def _send(body):
    """iMessage via the orphan-watchdog's grant'd context (launchd can't — see watchdog memory).
    Fail-soft: a denied send never breaks the scan."""
    esc = body.replace('"', "'").replace("\n", "\\n")
    for t in TARGETS:
        script = (f'tell application "Messages"\n  set s to 1st service whose service type = iMessage\n'
                  f'  send "{esc}" to buddy "{t}" of s\nend tell')
        try:
            subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=10)
        except Exception:
            pass


def main():
    try:
        st = json.load(open(STATE_FILE)) if os.path.exists(STATE_FILE) else {}
    except Exception:
        st = {}
    seen      = set(st.get("seen", []))
    baselined = set(st.get("baselined_venues", []))
    # Migration / first-run: when no per-venue baseline exists (fresh install OR the pre-Hyperliquid
    # state format that lacks "baselined_venues"), do a SILENT (re)baseline of ALL venues this run —
    # seed `seen` with whatever's live now (venue-namespaced) and alert on NOTHING. This is what makes
    # adding a 2nd venue safe: the live Limitless sentinel can't false-fire on the id-format change,
    # and Hyperliquid's existing universe is absorbed as baseline, not as "emergence".
    migrate = not baselined
    now = datetime.now(timezone.utc).isoformat()

    all_fresh, parts = [], []
    for venue, scan in VENUES:
        total, qual = scan()
        ids   = {f"{venue}:{q['id']}" for q in qual}
        fresh = [] if migrate else [q for q in qual if f"{venue}:{q['id']}" not in seen]
        # append-only history (always, even when quiet — a null is a real datapoint)
        try:
            with open(LOG_FILE, "a") as f:
                f.write(json.dumps({"ts": now, "venue": venue, "total": total,
                                    "qualifying": len(qual), "fresh": len(fresh),
                                    "fresh_titles": [q["title"] for q in fresh]}) + "\n")
        except Exception:
            pass
        seen |= ids
        baselined.add(venue)
        all_fresh += [(venue, q) for q in fresh]
        parts.append(f"{venue} {total}/{len(qual)} qual")

    try:
        json.dump({"seen": sorted(seen), "baselined_venues": sorted(baselined),
                   "updated": now}, open(STATE_FILE, "w"))
    except Exception:
        pass

    if migrate:
        print(f"venue_watch: baselined silently ({'; '.join(parts)}).")
        return 0
    if all_fresh:
        lines = "\n".join(f"  • [{v}] {q['title']} ({q['reason']})" for v, q in all_fresh[:8])
        msg = (f"🆕 VENUE SIGNAL — {len(all_fresh)} new non-coinflip/structural market(s):\n{lines}\n"
               f"If a real negRisk/multi-outcome group → depth-walk the book + point basketlock at it.")
        print(msg)
        _send(msg)
    else:
        print(f"venue_watch: {'; '.join(parts)}, 0 fresh — steady-state (nothing to capture). quiet.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
