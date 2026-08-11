#!/usr/bin/env python3
"""ctf_resolution.py — READ-ONLY on-chain resolution oracle for Polymarket markets.

Ported (read paths only) from oss-bots/polymarket-terminal/src/services/ctf.js.
The JS repo is a LIVE-MONEY bot; this port deliberately keeps ONLY the read-only
ConditionalTokens (CTF) calls — split/merge/redeem and all Safe signing were left
behind on purpose. PAPER-ONLY: this module never signs, never sends a tx, never
touches a private key. Pure stdlib JSON-RPC eth_call (same style as collect_onchain.py).

Why it matters: resolved Polymarket markets VANISH from gamma, so the lab books
held positions at $0 (the `stale_nodata` bug). The CTF contract still knows the
truth on-chain — this reads it directly:
  - is_resolved(conditionId)         -> payoutDenominator != 0
  - winning_outcome(conditionId)     -> which index paid out (0/1 for binary), or None
  - resolved_price(conditionId, idx) -> 1.0 / 0.0 for a resolved binary outcome
  - erc1155_balance(wallet, tokenId) -> true on-chain token balance (data-api lags)

Contract addresses verified against the JS source (Polygon mainnet).

Usage:
  python3 ctf_resolution.py <conditionId> [--rpc URL]
  >>> from ctf_resolution import is_resolved, resolved_price
"""
import os, sys, json, argparse, urllib.request

RPC_URL = os.environ.get("POLYGON_RPC_URL", "https://polygon.gateway.tenderly.co")  # polygon-rpc.com now 401s
CTF_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"   # ConditionalTokens (Gnosis)
USDC_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"  # USDC.e
UA = {"User-Agent": "poly-collect/1.0", "Content-Type": "application/json"}

# 4-byte selectors (keccak256(sig)[:4]); verified 2026-06-13
SEL_PAYOUT_DENOM = "dd34de67"    # payoutDenominator(bytes32)
SEL_PAYOUT_NUM   = "0504c814"    # payoutNumerators(bytes32,uint256)
SEL_BALANCE_OF   = "00fdd58e"    # balanceOf(address,uint256)


def _rpc(method, params, retries=3):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    last = None
    for a in range(retries + 1):
        try:
            req = urllib.request.Request(RPC_URL, data=body, headers=UA)
            with urllib.request.urlopen(req, timeout=20) as r:
                j = json.load(r)
            if "error" in j:
                raise RuntimeError(j["error"])
            return j["result"]
        except Exception as e:
            last = e
            if a < retries:
                import time; time.sleep(1.0 * (a + 1))
    raise RuntimeError(f"rpc {method} failed: {last}")


def _b32(hexstr):
    """Normalize a conditionId / bytes32 to 64 hex chars, no 0x."""
    h = hexstr.lower().removeprefix("0x")
    return h.rjust(64, "0")[-64:]


def _u256(n):
    return f"{n:064x}"


def _addr32(addr):
    return addr.lower().removeprefix("0x").rjust(64, "0")


def _call(to, data_hex):
    """eth_call returning the raw hex result (read-only; no state change)."""
    res = _rpc("eth_call", [{"to": to, "data": "0x" + data_hex}, "latest"])
    return res or "0x"


def payout_denominator(condition_id):
    """0 = unresolved. Nonzero = market resolved (sum of numerators)."""
    out = _call(CTF_ADDRESS, SEL_PAYOUT_DENOM + _b32(condition_id))
    return int(out, 16) if out and out != "0x" else 0


def payout_numerator(condition_id, outcome_index):
    out = _call(CTF_ADDRESS, SEL_PAYOUT_NUM + _b32(condition_id) + _u256(outcome_index))
    return int(out, 16) if out and out != "0x" else 0


def is_resolved(condition_id):
    return payout_denominator(condition_id) != 0


def resolved_price(condition_id, outcome_index):
    """Final on-chain price of an outcome token: numerator/denominator.
    Binary YES/NO resolves to exactly 1.0 or 0.0. Returns None if unresolved."""
    denom = payout_denominator(condition_id)
    if denom == 0:
        return None
    return payout_numerator(condition_id, outcome_index) / denom


def winning_outcome(condition_id, n_outcomes=2):
    """Index of the outcome that paid out (>0). None if unresolved; -1 if no winner."""
    denom = payout_denominator(condition_id)
    if denom == 0:
        return None
    for i in range(n_outcomes):
        if payout_numerator(condition_id, i) > 0:
            return i
    return -1


def erc1155_balance(wallet, token_id, decimals=6):
    """True on-chain ERC-1155 balance of an outcome token (data-api /positions lags
    resolution; the JS uses this as 'source of truth')."""
    tid = int(token_id) if not str(token_id).startswith("0x") else int(token_id, 16)
    data = SEL_BALANCE_OF + _addr32(wallet) + _u256(tid)
    out = _call(CTF_ADDRESS, data)
    raw = int(out, 16) if out and out != "0x" else 0
    return raw / (10 ** decimals)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Read on-chain Polymarket resolution from the CTF contract")
    ap.add_argument("condition_id", help="market conditionId (0x… bytes32)")
    ap.add_argument("--rpc", help="override Polygon RPC URL")
    ap.add_argument("--outcomes", type=int, default=2)
    a = ap.parse_args()
    if a.rpc:
        RPC_URL = a.rpc
    cid = a.condition_id
    denom = payout_denominator(cid)
    print(f"RPC: {RPC_URL}")
    print(f"conditionId: {cid}")
    print(f"payoutDenominator: {denom}  ->  {'RESOLVED' if denom else 'UNRESOLVED'}")
    if denom:
        win = winning_outcome(cid, a.outcomes)
        print(f"winning outcome index: {win}")
        for i in range(a.outcomes):
            print(f"  outcome[{i}] resolved_price = {resolved_price(cid, i):.4f}")
