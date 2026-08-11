#!/usr/bin/env python3
"""market_scout.py — autonomous AI research agent that continuously scans Polymarket
for high-quality analyst candidates and drafts them for review.

Two-stage pipeline per candidate (cheap → expensive):
  Stage 1 haiku pre-screen (~0.1¢): is this market verifiable + edge-plausible?
  Stage 2 opus draft      (~4¢):    full position draft (side/true_prob/thesis/sources)

The agent is PROPOSE-ONLY — it writes to scout_candidates.json.
Aryan promotes candidates into analyst_positions.json for the gate.

Usage:
  python3 market_scout.py             # full scan (verbose)
  python3 market_scout.py --once      # quiet watchdog mode
  python3 market_scout.py --show      # show existing candidates
  python3 market_scout.py --promote N # print N-th candidate ready for analyst_positions.json
"""
import os, sys, json, time, re, argparse, urllib.request, urllib.parse, math
from pathlib import Path

HERE = Path(__file__).resolve().parent
CANDIDATES = HERE / "scout_candidates.json"

MAX_PRESCREEN = 25   # haiku calls per run (cheap)
MAX_DRAFT     = 5    # opus calls per run (expensive — draft only best ones)
MIN_VOL       = 8_000
MIN_DAYS      = 7
MAX_DAYS      = 60
MIN_YES       = 0.04   # don't bother below 4¢ — too thin
MAX_YES       = 0.96

UA = {"User-Agent": "market-scout/1.0"}

# ── API helpers ──────────────────────────────────────────────────────────────

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
KEY = os.environ.get("ANTHROPIC_API_KEY")


def _get(url, params=None, timeout=20):
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=UA)
    try:
        return json.load(urllib.request.urlopen(req, timeout=timeout))
    except Exception:
        return None


def _call(model, prompt, max_tokens=60):
    if not KEY:
        return None
    body = json.dumps({
        "model": model, "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}]
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=body,
        headers={"x-api-key": KEY, "content-type": "application/json",
                 "anthropic-version": "2023-06-01"})
    try:
        r = json.load(urllib.request.urlopen(req, timeout=45))
        return r["content"][0]["text"].strip() if "content" in r else None
    except Exception:
        return None


# ── Market fetching ───────────────────────────────────────────────────────────

def _days_to(end_str):
    if not end_str:
        return None
    s = end_str[:19].replace("T", " ")   # "2026-07-20 00:00:00"
    try:
        return max(0, int((time.mktime(time.strptime(s, "%Y-%m-%d %H:%M:%S")) - time.time()) / 86400))
    except Exception:
        pass
    try:
        return max(0, int((time.mktime(time.strptime(end_str[:10], "%Y-%m-%d")) - time.time()) / 86400))
    except Exception:
        return None


def fetch_markets():
    """Pull open binary markets sorted by volume, return filtered list."""
    out, seen = [], set()
    for page in range(6):
        raw = _get("https://gamma-api.polymarket.com/markets", {
            "closed": "false", "active": "true",
            "order": "volumeNum", "ascending": "false",
            "limit": 100, "offset": page * 100,
        }) or []
        if not raw:
            break
        for m in raw:
            try:
                cid = str(m.get("conditionId") or m.get("id") or "")
                if not cid or cid in seen:
                    continue
                prices = m.get("outcomePrices")
                if isinstance(prices, str):
                    prices = json.loads(prices)
                if not prices or len(prices) < 2:
                    continue
                vol = float(m.get("volumeNum") or 0)
                if vol < MIN_VOL:
                    continue
                yes = float(prices[0])
                if not (MIN_YES <= yes <= MAX_YES):
                    continue
                days = _days_to(m.get("endDate"))
                if days is None or not (MIN_DAYS <= days <= MAX_DAYS):
                    continue
                seen.add(cid)
                out.append({
                    "market_id": cid,
                    "slug": m.get("slug") or "",
                    "q": (m.get("question") or "")[:120],
                    "yes": yes,
                    "vol": vol,
                    "liquidity": float(m.get("liquidityNum") or 0),
                    "days": days,
                    "end": m.get("endDate", "")[:10],
                })
            except Exception:
                continue
        if len(raw) < 100:
            break
    return out


def fetch_description(slug, market_id):
    """Gamma description (the resolution criteria) for one market."""
    for q in ([f"slug={slug}"] if slug else []) + ([f"condition_ids={market_id}"] if market_id else []):
        d = _get(f"https://gamma-api.polymarket.com/markets?{q}")
        if isinstance(d, list) and d and d[0].get("description"):
            return d[0]["description"].strip()[:1500]
    return None


# ── State helpers ─────────────────────────────────────────────────────────────

def _load():
    if CANDIDATES.exists():
        try:
            return json.loads(CANDIDATES.read_text())
        except Exception:
            pass
    return {"candidates": [], "last_scan": None}


def _save(st):
    tmp = CANDIDATES.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=2))
    tmp.replace(CANDIDATES)


def _known_slugs(st):
    """Market IDs + slugs already in candidates OR analyst_positions.json."""
    seen = set()
    for c in st.get("candidates", []):
        seen.add(c.get("market_id", ""))
        seen.add(c.get("slug", ""))
    book = HERE / "analyst_positions.json"
    if book.exists():
        try:
            for p in json.loads(book.read_text()).get("positions", []):
                seen.add(p.get("market_id", ""))
                seen.add(p.get("slug", ""))
        except Exception:
            pass
    seen.discard("")
    return seen


# ── Stage 1: haiku pre-screen ─────────────────────────────────────────────────

PRESCREEN_PROMPT = """You are a prediction-market analyst screening a market for research value.

Market: {q}
YES price: {yes:.2f}  Volume: ${vol:,.0f}  Resolves in: {days}d ({end})
Resolution criteria (excerpt):
\"\"\"{desc}\"\"\"

PASS if ALL of:
1. Resolution is VERIFIABLE and UNAMBIGUOUS (a clear binary outcome with a named source/oracle)
2. Probability range suggests the outcome is genuinely uncertain (not near 5% or 95%)
3. A well-sourced analyst COULD have an edge over the market price (event-driven information asymmetry)

FAIL if ANY of:
- Resolution mechanism is vague, subjective, or relies on Polymarket admin discretion
- Market is a pure prediction race (who can model faster) with no info edge
- Criteria unavailable or too brief to evaluate

Output EXACTLY one line: PASS or FAIL, then a ≤15-word reason.
Example: PASS — verifiable FIFA referee ruling, crowd likely unaware of rule nuance"""


def prescreen(m, desc):
    prompt = PRESCREEN_PROMPT.format(
        q=m["q"], yes=m["yes"], vol=m["vol"], days=m["days"],
        end=m["end"], desc=(desc or "UNAVAILABLE")[:600])
    r = _call("claude-haiku-4-5-20251001", prompt, max_tokens=50)
    if not r:
        return False, "API error"
    upper = r.upper()
    passed = upper.startswith("PASS") or (upper[:8].count("PASS") > 0)
    reason = r.split("—", 1)[-1].strip() if "—" in r else r[5:].strip()
    return passed, reason[:100]


# ── Stage 2: opus draft ───────────────────────────────────────────────────────

DRAFT_PROMPT = """You are a research analyst drafting an investment thesis for a prediction-market position.

Market: {q}
YES price: {yes:.2f}  Volume: ${vol:,.0f}  Resolves: {end} ({days}d)
Resolution criteria:
\"\"\"{desc}\"\"\"

Draft a short, specific thesis for the MORE INTERESTING side (YES or NO — whichever has a genuine edge opportunity given what you know about this topic).

Output EXACTLY this JSON (no other text):
{{
  "side": "YES" or "NO",
  "true_prob": <your estimate 0.01-0.99>,
  "edge_pct": <abs(true_prob - market_yes) * 100 rounded to int>,
  "confidence": <1-10>,
  "risk": "Low" or "Medium" or "High",
  "thesis": "<2-3 sentences: what the crowd is missing, why your side wins>",
  "sources_to_check": ["<specific search query or URL to verify thesis 1>", "<source 2>", "<source 3>"]
}}"""


def draft_position(m, desc):
    prompt = DRAFT_PROMPT.format(
        q=m["q"], yes=m["yes"], vol=m["vol"],
        end=m["end"], days=m["days"], desc=(desc or "UNAVAILABLE")[:800])
    r = _call("claude-opus-4-8", prompt, max_tokens=400)
    if not r:
        return None
    try:
        # extract JSON block from response
        match = re.search(r'\{.*\}', r, re.DOTALL)
        if match:
            d = json.loads(match.group())
            d["entry_yes"] = round(m["yes"], 3)
            d["resolves"] = m["end"]
            return d
    except Exception:
        pass
    return None


# ── Stage 3: haiku quick-gate on the draft ───────────────────────────────────

QUICK_GATE_PROMPT = """You are an adversarial prediction-market analyst. A research agent just drafted a position — check if it holds up against the three most common failure modes.

Market: {q}  Resolves: {end}
Draft position: {side} at YES price {entry_yes:.2f}
Claimed true probability: {true_prob}  Edge: {edge_pct}pts
Thesis: {thesis}
Resolution criteria: {desc}

Three failure modes (any one FAILs the draft):
1. RESOLUTION TRAP: Does the resolution criteria actually support the claimed winning condition? (touch-vs-close, ambiguous language, admin discretion)
2. CROWD-RIGHT: Is the crowd probably CORRECT and the stated edge illusory? (public information already priced, pure modeling race)
3. BASE-RATE MISMATCH: Is the claimed true_prob extraordinary vs base rates for this event class without sufficient specific evidence?

Output EXACTLY one line: PASS or FAIL, then ≤12-word reason covering the most critical concern."""


def quick_gate(m, desc, d):
    """Haiku quick-gate after opus draft. Returns (passed: bool, reason: str)."""
    if not d:
        return False, "no draft"
    prompt = QUICK_GATE_PROMPT.format(
        q=m["q"], end=m["end"],
        side=d.get("side","?"), entry_yes=m["yes"],
        true_prob=d.get("true_prob","?"), edge_pct=d.get("edge_pct","?"),
        thesis=d.get("thesis","")[:200], desc=(desc or "UNAVAILABLE")[:400])
    r = _call("claude-haiku-4-5-20251001", prompt, max_tokens=50)
    if not r:
        return False, "API error"
    upper = r.upper()
    passed = upper.startswith("PASS") or (upper[:8].count("PASS") > 0)
    reason = r.split("—", 1)[-1].strip() if "—" in r else r[5:].strip()
    return passed, reason[:100]


# ── iMessage digest ───────────────────────────────────────────────────────────

def _imessage(msg, recipient="+918449447444"):
    script = f'tell application "Messages" to send "{msg}" to buddy "{recipient}" of (service 1 whose service type is iMessage)'
    os.system(f"osascript -e '{script}' 2>/dev/null")


def _send_digest(new_candidates):
    if not new_candidates:
        return
    lines = [f"📡 Scout found {len(new_candidates)} new candidate(s):"]
    for c in new_candidates[:3]:
        d = c.get("draft") or {}
        side = d.get("side", "?")
        edge = d.get("edge_pct", "?")
        conf = d.get("confidence", "?")
        lines.append(f"• {side} {c['q'][:50]}… edge={edge}pts conf={conf}/10 {c['days']}d")
    lines.append("Run: python3 market_scout.py --show")
    _imessage("\n".join(lines))


# ── Main ──────────────────────────────────────────────────────────────────────

def scan(verbose=True):
    if not KEY:
        print("ANTHROPIC_API_KEY required"); sys.exit(1)
    st = _load()
    known = _known_slugs(st)

    markets = fetch_markets()
    fresh = [m for m in markets if m["market_id"] not in known and m["slug"] not in known]
    if verbose:
        print(f"[scout] {len(markets)} markets fetched → {len(fresh)} new after dedup")

    prescreened, drafted = 0, 0
    new_candidates = []

    for m in fresh:
        if prescreened >= MAX_PRESCREEN:
            break
        desc = fetch_description(m["slug"], m["market_id"])
        passed, reason = prescreen(m, desc)
        prescreened += 1
        if verbose:
            mark = "✅" if passed else "❌"
            print(f"  {mark} PRESCREEN  {m['yes']:.2f} {m['days']}d  {m['q'][:55]}  — {reason}")
        if not passed:
            continue
        if drafted >= MAX_DRAFT:
            # record as prescreened-pass with no draft (next run will draft)
            rec = {**m, "ts": int(time.time()), "prescreen": "PASS",
                   "prescreen_reason": reason, "draft": None, "promoted": False}
            st["candidates"].append(rec)
            new_candidates.append(rec)
            continue
        d = draft_position(m, desc)
        drafted += 1
        # Stage 3: haiku quick-gate — kills drafts that fail the 3 basic checks
        gpass, greason = quick_gate(m, desc, d)
        if d:
            d["gate_pass"] = gpass
            d["gate_reason"] = greason
        rec = {**m, "ts": int(time.time()), "prescreen": "PASS",
               "prescreen_reason": reason, "draft": d,
               "gate_pass": gpass, "promoted": False}
        st["candidates"].append(rec)
        new_candidates.append(rec)
        if verbose and d:
            gmark = "✅" if gpass else "❌"
            print(f"     → draft: {d.get('side')} true_prob={d.get('true_prob')} "
                  f"edge={d.get('edge_pct')}pts conf={d.get('confidence')}/10")
            print(f"     → gate3: {gmark} {greason}")
            print(f"       thesis: {d.get('thesis','?')[:90]}")

    st["last_scan"] = int(time.time())
    _save(st)
    ts = time.strftime("%Y-%m-%d %H:%MZ", time.gmtime())
    total = len(st["candidates"])
    undrafted = sum(1 for c in st["candidates"] if not c.get("draft"))
    print(f"[{ts}] scout: prescreened={prescreened} drafted={drafted} "
          f"new={len(new_candidates)} total={total} undrafted={undrafted} → {CANDIDATES}")
    gate_passes = [c for c in new_candidates if c.get("gate_pass")]
    if gate_passes:
        _send_digest(gate_passes)
    elif new_candidates and verbose:
        print(f"  (no gate-passing candidates — {len(new_candidates)} drafted but all failed quick-gate)")
    return new_candidates


def show_candidates(limit=20):
    st = _load()
    cands = sorted(st.get("candidates", []), key=lambda c: -(c.get("draft", {}) or {}).get("edge_pct", 0))
    promoted = sum(1 for c in cands if c.get("promoted"))
    print(f"\n  Scout candidates: {len(cands)} total, {promoted} promoted\n")
    for i, c in enumerate(cands[:limit]):
        d = c.get("draft") or {}
        if c.get("promoted"):
            mark = "✅"
        elif not d:
            mark = "🔍"
        elif d.get("gate_pass"):
            mark = "🟢"
        else:
            mark = "🟡"
        gate_str = f"[gate:{'✓' if d.get('gate_pass') else '✗'}]" if d else ""
        print(f"  [{i}] {mark} {d.get('side','?'):3}  yes={c['yes']:.2f}  edge={d.get('edge_pct','?'):>3}pts  "
              f"conf={d.get('confidence','?')}/10  {c['days']}d {gate_str}  {c['q'][:48]}")
        if d.get("thesis"):
            print(f"       {d['thesis'][:90]}")


def promote_candidate(n):
    """Print candidate N as a JSON block ready to paste into analyst_positions.json."""
    st = _load()
    cands = sorted(st.get("candidates", []), key=lambda c: -(c.get("draft", {}) or {}).get("edge_pct", 0))
    if n >= len(cands):
        print(f"only {len(cands)} candidates"); return
    c = cands[n]
    d = c.get("draft") or {}
    pos = {
        "market_id": c["market_id"],
        "slug": c["slug"],
        "q": c["q"],
        "side": d.get("side", "?"),
        "entry_yes": c["yes"],
        "entry_price": round(1 - c["yes"], 3) if d.get("side") == "NO" else c["yes"],
        "true_prob": d.get("true_prob", 0.5),
        "edge_pct": d.get("edge_pct", 0),
        "confidence": d.get("confidence", 5),
        "risk": d.get("risk", "Medium"),
        "resolves": d.get("resolves", c["end"]),
        "opened_at": time.strftime("%Y-%m-%d"),
        "thesis": d.get("thesis", ""),
        "sources": d.get("sources_to_check", []),
    }
    print("\n# Paste this into analyst_positions.json → positions array:")
    print(json.dumps(pos, indent=2))
    # mark as promoted in state
    for orig in st["candidates"]:
        if orig["market_id"] == c["market_id"]:
            orig["promoted"] = True
    _save(st)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="quiet watchdog mode")
    ap.add_argument("--show", action="store_true", help="show existing candidates")
    ap.add_argument("--promote", type=int, metavar="N", help="print candidate N as position JSON")
    a = ap.parse_args()
    if a.show:
        show_candidates(); return
    if a.promote is not None:
        promote_candidate(a.promote); return
    scan(verbose=not a.once)


if __name__ == "__main__":
    main()
