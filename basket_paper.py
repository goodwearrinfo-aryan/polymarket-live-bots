#!/usr/bin/env python3
"""basket_paper.py — paper track record for basket_arb locked baskets.

basket_arb finds locks (Σask<1 long, or Σbid>1 short) that are +EV BY CONSTRUCTION.
So the open question is NOT "is edge>0" (it is) — it's whether locks PERSIST long
enough to enter, RESOLVE cleanly, and how many/how much accrue over time. This paper
book ENTERS every verified lock once at a fixed $50 payout notional, HOLDS to
resolution, and books the locked profit — accumulating the n≥30 real-resolution
track record that graduates basket_arb from "theoretical" to "confirmed" per the
program's gate (n≥30 + CI>0; here CI>0 is structural, so the record proves the
strategy is REAL & RECURRING with real capacity, not that edge has the right sign).

Resolution detection: an open basket settles when its event has been ABSENT from the
active mutually-exclusive feed for ≥2 consecutive cycles (the 2-cycle guard avoids a
false settle on a transient quote gap). Once ENTERED a lock is held regardless of the
current quote — the edge was locked at entry; a vanishing edge ≠ resolution.

Realized P&L = the entry-locked edge × notional. This assumes the field resolved with
exactly one winner (guaranteed for a complete mutually-exclusive field; the
exhaustiveness filter enforces Σmid≈1 at entry). SETTLEMENT-VERIFIED realized (fetch
each leg's CLOB settled price, confirm exactly one YES paid) is the honest next upgrade.

PAPER research — read-only, no orders, no funds, no Postgres.

  python3 basket_paper.py            # one cycle: enter new locks, settle resolved, report
  python3 basket_paper.py --report   # print the book + stats only (no state change)
"""
import json, os, sys, random, urllib.request
from datetime import datetime, timezone
from pathlib import Path
import basket_arb

random.seed(42)
BASE = os.path.dirname(os.path.abspath(__file__))
BOOK = os.path.join(BASE, "basket_paper_book.json")
VAULT = Path.home() / "Documents/PolymarketVault/Bot"
NOTIONAL = 50.0            # $ payout/basket — small size where the lock holds (basket_depth.py)
MIN_EDGE = 0.01
DROP_AFTER_MISSES = 5      # absent from feed AND never cleanly settles → delisted, drop (no profit)


def _settled_yes(condition_id):
    """YES-token price via /markets/{cid} — returns 1/0 for a SETTLED market (the live
    /price?token_id endpoint 404s once the order book is gone). None if unresolved/error."""
    if not condition_id:
        return None
    try:
        ep = f"https://clob.polymarket.com/markets/{condition_id}"
        req = urllib.request.Request(ep, headers={"User-Agent": "basket-paper/1.0"})
        d = json.loads(urllib.request.urlopen(req, timeout=10).read())
        if not d.get("closed"):
            return None                 # not resolved yet
        toks = d.get("tokens") or []
        yes = next((t for t in toks if str(t.get("outcome", "")).lower() == "yes"),
                   toks[0] if toks else None)
        return float(yes["price"]) if yes and yes.get("price") is not None else None
    except Exception:
        return None


def _basket_settlement(legs):
    """(all_resolved, winning_yes_sum). all_resolved iff EVERY leg's market is closed;
    winning_yes_sum ≈ number of winning YES legs among ours (should be ~1 for a clean lock)."""
    if not legs:
        return (False, 0.0)
    s = 0.0
    for lg in legs:
        p = _settled_yes(lg.get("condition_id"))
        if p is None:
            return (False, 0.0)        # a leg not yet resolved → event not settled
        s += 1.0 if p >= 0.5 else 0.0
    return (True, s)


def load():
    if os.path.exists(BOOK):
        try:
            return json.load(open(BOOK))
        except Exception:
            pass
    return {"open": {}, "closed": [], "voided": []}  # "voided" carried explicitly so a reset/corrupt-file default doesn't silently drop the 2026-06-20 purge history


def save(b):
    tmp = BOOK + ".tmp"
    json.dump(b, open(tmp, "w"), indent=2)
    os.replace(tmp, BOOK)


def boot_ci(xs, n=4000):
    if len(xs) < 2:
        return (None, None)
    ms = []
    for _ in range(n):
        s = [xs[random.randrange(len(xs))] for _ in xs]
        ms.append(sum(s) / len(s))
    ms.sort()
    return (ms[int(0.025 * n)], ms[int(0.975 * n)])


def cycle():
    book = load()
    all_baskets = basket_arb.scan_baskets()            # one fetch
    active_slugs = {b["slug"] for b in all_baskets}    # all active mutually-exclusive events
    # book on the LIVE NET-of-fee edge (arb-lock-vet 2026-06-21): b["edge"] is the GROSS gamma
    # snapshot — booking it was the residual mark-to-mid leak (expected_pnl was fiction). The
    # honest, executable, fee-paid number is b["net_edge"]; "verified" already gates on it.
    locks = [b for b in all_baskets if b["verified"] and (b.get("net_edge") or 0) >= MIN_EDGE]

    # ENTER: every verified lock we don't already hold and didn't recently close
    recent_closed = {c["slug"] for c in book["closed"][-300:]}
    entered = []
    for b in locks:
        slug = b["slug"]
        if slug in book["open"] or slug in recent_closed:
            continue
        net = b.get("net_edge") or 0.0
        live_kind = b.get("live_kind", b["kind"])
        rec = {
            "slug": slug, "title": b["title"], "kind": live_kind,
            "edge": net,                 # NET-of-fee live edge (the honest booked edge)
            "gross_edge": b["edge"],     # gamma gross, kept for transparency
            "lock_cost": b.get("live_sum_ask") if live_kind == "LONG" else b["sum_bid"],
            "fee_rate": b.get("fee_rate"),
            "sum_ask": b["sum_ask"], "sum_bid": b["sum_bid"], "n_outcomes": b["n_outcomes"],
            "notional": NOTIONAL, "expected_pnl": round(net * NOTIONAL, 4),
            "legs": b.get("legs", []),   # per-leg yes_tokens for settlement verification
            "entry_ts": datetime.now(timezone.utc).isoformat(), "miss": 0,
        }
        book["open"][slug] = rec
        entered.append(rec)

    # SETTLE: once a basket leaves the active feed, VERIFY settlement via each leg's CLOB
    # token price (winner→~1, losers→~0) and book the ACTUAL realized P&L — this catches the
    # failure case (field didn't resolve to exactly one winner) instead of assuming the lock held.
    settled = []
    for slug in list(book["open"].keys()):
        rec = book["open"][slug]
        if slug in active_slugs:
            rec["miss"] = 0
            continue
        all_pinned, yes_sum = _basket_settlement(rec.get("legs", []))
        if not all_pinned:
            rec["miss"] = rec.get("miss", 0) + 1
            if rec["miss"] >= DROP_AFTER_MISSES:     # delisted without resolving → drop, no profit
                rec = book["open"].pop(slug)
                rec["exit_ts"] = datetime.now(timezone.utc).isoformat()
                rec["realized_pnl"] = 0.0
                rec["status"] = "dropped_unresolved"
                rec["note"] = f"left feed but never settled cleanly in {rec['miss']} cycles — not booked"
                book["closed"].append(rec); settled.append(rec)
            continue
        rec = book["open"].pop(slug)
        if rec["kind"] == "LONG":          # bought all YES @ ask; winner pays NOTIONAL
            realized = NOTIONAL * (yes_sum - rec["sum_ask"])
        else:                              # SHORT: sold all YES @ bid; pay NOTIONAL per winner
            realized = NOTIONAL * (rec["sum_bid"] - yes_sum)
        rec["exit_ts"] = datetime.now(timezone.utc).isoformat()
        rec["settled_yes_sum"] = round(yes_sum, 3)
        rec["realized_pnl"] = round(realized, 4)
        rec["lock_held"] = abs(yes_sum - 1.0) < 0.05   # exactly one winner = clean lock
        rec["status"] = "settled"
        rec["note"] = ("clean 1-winner lock" if rec["lock_held"]
                       else f"FIELD ANOMALY: {yes_sum:.0f} winners among our legs — booked honestly")
        book["closed"].append(rec); settled.append(rec)

    save(book)
    return book, entered, settled


def report(book):
    closed = book["closed"]
    n = len(closed)
    print(f"BASKET PAPER BOOK — open={len(book['open'])}  resolved={n}")
    if n:
        pnls = [c["realized_pnl"] for c in closed]
        total = sum(pnls)
        lo, hi = boot_ci(pnls)
        ci = f"[{lo:+.3f},{hi:+.3f}]" if lo is not None else "—"
        grad = "✅ GRADUATE (n≥30, CI>0)" if (n >= 30 and lo is not None and lo > 0) else f"accumulating ({n}/30)"
        print(f"  realized ${total:+.2f} over {n} resolved | mean ${total/n:+.3f}/basket | 95%CI {ci} (CI>0 structural) | {grad}")
    if book["open"]:
        exp = sum(r["expected_pnl"] for r in book["open"].values())
        print(f"  open locks: {len(book['open'])}  locked ${exp:+.2f} pending resolution")
        for r in sorted(book["open"].values(), key=lambda x: -x["edge"])[:8]:
            print(f"    {r['kind']:5} +{r['edge']*100:4.1f}%  cost {r['lock_cost']:.3f}  {r['title'][:44]}")


def snapshot_vault(book):
    """Mirror the paper book to the Obsidian vault (fail-soft). Refreshes every cycle."""
    try:
        VAULT.mkdir(parents=True, exist_ok=True)
        closed = book["closed"]
        n = len(closed)
        L = ["# Basket Arb — Paper Track Record",
             f"_Updated {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}. Settlement-verified (per-leg CLOB)._", ""]
        if n:
            pnls = [c["realized_pnl"] for c in closed]
            total = sum(pnls)
            lo, hi = boot_ci(pnls)
            ci = f"[{lo:+.3f}, {hi:+.3f}]" if lo is not None else "—"
            grad = "✅ GRADUATE" if (n >= 30 and lo is not None and lo > 0) else f"accumulating ({n}/30)"
            anomalies = sum(1 for c in closed if c.get("status") == "settled" and not c.get("lock_held", True))
            L += [f"- **resolved**: {n}  | **realized**: ${total:+.2f}  | mean ${total/n:+.3f}/basket",
                  f"- **95% CI**: {ci} (CI>0 is structural for locks)  | **{grad}**",
                  f"- **field anomalies** (lock_held=False): {anomalies}", ""]
        else:
            L += ["- no baskets resolved yet — accumulating", ""]
        if book["open"]:
            exp = sum(r["expected_pnl"] for r in book["open"].values())
            L += [f"## Open locks ({len(book['open'])}) — ${exp:+.2f} pending", "",
                  "| kind | edge | cost | event |", "|---|---|---|---|"]
            for r in sorted(book["open"].values(), key=lambda x: -x["edge"]):
                L.append(f"| {r['kind']} | +{r['edge']*100:.1f}% | {r['lock_cost']:.3f} | {r['title'][:50]} |")
            L.append("")
        if n:
            L += ["## Recently resolved", "", "| realized | held? | event |", "|---|---|---|"]
            for c in closed[-12:][::-1]:
                held = "✅" if c.get("lock_held") else ("⚠️ NO" if c.get("status") == "settled" else "—")
                L.append(f"| ${c['realized_pnl']:+.2f} | {held} | {c['title'][:50]} |")
        L += ["", "Related: [[basket_arb]] · [[basket_depth]]"]
        (VAULT / "basket_paper.md").write_text("\n".join(L), encoding="utf-8")
    except Exception as e:
        print(f"[basket_paper] vault snapshot failed: {e}")


def alert_settlements(settled):
    """Loud alert (WhatsApp + iMessage, fail-soft) when baskets settle — flags failures."""
    real = [s for s in settled if s.get("status") in ("settled", "dropped_unresolved")]
    if not real:
        return
    won = sum(s["realized_pnl"] for s in real)
    anomalies = [s for s in real if s.get("status") == "dropped_unresolved" or not s.get("lock_held", True)]
    lines = [f"🧺 BASKET ARB — {len(real)} settled, ${won:+.2f} realized"]
    for s in real:
        flag = "✅" if s.get("lock_held") else "⚠️"
        lines.append(f"{flag} ${s['realized_pnl']:+.2f} {s['title'][:40]}")
    if anomalies:
        lines.append(f"⚠️ {len(anomalies)} FIELD ANOMALY — a lock did not resolve to one clean winner; check basket_paper_book.json")
    body = "\n".join(lines)
    try:
        import wa_alert
        wa_alert.notify(body)          # WhatsApp + iMessage, both fail-soft
    except Exception as e:
        print(f"[basket_paper] alert failed: {e}")


def main():
    if "--report" in sys.argv:
        report(load())
        return
    book, entered, settled = cycle()
    print(f"[basket_paper] entered={len(entered)} settled={len(settled)}")
    for r in entered:
        print(f"  ENTER  {r['kind']:5} +{r['edge']*100:4.1f}%  {r['title'][:44]}")
    for r in settled:
        print(f"  SETTLE ${r['realized_pnl']:+.2f}  {r['title'][:44]}")
    report(book)
    snapshot_vault(book)
    alert_settlements(settled)


if __name__ == "__main__":
    main()
