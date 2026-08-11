#!/usr/bin/env python3
"""goldsky_sample.py — mine the DEEP V1 history (2022-11 → 2026-04) by SAMPLING
the timeline, not walking every fill.

Why: `goldsky_backfill.py` walks V1 fills sequentially, but the late-April-2026
pre-cutover region is so dense (~2M fills ≈ 9h chain time) that a sequential walk
never reaches 2022-2024. This instead drops an anchor at the 1st of each month
across V1's whole life and pulls N pages of fills around each anchor — broad
breadth coverage of the historical wallet population in bounded time.

Each anchor: cursor starts just after month-start, walk backwards PAGES_PER_ANCHOR
pages (1000 fills each). Accumulates into goldsky_sample_state.json (same
{"wallets":{addr:{fills}}} schema → feeds whale_screen.py's pool union).
Resumable at anchor granularity. PAPER research — read-only, no keys.

Usage:
  python3 goldsky_sample.py                      # default: 6 pages/month, all months
  python3 goldsky_sample.py --pages-per-anchor 10
  python3 goldsky_sample.py --stats
"""
import os, sys, json, time, argparse, calendar, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
URL = ("https://api.goldsky.com/api/public/project_cl6mb8i9h0003e201j6li0diw"
       "/subgraphs/orderbook-subgraph/prod/gn")
STATE_PATH = os.path.join(HERE, "goldsky_sample_wallets_state.json")  # *_wallets_state.json so whale_screen's pool-union glob picks it up
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


def month_anchors():
    """First-of-month UTC timestamps spanning V1 life (2022-11 .. 2026-04), newest first."""
    out = []
    for y in range(2022, 2027):
        for m in range(1, 13):
            if (y, m) < (2022, 11) or (y, m) > (2026, 4):
                continue
            out.append(calendar.timegm((y, m, 1, 0, 0, 0)))
    return sorted(out, reverse=True)


def load_state():
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH) as f:
            return json.load(f)
    return {"wallets": {}, "done_anchors": []}


def save_state(st):
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f)
    os.replace(tmp, STATE_PATH)


def run(pages_per_anchor):
    st = load_state()
    wallets = st["wallets"]
    done = set(st["done_anchors"])
    anchors = [a for a in month_anchors() if a not in done]
    print(f"sampling {len(anchors)} month-anchors × {pages_per_anchor} pages "
          f"(already done {len(done)}; wallets so far {len(wallets)})")
    for anc in anchors:
        label = time.strftime("%Y-%m", time.gmtime(anc))
        cursor = anc + 31 * 86400          # start just after month-start, walk back into the month
        got = 0
        for _ in range(pages_per_anchor):
            try:
                ev = gql(cursor)
            except Exception as e:
                print(f"  {label}: page failed ({e}) — saving, rerun to resume")
                save_state(st)
                return st
            if not ev:
                break
            for e in ev:
                for a in (e["maker"], e["taker"]):
                    a = (a or "").lower()
                    if a and a not in EXCHANGES:
                        wallets.setdefault(a, {"fills": 0})["fills"] += 1
            got += len(ev)
            cursor = int(ev[-1]["timestamp"])
            time.sleep(SLEEP)
        st["done_anchors"].append(anc)
        save_state(st)
        print(f"  {label}: +{got} fills  total wallets={len(wallets)}")
    print(f"\nDONE — sampled {len(st['done_anchors'])} months, {len(wallets)} unique V1 wallets")
    return st


def stats(st):
    ws = st["wallets"]
    print(f"\ngoldsky SAMPLE pool: {len(ws)} wallets across {len(st['done_anchors'])} months")
    for th in (1, 5, 10, 25, 50):
        print(f"  fills>={th:>2}: {sum(1 for w in ws.values() if w['fills'] >= th)}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages-per-anchor", type=int, default=6)
    ap.add_argument("--stats", action="store_true")
    a = ap.parse_args()
    if a.stats:
        stats(load_state())
    else:
        st = run(a.pages_per_anchor)
        stats(st)
