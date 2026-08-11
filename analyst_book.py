#!/usr/bin/env python3
"""
analyst_book.py — tracks the research-analyst's PAPER positions against live
Polymarket prices. Separate from the algo legs: these are evidence-based,
manually-vetted information-edge trades (sourced, >5% edge). PAPER ONLY.

  python3 analyst_book.py            # mark to market + print P&L table
  python3 analyst_book.py --add ...  # (positions are seeded in analyst_positions.json)
"""
import json, os, urllib.request
from datetime import datetime, timezone

BASE = os.path.dirname(os.path.abspath(__file__))
BOOK = os.path.join(BASE, "analyst_positions.json")
GAMMA = "https://gamma-api.polymarket.com/markets"
CLOB = "https://clob.polymarket.com/markets/"


def live_yes(market_id):
    """Fetch current YES price for a conditionId/market id."""
    for q in (f"?condition_ids={market_id}", f"?id={market_id}"):
        try:
            req = urllib.request.Request(GAMMA + q, headers={"User-Agent": "analyst-book/1.0"})
            d = json.loads(urllib.request.urlopen(req, timeout=12).read())
            m = d[0] if isinstance(d, list) and d else (d if isinstance(d, dict) else None)
            if m and m.get("outcomePrices"):
                return float(json.loads(m["outcomePrices"])[0])
        except Exception:
            continue
    return None


def clob_state(market_id):
    """Fallback when gamma DROPS a market (resolved markets vanish from gamma — the
    stale_nodata bug class). CLOB keeps them: returns {yes, resolved, closed}.
    `resolved` is 'YES'/'NO' once a winning token is set, else None."""
    try:
        req = urllib.request.Request(CLOB + market_id, headers={"User-Agent": "analyst-book/1.0"})
        m = json.loads(urllib.request.urlopen(req, timeout=12).read())
    except Exception:
        return {"yes": None, "resolved": None, "closed": False}
    yes, resolved = None, None
    for t in m.get("tokens", []):
        oc = (t.get("outcome") or "").lower()
        if oc == "yes":
            try:
                yes = float(t.get("price"))
            except (TypeError, ValueError):
                pass
            if t.get("winner") is True:
                resolved = "YES"
        elif oc == "no" and t.get("winner") is True:
            resolved = "NO"
    return {"yes": yes, "resolved": resolved, "closed": bool(m.get("closed"))}


def mark_book(persist=True):
    """Mark every position to live, BANK + lock any newly-resolved ones, and return a
    structured snapshot. Shared by the CLI (main) and the 24/7 analyst_agent so both
    use ONE marking/banking path. Returns:
      {rows:[{...}], realized, unrealized, n_settled, n_open, newly_settled:[q,...]}"""
    if not os.path.exists(BOOK):
        return {"rows": [], "realized": 0.0, "unrealized": 0.0,
                "n_settled": 0, "n_open": 0, "newly_settled": []}
    book = json.loads(open(BOOK).read())
    positions = book.get("positions", [])
    out, realized, unrealized, newly = [], 0.0, 0.0, []
    for p in positions:
        if p.get("status") == "settled":  # LOCKED — never re-fetch
            pnl = p.get("realized_pnl",
                        round((1.0 if p["resolved_side"] == p["side"] else 0.0) - p["entry_price"], 4))
            realized += pnl
            out.append({"p": p, "state": "settled", "resolved": p["resolved_side"],
                        "won": p["resolved_side"] == p["side"], "pnl": pnl,
                        "cur_val": p.get("settle_price"), "ly": None})
            continue

        ly = live_yes(p["market_id"])
        resolved = None
        if ly is None:                    # gamma dropped it — CLOB has live price OR resolution
            st = clob_state(p["market_id"])
            ly, resolved = st["yes"], st["resolved"]

        if resolved is not None:          # bank it: winner $1/share, loser $0
            cur_val = 1.0 if resolved == p["side"] else 0.0
            pnl = round(cur_val - p["entry_price"], 4)
            realized += pnl
            p.update({"status": "settled", "resolved_side": resolved,
                      "settle_price": cur_val, "realized_pnl": pnl,
                      "settled_at": f"{datetime.now(timezone.utc):%Y-%m-%dT%H:%M:%SZ}"})
            newly.append(p["q"])
            out.append({"p": p, "state": "newly_settled", "resolved": resolved,
                        "won": resolved == p["side"], "pnl": pnl, "cur_val": cur_val, "ly": ly})
            continue

        cur_val = (ly if p["side"] == "YES" else (1 - ly)) if ly is not None else p["entry_price"]
        pnl = round(cur_val - p["entry_price"], 4)
        unrealized += pnl
        mkt_now = (ly if p["side"] == "YES" else 1 - ly) if ly is not None else None
        out.append({"p": p, "state": "open", "resolved": None, "pnl": pnl,
                    "cur_val": cur_val, "ly": ly, "mkt_now": mkt_now})

    if newly and persist:
        book["positions"] = positions
        tmp = BOOK + ".tmp"
        with open(tmp, "w") as f:
            json.dump(book, f, indent=2)
        os.replace(tmp, BOOK)

    n_settled = sum(1 for r in out if r["state"] in ("settled", "newly_settled"))
    return {"rows": out, "realized": realized, "unrealized": unrealized,
            "n_settled": n_settled, "n_open": len(out) - n_settled, "newly_settled": newly}


def main():
    snap = mark_book()
    if not snap["rows"]:
        print("no analyst_positions.json"); return
    print(f"\n📋 ANALYST PAPER BOOK — {len(snap['rows'])} positions   ({datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC})")
    print("=" * 78)
    for r in snap["rows"]:
        p = r["p"]
        if r["state"] in ("settled", "newly_settled"):
            won = "WON " if r["won"] else "LOST"
            print(f"\n{p['side']:<3} {p['q'][:58]}")
            print(f"    SETTLED {r['resolved']} → {won}  realized P&L {r['pnl']:+.3f}  (entry {p['entry_price']:.3f})")
            continue
        mv = "?" if r["ly"] is None else f"{r['ly']:.2f}"
        edge_now = f"true {p['true_prob']:.2f} vs mkt {r['mkt_now']:.2f}" if r.get("mkt_now") is not None else ""
        warn = "  ⚠️ no live price (gamma+CLOB both blank)" if r["ly"] is None else ""
        print(f"\n{p['side']:<3} {p['q'][:58]}")
        print(f"    entry {p['entry_price']:.3f} → now {r['cur_val']:.3f}  P&L {r['pnl']:+.3f}  | live YES={mv}  {edge_now}{warn}")
        print(f"    conf {p['confidence']}/10 · {p['risk']} · resolves {p['resolves']} · edge@entry {p['edge_pct']}pts")
    print("\n" + "=" * 78)
    print(f"  realized (settled {snap['n_settled']}):  ${snap['realized']:+.3f}")
    print(f"  unrealized (open {snap['n_open']}):   ${snap['unrealized']:+.3f}")
    print(f"  analyst book TOTAL P&L:   ${snap['realized'] + snap['unrealized']:+.3f}   (paper, settles at resolution)")


if __name__ == "__main__":
    main()
