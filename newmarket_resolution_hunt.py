#!/usr/bin/env python3
"""newmarket_resolution_hunt.py — resolution-misread hunter on FRESH Polymarket markets (paper, read-only).

THE GAP THIS FILLS: edge_hunt_runner.py runs the free-LLM resolution-misread hunter on the
LIQUID book (order=volumeNum). newmarket_logger.py polls FRESH markets (order=createdAt) but does
ZERO judgment — it only logs novelty/age. So nobody reads the RESOLUTION RULES of a brand-new
listing. This runner bridges them: pull fresh markets (keyless gamma) -> free-LLM reads the
resolution text -> flag where the price implies the CROWD misread the rules -> VAULT-ONLY lead.

WHY FRESH + RESOLUTION (not fresh + price-snipe): the raw new-market TIMING vein already tested
EMPTY (2026-06-21: 63 novel markets = AMM wall, no executable lead). New = thin = wide spreads, so
sniping a fresh price is a mark-to-mid mirage. But resolution-MISREAD is a READING edge, not a fill
edge: you can DETECT the crowd's rule-misread the moment a market lists, log it, and wait for the
book to deepen before it is executable. Early detection of a durable mispricing, not a thin-book snipe.

Discipline (inherited from edge_hunt_runner / worker_lib — same house rules):
  • FREE LLM only (worker_lib.free_judge -> provider chain -> ollama floor). Zero Claude, $0.
  • VAULT-ONLY, NEVER PAGE: the free model is miscalibrated (cry-wolf); a STRONG flag is a research
    lead to verify, not an alert. Deduped by conditionId via worker_lib.is_new_alert.
  • READ-ONLY to shared state; writes ONLY its own vault note / log / dedup (two-writer lesson).
  • FAIL-SOFT: every fetch/LLM call guarded -> log + skip, exit 0. A 24/7 worker never crash-loops.
  • Reuses edge_hunt_runner.resolution_hunt + its price/days helpers (no reinvention).

Run:  python3 newmarket_resolution_hunt.py [once]
"""
import urllib.parse
from datetime import datetime, timezone

import worker_lib as W
import edge_hunt_runner as EH

STEM = "newmarket_resolution"
GAMMA = "https://gamma-api.polymarket.com/markets"

# FRESH-market query (keyless): the reverse-engineered createdAt sort that actually surfaces
# freshly-created markets (the default id-ascending sort masked them — 2026-06-21 newmarket_logger).
FRESH_PARAMS = {"active": "true", "closed": "false",
                "order": "createdAt", "ascending": "false", "limit": 100}

# Fresh markets are THIN by definition, so we do NOT gate on volume (that is the whole point —
# edge_hunt_runner already covers the liquid book). We keep: a genuine-uncertainty band, a real
# resolution description (can't audit rules with no text), an event window, the coinflip-mill skip,
# and a freshness cap so this stays the NEW-market hunter and doesn't re-judge the aged book.
YES_BAND = (0.08, 0.92)          # wider than the liquid hunter — fresh prices are noisier
DAYS_MIN, DAYS_MAX = 0.25, 240   # from just-opened to far-dated
MIN_DESC = 60                    # need actual resolution text to audit a misread
JUDGE_CAP = 10                   # cost/noise bound on free-LLM calls per cycle
MAX_AGE_DAYS = 4                 # "fresh" = created within N days


def fetch_fresh_markets():
    """Keyless gamma pull, createdAt-sorted -> fresh, genuinely-uncertain, has-rules markets."""
    try:
        url = f"{GAMMA}?{urllib.parse.urlencode(FRESH_PARAMS)}"
        d = EH._gj(url)
    except Exception as e:
        W.log(STEM, f"[fetch] gamma fail: {e}")
        return []
    raw = d if isinstance(d, list) else d.get("data", [])
    out, now = [], datetime.now(timezone.utc)
    for m in raw:
        q = (m.get("question") or "").strip()
        if not q or EH._SKIP_RX.search(q):
            continue
        # freshness gate (createdAt within MAX_AGE_DAYS)
        try:
            cdt = datetime.fromisoformat((m.get("createdAt") or "").replace("Z", "+00:00"))
            age_days = (now - cdt).total_seconds() / 86400.0
        except Exception:
            age_days = None
        if age_days is None or age_days > MAX_AGE_DAYS:
            continue
        y = EH._yes_price(m)
        if y is None or not (YES_BAND[0] <= y <= YES_BAND[1]):
            continue
        desc = (m.get("description") or "").strip()
        if len(desc) < MIN_DESC:                      # no rules text -> can't audit a misread
            continue
        d2e = EH._days_to_end(m)
        if d2e is None or not (DAYS_MIN <= d2e <= DAYS_MAX):
            continue
        out.append({
            "q": q, "desc": desc[:1400], "yes": round(y, 3),
            "slug": m.get("slug") or "",
            "cid": str(m.get("conditionId") or m.get("id") or m.get("slug") or q[:40]),
            "days": round(d2e, 1), "age": round(age_days, 2),
        })
        if len(out) >= JUDGE_CAP:
            break
    return out


def main():
    try:
        markets = fetch_fresh_markets()
    except Exception as e:
        markets = []
        W.log(STEM, f"[fetch] FAIL {e}")

    try:
        leads, status = EH.resolution_hunt(markets)   # reuse the vetted free-LLM misread judge
    except Exception as e:
        leads, status = [], f"FAIL {e}"
        W.log(STEM, f"[resolution] FAIL {e}")

    for l in leads:
        l["is_new"] = W.is_new_alert(STEM, f"freshmisread:{l['cid']}:{l['verdict']}")

    prov = W.prov()
    # The small ollama floor parrots the example traps and flags most markets STRONG — flag it so a
    # floor-only cycle is read as noise, not signal (the primary free model is down/rate-limited).
    low_conf = ("ollama" in prov.lower()) or ("fallback=True" in prov)
    body = [f"# New-Market Resolution-Misread Hunt ({W.ts()})",
            f"_fresh-market (createdAt, age<={MAX_AGE_DAYS}d) free-LLM resolution hunter: "
            f"{status} · model: {prov}_\n"]
    hdr = "## Fresh-listing resolution-misread leads (free-LLM — NOT trades; verify rules + executable book)"
    if low_conf:
        hdr += ("  \n> ⚠️ LOW CONFIDENCE: ran on the small ollama floor (primary free model "
                "down/rate-limited) — unreliable noise, treat as no-signal.")
    body.append(hdr)
    if leads:
        for l in sorted(leads, key=lambda x: 0 if x["verdict"] == "STRONG" else 1):
            tag = " 🆕" if l.get("is_new") else ""
            body.append(f"- **{l['verdict']}**{tag} · {l['q'][:70]} · YES {l['yes']} · side {l['side']} "
                        f"· age {l.get('age', '?')}d"
                        f"\n    - trap: {l['trap']}\n    - slug: `{l['slug']}` · resolves {l['days']}d")
    else:
        body.append("_none flagged this cycle — the honest steady state (fresh markets are thin; the "
                    "crowd usually reads rules right, and a thin book can't be filled anyway)._")
    body.append("\n## Honest note")
    body.append("- VAULT-ONLY, never paged: the free model is miscalibrated, so a STRONG flag is a lead to "
                "VERIFY (read the rules + confirm executable depth), not a trade. Fresh markets are thin by "
                "design — detection now, fill only once liquid. Default truthful result is NULL.")
    W.vault_write(STEM, "\n".join(body))
    W.log(STEM, f"fresh={len(markets)} leads={len(leads)} "
                f"strong={sum(1 for l in leads if l['verdict'] == 'STRONG')} "
                f"new={sum(1 for l in leads if l.get('is_new'))} | {status}")


if __name__ == "__main__":
    main()
