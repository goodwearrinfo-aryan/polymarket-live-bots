#!/usr/bin/env python3
"""
ws_basket_watch.py — REAL-TIME basket-arb lock detector (CLOB websocket).

Sub-second successor to basket_watch.py's 60s poll. Subscribes to the KEYLESS
public CLOB market socket for the legs of every basket near the lock boundary,
tracks each leg's best ask live, and the instant Σask drops below $1 on an
exhaustive (complete) field it ALERTS (WhatsApp + iMessage) and records to the
brain.

Protocol (verified empirically 2026-06-15):
  wss://ws-subscriptions-clob.polymarket.com/ws/market ; subscribe
  {"assets_ids":[...],"type":"market"}. 'book' carries full asks (best=min
  price); 'price_change' carries per-asset best_bid/best_ask directly.

Scope: only baskets that are EXHAUSTIVE and within WATCH_CEIL of locking (a tick
away from flipping) — capped at MAX_TOKENS so the socket stays tractable. LONG
locks (buy all YES, Σask<1) only; SHORT locks are rarer and the 60s poller
(basket_watch.py) still backstops them. Shares basket_watch_state.json so the
two never double-alert the same lock.

PAPER / DETECTION ONLY. Never places an order or moves money (hard rule + the
paper-guard hook). It tells you where the edge is the instant it appears; acting
on it — and eating depth/completeness/capital-lock risk — is your manual call.
Honest: even sub-second, genuinely-free locks are taken by faster HFT bots; what
survives carries friction.

Run as a KeepAlive launchd daemon (com.aryan.ws-basket-watch).
"""
import json, os, time, threading
from pathlib import Path

import websocket
import basket_arb
try:
    import wa_alert
except Exception:
    wa_alert = None

WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
BASE = Path(__file__).resolve().parent
STATE = BASE / "basket_watch_state.json"     # SHARED with the 60s poller (dedup)
LOG = BASE / "basket_locks.jsonl"
BRAIN = Path(os.path.expanduser("~/Documents/PolymarketVault")) / "brain" / "Resources"

WATCH_CEIL = 1.02       # only watch baskets within 2¢ of locking
MAX_TOKENS = 600        # keep the subscription tractable
ALERT_EDGE = 0.02       # alert on locks >= 2%
REALERT_JUMP = 0.02     # re-alert if a known lock's edge climbs this much
EXPIRE_SEC = 7200       # a lock unseen 2h is fresh again if it returns
REFRESH_SEC = 600       # rebuild the basket universe every 10 min

books = {}              # token -> best ask (float)
tok2basket = {}         # token -> basket slug
baskets = {}            # slug -> {title, n, tokens:[...]}
lock_state = {}         # slug -> {edge, seen} (mirrors STATE, shared with poller)
_lock = threading.Lock()


def load_seen():
    if STATE.exists():
        try:
            return json.loads(STATE.read_text())
        except Exception:
            pass
    return {}


def build_universe():
    """Closest-to-locking exhaustive baskets, capped at MAX_TOKENS."""
    cand = [x for x in basket_arb.scan_baskets(pages=10)
            if x["exhaustive"] and x["sum_ask"] < WATCH_CEIL]
    cand.sort(key=lambda x: x["sum_ask"])     # nearest the boundary first
    bk, t2b = {}, {}
    n_tok = 0
    for x in cand:
        toks = [str(L["yes_token"]) for L in x["legs"] if L.get("yes_token")]
        if not toks or n_tok + len(toks) > MAX_TOKENS:
            continue
        bk[x["slug"]] = {"title": x["title"], "n": x["n_outcomes"], "tokens": toks}
        for t in toks:
            t2b[t] = x["slug"]
        n_tok += len(toks)
    return bk, t2b


def recompute(slug):
    info = baskets.get(slug)
    if not info:
        return
    asks = [books.get(t) for t in info["tokens"]]
    if any(a is None for a in asks):
        return                                # wait until every leg has streamed
    s_ask = sum(asks)
    edge = round(1.0 - s_ask, 4)
    if edge >= ALERT_EDGE:
        maybe_alert(slug, info, edge, s_ask)


def _email(text):
    """Headless alert sink for a Mac-less VPS — Gmail SMTP via .smtp_creds.json."""
    creds = BASE / ".smtp_creds.json"
    if not creds.exists():
        return False
    try:
        import smtplib, ssl
        from email.message import EmailMessage
        c = json.loads(creds.read_text())
        m = EmailMessage()
        m["From"] = c["user"]
        m["To"] = c.get("to", c["user"])
        m["Subject"] = "⚡ Polymarket basket lock"
        m.set_content(text)
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context()) as s:
            s.login(c["user"], c.get("pass") or c.get("password"))
            s.send_message(m)
        return True
    except Exception as e:
        print(f"  email alert failed: {e}")
        return False


def _notify(text):
    """Portable: Mac WhatsApp+iMessage if present, else email (headless VPS), else log."""
    if wa_alert:
        try:
            wa_alert.notify(text)
            return
        except Exception as e:
            print(f"  wa_alert failed: {e}")
    if _email(text):
        return
    print("  [alert] " + text)


def maybe_alert(slug, info, edge, s_ask):
    now = time.time()
    with _lock:
        prev = lock_state.get(slug)
        fresh = prev is None or (now - prev.get("seen", 0)) > EXPIRE_SEC
        jumped = bool(prev) and (edge - prev.get("edge", 0)) >= REALERT_JUMP
        lock_state[slug] = {"edge": edge, "seen": now}
        try:
            STATE.write_text(json.dumps(lock_state, indent=2))
        except Exception:
            pass
    if not (fresh or jumped):
        return
    tag = "NEW" if fresh else "EDGE↑"
    txt = (f"⚡ LIVE basket lock {tag}: {edge*100:.1f}% — {info['title'][:60]} "
           f"(buy {info['n']} legs @ Σask {s_ask:.3f}). Real-time. Paper/detection only.")
    _notify(txt)
    try:
        with open(LOG, "a") as f:
            f.write(json.dumps({"ts": now, "event": "ws_" + ("new" if fresh else "jump"),
                                "slug": slug, "edge": edge, "kind": "LONG",  # ws-watcher only detects LONG locks (sum_ask<ceil)
                                "sum_ask": round(s_ask, 4),
                                "n": info["n"]}) + "\n")
    except Exception:
        pass
    print(f"  {tag} {edge:+.3f} {info['title'][:50]}")


def on_message(ws, msg):
    try:
        data = json.loads(msg)
    except Exception:
        return
    touched = set()
    for ev in (data if isinstance(data, list) else [data]):
        if not isinstance(ev, dict):
            continue
        et = ev.get("event_type")
        if et == "book":
            tid = str(ev.get("asset_id") or "")
            if tid in tok2basket:
                prices = [float(a["price"]) for a in (ev.get("asks") or [])]
                if prices:
                    books[tid] = min(prices)
                    touched.add(tok2basket[tid])
        elif et == "price_change":
            for pc in ev.get("price_changes") or []:
                tid = str(pc.get("asset_id") or "")
                if tid in tok2basket and pc.get("best_ask") not in (None, ""):
                    books[tid] = float(pc["best_ask"])
                    touched.add(tok2basket[tid])
    for slug in touched:
        recompute(slug)


def on_open(ws):
    ws.send(json.dumps({"assets_ids": list(tok2basket.keys()), "type": "market"}))
    print(f"[ws] subscribed {len(tok2basket)} tokens / {len(baskets)} baskets", flush=True)


HEARTBEAT_SEC = 90


def heartbeat():
    """Periodic proof-of-life so the log never goes silently dark between events."""
    while True:
        time.sleep(HEARTBEAT_SEC)
        try:
            covered = best = 0
            best_t = ""
            for s, i in baskets.items():
                aks = [books.get(t) for t in i["tokens"]]
                if all(a is not None for a in aks):
                    covered += 1
                    e = 1.0 - sum(aks)
                    if e > best:
                        best, best_t = e, i["title"][:30]
            print(f"[hb] alive | {covered}/{len(baskets)} baskets covered | "
                  f"best live edge {best:+.3f} {best_t}", flush=True)
        except Exception as e:
            print(f"[hb] err {e}", flush=True)


def main():
    global baskets, tok2basket, books, lock_state
    lock_state = load_seen()
    threading.Thread(target=heartbeat, daemon=True).start()
    while True:                                    # rebuild + reconnect cycle
        baskets, tok2basket = build_universe()
        books = {}
        if not tok2basket:
            print("[cycle] no watch-worthy baskets; sleeping")
            time.sleep(REFRESH_SEC)
            continue
        print(f"[cycle] watching {len(baskets)} baskets, {len(tok2basket)} tokens")
        ws = websocket.WebSocketApp(WS_URL, on_open=on_open, on_message=on_message)
        th = threading.Thread(
            target=lambda: ws.run_forever(ping_interval=20, ping_timeout=10), daemon=True)
        th.start()
        time.sleep(REFRESH_SEC)                     # run this connection, then refresh universe
        try:
            ws.close()
        except Exception:
            pass
        th.join(timeout=5)


if __name__ == "__main__":
    main()
