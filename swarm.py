#!/usr/bin/env python3
"""
swarm.py — run the info_bot engine over a LIST of topics in one pass and emit a single
consolidated markdown brief. This is the "swarm of bots that give info 24/7" as a schedulable
unit (NOT scheduled here — wiring it into launchd/cron is a separate, deliberate step that
needs sign-off, per the project's autonomy discipline).

Topics come from CLI args (one quoted topic each) or a newline-delimited --file.
Output goes to --out PATH, else stdout.

  python3 swarm.py "bitcoin" "ethereum" "us fed 2026"
  python3 swarm.py --file topics.txt --out /tmp/morning_brief.md

Open-weight LLM chain + keyless-first data; fail-soft per topic (one bad brief never
sinks the run); paper/research only — reads public data, never trades.
"""
import sys, os, json, hashlib, datetime, subprocess, time, urllib.request, urllib.parse
import info_bot

# ---- scheduled-mode config (the launchd "24/7 swarm" unit) ----
TARGETS = ["krisharyan@icloud.com", "+918449447444"]   # same handles as the rest of the fleet
TAVILY_TTL = 6 * 3600        # cache web search 6h -> ~4 refreshes/topic/day -> fits 1k/mo free
DESK_TTL = 6 * 3600          # cache Tier-2 desk data 6h -> respects BLS 25/day + spares APIs
STATE_DIR = os.path.expanduser("~/.info_swarm")
SIGNAL_FILE = os.path.join(STATE_DIR, "last_signals.json")   # Tier-4 actionable-signal dedup
VAULT_LIVE = os.path.expanduser("~/Documents/PolymarketVault/Reports/Info-Swarm-Live.md")

# ---- Tier 3 (edge radar) + Tier 4 (actionable alerting) ----
BW_STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "basket_watch_state.json")
GAMMA = ("https://gamma-api.polymarket.com/markets"
         "?order=createdAt&ascending=false&limit=100&active=true&closed=false")
GAMMA_TRENDING = ("https://gamma-api.polymarket.com/markets"
                  "?order=volume24hr&ascending=false&limit=50&active=true&closed=false")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
SENTIMENT_ALERT = 0.5    # |Marketaux entity sentiment| above this = an actionable swing
LOCK_SHOW_EDGE = 0.01    # show structural-arb leads above this edge in the radar
LOCK_ALERT_EDGE = 0.05   # only PAGE on a lead this strong (most are partial-field fakes -> don't cry wolf)

# all 4 domains -> concrete queries
# (label, web-search query, enrichment opts passed to info_bot.brief)
TOPICS = [
    ("Crypto",             "bitcoin ethereum solana crypto market price and news outlook today",
        {"finance": True, "news_query": "cryptocurrency bitcoin ethereum", "crypto_desk": True}),
    ("Prediction markets", "polymarket kalshi prediction market volume and trending markets this week",
        {}),
    ("Macro & markets",    "us federal reserve interest rate decision and stock market outlook",
        {"finance": True, "news_query": "federal reserve interest rates stock market",
         "tickers": ["SPY", "QQQ", "AAPL", "NVDA"], "macro_desk": True}),
    ("Geopolitics",        "strait of hormuz middle east shipping and oil supply disruption risk",
        {"geo_desk": True}),
]

_OSA = ('on run argv\ntell application "Messages"\nset s to 1st service whose service type = iMessage\n'
        'send (item 2 of argv) to buddy (item 1 of argv) of s\nend tell\nend run')


def send_imessage(body):
    """argv-safe osascript send (avoids the os.system quoting bug); to the fleet's own handles."""
    ok = True
    for t in TARGETS:
        try:
            r = subprocess.run(["osascript", "-e", _OSA, t, body], capture_output=True, text=True, timeout=30)
            ok = ok and r.returncode == 0
        except Exception:
            ok = False
    return ok


def _load_state():
    try:
        with open(SIGNAL_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_state(d):
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = SIGNAL_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(d, f)
    os.replace(tmp, SIGNAL_FILE)   # atomic


def _edge_radar():
    """Tier-3 READ-ONLY edge radar: structural-arb leads (from the basket-watch job's fresh
    state — never runs the live scan/booking) + new/trending Polymarket markets (keyless gamma)."""
    out = {"locks": [], "fresh": [], "trending": [], "lock_stale": False}
    try:
        out["lock_stale"] = (time.time() - os.path.getmtime(BW_STATE)) > 3600
        bw = json.load(open(BW_STATE))
        leads = sorted(((slug, v.get("edge", 0)) for slug, v in bw.items()
                        if isinstance(v, dict) and (v.get("edge") or 0) >= LOCK_SHOW_EDGE),
                       key=lambda t: t[1], reverse=True)
        out["locks"] = leads[:5]
    except Exception as e:
        out["locks_err"] = str(e)[:80]
    def _vol(m):
        try:
            return float(m.get("volume24hr") or m.get("volume") or 0)
        except Exception:
            return 0.0
    def _mill(q):
        return "up or down" in (q or "").lower()   # 5-min candle micro-market mill = noise
    def _fetch(url):
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.load(r)
    try:  # fresh = newest listings (createdAt desc), minus the candle mill
        out["fresh"] = [(m.get("question") or m.get("slug") or "?", _vol(m))
                        for m in _fetch(GAMMA) if not _mill(m.get("question"))][:5]
    except Exception as e:
        out["markets_err"] = str(e)[:80]
    try:  # trending = highest 24h volume (separate query), minus the mill
        trend = [(m.get("question") or m.get("slug") or "?", _vol(m))
                 for m in _fetch(GAMMA_TRENDING) if not _mill(m.get("question"))]
        out["trending"] = sorted(trend, key=lambda t: t[1], reverse=True)[:5]
    except Exception as e:
        out["trending_err"] = str(e)[:80]
    return out


def scheduled_run():
    """launchd entrypoint: brief all TOPICS (cached), run the Tier-3 edge radar, write the vault
    note every run, and Tier-4 alert ONLY on actionable signals (sentiment swing / strong arb
    lead / first run), deduped so a persistent signal doesn't re-ping each cycle."""
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    last = _load_state()
    last_sig = set(last.get("signals", []))
    first_run = "signals" not in last
    briefs, served, signals, alerts = [], {}, [], []
    md = [f"# Info swarm — live ({stamp})",
          "_open-weight LLM chain; Tavily/Marketaux cached; keyless data + edge radar each run_", ""]
    for label, query, opts in TOPICS:
        b = info_bot.brief(query, tavily_ttl=TAVILY_TTL, desk_ttl=DESK_TTL, **opts)
        briefs.append((label, b))
        prov = b.get("provenance", {})
        served[prov.get("served_by")] = served.get(prov.get("served_by"), 0) + 1
        ms = b.get("max_sentiment")          # Tier-4 trigger: a strong sentiment swing
        if ms and abs(ms.get("score", 0)) >= SENTIMENT_ALERT:
            key = f"sent:{label}:{ms['entity']}:{round(ms['score'], 1)}"
            signals.append(key)
            if key not in last_sig:
                alerts.append(f"⚡ {label}: {ms['entity']} sentiment {ms['score']:+.2f}")
        body = "\n".join(info_bot._render(b).split("\n")[1:])   # drop the engine's own H1
        md += [f"## {label}", body, ""]

    # ── Tier-3 edge radar ──
    radar = _edge_radar()
    md += ["## Edge radar", ""]
    if radar.get("locks"):
        md.append("**Structural-arb leads** (basket-watch edge — VERIFY net-of-fee + full-field; "
                  "most are partial-field fakes):")
        for slug, edge in radar["locks"]:
            md.append(f"- {slug} — edge {edge:.3f}")
        top_slug, top_edge = radar["locks"][0]                 # Tier-4: page ONLY the single strongest lead
        if top_edge >= LOCK_ALERT_EDGE:
            key = f"lock:{top_slug}"
            signals.append(key)
            if key not in last_sig:
                alerts.append(f"🔒 arb LEAD (verify): {top_slug} edge {top_edge:.3f}")
    else:
        md.append("_no structural-arb leads above threshold_"
                  + (" — ⚠️ basket-watch state STALE" if radar.get("lock_stale") else ""))
    if radar.get("trending"):
        md += ["", "**Trending Polymarket markets (24h volume):**"]
        md += [f"- {q} — ${v:,.0f}" for q, v in radar["trending"]]
    if radar.get("fresh"):
        md += ["", "**Freshly listed (mostly thin AMM seeds):**"]
        md += [f"- {q}" for q, v in radar["fresh"]]
    md.append("")

    # vault note every run (quiet), atomic
    try:
        os.makedirs(os.path.dirname(VAULT_LIVE), exist_ok=True)
        fm = (f"---\ntype: report\nstatus: active\ndate: {datetime.datetime.now().strftime('%Y-%m-%d')}"
              f"\nsource: swarm.py --scheduled\n---\n\n")
        tmp = VAULT_LIVE + ".tmp"
        with open(tmp, "w") as f:
            f.write(fm + "\n".join(md))
        os.replace(tmp, VAULT_LIVE)
    except Exception as e:
        print(f"[swarm] vault write failed: {e}", file=sys.stderr)

    # ── Tier-4 alerting: actionable-only, deduped ──
    if first_run:
        sent = send_imessage(f"🛰️ Info-swarm ONLINE — {stamp}\n{len(TOPICS)} topics + edge radar live. "
                             "Alerts now fire only on sentiment swings & strong arb leads.")
        print(f"[swarm] iMessage {'sent' if sent else 'FAILED'} [online]")
    elif alerts:
        sent = send_imessage(f"🛰️ Info-swarm — {stamp}\n" + "\n".join(f"• {a}" for a in alerts[:8]))
        print(f"[swarm] iMessage {'sent' if sent else 'FAILED'} ({len(alerts)} actionable signal(s))")
    else:
        print("[swarm] no actionable signal -> no iMessage")

    _save_state({"signals": signals})
    print(f"[swarm] {stamp} done: {len(TOPICS)} briefs, served={served}, "
          f"radar_locks={len(radar.get('locks', []))}, alerts={len(alerts)}")


def _topics_from_args(argv):
    topics, out, fpath = [], None, None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--out":
            i += 1; out = argv[i] if i < len(argv) else None
        elif a == "--file":
            i += 1; fpath = argv[i] if i < len(argv) else None
        elif not a.startswith("-"):
            topics.append(a)
        i += 1
    if fpath:
        try:
            with open(fpath) as f:
                topics += [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]
        except FileNotFoundError:
            print(f"[swarm] --file not found: {fpath}", file=sys.stderr)
    return topics, out


def run(topics):
    """Brief every topic; return (markdown, stats)."""
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    parts = [f"# Info swarm brief — {stamp}", f"_{len(topics)} topics on the free open-weight stack_", ""]
    served, degraded, failed = {}, [], 0
    for topic in topics:
        try:
            b = info_bot.brief(topic)
        except Exception as e:
            failed += 1
            parts += [f"## {topic}", f"⚠️ brief crashed: {type(e).__name__}: {e}", ""]
            continue
        if not b.get("ok"):
            failed += 1
        prov = b.get("provenance", {})
        lbl = prov.get("served_by")
        served[lbl] = served.get(lbl, 0) + 1
        if b.get("degraded"):
            degraded += b["degraded"]
        parts.append(info_bot._render(b).replace("# Info brief:", "##", 1))
        parts.append("")
    footer = [f"served_by tally: {served}", f"topics failed: {failed}/{len(topics)}"]
    if degraded:
        footer.append(f"degraded sources: {'; '.join(sorted(set(degraded)))}")
    parts += ["---", "  \n".join(footer)]
    return "\n".join(parts), {"served": served, "failed": failed, "n": len(topics)}


if __name__ == "__main__":
    if "--scheduled" in sys.argv:
        scheduled_run(); sys.exit(0)
    topics, out = _topics_from_args(sys.argv[1:])
    if not topics:
        print(__doc__); sys.exit(0)
    md, stats = run(topics)
    if out:
        with open(out, "w") as f:
            f.write(md)
        print(f"[swarm] wrote {stats['n']} briefs -> {out}  (served={stats['served']}, failed={stats['failed']})")
    else:
        print(md)
