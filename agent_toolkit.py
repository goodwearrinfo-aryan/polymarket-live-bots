#!/usr/bin/env python3
"""agent_toolkit.py — shared tool definitions + agentic loop for the 3 Claude tool-use agents.

Each tool is a real function that hits live APIs (Gamma, Hermes, CLOB, local state files).
Claude decides which tools to call, in what order, and when it has enough information
to produce a final answer — no fixed call sequence, true agency.

Agents that import this: deep_research_agent, position_defender_agent, edge_hunter_agent.
"""
import os, json, re, time, urllib.request, urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parent
UA   = {"User-Agent": "agent-toolkit/1.0"}

# ── API key ───────────────────────────────────────────────────────────────────

def _load_env():
    for envp in (HERE / ".env", Path.home() / "Documents/futures-bot/.env"):
        if not envp.exists():
            continue
        for line in envp.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

_load_env()


def _http_get(url, params=None, timeout=15):
    if params:
        url += "?" + urllib.parse.urlencode(params)
    try:
        return json.load(urllib.request.urlopen(
            urllib.request.Request(url, headers=UA), timeout=timeout))
    except Exception as e:
        return {"error": str(e)}


def _api_call(messages, tools, system="", model="claude-opus-4-8", max_tokens=800):
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return None
    body = {"model": model, "max_tokens": max_tokens,
            "tools": tools, "messages": messages}
    if system:
        body["system"] = system
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(body).encode(),
        headers={"x-api-key": key, "content-type": "application/json",
                 "anthropic-version": "2023-06-01"})
    try:
        return json.load(urllib.request.urlopen(req, timeout=60))
    except Exception as e:
        return {"error": str(e)}


# ── Tool implementations ──────────────────────────────────────────────────────

def tool_search_news(query: str, max_results: int = 8) -> str:
    """Search the live hermes news feed for headlines matching a query."""
    max_results = min(int(max_results), 20)
    try:
        items = (_http_get("http://127.0.0.1:48580/api/news", timeout=6) or {}).get("items", [])
    except Exception:
        return "hermes unavailable"
    kws = [w.lower() for w in re.findall(r"[a-zA-Z]{3,}", query)]
    matched = [it for it in items
               if any(k in (it.get("title") or "").lower() for k in kws)]
    matched.sort(key=lambda it: it.get("first_seen_utc", ""), reverse=True)
    if not matched:
        return f"no news matching '{query}' in hermes feed ({len(items)} items, 52 sources)"
    heads = "\n".join(
        f"• [{it.get('first_seen_utc','?')[:10]}] {it.get('title','?')[:90]}"
        for it in matched[:max_results])
    return f"{len(matched)} items matching '{query}':\n{heads}"


def tool_fetch_market(query: str) -> str:
    """Fetch market details from Polymarket Gamma by slug/ID/search phrase."""
    results = []
    for q in [f"slug={query}", f"condition_ids={query}",
               f"search={urllib.parse.quote(query)}&limit=3"]:
        d = _http_get(f"https://gamma-api.polymarket.com/markets?{q}")
        if isinstance(d, list) and d:
            results = d; break
    if not results:
        return f"no market found for '{query}'"
    m = results[0]
    prices = m.get("outcomePrices")
    if isinstance(prices, str):
        try:
            prices = json.loads(prices)
        except Exception:
            prices = []
    yes = float(prices[0]) if prices else None
    end = m.get("endDate", "")[:10]
    days = None
    if end:
        try:
            t = time.mktime(time.strptime(end, "%Y-%m-%d"))
            days = max(0, int((t - time.time()) / 86400))
        except Exception:
            pass
    desc = (m.get("description") or "")[:600]
    vol = float(m.get("volumeNum") or 0)
    return (f"Market: {m.get('question','?')}\n"
            f"Slug: {m.get('slug','?')}\n"
            f"YES price: {yes:.3f}" + (f"  (NO: {1-yes:.3f})" if yes else "") + "\n"
            f"Volume: ${vol:,.0f}  Resolves: {end} ({days}d)\n"
            f"Resolution criteria:\n{desc}")


def tool_get_order_book(token_id: str) -> str:
    """Get top-of-book CLOB bid/ask depth for a Polymarket token."""
    d = _http_get(f"https://clob.polymarket.com/book?token_id={token_id}")
    if not d or isinstance(d, dict) and "error" in d:
        return f"CLOB unavailable for {token_id}"
    bids = sorted((d.get("bids") or []), key=lambda x: -float(x.get("price",0)))[:5]
    asks = sorted((d.get("asks") or []), key=lambda x: float(x.get("price",1)))[:5]
    def fmt(side):
        return " | ".join(f"{float(o['price']):.3f}×{float(o['size']):.0f}" for o in side)
    best_bid = float(bids[0]["price"]) if bids else None
    best_ask = float(asks[0]["price"]) if asks else None
    spread = round(best_ask - best_bid, 3) if (best_bid and best_ask) else None
    return (f"CLOB for {token_id[:16]}…\n"
            f"Best bid: {best_bid}  Best ask: {best_ask}  Spread: {spread}\n"
            f"Bids: {fmt(bids)}\nAsks: {fmt(asks)}")


def tool_read_hypothesis_seeds() -> str:
    """Read active hypothesis seeds from hypothesis_lab.py output."""
    sf = HERE / "hypothesis_seeds.json"
    if not sf.exists():
        return "no hypothesis seeds yet — run hypothesis_lab.py first"
    st = json.loads(sf.read_text())
    active = [s for s in st.get("seeds", []) if not s.get("used")]
    if not active:
        return "no active seeds"
    lines = [f"Active hypothesis seeds (run #{st.get('runs',0)}):"]
    for s in active[-8:]:
        lines.append(
            f"[{s['id']}] {s['direction']} | {s['category']} | conf={s.get('confidence','?')}/10\n"
            f"  signal: {s.get('signal','')[:80]}\n"
            f"  query: {s.get('market_search_query','')}\n"
            f"  why: {s.get('why_crowd_wrong','')[:70]}")
    return "\n".join(lines)


def tool_check_open_positions() -> str:
    """Read current open analyst positions and their status."""
    book = HERE / "analyst_positions.json"
    if not book.exists():
        return "no analyst_positions.json"
    positions = json.loads(book.read_text()).get("positions", [])
    if not positions:
        return "no open positions"
    # Enrich with latest monitor data
    monitor_log = HERE / "position_monitor.log"
    latest_action = {}
    if monitor_log.exists():
        for line in open(monitor_log):
            try:
                r = json.loads(line)
                latest_action[r["q"]] = (r.get("action","?"), r.get("curr"), r.get("drift"))
            except Exception:
                continue
    lines = [f"{len(positions)} open positions:"]
    for p in positions:
        q = p.get("q","?")
        side = p.get("side","?")
        entry = p.get("entry_yes", "?")
        resolves = p.get("resolves","?")
        act, curr, drift = latest_action.get(q, ("?", None, None))
        drift_str = f"Δ{drift:+.3f}" if drift is not None else ""
        lines.append(
            f"• {side} '{q[:55]}'\n"
            f"  entry_yes={entry}  curr={curr or '?'} {drift_str}  resolves={resolves}\n"
            f"  monitor={act}  thesis={p.get('thesis','')[:80]}")
    return "\n".join(lines)


def tool_list_markets(topic: str, max_days: int = 60,
                      min_yes: float = 0.04, max_yes: float = 0.96) -> str:
    """List live Polymarket markets matching a topic keyword, sorted by volume."""
    raw = _http_get("https://gamma-api.polymarket.com/markets", {
        "closed": "false", "active": "true",
        "order": "volumeNum", "ascending": "false", "limit": 100})
    if not isinstance(raw, list):
        return "Gamma unavailable"
    kws = [w.lower() for w in re.findall(r"[a-zA-Z]{3,}", topic)]
    results = []
    for m in raw:
        q = (m.get("question") or "").lower()
        if not any(k in q for k in kws):
            continue
        prices = m.get("outcomePrices")
        if isinstance(prices, str):
            try:
                prices = json.loads(prices)
            except Exception:
                continue
        if not prices:
            continue
        yes = float(prices[0])
        if not (min_yes <= yes <= max_yes):
            continue
        end = (m.get("endDate") or "")[:10]
        days = None
        if end:
            try:
                t = time.mktime(time.strptime(end, "%Y-%m-%d"))
                days = max(0, int((t - time.time()) / 86400))
            except Exception:
                pass
        if days is None or days > max_days:
            continue
        vol = float(m.get("volumeNum") or 0)
        results.append((vol, yes, days, m.get("question","?"), m.get("slug","?")))
    results.sort(key=lambda x: -x[0])
    if not results:
        return f"no markets matching '{topic}' with {max_days}d window"
    lines = [f"{len(results)} markets matching '{topic}':"]
    for vol, yes, days, q, slug in results[:12]:
        lines.append(f"• yes={yes:.2f} vol=${vol:,.0f} {days}d [{slug}] {q[:65]}")
    return "\n".join(lines)


# ── Tool registry ─────────────────────────────────────────────────────────────

def tool_scan_orderbooks(topic: str, max_markets: int = 10) -> str:
    """Fetch CLOB bid/ask + spread for multiple markets matching a topic.
    Returns a ranked table: spread, imbalance, depth — sorted widest spread first."""
    max_markets = min(int(max_markets), 20)
    raw = _http_get("https://gamma-api.polymarket.com/markets", {
        "closed": "false", "active": "true",
        "order": "volumeNum", "ascending": "false", "limit": 80})
    if not isinstance(raw, list):
        return "Gamma unavailable"
    kws = [w.lower() for w in re.findall(r"[a-zA-Z]{3,}", topic)]
    rows = []
    for m in raw:
        q = (m.get("question") or "").lower()
        if not any(k in q for k in kws):
            continue
        # extract clobTokenIds
        tids = m.get("clobTokenIds")
        if isinstance(tids, str):
            try: tids = json.loads(tids)
            except Exception: tids = []
        if not tids:
            continue
        yes_tid = tids[0]
        prices = m.get("outcomePrices")
        if isinstance(prices, str):
            try: prices = json.loads(prices)
            except Exception: prices = []
        yes = float(prices[0]) if prices else None
        rows.append((m.get("question","?")[:55], yes, yes_tid, m.get("slug","?")))
        if len(rows) >= max_markets:
            break
    if not rows:
        return f"no CLOB-enabled markets found for '{topic}'"

    results = []
    for q, yes, tid, slug in rows:
        d = _http_get(f"https://clob.polymarket.com/book?token_id={tid}")
        if not d or "error" in d:
            continue
        bids = sorted((d.get("bids") or []), key=lambda x: -float(x.get("price",0)))[:5]
        asks = sorted((d.get("asks") or []), key=lambda x: float(x.get("price",1)))[:5]
        best_bid = float(bids[0]["price"]) if bids else None
        best_ask = float(asks[0]["price"]) if asks else None
        if best_bid is None or best_ask is None:
            continue
        spread = round(best_ask - best_bid, 3)
        bid_depth = sum(float(o.get("size",0)) for o in bids)
        ask_depth = sum(float(o.get("size",0)) for o in asks)
        imbalance = round(bid_depth / ask_depth, 2) if ask_depth > 0 else 99.0
        flag = ""
        if spread > 0.08: flag += "THIN "
        if imbalance > 3.0: flag += "BID_HEAVY "
        if imbalance < 0.33: flag += "ASK_HEAVY "
        results.append((spread, yes, bid_depth, ask_depth, imbalance, flag.strip(), q, slug))

    results.sort(key=lambda x: -x[0])
    lines = [f"CLOB scan for '{topic}' ({len(results)} markets):"]
    lines.append("spread  yes   bid$    ask$    imbal  flags    market")
    for sp, yes, bd, ad, im, fl, q, sl in results:
        lines.append(f"{sp:.3f}   {yes:.2f}  {bd:6.0f}  {ad:6.0f}  {im:5.2f}  {fl:12} {q}")
    return "\n".join(lines)


def tool_news_velocity(query: str, window_hours: int = 6) -> str:
    """Measure news acceleration for a topic: count hermes matches in recent vs baseline windows.
    Returns velocity score and whether coverage is accelerating vs steady vs fading."""
    try:
        items = (_http_get("http://127.0.0.1:48580/api/news", timeout=6) or {}).get("items", [])
    except Exception:
        return "hermes unavailable"
    kws = [w.lower() for w in re.findall(r"[a-zA-Z]{3,}", query)]
    now = time.time()
    matched = []
    for it in items:
        title = (it.get("title") or "").lower()
        if any(k in title for k in kws):
            ts_str = it.get("first_seen_utc","")
            try:
                ts = time.mktime(time.strptime(ts_str[:19], "%Y-%m-%dT%H:%M:%S"))
            except Exception:
                try: ts = time.mktime(time.strptime(ts_str[:10], "%Y-%m-%d"))
                except Exception: ts = now - 86400
            matched.append((ts, it.get("title","?")))

    window_h = max(1, int(window_hours))
    recent = [t for t, _ in matched if now - t <= window_h * 3600]
    older  = [t for t, _ in matched if window_h * 3600 < now - t <= window_h * 4 * 3600]
    baseline_rate = len(older) / (3 * window_h) if older else 0
    recent_rate   = len(recent) / window_h if recent else 0
    accel = round(recent_rate / baseline_rate, 1) if baseline_rate > 0 else (99.0 if recent else 0.0)
    if accel > 2:   trend = "ACCELERATING"
    elif accel > 1: trend = "ELEVATED"
    elif accel > 0: trend = "STEADY"
    else:           trend = "QUIET"
    headlines = "\n".join(f"  [{time.strftime('%H:%M', time.localtime(t))}] {ti[:80]}"
                          for t, ti in sorted(matched, key=lambda x: -x[0])[:5])
    return (f"News velocity for '{query}' ({window_h}h window):\n"
            f"  recent={len(recent)} items/{window_h}h  baseline_rate={baseline_rate:.1f}/h  "
            f"accel={accel}x  trend={trend}\n"
            f"  total_matched={len(matched)} (all time in cache)\n"
            f"Recent headlines:\n{headlines or '  (none)'}")


TOOL_DEFS = [
    {
        "name": "search_news",
        "description": ("Search the live hermes news feed (52 sources, ~800 items, "
                        "continuously updated) for headlines matching keywords. "
                        "Best for: verifying if a thesis topic is currently active in the news."),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "keywords to search"},
                "max_results": {"type": "integer", "description": "max headlines (default 8)"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "fetch_market",
        "description": ("Fetch live market details from Polymarket Gamma: current YES/NO price, "
                        "volume, days to resolution, full resolution criteria text. "
                        "Best for: checking what a market actually resolves on."),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string",
                          "description": "market slug, condition ID, or search phrase"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "get_order_book",
        "description": ("Get top-of-book bid/ask and CLOB depth for a market token. "
                        "Best for: checking real spread cost before claiming an edge is tradeable."),
        "input_schema": {
            "type": "object",
            "properties": {
                "token_id": {"type": "string",
                             "description": "Polymarket condition ID / token ID"}
            },
            "required": ["token_id"]
        }
    },
    {
        "name": "read_hypothesis_seeds",
        "description": ("Read the active edge hypothesis seeds from hypothesis_lab.py. "
                        "Best for: knowing which edge directions to hunt for."),
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "check_open_positions",
        "description": ("Read current open analyst positions with latest monitor status. "
                        "Best for: avoiding duplicate bets, checking what's already in the book."),
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "scan_orderbooks",
        "description": ("Scan CLOB bid/ask + spread + depth imbalance across multiple markets matching a topic. "
                        "Returns a ranked table sorted by spread width. Flags: THIN (spread>8¢), "
                        "BID_HEAVY (bids 3x asks = informed buying), ASK_HEAVY (asks 3x bids = informed selling). "
                        "Best for: detecting informed positioning or illiquidity windows."),
        "input_schema": {
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "keyword to filter markets by"},
                "max_markets": {"type": "integer", "description": "max markets to scan (default 10)"}
            },
            "required": ["topic"]
        }
    },
    {
        "name": "news_velocity",
        "description": ("Measure news acceleration for a topic: count hermes matches in recent vs baseline "
                        "windows. Returns accel multiplier and trend (ACCELERATING/ELEVATED/STEADY/QUIET). "
                        "Best for: detecting a news story gaining momentum before the market re-prices."),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "topic keywords"},
                "window_hours": {"type": "integer",
                                 "description": "recent window size in hours (default 6)"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "list_markets",
        "description": ("List live Polymarket markets filtered by topic keyword and resolution window. "
                        "Best for: discovering markets that match a hypothesis or news theme."),
        "input_schema": {
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "topic/keyword to filter by"},
                "max_days": {"type": "integer",
                             "description": "max days to resolution (default 60)"},
                "min_yes": {"type": "number", "description": "min YES price (default 0.04)"},
                "max_yes": {"type": "number", "description": "max YES price (default 0.96)"}
            },
            "required": ["topic"]
        }
    }
]

TOOL_FNS = {
    "search_news":          lambda inp: tool_search_news(**inp),
    "fetch_market":         lambda inp: tool_fetch_market(**inp),
    "get_order_book":       lambda inp: tool_get_order_book(**inp),
    "read_hypothesis_seeds":lambda inp: tool_read_hypothesis_seeds(),
    "check_open_positions": lambda inp: tool_check_open_positions(),
    "list_markets":         lambda inp: tool_list_markets(**inp),
    "scan_orderbooks":      lambda inp: tool_scan_orderbooks(**inp),
    "news_velocity":        lambda inp: tool_news_velocity(**inp),
}


# ── Agentic loop ──────────────────────────────────────────────────────────────

def run_agent(system: str, user: str,
              model: str = "claude-opus-4-8",
              max_tokens: int = 900,
              max_iters: int = 8,
              tools: list = None,
              verbose: bool = True) -> str:
    """Standard agentic tool-use loop.

    Claude can call tools in any order until stop_reason=end_turn.
    Returns the final text response. Prints tool call trace when verbose=True.
    """
    if tools is None:
        tools = TOOL_DEFS
    messages = [{"role": "user", "content": user}]
    for i in range(max_iters):
        r = _api_call(messages, tools, system=system, model=model, max_tokens=max_tokens)
        if not r or "error" in r:
            return f"[agent error: {r}]"
        stop = r.get("stop_reason")
        content = r.get("content", [])
        if stop == "end_turn":
            return next((b["text"] for b in content if b.get("type") == "text"), "")
        if stop != "tool_use":
            # max_tokens hit mid-response — return what we have
            text = next((b["text"] for b in content if b.get("type") == "text"), "")
            return text or f"[stopped: {stop}]"
        # Execute all tool calls in this turn
        tool_results = []
        for block in content:
            if block.get("type") != "tool_use":
                continue
            name = block["name"]
            inp  = block.get("input", {})
            fn   = TOOL_FNS.get(name)
            if verbose:
                print(f"    [tool:{name}] {json.dumps(inp)[:80]}")
            result = fn(inp) if fn else f"unknown tool: {name}"
            if verbose:
                print(f"    → {str(result)[:120]}")
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block["id"],
                "content": str(result)[:2000]
            })
        messages.append({"role": "assistant", "content": content})
        messages.append({"role": "user",      "content": tool_results})
    return "[agent: max iterations reached]"
