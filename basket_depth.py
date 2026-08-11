"""
basket_depth.py — depth + exhaustiveness verifier for basket_arb locks.

basket_arb.py SCREENS (bestAsk only, Σmid proxy). This VERIFIES the two things
that turn a screened lock into a sizeable one:

  1. EXHAUSTIVENESS (the "untracked other outcome" risk): each leg's CLOB book
     carries a `neg_risk` flag. Polymarket's neg-risk adapter augments these
     markets so NO == sum of the other YESes — i.e. the field is EXHAUSTIVE BY
     PROTOCOL. If every leg reports neg_risk=true, a missing tail outcome cannot
     sink the lock. That's the caveat in BRAIN #14, resolved at the book level.

  2. FILLABLE DEPTH: bestAsk may be 1 share. We walk each leg's ask ladder and
     find the max number of baskets q such that Σ(VWAP_ask_leg(q)) < 1 — the
     real locked edge shrinks as size grows. Reports edge at $50/$200/$1k payout.

Read-only. Paper research. No keys, no funds, no orders.
"""
import json, sys, urllib.request
from pathlib import Path
from datetime import datetime, timezone
import edge_common as ec
import basket_arb

VAULT = Path.home() / "Documents/PolymarketVault/Bot"
BOOK = "https://clob.polymarket.com/book?token_id={}"


def fetch_book(token):
    if not token:
        return None
    try:
        req = urllib.request.Request(BOOK.format(token), headers=ec.UA)
        return json.load(urllib.request.urlopen(req, timeout=15))
    except Exception:
        return None


def ask_ladder(book):
    """Ascending (price, size) asks — cheapest first. CLOB returns descending."""
    if not book:
        return []
    out = []
    for lvl in book.get("asks", []):
        try:
            out.append((float(lvl["price"]), float(lvl["size"])))
        except (KeyError, ValueError, TypeError):
            continue
    out.sort(key=lambda t: t[0])
    return out


def cost_to_buy(ladder, q):
    """Cost (and avg px) to buy q shares walking the ask ladder. None if not enough depth."""
    need, paid = q, 0.0
    for price, size in ladder:
        take = min(need, size)
        paid += take * price
        need -= take
        if need <= 1e-9:
            return paid
    return None  # insufficient depth for q shares


def verify_basket(b, legs):
    """Fetch books for every leg, return depth-aware verification of a LONG lock."""
    books = []
    for m in legs:
        bk = fetch_book(m.get("yes_token"))
        books.append(bk)

    # exhaustiveness by protocol: every leg's book flags neg_risk
    flags = [bool(bk.get("neg_risk")) if bk else False for bk in books]
    protocol_exhaustive = all(flags) and len(flags) > 0

    ladders = [ask_ladder(bk) for bk in books]
    if any(not l for l in ladders):
        return {**b, "depth_ok": False, "protocol_exhaustive": protocol_exhaustive,
                "reason": "a leg has no ask liquidity", "tiers": {}}

    # max lockable: largest q (shares per leg = baskets, each pays $1) with Σcost < 1 per basket
    tiers = {}
    for payout in (50, 200, 1000):
        q = payout  # q baskets => $q payout (one leg pays $1 each)
        per_leg = [cost_to_buy(l, q) for l in ladders]
        if any(c is None for c in per_leg):
            tiers[payout] = None  # not enough depth at this size
            continue
        total_cost = sum(per_leg)
        edge = payout - total_cost  # locked $ profit (payout q*1 minus cost)
        tiers[payout] = {"cost": round(total_cost, 2), "edge": round(edge, 2),
                         "edge_pct": round(edge / payout * 100, 2)}

    best_size = max((p for p, v in tiers.items() if v and v["edge"] > 0), default=0)
    return {**b, "depth_ok": best_size > 0, "protocol_exhaustive": protocol_exhaustive,
            "max_locked_payout": best_size, "tiers": tiers,
            "reason": "ok" if best_size else "edge gone once depth-walked"}


def verify_top(n=8, max_legs=12):
    """Verify the top screened LONG locks (skip huge-leg events to bound book calls)."""
    screened = [b for b in basket_arb.scan_baskets()
                if b["verified"] and b["kind"] == "LONG" and b["n_outcomes"] <= max_legs]
    evs = {e["title"]: e["markets"] for e in ec.poly_events(pages=10)}
    results = []
    for b in screened[:n]:
        legs = evs.get(b["title"])
        if not legs:
            continue
        results.append(verify_basket(b, legs))
    return results


def snapshot():
    res = verify_top()
    ts = datetime.now(timezone.utc).isoformat()[:16]
    real = [r for r in res if r["depth_ok"] and r["protocol_exhaustive"]]
    lines = [
        "# Basket Arb — Depth + Exhaustiveness Verified",
        f"_Last update: {ts}_  ·  {len(res)} screened locks book-walked, **{len(real)} sizeable & exhaustive**",
        "",
        "`neg_risk=true` on every leg = exhaustive BY PROTOCOL (no untracked outcome can",
        "sink the lock — BRAIN #14 caveat resolved at the book). Depth = real $ lockable",
        "after walking the ask ladder (bestAsk alone lies about size).",
        "",
        "| event | #out | protocol-exh | $50 | $200 | $1k | verdict |",
        "|-------|------|--------------|-----|------|-----|---------|",
    ]
    def cell(t):
        return f"+${t['edge']}" if t else "—"
    for r in sorted(res, key=lambda r: -(r.get("max_locked_payout") or 0)):
        t = r["tiers"]
        verdict = "✅ SIZEABLE" if (r["depth_ok"] and r["protocol_exhaustive"]) else (
            "⚠️ not exhaustive" if not r["protocol_exhaustive"] else "❌ thin")
        lines.append(f"| {r['title'][:34]} | {r['n_outcomes']} | "
                     f"{'yes' if r['protocol_exhaustive'] else 'NO'} | "
                     f"{cell(t.get(50))} | {cell(t.get(200))} | {cell(t.get(1000))} | {verdict} |")
    lines += ["", "See [[EDGE_SCAN_FINDINGS]] · screen = basket_arb.py, this = depth confirm.", ""]
    VAULT.mkdir(parents=True, exist_ok=True)
    (VAULT / "basket_depth.md").write_text("\n".join(lines), encoding="utf-8")
    return len(real)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "snapshot":
        n = snapshot()
        print(f"✓ basket_depth.md written: {n} sizeable+exhaustive locks")
    else:
        res = verify_top()
        print(f"Verified {len(res)} screened LONG locks (book-walked):\n")
        for r in sorted(res, key=lambda r: -(r.get("max_locked_payout") or 0)):
            t = r["tiers"]
            exh = "exhaustive" if r["protocol_exhaustive"] else "NOT-exhaustive(!)"
            print(f"  {r['title'][:40]:40} n={r['n_outcomes']:>2} [{exh}]")
            for size in (50, 200, 1000):
                tv = t.get(size)
                if tv:
                    print(f"     ${size:>4} payout -> cost ${tv['cost']:>7.2f}  locked +${tv['edge']:>6.2f} ({tv['edge_pct']:+.2f}%)")
                else:
                    print(f"     ${size:>4} payout -> insufficient depth")
            print(f"     reason: {r['reason']}")
