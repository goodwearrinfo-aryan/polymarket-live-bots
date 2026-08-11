#!/usr/bin/env python3
"""collect_onchain.py — build resolved-market FADE observations from ON-CHAIN
Polymarket fills (CTF Exchange `OrderFilled` events) joined to Gamma for the
resolution. This is the scale lever after CLOB /prices-history went dead (returns
0 points): on-chain has every fill ever, so it can manufacture thousands of
out-of-sample resolved fades. Stdlib only (urllib JSON-RPC). PAPER research.

Decode VERIFIED 2026-06-07: V2 path on recent blocks against a free public node;
V1 path (the historical bulk, pre-2026-04-28) needs an ARCHIVE rpc because free
public nodes PRUNE history ("History has been pruned"). Route by topic0.

RPC VERIFIED 2026-06-14: default RPC is now drpc.org (free, no key, serves archive
history). Free Alchemy is NOT usable here — its free tier caps eth_getLogs at a
10-block range and serves shallow archive. eth_getLogs is adaptive (get_logs()
bisects [from,to] on any provider's range-cap error), so WINDOW just sets the
starting span. rpc() now reads the HTTP-error BODY (the real reason lives there).

KNOWN LIMITATION 2026-06-14 (measured): the Gamma join degrades with age. Gamma's
?clob_token_ids= index drops older markets — token→market match rate fell from
6/8 at ~1 day old to 1/8 at ~14 days old in testing. Since the on-chain resolution
fallback (ctf_resolution) needs the conditionId FROM that Gamma match, deep-history
backfill yields ~0 resolved rows here. ==> Use this for RECENT bounded ranges only.
For the deep V1 era (2022-2026) bulk, use the GOLDSKY orderbook-subgraph backfill
(built 2026-06-13) — a subgraph indexes resolved markets without the token-id
staleness. A future fix would resolve token→condition→payout fully on-chain
(positionId encodes the condition), dropping the Gamma dependency entirely.

Output columns (prob, final_outcome, category) match ml/dataset.csv, so
fade_research.py / bot_backtest.py can consume the bigger file directly.

Usage:
  # validate decode+join on recent reachable blocks (drpc.org default, no key):
  python3 collect_onchain.py --selftest
  # bounded recent backfill (override RPC_URL only if you have a paid archive node):
  python3 collect_onchain.py --from-block 88400000 --to-block 88420000 --out ml/dataset_onchain.csv
"""
import os, sys, csv, json, time, statistics, argparse, urllib.request, urllib.parse, urllib.error

# drpc.org public endpoint: free, no key, serves ARCHIVE history (publicnode prunes
# it; Alchemy free caps eth_getLogs at 10 blocks). Probed 2026-06-14: 60-block
# getLogs range + 6-month-old blocks reachable. Override with RPC_URL env.
RPC_URL = os.environ.get("RPC_URL", "https://polygon.drpc.org")
UA = "poly-collect/1.0"            # publicnode 403s without a User-Agent — mandatory
GAMMA = "https://gamma-api.polymarket.com/markets"
WINDOW = int(os.environ.get("GETLOGS_WINDOW", "60"))   # per-call block span; adaptive
                                   # split kicks in below this if the provider caps lower

TOPIC_V1 = "0xd0a08e8c493f9c94f29311604c9de1b4e8c8d4c06bd0c789af57f2d65bfec0f6"
TOPIC_V2 = "0xd543adfd945773f1a62f74f0ee55a5e3b9b1a28262980ba90b1a89f2ea84d8ee"
SRC = [  # (label, address, topic0, version)
    ("v1-ctf",     "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E", TOPIC_V1, 1),
    ("v1-negrisk", "0xC5d563A36AE78145C45a50134d48A1215220f80a", TOPIC_V1, 1),
    ("v2-ctf",     "0xE111180000d2663C0091e4f400237545B87B996B", TOPIC_V2, 2),
    ("v2-negrisk", "0xe2222d279d744050d28e00520010520000310F59", TOPIC_V2, 2),
]


def rpc(method, params, retries=3):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    for a in range(retries + 1):
        try:
            req = urllib.request.Request(RPC_URL, data=body,
                                         headers={"Content-Type": "application/json", "User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as r:
                j = json.load(r)
            return {"_error": j["error"]} if "error" in j else j.get("result")
        except urllib.error.HTTPError as e:
            # Providers put the real reason (e.g. "up to a 10 block range") in the
            # response BODY, not the status line — surface it so get_logs can adapt.
            try:
                eb = e.read().decode()
                ej = json.loads(eb)
                err = ej.get("error", eb)
            except Exception:
                err = f"HTTP {e.code}"
            if e.code == 429 and a < retries:        # rate limited — back off and retry
                time.sleep(1.5 * (a + 1)); continue
            return {"_error": err}
        except Exception as e:
            if a < retries:
                time.sleep(1.0 * (a + 1)); continue
            return {"_error": str(e)}


def _is_range_cap(err):
    """True if the RPC rejected the call only because the block span is too wide
    (Alchemy 'up to a 10 block range', drpc 'ranges over N blocks not supported',
    Infura 'query returned more than', etc.). Retryable by splitting — unlike
    'pruned', which means there is genuinely no data to fetch at this height."""
    s = str(err).lower()
    return any(k in s for k in ("block range", "range should", "too large", "10 block",
                                "limit exceeded", "more than", "query returned more",
                                "not supported", "ranges over", "blocks are not",
                                "exceeds", "max is", "response size", "too many results"))


def get_logs(addr, topic, frm, to, depth=0):
    """eth_getLogs with adaptive bisection: if the provider caps the block range,
    split [frm,to] in half and recurse. Works on any tier. Returns:
      list  -> logs (possibly empty)
      {"_pruned": True} -> node pruned this history (caller should stop/skip)
      {"_error": ...}   -> other hard error after retries."""
    res = rpc("eth_getLogs", [{"address": addr, "topics": [topic],
                               "fromBlock": hex(frm), "toBlock": hex(to)}])
    if isinstance(res, dict) and "_error" in res:
        e = res["_error"]
        if "pruned" in str(e).lower():
            return {"_pruned": True}
        if _is_range_cap(e) and to > frm and depth < 24:
            mid = (frm + to) // 2
            left = get_logs(addr, topic, frm, mid, depth + 1)
            if isinstance(left, dict):
                return left                      # propagate pruned/error
            right = get_logs(addr, topic, mid + 1, to, depth + 1)
            if isinstance(right, dict):
                return right
            return left + right
        return {"_error": e}
    # Guarantee the success contract is a list. A well-formed node returns a list
    # of log objects; anything else (null, stray dict) becomes an empty list so
    # the caller never iterates dict keys as if they were log entries.
    return res if isinstance(res, list) else []


def _w(data, i):
    return int(data[2 + i * 64:2 + (i + 1) * 64], 16)


def decode(log):
    """OrderFilled log -> (positionId:int, price:float of THAT token) or None.
    price = USDC_leg_amount / token_leg_amount (decimal-invariant, in [0,1])."""
    d = log["data"]
    topic = log["topics"][0].lower()
    try:
        if topic == TOPIC_V1:   # data: makerAssetId, takerAssetId, makerAmt, takerAmt, fee
            mA, tA, mAmt, tAmt = _w(d, 0), _w(d, 1), _w(d, 2), _w(d, 3)
            if mA == 0 and tAmt > 0:   return tA, mAmt / tAmt   # bought tokens with USDC
            if tA == 0 and mAmt > 0:   return mA, tAmt / mAmt   # sold tokens for USDC
            return None
        else:                   # V2 data: side(uint8), tokenId, makerAmt, takerAmt, fee, builder, meta
            side, tok, mAmt, tAmt = _w(d, 0), _w(d, 1), _w(d, 2), _w(d, 3)
            if side == 0 and tAmt > 0: return tok, mAmt / tAmt  # maker buys tokens (USDC=maker leg)
            if side == 1 and mAmt > 0: return tok, tAmt / mAmt  # maker sells tokens (USDC=taker leg)
            return None
    except Exception:
        return None


_gcache = {}
def gamma_for_token(pid):
    if pid in _gcache:
        return _gcache[pid]
    u = GAMMA + "?" + urllib.parse.urlencode({"clob_token_ids": str(pid)})
    try:
        m = json.load(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": UA}), timeout=20))
        res = m[0] if m else None
    except Exception:
        res = None
    _gcache[pid] = res
    return res


def category_of(mk):
    blob = ((" ".join(mk.get("tags") or [])) + " " + (mk.get("question") or "")).lower()
    if any(k in blob for k in ("bitcoin", "ethereum", "crypto", "btc", "eth", "solana")): return "crypto"
    if any(k in blob for k in ("election", "president", "senate", "congress", "minister", "vote", "poll")): return "politics"
    if any(k in blob for k in ("nba", "nfl", "soccer", "tennis", "ufc", "mlb", "match", "vs.", "game")): return "sports"
    return "other"


def yes_outcome(mk):
    """Final YES (token0) outcome 1/0 from a RESOLVED market, else None.
    Falls back to the on-chain CTF resolution (ctf_resolution.py, read-only) when
    gamma is missing/lagging — fixes resolved markets gamma hasn't marked closed
    yet, a stale_nodata trigger. YES = token0 = on-chain outcome index 0."""
    op = mk.get("outcomePrices"); op = json.loads(op) if isinstance(op, str) else op
    if mk.get("closed") and op and len(op) == 2 and op[0] in ("0", "1") and op[1] in ("0", "1"):
        return int(op[0] == "1")
    cid = mk.get("conditionId")
    if cid:
        try:
            import ctf_resolution
            win = ctf_resolution.winning_outcome(cid)   # 0, 1, -1, or None
            if win in (0, 1):
                return 1 if win == 0 else 0             # index 0 = YES
        except Exception:
            pass
    return None


def collect(from_block, to_block, out_path):
    fills = {}                       # token_id(str) -> [fill prices of that token]
    b = from_block
    while b <= to_block:
        end = min(b + WINDOW, to_block)
        for label, addr, topic, ver in SRC:
            logs = get_logs(addr, topic, b, end)
            if isinstance(logs, dict):
                if logs.get("_pruned"):
                    print(f"  [block {b}] PRUNED history — set RPC_URL to a Polygon ARCHIVE node "
                          f"(drpc.org public works, no key) for historical backfill. Stopping.")
                    return
                continue   # other hard error on this source/window — skip, keep going
            for lg in (logs or []):
                dec = decode(lg)
                if dec and 0 < dec[1] < 1.0001:
                    fills.setdefault(str(dec[0]), []).append(min(dec[1], 1.0))
        print(f"  scanned {b}..{end}  unique tokens so far: {len(fills)}", flush=True)
        b = end + 1

    # Resolve every unique token via Gamma CONCURRENTLY — sequential lookups were
    # the bottleneck (hundreds of HTTP calls) that timed out long backfills before
    # the single end-of-run CSV write, losing all progress.
    from concurrent.futures import ThreadPoolExecutor
    pids = list(fills.keys())
    print(f"  resolving {len(pids)} unique tokens via Gamma (concurrent)…", flush=True)
    with ThreadPoolExecutor(max_workers=12) as ex:
        list(ex.map(gamma_for_token, pids))   # warms _gcache; aggregation below is instant

    rows = {}                        # conditionId -> aggregated row
    for pid, prices in fills.items():
        mk = gamma_for_token(pid)
        if not mk:
            continue
        yo = yes_outcome(mk)
        if yo is None:
            continue
        toks = mk.get("clobTokenIds"); toks = json.loads(toks) if isinstance(toks, str) else toks
        if not toks or str(pid) not in toks:
            continue
        idx = toks.index(str(pid))
        yprices = [p if idx == 0 else (1 - p) for p in prices]   # normalize to YES price
        cid = mk.get("conditionId") or mk.get("id")
        if cid in rows:
            rows[cid]["p"].extend(yprices)
        else:
            rows[cid] = {"p": yprices, "cat": category_of(mk), "y": yo}

    new = not os.path.exists(out_path)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["condition_id", "category", "prob", "final_outcome", "n_fills", "source"])
        for cid, r in rows.items():
            w.writerow([cid, r["cat"], round(statistics.median(r["p"]), 4), r["y"], len(r["p"]), "onchain"])
    print(f"\nwrote {len(rows)} resolved-market rows -> {out_path}  (median YES fill price/market)")
    print("Feed it to the fade backtest:  DATA override -> run fade_research/bot_backtest on this CSV.")


def selftest():
    print(f"RPC: {RPC_URL}")
    head = rpc("eth_blockNumber", [])
    if isinstance(head, dict):
        print("RPC error:", head); return
    h = int(head, 16)
    print(f"head block {h}. Validating V2 decode + Gamma join on recent blocks…")
    logs = get_logs(SRC[2][1], TOPIC_V2, h - 300, h)   # adaptive: auto-splits to the provider cap
    if isinstance(logs, dict):
        print("getLogs error:", logs); return
    print(f"  {len(logs)} V2 OrderFilled fills in 300 blocks")
    shown = 0
    for lg in logs:
        dec = decode(lg)
        if not dec or not (0 < dec[1] < 1.0001):
            continue
        mk = gamma_for_token(dec[0])
        tag = (f"-> '{(mk.get('question') or '')[:34]}' closed={mk.get('closed')} "
               f"outcome={mk.get('outcomePrices')}") if mk else "(no gamma match)"
        print(f"  price={dec[1]:.3f} token={str(dec[0])[:16]}… {tag}")
        shown += 1
        if shown >= 4:
            break
    print("decode + Gamma join OK ✓")
    print("Default RPC (drpc.org) serves archive — but Gamma drops old markets from its")
    print("token-id index (see docstring): use this for RECENT ranges; Goldsky subgraph for deep V1 bulk.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--from-block", type=int)
    ap.add_argument("--to-block", type=int)
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "ml", "dataset_onchain.csv"))
    a = ap.parse_args()
    if a.selftest or not (a.from_block and a.to_block):
        selftest()
    else:
        print(f"collecting OrderFilled {a.from_block}..{a.to_block} via {RPC_URL}")
        collect(a.from_block, a.to_block, a.out)
