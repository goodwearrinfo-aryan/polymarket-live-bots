#!/usr/bin/env python3
"""
judge_panel.py — per-bet execution gate (v3, 2026-06-15).

LAYER: EXECUTION. Gates a SINGLE discrete bet ("should I place THIS trade?").
The research layer ("is this edge real?", aggregate hypotheses) lives in
analyst_gate.py — kept separate on purpose (different question, different input,
LLM judgment vs cheap mechanical rules). Do not merge the two layers.

A bet trades ONLY if it survives ALL lenses:
  1. EDGE        — conviction must beat the price paid (the check v1 lacked).
  2. CORRECTNESS — trivial sanity: thesis is non-empty and cites a number/date.
  3. BASE_RATE   — conviction must respect category predictability ceiling.
  4. RESOLUTION  — reads the MARKET's actual Gamma resolution text (not the
                   analyst's thesis) and refutes if it's missing or contains
                   subjective/discretionary wording. This replaces v2's
                   keyword-on-thesis "security" lens, which was theater: no
                   honest analyst writes "this is manipulable" into their pitch,
                   so scanning the pitch caught nothing. Real resolution risk
                   lives in the market's own criteria.
  5. CORRELATION — refutes if the bet duplicates a subject already held in the
                   live book(s). Doubling BTC-down exposure across two bets
                   isn't diversification; it shrinks the independent sample the
                   30-exit edge test needs. This gate did not exist before.

v2 -> v3: security(keyword-on-thesis) -> resolution(fetch market text);
+ correlation lens; correctness demoted to sanity. EDGE/base_rate unchanged.
The deeper LLM resolution read stays in analyst_gate (research layer) — here we
do the cheap deterministic version (one cached HTTP GET, no API key).
"""

import json, re, os, sys, urllib.request, urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parent
RES_CACHE = HERE / "resolution_cache.json"
GAMMA = "https://gamma-api.polymarket.com/markets"
DEFAULT_BOOKS = [HERE / "analyst.json", HERE / "analyst_positions.json"]

CATEGORY_CEILING = {
    "geopolitics": 0.80, "politics": 0.82, "macro": 0.85, "crypto": 0.82,
    "commodity": 0.80, "sports": 0.92, "other": 0.85, "default": 0.85,
}
MIN_EDGE = 0.03

# Subjective/discretionary wording in a MARKET's resolution description.
AMBIGUITY_MARKERS = (
    "at the discretion", "sole discretion", "discretion of", "at its sole",
    "subjective", "deemed", "reasonable", "approximately", "or similar",
    "broadly", "credible report", "significant", "substantial", "major closure",
    "good faith", "spirit of", "materially", "generally", "as determined",
)

# Canonical subject -> trigger words, for correlation detection.
SUBJECT_KEYWORDS = {
    "bitcoin":   ("bitcoin", "btc"),
    "ethereum":  ("ethereum", "ether", " eth"),
    "iran":      ("iran", "hormuz", "airspace", "tehran"),
    "israel":    ("israel", "hezbollah", "gaza"),
    "fed_rates": ("federal reserve", "fed ", "interest rate", "rate cut",
                  "rate hike", "fomc", "rate pause"),
    "inflation": ("cpi", "inflation"),
    "silver":    ("silver",),
    "gold":      ("gold",),
    "clarity_act": ("clarity act",),
}


def _subject(text):
    t = (text or "").lower()
    for subj, kws in SUBJECT_KEYWORDS.items():
        if any(k in t for k in kws):
            return subj
    return None


def fetch_spread(market_id, timeout=12):
    """Return the market's live bid-ask spread (fraction). None on failure.
    Not cached — spreads move, and gating is infrequent (deploy-time)."""
    if not market_id:
        return None
    for q in (f"?condition_ids={market_id}", f"?id={market_id}"):
        try:
            req = urllib.request.Request(GAMMA + q, headers={"User-Agent": "judge-panel/3.1"})
            d = json.loads(urllib.request.urlopen(req, timeout=timeout).read())
            m = d[0] if isinstance(d, list) and d else (d if isinstance(d, dict) else None)
            if not m:
                continue
            sp = m.get("spread")
            if sp is not None:
                return float(sp)
            bb, ba = m.get("bestBid"), m.get("bestAsk")
            if bb is not None and ba is not None:
                return abs(float(ba) - float(bb))
        except Exception:
            continue
    return None


def fetch_resolution(market_id, timeout=12):
    """Return the market's resolution `description` (cached). None on failure."""
    if not market_id:
        return None
    cache = {}
    if RES_CACHE.exists():
        try:
            cache = json.loads(RES_CACHE.read_text())
        except Exception:
            cache = {}
    if market_id in cache:
        return cache[market_id]
    desc = None
    for q in (f"?condition_ids={market_id}", f"?id={market_id}"):
        try:
            req = urllib.request.Request(GAMMA + q, headers={"User-Agent": "judge-panel/3.0"})
            d = json.loads(urllib.request.urlopen(req, timeout=timeout).read())
            m = d[0] if isinstance(d, list) and d else (d if isinstance(d, dict) else None)
            if m:
                desc = (m.get("description") or "").strip()
                break
        except Exception:
            continue
    if desc is not None:
        cache[market_id] = desc
        try:
            RES_CACHE.write_text(json.dumps(cache))
        except Exception:
            pass
    return desc


class Judge:
    def __init__(self, thesis, conviction, category=None, entry_price=None,
                 side=None, market_id=None, book_paths=None, exclude_id=None):
        self.thesis = thesis or ""
        self.tl = self.thesis.lower()
        self.conviction = float(conviction)
        self.category = (category or "default").lower()
        self.entry_price = entry_price
        self.side = side
        self.market_id = market_id
        self.book_paths = book_paths if book_paths is not None else DEFAULT_BOOKS
        self.exclude_id = exclude_id

    def refute_edge(self):
        if self.entry_price is None:
            return False, "no entry_price — edge unverifiable"
        edge = self.conviction - float(self.entry_price)
        if edge <= 0:
            return False, f"NEGATIVE edge: conviction {self.conviction:.2f} <= price {self.entry_price:.2f}"
        if edge < MIN_EDGE:
            return False, f"edge {edge:+.3f} below {MIN_EDGE} floor — noise"
        # spread-aware: the edge must clear the price you actually pay to get in.
        # A +4pt edge under a 5pt spread is a guaranteed loss (the Silver case).
        # FAIL CLOSED, to match the resolution lens: if a market is named but its
        # spread won't verify, refuse the bet ("don't bet what you can't verify")
        # rather than silently passing on the fixed floor (a transient gamma
        # outage must not let a phantom edge through).
        if not self.market_id:
            return False, "no market_id — spread unverifiable (don't bet what you can't verify)"
        spread = fetch_spread(self.market_id)
        if spread is None:
            return False, "could not fetch live spread — refuse rather than pass on the fixed floor"
        net = edge - spread
        if net <= 0:
            return False, f"edge {edge:+.3f} does NOT clear spread {spread:.3f} (net {net:+.3f}) — phantom"
        return True, f"edge {edge:+.3f} clears spread {spread:.3f} (net {net:+.3f})"

    def refute_correctness(self):
        if not self.thesis.strip():
            return False, "empty thesis"
        if not re.search(r"\d", self.thesis):
            return False, "no concrete evidence (no number/date) — assertion, not analysis"
        return True, "thesis present, evidence-anchored"

    def refute_base_rate(self):
        ceiling = CATEGORY_CEILING.get(self.category, CATEGORY_CEILING["default"])
        if self.conviction > ceiling:
            return False, f"conviction {self.conviction:.2f} exceeds {self.category} ceiling {ceiling:.2f}"
        return True, f"conviction {self.conviction:.2f} within {self.category} ceiling {ceiling:.2f}"

    def refute_resolution(self):
        if not self.market_id:
            return False, "no market_id — resolution criteria unverifiable (don't bet what you can't verify)"
        desc = fetch_resolution(self.market_id)
        if desc is None:
            return False, "could not fetch resolution criteria from gamma"
        if not desc:
            return False, "market has NO published resolution criteria"
        hits = [m for m in AMBIGUITY_MARKERS if m in desc.lower()]
        if hits:
            return False, f"subjective resolution wording: {hits[:3]}"
        return True, f"resolution criteria present, no ambiguity markers ({len(desc)} chars)"

    def refute_correlation(self):
        subj = _subject(self.thesis) or _subject(self.tl)
        if not subj:
            return True, "no recognizable subject — correlation unchecked"
        held = []
        for bp in self.book_paths:
            bp = Path(bp)
            if not bp.exists():
                continue
            try:
                book = json.loads(bp.read_text())
            except Exception:
                continue
            rows = book.get("open", []) or book.get("positions", [])
            for r in rows:
                if self.exclude_id and r.get("id") == self.exclude_id:
                    continue
                q = r.get("q") or r.get("question") or ""
                if _subject(q) == subj:
                    held.append(r.get("id") or q[:30])
        if held:
            return False, f"correlated: subject '{subj}' already in book ({held[:2]})"
        return True, f"subject '{subj}' not already held"


class JudgePanel:
    LENSES = ("edge", "correctness", "base_rate", "resolution", "correlation")

    def evaluate_bet(self, thesis, conviction, category=None, entry_price=None,
                     side=None, market_id=None, book_paths=None, exclude_id=None):
        j = Judge(thesis, conviction, category, entry_price, side, market_id,
                  book_paths, exclude_id)
        runners = {
            "edge": j.refute_edge,
            "correctness": j.refute_correctness,
            "base_rate": j.refute_base_rate,
            "resolution": j.refute_resolution,
            "correlation": j.refute_correlation,
        }
        verdicts = {}
        for lens in self.LENSES:
            ok, reason = runners[lens]()
            verdicts[lens] = {"survived_refutation": ok, "reasoning": reason}
        survived = all(v["survived_refutation"] for v in verdicts.values())
        return survived, verdicts


def _demo(name, thesis, conv, **kw):
    s, v = JudgePanel().evaluate_bet(thesis, conv, **kw)
    print(f"\n{name} -> {'SURVIVED' if s else 'REJECTED'}")
    for lens, vv in v.items():
        print(f"   [{'PASS' if vv['survived_refutation'] else 'REFUTE':6s}] {lens:11s} {vv['reasoning']}")


if __name__ == "__main__":
    # Iran airspace: real market text says "major closure" -> resolution lens should refute
    _demo("Iran airspace YES (real market text)",
          "Active conflict; June 7-8 closures reported. Research P=0.70 vs 0.335.",
          0.70, category="geopolitics", entry_price=0.335, side="YES",
          market_id="0xc239b0621a1a7d79228032559bbc5f2a43cffeac5f9bdca63cb872347142c16b")
