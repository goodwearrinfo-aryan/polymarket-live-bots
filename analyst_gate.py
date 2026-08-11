#!/usr/bin/env python3
"""analyst_gate.py — 3-lens adversarial refutation panel for analyst observations.

LAYER: RESEARCH (aggregate hypotheses). NOT a duplicate of judge_panel.py — that is
the EXECUTION layer (gates a SINGLE bet via edge/correctness/base_rate/resolution/
correlation). Kept separate on purpose (2026-06-15 debate): this gates whether an
EDGE is real; judge_panel gates whether a BET is worth placing.

Gates sourced edges into Track 1 (analyst book). Each observation runs through
3 independent lenses trying to refute it: execution_risk, data_quality, generalization.
If ≥2/3 fail to refute (cannot poke holes), the observation is marked PASS and
becomes a candidate for analyst_positions.json.

Usage:
  # Score observations from goldsky dataset (sample 20)
  python3 analyst_gate.py --file ml/dataset_goldsky.csv --sample 20

  # Score a single custom observation
  python3 analyst_gate.py --category crypto --prob 0.65 --outcome 1 --fills 45 --claim "BTC surge near-resolution markets "

  # Show past gates/decisions
  python3 analyst_gate.py --report

Output: analyst_verdicts.csv with {claim, category, verdict (PASS/FAIL), confidence,
        execution_refuted, data_quality_refuted, generalization_refuted, notes}
"""
import os, sys, json, csv, argparse, time, math, re
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATE = HERE / "analyst_verdicts_state.json"
OUT = HERE / "analyst_verdicts.csv"

LENSES = [
    {
        "name": "execution_risk",
        "title": "Execution Risk",
        "prompt": """You are an adversarial trader reviewing whether a market edge is EXECUTABLE.

Edge claim: {claim}
- Category: {category}
- Median fill price (YES): {prob:.3f}
- Resolved outcome: {outcome}
- Sample size: {fills} fills
- Source: {source}

Your job: Try to REFUTE this edge by identifying execution risks.
Consider: price gaps at resolution, market liquidity/depth, slippage, orderbook concentration,
  time constraints (can you actually get fills?), Polymarket-specific risks.

Output ONLY one word: REFUTED or NOT_REFUTED"""
    },
    {
        "name": "data_quality",
        "title": "Data Quality / Selection Bias",
        "prompt": """You are an adversarial data scientist reviewing whether an edge is DATA-HONEST.

Edge claim: {claim}
- Category: {category}
- Median fill price (YES): {prob:.3f}
- Resolved outcome: {outcome}
- Sample size: {fills} fills
- Source: {source}

Your job: Try to REFUTE this edge by identifying data quality or selection bias.
Consider: survivorship bias (did we drop failed observations?), look-ahead bias (is the
  claim using information known before the trade?), cherry-picking (selected category/timeframe
  to look good?), stale data issues, outcome bias.

Output ONLY one word: REFUTED or NOT_REFUTED"""
    },
    {
        "name": "generalization",
        "title": "Generalization (OOS resilience)",
        "prompt": """You are an adversarial ML researcher reviewing whether an edge GENERALIZES.

Edge claim: {claim}
- Category: {category}
- Median fill price (YES): {prob:.3f}
- Resolved outcome: {outcome}
- Sample size: {fills} fills
- Source: {source}

Your job: Try to REFUTE this edge by identifying overfitting or regime-dependence.
Consider: is {fills} fills enough for statistical power given the complexity of the claim?
  Does the category have hidden regime shifts (bear market vs bull, high-vol vs low-vol)?
  Is the pattern likely market-timing luck? Would this {claim} still work in different periods?

Output ONLY one word: REFUTED or NOT_REFUTED"""
    },
]


def _load_env():
    env_path = HERE / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")

if not ANTHROPIC_API_KEY:
    print("ERROR: ANTHROPIC_API_KEY not set in env or .env")
    sys.exit(1)


def _call_claude(prompt, max_tokens=20):
    """Call Claude API; return response text. NOTE: `temperature` is DEPRECATED for
    claude-opus-4-8 (the API rejects it), so verdict reproducibility CANNOT be bought
    with temperature=0 — the panel is stochastic at the model default and the same
    position can flip PASS↔FAIL run-to-run. Variance must be reduced by majority-of-K
    voting per lens instead (not yet wired). Do NOT re-add a temperature field here."""
    import urllib.request
    body = json.dumps({
        "model": "claude-opus-4-8",
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}]
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "content-type": "application/json",
            "anthropic-version": "2023-06-01",
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            resp = json.load(r)
        if "error" in resp:
            print(f"  API error: {resp['error']}")
            return None
        return resp["content"][0]["text"].strip()
    except Exception as e:
        print(f"  API call failed: {e}")
        return None


def gate_observation(obs):
    """Run 3-lens adversarial panel on one observation.

    Args:
        obs: dict with {claim, category, prob, final_outcome, n_fills, source}

    Returns:
        dict with {claim, category, verdict (PASS/FAIL), confidence, ...refuted fields, notes}
    """
    claim = obs.get("claim", f"{obs['category']} edge")

    result = {
        "claim": claim,
        "category": obs["category"],
        "verdict": None,
        "confidence": None,
        "notes": "",
    }

    refute_votes = []
    for lens in LENSES:
        print(f"    {lens['title']}…", end=" ", flush=True)
        prompt = lens["prompt"].format(
            claim=claim,
            category=obs["category"],
            prob=obs["prob"],
            outcome=obs["final_outcome"],
            fills=obs["n_fills"],
            source=obs["source"]
        )
        response = _call_claude(prompt)
        if response is None:
            print("error")
            result["notes"] += f"{lens['name']}: API error. "
            continue

        refuted = "REFUTED" in response.upper()
        refute_votes.append(refuted)
        result[f"{lens['name']}_refuted"] = 1 if refuted else 0
        print("REFUTED" if refuted else "NOT_REFUTED")

    if not refute_votes:
        result["verdict"] = "ERROR"
        result["confidence"] = 0
        return result

    not_refuted_count = sum(1 for v in refute_votes if not v)
    result["verdict"] = "PASS" if not_refuted_count >= 2 else "FAIL"
    result["confidence"] = not_refuted_count / len(refute_votes)
    return result


# ── position-level gate: stress-test a SOURCED analyst thesis ────────────────
# (the gate's real job — band hypotheses are killed for free by the calibration
#  screen in scorecard.py; this evaluates edges sourced from OUTSIDE the price.)
#
# Lenses upgraded 2026-06-15 from CL4R1T4S patterns (see PROMPT_PATTERNS.md):
#   evidence-grounded verdicts (DROID) + verify-before-assert (Devin/Cursor) +
#   default-REFUTED-under-uncertainty (Cursor "never make things up"). Each lens now
#   QUOTES the real Gamma resolution criteria and AUTO-REFUTES when the thesis rests on
#   a mechanism not verifiable in them — encoding the touch-vs-close / "unverified" hole
#   that sank the BTC-dip and US-Iran positions on 2026-06-15.
def _http_get(url, timeout=20):
    import urllib.request
    return json.loads(urllib.request.urlopen(
        urllib.request.Request(url, headers={"User-Agent": "analyst-gate/1.0"}), timeout=timeout).read())


CRITERIA_CACHE = HERE / "analyst_criteria_cache.json"


def fetch_resolution(pos):
    """The market's REAL resolution-criteria text from Gamma (the 'description').
    Caches by market_id so a TRANSIENT Gamma miss (Gamma drops markets — see BRAIN)
    falls back to last-known criteria instead of flip-flopping a verdict. Returns None
    only when we've NEVER seen the criteria — then the lenses treat the mechanism as
    UNVERIFIABLE and refute (uncertainty = refute)."""
    key = pos.get("market_id") or pos.get("slug") or pos.get("q", "?")
    try:
        cache = json.loads(CRITERIA_CACHE.read_text()) if CRITERIA_CACHE.exists() else {}
    except Exception:
        cache = {}
    mid, slug = pos.get("market_id"), pos.get("slug")
    queries = ([f"condition_ids={mid}"] if mid else []) + ([f"slug={slug}"] if slug else [])
    for q in queries:
        try:
            d = _http_get(f"https://gamma-api.polymarket.com/markets?{q}")
            m = d[0] if isinstance(d, list) and d else (d if isinstance(d, dict) else None)
            if m and m.get("description"):
                txt = m["description"].strip()[:2200]
                cache[key] = txt
                try:
                    CRITERIA_CACHE.write_text(json.dumps(cache))
                except Exception:
                    pass
                return txt
        except Exception:
            continue
    return cache.get(key)   # Gamma miss → last-known criteria, else None


POSITION_LENSES = [
    {
        "name": "resolution_risk",
        "title": "Resolution-criteria risk",
        "prompt": """You are an adversarial analyst trying to REFUTE a prediction-market thesis by attacking its READING OF THE RESOLUTION CRITERIA. Prediction-market edges die when a bet rests on a resolution mechanism the analyst ASSUMED rather than verified.

Market: {q}
Resolves: {resolves}
Analyst's bet: {side}  (entry YES price {entry_yes:.2f})
Analyst's thesis: {thesis}

OFFICIAL RESOLUTION CRITERIA (verbatim from the market):
\"\"\"
{resolution_text}
\"\"\"

Do this in order:
1. EVIDENCE — quote the exact phrase(s) from the OFFICIAL RESOLUTION CRITERIA that decide whether this bet wins. If the criteria say "UNAVAILABLE", you cannot verify the mechanism.
2. Judge: is the winning mechanism the thesis relies on ACTUALLY PRESENT and UNAMBIGUOUS in those quoted criteria? A thesis that depends on a mechanism not stated/verifiable there (touch-vs-close, what counts as a "qualifying" event, partial fulfillment, oracle/UMA edge cases) is REFUTED. If the criteria are UNAVAILABLE, you cannot verify → REFUTED.

Output EXACTLY:
EVIDENCE: <quoted criteria phrase, or "criteria UNAVAILABLE">
REASONING: <2-3 sentences>
VERDICT: REFUTED   (mechanism unverified / criteria cut against the bet / criteria unavailable)
VERDICT: NOT_REFUTED   (the winning mechanism is explicitly present in the criteria and supports the bet)"""
    },
    {
        "name": "edge_is_real",
        "title": "Edge real vs crowd-right",
        "prompt": """You are an adversarial trader trying to REFUTE that an analyst has a real edge. Prediction-market prices are usually well-calibrated; the burden is on the analyst to show the price is WRONG using information not already in it.

Market: {q}
Market-implied YES probability: {entry_yes:.2f}
Analyst's claimed true probability: {true_prob:.2f}  (claimed edge {edge_pct} pts, side {side})
Analyst's thesis: {thesis}

Output EXACTLY:
EVIDENCE: name the single ROOT TRUTH and quote the thesis phrase that decides it — is the MARKET PRICE the correct estimate (analyst anchoring on a vivid narrative / public info already priced / ignoring base rates), or does the analyst cite SPECIFIC information the crowd plausibly mis-weighted?
REASONING: <2-3 sentences>
VERDICT: REFUTED   (price is the root truth, edge illusory, OR insufficient specific basis to credit the edge)
VERDICT: NOT_REFUTED   (the analyst plausibly knows something specific the price doesn't)"""
    },
    {
        "name": "timing_execution",
        "title": "Timing / clean-resolution risk",
        "prompt": """You are an adversarial risk manager trying to REFUTE a prediction-market thesis on TIMING and clean RESOLUTION.

Market: {q}
Resolves: {resolves}   (today is 2026-06-15)
Analyst's bet: {side}, confidence {confidence}/10, risk tag {risk}
Analyst's thesis: {thesis}

OFFICIAL RESOLUTION CRITERIA (verbatim):
\"\"\"
{resolution_text}
\"\"\"

Output EXACTLY:
EVIDENCE: quote the date/deadline/condition (from the criteria or thesis) that governs WHEN and WHETHER this resolves cleanly — extension/deadline-slip risk, lingering ambiguity, a catalyst that must arrive in-window, or liquidity to hold to resolution. If you cannot find one, say so.
REASONING: <2-3 sentences>
VERDICT: REFUTED   (credible timing/execution failure, OR cannot confirm clean in-window resolution)
VERDICT: NOT_REFUTED   (resolves cleanly and is holdable in the window)"""
    },
    {
        "name": "base_rate_check",
        "title": "Base-rate sanity",
        "prompt": """You are an adversarial statistician trying to REFUTE a prediction-market thesis by attacking its BASE RATES. An analyst's true_prob is only credible if it's consistent with the historical frequency of similar events — and most analysts anchor on narratives rather than checking base rates.

Market: {q}
Market-implied YES probability: {entry_yes:.2f}
Analyst's claimed true probability: {true_prob:.2f}  (side {side})
Analyst's thesis: {thesis}
Resolves: {resolves}

RESOLUTION CRITERIA (verbatim):
\"\"\"{resolution_text}\"\"\"

Do this in order:
1. EVIDENCE — name the EVENT CLASS this market belongs to (e.g. "geopolitical peace deals between historically adversarial states", "individual athlete injury clearance", "company market-cap ranking in 30 days"). State the rough base rate for this class from general knowledge.
2. REASONING — does the analyst's claimed true_prob of {true_prob:.2f} land INSIDE a plausible 90% interval around the event-class base rate? If the analyst's number implies an extraordinary departure from the base rate (e.g. claiming 15% for a class that resolves YES <1% historically), that extraordinary claim requires extraordinary specific evidence the thesis doesn't supply — refute. Base-rate-consistent claims survive.

Output EXACTLY:
EVIDENCE: <event class + rough historical base rate>
REASONING: <2-3 sentences>
VERDICT: REFUTED   (analyst's true_prob is extraordinary vs base rates and thesis gives insufficient specific reason)
VERDICT: NOT_REFUTED   (analyst's true_prob is base-rate-plausible, or thesis cites specific facts that justify the deviation)"""
    },
    {
        "name": "market_liquidity",
        "title": "Liquidity / holdability",
        "prompt": """You are an adversarial risk manager trying to REFUTE a prediction-market thesis on the grounds of LIQUIDITY AND HOLDABILITY. A thesis is worthless if you can't get filled or can't hold the position profitably to resolution.

Market: {q}
Side: {side}  Entry YES price: {entry_yes:.2f}  Resolves: {resolves}  (today is 2026-06-16)
Analyst's thesis: {thesis}
Analyst's risk tag: {risk}, confidence: {confidence}/10

Known liquidity context: Polymarket is a binary prediction market. Typical bid-ask spreads on low-liquidity markets are 5-15¢. On high-volume political/crypto markets, spreads are 0.5-3¢. Holding to resolution requires no active management but ties up capital. Early exit requires crossing the spread again.

Output EXACTLY:
EVIDENCE: <specific liquidity concern: what type of market is this, what is a realistic spread at {entry_yes:.2f} price, how long until {resolves}, what capital efficiency risks exist>
REASONING: <2-3 sentences — is the spread cost likely to consume the {edge_pct}pt claimed edge? Can the position be held to resolution without forced exit risk? Is the position size economic given Polymarket minimums?>
VERDICT: REFUTED   (spread cost likely exceeds edge, OR position is too illiquid to hold, OR capital lockup duration makes it uneconomic)
VERDICT: NOT_REFUTED   (edge comfortably exceeds realistic spread cost, position is holdable, capital efficiency is acceptable)"""
    },
    {
        "name": "sourcing_quality",
        "title": "Sourcing quality (recency/diversity)",
        "prompt": """You are an adversarial fact-checker trying to REFUTE a prediction-market thesis on the QUALITY OF ITS SOURCING. A thesis is only as good as its evidence: good research cites RECENT, MULTIPLE, INDEPENDENT, credible sources (Perplexity-style). Stale, single-source, or circular citations (citing the market page itself as evidence) are a red flag — especially for fast-moving geopolitical/crypto events.

Market: {q}  (resolves {resolves}, today is 2026-06-15)
Analyst's bet: {side}
Analyst's thesis: {thesis}

SOURCES PROVIDED:
{sources_text}

AUTOMATED SOURCING SUMMARY (deterministic — judge against these numbers):
{sourcing_summary}

LIVE NEWS CROSS-CHECK (hermes feed, 52 sources, polled continuously):
{live_news}

Output EXACTLY:
EVIDENCE: state the source count, how many are INDEPENDENT (not the Polymarket market page itself), how stale the most-recent source is vs today / the {resolves} resolution date, AND whether the LIVE NEWS shows active developments on this topic that the analyst's cited sources predate or contradict.
REASONING: <2-3 sentences> — is the sourcing recent enough for an event resolving {resolves}, multiple/independent/credible, AND consistent with the current news cycle (not working off stale info while the live feed shows the story has moved)?
VERDICT: REFUTED   (stale, single-source, circular, no real sources, OR the live feed shows fresher developments the cited sources missed)
VERDICT: NOT_REFUTED   (recent, multiple, independent, and consistent with / corroborated by the live news cycle)"""
    },
]


def sourcing_summary(pos):
    """Deterministic sourcing hygiene from the position's `sources` URLs — the evidence
    the sourcing lens grounds on. Counts sources, independence (non-Polymarket), and the
    recency of any date embedded in the URLs vs today (2026-06-15)."""
    srcs = pos.get("sources") or []
    if isinstance(srcs, str):
        srcs = [srcs]
    n = len(srcs)
    independent = [s for s in srcs if "polymarket.com" not in s.lower()]
    today = time.mktime(time.strptime("2026-06-15", "%Y-%m-%d"))
    dates = []
    for s in srcs:
        m = re.search(r"/(20\d\d)[/-](\d{1,2})[/-](\d{1,2})", s)
        if m:
            y, mo, d = m.groups()
            try:
                dates.append(time.mktime(time.strptime(f"{int(y):04d}-{int(mo):02d}-{int(d):02d}", "%Y-%m-%d")))
            except Exception:
                pass
    if not n:
        return "0 sources provided — no evidence basis."
    rec = (f"most-recent source ~{int((today - max(dates)) / 86400)}d old"
           if dates else "no dates parseable from the source URLs (recency unverifiable)")
    return f"{n} source(s); {len(independent)} independent (non-Polymarket); {rec}."


HERMES_URL = "http://127.0.0.1:48580/api/news"
_MONTHS = {"january", "february", "march", "april", "may", "june", "july", "august",
           "september", "october", "november", "december"}


def hermes_news_scan(pos, max_show=6):
    """Ground the sourcing lens in the LIVE news feed (hermes, 52 sources): does the
    current news cycle cover this market's topic, and does it show developments the
    analyst's cited (possibly stale) sources predate? Keyword = proper nouns in the
    question. Fail-soft: a neutral note if hermes is down (sourcing falls back to URLs)."""
    caps = [w for w in re.findall(r"[A-Z][a-zA-Z]{2,}", pos.get("q", ""))
            if w.lower() not in _MONTHS and w.lower() not in {"will", "the", "by"}]
    kw = list(dict.fromkeys(w.lower() for w in caps)) or \
        [w for w in re.findall(r"[a-z]{5,}", pos.get("q", "").lower()) if w not in _MONTHS][:4]
    if not kw:
        return "no topic keywords extractable — live-news cross-check skipped."
    try:
        items = _http_get(HERMES_URL, timeout=6).get("items", [])
    except Exception:
        return "live news feed unavailable (hermes down) — sourcing judged on cited URLs only."
    matched = [it for it in items if any(k in (it.get("title") or "").lower() for k in kw)]
    if not matched:
        return (f"hermes up ({len(items)} items, 52 sources) but ZERO match {kw} — topic is "
                f"NOT in the live news cycle (niche, or already-settled).")
    matched.sort(key=lambda it: it.get("first_seen_utc", ""), reverse=True)
    heads = " | ".join((it.get("title") or "")[:72] for it in matched[:max_show])
    fresh = matched[0].get("first_seen_utc", "?")[:16]
    return (f"hermes feed: {len(matched)} live items match {kw} (freshest seen {fresh}). "
            f"Recent headlines the analyst's sources should not contradict/predate: {heads}")


def _parse_verdict(resp):
    """(refuted: bool, reason: str) from one lens response; (None, '') on API error.
    NOT_REFUTED contains REFUTED as a substring, so test NOT_REFUTED first."""
    if resp is None:
        return None, ""
    tail = resp.upper().split("VERDICT:")[-1] if "VERDICT:" in resp.upper() else resp.upper()
    refuted = "NOT_REFUTED" not in tail and "REFUTED" in tail
    reason = resp.split("VERDICT:")[0].strip().replace("\n", " ")[:200]
    return refuted, reason


def gate_position(pos):
    """Stress-test ONE sourced analyst position through the 4 thesis-lenses
    (resolution / edge / timing / sourcing). Each lens is sampled K times (GATE_SAMPLES,
    default 3) and votes by MAJORITY — Opus 4.8 is stochastic and `temperature` is
    deprecated, so a single draw flips PASS↔FAIL run-to-run; majority-of-K makes the
    verdict reproducible. PASS if >=2/3 of the lenses fail to refute."""
    result = {
        "claim": pos.get("q", pos.get("slug", "?")),
        "category": pos.get("side", "?"),
        "verdict": None, "confidence": None, "notes": "",
    }
    # fetch the REAL resolution criteria once — the evidence the lenses must quote.
    res_text = fetch_resolution(pos)
    if not res_text:
        res_text = "UNAVAILABLE — Gamma did not return resolution criteria for this market."
    result["criteria_found"] = 0 if res_text.startswith("UNAVAILABLE") else 1
    srcs = pos.get("sources") or []
    if isinstance(srcs, str):
        srcs = [srcs]
    fields = {
        "q": pos.get("q", ""), "side": pos.get("side", ""),
        "entry_yes": float(pos.get("entry_yes", pos.get("entry_price", 0.5))),
        "true_prob": float(pos.get("true_prob", 0.5)),
        "edge_pct": pos.get("edge_pct", "?"), "confidence": pos.get("confidence", "?"),
        "risk": pos.get("risk", "?"), "resolves": pos.get("resolves", "?"),
        "thesis": pos.get("thesis", ""), "resolution_text": res_text,
        "sources_text": "\n".join(f"- {s}" for s in srcs) if srcs else "(none provided)",
        "sourcing_summary": sourcing_summary(pos),
        "live_news": hermes_news_scan(pos),
    }
    K = max(1, int(os.environ.get("GATE_SAMPLES", "3")))
    # 6 lenses: resolution_risk, edge_is_real, timing_execution, base_rate_check,
    # market_liquidity, sourcing_quality. threshold = ceil(2/3 * 6) = 4.
    votes, reasons = [], []
    for lens in POSITION_LENSES:
        print(f"    {lens['title']}…", end=" ", flush=True)
        prompt = lens["prompt"].format(**fields)
        samples = [_parse_verdict(_call_claude(prompt, max_tokens=500)) for _ in range(K)]
        valid = [r for r, _ in samples if r is not None]
        refuted_ct = sum(1 for r in valid if r)
        # majority of the VALID samples; ties → refuted; all-error → refuted (a lens
        # that can't render a verdict = that dimension UNVERIFIED, can only make us stricter).
        refuted = (refuted_ct * 2 >= len(valid)) if valid else True
        if not valid:
            result["notes"] += f"{lens['name']}:all-API-error→refuted. "
        votes.append(refuted)
        result[f"{lens['name']}_refuted"] = 1 if refuted else 0
        # keep a reason from a sample that AGREES with the majority verdict
        reason = next((rs for r, rs in samples if r == refuted and rs),
                      next((rs for r, rs in samples if rs), ""))
        tally = "".join("R" if r else ("N" if r is not None else "x") for r, _ in samples)
        reasons.append(f"[{lens['name']} {tally}] {reason}")
        print(f"[{tally}] → " + ("REFUTED" if refuted else "NOT_REFUTED"))
    if not votes:
        result["verdict"] = "ERROR"; result["confidence"] = 0; return result
    survived = sum(1 for v in votes if not v)
    threshold = math.ceil(2 * len(votes) / 3)   # ≥2/3 of voting lenses must fail to refute
    result["verdict"] = "PASS" if survived >= threshold else "FAIL"
    result["confidence"] = survived / len(votes)
    result["notes"] = " || ".join(reasons)
    return result


def load_state():
    if STATE.exists():
        return json.loads(STATE.read_text())
    return {"gated": [], "last_updated": None}


def save_state(st):
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=2))
    tmp.replace(STATE)


def write_verdicts(results):
    os.makedirs(OUT.parent, exist_ok=True)
    new = not OUT.exists()
    fieldnames = [
        "claim", "category", "verdict", "confidence",
        "execution_risk_refuted", "data_quality_refuted", "generalization_refuted",
        "resolution_risk_refuted", "edge_is_real_refuted", "timing_execution_refuted",
        "base_rate_check_refuted", "market_liquidity_refuted", "sourcing_quality_refuted",
        "notes",
    ]
    with open(OUT, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        if new:
            w.writeheader()
        for r in results:
            w.writerow({k: r.get(k, "") for k in fieldnames})
    print(f"\nwrote {len(results)} verdicts -> {OUT}")


def main():
    ap = argparse.ArgumentParser(description="Analyst gate: 3-lens adversarial refutation panel")
    ap.add_argument("--file", help="CSV with observations (condition_id, category, prob, final_outcome, n_fills, source)")
    ap.add_argument("--sample", type=int, default=None, help="gate N random observations from file")
    ap.add_argument("--category", help="category (crypto, politics, sports, other)")
    ap.add_argument("--prob", type=float, help="median fill price [0, 1]")
    ap.add_argument("--outcome", type=int, help="resolved outcome (0 or 1)")
    ap.add_argument("--fills", type=int, help="number of fills/sample size")
    ap.add_argument("--claim", help="custom claim (e.g. 'BTC surge near-resolution markets underpriced')")
    ap.add_argument("--source", default="manual", help="data source")
    ap.add_argument("--positions", action="store_true",
                    help="gate the SOURCED edges in analyst_positions.json (the gate's real job)")
    ap.add_argument("--report", action="store_true", help="show past verdicts")
    a = ap.parse_args()

    if a.report:
        if OUT.exists():
            print(f"\nanalyst verdicts ({OUT}):\n")
            with open(OUT) as f:
                rows = list(csv.DictReader(f))
            passed = sum(1 for r in rows if r["verdict"] == "PASS")
            failed = sum(1 for r in rows if r["verdict"] == "FAIL")
            print(f"  {passed} PASS, {failed} FAIL\n")
            for r in rows[-10:]:
                conf = float(r.get("confidence", 0))
                print(f"  {r['verdict']:6s} {r['category']:10s} conf={conf:.2f}  {r['claim'][:50]}")
        else:
            print("no verdicts yet")
        return

    results = []

    if a.positions:
        book = HERE / "analyst_positions.json"
        if not book.exists():
            print("no analyst_positions.json"); return
        positions = json.loads(book.read_text()).get("positions", [])
        if not positions:
            print("no open positions to gate"); return
        print(f"\ngating {len(positions)} sourced analyst positions through the 4-lens panel…\n")
        for i, pos in enumerate(positions):
            print(f"[{i+1}/{len(positions)}] {pos.get('side','?')} {pos.get('q','?')[:54]}")
            results.append(gate_position(pos))
            time.sleep(1)

    elif a.file:
        import random
        with open(a.file) as f:
            rows = list(csv.DictReader(f))
        if a.sample:
            rows = random.sample(rows, min(a.sample, len(rows)))

        st = load_state()
        gated_claims = {r["claim"] for r in st["gated"]}
        rows = [r for r in rows if r["condition_id"] not in gated_claims]

        if not rows:
            print("all observations already gated")
            return

        print(f"\ngating {len(rows)} observations…\n")
        for i, row in enumerate(rows):
            cid = row["condition_id"][:16]
            print(f"[{i+1}/{len(rows)}] {cid}… ({row['category']})")
            obs = {
                "claim": f"{row['category']} edge: median price {row['prob']}, outcome {row['final_outcome']}, {row['n_fills']} fills",
                "category": row["category"],
                "prob": float(row["prob"]),
                "final_outcome": int(row["final_outcome"]),
                "n_fills": int(row["n_fills"]),
                "source": row.get("source", "dataset"),
            }
            verdict = gate_observation(obs)
            results.append(verdict)
            st["gated"].append(verdict)
            time.sleep(1)

        st["last_updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
        save_state(st)

    elif a.category and a.prob is not None and a.outcome is not None and a.fills:
        claim = a.claim or f"{a.category} edge: price {a.prob}, outcome {a.outcome}, {a.fills} fills"
        print(f"\ngating: {claim}\n")
        obs = {
            "claim": claim,
            "category": a.category,
            "prob": a.prob,
            "final_outcome": a.outcome,
            "n_fills": a.fills,
            "source": a.source,
        }
        verdict = gate_observation(obs)
        results.append(verdict)

    else:
        ap.print_help()
        return

    if results:
        write_verdicts(results)
        passed = sum(1 for r in results if r["verdict"] == "PASS")
        print(f"\n{'='*64}\n  PANEL SUMMARY: {passed}/{len(results)} survived (>=2/3 lenses failed to refute)\n{'='*64}")
        for r in results:
            mark = "✅ PASS" if r["verdict"] == "PASS" else ("❌ FAIL" if r["verdict"] == "FAIL" else "⚠️ ERR")
            print(f"  {mark}  conf={r.get('confidence',0):.2f}  {r['category']} {r['claim'][:50]}")
            if r.get("notes"):
                for chunk in r["notes"].split(" || "):
                    print(f"        {chunk}")


if __name__ == "__main__":
    main()
