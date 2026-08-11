#!/usr/bin/env python3
"""onchain_wallet_backfill.py — harvest historical Polymarket trader wallets from
on-chain OrderFilled events, past the data-api's ~700-wallet recent-trades ceiling.

Empirically verified 2026-06-12 (receipts of live data-api trades):
  - fills emit OrderFilled on the V2 exchanges 0xe111...996b (CTF) and
    0xe2222...0f59 (NegRisk), topic0 0xd543adfd... ; the legacy V1 exchanges
    (0x4bfb..., 0xc5d5...) used topic0 0xd0a08e8c... before the 2026-04-28 cutover.
  - topics layout = [sig, orderHash, maker, taker] — trader addresses come from
    topics[2]/[3]; no data decoding needed. maker is the user's proxy wallet
    (same address space data-api /positions understands).
  - https://1rpc.io/matic is free but caps eth_getLogs at 50 blocks/call and
    429s aggressively — sleep between calls, back off hard on 429.

Walks history BACKWARDS from chain head in 50-block pages, one getLogs per page
(all 4 exchange addresses + both topic0s OR'd in a single filter). Resumable:
cursor + accumulated wallet stats persist in onchain_wallets_state.json after
every page. PAPER research — read-only chain access, no keys, no transactions.

Usage:
  python3 onchain_wallet_backfill.py --pages 500          # harvest ~25k blocks
  python3 onchain_wallet_backfill.py --pages 0 --flag     # just flag what we have
  python3 onchain_wallet_backfill.py --pages 500 --flag   # harvest then flag
"""
import os, sys, json, time, argparse, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from wallet_scanner import flag_wallet  # SHARP = WR>=55% & PnL>0, or PnL>=$500 & WR>=15%

RPC_URL    = os.environ.get("RPC_URL", "https://1rpc.io/matic")
PAGE       = int(os.environ.get("PAGE", "50"))    # 1rpc.io caps at 50; publicnode takes 2000
SLEEP      = float(os.environ.get("SLEEP", "2.5"))
TIMEOUT    = int(os.environ.get("TIMEOUT", "30"))  # big pages (1000 blocks ~ 126k logs) need ~90
STATE_PATH = os.path.join(HERE, "onchain_wallets_state.json")
SHARP_OUT  = os.path.join(HERE, "onchain_sharp_candidates.json")
WATCHLIST  = os.path.join(HERE, "insider_wallets.json")
UA = {"User-Agent": "poly-collect/1.0", "Content-Type": "application/json"}

TOPIC_V1 = "0xd0a08e8c493f9c94f29311604c9de1b4e8c8d4c06bd0c789af57f2d65bfec0f6"
TOPIC_V2 = "0xd543adfd945773f1a62f74f0ee55a5e3b9b1a28262980ba90b1a89f2ea84d8ee"
EXCHANGES = [
    "0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e",   # v1 CTF
    "0xc5d563a36ae78145c45a50134d48a1215220f80a",   # v1 NegRisk
    "0xe111180000d2663c0091e4f400237545b87b996b",   # v2 CTF
    "0xe2222d279d744050d28e00520010520000310f59",   # v2 NegRisk
]
SKIP = set(EXCHANGES) | {"0x" + "0" * 40}            # not trader wallets


def rpc(method, params, tries=8):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    for a in range(tries):
        try:
            req = urllib.request.Request(RPC_URL, data=body, headers=UA)
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                j = json.load(r)
            if "error" in j:
                raise RuntimeError(j["error"])
            return j["result"]
        except urllib.error.HTTPError as e:
            if e.code == 429 and a < tries - 1:
                time.sleep(5 * (a + 1)); continue
            raise
        except Exception:
            if a < tries - 1:
                time.sleep(3 * (a + 1)); continue
            raise


def load_state():
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH) as f:
            return json.load(f)
    return {"head": None, "cursor_low": None, "wallets": {}, "pages_done": 0}


def save_state(st):
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f)
    os.replace(tmp, STATE_PATH)


def topic_addr(t):
    return "0x" + t[-40:].lower()


def harvest(pages):
    st = load_state()
    if st["head"] is None:
        st["head"] = int(rpc("eth_blockNumber", []), 16)
        st["cursor_low"] = st["head"] + 1
        print(f"fresh start: head block {st['head']}")
        time.sleep(SLEEP)
    wallets = st["wallets"]
    print(f"resuming: cursor_low={st['cursor_low']}  wallets so far={len(wallets)}  pages_done={st['pages_done']}")
    for i in range(pages):
        hi = st["cursor_low"] - 1
        lo = hi - PAGE + 1
        if hi < 1:
            print("reached genesis-ish; nothing left to scan"); break
        try:
            logs = rpc("eth_getLogs", [{"address": EXCHANGES,
                                        "topics": [[TOPIC_V1, TOPIC_V2]],
                                        "fromBlock": hex(lo), "toBlock": hex(hi)}])
        except Exception as e:
            print(f"  page {lo}..{hi} failed ({e}) — state saved, rerun to resume")
            save_state(st)
            return st
        for lg in logs or []:
            for t in lg["topics"][2:4]:
                a = topic_addr(t)
                if a in SKIP:
                    continue
                w = wallets.setdefault(a, {"fills": 0, "lo": lo, "hi": hi})
                w["fills"] += 1
                w["lo"] = min(w["lo"], lo)
        st["cursor_low"] = lo
        st["pages_done"] += 1
        save_state(st)
        if (i + 1) % 10 == 0 or i == pages - 1:
            print(f"  [{i+1}/{pages}] scanned down to block {lo}  fills-page={len(logs or [])}  unique wallets={len(wallets)}")
        time.sleep(SLEEP)
    return st


def flag_all(st, min_positions=5, min_fills=1, workers=15):
    known = {}
    if os.path.exists(WATCHLIST):
        with open(WATCHLIST) as f:
            known = {k.lower() for k in json.load(f)}
    prev = {}
    if os.path.exists(SHARP_OUT):
        with open(SHARP_OUT) as f:
            prev = json.load(f)
    cands = [a for a, w in st["wallets"].items() if w["fills"] >= min_fills and a not in known]
    print(f"\nflagging {len(cands)} harvested wallets (min {min_positions} closed positions) via data-api…")
    # Recompute fresh every run — do NOT seed with `prev` (stale entries that are
    # no longer SHARP would persist; 2026-06-21: 0x3139 kept at a stale
    # 55.6%WR/$378 while live = 22%/$17.84). `prev` retained only for the diff count.
    sharps, done, skipped_cf = {}, 0, 0
    now = int(time.time())
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(flag_wallet, a, min_positions): a for a in cands}
        for f in as_completed(futs):
            done += 1
            r = f.result()
            if r and r["flag"] == "SHARP":
                # Exclude coinflip-HFT: >50% of closed positions in 5-min crypto
                # Up/Down markets = uncopyable at any lag, never a real sharp.
                if r.get("coinflip_share", 0) > 0.5:
                    skipped_cf += 1
                    print(f"  ⚪ SKIP coinflip-HFT {r['addr']}  coinflip={r['coinflip_share']:.0%}  WR={r['win_rate']}%")
                    continue
                sharps[r["addr"]] = {"win_rate": r["win_rate"], "realized_pnl": r["realized_pnl"],
                                     "closed": r["closed"], "avg_bet": r["avg_bet"],
                                     "onchain_fills": st["wallets"][r["addr"]]["fills"],
                                     "coinflip_share": r.get("coinflip_share", 0), "flagged_at": now}
                print(f"  🟢 SHARP {r['addr']}  WR={r['win_rate']}%  PnL=${r['realized_pnl']:,.2f}  closed={r['closed']}")
            if done % 100 == 0:
                print(f"  …{done}/{len(cands)} checked, {len(sharps)} SHARP, {skipped_cf} coinflip-skipped")
    with open(SHARP_OUT, "w") as f:
        json.dump(sharps, f, indent=2)
    print(f"\n{len(sharps)} SHARP candidates (fresh, coinflip-HFT filtered, {skipped_cf} skipped) -> {SHARP_OUT}")
    print("Review them, then add keepers to insider_wallets.json for the SHARP watcher.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", type=int, default=200, help="50-block pages to scan this run (0 = skip harvest)")
    ap.add_argument("--flag", action="store_true", help="run accumulated wallets through SHARP flagging")
    ap.add_argument("--min-fills", type=int, default=1, help="min on-chain fills before flagging a wallet")
    ap.add_argument("--min-positions", type=int, default=5, help="min closed positions to qualify for a flag")
    a = ap.parse_args()
    st = harvest(a.pages) if a.pages > 0 else load_state()
    if a.flag:
        flag_all(st, a.min_positions, a.min_fills)
