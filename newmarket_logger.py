#!/usr/bin/env python3
"""
newmarket_logger.py — forward-only RESEARCH logger for the new-market timing vein.

WHY: 2026-06-21 we reverse-engineered the keyless gamma query that actually
returns freshly-created markets (order=createdAt&ascending=false; the default
id-ascending sort had masked them). A one-shot scan found the fresh end is
either (a) the auto-minted crypto Up/Down coinflip mill, or (b) genuinely-novel
markets that are zero-volume AMM seeds with wide synthetic spreads = no
counterparty to fade. So the timing window is EMPTY at t=0 — but does it ever
OPEN as a novel market accumulates real two-sided depth in its first hours?
This logger accumulates the evidence to answer that, forward-only.

WHAT IT IS: an append-only JSONL logger, like candle_knowledge_logger /
newsmove. NOT a trading leg. It never trades, never sizes, paper-only,
read-only against keyless gamma. It tracks each NOVEL (non-mill) fresh market
over time and snapshots its executable spread, so later analysis can ask:
"how long after creation does a novel market reach a tradeable spread, and is
there a window where the quote is stale/mispriced before the bots arrive?"

HONEST PRIOR (do not forget): the strong expectation is that the window never
opens executably — by the time depth arrives, makers/arb bots have priced it.
A 'tradeable' flag here is a LEAD to verify on the live book, never an edge.

Output:  ~/Documents/polymarket/newmarket_log.jsonl   (append-only snapshots)
State:   ~/Documents/polymarket/newmarket_state.json  (tracked condition ids)
"""
import errno, json, os, time, subprocess, urllib.request, urllib.error

# leads must be LOUD — a novel tradeable market is the signal this logger exists
# to catch, and it usually fires while Aryan is offline. Dedup'd per conditionId
# in state so the same market never re-alerts.
ALERT_TARGETS = ("krisharyan@icloud.com", "+918449447444")


def alert(msg):
    subprocess.run(["osascript", "-e",
                    f'display notification "{msg}" with title "Polymarket new-market LEAD"'],
                   capture_output=True)
    for t in ALERT_TARGETS:
        subprocess.run(["osascript", "-e",
                        f'tell application "Messages" to send "{msg}" to buddy "{t}" '
                        'of (service 1 whose service type is iMessage)'],
                       capture_output=True)

GAMMA = ("https://gamma-api.polymarket.com/markets"
         "?order=createdAt&ascending=false&limit=100&active=true&closed=false")
HOME = os.path.expanduser("~")
BASE = os.path.join(HOME, "Documents", "polymarket")
LOG = os.path.join(BASE, "newmarket_log.jsonl")


def _append(rec):
    """Append one JSONL record, resilient to EDEADLK — the log dir is shared into a
    VM/Docker guest + iCloud Drive, either of which can briefly lock the file. Retry a
    few times, then fail-soft (skip the write rather than crash the whole logger)."""
    line = json.dumps(rec) + "\n"
    for _ in range(4):
        try:
            with open(LOG, "a") as f:
                f.write(line)
            return
        except OSError as e:
            if e.errno != errno.EDEADLK:
                raise
            time.sleep(0.25)
STATE = os.path.join(BASE, "newmarket_state.json")

# A novel market stays "tracked" for this long after creation; after that the
# timing window is long gone and we stop snapshotting it (keeps log bounded).
TRACK_WINDOW_H = 48.0
# tradeable-lead heuristic: two-sided quote, tight spread, real liquidity.
TIGHT_SPREAD = 0.05
MIN_LIQ = 500.0

# the coinflip mill: auto-minted short-dated crypto Up/Down series. Excluded —
# already-traded (coinup/coindown/lateprox) and calibrated, not novel edge.
MILL_SLUG_HINTS = ("updown", "up-or-down", "-up-down-", "hourly")
MILL_Q_HINTS = ("up or down",)

# auto-generated sports player-/game-prop series. NOT excluded (still logged),
# but tagged so the genuinely-novel signal isn't drowned by prop noise — these
# are also zero-vol AMM walls. Heuristic keyword lists (observed: cs2, fifwc,
# soccer, mlb, nba...); extend as new league prefixes show up.
SPORTS_SLUG_HINTS = ("cs2-", "fifwc-", "nba-", "mlb-", "nfl-", "nhl-", "epl-",
                     "ucl-", "soccer-", "lol-", "dota-", "valorant-", "tennis-",
                     "atp-", "wta-", "itf-", "ncaab-", "ncaaf-", "f1-", "ufc-",
                     "mls-", "laliga-", "seriea-", "bundesliga-", "ligue1-")
SPORTS_PROP_TOKENS = ("-shots-", "-gte", "-o-u-", "-spread-", "-total-",
                      "-btts", "-corners", "-cards", "-assists", "-goals-",
                      "-points-", "-rebounds-", "-saves-")


def classify(m):
    """mill | sports_prop | novel — mill is excluded, the rest are logged tagged."""
    slug = (m.get("slug") or "").lower()
    q = (m.get("question") or "").lower()
    if any(h in slug for h in MILL_SLUG_HINTS) or any(h in q for h in MILL_Q_HINTS):
        return "mill"
    if any(slug.startswith(h) or h in slug for h in SPORTS_SLUG_HINTS) \
       or any(t in slug for t in SPORTS_PROP_TOKENS):
        return "sports_prop"
    return "novel"


def fget(m, *keys):
    """first present, float-coerced; None on miss/garbage."""
    for k in keys:
        v = m.get(k)
        if v in (None, "", "null"):
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return None


def fetch():
    req = urllib.request.Request(GAMMA, headers={"User-Agent": "newmarket-logger/1.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def load_state():
    try:
        with open(STATE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(s):
    tmp = STATE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(s, f)
    os.replace(tmp, STATE)  # atomic; no half-written state


def main():
    now = time.time()
    try:
        markets = fetch()
    except (urllib.error.URLError, ValueError, TimeoutError) as e:
        # fail-soft: one bad poll must not crash the loop or pollute the log
        _append({"ts": now, "event": "fetch_error", "err": str(e)[:200]})
        return

    state = load_state()
    snaps = 0
    novel_seen = 0
    prop_seen = 0
    for m in markets:
        kind = classify(m)
        if kind == "mill":
            continue
        if kind == "sports_prop":
            prop_seen += 1
        else:
            novel_seen += 1
        cid = m.get("conditionId")
        if not cid:
            continue
        created = m.get("createdAt", "")
        # age from createdAt (ISO Z) — fall back to first-seen if unparseable
        age_h = None
        try:
            t = time.mktime(time.strptime(created[:19], "%Y-%m-%dT%H:%M:%S"))
            # strptime treats as local; gamma is UTC. correct with tz offset.
            t -= time.timezone
            age_h = (now - t) / 3600.0
        except (ValueError, TypeError):
            pass

        st = state.get(cid)
        if st is None:
            st = {"first_seen": now, "created": created, "slug": m.get("slug"),
                  "question": m.get("question")}
            state[cid] = st
        if age_h is None:
            age_h = (now - st["first_seen"]) / 3600.0

        if age_h > TRACK_WINDOW_H:
            continue  # past the timing window; stop snapshotting

        bid = fget(m, "bestBid")
        ask = fget(m, "bestAsk")
        spread = fget(m, "spread")
        liq = fget(m, "liquidity", "liquidityNum", "liquidityClob")
        if spread is None and bid is not None and ask is not None:
            spread = ask - bid
        prices = m.get("outcomePrices")
        tradeable = bool(
            bid and ask and spread is not None and spread <= TIGHT_SPREAD
            and (liq or 0) >= MIN_LIQ
        )

        rec = {
            "ts": round(now, 1), "cid": cid, "slug": m.get("slug"),
            "question": (m.get("question") or "")[:120],
            "age_h": round(age_h, 3), "bestBid": bid, "bestAsk": ask,
            "spread": round(spread, 4) if spread is not None else None,
            "liquidity": round(liq, 1) if liq is not None else None,
            "outcomePrices": prices, "endDate": m.get("endDate"),
            "kind": kind, "tradeable_lead": tradeable,
        }
        _append(rec)
        snaps += 1
        # surface a LEAD only for genuinely-novel markets — a tight spread on a
        # sports prop is noise, not the timing-vein signal we're hunting.
        if tradeable and kind == "novel":
            line = (f"LEAD tradeable novel fresh market: {rec['question']!r} "
                    f"age={age_h:.1f}h spread={spread} liq={liq} cid={cid}")
            print(line)
            # wire LOUD, once per market: notify + iMessage, then mark in state
            if not st.get("alerted"):
                alert(f"New-market LEAD: {rec['question'][:80]} "
                      f"spread={spread} liq={liq} (age {age_h:.1f}h) — VERIFY on live book")
                st["alerted"] = True

    # prune state entries we no longer see AND that are past the window
    live = {m.get("conditionId") for m in markets}
    for cid in list(state):
        st = state[cid]
        if cid not in live and (now - st["first_seen"]) / 3600.0 > TRACK_WINDOW_H:
            del state[cid]
    save_state(state)

    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} polled {len(markets)} | "
          f"novel {novel_seen} | sports_prop {prop_seen} | "
          f"snapshots {snaps} | tracking {len(state)}")


if __name__ == "__main__":
    main()
