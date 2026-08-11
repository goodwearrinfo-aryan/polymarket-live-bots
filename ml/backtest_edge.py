#!/usr/bin/env python3
"""
ml/backtest_edge.py — Phase 3: does the +0.067 AUC edge turn into DOLLARS?

Honest backtest of trading the model's DIVERGENCE from the market:
  • Evaluate ONLY on held-out markets (same group split as train.py, seed 42) —
    never on markets the model trained on.
  • For each market, take ONE row (latest as-of) so it's one independent bet.
  • Where |model_prob - market_price| > EDGE_MIN, bet the model's side.
  • Charge the spread on entry (you pay ask = price + spread/2).
  • Compare to the ALLIN control (buy YES on everything) and to base rates.

If the divergence strategy's $/trade is positive AND beats allin, the edge is
real money — not just a better AUC. If not, the +0.067 was statistical, not tradable.

Run on the Mac (needs sklearn to load model.pkl):  python3 ml/backtest_edge.py
"""
import os, sys, csv, json, pickle, random

HERE = os.path.dirname(os.path.abspath(__file__))
DATASET = os.path.join(HERE, "dataset.csv")
MODEL = os.path.join(HERE, "model.pkl")

EDGE_MIN = 0.10      # only trade when model disagrees with price by >= this
SPREAD = 0.02        # round-trip spread assumption; you pay half on entry
BAND = (0.05, 0.95)  # skip degenerate near-0/1 prices


def group_test_markets(rows, frac=0.8, seed=42):
    cids = sorted({r["condition_id"] for r in rows})
    random.Random(seed).shuffle(cids)
    cut = max(1, int(len(cids) * frac))
    return set(cids[cut:])   # the held-out 20%


def main():
    if not os.path.exists(MODEL):
        sys.exit("No model.pkl — run train.py first (and it must have found an edge to save one).")
    import numpy as np
    blob = pickle.load(open(MODEL, "rb"))
    model, feats = blob["model"], blob["features"]

    rows = [r for r in csv.DictReader(open(DATASET)) if r.get("final_outcome") not in ("", "None", None)]
    test_cids = group_test_markets(rows)
    # one row per held-out market (latest as_of) = one independent bet
    latest = {}
    for r in rows:
        if r["condition_id"] in test_cids:
            c = r["condition_id"]
            if c not in latest or int(r["as_of"]) > int(latest[c]["as_of"]):
                latest[c] = r
    test = list(latest.values())
    if not test:
        sys.exit("No held-out markets to backtest.")

    X = np.array([[float(r[f]) for f in feats] for r in test])
    mp = model.predict_proba(X)[:, 1]   # model P(YES)

    div_pnl = div_n = div_w = 0.0
    allin_pnl = 0.0
    for r, model_p in zip(test, mp):
        price = float(r["prob"])
        outcome = int(r["final_outcome"])           # 1 = YES resolved
        # ALLIN control: buy YES on everything in-band, pay the ask
        if BAND[0] <= price <= BAND[1]:
            allin_pnl += (outcome - (price + SPREAD / 2))
        # DIVERGENCE strategy
        if not (BAND[0] <= price <= BAND[1]):
            continue
        if abs(model_p - price) < EDGE_MIN:
            continue
        if model_p > price:                          # model says YES underpriced -> buy YES
            entry = price + SPREAD / 2
            pnl = outcome - entry
        else:                                        # model says YES overpriced -> buy NO
            entry = (1 - price) + SPREAD / 2
            pnl = (1 - outcome) - entry
        div_pnl += pnl; div_n += 1; div_w += 1 if pnl > 0 else 0

    n_allin = sum(1 for r in test if BAND[0] <= float(r["prob"]) <= BAND[1])
    print("\n============== PHASE 3 — DIVERGENCE BACKTEST ==============")
    print(f" held-out markets: {len(test)}  (never seen in training)")
    print(f" edge_min={EDGE_MIN}  spread={SPREAD}  band={BAND}")
    print("-" * 58)
    if div_n:
        print(f" DIVERGENCE strategy: trades={int(div_n)}  win={div_w/div_n:.0%}")
        print(f"   total P&L = {div_pnl:+.3f}   per-trade = {div_pnl/div_n:+.4f}")
    else:
        print(" DIVERGENCE strategy: 0 trades (model never disagreed with price by >= edge_min)")
    if n_allin:
        print(f" ALLIN control:       trades={n_allin}  per-trade = {allin_pnl/n_allin:+.4f}")
    print("-" * 58)
    if div_n and (div_pnl / div_n) > 0 and (not n_allin or div_pnl/div_n > allin_pnl/n_allin):
        print(" VERDICT: edge is TRADABLE — divergence is +EV after spread AND beats allin.")
        print("          Next: confirm on a fresh time period, then tiny real money.")
        # HONEST CROSS-CHECK: this backtest is IN-SAMPLE (group-split on the training
        # dataset). The OUT-OF-TIME test is the real verdict — if it refuted the edge,
        # say so here so a reader never trusts the stale banner (experiment closed
        # 2026-08-11: P(edge>0)=0.035, model AUC 0.478 < price AUC 0.648).
        try:
            oot = json.load(open(os.path.join(HERE, "backtest_oot_result.json")))
            m, p = oot.get("model_auc"), oot.get("price_auc")
            if m is not None and p is not None and m <= p:
                print("          ⚠ OUT-OF-TIME REFUTED this: model AUC %.3f <= price AUC %.3f." % (m, p))
                print("            This in-sample TRADABLE banner is SUPERSEDED — do not act on it.")
        except Exception:
            pass
    elif div_n:
        print(" VERDICT: edge does NOT survive costs — +0.067 AUC was statistical, not money.")
        print("          The market's price is already good enough; don't trade this.")
    print("==========================================================\n")
    json.dump({"held_out_markets": len(test), "div_trades": int(div_n),
               "div_pnl_per_trade": round(div_pnl/div_n, 4) if div_n else None,
               "div_win": round(div_w/div_n, 3) if div_n else None,
               "allin_pnl_per_trade": round(allin_pnl/n_allin, 4) if n_allin else None,
               "edge_min": EDGE_MIN, "spread": SPREAD},
              open(os.path.join(HERE, "backtest_result.json"), "w"), indent=2)


if __name__ == "__main__":
    main()
