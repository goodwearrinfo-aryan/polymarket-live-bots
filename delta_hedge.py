#!/usr/bin/env python3
"""
delta_hedge.py — the AutoHedge.jl skill, in Python (keyless), on real Deribit IV/skew.
─────────────────────────────────────────────────────────────────────────────
Harvests AutoHedge.jl's transferable skill (NOT its Julia runtime): Black-Scholes
greeks → delta-neutral rebalancing → per-rebalance transaction cost → P&L attribution.
Answers the question the options-legs-oss note left open: is SKEW-RV / short-vol a real
edge ONCE delta-hedged, or just the naked short-vol trap?

HONESTY (parallel-review fixes 2026-06-20): premium is collected at the BID
(iv − vol_spread/2), NOT the mid — you SELL vol, so the option bid/ask is paid out of
the VRP itself (the one positive term must not get a clean fill, Lesson 13). Perp FUNDING
carry is NOT modeled (only per-trade tx, r=0); and GBM paths have no jumps, so the printed
5% left tail is a FLOOR on real crash risk, never the actual tail. Treat a 🟢 as necessary-
not-sufficient; the real verdict needs a forward-recorded tape (accumulate-don't-trade).

Real inputs: current ATM IV + 25Δ skew + matched realized vol from deribit_vol.py
(keyless Deribit + Binance). Forward paths are SIMULATED GBM at the realized vol
(exactly how AutoHedge.jl backtests) — because Deribit's free API is a snapshot, not
a historical option tape. So this measures the HEDGED P&L DISTRIBUTION + its left tail,
honestly labeled synthetic. For a real-data verdict, the path must come from a
forward-recorded tape (accumulate-don't-trade), not free history.

  python3 delta_hedge.py                 # pull real BTC/ETH IV+skew, sim hedged short-vol P&L
  python3 delta_hedge.py --paths 20000   # more Monte-Carlo paths
─────────────────────────────────────────────────────────────────────────────
"""
import sys, math, statistics, random
random.seed(7)

try:
    import deribit_vol as dv
    HAVE_DV = True
except Exception:
    HAVE_DV = False


# ── Black-Scholes greeks (pure Python, no scipy) ──────────────────────────────
def _N(x):  return 0.5 * (1 + math.erf(x / math.sqrt(2)))
def _n(x):  return math.exp(-x * x / 2) / math.sqrt(2 * math.pi)

def bs(S, K, T, r, sig, typ="call", q=0.0):
    if T <= 0 or sig <= 0:
        intrinsic = max(S - K, 0) if typ == "call" else max(K - S, 0)
        return {"price": intrinsic, "delta": (1.0 if (typ=="call" and S>K) else (-1.0 if (typ=="put" and S<K) else 0.0)),
                "gamma": 0.0, "vega": 0.0, "theta": 0.0}
    d1 = (math.log(S / K) + (r - q + 0.5 * sig * sig) * T) / (sig * math.sqrt(T))
    d2 = d1 - sig * math.sqrt(T)
    df, dfq = math.exp(-r * T), math.exp(-q * T)
    if typ == "call":
        price = S * dfq * _N(d1) - K * df * _N(d2);  delta = dfq * _N(d1)
    else:
        price = K * df * _N(-d2) - S * dfq * _N(-d1); delta = -dfq * _N(-d1)
    gamma = dfq * _n(d1) / (S * sig * math.sqrt(T))
    vega  = S * dfq * _n(d1) * math.sqrt(T) / 100
    theta = (-(S * dfq * _n(d1) * sig) / (2 * math.sqrt(T))) / 365
    return {"price": price, "delta": delta, "gamma": gamma, "vega": vega, "theta": theta}


def run(currency, symbol, index, paths, cost_bps=4.0, vol_spread=2.0):
    if not HAVE_DV:
        print("  deribit_vol.py not importable — run from ~/Documents/polymarket"); return
    _idx, ts = dv.term_structure(currency, symbol, index)
    if not ts:
        print(f"  no Deribit chain for {currency}"); return
    # pick the nearest expiry with a real skew + rv
    row = next((r for r in ts if r.get("atm_iv") and r.get("rv") and r.get("skew") is not None), None)
    if not row:
        print(f"  {currency}: no expiry with IV+RV+skew"); return
    dte = row["dte"]; iv = row["atm_iv"]; rv = row["rv"]; skew = row["skew"]; S0 = row["fwd"]
    T = max(dte / 365.0, 1/365); K = round(S0)  # ATM
    steps = max(int(dte), 2)

    pnls = []
    for _ in range(paths):
        # net P&L of a delta-hedged short ATM straddle-leg (call) harvesting VRP
        pnl = _net(S0, K, T, 0.0, iv, rv, "call", steps, cost_bps, vol_spread)
        pnls.append(pnl)
    mean = statistics.mean(pnls); sd = statistics.pstdev(pnls)
    p05 = sorted(pnls)[int(0.05*len(pnls))]; p95 = sorted(pnls)[int(0.95*len(pnls))]
    print(f"\n  {currency}  ATM≈{S0:.0f}  DTE {dte:.0f}  IV {iv:.1f}%  RV {rv:.1f}%  VRP {iv-rv:+.1f}  25Δskew {skew:+.1f}")
    print(f"    Delta-hedged short-vol P&L over {paths} sim paths (GBM@RV, sold@bid={iv-vol_spread/2:.1f}%, {cost_bps}bps tx):")
    print(f"      mean ${mean:+.2f}/contract  sd ${sd:.2f}  5%—95% [${p05:+.2f}, ${p95:+.2f}]")
    edge = "🟢 positive expectancy (VRP harvested net of bid spread)" if mean > 0 else "🔴 negative even hedged"
    tail = "⚠️ fat left tail — and GBM UNDERSTATES it (no jumps); real crash worse" if p05 < -abs(mean)*3 else "tail contained (GBM floor — real tail fatter)"
    print(f"      → {edge}; {tail}")


def _net(S0, K, T, r, iv, rv, typ, steps, cost_bps, vol_spread=2.0):
    """Net P&L of the delta-hedged short option (premium − payoff − hedge − tx).
    Premium is collected at the BID (iv − vol_spread/2), NOT the mid: you sell vol, so the
    option bid/ask is paid out of the VRP — the one positive term gets no clean fill."""
    dt = T / steps; sig = rv / 100.0
    iv_bid = max(iv - vol_spread/2.0, 0.1)
    prem = bs(S0, K, T, r, iv_bid/100.0, typ)["price"]
    S = S0; hedge = 0.0; cash = 0.0; tx = 0.0
    for i in range(steps):
        g = bs(S, K, T - i*dt, r, iv/100.0, typ)
        d = g["delta"] - hedge
        tx += abs(d) * S * (cost_bps/1e4)
        cash -= d * S; hedge = g["delta"]
        z = random.gauss(0, 1)
        S *= math.exp(-0.5*sig**2*dt + sig*math.sqrt(dt)*z)
    payoff = max(S-K,0) if typ=="call" else max(K-S,0)
    cash += hedge * S
    return prem - payoff + cash - tx


if __name__ == "__main__":
    paths = int(sys.argv[sys.argv.index("--paths")+1]) if "--paths" in sys.argv else 10000
    print(f"{'='*72}\n  DELTA-HEDGED SHORT-VOL (AutoHedge skill, Python, real Deribit IV/skew)\n{'='*72}")
    print("  ⚠️ Forward paths are SIMULATED GBM (no free historical option tape). The")
    print("     P&L DISTRIBUTION + left tail are the honest output, not a live backtest.")
    for cur, sym, idx in [("BTC","BTCUSDT","btc_usd"), ("ETH","ETHUSDT","eth_usd")]:
        try: run(cur, sym, idx, paths)
        except Exception as e: print(f"  {cur} ERR {str(e)[:70]}")
    print("\n  Honest: mean>0 = VRP is real premium; the left tail = why it's NOT free money")
    print("  (crash insurance). A LIVE verdict needs a forward-recorded Deribit tape.\n")
