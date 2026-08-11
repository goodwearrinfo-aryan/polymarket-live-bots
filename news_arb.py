#!/usr/bin/env python3
"""
news_arb.py — pm-news-arb as a standalone tool on the free LLM.
Given a Polymarket event market, judges whether the news driving it is ALREADY PRICED
(headline/public = no edge) or genuinely UNINCORPORATED (a real fresh edge), with a
timing-vs-resolution check. Python pulls the market + recent relevant headlines from the
keyless hermes RSS feed (:48580); the LLM judges. The efficient-market prior on liquid
markets is strong — the job is to find the rare unpriced fact or honestly say "priced in."
Zero Claude-Code cost. Run: python3 news_arb.py --cid 0x... [--mock]
"""
import os, sys, re, json, datetime, argparse, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import edge_common, llm_client

BASE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(BASE, "news_arb_log.jsonl")
VAULT = os.path.expanduser("~/Documents/PolymarketVault/Reports/news_arb_latest.md")
HERMES = "http://127.0.0.1:48580/api/news"

SHAPE = ('{"already_priced": true|false, "edge": true|false, "side": "YES|NO|none", '
         '"timing_ok": true|false, "confidence": 0-100, '
         '"why": "specific: which fact is/ is not in the price, and the timing vs resolution"}')

SYSTEM = (
    "You judge whether the news around a Polymarket event market is ALREADY PRICED or a fresh, "
    "unincorporated edge. Liquid prediction markets are mostly efficient: a fact that is in the "
    "headlines below is, by definition, public and almost certainly already in the price — that is "
    "NOT an edge. An edge requires a specific fact the market demonstrably has NOT absorbed (a "
    "resolution nuance, a just-broke detail, a misread catalyst), AND time for it to resolve before "
    "the cutoff (timing_ok). Default already_priced=true / edge=false; only flag an edge if you can "
    "name the specific unpriced fact. Use ONLY the provided headlines + criteria; do not invent news."
)


def fetch_news(q, n=8):
    try:
        data = json.loads(urllib.request.urlopen(HERMES, timeout=6).read())
        items = data.get("items", []) if isinstance(data, dict) else (data or [])
    except Exception:
        return [], False
    terms = {w for w in re.findall(r"[a-z]{4,}", q.lower())
             if w not in {"will", "the", "by", "in", "to", "of", "and", "for", "2026", "before"}}
    scored = []
    for it in items:
        t = (it.get("title") or "")
        hits = sum(1 for w in terms if w in t.lower())
        if hits:
            scored.append((hits, f"{t}  [{it.get('source','')}]"))
    scored.sort(reverse=True)
    return [t for _, t in scored[:n]], True


def gather(cid):
    raw = edge_common._get(edge_common.GAMMA, {"condition_ids": cid}) or []
    m = raw[0] if raw else {}
    q = m.get("question", "")
    prices = m.get("outcomePrices"); prices = json.loads(prices) if isinstance(prices, str) else (prices or [])
    heads, feed_ok = fetch_news(q)
    return {"q": q, "market_yes": float(prices[0]) if prices else None,
            "end": (m.get("endDate") or "")[:10],
            "resolution_text": (m.get("description") or "")[:900],
            "headlines": heads, "feed_ok": feed_ok}


def run(cid, mock=False):
    ctx = gather(cid)
    user = (f"MARKET: {ctx['q']}\nMarket YES: {ctx['market_yes']} | resolves {ctx['end']}\n"
            f"RESOLUTION: {ctx['resolution_text']}\n"
            f"RECENT RELEVANT HEADLINES (keyless RSS, last poll):\n- " +
            ("\n- ".join(ctx["headlines"]) if ctx["headlines"] else "(no matching headlines)"))
    if mock:
        verdict = {"already_priced": True, "edge": False, "side": "none", "timing_ok": True,
                   "confidence": 60, "why": "[mock] pipeline test"}
    else:
        try:
            verdict = llm_client.judge(system=SYSTEM, user=user, schema_hint=SHAPE)
        except Exception as e:
            verdict = {"error": str(e), "edge": False}
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    # provenance: which provider judged this — a fallback (weaker local model) is re-checkable
    served = None if mock else getattr(llm_client, "last_served", lambda: None)()
    r = {"ts": ts, "cid": cid, "q": ctx["q"], "market_yes": ctx["market_yes"],
         "n_headlines": len(ctx["headlines"]), "feed_ok": ctx["feed_ok"], "verdict": verdict, "mock": mock,
         "served_by": (served[0] if served else None), "served_fallback": (served[2] if served else None)}
    _persist(r, ctx)
    return r


def _persist(r, ctx):
    try:
        open(LOG, "a").write(json.dumps(r) + "\n")
    except Exception as e:
        print(f"[log FAIL] {e}", file=sys.stderr)
    v = r["verdict"]
    try:
        lines = [f"# News-arb — {ctx['q'][:60]}", "",
                 f"_{r['ts']} · YES {r['market_yes']} · resolves {ctx['end']}_" + ("  _(mock)_" if r["mock"] else ""), "",
                 f"**already_priced: {v.get('already_priced')} · edge: {v.get('edge')} "
                 f"({v.get('side')}) · timing_ok: {v.get('timing_ok')} @{v.get('confidence','?')}**", "",
                 f"**Why:** {v.get('why','')}", "", f"### Headlines used ({r['n_headlines']})"]
        for h in ctx["headlines"]:
            lines.append(f"- {h}")
        if not ctx["headlines"]:
            lines.append("- (none matched — feed_ok=%s)" % r["feed_ok"])
        os.makedirs(os.path.dirname(VAULT), exist_ok=True)
        open(VAULT, "w").write("\n".join(lines) + "\n")
    except Exception as e:
        print(f"[vault FAIL] {e}", file=sys.stderr)


# ─── AUTONOMOUS SCAN ──────────────────────────────────────────────────────────
# news_arb judges ONE market; autonomy needs a driver that PICKS candidates itself.
# Discipline (keeps it cheap + meaningful, not 800 LLM calls/cycle):
#   • liquid only (min_vol) — illiquid markets aren't worth an edge claim;
#   • news must ACTUALLY touch the market (≥2 matching hermes headlines) — no headlines = nothing to (un)price, skip without an LLM call;
#   • not near-certain (0.05<YES<0.95) — a market at 0.99 has no room for a fresh-news edge;
#   • cap N per run so a free-LLM judge call burst stays bounded.
# Alerts ONLY on a real edge (edge=True, timing_ok, confidence≥70), dedup'd by cid.
SCAN_STATE = os.path.join(BASE, "news_arb_scan_state.json")
RECIPIENT = "+918449447444"


def _imessage(msg):
    safe = msg.replace('"', "'").replace("\\", "")
    os.system('osascript -e \'tell application "Messages" to send "%s" to buddy "%s" '
              'of (service 1 whose service type is iMessage)\' 2>/dev/null' % (safe, RECIPIENT))


def scan(n=6, min_vol=100_000, pages=8):
    """Autonomously pick liquid, news-touched, non-certain markets and judge each."""
    try:
        markets = edge_common.poly_markets(pages=pages, per_page=100, min_vol=min_vol)
    except Exception as e:
        print(f"[scan] market fetch failed: {e}", file=sys.stderr); return []
    try:
        seen = json.load(open(SCAN_STATE))
    except Exception:
        seen = {"alerted": {}}
    alerted = seen.get("alerted", {})

    cands, judged, edges = [], [], []
    for m in markets:
        cid = m.get("id")            # poly_markets normalizes conditionId → "id"
        q = m.get("q", "")           # ...and question → "q"
        yes = m.get("yes")
        if not cid or not q:
            continue
        if yes is None or not (0.05 < yes < 0.95):   # near-certain → no fresh-news room
            continue
        heads, feed_ok = fetch_news(q)
        if len(heads) < 2:                            # news doesn't touch it → skip, no LLM call
            continue
        cands.append((cid, q))

    for cid, q in cands[:n]:
        try:
            r = run(cid, mock=False)
        except Exception as e:
            print(f"[scan] judge failed {cid[:10]}: {e}", file=sys.stderr); continue
        judged.append(r)
        v = r["verdict"]
        if v.get("edge") and v.get("timing_ok") and (v.get("confidence") or 0) >= 70:
            edges.append(r)
            if cid not in alerted:                    # dedup: alert each edge once
                _imessage(f"📰 NEWS-ARB edge: {v.get('side')} @{v.get('confidence')} — "
                          f"{q[:60]} (YES {r['market_yes']}). {str(v.get('why',''))[:80]}")
                alerted[cid] = r["ts"]

    seen["alerted"] = alerted
    try:
        json.dump(seen, open(SCAN_STATE, "w"))
    except Exception:
        pass
    print(f"[news_arb scan] {len(cands)} candidates · {len(judged)} judged · {len(edges)} edge(s)")
    for r in edges:
        v = r["verdict"]
        print(f"  ⭐ {v.get('side')} @{v.get('confidence')} — {r['q'][:55]}")
    return edges


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cid"); ap.add_argument("--mock", action="store_true")
    ap.add_argument("--scan", action="store_true", help="autonomous: pick + judge candidates")
    ap.add_argument("--n", type=int, default=6, help="max markets to judge per scan")
    a = ap.parse_args()
    if a.scan:
        if not llm_client.configured():
            print("⚠️ No LLM endpoint — scan skipped (set LLM_* in .env)."); sys.exit(0)
        scan(n=a.n); return
    if not a.cid:
        print("usage: news_arb.py --cid 0x... [--mock]  |  news_arb.py --scan [--n N]"); sys.exit(2)
    if not a.mock and not llm_client.configured():
        print("⚠️ No LLM endpoint (set LLM_* in .env) — or use --mock."); sys.exit(2)
    r = run(a.cid, mock=a.mock)
    v = r["verdict"]
    print(f"\nNEWS-ARB — {r['q'][:55]}  (YES {r['market_yes']})  [{'mock' if r['mock'] else 'live'}]")
    print(f"  headlines matched: {r['n_headlines']} (feed_ok={r['feed_ok']})")
    print(f"  already_priced={v.get('already_priced')} | edge={v.get('edge')} ({v.get('side')}) | "
          f"timing_ok={v.get('timing_ok')} @{v.get('confidence','?')}")
    print(f"  why: {str(v.get('why',''))[:110]}")


if __name__ == "__main__":
    main()
