"""
edge1_flb.py — Favorite-Longshot Bias, HOLD-TO-RESOLUTION.

Thesis: longshots (low YES prob) are systematically OVERpriced; fading them
(buy NO, hold to resolution, NEVER stop out) harvests the premium. Immune to
gap-through (no stop) and to spread-on-exit (held to settlement at 0/1).

Two measurements:
  A. HISTORICAL (ml/dataset_goldsky.csv, resolved on-chain): for each YES-prob
     band, realized YES rate vs implied. If realized < implied in longshot bands,
     fading is +EV. Reports per-band EV of buy-NO-hold-to-resolution.
  B. LIVE (gamma): current longshot inventory you could fade right now, with
     horizon (days to resolution) so you only take long-dated (slow-decay) ones.

Read-only. Paper research. No funds.
"""
import csv, sys, math
from pathlib import Path
from datetime import datetime, timezone
import edge_common as ec

PM = Path(__file__).resolve().parent
GOLDSKY = PM / "ml" / "dataset_goldsky.csv"

def wilson(k, n, z=1.96):
    if n == 0: return (0.0, 0.0)
    p = k / n; d = 1 + z*z/n
    c = (p + z*z/(2*n))/d
    h = z*math.sqrt(p*(1-p)/n + z*z/(4*n*n))/d
    return (c-h, c+h)

def historical_flb():
    """Per-band realized-vs-implied + buy-NO-hold EV. Goldsky resolved markets."""
    if not GOLDSKY.exists():
        return None
    rows = list(csv.DictReader(open(GOLDSKY)))
    bands = {}
    for r in rows:
        try:
            p = float(r["prob"]); y = int(r["final_outcome"])
        except (ValueError, KeyError):
            continue
        b = round(p * 20) / 20  # 5-cent bands
        bands.setdefault(b, []).append((p, y))

    print("="*78)
    print("  EDGE 1A — FAVORITE-LONGSHOT BIAS (historical, Goldsky resolved)")
    print("="*78)
    print(f"  {len(rows)} resolved on-chain markets. Buy-NO @ (1-p), hold to resolution.")
    print(f"  NO pays 1 if outcome=0. EV_no = (1-realized) - (1-p) = p - realized.\n")
    print(f"  {'band':>5} {'n':>4} {'implied':>8} {'realized':>9} {'95% CI':>16} {'EV_fade':>8} flag")
    print("  " + "-"*72)
    edges = []
    for b in sorted(bands):
        v = bands[b]; n = len(v); k = sum(y for _, y in v)
        implied = sum(p for p, _ in v) / n
        realized = k / n
        lo, hi = wilson(k, n)
        ev_fade = implied - realized  # +EV to fade if realized < implied
        # is the fade EV real? implied must sit ABOVE the realized CI upper bound
        real = implied > hi
        flag = "FADE+EV" if (real and ev_fade > 0) else ("buy+EV" if (implied < lo) else "calibrated")
        if b <= 0.30 or b >= 0.70:
            edges.append((b, n, ev_fade, real))
        print(f"  {b:>5.2f} {n:>4} {implied:>8.3f} {realized:>9.3f} "
              f"[{lo:.2f},{hi:.2f}]{'':>4} {ev_fade:>+8.3f} {flag}")
    print()
    longshot_edge = sum(ev for b, n, ev, real in edges if b <= 0.30 and real and ev > 0)
    fav_edge = sum(ev for b, n, ev, real in edges if b >= 0.70 and real)
    print(f"  Longshot bands (≤0.30) with REAL fade edge: "
          f"{sum(1 for b,n,ev,real in edges if b<=0.30 and real and ev>0)}")
    print(f"  Favorite bands (≥0.70) miscalibrated: "
          f"{sum(1 for b,n,ev,real in edges if b>=0.70 and real)}")
    return bands

def live_longshots(min_days=14, max_yes=0.12, min_vol=20000):
    """Current long-dated longshots you could fade and hold."""
    print("\n" + "="*78)
    print("  EDGE 1B — LIVE LONGSHOT INVENTORY (fade candidates, hold-to-res)")
    print("="*78)
    mkts = ec.poly_markets(pages=10, min_vol=min_vol)
    now = datetime.now(timezone.utc)
    cands = []
    for m in mkts:
        if not (0.02 <= m["yes"] <= max_yes):
            continue
        days = None
        if m.get("end"):
            try:
                end = datetime.fromisoformat(m["end"].replace("Z", "+00:00"))
                days = (end - now).days
            except Exception:
                pass
        if days is None or days < min_days:
            continue
        cands.append((m, days))
    cands.sort(key=lambda x: -x[0]["vol"])
    print(f"  {len(cands)} long-dated (≥{min_days}d) longshots (YES≤{max_yes}) @ vol≥${min_vol/1000:.0f}k:\n")
    print(f"  {'YES':>5} {'days':>5} {'vol($k)':>8}  question")
    print("  " + "-"*72)
    for m, days in cands[:25]:
        print(f"  {m['yes']:>5.3f} {days:>5} {m['vol']/1000:>8.0f}  {m['q'][:54]}")
    print(f"\n  → Fade thesis: buy NO @ ~{1-max_yes:.2f}, hold to resolution, size $1-2,")
    print(f"    diversify across {len(cands)}+ independent longshots. No stop = no gap-through.")
    return cands

if __name__ == "__main__":
    historical_flb()
    live_longshots()
