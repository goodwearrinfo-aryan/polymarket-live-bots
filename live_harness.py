#!/usr/bin/env python3
"""live_harness.py — the go-live checklist harness (GRADUATION_PROTOCOL Step 2).

Reads the paper books (the signal generator) and emits the exact trade a human
should take with real money: market, paper fill, net edge, cost, the condition
id/yes_token to buy, and the max to stake (1/4-Kelly capped, per protocol).

It NEVER places orders. It only prints + appends a row to real_test_log.md so
the paper-vs-real fill gap is logged every time. Real fills are ARYAN's manual
ones in his own account — this is the checklist that makes the transition to
live a copy-paste, not a rewrite.

Run:  python3 live_harness.py           # print today's candidate trades
      python3 live_harness.py --applog  # also append a candidate row to real_test_log.md
"""
import os, sys, json, time

BASE = os.path.dirname(os.path.abspath(__file__))
BASKET = os.path.join(BASE, "basket_paper_book.json")
LOG = os.path.join(BASE, "real_test_log.md")
STOP_PCT = 0.03          # mental stop: ride to settlement, 3c stop (protocol Step 2)
# protocol Step 2: bankroll you can lose entirely ($100-200), fixed $5/trade, 1/4-Kelly cap
BANKROLL = 200.0
KELLY_FRAC = 0.25
MAX_STAKE = 5.0


def _load(p, d=None):
    try:
        return json.load(open(p))
    except Exception:
        return d if d is not None else {}


def _candidate_from_basket(rec, slug):
    """A paper basket lock → the executable real trade (buy ALL yes @ each leg ask)."""
    legs = []
    for leg in rec.get("legs", []):
        if leg.get("ask") is not None:
            legs.append({"yes_token": leg["yes_token"],
                         "condition_id": leg.get("condition_id"),
                         "ask": leg["ask"]})
    return {
        "source": "basket",
        "slug": slug,
        "title": rec.get("title"),
        "side": "LONG (buy all YES @ ask, one pays $1)",
        "net_edge": rec.get("edge"),
        "cost": rec.get("lock_cost"),
        "n_outcomes": rec.get("n_outcomes"),
        "expected_pnl": rec.get("expected_pnl"),
        "notional_paper": rec.get("notional"),
        "paper_fill_ts": rec.get("entry_ts"),
        "legs": legs,
        "real_fill_ts": None,   # ← ARYAN fills this manually
        "real_fill_price": None,
        "real_pnl": None,
        "note": "",
    }


def _candidate_from_leg(source, book_path, rec, key=None):
    """A dataarb/monoarb REAL open position → the executable trade (buy the side @ entry)."""
    return {
        "source": source,
        "slug": key or rec.get("cid"),
        "title": rec.get("q") or rec.get("title"),
        "side": f"{rec.get('side')} @ {rec.get('entry')} (paper entry)",
        "net_edge": None,               # data/mono book net edge; print from rec if present
        "cost": rec.get("entry"),
        "n_outcomes": rec.get("size"),
        "expected_pnl": None,
        "notional_paper": rec.get("size"),
        "paper_fill_ts": rec.get("opened"),
        "legs": [{"condition_id": rec.get("cid"),
                  "ask": rec.get("entry"),
                  "yes_token": rec.get("cid")}] if rec.get("cid") else [],
        "real_fill_ts": None,
        "real_fill_price": None,
        "real_pnl": None,
        "note": "",
    }


def candidates():
    """All currently-open REAL paper locks across the arb books = the real-money signal set."""
    out = []
    bk = _load(BASKET)
    for slug, rec in (bk.get("open") or {}).items():
        out.append(_candidate_from_basket(rec, slug))
    for src, path in (("data", "dataarb_state.json"), ("mono", "monoarb_state.json")):
        book = _load(os.path.join(BASE, path))
        open_pos = book.get("open") or []
        if isinstance(open_pos, dict):
            for k, rec in open_pos.items():
                if rec.get("kind") != "control":
                    out.append(_candidate_from_leg(src, path, rec, k))
        elif isinstance(open_pos, list):
            for rec in open_pos:
                if isinstance(rec, dict) and rec.get("kind") != "control":
                    out.append(_candidate_from_leg(src, path, rec))
    return out


def _max_stake():
    # protocol Step 2: fixed $5/trade, capped at 1/4-Kelly of bankroll
    return min(MAX_STAKE, BANKROLL * KELLY_FRAC)


def fmt(c, i):
    stake = _max_stake()
    legline = ""
    if c["legs"]:
        rows = [f"        {j+1}. YES token {l['yes_token'][:24]}… @ {l['ask']:.3f}"
                for j, l in enumerate(c["legs"])]
        legline = "\n".join(rows[:6])
        if len(c["legs"]) > 6:
            legline += f"\n        … +{len(c['legs']) - 6} more legs"
    edge_s = f"  net +{c['net_edge']:.1%}" if c["net_edge"] is not None else ""
    exp_s = f" · expected +${c['expected_pnl']:.2f}" if c["expected_pnl"] is not None else ""
    return (f"  [{i}] {c['side']}{edge_s}  cost {c['cost']:.3f}\n"
            f"      {c['title']}\n"
            f"      slug: {c['slug']}\n"
            f"      paper fill {c['paper_fill_ts']} · notional ${c['notional_paper']:.0f}{exp_s}\n"
            f"      REAL stake ≤ ${stake:.0f} (1/4-Kelly cap) · mental stop {STOP_PCT:.0%} · "
            f"buy on polymarket.com manually\n"
            f"{legline}")


def main():
    cs = candidates()
    print("=" * 60)
    print(f"  LIVE HARNESS — GRADUATION_PROTOCOL Step 2  ({time.strftime('%Y-%m-%d %H:%MZ', time.gmtime())})")
    print("  GATE CHECK: paper combined REAL n≥30 + CI>0 + controls negative  →  NOT YET")
    print("=" * 60)
    if not cs:
        print("  No open paper locks right now — nothing to mirror live. (Correct: the\n"
              "  signal generator decides; live never trades a market paper didn't enter.)")
        return 0
    for i, c in enumerate(cs, 1):
        print(fmt(c, i))
        print()
    if "--applog" in sys.argv:
        seen = set()
        try:
            for line in open(LOG):
                if line.startswith("| ") and "\t" not in line:
                    parts = [p.strip() for p in line.strip().strip("|").split("|")]
                    if len(parts) >= 3:
                        seen.add(f"{parts[1]}:{parts[2]}")
        except FileNotFoundError:
            pass
        added = 0
        with open(LOG, "a") as f:
            for c in cs:
                key = f"{c['source']}:{c['slug']}"
                if key in seen:
                    continue
                seen.add(key)
                f.write(f"| {time.strftime('%Y-%m-%d %H:%MZ', time.gmtime())} | {c['source']} | "
                        f"{c['slug']} | {c['paper_fill_ts']} | {c['net_edge'] if c['net_edge'] is not None else '—'} | "
                        f"{c['cost']:.3f} | {c['real_fill_ts'] or '—'} | "
                        f"{c['real_fill_price'] or '—'} | {c['real_pnl'] or '—'} |\n")
                added += 1
        print(f"  → appended {added} NEW candidate row(s) to {LOG} (deduped by source:slug)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
