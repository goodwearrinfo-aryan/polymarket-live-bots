#!/usr/bin/env python3
"""goldsky_fades.py — build RESOLVED-MARKET fade observations from the V1-era
Goldsky orderbook-subgraph, FULLY ON-CHAIN (no Gamma dependency).

Why this exists: collect_onchain.py joins fills to Gamma by clob_token_ids, but
Gamma drops old markets from BOTH its token-id AND condition-id indexes (measured
2026-06-14: token match 6/8 at 1d → 1/8 at 14d → 0/4 at 1yr). So the deep V1 era
(2022-11 → 2026-04) — exactly the OOS bulk we want — is invisible to that path.

This collector never touches Gamma. Every join is the subgraph or the chain:
  1. subgraph orderFilledEvents  -> fill prices  (USDC leg assetId == "0")
  2. subgraph MarketData         -> token assetId -> conditionId
  3. on-chain getCollectionId/getPositionId (CTF) -> token -> outcomeIndex (0/1)
  4. on-chain ConditionalTokens payout (ctf_resolution) -> conditionId -> winner
Prices are normalized to the YES (outcome-0) side, aggregated per condition.
Coverage verified 2026-06-14 on 1-yr-old fills: MarketData 92%, positionId 83%,
on-chain resolution 100%. Category is unavailable on-chain -> "onchain_unknown".

Output columns match ml/dataset.csv (condition_id, category, prob, final_outcome,
n_fills, source) so fade_research.py / bot_backtest.py consume it directly.
PAPER research — read-only, no keys, no transactions.

Usage:
  python3 goldsky_fades.py --pages 200                 # harvest+resolve ~200k fills
  python3 goldsky_fades.py --pages 0 --resolve         # resolve what's accumulated, write CSV
  python3 goldsky_fades.py --pages 0 --stats           # show accumulated state
"""
import os, sys, json, time, argparse, statistics, urllib.request
from Crypto.Hash import keccak

HERE = os.path.dirname(os.path.abspath(__file__))
SUBGRAPH = ("https://api.goldsky.com/api/public/project_cl6mb8i9h0003e201j6li0diw"
            "/subgraphs/orderbook-subgraph/prod/gn")
RPC_URL  = os.environ.get("POLYGON_RPC_URL", "https://polygon.drpc.org")
STATE    = os.path.join(HERE, "goldsky_fades_state.json")
OUT_CSV  = os.path.join(HERE, "ml", "dataset_goldsky.csv")

CTF  = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"   # ConditionalTokens (Polygon)
USDC = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"   # USDC.e — Polymarket collateral
SEL_COLL = "856296f7"   # getCollectionId(bytes32,bytes32,uint256)
SEL_POS  = "39dd7530"   # getPositionId(address,bytes32)
SEL_DENOM = "dd34de67"  # payoutDenominator(bytes32)
SEL_NUM   = "0504c814"  # payoutNumerators(bytes32,uint256)

CUTOVER_TS = 1782000000          # sentinel just past newest V1 fill (2026-04-28)
PAGE  = 1000                     # subgraph hard cap
SLEEP = float(os.environ.get("SLEEP", "0.5"))
MIN_FILLS = int(os.environ.get("MIN_FILLS", "1"))   # min fills/condition to emit a row


# ── HTTP plumbing ───────────────────────────────────────────────────────────
def _post(url, payload, tries=6):
    body = json.dumps(payload).encode()
    for a in range(tries):
        try:
            req = urllib.request.Request(url, data=body,
                    headers={"Content-Type": "application/json", "User-Agent": "poly-collect/1.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except Exception as e:
            if a == tries - 1:
                raise
            time.sleep(2 * (a + 1))


def gql(query, variables=None):
    j = _post(SUBGRAPH, {"query": query, "variables": variables or {}})
    if "errors" in j:
        raise RuntimeError(j["errors"][0].get("message", "gql error"))
    return j["data"]


def eth_call(to, data):
    j = _post(RPC_URL, {"jsonrpc": "2.0", "id": 1, "method": "eth_call",
                        "params": [{"to": to, "data": "0x" + data}, "latest"]})
    return j.get("result")


# ── on-chain helpers ─────────────────────────────────────────────────────────
def _b32(h):    return h.lower().replace("0x", "").rjust(64, "0")
def _u256(n):   return format(n, "x").rjust(64, "0")
def _addr32(a): return a.lower().replace("0x", "").rjust(64, "0")


def position_id(cond, index_set):
    """CTF positionId for (USDC collateral, top-level collection of `cond`/`index_set`)."""
    coll = eth_call(CTF, SEL_COLL + _b32("0" * 64) + _b32(cond) + _u256(index_set))
    if not coll or int(coll, 16) == 0:
        return None
    pos = eth_call(CTF, SEL_POS + _addr32(USDC) + coll[2:].rjust(64, "0"))
    return int(pos, 16) if pos else None


def outcome_index_of(token_int, cond):
    """0 if token is outcome-0 (indexSet 1), 1 if outcome-1 (indexSet 2), else None
    (non-USDC collateral / negRisk / multi-outcome — skip those)."""
    p0 = position_id(cond, 1)
    if token_int == p0:
        return 0
    p1 = position_id(cond, 2)
    if token_int == p1:
        return 1
    return None


def winning_outcome(cond):
    """0 or 1 = winning outcome of a resolved binary condition; None if unresolved."""
    denom = eth_call(CTF, SEL_DENOM + _b32(cond))
    if not denom or int(denom, 16) == 0:
        return None                       # payoutDenominator 0 => not resolved
    num0 = eth_call(CTF, SEL_NUM + _b32(cond) + _u256(0))
    return 0 if (num0 and int(num0, 16) > 0) else 1


# ── decode ────────────────────────────────────────────────────────────────────
def decode(e):
    """OrderFilledEvent -> (token_assetId:str, price_of_that_token:float in [0,1])
    or None. USDC leg has assetId '0'; price = USDC_amount / token_amount."""
    mA, tA = e["makerAssetId"], e["takerAssetId"]
    mAmt, tAmt = int(e["makerAmountFilled"]), int(e["takerAmountFilled"])
    if mA == "0" and tAmt > 0:  return tA, mAmt / tAmt   # bought token with USDC
    if tA == "0" and mAmt > 0:  return mA, tAmt / mAmt   # sold token for USDC
    return None


# ── state ───────────────────────────────────────────────────────────────────
def load_state():
    if os.path.exists(STATE):
        with open(STATE) as f:
            return json.load(f)
    return {"cursor_ts": CUTOVER_TS, "fills": {}, "pages_done": 0, "done": False}


def save_state(st):
    tmp = STATE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f)
    os.replace(tmp, STATE)


# ── phase A: harvest fills (per token price list) ─────────────────────────────
_Q = ("query($lt:BigInt!){orderFilledEvents(first:1000,orderBy:timestamp,"
      "orderDirection:desc,where:{timestamp_lt:$lt}){timestamp makerAssetId "
      "takerAssetId makerAmountFilled takerAmountFilled}}")


def harvest(pages):
    st = load_state()
    if st.get("done"):
        print("subgraph fully walked to V1 genesis. Delete state to redo.")
        return st
    fills = st["fills"]
    print(f"resuming: cursor_ts={st['cursor_ts']} "
          f"({time.strftime('%Y-%m-%d', time.gmtime(st['cursor_ts']))})  "
          f"tokens={len(fills)}  pages_done={st['pages_done']}", flush=True)
    for i in range(pages):
        try:
            ev = gql(_Q, {"lt": str(st["cursor_ts"])})["orderFilledEvents"]
        except Exception as e:
            print(f"  page at ts={st['cursor_ts']} failed ({e}) — state saved, rerun to resume", flush=True)
            save_state(st)
            return st
        if not ev:
            st["done"] = True
            save_state(st)
            print("  reached oldest V1 fill (2022) — harvest COMPLETE.", flush=True)
            return st
        for e in ev:
            d = decode(e)
            if d and 0 < d[1] < 1.0001:
                fills.setdefault(d[0], []).append(round(d[1], 4))
        st["cursor_ts"] = int(ev[-1]["timestamp"])
        st["pages_done"] += 1
        if (i + 1) % 10 == 0 or i == pages - 1:
            save_state(st)
            d = time.strftime("%Y-%m-%d", time.gmtime(st["cursor_ts"]))
            print(f"  [{i+1}/{pages}] back to {d}  unique tokens={len(fills)}", flush=True)
        time.sleep(SLEEP)
    save_state(st)
    return st


# ── phase B: resolve tokens -> conditions -> CSV rows ─────────────────────────
def resolve_and_write(st, out_path):
    fills = st["fills"]
    tokens = list(fills.keys())
    if not tokens:
        print("no fills accumulated — run with --pages N first.")
        return
    print(f"resolving {len(tokens)} unique tokens…", flush=True)

    # token -> conditionId via subgraph MarketData (batched)
    tok2cond = {}
    for i in range(0, len(tokens), 500):
        batch = tokens[i:i + 500]
        md = gql('query($ids:[ID!]){marketDatas(where:{id_in:$ids}){id condition}}',
                 {"ids": batch})["marketDatas"]
        for r in md:
            tok2cond[r["id"]] = r["condition"]
        print(f"  MarketData {min(i+500,len(tokens))}/{len(tokens)} "
              f"(matched {len(tok2cond)})", flush=True)
    print(f"  token->condition: {len(tok2cond)}/{len(tokens)} matched", flush=True)

    # per condition: collect YES-normalized prices; resolve winner on-chain (concurrent)
    from concurrent.futures import ThreadPoolExecutor, as_completed

    unique_conds = list(set(tok2cond.values()))
    win_cache = {}
    print(f"  resolving {len(unique_conds)} unique conditions concurrently…", flush=True)

    def _resolve_win(cond):
        try:
            return cond, winning_outcome(cond)
        except Exception:
            return cond, None

    with ThreadPoolExecutor(max_workers=16) as ex:
        for fut in as_completed({ex.submit(_resolve_win, c): c for c in unique_conds}):
            c, w = fut.result()
            win_cache[c] = w

    resolved_conds = [(tok, cond) for tok, cond in tok2cond.items() if win_cache.get(cond) is not None]
    print(f"  {len(resolved_conds)} tokens in resolved conditions "
          f"({len(unique_conds)-len(set(c for _,c in resolved_conds))} unresolved conditions skipped)", flush=True)

    oidx_cache = {}
    print(f"  resolving outcome-index for {len(resolved_conds)} tokens concurrently…", flush=True)

    def _resolve_oidx(tok, cond):
        try:
            return tok, outcome_index_of(int(tok), cond)
        except Exception:
            return tok, None

    with ThreadPoolExecutor(max_workers=16) as ex:
        futs = {ex.submit(_resolve_oidx, tok, cond): tok for tok, cond in resolved_conds}
        done = 0
        for fut in as_completed(futs):
            tok, oidx = fut.result()
            oidx_cache[tok] = oidx
            done += 1
            if done % 50 == 0 or done == len(resolved_conds):
                print(f"  oidx {done}/{len(resolved_conds)}", flush=True)

    rows = {}
    skipped_unresolved = skipped_noidx = 0
    for tok, cond in tok2cond.items():
        win = win_cache.get(cond)
        if win is None:
            skipped_unresolved += 1
            continue
        oidx = oidx_cache.get(tok)
        if oidx is None:
            skipped_noidx += 1
            continue
        yes_prices = [p if oidx == 0 else (1 - p) for p in fills[tok]]
        r = rows.setdefault(cond, {"p": [], "y": int(win == 0)})  # YES = outcome 0
        r["p"].extend(yes_prices)

    rows = {c: r for c, r in rows.items() if len(r["p"]) >= MIN_FILLS}
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    new = not os.path.exists(out_path)
    with open(out_path, "w", newline="") as f:
        import csv
        w = csv.writer(f)
        w.writerow(["condition_id", "category", "prob", "final_outcome", "n_fills", "source"])
        for cond, r in rows.items():
            w.writerow([cond, "onchain_unknown", round(statistics.median(r["p"]), 4),
                        r["y"], len(r["p"]), "goldsky"])
    print(f"\nwrote {len(rows)} resolved-market rows -> {out_path}", flush=True)
    print(f"  skipped: {skipped_unresolved} tokens in unresolved conditions, "
          f"{skipped_noidx} tokens with no on-chain outcome index (negRisk/multi-outcome)")
    yes_won = sum(1 for r in rows.values() if r["y"] == 1)
    print(f"  YES-won {yes_won}/{len(rows)}  ({100*yes_won/max(1,len(rows)):.0f}%)  "
          f"median prob {statistics.median([statistics.median(r['p']) for r in rows.values()]):.3f}")


def stats(st):
    fills = st["fills"]
    total = sum(len(v) for v in fills.values())
    print(f"\ngoldsky_fades: {len(fills)} unique tokens, {total} fills, "
          f"{st['pages_done']} pages, cursor "
          f"{time.strftime('%Y-%m-%d', time.gmtime(st['cursor_ts']))}, done={st.get('done')}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", type=int, default=200, help="1000-fill subgraph pages this run")
    ap.add_argument("--resolve", action="store_true", help="resolve accumulated fills -> CSV")
    ap.add_argument("--stats", action="store_true")
    ap.add_argument("--out", default=OUT_CSV)
    a = ap.parse_args()
    st = harvest(a.pages) if a.pages > 0 else load_state()
    if a.stats:
        stats(st)
    if a.resolve or a.pages > 0:
        resolve_and_write(st, a.out)
