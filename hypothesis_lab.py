#!/usr/bin/env python3
"""hypothesis_lab.py — weekly AI agent that synthesizes patterns across the entire bot
into fresh, specific edge hypotheses, then seeds market_scout.py for the next cycle.

Reads (all read-only):
  - analyst_verdicts.csv       what passed/failed the 4-6 lens gate and WHY
  - analyst_positions.json     current open book (avoid duplicating)
  - scout_candidates.json      what scout has already proposed
  - edge_screen_logger output  bootstrap-CI screen across legs
  - position_monitor.log       which positions generated INVESTIGATE/EXIT alerts
  - hermes feed (live)         current news cycle topics

Runs an opus research agent that:
  1. Reviews the evidence above
  2. Generates 5 SPECIFIC hypothesis seeds — each a concrete bet direction on a
     particular event-type/category with a stated signal
  3. Each seed includes: category, signal, direction, why_crowd_wrong, market_search_query

Seeds → hypothesis_seeds.json (market_scout reads these on next run to weight its
pre-screen in favour of hypothesis-consistent markets).

Usage:
  python3 hypothesis_lab.py          # full run (calls opus once, ~10¢)
  python3 hypothesis_lab.py --show   # show existing seeds
  python3 hypothesis_lab.py --feed   # feed seeds to market_scout (print matches)
"""
import os, sys, json, csv, time, argparse, urllib.request
from pathlib import Path

HERE  = Path(__file__).resolve().parent
SEEDS = HERE / "hypothesis_seeds.json"
LOG   = HERE / "hypothesis_lab.log"

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


def _call_opus(prompt, max_tokens=900):
    if not KEY:
        return None
    body = json.dumps({
        "model": "claude-opus-4-8", "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}]
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=body,
        headers={"x-api-key": KEY, "content-type": "application/json",
                 "anthropic-version": "2023-06-01"})
    try:
        r = json.load(urllib.request.urlopen(req, timeout=60))
        return r["content"][0]["text"].strip() if "content" in r else None
    except Exception as e:
        print(f"  API error: {e}"); return None


# ── Evidence collectors ────────────────────────────────────────────────────────

def _verdicts_summary():
    vf = HERE / "analyst_verdicts.csv"
    if not vf.exists():
        return "no verdicts yet"
    rows = list(csv.DictReader(open(vf)))
    passes = [r for r in rows if r.get("verdict") == "PASS"]
    fails  = [r for r in rows if r.get("verdict") == "FAIL"]
    # Collect refusal reasons: which lens most often refutes
    lens_cols = [c for c in (rows[0].keys() if rows else []) if c.endswith("_refuted")]
    lens_totals = {c: sum(int(r.get(c,0) or 0) for r in rows) for c in lens_cols}
    top_lens = sorted(lens_totals, key=lambda c: -lens_totals[c])[:3]
    pass_claims = "; ".join(r.get("claim","")[:50] for r in passes[-3:]) or "none"
    fail_claims = "; ".join(r.get("claim","")[:50] for r in fails[-3:]) or "none"
    return (f"{len(passes)} PASSed / {len(fails)} FAILed. "
            f"Most-refuted lenses: {', '.join(top_lens)}. "
            f"Recent PASSes: {pass_claims}. Recent FAILs: {fail_claims}.")


def _open_positions():
    book = HERE / "analyst_positions.json"
    if not book.exists():
        return "none"
    ps = json.loads(book.read_text()).get("positions", [])
    return "; ".join(f"{p.get('side')} {p.get('q','')[:40]}" for p in ps) or "none"


def _scout_summary():
    sc = HERE / "scout_candidates.json"
    if not sc.exists():
        return "no candidates yet"
    cands = json.loads(sc.read_text()).get("candidates", [])
    promoted = [c for c in cands if c.get("promoted")]
    pending  = [c for c in cands if not c.get("promoted") and c.get("draft")]
    top = sorted(pending, key=lambda c: -(c.get("draft",{}) or {}).get("edge_pct",0))[:3]
    top_str = "; ".join(f"{(c.get('draft',{}) or {}).get('side','?')} {c['q'][:35]}" for c in top)
    return f"{len(cands)} total, {len(promoted)} promoted, {len(pending)} pending. Top: {top_str}"


def _monitor_alerts():
    mlog = HERE / "position_monitor.log"
    if not mlog.exists():
        return "no monitor data"
    rows = []
    for line in open(mlog):
        try:
            r = json.loads(line)
            if r.get("action") in ("INVESTIGATE","EXIT"):
                rows.append(r)
        except Exception:
            continue
    if not rows:
        return "no INVESTIGATE/EXIT alerts"
    return "; ".join(f"{r['action']} {r['side']} {r['q'][:35]}" for r in rows[-5:])


def _hermes_topics():
    try:
        items = json.load(urllib.request.urlopen(
            urllib.request.Request("http://127.0.0.1:48580/api/news",
                                   headers={"User-Agent":"hyp-lab/1.0"}), timeout=6)).get("items",[])
        from collections import Counter
        import re
        words = []
        for it in items[:100]:
            words += re.findall(r"[A-Z][a-z]{3,}", it.get("title",""))
        top = [w for w,_ in Counter(words).most_common(12)]
        return ", ".join(top)
    except Exception:
        return "hermes unavailable"


# ── Hypothesis generation ─────────────────────────────────────────────────────

LAB_PROMPT = """You are a prediction-market research strategist. Your job is to generate SPECIFIC, TESTABLE edge hypotheses for the next scouting cycle.

CONTEXT (what we know):
Gate verdicts: {verdicts}
Open positions: {positions}
Scout pipeline: {scout}
Monitor alerts: {alerts}
Current news cycle (top proper nouns from 52-source feed): {hermes}

RULES for a good hypothesis:
1. Must be SPECIFIC: name a market type, event category, or resolution trigger — NOT "political events in general"
2. Must have a SIGNAL: name what information asymmetry creates the edge (insider info, base-rate blindness, resolution-criteria ambiguity, market overreaction to news)
3. Must be HUNTABLE: a market_scout can find matching Polymarket markets today (7-60 day resolution window)
4. Must NOT duplicate: avoid the open positions and concepts already in scout pipeline

Generate EXACTLY 5 hypothesis seeds. Output as a JSON array:
[
  {{
    "id": "hyp-001",
    "category": "<category: geopolitics|sports|crypto|finance|science|other>",
    "direction": "YES overpriced" or "NO overpriced",
    "signal": "<the specific information asymmetry — what does the crowd get wrong?>",
    "market_search_query": "<3-6 word query to find matching Polymarket markets>",
    "why_crowd_wrong": "<1 sentence on the base-rate or resolution mechanism mistake>",
    "confidence": <1-10>
  }},
  ...
]

Output ONLY the JSON array, no other text."""


def generate_seeds(max_tokens=1500):
    evidence = {
        "verdicts": _verdicts_summary(),
        "positions": _open_positions(),
        "scout": _scout_summary(),
        "alerts": _monitor_alerts(),
        "hermes": _hermes_topics(),
    }
    print("  Evidence collected:")
    for k, v in evidence.items():
        print(f"    {k}: {v[:80]}")
    print("  Calling opus for hypothesis generation…")
    resp = _call_opus(LAB_PROMPT.format(**evidence), max_tokens=max_tokens)
    if not resp:
        print("  API error"); return None
    import re
    m = re.search(r'\[.*\]', resp, re.DOTALL)
    if not m:
        print(f"  Bad response format: {resp[:100]}"); return None
    try:
        seeds = json.loads(m.group())
    except json.JSONDecodeError as e:
        print(f"  JSON parse error: {e}"); return None
    return seeds


# ── State management ──────────────────────────────────────────────────────────

def _load_seeds():
    if SEEDS.exists():
        try:
            return json.loads(SEEDS.read_text())
        except Exception:
            pass
    return {"seeds": [], "generated_at": None, "runs": 0}


def _save_seeds(st):
    tmp = SEEDS.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=2))
    tmp.replace(SEEDS)


# ── Main ──────────────────────────────────────────────────────────────────────

def run():
    if not KEY:
        print("ANTHROPIC_API_KEY required"); sys.exit(1)
    print(f"\n[hypothesis_lab] generating fresh edge hypotheses…")
    seeds = generate_seeds(max_tokens=1500)
    if not seeds:
        return
    st = _load_seeds()
    # assign sequential IDs, keep history
    existing_ids = {s["id"] for s in st["seeds"]}
    run_n = st["runs"] + 1
    for i, seed in enumerate(seeds):
        seed["id"] = f"hyp-{run_n:02d}-{i+1:02d}"
        seed["generated_at"] = time.strftime("%Y-%m-%d")
        seed["used"] = False
    st["seeds"].extend(seeds)
    st["generated_at"] = time.strftime("%Y-%m-%d %H:%MZ", time.gmtime())
    st["runs"] = run_n
    _save_seeds(st)
    # log
    with open(LOG, "a") as f:
        f.write(json.dumps({"ts": st["generated_at"], "run": run_n, "n": len(seeds)}) + "\n")
    print(f"\n  Generated {len(seeds)} seeds (run #{run_n}) → {SEEDS}")
    for s in seeds:
        conf = s.get("confidence","?")
        print(f"  [{s['id']}] {s['direction']:20} cat={s['category']:12} conf={conf}/10")
        print(f"    signal: {s.get('signal','')[:70]}")
        print(f"    query: {s.get('market_search_query','')}")


def show():
    st = _load_seeds()
    seeds = [s for s in st.get("seeds",[]) if not s.get("used")]
    print(f"\n  Hypothesis lab — {len(seeds)} active seeds (run #{st.get('runs',0)})\n")
    for s in seeds[-10:]:
        conf = s.get("confidence","?")
        print(f"  [{s['id']}] {s['direction']:20} {s['category']:12} conf={conf}/10")
        print(f"    signal:  {s.get('signal','')[:75]}")
        print(f"    query:   {s.get('market_search_query','')}")
        print(f"    why:     {s.get('why_crowd_wrong','')[:75]}")
        print()


def feed_to_scout():
    """Print hypothesis-driven search terms for market_scout to prioritise."""
    st = _load_seeds()
    active = [s for s in st.get("seeds",[]) if not s.get("used")]
    if not active:
        print("no active seeds — run hypothesis_lab.py first"); return
    print("\n  Hypothesis-driven scout queries:")
    for s in active[-5:]:
        print(f"  [{s['id']}] {s['market_search_query']}  ({s['direction']})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--feed", action="store_true", help="print scout queries from seeds")
    a = ap.parse_args()
    if a.show:
        show(); return
    if a.feed:
        feed_to_scout(); return
    run()


if __name__ == "__main__":
    main()
