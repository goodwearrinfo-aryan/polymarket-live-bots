#!/usr/bin/env python3
"""
check_resolution.py — pm-resolution-checker as a standalone tool on the free LLM.
Pattern: Python fetches the VERBATIM resolution criteria from gamma (deterministic), the LLM
states exactly what triggers YES vs NO + every trap. The #1 edge killer ("most seats" != "most
seats GAINED", MOU != permanent deal, touch != close, window start). Zero Claude-Code cost.

Run:
  python3 check_resolution.py --cid 0x...                 # live (needs LLM_* in .env)
  python3 check_resolution.py --slug some-market-slug
  python3 check_resolution.py --cid 0x... --mock          # verify pipeline w/o endpoint
"""
import os, sys, json, argparse, datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import edge_common, llm_client

BASE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(BASE, "check_resolution_log.jsonl")
VAULT = os.path.expanduser("~/Documents/PolymarketVault/Reports/resolution_check_latest.md")

SHAPE = ('{"yes_triggers": "exact condition(s)", "no_triggers": "exact condition(s)", '
         '"cutoff": "datetime + timezone", "traps": ["one sentence each"], '
         '"resolution_confirmed": true|false, "notes": "any ambiguity / what to verify"}')

SYSTEM = (
    "You are a Polymarket resolution-criteria specialist for a PAPER research bot. Given the "
    "VERBATIM resolution text, state EXACTLY what makes it resolve YES vs NO, the date cutoff, "
    "and EVERY trap. Run this checklist: wording precision (win vs reach-runoff; permanent/signed "
    "vs ceasefire/MOU; 'most seats' vs 'most seats GAINED'); touch-vs-close (any 1-min Low<=X is a "
    "one-touch over the window, not a close); exact date cutoff + tz; immediate-resolution clauses "
    "(eliminated->No, any-candle-touch->Yes); announcement-counts; negated questions; window start "
    "(only in-window data counts). Set resolution_confirmed=false if the criteria are ambiguous OR "
    "you cannot tell what the resolving facts are. Be terse and exact; a fuzzy read is worse than none."
)


def gather(cid=None, slug=None):
    q = {"condition_ids": cid} if cid else {"slug": slug}
    raw = edge_common._get(edge_common.GAMMA, q) or []
    m = raw[0] if raw else {}
    prices = m.get("outcomePrices"); prices = json.loads(prices) if isinstance(prices, str) else (prices or [])
    return {"q": m.get("question", ""), "cid": m.get("conditionId", cid or ""),
            "slug": m.get("slug", slug or ""), "end": (m.get("endDate") or "")[:10],
            "market_yes": float(prices[0]) if prices else None,
            "resolution_text": (m.get("description") or "")[:1500]}


def check(cid=None, slug=None, mock=False):
    ctx = gather(cid, slug)
    user = (f"MARKET: {ctx['q']}\nEnd date (listing): {ctx['end']}\nMarket YES price: {ctx['market_yes']}\n"
            f"VERBATIM RESOLUTION CRITERIA:\n{ctx['resolution_text']}")
    if mock:
        verdict = {"yes_triggers": "[mock]", "no_triggers": "[mock]", "cutoff": "[mock]",
                   "traps": ["[mock] pipeline test"], "resolution_confirmed": True, "notes": "[mock]"}
    else:
        try:
            verdict = llm_client.judge(system=SYSTEM, user=user, schema_hint=SHAPE)
        except Exception as e:
            verdict = {"error": str(e), "resolution_confirmed": False}
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    result = {"ts": ts, **{k: ctx[k] for k in ("q", "cid", "slug", "market_yes")}, "mock": mock, "verdict": verdict}
    _persist(result, ctx)
    return result


def _persist(r, ctx):
    try:
        open(LOG, "a").write(json.dumps({**r, **llm_client.provenance()}) + "\n")
    except Exception as e:
        print(f"[log FAIL] {e}", file=sys.stderr)
    v = r["verdict"]
    try:
        lines = [f"# Resolution check — {ctx['q'][:60]}", "",
                 f"_{r['ts']} · market YES {r['market_yes']}_" + ("  _(mock)_" if r["mock"] else ""), "",
                 f"**resolution_confirmed: {v.get('resolution_confirmed')}**", "",
                 f"- **YES triggers:** {v.get('yes_triggers','')}",
                 f"- **NO triggers:** {v.get('no_triggers','')}",
                 f"- **Cutoff:** {v.get('cutoff','')}", "", "**Traps:**"]
        for t in (v.get("traps") or []):
            lines.append(f"- {t}")
        lines += ["", f"**Notes:** {v.get('notes','')}"]
        os.makedirs(os.path.dirname(VAULT), exist_ok=True)
        open(VAULT, "w").write("\n".join(lines) + "\n")
    except Exception as e:
        print(f"[vault FAIL] {e}", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cid"); ap.add_argument("--slug"); ap.add_argument("--mock", action="store_true")
    a = ap.parse_args()
    if not a.cid and not a.slug:
        print("need --cid or --slug"); sys.exit(2)
    if not a.mock and not llm_client.configured():
        print("⚠️ No LLM endpoint (set LLM_* in .env) — or use --mock."); sys.exit(2)
    r = check(a.cid, a.slug, mock=a.mock)
    v = r["verdict"]
    print(f"\n{r['q'][:60]}  (YES {r['market_yes']})  [{'mock' if r['mock'] else 'live'}]")
    print(f"  resolution_confirmed: {v.get('resolution_confirmed')}")
    print(f"  YES: {str(v.get('yes_triggers',''))[:90]}")
    print(f"  NO:  {str(v.get('no_triggers',''))[:90]}")
    for t in (v.get("traps") or [])[:4]:
        print(f"  ⚠️ {str(t)[:90]}")


if __name__ == "__main__":
    main()
