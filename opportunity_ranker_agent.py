#!/usr/bin/env python3
"""opportunity_ranker_agent.py — daily cross-agent synthesis and priority ranking.

Reads ALL agent output files and synthesizes them into ONE ranked priority dossier:
  - scout_candidates.json     (pre-screened markets)
  - deep_research_reports.json (tool-use deep dives)
  - edge_hunter_dossiers.json  (hypothesis-driven finds)
  - thesis_critic_verdicts.json (pre-entry quality gate)
  - breaking_news_signals.json (real-time news gaps)
  - position_monitor.log       (live position health)
  - analyst_positions.json     (open book context)

Claude synthesizes everything into a TOP-5 priority list with concrete next steps.
No tool-use needed (all data is local); uses opus for synthesis quality.

Runs daily; output in opportunity_rankings.json + Obsidian vault.

Usage:
  python3 opportunity_ranker_agent.py        # rank now
  python3 opportunity_ranker_agent.py --once # quiet watchdog mode
  python3 opportunity_ranker_agent.py --show # show latest ranking
"""
import os, json, time, argparse, urllib.request
from pathlib import Path
from datetime import datetime, timezone

HERE     = Path(__file__).resolve().parent
RANKINGS = HERE / "opportunity_rankings.json"


def _api_call(prompt: str, model: str = "claude-opus-4-8",
              max_tokens: int = 1600) -> str:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        _load_env()
        key = os.environ.get("ANTHROPIC_API_KEY","")
    body = {"model": model, "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}]}
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(body).encode(),
        headers={"x-api-key": key, "content-type": "application/json",
                 "anthropic-version": "2023-06-01"})
    try:
        r = json.load(urllib.request.urlopen(req, timeout=90))
        return next((b["text"] for b in r.get("content",[]) if b.get("type")=="text"), "")
    except Exception as e:
        return f"[api error: {e}]"


def _load_env():
    for p in (HERE / ".env", Path.home() / "Documents/futures-bot/.env"):
        if not p.exists(): continue
        for line in p.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line: continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _read_json(path, default):
    try:
        return json.loads(Path(path).read_text()) if Path(path).exists() else default
    except Exception:
        return default


def _read_jsonl_latest(path, key="q", n=20):
    """Read last n rows from a jsonl log, dedupe by key (latest wins)."""
    if not Path(path).exists():
        return {}
    rows = {}
    for line in open(path):
        try:
            r = json.loads(line)
            rows[r.get(key,"?")] = r
        except Exception:
            continue
    return rows


def _gather_context() -> str:
    """Collect all agent outputs into a structured text block for synthesis."""
    parts = []

    # Scout candidates
    sc = _read_json(HERE / "scout_candidates.json", {})
    gate_pass = [c for c in sc.get("candidates",[]) if c.get("gate_pass") and not c.get("promoted")]
    if gate_pass:
        parts.append(f"SCOUT GATE-PASSING CANDIDATES ({len(gate_pass)}):")
        for c in gate_pass[:6]:
            d = c.get("draft") or {}
            parts.append(f"  [{c.get('yes',0):.2f}] {d.get('side','?')} '{c.get('q','?')[:55]}' "
                         f"edge={d.get('edge_pct','?')}pts conf={d.get('confidence','?')}/10")
            if d.get("thesis"):
                parts.append(f"    {d['thesis'][:80]}")

    # Deep research PURSUEs
    dr = _read_json(HERE / "deep_research_reports.json", {})
    pursues = [r for r in dr.get("reports",[]) if r.get("action") == "PURSUE"]
    if pursues:
        parts.append(f"\nDEEP RESEARCH PURSUES ({len(pursues)}):")
        for r in pursues[-5:]:
            parts.append(f"  [{r.get('ts','')}] conf={r.get('confidence','?')} "
                         f"edge={r.get('edge','?')} '{r.get('query','?')[:55]}'")
            if r.get("thesis"):
                parts.append(f"    {r['thesis'][:80]}")

    # Edge hunter dossiers
    eh = _read_json(HERE / "edge_hunter_dossiers.json", {})
    dossiers = [d for d in eh.get("dossiers",[]) if d.get("signal_confirmed") in ("YES","PARTIAL")]
    if dossiers:
        parts.append(f"\nEDGE HUNTER DOSSIERS (signal confirmed) ({len(dossiers)}):")
        for d in dossiers[-5:]:
            parts.append(f"  [{d.get('seed_id','?')}] {d.get('side','?')} edge={d.get('edge','?')} "
                         f"sig={d.get('signal_confirmed','?')} risk={d.get('risk','?')} "
                         f"'{d.get('market','?')[:50]}'")

    # Thesis critic verdicts
    tc = _read_json(HERE / "thesis_critic_verdicts.json", {})
    promotes = [v for v in tc.get("verdicts",[]) if v.get("overall") == "PROMOTE"]
    kills = [v for v in tc.get("verdicts",[]) if v.get("overall") == "KILL"]
    if promotes:
        parts.append(f"\nTHESIS CRITIC — PROMOTE ({len(promotes)}):")
        for v in promotes[-5:]:
            parts.append(f"  {v.get('side','?')} '{v.get('q','?')[:55]}' "
                         f"fatal_flaw={v.get('fatal_flaw','none')[:50]}")
    if kills:
        parts.append(f"\nTHESIS CRITIC — KILL ({len(kills)}) [DO NOT ENTER]:")
        for v in kills[-5:]:
            parts.append(f"  {v.get('side','?')} '{v.get('q','?')[:55]}' — {v.get('fatal_flaw','?')[:60]}")

    # Breaking news signals
    bn = _read_json(HERE / "breaking_news_signals.json", {})
    hot = [s for s in bn.get("signals",[]) if s.get("urgency") in ("CRITICAL","HIGH")]
    if hot:
        recent = [s for s in hot if s.get("ts","") >= time.strftime("%Y-%m-%d", time.gmtime())]
        parts.append(f"\nBREAKING NEWS SIGNALS TODAY (HIGH+) ({len(recent)}):")
        for s in recent[-5:]:
            parts.append(f"  {s.get('urgency','?'):8} {s.get('side','?')} {s.get('implied_move','?')}pt "
                         f"'{s.get('market','?')[:45]}'")
            parts.append(f"    news: {s.get('news_headline','?')[:70]}")

    # Open positions health
    monitor = _read_jsonl_latest(HERE / "position_monitor.log")
    investigate = [r for r in monitor.values() if r.get("action") in ("INVESTIGATE","EXIT")]
    if investigate:
        parts.append(f"\nPOSITION MONITOR ALERTS ({len(investigate)} INVESTIGATE/EXIT):")
        for r in investigate:
            parts.append(f"  {r.get('action','?'):12} {r.get('side','?')} "
                         f"drift={r.get('drift',0):+.3f} '{r.get('q','?')[:50]}'")

    # Defender verdicts
    dv = _read_json(HERE / "defender_verdicts.json", {})
    df_acts = [v for v in dv.get("verdicts",[]) if v.get("action") in ("EXIT","REDUCE")]
    if df_acts:
        parts.append(f"\nDEFENDER EXIT/REDUCE ({len(df_acts)}):")
        for v in df_acts[-3:]:
            parts.append(f"  {v.get('action','?')} {v.get('side','?')} '{v.get('q','?')[:50]}' "
                         f"— {v.get('key_finding','?')[:60]}")

    if not parts:
        return "(no agent data yet — agents need one run cycle to populate)"
    return "\n".join(parts)


RANKING_PROMPT = """You are a synthesis analyst for a Polymarket paper-trading research system.
Below is the full output from 6 AI agents that have been scanning for opportunities.

Your job: produce a TOP-5 ranked priority list telling the human exactly what to do TODAY.

Rules:
- Only rank opportunities where thesis_critic says PROMOTE (or no critic verdict yet = pending review)
- NEVER rank anything thesis_critic KILLed
- Breaking news CRITICAL/HIGH signals go to top if action=ENTER_NOW
- Defender EXIT/REDUCE alerts are URGENT and override new entries
- Weight: breaking_news (time-sensitive) > thesis_critic PROMOTE > deep_research PURSUE > edge_hunter YES-confirmed > scout gate-pass
- Be concrete: name the market, the side, the entry price, and exactly ONE next action

RANKING FORMAT:
---RANKING---
GENERATED: {ts}

RANK 1: [URGENCY LABEL]
MARKET: <question>
ACTION: <exactly what to do — e.g. "promote to book at YES=0.32 side=NO" or "check position / consider exit">
SIGNAL_SOURCE: <which agent(s) flagged this>
CONFIDENCE: <1-10>/10
WHY_NOW: <1 sentence — why this ranks #1 today>

RANK 2: ...
[up to RANK 5]

BLOCKERS: <anything that should be done BEFORE entering new positions — e.g. exit REDUCE alerts>
PASS_LIST: <markets that were flagged but explicitly should NOT be entered today, and why>
---END---

Agent data:
{context}"""


def run(verbose: bool = True):
    ctx = _gather_context()
    if verbose:
        print("[opportunity_ranker] synthesizing all agent outputs...")
        print(f"  context: {len(ctx)} chars")

    prompt = RANKING_PROMPT.format(
        ts=time.strftime("%Y-%m-%d %H:%MZ", time.gmtime()),
        context=ctx
    )
    response = _api_call(prompt)

    # Parse ranking block
    ranking_text = ""
    if "---RANKING---" in response:
        ranking_text = response.split("---RANKING---")[-1].split("---END---")[0].strip()

    st = _read_json(RANKINGS, {"rankings": []})
    st["rankings"].append({
        "ts": time.strftime("%Y-%m-%d %H:%MZ", time.gmtime()),
        "ranking_text": ranking_text,
        "full_response": response,
    })
    tmp = RANKINGS.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=2))
    tmp.replace(RANKINGS)

    if verbose:
        print(f"\n[opportunity_ranker] → {RANKINGS}")
        if ranking_text:
            print("\n" + ranking_text[:1200])

    # Also write a plain-text copy for Obsidian
    out = HERE / "opportunity_ranking_latest.md"
    out.write_text(
        f"# Opportunity Ranking\n_Generated: {time.strftime('%Y-%m-%d %H:%MZ', time.gmtime())}_\n\n"
        + (ranking_text if ranking_text else response[:1500])
    )


def show():
    st = _read_json(RANKINGS, {"rankings": []})
    rs = st.get("rankings", [])
    if not rs:
        print("no rankings yet"); return
    latest = rs[-1]
    print(f"\n  Latest ranking [{latest.get('ts','')}]\n")
    print(latest.get("ranking_text","(empty)")[:2000])


def main():
    _load_env()
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="quiet watchdog mode")
    ap.add_argument("--show", action="store_true", help="show latest ranking")
    a = ap.parse_args()
    if a.show:
        show(); return
    run(verbose=not a.once)


if __name__ == "__main__":
    main()
