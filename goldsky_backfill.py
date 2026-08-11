#!/usr/bin/env python3
"""goldsky_backfill.py — harvest V1-era Polymarket trader wallets from the live
Goldsky `orderbook-subgraph`, the deep history the RPC walk can't reach.

Why this exists: `onchain_wallet_backfill.py` (tenderly RPC) covers the V2 era
(2026-04-28 → now). The V1 CTF exchange's OrderFilled tape (2022-11-21 →
2026-04-28) was previously gated on a paid archive node. This subgraph serves
that whole era — maker/taker populated, 1000 fills/call, cursor-paginated, no
block limit, ~190 wallets/sec (5×+ the RPC). Verified 2026-06-13.

Cursor-walks BACKWARDS by timestamp from the V1 cutover. Resumable: cursor +
per-wallet fill counts persist in goldsky_wallets_state.json after every page.
Feeds the same whale_screen.py pipeline (which unions *_wallets_state.json).
PAPER research — read-only, no keys, no transactions.

Usage:
  python3 goldsky_backfill.py --pages 2000      # ~2M fills
  python3 goldsky_backfill.py --pages 0 --stats # just show what's accumulated
"""
import os, sys, json, time, argparse, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
URL = ("https://api.goldsky.com/api/public/project_cl6mb8i9h0003e201j6li0diw"
       "/subgraphs/orderbook-subgraph/prod/gn")
STATE_PATH = os.path.join(HERE, "goldsky_wallets_state.json")
CUTOVER_TS = 1782000000          # sentinel just past newest V1 fill (2026-04-28);
                                 # first query returns the true newest and walks back
PAGE = 1000                      # subgraph hard cap per query
SLEEP = float(os.environ.get("SLEEP", "0.6"))
EXCHANGES = {"0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e",
             "0xc5d563a36ae78145c45a50134d48a1215220f80a", "0x" + "0" * 40}

_Q = ("query($lt:BigInt!){orderFilledEvents(first:1000,orderBy:timestamp,"
      "orderDirection:desc,where:{timestamp_lt:$lt}){timestamp maker taker}}")


def gql(lt, tries=6):
    body = json.dumps({"query": _Q, "variables": {"lt": str(lt)}}).encode()
    for a in range(tries):
        try:
            req = urllib.request.Request(URL, data=body,
                    headers={"Content-Type": "application/json", "User-Agent": "poly-collect/1.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                j = json.load(r)
            if "errors" in j:
                raise RuntimeError(j["errors"][0].get("message", "gql error"))
            return j["data"]["orderFilledEvents"]
        except Exception as e:
            if a == tries - 1:
                raise
            time.sleep(3 * (a + 1))


def load_state():
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH) as f:
            return json.load(f)
    return {"cursor_ts": CUTOVER_TS, "wallets": {}, "pages_done": 0, "done": False}


def save_state(st):
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f)
    os.replace(tmp, STATE_PATH)


def harvest(pages):
    st = load_state()
    if st.get("done"):
        print("already walked V1 to genesis — nothing left. Delete state to redo.")
        return st
    wallets = st["wallets"]
    print(f"resuming: cursor_ts={st['cursor_ts']}  wallets={len(wallets)}  pages_done={st['pages_done']}")
    for i in range(pages):
        try:
            ev = gql(st["cursor_ts"])
        except Exception as e:
            print(f"  page at ts={st['cursor_ts']} failed ({e}) — state saved, rerun to resume")
            save_state(st)
            return st
        if not ev:
            st["done"] = True
            save_state(st)
            print("  reached oldest V1 fill (2022) — backfill COMPLETE.")
            return st
        for e in ev:
            for a in (e["maker"], e["taker"]):
                a = (a or "").lower()
                if not a or a in EXCHANGES:
                    continue
                wallets.setdefault(a, {"fills": 0})["fills"] += 1
        st["cursor_ts"] = int(ev[-1]["timestamp"])
        st["pages_done"] += 1
        save_state(st)
        if (i + 1) % 20 == 0 or i == pages - 1:
            d = time.strftime("%Y-%m-%d", time.gmtime(st["cursor_ts"]))
            print(f"  [{i+1}/{pages}] back to {d}  unique wallets={len(wallets)}")
        time.sleep(SLEEP)
    return st


def stats(st):
    ws = st["wallets"]
    print(f"\ngoldsky V1 pool: {len(ws)} wallets, {st['pages_done']} pages, "
          f"cursor at {time.strftime('%Y-%m-%d', time.gmtime(st['cursor_ts']))}, done={st.get('done')}")
    for th in (1, 3, 5, 10, 25):
        print(f"  fills>={th:>2}: {sum(1 for w in ws.values() if w['fills'] >= th)}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", type=int, default=500, help="1000-fill pages this run (0 = none)")
    ap.add_argument("--stats", action="store_true")
    a = ap.parse_args()
    st = harvest(a.pages) if a.pages > 0 else load_state()
    if a.stats or a.pages == 0:
        stats(st)
