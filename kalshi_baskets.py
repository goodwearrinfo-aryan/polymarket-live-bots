#!/usr/bin/env python3
"""kalshi_baskets.py — COMPLETE-SET BASKET LOCKS on Kalshi (venue #2 for the lock math).

The same structural lock Polymarket's basket_arb exploits, on Kalshi's mutually-exclusive
multi-outcome events. In a complete set (exactly one outcome resolves to YES/$1, the rest
to $0):
  • LONG  — buy ALL yes @ ask when Σyes_ask < 1 → one pays $1, profit = 1 − Σask
  • SHORT — buy ALL no  @ bid when Σno_bid  > N−1 → N−1 pay $1, profit = Σbid − (N−1)
Kalshi is a SECOND, independent liquidity pool → more locks, and the two venues hedge each
other (an event priced as a set <$1 here may be >$1 there = a cross-venue lock; that's the
next variation). PAPER / read-only. No keys, no orders, no state writes to the bot books.

Run:  python3 kalshi_baskets.py            # scan + report locked baskets (episodic, ~5 pages)
      python3 kalshi_baskets.py --all      # report all complete-set events (incl. no lock)
NOTE: episodic/manual ONLY. Kalshi API is 45-120s/page via the jina fallback (direct host
blocks this machine) — too slow for the per-cycle watchdog. Not a live monitor.
"""
import json, sys, os
from datetime import datetime, timezone
import edge_common as ec

# Kalshi taker fee is ~0.03 (3%) on the premium; keep a conservative floor like basket_arb.
MIN_EDGE = 0.01          # 1c locked minimum (NET of the fee estimate)
FEE_RATE = 0.03          # Kalshi taker fee estimate (conservative mid)
MIN_LIQ = 0.0            # no liquidity floor for now — report, human decides
MAX_PAGES = 5            # episodic manual depth; Kalshi API ~45-120s/page through jina fallback
                         # (direct host blocks this machine) → NOT suitable for the 60s watchdog


def _fmt(p):
    """Kalshi returns prices as decimal strings like '0.1000'."""
    if p in (None, ""):
        return None
    try:
        return float(p)
    except Exception:
        return None


def kalshi_complete_sets(pages=MAX_PAGES):
    """All open Kalshi events with >=2 binary markets = candidate complete sets."""
    out = []
    for _p in range(pages):
        raw = ec._get(ec.KALSHI_EVENTS, {
            "status": "open", "limit": 100, "with_nested_markets": "true",
            "cursor": out[-1]["_cursor"] if out and out[-1].get("_cursor") else "",
        })
        evs = (raw or {}).get("events") or []
        for ev in evs:
            mkts = ev.get("markets") or []
            rows = []
            for m in mkts:
                if m.get("market_type") != "binary":
                    continue
                rows.append({
                    "ticker": m.get("ticker"),
                    "yes_sub": m.get("yes_sub_title") or m.get("sub_title"),
                    "yes_ask": _fmt(m.get("yes_ask_dollars")),
                    "yes_bid": _fmt(m.get("yes_bid_dollars")),
                    "no_ask": _fmt(m.get("no_ask_dollars")),
                    "no_bid": _fmt(m.get("no_bid_dollars")),
                    "last": _fmt(m.get("last_price_dollars")),
                    "liq": _fmt(m.get("liquidity_dollars")),
                })
            if len(rows) < 2:
                continue
            # REJECT zero-liquidity phantom quotes: a "lock" at $0 liq is stale-quote fiction
            # (mark-to-mid leak — the exact bug basket_arb learned). No depth = not fillable.
            if any((r["liq"] or 0) <= 0 for r in rows):
                continue
            # REJECT cumulative-date sets: subtitles like "Before 2030" / "After 2027" are
            # OVERLAPPING (multiple can pay), NOT a mutually-exclusive complete set. A true
            # lock needs exactly-one-winner outcomes (people/teams/values). This is the same
            # complete_field guard basket_arb learned (Tennessee 2/13, French 36/128).
            subs = [r["yes_sub"] for r in rows]
            if any(s and (s.lower().startswith("before") or s.lower().startswith("after")
                          or " or later" in s.lower() or " by " in s.lower())
                   for s in subs):
                continue
            asks = [r["yes_ask"] for r in rows]
            bids = [r["no_bid"] for r in rows]
            if any(a is None for a in asks) or any(b is None for b in bids):
                continue
            n = len(rows)
            s_ask, s_bid = sum(asks), sum(bids)
            long_gross = 1.0 - s_ask
            short_gross = s_bid - (n - 1)
            long_net = long_gross - FEE_RATE * sum(asks)     # fee on the premium bought
            short_net = short_gross - FEE_RATE * sum(bids)   # fee on the premium sold
            edge = max(long_net, short_net)
            kind = "LONG" if long_net >= short_net else "SHORT"
            if edge >= MIN_EDGE:
                out.append({
                    "title": ev.get("title"),
                    "ticker": ev.get("ticker"),
                    "n_outcomes": n,
                    "sum_yes_ask": round(s_ask, 3),
                    "sum_no_bid": round(s_bid, 3),
                    "long_edge": round(long_net, 3), "short_edge": round(short_net, 3),
                    "edge": round(edge, 3), "kind": kind,
                    "fee_rate": FEE_RATE,
                    "markets": rows,
                    "_cursor": (raw or {}).get("cursor") or "",
                })
        nxt = (raw or {}).get("cursor") or ""
        if not nxt or not evs:
            break
    out.sort(key=lambda d: d["edge"], reverse=True)
    return out


def _mk_lines(b):
    rows = []
    for i, m in enumerate(b["markets"], 1):
        rows.append(f"        {i}. {m['yes_sub'][:40]:42} yes@ask {m['yes_ask']:.3f} "
                    f"no@bid {m['no_bid']:.3f} (liq ${m['liq'] or 0:,.0f})")
    return rows


def main():
    locks = kalshi_complete_sets()
    print("=" * 62)
    print(f"  KALSHI COMPLETE-SET LOCKS  ({datetime.now(timezone.utc):%Y-%m-%d %H:%MZ})")
    print("  second venue for the basket-lock math (LONG Σask<1 | SHORT Σbid>N−1)")
    print("=" * 62)
    if not locks:
        print("  no locks >= 1% NET of fee right now (dormant = correct; same as Polymarket)")
        return 0
    for b in locks:
        side = "LONG  buy all YES @ ask" if b["kind"] == "LONG" else "SHORT buy all NO @ bid"
        print(f"  [{b['kind']}]  {side}  net +{b['edge']:.1%}  ({b['n_outcomes']} outcomes)")
        print(f"      {b['title']}")
        print(f"      Σyes_ask {b['sum_yes_ask']:.3f} | Σno_bid {b['sum_no_bid']:.3f} "
              f"(N-1={b['n_outcomes']-1}) | event {b['ticker']}")
        print("\n".join(_mk_lines(b)))
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
