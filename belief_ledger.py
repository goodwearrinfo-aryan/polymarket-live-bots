"""
belief_ledger.py — append-only forecast ledger + calibration scoring.

Ported concept from oss-bots/openthomas (Bayesian PM worker): treat the *belief*
as the primary artifact, not just the PnL. Every entry the lab makes is a forecast
— `entry_fill` is the de-vigged market-implied probability that the chosen side
resolves true. Live state is DELETE+rewrite (Postgres), so that belief is mutable
and vanishes on close; this ledger makes it immutable and, crucially, **scores it
for calibration** (Brier + reliability deciles) — which win-rate/PnL never do.

NOT WIRED INTO scalp_lab.py YET. Two-line hook (see wire_into_close below) goes in
_close(); a separate forecast append goes at each book["open"].append site (or once
over `opened` at the end of scan_entries). Paper-only; writes a local jsonl, no I/O
to any venue.

Files (next to the mirror):
  belief_forecasts.jsonl   one forecast per line, append-only, never rewritten
  belief_settlements.jsonl one settlement per line, back-filled at _close()
"""
import json, os
from collections import defaultdict
from datetime import datetime, timezone

LEDGER_DIR = os.path.dirname(os.path.abspath(__file__))
FORECASTS = os.path.join(LEDGER_DIR, "belief_forecasts.jsonl")
SETTLEMENTS = os.path.join(LEDGER_DIR, "belief_settlements.jsonl")


def _now():
    return datetime.now(timezone.utc).isoformat()


def append_forecast(pos, leg):
    """Call once when a position opens. `pos` is the book dict already built by the
    leg; we copy only the belief + its evidence so the record is self-contained."""
    rec = {
        "ts": _now(),
        "leg": leg,
        "market_id": pos.get("id"),
        "q": pos.get("q"),
        "side": pos.get("side"),
        # THE forecast: implied P(side resolves true) = cost of the outcome share.
        "p": pos.get("entry_fill"),
        "entry_mid": pos.get("entry_mid"),
        # evidence chain — whatever the leg already knows at decision time.
        "evidence": {
            "vol": pos.get("vol"),
            "days_left": pos.get("days_left"),
            "cat": pos.get("cat"),
            "book_spread": pos.get("book_spread"),
            "ps_status": pos.get("ps_status") or pos.get("tournament_round"),
            "cs2_fair_leader": pos.get("cs2_fair_leader"),
        },
    }
    with open(FORECASTS, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")


def append_settlement(pos, exit_px, pnl, reason):
    """Call from _close(). stale_nodata (pnl None) is never scored — same rule as
    everywhere else in the bot."""
    if pnl is None:
        return
    rec = {
        "ts": _now(),
        "market_id": pos.get("id"),
        "leg": pos.get("strategy"),
        "p": pos.get("entry_fill"),         # the forecast being scored
        "outcome": 1 if pnl > 0 else 0,     # ride-to-res legs: win == side resolved true
        "exit_fill": round(exit_px, 4),
        "pnl_usdc": pnl,
        "reason": reason,
    }
    with open(SETTLEMENTS, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")


def calibration(leg=None):
    """Brier score + reliability buckets over settled forecasts. Optionally per-leg."""
    if not os.path.exists(SETTLEMENTS):
        return {"n": 0}
    rows = [json.loads(l) for l in open(SETTLEMENTS, encoding="utf-8") if l.strip()]
    if leg:
        rows = [r for r in rows if r["leg"] == leg]
    rows = [r for r in rows if r.get("p") is not None]
    n = len(rows)
    if not n:
        return {"n": 0}
    brier = sum((r["p"] - r["outcome"]) ** 2 for r in rows) / n
    mean_p = sum(r["p"] for r in rows) / n
    realized = sum(r["outcome"] for r in rows) / n
    buckets = defaultdict(list)
    for r in rows:
        buckets[round(r["p"], 1)].append(r["outcome"])
    reliability = {
        k: {"n": len(v), "realized": round(sum(v) / len(v), 3)}
        for k, v in sorted(buckets.items())
    }
    return {
        "n": n, "brier": round(brier, 4),
        "mean_implied": round(mean_p, 3),
        "realized_winrate": round(realized, 3),
        "calibration_gap": round(realized - mean_p, 3),
        "reliability": reliability,
    }


# ── How it wires into scalp_lab.py (paper-safe, two hooks) ───────────────────
#
#   import belief_ledger
#
#   # 1. forecast at decision time — at the END of scan_entries, over fresh opens:
#   for leg in LEGS:
#       for pos in state[leg]["open"]:
#           if pos.get("opened_at", "")[:19] == ... # this cycle only
#   # simpler: each leg already builds `opened`; one call per appended pos:
#   #     belief_ledger.append_forecast(pos, "nearres")
#
#   # 2. settlement backfill — ONE line inside _close(), the universal chokepoint:
#   #     belief_ledger.append_settlement(pos, exit_px, rec["pnl_usdc"], reason)
#
# Nothing reads venue APIs; both files are local append-only jsonl.

def from_state(leg=None, state_path=os.path.join(LEDGER_DIR, "scalp_lab_state.json")):
    """Standalone calibration from the EXISTING closed records — no hooks needed.
    Uses entry_fill as the forecast p and pnl_usdc>0 as the realized outcome (valid
    for ride-to-resolution legs where a win == the entered side resolving true).
    This is the immediately-useful path; the jsonl ledger only fills once wired in."""
    s = json.load(open(state_path, encoding="utf-8"))
    legs = [leg] if leg else list(s.keys())
    out = {}
    for lg in legs:
        book = s.get(lg, {})
        rows = [r for r in book.get("closed", [])
                if r.get("pnl_usdc") is not None and r.get("entry_fill") is not None]
        if not rows:
            continue
        pairs = [(r["entry_fill"], 1 if r["pnl_usdc"] > 0 else 0) for r in rows]
        n = len(pairs)
        brier = sum((p - o) ** 2 for p, o in pairs) / n
        mean_p = sum(p for p, _ in pairs) / n
        realized = sum(o for _, o in pairs) / n
        buckets = defaultdict(list)
        for p, o in pairs:
            buckets[round(p, 1)].append(o)
        out[lg] = {
            "n": n, "brier": round(brier, 4),
            "mean_implied": round(mean_p, 3),
            "realized_winrate": round(realized, 3),
            "calibration_gap": round(realized - mean_p, 3),
            "reliability": {k: {"n": len(v), "realized": round(sum(v) / len(v), 3)}
                            for k, v in sorted(buckets.items())},
        }
    return out


if __name__ == "__main__":
    import sys
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    leg = args[0] if args else None
    if "--live" in sys.argv:           # score the jsonl ledger (post-wiring)
        print(json.dumps(calibration(leg), indent=2))
    else:                              # default: backfill from existing closed records
        res = from_state(leg)
        if not res:
            print("no priced closed records" + (f" for {leg}" if leg else ""))
        for lg, c in sorted(res.items(), key=lambda kv: -kv[1]["n"]):
            flag = ""
            if c["n"] >= 10:
                flag = "  WELL-CALIBRATED" if abs(c["calibration_gap"]) <= 0.05 else "  MISCALIBRATED"
            print(f"\n[{lg}] n={c['n']}  Brier={c['brier']}  implied={c['mean_implied']}  "
                  f"realized={c['realized_winrate']}  gap={c['calibration_gap']:+}{flag}")
            for k, b in c["reliability"].items():
                print(f"    implied~{k}: n={b['n']:2d}  realized={b['realized']}")
