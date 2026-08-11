"""
edge_ledger.py — append-only edge-hunt verdict ledger + failure-pattern recall.

Closes the open loop the fleet was missing: edge-verdict records EVERY verdict +
kill-reason here; the orchestrator/hunters read recent_failures() BEFORE a hunt so
the fleet stops re-surfacing the false-positive TYPES it already knows
(hallucination, mid-mirage, partial-field, fee-myth, parsing-bug, ...).

Local-only, append-only jsonl. Nothing reads venue APIs. Mirrors belief_ledger.py.
Importable AND a CLI:
    python3 edge_ledger.py record <hunter> <verdict> <reason words...>
    python3 edge_ledger.py patterns        # frequency-ranked avoid-list for hunters
"""
import os, sys, json
from datetime import datetime, timezone
from collections import Counter

BASE = os.path.dirname(os.path.abspath(__file__))
LEDGER = os.path.join(BASE, "edge_ledger.jsonl")
SETTLEMENTS = os.path.join(BASE, "edge_settlements.jsonl")  # back-filled outcomes (slow loop)
VAULT = os.path.expanduser("~/Documents/PolymarketVault/Reports/Edge Ledger.md")  # Obsidian mirror

# The kill taxonomy — the recurring ways an "edge" turns out to be fiction here.
PATTERNS = {
    "hallucination":      "LLM named a market that does not exist — fetch the real book FIRST",
    "mid-mirage":         "edge only at the gamma mid; the executable CLOB book kills it",
    "partial-field":      "basket Σ≈1 over a PARTIAL field, not the full negRisk pool",
    "fee-myth":           "netted a fee that does not apply (held basket locks are fee-free)",
    "parsing-bug":        "hunt code mis-parsed threshold/asset/units — re-check the number",
    "sub-fee":            "gross edge <= fees + spread",
    "already-covered":    "basket_arb / monoarb / dataarb already books it",
    "resolution-mismatch":"legs resolve on a different source or date",
    "capacity-dead":      "real but only pennies of executable size",
    "market-is-right":    "no misread — the price reflects the correct reading",
}


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def record(hunter, verdict, reason, candidate="", market="", p=None):
    """Append one verdict line. For a KILL, `reason` should contain a PATTERNS key.
    `market` + `p` (the verdict's probability the candidate is a REAL edge: EDGE≈0.8,
    ACCUMULATE≈0.5, NULL≈0.1) let settle()/scorecard() score the call once it resolves."""
    row = {"ts": _now(), "hunter": hunter, "verdict": verdict, "reason": reason,
           "candidate": str(candidate)[:140], "market": market, "p": p}
    with open(LEDGER, "a") as f:
        f.write(json.dumps(row) + "\n")
    try:
        snapshot()                 # auto-mirror to Obsidian on every verdict (fail-soft)
    except Exception:
        pass
    return row


def settle(market, won, note=""):
    """Back-fill the realized outcome of a market a verdict was rendered on.
    won=1 if a claimed EDGE actually realized, 0 if it did not."""
    row = {"ts": _now(), "market": market, "won": int(bool(int(won))), "note": note}
    with open(SETTLEMENTS, "a") as f:
        f.write(json.dumps(row) + "\n")
    return row


def scorecard():
    """Calibration over verdicts that named market+p AND have since settled (the SLOW
    loop): Brier + directional hit-rate — does an EDGE/ACCUMULATE call actually pay?"""
    latest = {}
    for r in _rows(2000):
        if r.get("market") and r.get("p") is not None:
            latest[r["market"]] = r            # keep the latest verdict per market
    setts = {}
    if os.path.exists(SETTLEMENTS):
        for l in open(SETTLEMENTS):
            if l.strip():
                s = json.loads(l); setts[s["market"]] = s["won"]
    pairs = [(latest[m]["p"], setts[m]) for m in latest if m in setts]
    n = len(pairs)
    if not n:
        return {"n_settled": 0,
                "note": "no settled verdicts yet — slow loop dormant until markets resolve"}
    brier = sum((p - o) ** 2 for p, o in pairs) / n
    hits = sum(1 for p, o in pairs if (p >= 0.5) == bool(o))
    return {"n_settled": n, "brier": round(brier, 4),
            "directional_hit_rate": round(hits / n, 3),
            "mean_p": round(sum(p for p, _ in pairs) / n, 3),
            "base_rate_won": round(sum(o for _, o in pairs) / n, 3)}


def _rows(k=300):
    if not os.path.exists(LEDGER):
        return []
    rows = [json.loads(l) for l in open(LEDGER) if l.strip()]
    return rows[-k:]


def recent_failures(k=40):
    """Frequency-ranked avoid-list over the last k verdicts — what hunters read first."""
    rows = _rows(k)
    c = Counter()
    for r in rows:
        reason = (r.get("reason") or "").lower()
        for p in PATTERNS:
            if p in reason:
                c[p] += 1
    return {"n_reviewed": len(rows),
            "avoid": [{"type": p, "hits": n, "what": PATTERNS[p]} for p, n in c.most_common()]}


def snapshot():
    """Mirror the ledger to Obsidian (Vault/Reports/Edge Ledger.md): verdict tally, the
    kill-taxonomy avoid-list with live hit-counts, and the recent verdicts. Called
    automatically after every record(); also runnable via the CLI. Fail-soft."""
    rows = _rows(2000)
    vc = Counter(r.get("verdict", "?") for r in rows)
    hit = {a["type"]: a["hits"] for a in recent_failures(len(rows) or 1)["avoid"]}
    L = ["---", "type: dashboard", "status: live", f"date: {_now()[:10]}", "---", "",
         "# 🧭 Edge Ledger", "",
         f"> The fleet's closed loop — every edge-hunt verdict + kill-reason. "
         f"**{len(rows)} verdicts: {vc.get('EDGE',0)} edge · {vc.get('ACCUMULATE',0)} accumulate · {vc.get('NULL',0)} null.**", "",
         f"_updated {_now()} (all times UTC)._", "",
         "## Kill taxonomy — the avoid-list hunters read first", "",
         "| type | hits | what it means |", "|---|---|---|"]
    for t, what in PATTERNS.items():
        L.append(f"| {t} | {hit.get(t, 0)} | {what} |")
    L += ["", "## Recent verdicts", "", "| when (UTC) | hunter | verdict | reason | candidate |",
          "|---|---|---|---|---|"]
    for r in reversed(rows[-15:]):
        reason = (r.get("reason") or "").replace("|", "/").replace("\n", " ")[:80]
        cand = (r.get("candidate") or "").replace("|", "/")[:40]
        when = (r.get("ts", "") or "")[:16].replace("T", " ")   # full YYYY-MM-DD HH:MM, no year drop
        L.append(f"| {when} | {r.get('hunter','')} | {r.get('verdict','')} | {reason} | {cand} |")
    sc = scorecard()
    L += ["", (f"## Calibration (settled): n={sc['n_settled']} · Brier {sc.get('brier')} · "
               f"hit-rate {sc.get('directional_hit_rate')}" if sc.get("n_settled")
               else "_Calibration: slow loop dormant — no verdicts have settled yet._")]
    try:
        os.makedirs(os.path.dirname(VAULT), exist_ok=True)
        with open(VAULT, "w") as f:
            f.write("\n".join(L) + "\n")
    except Exception as e:
        print(f"vault snapshot skipped: {e}", file=sys.stderr)
        return None
    return VAULT


if __name__ == "__main__":
    a = sys.argv[1:]
    if a and a[0] == "record":
        kw, pos = {}, []
        for tok in a[1:]:                         # optional market=<slug> p=<0..1>
            if tok.startswith("market="):
                kw["market"] = tok[len("market="):]
            elif tok.startswith("p="):
                try: kw["p"] = float(tok[2:])
                except ValueError: pass
            else:
                pos.append(tok)
        print(json.dumps(record(pos[0] if pos else "?",
                                pos[1] if len(pos) > 1 else "?",
                                " ".join(pos[2:]) if len(pos) > 2 else "", **kw)))
    elif a and a[0] == "patterns":
        print(json.dumps(recent_failures(), indent=2))
    elif a and a[0] == "settle":
        print(json.dumps(settle(a[1] if len(a) > 1 else "?", a[2] if len(a) > 2 else 0)))
    elif a and a[0] == "scorecard":
        print(json.dumps(scorecard(), indent=2))
    elif a and a[0] == "snapshot":
        print(snapshot() or "snapshot failed")
    else:
        print(__doc__)
