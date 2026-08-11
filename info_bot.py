#!/usr/bin/env python3
"""
info_bot.py — one "strong info bot": a topic -> a structured, source-grounded info brief,
built entirely on the live FREE stack wired 2026-06-22 (open-weight LLM only, keyless-first).

This is the reusable engine for the "swarm of bots that give info 24/7": run it per-topic
(on a schedule — NOT auto-scheduled here; scheduling is a separate, deliberate step that
needs sign-off). Paper/research only: it reads public data and never trades.

Pipeline:
  1. GATHER (fail-soft, each source optional):
       - Tavily AI web search          (keyed_sources.tavily_search)   -> recent web context
       - crypto spot + 24h change      (keyed_sources.coingecko_price) -> if topic is crypto
         (works keyless; demo key only raises limits)
  2. SYNTHESIZE with the open-weight LLM failover chain (llm_client.judge):
       groq -> cerebras -> sambanova -> nvidia -> public -> ollama
       -> {tldr, key_facts[], what_changed, what_to_watch[], confidence}, grounded ONLY
          in the gathered context (told not to invent).
  3. HONEST PROVENANCE: stamps which LLM tier served (served_fallback flags a weaker
     backup) and which sources were actually used; if web search was unavailable it says so
     rather than returning a silently-thin brief ("silence != success").

Usage:
  python3 info_bot.py "will the fed cut rates in 2026"
  python3 info_bot.py "bitcoin" --json
  import info_bot; b = info_bot.brief("strait of hormuz closure")
"""
import sys, json, os, time, hashlib
import llm_client as L          # seeds .env, gives the open-weight chain + provenance
import keyed_sources as K       # Tavily (live) + CoinGecko (keyless fallback)
import desk_data as DK          # Tier-2 keyless desk enrichers (crypto / macro / geo)

# Tavily file cache: scheduled runs reuse a topic's web results within a TTL so a frequent
# cadence (e.g. every 30 min) doesn't blow the 1k/mo free quota. ttl=0 -> always live.
_CACHE_ROOT = os.path.expanduser("~/.info_swarm")


def _cached(ns, key, ttl, fetch):
    """Generic file cache for scheduled runs: reuse a fetch's result within ttl seconds so a
    frequent cadence doesn't blow a provider's free quota. ttl<=0 -> always live. Atomic write,
    and only successful ({ok:True}) results are cached."""
    if ttl <= 0:
        return fetch()
    fp = None
    try:
        d = os.path.join(_CACHE_ROOT, ns)
        os.makedirs(d, exist_ok=True)
        fp = os.path.join(d, hashlib.sha1(key.encode()).hexdigest()[:16] + ".json")
        if os.path.exists(fp) and (time.time() - os.path.getmtime(fp)) < ttl:
            with open(fp) as f:
                out = json.load(f)
            out["_cached"] = True
            return out
    except Exception:
        fp = None
    out = fetch()
    if isinstance(out, dict) and out.get("ok") and fp:
        try:  # atomic: tmp + os.replace (state-atomicity)
            tmp = fp + ".tmp"
            with open(tmp, "w") as f:
                json.dump(out, f)
            os.replace(tmp, fp)
        except Exception:
            pass
    return out


def _tavily_cached(query, max_results, ttl):
    return _cached("tavily", f"{query}|{max_results}", ttl,
                   lambda: K.tavily_search(query, max_results=max_results))

# minimal crypto detector -> CoinGecko id (extend as needed)
_CRYPTO = {
    "btc": "bitcoin", "bitcoin": "bitcoin", "eth": "ethereum", "ethereum": "ethereum",
    "sol": "solana", "solana": "solana", "xrp": "ripple", "doge": "dogecoin",
    "bnb": "binancecoin", "ada": "cardano", "crypto": "bitcoin",
}


def _detect_crypto(topic):
    toks = {t.strip(".,?!()").lower() for t in topic.split()}
    ids, seen = [], set()
    for t in toks:
        cid = _CRYPTO.get(t)
        if cid and cid not in seen:
            seen.add(cid)
            ids.append(cid)
    return ids


def _gather(topic, max_web=5, tavily_ttl=0, finance=False, tickers=None, news_query=None, news_ttl=6 * 3600,
            crypto_desk=False, macro_desk=False, geo_desk=False, desk_ttl=0):
    """Pull context from the live sources. Each source is fail-soft and self-reports.
    finance=True adds Marketaux news+sentiment (cached news_ttl to respect 100/day);
    tickers=[...] adds live Finnhub stock quotes;
    crypto_desk/macro_desk/geo_desk add the Tier-2 keyless desk-read enrichers (desk_data)."""
    ctx = {"web": [], "crypto": None, "quotes": {}, "news": [], "sentiments": [],
           "sources_used": [], "degraded": [], "web_urls": []}

    # web search (Tavily; cached within tavily_ttl seconds for scheduled runs)
    t = _tavily_cached(topic, max_web, tavily_ttl)
    if t.get("ok"):
        for r in t.get("results", []):
            ctx["web"].append({"title": r.get("title"), "url": r.get("url"),
                               "snippet": (r.get("content") or "")[:400]})
            if r.get("url"):
                ctx["web_urls"].append(r["url"])
        ctx["sources_used"].append(f"tavily({len(ctx['web'])}{',cached' if t.get('_cached') else ''})")
        if t.get("answer"):
            ctx["web"].insert(0, {"title": "Tavily answer", "url": None, "snippet": t["answer"][:600]})
    else:
        ctx["degraded"].append(f"web_search_unavailable: {t.get('error')}")

    # crypto spot (only if the topic looks crypto)
    ids = _detect_crypto(topic)
    if ids:
        c = K.coingecko_price(ids)
        if c.get("ok"):
            ctx["crypto"] = c.get("prices")
            tag = "coingecko" + ("" if c.get("configured") else "(keyless)")
            ctx["sources_used"].append(tag)
        else:
            ctx["degraded"].append(f"crypto_price_unavailable: {c.get('error')}")

    # Finnhub live stock quotes for explicit tickers (fresh; 60/min free is ample)
    for sym in (tickers or []):
        q = K.finnhub_quote(sym)
        if q.get("ok") and q.get("quote", {}).get("c"):
            ctx["quotes"][sym] = q["quote"]
        elif q.get("configured") is False:
            ctx["degraded"].append("finnhub: no key"); break
    if ctx["quotes"]:
        ctx["sources_used"].append(f"finnhub({len(ctx['quotes'])})")

    # Marketaux finance news + entity sentiment (cached to respect the 100/day quota)
    if finance:
        mq = news_query or topic
        m = _cached("marketaux", f"search:{mq}", news_ttl, lambda: K.marketaux_news(search=mq, limit=5))
        if m.get("ok"):
            for a in m.get("data", [])[:5]:
                senti = []
                for e in a.get("entities", []):
                    sc = e.get("sentiment_score")
                    if sc is not None:
                        ent = e.get("symbol") or e.get("name")
                        senti.append(f"{ent}:{sc}")
                        ctx["sentiments"].append((ent, sc))
                ctx["news"].append({"title": a.get("title"), "sentiment": senti[:3], "url": a.get("url")})
            if ctx["news"]:
                ctx["sources_used"].append(f"marketaux({len(ctx['news'])}{',cached' if m.get('_cached') else ''})")
        elif m.get("configured"):
            ctx["degraded"].append(f"marketaux: {m.get('error')}")

    # Tier-2 desk enrichers (keyless): deepen crypto/macro/geo beyond price+news -> a desk read
    DK.gather(ctx, crypto=crypto_desk, macro=macro_desk, geo=geo_desk, ttl=desk_ttl)
    return ctx


_SCHEMA = (
    '{"tldr": "one-sentence summary grounded in the context",'
    ' "key_facts": ["specific fact with a source url or number", "..."],'
    ' "what_changed": "what is new/recent, or \'nothing material\'",'
    ' "what_to_watch": ["upcoming catalyst / risk", "..."],'
    ' "confidence": "low|medium|high"}'
)


def _pct(v):
    return f"{v:+.2f}%" if isinstance(v, (int, float)) else "n/a"


def _context_block(topic, ctx):
    lines = [f"TOPIC: {topic}", ""]
    if ctx.get("crypto"):
        lines.append("LIVE PRICE DATA (CoinGecko):")
        for cid, d in ctx["crypto"].items():
            lines.append(f"  - {cid}: ${d.get('usd')} (24h {_pct(d.get('usd_24h_change'))})")
        lines.append("")
    if ctx.get("quotes"):
        lines.append("LIVE STOCK QUOTES (Finnhub):")
        for sym, q in ctx["quotes"].items():
            lines.append(f"  - {sym}: ${q.get('c')} ({_pct(q.get('dp'))})")
        lines.append("")
    if ctx.get("news"):
        lines.append("FINANCE NEWS + SENTIMENT (Marketaux, score -1..+1):")
        for n in ctx["news"]:
            s = f"  [sentiment {', '.join(n['sentiment'])}]" if n.get("sentiment") else ""
            lines.append(f"  - {n.get('title')}{s}")
        lines.append("")
    if ctx.get("web"):
        lines.append("WEB SEARCH RESULTS:")
        for i, w in enumerate(ctx["web"], 1):
            src = f" [{w['url']}]" if w.get("url") else ""
            lines.append(f"  {i}. {w.get('title')}{src}\n     {w.get('snippet')}")
    lines += DK.render(ctx)   # Tier-2 desk sections (only those present)
    if not any([ctx.get("web"), ctx.get("crypto"), ctx.get("quotes"), ctx.get("news"),
                ctx.get("crypto_desk"), ctx.get("macro_desk"), ctx.get("geo_desk")]):
        lines.append("(no external context could be gathered)")
    return "\n".join(lines)


def brief(topic, max_web=5, tavily_ttl=0, finance=False, tickers=None, news_query=None,
          crypto_desk=False, macro_desk=False, geo_desk=False, desk_ttl=0):
    """Return a structured, source-grounded brief dict for `topic` (+ honest provenance).
    finance/tickers/news_query enable the Finnhub + Marketaux enrichment layer;
    crypto_desk/macro_desk/geo_desk add the Tier-2 keyless desk-read enrichers."""
    ctx = _gather(topic, max_web=max_web, tavily_ttl=tavily_ttl,
                  finance=finance, tickers=tickers, news_query=news_query,
                  crypto_desk=crypto_desk, macro_desk=macro_desk, geo_desk=geo_desk, desk_ttl=desk_ttl)
    system = (
        "You are a precise research analyst. Using ONLY the context provided, write a tight "
        "info brief. Do NOT invent facts not supported by the context; if the context is thin, "
        "say so and set confidence low. Prefer concrete numbers and cite source URLs inline."
    )
    user = _context_block(topic, ctx)
    _sm = max(ctx.get("sentiments", []), key=lambda t: abs(t[1]), default=None)
    out = {"topic": topic, "sources_used": ctx["sources_used"], "degraded": ctx["degraded"],
           "max_sentiment": ({"entity": _sm[0], "score": _sm[1]} if _sm else None),
           "web_urls": ctx["web_urls"] + [n["url"] for n in ctx.get("news", []) if n.get("url")]}
    try:
        result = L.judge(system, user, schema_hint=_SCHEMA)
        out.update(result)
        out["ok"] = True
    except Exception as e:
        out["ok"] = False
        out["error"] = f"synthesis failed: {type(e).__name__}: {e}"
    out["provenance"] = L.provenance()
    return out


def _render(b):
    p = b.get("provenance", {})
    lines = [f"# Info brief: {b['topic']}", ""]
    if not b.get("ok"):
        lines.append(f"⚠️ {b.get('error')}")
    else:
        lines.append(f"**TL;DR** {b.get('tldr','')}")
        lines.append(f"\n**What changed:** {b.get('what_changed','')}")
        if b.get("key_facts"):
            lines.append("\n**Key facts:**")
            lines += [f"- {f}" for f in b["key_facts"]]
        if b.get("what_to_watch"):
            lines.append("\n**What to watch:**")
            lines += [f"- {w}" for w in b["what_to_watch"]]
        lines.append(f"\n**Confidence:** {b.get('confidence','?')}")
    lines.append("\n---")
    lines.append(f"sources: {', '.join(b.get('sources_used') or ['none'])}"
                 + (f" | DEGRADED: {'; '.join(b['degraded'])}" if b.get("degraded") else ""))
    lines.append(f"served_by: {p.get('served_by')} ({p.get('served_model')})"
                 + ("  ⚠️ fallback (weaker model)" if p.get("served_fallback") else ""))
    return "\n".join(lines)


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    as_json = "--json" in sys.argv
    if not args:
        print(__doc__)
        sys.exit(0)
    b = brief(" ".join(args))
    print(json.dumps(b, indent=2) if as_json else _render(b))
