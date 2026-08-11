#!/usr/bin/env python3
"""
whale_copy_paper.py — PAPER, read-only copy-trading experiment.

The HONEST version of "copy the top traders" (2026-07-30). Your existing scalp-copy
legs (walletcopy/whale/multiwhale) all LOSE because they copy whale *scalps* at 60s
latency → adverse selection (you fill after the edge is gone). This experiment tries
the ONE variant that can sidestep timing latency: copy their HELD-to-resolution
positions — you bet on the whale's *outcome read*, not race their entry.

Method (paper, no orders ever):
  1. pull the top-N leaderboard wallets (30d PnL, keyless)
  2. read each wallet's ACTIVE held positions (redeemable=False)
  3. paper-enter NEW ones at the current price (honest: real fills would be worse)
  4. each run, mark existing copies; when a market resolves, book paper P&L
  5. log everything so the data decides — measured against the known-losing baseline

Read-only: reads data-api + gamma. NEVER places, signs, or sizes a real order.
State: whale_copy_state.json   Log: whale_copy_paper.log
"""
from __future__ import annotations
import json, os, urllib.request
from datetime import datetime, timezone

STATE = os.path.expanduser("~/polymarket-live/whale_copy_state.json")
LOG   = os.path.expanduser("~/polymarket-live/whale_copy_paper.log")
N_WALLETS   = 10       # copy top-10 by 30d PnL (5x more copyable positions than top-5 → reach n>=30 faster; still elite)
MIN_NOTIONAL = 5000    # only copy the whale's meaningful positions ($ initialValue)
STAKE = 10.0           # paper $ per copied position (fixed, honest small size)

def get(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": "whale-copy-paper/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)

def top_wallets():
    d = get(f"https://data-api.polymarket.com/v1/leaderboard?window=30d&rankType=pnl&limit={N_WALLETS}")
    rows = d if isinstance(d, list) else d.get("data", [])
    return [r.get("proxyWallet", r.get("wallet", "")) for r in rows if r.get("proxyWallet") or r.get("wallet")]

def active_positions(wallet):
    try:
        d = get(f"https://data-api.polymarket.com/positions?user={wallet}&limit=50")
    except Exception:
        return []
    out = []
    for p in (d if isinstance(d, list) else d.get("data", [])):
        if p.get("redeemable"):                    # already resolved — skip (can't copy a settled bet)
            continue
        cur = p.get("curPrice")
        if cur is None or cur <= 0 or cur >= 1:     # no live price
            continue
        if (p.get("initialValue") or 0) < MIN_NOTIONAL:
            continue
        out.append({"asset": p.get("asset"), "cond": p.get("conditionId"),
                    "idx": p.get("outcomeIndex"),
                    "title": p.get("title", ""), "outcome": p.get("outcome", ""),
                    "entry": float(cur), "endDate": p.get("endDate", "")})
    return out

def load_state():
    try:
        return json.load(open(STATE))
    except Exception:
        return {"open": {}, "closed": []}

def resolve_price(cond, idx):
    """Deterministic ON-CHAIN resolution (CTF payoutNumerators). Returns 1.0 if the
    copied outcome won, 0.0 if lost, None if not yet resolved. This is ground truth —
    the data-api positions endpoint is unreliable (winners are redeemed and vanish;
    only losing tokens linger as redeemable, so it falsely reports every copy as a loss)."""
    if cond is None or idx is None:
        return None
    try:
        import ctf_resolution as ctf
        if not ctf.is_resolved(cond):
            return None
        return ctf.resolved_price(cond, int(idx))
    except Exception:
        return None

def main():
    st = load_state()
    wallets = top_wallets()
    added = 0
    # 1. discover + paper-enter new held positions
    for w in wallets:
        for pos in active_positions(w):
            key = f"{w[:8]}:{pos['asset']}"
            if key in st["open"]:
                continue
            st["open"][key] = {"wallet": w, **pos, "stake": STAKE,
                               "opened": datetime.now(timezone.utc).isoformat()}
            added += 1
    # 2. mark / resolve existing copies
    closed_now = 0
    for key in list(st["open"].keys()):
        c = st["open"][key]
        term = resolve_price(c.get("cond"), c.get("idx"))
        if term is not None:
            # paper P&L: bought `outcome` at entry; pays 1 if it won, 0 if lost
            shares = c["stake"] / c["entry"]
            pnl = shares * (term - c["entry"])
            c.update({"exit": term, "pnl": round(pnl, 3),
                      "closed_at": datetime.now(timezone.utc).isoformat()})
            st["closed"].append(c)
            del st["open"][key]
            closed_now += 1
    json.dump(st, open(STATE, "w"), indent=0)
    # 3. honest ledger
    tot = sum(c.get("pnl", 0) for c in st["closed"])
    n = len(st["closed"])
    wins = sum(1 for c in st["closed"] if c.get("pnl", 0) > 0)
    line = (f"[{datetime.now(timezone.utc).isoformat()}] copies open={len(st['open'])} "
            f"(+{added} new) closed={n} (+{closed_now}) "
            f"paper_pnl={tot:.3f} wr={100*wins/n:.0f}%" if n else
            f"[{datetime.now(timezone.utc).isoformat()}] copies open={len(st['open'])} "
            f"(+{added} new) closed=0 — accumulating, no verdict yet")
    open(LOG, "a").write(line + "\n")
    print(line)
    if n >= 30:
        print(f"  → {n} closed: baseline to beat is your scalp-copy legs (all negative). "
              f"This copy-held P&L = {tot:.2f}. CI test needed before any verdict.")

if __name__ == "__main__":
    main()
