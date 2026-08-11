#!/usr/bin/env python3
"""copybot_checkpoint.py — did the R:R change help the copy-trading bot? (read-only)

Splits trade_log.json's closed trades by the .rr_fix_at cutoff and compares
$/trade (bootstrap 95% CI) BEFORE vs AFTER the change, plus the exit-reason mix
(is the trailing stop firing now? are wins bigger?).

IMPORTANT: the copy-trading signal has NO measured edge (top-ranked wallets did
worse; confidence didn't predict winners). So this tells you whether the structural
R:R fix REDUCED the bleed — NOT whether the bot is profitable. Best case is
less-negative. Paper only.
"""
import os, sys, json, collections
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
from psr_dsr import boot_ci

_cf = os.path.join(HERE, ".rr_fix_at")
CUT = open(_cf).read().strip() if os.path.exists(_cf) else ""
P = lambda t: t.get("pnl_usdc", 0.0)


def line(name, g):
    p = [P(t) for t in g]
    if len(p) >= 2:
        m, lo, hi = boot_ci(p, 3000, 0)
        win = 100 * sum(1 for x in p if x > 0) / len(p)
        flag = "LOSES" if hi < 0 else ("WINS" if lo > 0 else "noise")
        print(f"  {name}: n={len(p):3}  ${m:+.2f}/trade  CI[{lo:+.2f},{hi:+.2f}]  win={win:.0f}%  total=${sum(p):+.0f}  {flag}")
    else:
        print(f"  {name}: n={len(g)} (need >=2 closed)")


def main():
    c = json.load(open(os.path.join(HERE, "trade_log.json"))).get("closed", [])
    pre = [t for t in c if t.get("closed_at", "") < CUT]
    post = [t for t in c if t.get("closed_at", "") >= CUT]
    print(f"COPY-BOT R:R CHECKPOINT  (cutoff {CUT}; signal has NO measured edge)")
    line("BEFORE R:R fix", pre)
    line("AFTER  R:R fix", post)
    if post:
        print("  post exit-reason mix:", dict(collections.Counter(t.get("reason") for t in post)))
    if len(post) < 20:
        print(f"\n  VERDICT: too early — {len(post)}/~30 trades closed since the fix. Let it run, re-check.")
    else:
        mp = sum(P(t) for t in post) / len(post)
        mpre = sum(P(t) for t in pre) / len(pre) if pre else 0.0
        print(f"\n  VERDICT: post ${mp:+.2f}/tr vs pre ${mpre:+.2f}/tr -> "
              f"R:R fix {'REDUCED the bleed' if mp > mpre else 'did NOT help'}.")
        print("  (No signal edge -> the realistic ceiling is less-negative, not profit.)")


if __name__ == "__main__":
    main()
