"""
basket_arb.py — reusable combinatorial basket-arb engine.

The one survivor of the 5-edge scan (EDGE_SCAN_FINDINGS.md): mutually-exclusive
(negRisk) events where buying every YES costs < $1 (long lock) or every NO costs
< $(N-1) (short lock). One outcome pays $1; the basket is locked profit, immune
to gap-through / calibration / spread-on-exit (held to resolution, no view).

THE EXHAUSTIVENESS FILTER (kills the artifacts):
  A real lock needs the FULL outcome set priced AND summing to ~1.00 at mid. Two
  failure modes are rejected: (1) "Σask=0.21, 23 outcomes" baskets whose listed
  outcomes cover only a fraction of probability (Σmid far from 1); and (2)
  PARTIAL-FIELD fakes where the gamma feed returns only the LIQUID legs (e.g. 2 of
  13, 36 of 128) so Σmid≈1 looks complete, but the omitted placeholder outcomes
  carry the residual tail — buying the subset is a directional bet, not a lock.
  We require complete_field (priced legs == full live outcome set, non-closed)
  AND Σmid ∈ [lo, hi] before trusting any Σask<1 as a real lock.

Public API (legs + brain import these):
  get_locked_baskets(min_edge=0.01) -> list of verified locked baskets
  best_basket_edge()                -> single best locked edge (float, 0 if none)
  snapshot_basket()                 -> write vault/Bot/basket_arb.md

Read-only. Paper research. No keys, no funds, no Postgres.
"""
import json, sys
from pathlib import Path
from datetime import datetime, timezone
import edge_common as ec

VAULT = Path.home() / "Documents/PolymarketVault/Bot"

# exhaustiveness window: a complete mutually-exclusive field sums ~1.0 at mid.
EXHAUST_LO, EXHAUST_HI = 0.93, 1.08
MIN_LEG_VOL = 1000          # ignore dead legs with no real quotes
MIN_EDGE = 0.01             # 1¢ locked minimum to bother (applied to NET-of-fee live edge)
CLOB_BOOK = "https://clob.polymarket.com/book?token_id="   # LIVE executable book

# Category TAKER-fee schedule (Polymarket, since 2026-03-30; mirrors basketlock.py:6-9).
# Maker = FREE. Taker fee per share = rate · p · (1−p) (peak rate/4 at p=0.5, →0 near 0/1).
# Held complete-set locks pay ZERO settlement fee — the only cost is the entry taker fee.
# (arb-lock-vet 2026-06-21 BUG 2: the entry gate used GROSS edge, so multi-leg SHORT baskets
#  with fat mid-probability legs booked while net-NEGATIVE once the taker haircut is paid.)
FEE_RATE_BY_CAT = {
    "geopolitics": 0.0, "world": 0.0,        # geopolitics / world-events: FREE
    "politics": 0.04, "finance": 0.04, "macro": 0.04,
    "econ": 0.05, "culture": 0.05, "weather": 0.05, "science": 0.05,
    "sports": 0.03, "esports": 0.03,
    "crypto": 0.07,
}
FEE_RATE_DEFAULT = 0.05     # unknown category → conservative mid rate (prefer false-reject)
# keyword → category inference from the event title (legs carry no category field).
_CAT_KEYWORDS = [
    ("geopolitics", r"war|ceasefire|hormuz|iran|israel|ukraine|russia|nato|gaza|airspace|sanction|invade|blockade"),
    ("crypto", r"bitcoin|btc|ethereum|eth|solana|crypto|token|fdv|stablecoin|altcoin"),
    ("finance", r"\bfed\b|rate cut|interest rate|gold|s&p|nasdaq|dow|treasury|cpi|inflation|recession|gdp|jobs"),
    ("politics", r"president|election|senate|midterm|nomination|congress|prime minister|parliament|governor"),
    ("weather", r"hurricane|temperature|hottest|volcano|eruption|earthquake|weather|climate"),
    ("sports", r"\bnba\b|\bnfl\b|\bmlb\b|\bnhl\b|world cup|champions|finals|match|tournament|wins the"),
]


def _cat_rate(title):
    """Best-effort category taker-fee rate inferred from the event title."""
    import re
    t = (title or "").lower()
    for cat, pat in _CAT_KEYWORDS:
        if re.search(pat, t):
            return FEE_RATE_BY_CAT.get(cat, FEE_RATE_DEFAULT)
    return FEE_RATE_DEFAULT


def _taker_fee(price, rate):
    """Taker fee per share for a leg filled at `price` (rate · p · (1−p)). 0 for free categories."""
    p = max(0.0, min(1.0, price))
    return rate * p * (1.0 - p)


def _leg_quotes(m):
    """Return (ask, bid) for a leg, falling back to mid when a side is missing."""
    ask = m["ask"] if m["ask"] is not None else m["yes"]
    bid = m["bid"] if m["bid"] is not None else m["yes"]
    return ask, bid


def _live_clob_quotes(token):
    """LIVE executable best-ask / best-bid from the CLOB order book — NOT gamma's stale
    bestAsk/bestBid snapshot. best-ask = lowest ask (cheapest to buy), best-bid = highest bid.
    Returns (None, None) on any failure or an empty side (fail-soft → leg can't be locked)."""
    if not token:
        return None, None
    try:
        d = ec._get(CLOB_BOOK + str(token)) or {}
        asks = sorted(float(a["price"]) for a in d.get("asks", []) if a.get("price") not in (None, ""))
        bids = sorted((float(b["price"]) for b in d.get("bids", []) if b.get("price") not in (None, "")),
                      reverse=True)
        return (asks[0] if asks else None), (bids[0] if bids else None)
    except Exception:
        return None, None


def _live_basket_edge(legs, fee_rate=FEE_RATE_DEFAULT):
    """Re-price a candidate basket on the LIVE CLOB book, NET of taker fees. Returns the live
    edge dict, or None if ANY leg lacks a live two-sided book (then it is not an executable lock).
    BUG 1 (mark-to-mid): gamma snapshots are stale/wrong-side, so 7 of 8 booked 'locks' were
    fiction — a basket is verified only if the LIVE executable edge still clears. BUG 2 (fee):
    `edge` is now NET of the entry taker fee (rate·p·(1−p) per leg), since the gross edge alone
    booked net-negative SHORT baskets. LONG pays the fee on the YES ask; SHORT on the NO leg
    (price 1−bid, fee symmetric = rate·bid·(1−bid))."""
    asks, bids = [], []
    for m in legs:
        a, b = _live_clob_quotes(m.get("yes_token"))
        if a is None or b is None:
            return None
        asks.append(a); bids.append(b)
    s_ask, s_bid = sum(asks), sum(bids)
    long_fee = sum(_taker_fee(a, fee_rate) for a in asks)   # buy YES @ ask
    short_fee = sum(_taker_fee(b, fee_rate) for b in bids)  # buy NO (1−bid); fee symmetric in bid
    long_net = (1.0 - s_ask) - long_fee
    short_net = (s_bid - 1.0) - short_fee
    return {"sum_ask": round(s_ask, 3), "sum_bid": round(s_bid, 3),
            "fee_rate": fee_rate,
            "long_gross": round(1.0 - s_ask, 3), "short_gross": round(s_bid - 1.0, 3),
            "long_edge": round(long_net, 3), "short_edge": round(short_net, 3),
            "fee": round(max(long_fee, short_fee), 4),
            "edge": round(max(long_net, short_net), 3)}   # NET-of-fee executable edge


def scan_baskets(pages=20):
    """All mutually-exclusive events with their long/short locked edges + exhaustiveness."""
    evs = ec.poly_events(pages=pages)
    out = []
    for e in evs:
        if not e["mutually_exclusive"]:
            continue
        legs = e["markets"]
        if len(legs) < 2:
            continue
        # require every leg to have a real two-sided quote (else field is dead/incomplete)
        if any(m["bid"] is None or m["ask"] is None for m in legs):
            # still allow, but mark — these are lower-confidence
            pass
        mids = [m["yes"] for m in legs]
        asks = [_leg_quotes(m)[0] for m in legs]
        bids = [_leg_quotes(m)[1] for m in legs]
        n = len(legs)
        # n_markets_total = full live outcome set (incl. unpriced placeholder legs the gamma
        # feed omits). complete_field rejects PARTIAL-FIELD fake locks: Σmid≈1 over the
        # RETURNED legs is necessary but NOT sufficient — the dropped placeholders price ~0
        # and carry the residual tail. Buying a subset of a 13- or 128-slot field is a
        # DIRECTIONAL bet (loses full notional if an omitted outcome wins), not a lock.
        # (Confirmed live 2026-06-20: Tennessee 2/13, Minnesota 2/13, French 36/128.)
        n_total = e.get("n_markets_total", n)
        complete_field = (n == n_total)
        s_mid, s_ask, s_bid = sum(mids), sum(asks), sum(bids)
        long_edge = 1.0 - s_ask                 # buy all YES @ ask, one pays 1
        short_edge = s_bid - 1.0                # buy all NO, payout N-1, cost N-Σbid
        exhaustive = EXHAUST_LO <= s_mid <= EXHAUST_HI
        two_sided = all(m["bid"] is not None and m["ask"] is not None for m in legs)
        edge = max(long_edge, short_edge)             # GROSS gamma edge (display/prefilter only)
        kind = "LONG" if long_edge >= short_edge else "SHORT"
        fee_rate = _cat_rate(e["title"])              # category taker rate for the NET gate
        # gamma gate (stale snapshot, GROSS) — a cheap necessary-not-sufficient prefilter.
        gamma_pass = exhaustive and two_sided and complete_field and edge >= MIN_EDGE
        # LIVE-CLOB re-confirmation NET of taker fee (arb-lock-vet 2026-06-21, BUG 1 + BUG 2):
        # gamma bestAsk/bestBid is a stale snapshot AND the gross edge ignores the entry taker
        # fee, so a gamma-pass basket is a REAL lock only if the LIVE executable edge NET of fee
        # still clears MIN_EDGE. Only re-fetch CLOB for the few gamma-passers (keeps load low).
        live = _live_basket_edge([{"yes_token": m.get("yes_token")} for m in legs], fee_rate) if gamma_pass else None
        verified = bool(gamma_pass and live and live["edge"] >= MIN_EDGE)
        net_edge = live["edge"] if live else None    # NET-of-fee executable edge (the honest number)
        live_kind = ("LONG" if live["long_edge"] >= live["short_edge"] else "SHORT") if live else kind
        out.append({
            "title": e["title"],
            "slug": e["slug"],
            "n_outcomes": n,
            "n_markets_total": n_total,
            "complete_field": complete_field,   # priced legs == full live outcome set
            "sum_mid": round(s_mid, 3),
            "sum_ask": round(s_ask, 3),
            "sum_bid": round(s_bid, 3),
            "long_edge": round(long_edge, 3),
            "short_edge": round(short_edge, 3),
            "edge": round(edge, 3),          # GROSS gamma edge (display/prefilter only)
            "kind": kind,
            "fee_rate": fee_rate,            # category taker rate applied to the NET gate
            "net_edge": net_edge,            # NET-of-fee LIVE executable edge — the honest number
            "live_kind": live_kind,          # side of the live net edge
            "exhaustive": exhaustive,        # Σmid ≈ 1 → complete field (necessary, NOT sufficient)
            "two_sided": two_sided,          # every leg has real bid+ask
            "gamma_pass": gamma_pass,        # passed the stale-snapshot GROSS gate
            "live_sum_ask": live["sum_ask"] if live else None,
            "live_edge": net_edge,           # NET-of-fee edge on the LIVE executable book
            "verified": verified,            # gamma_pass AND live NET executable edge clears
            # per-leg YES tokens — lets a paper book verify settlement after the event
            # leaves the active feed (basket_paper.py); resolved YES token prices to ~0/1.
            "legs": [{"yes_token": m.get("yes_token"), "condition_id": m.get("condition_id"),
                      "ask": _leg_quotes(m)[0], "bid": _leg_quotes(m)[1]} for m in legs],
        })
    out.sort(key=lambda d: d["edge"], reverse=True)
    return out


def get_locked_baskets(min_edge=MIN_EDGE):
    """VERIFIED locked baskets only (exhaustive + two-sided + LIVE NET edge≥min), sorted by the
    honest NET-of-fee live edge. This is the function legs/brain should call — gamma artifacts and
    fee-negative SHORT baskets already filtered out."""
    locks = [b for b in scan_baskets() if b["verified"] and (b["net_edge"] or 0) >= min_edge]
    locks.sort(key=lambda d: (d["net_edge"] or 0), reverse=True)
    return locks


def best_basket_edge():
    """Single best verified locked NET edge as a float (0.0 if none). For brain/leg gates."""
    v = get_locked_baskets()
    return v[0]["net_edge"] if v else 0.0


def snapshot_basket():
    """Write vault/Bot/basket_arb.md — verified locks + rejected artifacts (transparency)."""
    VAULT.mkdir(parents=True, exist_ok=True)
    allb = scan_baskets()
    verified = [b for b in allb if b["verified"]]
    ts = datetime.now(timezone.utc).isoformat()[:16]
    lines = [
        "# Basket Arb — Mutually-Exclusive Locks",
        f"_Last update: {ts}_  ·  {len(allb)} negRisk events, **{len(verified)} VERIFIED locks**",
        "",
        "A real lock needs the FULL outcome set priced. We require complete_field",
        f"(priced legs == all live outcomes) + Σmid ∈ [{EXHAUST_LO}, {EXHAUST_HI}] + every leg two-sided —",
        "this kills incomplete-field artifacts (Lebanon Σmid=0.15) AND partial-field fake",
        "locks (Tennessee 2/13, French 36/128 — Σmid≈1 but the omitted outcomes carry the tail).",
        "",
        "## ✅ Verified Locks (complete field + two-sided + LIVE NET-of-fee edge≥1¢)",
        "",
        "| net edge | kind | #out | fee | live Σask | gross | event |",
        "|----------|------|------|-----|-----------|-------|-------|",
    ]
    verified.sort(key=lambda d: (d["net_edge"] or 0), reverse=True)
    for b in verified[:30]:
        lines.append(f"| {(b['net_edge'] or 0):+.3f} | {b['live_kind']} | {b['n_outcomes']} | "
                     f"{b['fee_rate']:.2f} | {(b['live_sum_ask'] or 0):.3f} | {b['edge']:+.3f} | {b['title'][:42]} |")
    if not verified:
        lines.append("| — | — | — | — | — | — | _no verified locks this scan_ |")
    lines += [
        "",
        "## ❌ Rejected (edge≥1¢ but failed exhaustiveness/two-sided — artifacts)",
        "",
        "| raw edge | #out | Σmid | why rejected | event |",
        "|----------|------|------|--------------|-------|",
    ]
    rejected = [b for b in allb if b["edge"] >= MIN_EDGE and not b["verified"]]
    for b in rejected[:15]:
        why = []
        if not b.get("complete_field", True):
            why.append(f"partial field {b['n_outcomes']}/{b['n_markets_total']}")
        if not b["exhaustive"]:
            why.append(f"Σmid={b['sum_mid']:.2f} incomplete")
        if not b["two_sided"]:
            why.append("missing quotes")
        lines.append(f"| {b['edge']:+.3f} | {b['n_outcomes']} | {b['sum_mid']:.3f} | "
                     f"{', '.join(why)} | {b['title'][:38]} |")
    lines += ["", "See [[EDGE_SCAN_FINDINGS]] — basket arb is the lead survivor of the 5-edge scan.", ""]
    (VAULT / "basket_arb.md").write_text("\n".join(lines), encoding="utf-8")
    return len(verified)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "scan"
    if cmd == "snapshot":
        n = snapshot_basket()
        print(f"✓ basket_arb.md written: {n} verified locks")
    else:
        allb = scan_baskets()
        verified = [b for b in allb if b["verified"]]
        verified.sort(key=lambda d: (d["net_edge"] or 0), reverse=True)
        print(f"negRisk events: {len(allb)}  |  VERIFIED locks (live NET-of-fee): {len(verified)}\n")
        print(f"  {'netEdg':>7} {'kind':>5} {'#out':>4} {'fee':>5} {'livAsk':>6} {'gross':>6}  event")
        print("  " + "-"*70)
        for b in verified[:25]:
            print(f"  {(b['net_edge'] or 0):>+7.3f} {b['live_kind']:>5} {b['n_outcomes']:>4} "
                  f"{b['fee_rate']:>5.2f} {(b['live_sum_ask'] or 0):>6.3f} {b['edge']:>+6.3f}  {b['title'][:40]}")
        if not verified:
            print("  (none verified — see rejected artifacts via `snapshot`)")
        print(f"\n  best_basket_edge() = {best_basket_edge():+.3f}")
