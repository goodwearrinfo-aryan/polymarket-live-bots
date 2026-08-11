#!/usr/bin/env python3
"""funding_basis.py — PAPER probe: NON-predictive crypto carry (cash-and-carry funding harvest).

The only edge type that has ever survived this project is NON-predictive arb (basket, data).
Directional crypto is the HARDEST version of the dead taker pattern (Strategy Graveyard: every
predictive taker leg pays the spread and dies; BTC/ETH perps are the deepest markets on earth).
This probe tests the one STRUCTURAL crypto edge instead — delta-neutral funding-rate carry:

  REAL    = harvest carry: take the delta-neutral side that RECEIVES funding (short perp when
            funding>0, long perp when funding<0), plus basis convergence. Delta-neutral → NOT a
            price-direction bet. Direction is set from the OBSERVED current funding sign (a known
            fact), never a forecast.
  CONTROL = pay carry: the opposite (mirror) side that PAYS funding → MUST lose when funding is
            persistent, on EITHER sign. If control is +EV at n>=30, there's an accounting bug
            (same honest-by-construction discipline as every leg).

P&L per closed hold = funding_accrued ± basis_drift − round-trip fees. Held MIN_HOLD_DAYS to
amortize fees: a 1-interval hold is killed by fees — that's the point. The carry is only a real
edge if it beats realistic transaction cost. FEE_BPS_RT is a DOCUMENTED conservative assumption
(4 fills, low-fee tier); the gate tells the truth either way.

GATE: n>=30 closed REAL holds AND bootstrap CI>0 AND mean>control AND control<=0.
Slow by nature (3 assets x ~MIN_HOLD_DAYS → ~n=30 in a month) — rare-but-real, like dataarb.

PAPER only. Read-only ccxt market data (no orders, no keys, no funds). Stdlib + ccxt.

Run: funding_basis.py scan | settle | once | report
"""
import os, json, time

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "funding_basis_state.json")

ASSETS = ("BTC", "ETH", "SOL")     # liquid perps with real funding markets
NOTIONAL = 100.0                   # paper $ per leg (delta-neutral, so notional is per side)
FEE_BPS_RT = 8.0                   # round-trip total over 4 fills (spot+perp, entry+exit).
                                   # Conservative low-fee-tier assumption — the edge must beat it.
MIN_HOLD_DAYS = 3.0                # hold to amortize fees; 1-interval holds are fee-killed by design
FUNDING_INTERVAL_H = 8.0           # standard perp funding cadence
HURDLE_MARGIN = 1.5                # require funding premium >= 1.5x the fee-breakeven before
                                   # harvesting. Below it the carry is -EV → stay IDLE. This is
                                   # the only honest way to "make it profitable": trade ONLY when
                                   # a real premium exists, never fake the number (the nearres rule).
# Carry CONFIGS — funding_landscape's map wired into the leg. Same-venue carries (spot+perp on ONE
# venue, 8h funding) for binance/bybit/okx; PLUS a Hyperliquid CROSS-VENUE carry — HL is perp-only
# and funds HOURLY, so its spot leg sits on binance. Cross-venue adds basis risk, captured honestly
# by the basis P&L term (binance-spot vs HL-perp), so the gate catches it if it's lossy.
CONFIGS = [
    {"label": "binance",   "perp_venue": "binance",     "perp": "{}/USDT:USDT", "spot_venue": "binance", "spot": "{}/USDT", "interval_h": 8.0},
    {"label": "bybit",     "perp_venue": "bybit",       "perp": "{}/USDT:USDT", "spot_venue": "bybit",   "spot": "{}/USDT", "interval_h": 8.0},
    {"label": "okx",       "perp_venue": "okx",         "perp": "{}/USDT:USDT", "spot_venue": "okx",     "spot": "{}/USDT", "interval_h": 8.0},
    {"label": "hl_xvenue", "perp_venue": "hyperliquid", "perp": "{}/USDC:USDC", "spot_venue": "binance", "spot": "{}/USDT", "interval_h": 1.0},
]
_CFG = {c["label"]: c for c in CONFIGS}


def _now():
    return time.time()


def _stamp(ts=None):
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts or _now()))


def snapshot(cfg, asset):
    """{funding, spot, perp, interval_h} for a carry config — perp on perp_venue, spot on spot_venue
    (same venue, or cross-venue for HL). Read-only ccxt. None on failure (skip, don't fake)."""
    try:
        import ccxt
        pex = getattr(ccxt, cfg["perp_venue"])({"enableRateLimit": True})
        perp = cfg["perp"].format(asset)
        fr = pex.fetch_funding_rate(perp)
        funding = fr.get("fundingRate")
        if funding is None:
            return None
        perp_px = fr.get("markPrice") or pex.fetch_ticker(perp).get("last")
        sex = pex if cfg["spot_venue"] == cfg["perp_venue"] \
            else getattr(ccxt, cfg["spot_venue"])({"enableRateLimit": True})
        spot_px = sex.fetch_ticker(cfg["spot"].format(asset)).get("last")
        if not perp_px or not spot_px:
            return None
        return {"funding": float(funding), "spot": float(spot_px), "perp": float(perp_px),
                "interval_h": cfg["interval_h"]}
    except Exception:
        return None


def load_state():
    if os.path.exists(STATE):
        try:
            return json.load(open(STATE))
        except Exception:
            pass
    return {"open": [], "resolved": [], "last_scan": None}


def save_state(st):
    tmp = STATE + ".tmp"
    json.dump(st, open(tmp, "w"), indent=2)
    os.replace(tmp, STATE)


def _pos(cfg, asset, snap, kind):
    d = 1 if snap["funding"] >= 0 else -1   # REAL takes the funding-RECEIVING side
    real_long_spot = (d > 0)                # funding>0 → long spot/short perp collects
    if kind == "control":
        real_long_spot = not real_long_spot
    legs = "long spot/short perp" if real_long_spot else "short spot/long perp"
    tag = f" [{cfg['spot_venue']}+{cfg['perp_venue']}]" if cfg["perp_venue"] != cfg["spot_venue"] else ""
    side = ("carry: " if kind == "real" else "reverse: ") + legs + tag
    return {"config": cfg["label"], "asset": asset, "kind": kind, "dir": d, "side": side,
            "interval_h": cfg["interval_h"], "xvenue": cfg["perp_venue"] != cfg["spot_venue"],
            "spot_entry": snap["spot"], "perp_entry": snap["perp"],
            "spot_now": snap["spot"], "perp_now": snap["perp"],
            "funding_accrued": 0.0, "last_funding": snap["funding"],
            "opened_ts": _now(), "last_update": _now(), "opened": _stamp(), "status": "open"}


def refresh(st):
    """Accrue funding for each open position since last_update; refresh funding rate + marks.
    REAL is short the perp → receives funding when funding>0 (sign +1); CONTROL is long (sign -1)."""
    for p in st["open"]:
        cfg = _CFG.get(p.get("config", "binance"))
        if not cfg:
            continue
        snap = snapshot(cfg, p["asset"])
        if not snap:
            continue
        dt_h = (_now() - p["last_update"]) / 3600.0
        # REAL receives on its chosen side (psign>0 collects); CONTROL is the mirror. Per-position
        # interval_h (8h venues vs HL 1h). Funding flips mid-hold → accrual flips too (honest).
        psign = p["dir"] * (1.0 if p["kind"] == "real" else -1.0)
        p["funding_accrued"] += psign * p["last_funding"] * (dt_h / p.get("interval_h", 8.0))
        p["last_funding"] = snap["funding"]
        p["spot_now"], p["perp_now"] = snap["spot"], snap["perp"]
        p["last_update"] = _now()
    return st


def _close_pnl(p):
    """Realized paper P&L (in $) for a held delta-neutral position.
    REAL (long spot/short perp): price return = r_spot - r_perp (captures basis convergence)."""
    r_spot = p["spot_now"] / p["spot_entry"] - 1.0
    r_perp = p["perp_now"] / p["perp_entry"] - 1.0
    psign = p["dir"] * (1.0 if p["kind"] == "real" else -1.0)
    basis_ret = psign * (r_spot - r_perp)
    funding_ret = p["funding_accrued"]            # already signed in refresh()
    fee_ret = -FEE_BPS_RT / 10000.0
    return round((funding_ret + basis_ret + fee_ret) * NOTIONAL, 4)


def settle(st):
    """Close positions held >= MIN_HOLD_DAYS; book P&L; move to resolved."""
    keep, n = [], 0
    for p in st["open"]:
        held_days = (_now() - p["opened_ts"]) / 86400.0
        if held_days >= MIN_HOLD_DAYS:
            p["pnl"] = _close_pnl(p)
            p["held_days"] = round(held_days, 2)
            p["closed_at"] = _stamp()
            p["status"] = "resolved"
            st["resolved"].append(p)
            n += 1
        else:
            keep.append(p)
    st["open"] = keep
    print(f"settle: {n} closed (open: {len(st['open'])}, total resolved: {len(st['resolved'])})")
    return st


def _annual_hurdle():
    """Min ANNUALIZED |funding| for a MIN_HOLD_DAYS carry to clear round-trip fees by HURDLE_MARGIN.
    Annualized so configs with different funding intervals (8h venues vs HL's 1h) compare on ONE
    scale. Below it the premium doesn't cover cost → IDLE (honest null, not a faked number)."""
    return (FEE_BPS_RT / 10000.0) * HURDLE_MARGIN * 365.0 / MIN_HOLD_DAYS   # ~14.6%/yr at 8bps/3d


def _annualized(funding, interval_h):
    """Per-interval funding → annualized fraction, accounting for the venue's funding cadence."""
    return funding * (24.0 / interval_h) * 365.0


def scan(st):
    """Per asset, harvest on the CONFIG whose ANNUALIZED |funding| clears the hurdle by the MOST —
    funding_landscape's map wired into the leg. Same-venue (binance/bybit/okx) OR HL cross-venue.
    Opens REAL + CONTROL on that config. No config clears → asset IDLE (no cost-covering premium)."""
    held_assets = {p["asset"] for p in st["open"]}
    hurdle = _annual_hurdle()
    n = 0
    for asset in ASSETS:
        if asset in held_assets:
            continue
        best = None   # (cfg, snap, annualized)
        for cfg in CONFIGS:
            snap = snapshot(cfg, asset)
            if not snap:
                continue
            ann = _annualized(snap["funding"], snap["interval_h"])
            if abs(ann) >= hurdle and (best is None or abs(ann) > abs(best[2])):
                best = (cfg, snap, ann)
        if not best:
            print(f"scan: {asset} — no config clears {hurdle*100:.1f}%/yr, idle")
            continue
        cfg, snap, ann = best
        st["open"].append(_pos(cfg, asset, snap, "real"))
        st["open"].append(_pos(cfg, asset, snap, "control"))
        n += 2
        print(f"scan: {asset} → {cfg['label']} ann={ann*100:+.1f}%/yr → opened real+control")
    st["last_scan"] = _stamp()
    print(f"scan: opened {n} new (open now: {len(st['open'])}) · hurdle {hurdle*100:.1f}%/yr "
          f"across {len(CONFIGS)} configs (incl HL cross-venue)")
    return st


def _boot_ci(xs, iters=2000):
    if len(xs) < 2:
        return (None, None)
    import random
    random.seed(42)
    n = len(xs)
    means = sorted(sum(xs[random.randrange(n)] for _ in range(n)) / n for _ in range(iters))
    return (round(means[int(0.025 * iters)], 4), round(means[int(0.975 * iters)], 4))


def report(st):
    real = [p["pnl"] for p in st["resolved"] if p["kind"] == "real"]
    ctrl = [p["pnl"] for p in st["resolved"] if p["kind"] == "control"]
    print("=" * 66)
    print("  funding_basis — delta-neutral crypto carry (paper, NON-predictive)")
    print("=" * 66)
    for name, xs in (("REAL", real), ("control", ctrl)):
        if not xs:
            print(f"  {name:9} n=0")
            continue
        m = sum(xs) / len(xs)
        lo, hi = _boot_ci(xs)
        ci = f"  CI[{lo:+.3f},{hi:+.3f}]" if lo is not None else ""
        print(f"  {name:9} n={len(xs):>3}  ${m:+.4f}/exit  total ${sum(xs):+.2f}{ci}")
    nr = sum(1 for p in st["open"] if p["kind"] == "real")
    print(f"  open: {len(st['open'])} (real {nr}, control {len(st['open']) - nr}) · "
          f"last scan {st.get('last_scan')}")
    if len(real) >= 30:
        lo, _ = _boot_ci(real)
        rm, cm = sum(real) / len(real), (sum(ctrl) / len(ctrl) if ctrl else 0.0)
        ok = lo is not None and lo > 0 and rm > cm and cm <= 0
        print(f"  GATE: {'PASS — real carry edge (CI>0, beats control, control<=0)' if ok else 'no edge (gate not cleared)'}")
    else:
        print(f"  GATE: ACCUMULATING {len(real)}/30 real holds — fires ONLY when ANNUALIZED |funding| "
              f"clears the fee hurdle (~{_annual_hurdle()*100:.1f}%/yr) on one of {len(CONFIGS)} configs "
              f"(incl HL cross-venue). Idle in low-funding regimes is CORRECT, not broken. (Crypto "
              f"legs co-move → effective n < nominal; CI optimistic.)")


def main():
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "report"
    st = load_state()
    if cmd == "scan":
        st = scan(st); save_state(st); report(st)
    elif cmd == "settle":
        st = refresh(st); st = settle(st); save_state(st); report(st)
    elif cmd == "once":
        st = refresh(st); st = settle(st); st = scan(st); save_state(st); report(st)
    elif cmd == "report":
        report(st)
    else:
        print("usage: funding_basis.py scan|settle|once|report")


if __name__ == "__main__":
    main()
