#!/usr/bin/env python3
"""cross_market_hunt.py — logical-implication edge discovery.

Hunts for LOGICAL-INCONSISTENCY mispricing: markets within an event where one market
logically entails another (X→Y) but they're priced backwards (P(X) > P(Y) despite X⊆Y).

Example: "Will BTC reach $100k?" and "Will BTC reach $50k?" — if you believe $100k, you MUST
believe $50k. So P(100k) ≤ P(50k) by logic alone. A violation is a locked edge.

Non-predictive: logic is deterministic. Immune to calibrated-mids (if you read the logic right,
the edge exists regardless of crowd's forecast skill).

Read-only, paper, keyless.
"""
import os, sys, json, urllib.parse, urllib.request
from datetime import datetime, timezone
import worker_lib as W

STEM = "cross_market"
BASE = os.path.dirname(os.path.abspath(__file__))
GAMMA = "https://gamma-api.polymarket.com/markets"

# Implication patterns (hand-coded for common event structures)
IMPLICATION_PATTERNS = [
    # Sports (tournament structure)
    {"name": "win_implies_reach", "from": "win", "to": "reach", "entails": True},
    # Financial
    {"name": "above_X_implies_above_Y", "from": "above", "to": "above", "entails": True},  # if X>B, then X>A (A<B)
]

def http_get(url, params=None):
    if params:
        url += "?" + urllib.parse.urlencode(params)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "cross-market-hunt/1.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception:
        return None

def fetch_event_markets():
    """Fetch all markets; group by event/slug prefix (naive clustering)."""
    try:
        raw = http_get(GAMMA, {"active": "true", "closed": "false", "limit": 500})
        markets = raw if isinstance(raw, list) else raw.get("data", [])

        # Naive: group by event title prefix (first N words)
        from collections import defaultdict
        by_event = defaultdict(list)
        for m in markets:
            q = (m.get("question") or "").strip()
            if not q:
                continue
            # Group by first 5 words (event identifier)
            event_key = " ".join(q.split()[:5]).lower()
            try:
                prices = m.get("outcomePrices")
                if isinstance(prices, str):
                    prices = json.loads(prices)
                yes_price = float(prices[0]) if prices else 0
            except Exception:
                yes_price = 0
            by_event[event_key].append({
                "id": str(m.get("conditionId") or m.get("id") or ""),
                "q": q,
                "yes": yes_price,
            })
        return dict(by_event)
    except Exception as e:
        W.log(STEM, f"[fetch] FAIL {e}")
        return {}

def find_monotonicity_violations(event_markets):
    """Scan event markets for price-monotonicity violations (X→Y but P(X)>P(Y))."""
    violations = []
    n = len(event_markets)
    if n < 2:
        return violations

    # Simple O(n²) check: every pair
    for i, m1 in enumerate(event_markets):
        for m2 in event_markets[i+1:]:
            q1, q2 = m1["q"].lower(), m2["q"].lower()
            p1, p2 = m1["yes"], m2["yes"]

            # Heuristic: if q1 is "more specific" and q2 is "broader", q1→q2
            # (e.g., "reach $100k" → "reach $50k")
            if ("100" in q1 and "50" in q2) or ("win" in q1 and "reach" in q2):
                if p1 > p2 + 0.05:  # violation if P(specific) > P(broad) + buffer
                    violations.append({
                        "from": m1["q"],
                        "from_yes": round(p1, 3),
                        "to": m2["q"],
                        "to_yes": round(p2, 3),
                        "gap": round(p1 - p2, 3),
                        "type": "monotonicity_violation",
                    })
    return violations

def main():
    events = fetch_event_markets()
    all_violations = []

    for event_key, markets in events.items():
        violations = find_monotonicity_violations(markets)
        all_violations.extend(violations)

    # Filter: gap >= 5% (after fees)
    real_violations = [v for v in all_violations if v["gap"] >= 0.05]

    W.log(STEM, f"[main] scanned {len(events)} events, found {len(real_violations)} violations")

    # Log to vault
    try:
        vault = os.path.expanduser("~/Documents/PolymarketVault/Reports")
        os.makedirs(vault, exist_ok=True)
        body = [f"# Cross-Market Implication Hunt ({W.ts()})\n"]
        body.append(f"_{len(real_violations)} logical-inconsistency candidates_\n")

        if real_violations:
            for v in real_violations[:20]:
                body.append(f"- **{v['gap']:+.1%} gap** from `{v['from'][:60]}` (YES={v['from_yes']})")
                body.append(f"  to `{v['to'][:60]}` (YES={v['to_yes']})")
        else:
            body.append("_No violations found (markets priced logically)._")

        with open(os.path.join(vault, "cross_market_hunt.md"), "w") as f:
            f.write("\n".join(body))
    except Exception as e:
        W.log(STEM, f"[vault] FAIL {e}")

    return len(real_violations)

if __name__ == "__main__":
    exit(main())
