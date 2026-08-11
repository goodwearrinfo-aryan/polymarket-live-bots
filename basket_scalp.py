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
      python3 basket_scalp.py --alert        # + loud deduped WhatsApp/iMessage on a NEW scalp
"""
import json, sys, os
from datetime import datetime, timezone
import basket_arb

BASE = os.path.dirname(os.path.abspath(__file__))
BOOK = os.path.join(BASE, "basket_paper_book.json")
STATE = os.path.join(BASE, ".basket_scalp_seen.json")
# a scalp must clear BOTH: realizable edge >= booked edge AND edge over the exit
# taker fee (re-entering is symmetric). SCALP_FLOOR guards micro-haircuts.
SCALP_FLOOR = 0.005

try:
    import wa_alert
except Exception:
    wa_alert = None


def _load(p, d=None):
    try:
        return json.load(open(p))
    except Exception:
        return d if d is not None else {}


def _save(p, obj):
    tmp = p + ".tmp"
    try:
        json.dump(obj, open(tmp, "w"))
        os.replace(tmp, p)
    except Exception:
        pass


def _alert(text):
    if wa_alert:
        try:
            return wa_alert.notify(text)
        except Exception as e:
            print(f"  alert failed: {e}")
    print("  [alert unavailable] " + text)
    return False


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
    alert_mode = "--alert" in sys.argv
    seen = _load(STATE)
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
        # ALERT: fire ONCE per slug when a scalp first opens; clear the flag once the
        # scalp is gone (hold) so a fresh convergence later re-alerts. Deduped + fail-soft.
        if not alert_mode:
            continue
        if tag == "SCALP" and not seen.get(slug):
            msg = (f"BASKET SCALP AVAILABLE: {rec.get('title','')}\n"
                   f"live-exit {s['net_realized']:+.1%} vs booked {s['booked']:+.1%} "
                   f"(Σbid {s['live_sum_bid']:.3f}) — exit early to free the {rec.get('notional',0):.0f}$ lock.\n"
                   f"paper signal: {slug}")
            _alert(msg)
            seen[slug] = True
        elif tag == "hold" and seen.get(slug):
            seen[slug] = False
    if alert_mode:
        _save(STATE, seen)
    return 0


if __name__ == "__main__":
    sys.exit(main())
