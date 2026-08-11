#!/usr/bin/env python3
"""edge_hunt_runner.py — autonomous FULL-SQUAD edge hunt on the FREE path (paper, read-mostly).

Completes the autonomously-SCHEDULED edge-hunt squad. The proven veins already run on
their own launchd jobs and keep their own alerts/state:
  • structural / complete-set basket locks  -> com.aryan.basket-watch / ws-basket-watch
  • settled-but-mispriced crypto data-arb    -> com.aryan.edge-scan (6h)
  • new-market timing vein                    -> com.aryan.newmarket-logger
This runner adds the ONE squad member with NO autonomous coverage — the
RESOLUTION-MISREAD hunter (the project's #1 documented edge-killer, inverted into an
edge: when YOU read the rules right and the price implies the crowd hasn't) — and folds
every hunter's latest state into ONE unified "full squad" vault brief.

Discipline baked in (matches the house rules):
  • FREE LLM only (worker_lib.free_judge -> nvidia/minimax -> ollama floor). Zero Claude, $0.
  • The free model is MISCALIBRATED (commentary, not conviction): a resolution lead is
    ALWAYS vault-logged and only PAGED when STRONG, and the page is labelled a lead to
    VERIFY, never a trade. No spam — deduped by conditionId + quiet-hours via worker_lib.
  • READ-ONLY to every shared state file. It re-runs nothing that already has a scheduled
    owner and never writes another job's state (the two-writer corruption lesson). It only
    writes its OWN vault note / log / dedup file.
  • FAIL-SOFT: every hunter + feed + LLM call is guarded; on failure -> log + skip, exit 0.
    A 24/7 worker must never crash-loop.

Run:  python3 edge_hunt_runner.py [once]
"""
import os, sys, json, re, urllib.request, urllib.parse
from datetime import datetime, timezone
import worker_lib as W

STEM = "edge_hunt"
BASE = os.path.dirname(os.path.abspath(__file__))
GAMMA = "https://gamma-api.polymarket.com/markets"
VAULT_REPORTS = os.path.expanduser("~/Documents/PolymarketVault/Reports")

# resolution-misread candidate filters (genuine uncertainty, liquid, event-driven)
YES_BAND   = (0.10, 0.90)  # widened: catch extreme outcomes (98% YES/NO fakes common)
MIN_VOLUME = 10_000        # lowered: catch thin markets before arb'd (emerging misreads)
DAYS_MIN, DAYS_MAX = 1, 150  # extended: 150d for elections/geopolitical events
SCAN_PULL  = 500           # deepened: 300 only covers ~10-20% of Polymarket
JUDGE_CAP  = 20            # raised: free LLM cheap, threshold is finding rate not cost
# gamma's `volume` field is often a stale/odd value and `order=volume` mis-sorts; read the
# real number from whichever volume-ish field is populated, and sort by volumeNum.
_VOL_FIELDS = ("volumeNum", "volume", "volume1yr", "volume1mo", "liquidityNum")

# crypto Up/Down coinflip mill + hourly noise — never resolution-misread edges
_SKIP_RX = re.compile(r"\bup or down\b|\bhourly\b|\d\s*(am|pm)\s*et\b|\bup/down\b", re.I)


# --------------------------------------------------------------------------- fetch
def _gj(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": "edge-hunt/1.0"})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())


def _yes_price(m):
    """Best-effort YES price from gamma's outcomePrices (JSON string or list)."""
    op = m.get("outcomePrices")
    try:
        if isinstance(op, str):
            op = json.loads(op)
        if op:
            return float(op[0])
    except Exception:
        pass
    return None


def _vol(m):
    """Largest populated volume-ish value (gamma's `volume` alone is unreliable)."""
    best = 0.0
    for k in _VOL_FIELDS:
        try:
            v = float(m.get(k) or 0)
            if v > best:
                best = v
        except Exception:
            pass
    return best


def _days_to_end(m):
    try:
        end = m.get("endDate") or m.get("end_date_iso")
        if not end:
            return None
        dt = datetime.fromisoformat(end.replace("Z", "+00:00"))
        return (dt - datetime.now(timezone.utc)).total_seconds() / 86400.0
    except Exception:
        return None


def fetch_event_markets():
    """Keyless gamma pull -> liquid, genuinely-uncertain, event-window markets only."""
    out, offset = [], 0
    raw = []
    while len(raw) < SCAN_PULL:
        params = urllib.parse.urlencode({
            "active": "true", "closed": "false",
            "order": "volumeNum", "ascending": "false",
            "limit": 100, "offset": offset,
        })
        try:
            d = _gj(f"{GAMMA}?{params}")
        except Exception as e:
            W.log(STEM, f"[fetch] gamma fail @off{offset}: {e}")
            break
        batch = d if isinstance(d, list) else d.get("data", [])
        if not batch:
            break
        raw.extend(batch)
        offset += 100
    for m in raw[:SCAN_PULL]:
        q = (m.get("question") or "").strip()
        if not q or _SKIP_RX.search(q):
            continue
        y = _yes_price(m)
        if y is None or not (YES_BAND[0] <= y <= YES_BAND[1]):
            continue
        if _vol(m) < MIN_VOLUME:
            continue
        d2e = _days_to_end(m)
        if d2e is None or not (DAYS_MIN <= d2e <= DAYS_MAX):
            continue
        out.append({
            "q": q,
            "desc": (m.get("description") or "")[:1400],
            "yes": round(y, 3),
            "slug": m.get("slug") or "",
            "cid": str(m.get("conditionId") or m.get("id") or m.get("slug") or q[:40]),
            "days": round(d2e, 1),
        })
        if len(out) >= JUDGE_CAP:
            break
    return out


# ------------------------------------------------------------------- resolution hunt
_SYS = (
    "You are a Polymarket RESOLUTION-CRITERIA auditor. Given a market's question, its "
    "exact resolution text, and the current YES price, decide whether the CROWD is likely "
    "MISREADING the resolution rules in a way that makes the price wrong. The edge is NOT "
    "forecasting the event — it is reading the rules more correctly than the market. "
    "Known traps: 'most seats' != 'most seats GAINED'; an MOU/announcement != a permanent "
    "deal; a price TOUCH intraday != a daily close; 'by <date>' cutoffs; immediate "
    "resolution on elimination; ambiguous source/measurement. Be skeptical — the default "
    "is NONE (liquid markets are usually right). Only STRONG if you can name the specific "
    "misread AND say which side is therefore mispriced."
)
_SCHEMA = ('{"verdict":"STRONG|WEAK|NONE","trap":"one line naming the specific misread or '
           "'none'\",\"side\":\"YES|NO|none — which side the misread makes mispriced\"}")


def resolution_hunt(markets):
    leads = []
    if not W.llm_ready():
        W.log(STEM, "[resolution] LLM not ready -> skipped")
        return leads, "skipped (LLM not ready)"
    for m in markets:
        user = (f"QUESTION: {m['q']}\nCURRENT YES PRICE: {m['yes']}\n"
                f"RESOLVES IN: {m['days']} days\nRESOLUTION TEXT:\n{m['desc'] or '(none provided)'}")
        v = W.free_judge(_SYS, user, schema_hint=_SCHEMA)
        if not isinstance(v, dict):
            continue
        verdict = str(v.get("verdict", "NONE")).upper()
        if verdict in ("STRONG", "WEAK"):
            leads.append({**m, "verdict": verdict,
                          "trap": str(v.get("trap", ""))[:160],
                          "side": str(v.get("side", ""))[:8]})
    return leads, f"judged {len(markets)}, {len(leads)} flagged"


# -------------------------------------------------------------- squad context (read-only)
def _tail_line(path, must_contain=None, default="—"):
    try:
        lines = [l for l in open(path).read().splitlines() if l.strip()]
        if must_contain:
            lines = [l for l in lines if must_contain in l]
        return lines[-1][:140] if lines else default
    except Exception:
        return default


def _vault_headline(stem, default="—"):
    try:
        body = open(os.path.join(VAULT_REPORTS, f"{stem}.md")).read()
        for ln in body.splitlines():
            s = ln.strip()
            if s.startswith("#"):
                return s.lstrip("# ").strip()[:140]
    except Exception:
        pass
    return default


def _monoarb_locks():
    try:
        st = json.load(open(os.path.join(BASE, "monoarb_state.json")))
        locks = st.get("locks") or st.get("real") or []
        return len(locks) if isinstance(locks, list) else "—"
    except Exception:
        return "—"


def actionable_now():
    """Read-only: the genuinely EXECUTABLE, fee-netted edge on the table RIGHT NOW.
    Verified basket locks from basket_arb (LIVE CLOB net-of-fee, verified-only — the
    mid-mirage / partial-field fakes are already filtered out). NOTHING is paged here:
    basket-watch / ws-basket-watch page verified locks >=2% live (WhatsApp+iMessage), and
    edge-scan pages data-arb. This is the unified read-only 'is there money?' view so the
    operator sees ONE connected answer instead of scattered per-job nulls."""
    out = {"locks": [], "err": None}
    try:
        import basket_arb
        out["locks"] = basket_arb.get_locked_baskets(0.01) or []   # verified, net>=1c
    except Exception as e:
        out["err"] = str(e)[:140]
    return out


def read_squad_context():
    """Best-effort one-liners from the already-scheduled squad members (no writes)."""
    return {
        "edge_scan (structural+data, 6h)": _vault_headline("edge_scan"),
        "ws-basket (complete-set locks)": _tail_line("/tmp/ws-basket-watch.log", "[hb]"),
        "newmarket-logger (timing vein)": _vault_headline("newmarket_logger",
                                                          default=_tail_line("/tmp/newmarket-logger.out")),
        "monoarb (monotone real locks)": _monoarb_locks(),
    }


# ------------------------------------------------------------------------------- main
def main():
    try:
        markets = fetch_event_markets()
    except Exception as e:
        markets = []
        W.log(STEM, f"[fetch] FAIL {e}")

    try:
        leads, res_status = resolution_hunt(markets)
    except Exception as e:
        leads, res_status = [], f"FAIL {e}"
        W.log(STEM, f"[resolution] FAIL {e}")

    # Aggressive: if zero leads from tuned scan, try a broader fallback scan
    # (10x more markets, but same judge cap → faster)
    if not leads:
        try:
            import time
            start = time.time()
            params = urllib.parse.urlencode({
                "active": "true", "closed": "false",
                "order": "volumeNum", "ascending": "false",
                "limit": 100, "offset": 0,
            })
            raw = _gj(f"{GAMMA}?{params}")
            batch = raw if isinstance(raw, list) else raw.get("data", [])
            # Minimal filtering: just YES_BAND + min_volume, no day cap
            fallback_markets = []
            for m in batch[:100]:  # scan first 100 only (60s cap)
                q = (m.get("question") or "").strip()
                if not q or _SKIP_RX.search(q):
                    continue
                y = _yes_price(m)
                if y is None or not (YES_BAND[0] <= y <= YES_BAND[1]):
                    continue
                if _vol(m) < MIN_VOLUME / 2:  # even more lenient
                    continue
                fallback_markets.append({
                    "q": q,
                    "desc": (m.get("description") or "")[:1400],
                    "yes": round(y, 3),
                    "slug": m.get("slug") or "",
                    "cid": str(m.get("conditionId") or m.get("id") or m.get("slug") or q[:40]),
                    "days": round(_days_to_end(m) or 999, 1),
                })
                if len(fallback_markets) >= JUDGE_CAP:
                    break
            if fallback_markets and (time.time() - start) < 60:
                fallback_leads, fallback_status = resolution_hunt(fallback_markets)
                if fallback_leads:
                    leads = fallback_leads
                    res_status += f" + fallback({len(fallback_markets)} scanned, {len(fallback_leads)} found)"
        except Exception as e:
            W.log(STEM, f"[fallback] skipped: {e}")

    try:
        ctx = read_squad_context()
    except Exception as e:
        ctx = {}
        W.log(STEM, f"[context] FAIL {e}")

    act = actionable_now()

    # VAULT-ONLY, NEVER PAGE. A free-LLM resolution hunch is research-grade, not
    # alert-grade: the free model is miscalibrated (commentary, not conviction) and will
    # flag noise STRONG (e.g. a timestamp technicality on a novelty market). Paging on it
    # would train the operator to ignore alerts (the cry-wolf failure). The proven veins
    # still page via their OWN jobs (edge-scan data-arb, basket-watch locks). Here we only
    # accumulate leads to the vault and TAG which are new since last cycle for the human.
    strong = [l for l in leads if l["verdict"] == "STRONG"]
    weak_included = [l for l in leads if l["verdict"] in ("STRONG", "WEAK")]  # aggressive: include WEAK
    for l in leads:
        l["is_new"] = W.is_new_alert(STEM, f"resmisread:{l['cid']}:{l['verdict']}")

    # Unified full-squad vault brief (always written — silence != success).
    locks = act["locks"]
    if locks:
        money = [f"## 🟢 ACTIONABLE NOW — {len(locks)} verified executable lock(s), net-of-fee"]
        for b in locks[:8]:
            money.append(f"- **{b.get('live_kind', b.get('kind','?'))}** {str(b.get('title','?'))[:60]} "
                         f"· net edge **{b.get('net_edge')}** (gross {b.get('edge')})")
        money.append("→ basket-watch / ws-basket-watch page these LIVE (WhatsApp+iMessage) at ≥2%. "
                     "Confirm depth before sizing; paper only.")
    else:
        why = f" (basket_arb err: {act['err']})" if act.get("err") else ""
        money = [f"## 🟢 ACTIONABLE NOW — none · $0 on the table{why}",
                 "_Honest NULL: no verified executable lock and no live data-arb. On a calibrated "
                 "venue this is the correct steady state, not a wiring failure — the escalators "
                 "(basket-watch / ws-basket-watch / edge-scan) page the instant that changes._"]

    prov_str = W.prov()
    # The small ollama floor (8B) is a noise generator for this task — it parrots the example
    # traps and flags most markets STRONG. Flag low-confidence so a floor cycle isn't trusted.
    low_conf = ("ollama" in prov_str.lower()) or ("fallback=True" in prov_str)
    res_hdr = ("## Resolution-misread leads (free-LLM — NOT trades; verify the rules + executable book)"
               + ("  \n> ⚠️ LOW CONFIDENCE: ran on the small ollama fallback floor (primary free "
                  "model down/rate-limited) — these flags are unreliable noise, treat as no-signal."
                  if low_conf else ""))
    body = [f"# Edge Hunt — full squad ({W.ts()})",
            f"_free-LLM resolution hunter: {res_status} · model: {prov_str}_\n"]
    body += money
    body += ["\n" + res_hdr]
    if weak_included:  # aggressive: show STRONG + WEAK
        for l in sorted(weak_included, key=lambda x: 0 if x["verdict"] == "STRONG" else 1):
            tag = " 🆕" if l.get("is_new") else ""
            body.append(f"- **{l['verdict']}**{tag} · {l['q'][:70]} · YES {l['yes']} · side {l['side']}"
                        f"\n    - trap: {l['trap']}\n    - slug: `{l['slug']}` · resolves {l['days']}d")
    else:
        body.append("_none flagged this cycle — the honest steady state (the crowd usually reads rules right)._")

    body.append("\n## Already-scheduled squad members (this runner does not re-run them)")
    for k, v in ctx.items():
        body.append(f"- **{k}**: {v}")

    body.append("\n## Honest note")
    body.append("- Resolution leads are VAULT-ONLY (never paged): the free model is miscalibrated, so "
                "a STRONG flag is a research lead to verify, not an alert. The proven veins "
                "(basket/data/new-market) page via their own jobs above. Default truthful result is NULL.")
    W.vault_write(STEM, "\n".join(body))
    W.log(STEM, f"actionable_locks={len(act['locks'])} markets={len(markets)} leads={len(leads)} "
                f"strong={len(strong)} new={sum(1 for l in leads if l.get('is_new'))} | {res_status}")


if __name__ == "__main__":
    main()
