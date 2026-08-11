#!/usr/bin/env python3
"""
ws_pricefeed.py — KEYLESS real-time Polymarket price feed.

Subscribes to the public CLOB market WebSocket (NO key, NO wallet, NO funds — it is
read-only market data) for the top-volume open markets, and maintains a real-time
mid/bid/ask cache the bot can read instead of (or alongside) the ~60s Gamma poll.

This is the SAFE answer to "feed real-time data to the bot": it carries zero trading
credentials. A relayer key / CLOB API key would authenticate REAL orders — those have
no place in a paper bot and do nothing for data anyway.

Design: pull top-N markets from Gamma (one call → token↔market-id map), open the WS,
collect book snapshots for a few seconds, write the cache keyed by MARKET ID (how the
bot thinks), repeat. Refreshes the market list periodically. Reconnects on drop.

Output: ws_prices.json  { "<market_id>": {yes_mid, bid, ask, ts}, "_meta": {...} }
Run as KeepAlive launchd daemon (com.aryan.polymarket-wsfeed).
"""
import json, os, time, threading, urllib.request
import websocket

DIR    = os.path.dirname(os.path.abspath(__file__))
OUT    = os.path.join(DIR, "ws_prices.json")
WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
GAMMA  = "https://gamma-api.polymarket.com"
UA     = {"User-Agent": "Mozilla/5.0"}
N_MARKETS    = int(os.environ.get("WS_N_MARKETS", "120"))
SNAP_SEC     = int(os.environ.get("WS_SNAP_SEC", "12"))   # collect window per cycle
REFRESH_EVERY = 15                                        # rebuild market list every N cycles


def get(u, t=15):
    try:
        return json.loads(urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=t).read())
    except Exception:
        return None


def build_token_map():
    """token_id -> (market_id, is_yes) for the top-volume open markets."""
    rows = get(f"{GAMMA}/markets?closed=false&order=volume24hr&ascending=false&limit={N_MARKETS}") or []
    tm = {}
    for m in rows:
        try:
            toks = json.loads(m.get("clobTokenIds") or "[]")
        except Exception:
            continue
        if len(toks) == 2 and m.get("id"):
            tm[str(toks[0])] = (str(m["id"]), True)
            tm[str(toks[1])] = (str(m["id"]), False)
    return tm


def mid_from_book(ev):
    bids, asks = ev.get("bids") or [], ev.get("asks") or []
    bb = max((float(b["price"]) for b in bids), default=None)
    ba = min((float(a["price"]) for a in asks), default=None)
    if bb is not None and ba is not None:
        return bb, ba, round((bb + ba) / 2, 4)
    return bb, ba, (bb if bb is not None else ba)


def collect_snapshot(tok_map, dur):
    """Open WS, subscribe to all tokens, gather book snapshots for `dur` seconds."""
    got = {}
    def on_open(ws):
        ws.send(json.dumps({"assets_ids": list(tok_map.keys()), "type": "market"}))
    def on_msg(ws, msg):
        try:
            data = json.loads(msg)
        except Exception:
            return
        for ev in (data if isinstance(data, list) else [data]):
            if isinstance(ev, dict) and ev.get("event_type") == "book":
                tid = str(ev.get("asset_id") or "")
                bb, ba, md = mid_from_book(ev)
                if md is not None:
                    got[tid] = (bb, ba, md)
    ws = websocket.WebSocketApp(WS_URL, on_open=on_open, on_message=on_msg)
    threading.Thread(target=ws.run_forever, daemon=True).start()
    time.sleep(dur)
    try: ws.close()
    except Exception: pass
    return got


def write_cache(prices, n_tokens):
    payload = {"_meta": {"ts": time.time(), "markets": len(prices), "tokens": n_tokens,
                         "source": "clob-ws", "keyless": True}}
    payload.update(prices)
    tmp = OUT + ".tmp"
    json.dump(payload, open(tmp, "w"))
    os.replace(tmp, OUT)


def main():
    cycle = 0
    tok_map = build_token_map()
    while True:
        if cycle % REFRESH_EVERY == 0:
            tm = build_token_map()
            if tm:
                tok_map = tm
        got = collect_snapshot(tok_map, SNAP_SEC)
        prices = {}
        # Normalize everything to YES side. Prefer the YES token; fall back to the NO
        # token (complement) only for markets the YES side didn't stream this cycle.
        # snapshot once: `got` is the LIVE dict the ws thread keeps writing, so iterating it
        # directly raced -> "dictionary changed size during iteration" crashed the feed (exit 1,
        # KeepAlive relaunched it, losing in-flight state). list() takes a stable point-in-time view.
        snap = list(got.items())
        for want_yes in (True, False):
            for tid, (bb, ba, md) in snap:
                ms = tok_map.get(tid)
                if not ms:
                    continue
                mkt_id, is_yes = ms
                if is_yes != want_yes or mkt_id in prices:
                    continue
                if is_yes:
                    y_bid, y_ask, y_mid = bb, ba, md
                else:                                  # NO token: YES = complement
                    y_bid = round(1 - ba, 4) if ba is not None else None
                    y_ask = round(1 - bb, 4) if bb is not None else None
                    y_mid = round(1 - md, 4)
                prices[mkt_id] = {"yes_mid": y_mid, "bid": y_bid, "ask": y_ask,
                                  "ts": round(time.time(), 1)}
        if prices:
            write_cache(prices, len(got))
        cycle += 1


if __name__ == "__main__":
    main()
