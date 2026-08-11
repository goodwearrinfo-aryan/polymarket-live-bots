#!/usr/bin/env python3
"""
refute_edge.py — the pm-edge-refuter agent as a STANDALONE Python tool on a free LLM.

The pattern (provider-agnostic, no tool-calling, zero Claude-Code cost):
  1. Python gathers ALL evidence deterministically — verbatim resolution criteria (gamma),
     live price/book (CLOB), the resolving-data dossier (analyst_data_gate), recent news.
  2. Each of the 3 adversarial lenses is a judge() call over that evidence → {refuted,
     confidence, why}. A free text LLM (Groq/Ollama) needs no web access because the Python
     half already fetched everything.
  3. Aggregate: an edge SURVIVES iff <2 lenses refute at >70% confidence (confidence-weighted,
     the Variant-C rule). Writes verdict to Obsidian + an append-only jsonl.

Run:
  python3 refute_edge.py --cid 0x... --side NO --true 0.15 --reason "..."   # live (needs LLM_* env)
  python3 refute_edge.py --cid 0x... --side NO --true 0.15 --mock           # verify pipeline w/o endpoint
"""
import os, sys, json, re, argparse, datetime, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import edge_common
import analyst_data_gate as adg
import llm_client

BASE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(BASE, "refute_edge_log.jsonl")
VAULT = os.path.expanduser("~/Documents/PolymarketVault/Reports/refute_edge_latest.md")

VERDICT_SHAPE = '{"refuted": true|false, "confidence": 0-100, "why": "one specific sourced paragraph"}'


# ---------- Step 1: deterministic evidence gathering (pure Python) ----------

def gather_context(cid):
    raw = edge_common._get(edge_common.GAMMA, {"condition_ids": cid}) or []
    m = raw[0] if raw else {}
    q = m.get("question", "")
    desc = (m.get("description") or "")[:1200]
    prices = m.get("outcomePrices"); prices = json.loads(prices) if isinstance(prices, str) else (prices or [])
    yes = float(prices[0]) if prices else None
    # resolving-data dossier, if the market settles on an objective series
    src, params = adg.classify(q)
    dossier = adg.fetch_dossier(src, params, cid) if src else None
    return {"q": q, "resolution_text": desc, "market_yes": yes,
            "data_source": src, "data_dossier": dossier,
            "recent_news": _news(q)}


def _news(q, n=6):
    """Best-effort keyless recent headlines RELEVANT to the market (hermes :48580, fail-soft)."""
    try:
        data = json.loads(urllib.request.urlopen("http://127.0.0.1:48580/api/news", timeout=5).read())
        items = data.get("items", []) if isinstance(data, dict) else (data or [])
        terms = {w for w in re.findall(r"[a-z]{4,}", q.lower())
                 if w not in {"will", "the", "by", "in", "to", "of", "and", "for", "2026"}}
        scored = []
        for it in items:
            t = (it.get("title") or "")
            hits = sum(1 for w in terms if w in t.lower())
            if hits:
                scored.append((hits, t))
        scored.sort(reverse=True)
        heads = [t for _, t in scored[:n]]
        return heads or ["(no headlines matched this market in the live feed)"]
    except Exception:
        return ["(news feed unavailable — judging on resolution + data + price only)"]


# ---------- Step 2: the 3 adversarial lenses ----------

def _evidence_block(ctx, claim):
    dos = json.dumps(ctx["data_dossier"], default=str)[:600] if ctx["data_dossier"] else "none"
    return (f"MARKET: {ctx['q']}\nMarket YES price: {ctx['market_yes']}\n"
            f"RESOLUTION CRITERIA (verbatim): {ctx['resolution_text']}\n"
            f"RESOLVING-DATA DOSSIER ({ctx['data_source']}): {dos}\n"
            f"RECENT NEWS: {' | '.join(ctx['recent_news'])}\n"
            f"CLAIMED EDGE: side={claim['side']}, true P(YES)={claim['true']}, "
            f"reasoning={claim['reason']}")


LENSES = {
    "resolution-misread": "You are an adversarial reviewer attacking the RESOLUTION READING of a prediction-market edge. Default: the analyst MISREAD how/when it resolves (exact threshold, touch-vs-close, date cutoff, wording, window start). Using ONLY the evidence provided, set refuted=true unless the resolution interpretation is airtight AND the edge survives a correct reading. If the read is fine but the probability looks wrong, that's NOT your lens — say so and do not refute.",
    "counter-evidence": "You are an adversarial reviewer hunting COUNTER-EVIDENCE the analyst missed, using ONLY the provided news/data. Default: such evidence exists. Set refuted=true unless the provided evidence does NOT contain credible counter-evidence pushing the probability toward the market price. Be honest if the evidence actually supports the edge.",
    "market-is-right": "You are an adversarial reviewer arguing the liquid MARKET price is correct and the edge is illusory (already priced, risk premium, longshot bias, overconfidence, wrong anchor). Using ONLY the provided evidence, set refuted=true unless the edge clearly survives the efficient-market prior. Also flag if a NO at <0.10 on a long tail is a capital-inefficient FLB tail-sell rather than an edge.",
}


def run_lens(lens, ctx, claim, mock=False):
    if mock:
        # deterministic stub so the FULL pipeline is verifiable without an endpoint
        stub = {"resolution-misread": {"refuted": False, "confidence": 85, "why": "[mock] resolution read airtight"},
                "counter-evidence":   {"refuted": False, "confidence": 25, "why": "[mock] no counter-evidence in provided data"},
                "market-is-right":    {"refuted": False, "confidence": 78, "why": "[mock] edge survives efficient-market prior"}}
        return {"lens": lens, **stub[lens]}
    system = LENSES[lens] + "\nReturn confidence 0-100 that your refutation is correct."
    try:
        v = llm_client.judge(system=system, user=_evidence_block(ctx, claim), schema_hint=VERDICT_SHAPE)
        return {"lens": lens, "refuted": bool(v.get("refuted")), "confidence": int(v.get("confidence", 0)),
                "why": str(v.get("why", ""))[:600]}
    except Exception as e:
        return {"lens": lens, "refuted": True, "confidence": 100, "why": f"[lens failed -> treat as refuted] {e}"}


# ---------- Step 3: aggregate + persist ----------

def refute(cid, side, true_prob, reason, mock=False):
    ctx = gather_context(cid)
    claim = {"side": side, "true": true_prob, "reason": reason}
    verdicts = [run_lens(L, ctx, claim, mock=mock) for L in LENSES]
    hard_refuters = [v for v in verdicts if v["refuted"] and v["confidence"] > 70]
    survived = len(hard_refuters) < 2
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    result = {"ts": ts, "cid": cid, "q": ctx["q"], "side": side, "true_prob": true_prob,
              "market_yes": ctx["market_yes"], "verdicts": verdicts,
              "hard_refuters": len(hard_refuters), "survived": survived, "mock": mock}
    _persist(result)
    return result


def _persist(r):
    try:
        with open(LOG, "a") as f:
            f.write(json.dumps({**r, **llm_client.provenance()}) + "\n")
    except Exception as e:
        print(f"[log FAIL] {e}", file=sys.stderr)
    try:
        lines = [f"# Refute-Edge verdict — {r['q'][:60]}", "",
                 f"_{r['ts']} · side {r['side']} · true P(YES) {r['true_prob']} vs market {r['market_yes']}_", "",
                 f"**{'SURVIVED' if r['survived'] else 'REFUTED'}** — {r['hard_refuters']}/3 lenses refuted at >70%"
                 + ("  _(mock run)_" if r['mock'] else ""), ""]
        for v in r["verdicts"]:
            lines.append(f"- **{v['lens']}** — refuted={v['refuted']} @{v['confidence']} — {v['why']}")
        os.makedirs(os.path.dirname(VAULT), exist_ok=True)
        open(VAULT, "w").write("\n".join(lines) + "\n")
    except Exception as e:
        print(f"[vault FAIL] {e}", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cid", required=True)
    ap.add_argument("--side", required=True, choices=["YES", "NO"])
    ap.add_argument("--true", type=float, required=True, dest="true_prob")
    ap.add_argument("--reason", default="")
    ap.add_argument("--mock", action="store_true")
    a = ap.parse_args()
    if not a.mock and not llm_client.configured():
        print("⚠️ No LLM endpoint (set LLM_* in .env) — use --mock to test the pipeline. See llm_client.py.")
        sys.exit(2)
    r = refute(a.cid, a.side, a.true_prob, a.reason, mock=a.mock)
    print(f"\n{'SURVIVED' if r['survived'] else 'REFUTED'} — {r['hard_refuters']}/3 hard refuters "
          f"({'mock' if r['mock'] else 'live'})")
    for v in r["verdicts"]:
        print(f"  [{v['lens']:18}] refuted={v['refuted']} @{v['confidence']:>3} — {v['why'][:70]}")


if __name__ == "__main__":
    import urllib.parse  # used by _news
    main()
