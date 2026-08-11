#!/usr/bin/env python3
"""
polymarket_data.py — client for Polymarket's Data API (https://data-api.polymarket.com)
plus a local SQLite analytics store.

Public, no auth. Endpoints (all capped ~500/req):
  /positions?user=<addr>
  /trades?user=<addr> | ?market=<conditionId>
  /activity?user=<addr>
  /holders?market=<conditionId>
  /value?user=<addr>

Design: fail-soft (errors return [] / {} / None, never raise), stdlib only
(urllib + sqlite3 + json). The analytics store keeps the RAW JSON of every
snapshot keyed by (endpoint, key, ts) so analysis isn't blocked on knowing the
exact field names — we can parse later once shapes are confirmed.

CLI:
  python3 polymarket_data.py test                 # sample each endpoint, print
  python3 polymarket_data.py wallet <address>     # dump a wallet's positions/value/trades
  python3 polymarket_data.py snapshot             # store holders(held mkts)+recent trades to analytics.db
  python3 polymarket_data.py stats                # summarize what's in analytics.db
"""
import os, sys, json, time, sqlite3, urllib.parse, urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_API = "https://data-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
DB_PATH = os.path.join(BASE, "analytics.db")
TRADE_LOG = os.path.join(BASE, "trade_log.json")
_TIMEOUT = 8
_UA = "polymarket-bot-data/1.0"


# ── HTTP (fail-soft) ────────────────────────────────────────────────────────
def _get_url(url, params=None):
    if params:
        url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception as e:
        sys.stderr.write(f"[http] {url[:60]} failed: {e}\n")
        return None


def _get(path, params=None):
    return _get_url(f"{DATA_API}{path}", params)


def book_metrics(token_id, depth_levels=5):
    """Live order-book microstructure for a token from the CLOB:
    best bid/ask, mid, spread, and depth imbalance over the top N levels.
    imbalance in [-1,1]: +1 = all bid-side (buy pressure), -1 = all ask-side.
    Fail-soft -> None."""
    b = _get_url(f"{CLOB}/book", {"token_id": token_id})
    if not b or not isinstance(b, dict):
        return None
    bids = b.get("bids") or []
    asks = b.get("asks") or []
    if not bids or not asks:
        return None
    try:
        best_bid = max(float(x["price"]) for x in bids)
        best_ask = min(float(x["price"]) for x in asks)
        bid_depth = sum(float(x["size"]) for x in bids[:depth_levels])
        ask_depth = sum(float(x["size"]) for x in asks[:depth_levels])
        tot = bid_depth + ask_depth
        return {
            "best_bid": round(best_bid, 4),
            "best_ask": round(best_ask, 4),
            "mid": round((best_bid + best_ask) / 2, 4),
            "spread": round(best_ask - best_bid, 4),
            "bid_depth": round(bid_depth, 2),
            "ask_depth": round(ask_depth, 2),
            "imbalance": round((bid_depth - ask_depth) / tot, 4) if tot else 0.0,
        }
    except Exception:
        return None


class DataAPI:
    def positions(self, user, limit=500):
        return _get("/positions", {"user": user, "limit": limit}) or []

    def trades(self, user=None, market=None, limit=500):
        return _get("/trades", {"user": user, "market": market, "limit": limit}) or []

    def activity(self, user, limit=500):
        return _get("/activity", {"user": user, "limit": limit}) or []

    def holders(self, condition_id, limit=100):
        return _get("/holders", {"market": condition_id, "limit": limit}) or []

    def value(self, user):
        return _get("/value", {"user": user}) or {}


# ── analytics store (raw snapshots) ─────────────────────────────────────────
def _db():
    con = sqlite3.connect(DB_PATH)
    con.execute("""CREATE TABLE IF NOT EXISTS snapshots(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts INTEGER NOT NULL,
        endpoint TEXT NOT NULL,
        key TEXT,
        n INTEGER,
        payload TEXT NOT NULL)""")
    con.execute("CREATE INDEX IF NOT EXISTS ix_snap ON snapshots(endpoint, ts)")
    return con


def store(endpoint, key, payload):
    try:
        con = _db()
        n = len(payload) if isinstance(payload, (list, tuple)) else 1
        con.execute("INSERT INTO snapshots(ts, endpoint, key, n, payload) VALUES (?,?,?,?,?)",
                    (int(time.time()), endpoint, key, n, json.dumps(payload)))
        con.commit()
        con.close()
        return n
    except Exception as e:
        sys.stderr.write(f"[store] failed: {e}\n")
        return 0


def held_condition_ids():
    try:
        with open(TRADE_LOG) as f:
            d = json.load(f)
        cids = {t.get("condition_id") for t in d.get("open", []) if t.get("condition_id")}
        return sorted(cids)
    except Exception:
        return []


def held_tokens():
    """[(token_id, condition_id, title)] for open positions — for order-book snapshots."""
    try:
        with open(TRADE_LOG) as f:
            d = json.load(f)
        out = []
        for t in d.get("open", []):
            tid = t.get("token_id")
            if tid:
                out.append((str(tid), t.get("condition_id"), t.get("title", "")))
        return out
    except Exception:
        return []


# ── high-level actions ───────────────────────────────────────────────────────
def snapshot(max_markets=60):
    """Store top holders for each held market + recent global trades."""
    api = DataAPI()
    cids = held_condition_ids()[:max_markets]
    total_h = 0
    for cid in cids:
        h = api.holders(cid)
        if h:
            total_h += store("holders", cid, h)
        time.sleep(0.15)  # be gentle on the API
    tr = api.trades(limit=500)
    n_tr = store("trades", "recent", tr) if tr else 0

    # Order-book microstructure (spread + imbalance) for held tokens — builds a
    # forward dataset of features that historical price-only data can't provide.
    n_book = 0
    for tid, cid, title in held_tokens()[:max_markets]:
        bm = book_metrics(tid)
        if bm:
            bm["condition_id"] = cid
            bm["title"] = title
            n_book += store("book", tid, bm)
        time.sleep(0.15)
    print(f"snapshot: holders {len(cids)} markets ({total_h} rows), {n_tr} trades, "
          f"{n_book} order-books -> {DB_PATH}")


def wallet(addr):
    api = DataAPI()
    pos = api.positions(addr)
    val = api.value(addr)
    tr = api.trades(user=addr, limit=50)
    store("positions", addr, pos)
    store("value", addr, val)
    print(f"\nWallet {addr}")
    print(f"  value: {json.dumps(val)[:200]}")
    print(f"  positions: {len(pos)}  | recent trades: {len(tr)}")
    for p in (pos[:8] if isinstance(pos, list) else []):
        title = str(p.get('title', '?'))[:48]
        print(f"   • {title:48s} sz={p.get('size')} avg={p.get('avgPrice')} "
              f"cur={p.get('curPrice')} pnl={p.get('cashPnl')}")


def test():
    api = DataAPI()
    cids = held_condition_ids()
    print("Held condition_ids:", len(cids))
    print("\n/trades (limit 2):")
    print(json.dumps(api.trades(limit=2), indent=2)[:1200])
    if cids:
        print(f"\n/holders for {cids[0]} (limit 2):")
        print(json.dumps(api.holders(cids[0], limit=2), indent=2)[:900])
    toks = held_tokens()
    if toks:
        raw = _get_url(f"{CLOB}/book", {"token_id": toks[0][0]})
        print(f"\nraw /book response keys: {list(raw.keys()) if isinstance(raw, dict) else type(raw)}")
        print(json.dumps(raw, indent=2)[:700] if raw else "  (empty/none)")
        for tid, cid, title in toks[:8]:
            bm = book_metrics(tid)
            if bm:
                print(f"\nbook_metrics OK — {title[:40]}: {json.dumps(bm)}")
                break
        else:
            print("\nno live book among first 8 held tokens (likely resolved/illiquid markets)")


def stats():
    con = _db()
    rows = con.execute("SELECT endpoint, COUNT(*), SUM(n), MIN(ts), MAX(ts) "
                       "FROM snapshots GROUP BY endpoint").fetchall()
    print(f"analytics.db -> {DB_PATH}")
    for ep, c, n, mn, mx in rows:
        span = (mx - mn) / 3600 if mn and mx else 0
        print(f"  {ep:10s}: {c} snapshots, {n} rows, spanning {span:.1f}h")
    con.close()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "test"
    if cmd == "test":
        test()
    elif cmd == "snapshot":
        snapshot()
    elif cmd == "wallet" and len(sys.argv) > 2:
        wallet(sys.argv[2])
    elif cmd == "stats":
        stats()
    else:
        print(__doc__)
