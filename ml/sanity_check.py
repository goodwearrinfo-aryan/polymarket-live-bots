#!/usr/bin/env python3
"""
ml/sanity_check.py — is the model calibrated, or collapsing everything to ~0?

Scores several live markets (with their known market prices) and compares the
model's P(YES) to the market. A SANE model lands in a believable range; a BROKEN
one outputs ~0 for everything (even a market priced 0.41). Run on the Mac.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from predict import score_market

# (condition_id, market_price, label) — spread across the price range
TESTS = [
    ("0xd86a816093fcd0a0e1ca440bc5ce199bd3c5a8d6139e044b076958164f8c5423", 0.185, "US-Iran peace by Jun 15"),
    ("0x2cd24d17f5680dc6e7b7d67c712a223ae85550745597e81bd2d2b8d073443951", 0.228, "Sanchez Palomino (Peru)"),
    ("0x348cd9adf4f6855f58bd9c6dbf9ff251c4142ef77233a5dc95c65b4b61cd2187", 0.295, "Hormuz normal by Jun"),
    ("0xa70fc3695a65833b91b45df6db6015096f3e1471b70352ca411b4209010e7633", 0.375, "US-Iran nuclear by Jun 30"),
    ("0x89389a6b1439856a8d366234c795d72865c927a1aa3cf9d4cc04a3a400defde1", 0.4145, "Vegas win Stanley Cup"),
]

print("\n=========== MODEL SANITY CHECK ===========")
print(f"{'market':28s} {'mkt_price':>9s} {'model_P(YES)':>13s}   {'diff':>7s}")
print("-" * 64)
collapsed = 0
scored = 0
for cid, mp, label in TESTS:
    model_p = score_market(cid)
    if model_p is None:
        print(f"{label:28s} {mp:9.3f} {'(unavailable)':>13s}")
        continue
    scored += 1
    if model_p < 0.05:
        collapsed += 1
    print(f"{label:28s} {mp:9.3f} {model_p:13.3f}   {model_p-mp:+7.3f}")
print("-" * 64)
if scored == 0:
    print(" VERDICT: model couldn't score anything (network? model.pkl?).")
elif collapsed == scored:
    print(" VERDICT: ❌ BROKEN/OVERCONFIDENT — model says ~0 for EVERYTHING,")
    print("          including the 0.41 market. Its live NO bets are not trustworthy.")
    print("          Recalibrate (e.g. isotonic/Platt) before believing edge_trader.")
elif collapsed >= scored * 0.6:
    print(" VERDICT: ⚠️ heavy NO-bias — plausibly the favorite-longshot edge, but")
    print("          overconfident at the top. Watch live results closely.")
else:
    print(" VERDICT: ✅ predictions track the market sanely — edge_trader is trustworthy to test.")
print("==========================================\n")
