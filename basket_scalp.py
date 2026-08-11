#!/usr/bin/env python3
"""basket_scalp.py — EARLY-EXIT scalp monitor for open basket locks.

A basket lock's edge is banked at entry, but holding to resolution ties up
NOTIONAL per lock for months (the current open lock resolves end of 2026). On
a capacity-starved edge, freeing capital EARLY is real value — a scalp: re-price
the open lock on the LIVE book and, when selling now realizes >= the booked edge,
flag it as scalpable. PAPER / read-only — never touches basket_paper_book.json
(the graduation track stays hold-to-resolution).

Read-only. No keys, no orders, no state mutation.
Run:  python3 basket_scalp.py                # scan + report
"""
import json, sys, os
from datetime import datetime, timezone
import basket_arb

BASE = os.path.dirname(os.path.abspath(__file__))
BOOK = os.path.join(BASE, "basket_paper_book.json")
# a scalp must clear BOTH: realizable edge >= booked edge AND edge over the exit
# taker fee (re-entering is symmetric). SCALP_FLOOR guards micro-haircuts.
SCALP_FLOOR = 0.005


def _load(p, d=None):
    try:
        return json.load(open(p))
    except Exception:
        return d if d is not None else {}


def _scalp_one(rec):
    """Re-price an open LONG lock on the live book. Returns scalp stats or None if
    the book is gone (can't exit → hold to resolution is the only path)."""
    legs = [{"yes_token": m.get("yes_token")} for m in rec.get("legs", [])]
    live = basket_arb._live_basket_edge(legs, rec.get("fee_rate"))
    if live is None:
        return None
    # LONG: bought all YES for lock_cost; selling now at live sum_bid realizes sum_bid - lock_cost.
    realized = live["sum_bid"] - rec.get("lock_cost", 0.0)
    exit_fee = live.get("fee", 0.0)              # symmetric taker fee on the exit leg
    net_realized = realized - exit_fee
    booked = rec.get("edge", 0.0)                # NET-of-fee booked edge at entry
    return {
        "realized": realized, "net_realized": net_realized,
        "booked": booked, "live_sum_bid": live["sum_bid"],
        "fee": exit_fee,
        "edge_clears": (net_realized >= booked),
        "over_floor": (net_realized >= SCALP_FLOOR),
        "exit_available": True,
    }

def main():
    book = _load(BOOK)
    opens = book.get("open", {})
    print("=" * 62)
    print(f"  BASKET SCALP — early-exit watch  ({datetime.now(timezone.utc):%Y-%m-%d %H:%MZ})")
    print("  holds-to-resolution is the graduation track; this only asks: can we "
          "\n  exit EARLY at >= the booked edge and free the capital?  (read-only)")
    print("=" * 62)
    if not opens:
        print("  no open locks — nothing to scalp")
        return 0
    for slug, rec in opens.items():
        s = _scalp_one(rec)
        title = rec.get("title", slug)[:62]
        if s is None:
            print(f"  [hold]  {title}\n          book gone/unfilled — no exit available, ride to resolution")
            continue
        tag = "SCALP" if (s["edge_clears"] and s["over_floor"]) else "hold"
        print(f"  [{tag}]  {title}")
        print(f"          booked {s['booked']:+.1%}  live-exit {s['net_realized']:+.1%} "
              f"(gross {s['realized']:+.1%}, fee {s['fee']:.2%})  Σbid {s['live_sum_bid']:.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
