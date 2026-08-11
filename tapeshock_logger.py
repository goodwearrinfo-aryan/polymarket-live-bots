#!/usr/bin/env python3
"""tapeshock_logger.py — READ-ONLY logger (NOT a trading leg, places NO trades).

Question: when a single large taker print jerks a market's mid with NO
corroborating news, does it REVERT — and over what TIMESCALE? The tapeshock
trading leg was killed because a 60s loop can't outrun a seconds-fast snap-back.
But if shocks revert over HOURS, a slow version could work. This logger settles
the timescale empirically (it does not trade).

Mechanism: each cycle, pull the trades tape; find a large single print
(usdcSize>=MIN_USD) on a liquid market, in a news-quiet window (no hermes
headline matching the market). Snapshot the post-shock mid; forward-record the
mid at +30m/+2h/+6h and whether it moved BACK against the shock direction
(reversion) net of spread.

PAPER ONLY, read-only, stdlib. Usage: python3 tapeshock_logger.py once | report
"""
import os, sys, json, time, urllib.request, urllib.parse
import calendar

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "tapeshock_state.json")
LOG = os.path.join(HERE, "tapeshock_log.jsonl")
VAULT = os.path.expanduser("~/Documents/PolymarketVault/Reports/tapeshock.md")
HERMES = "http://127.0.0.1:48580/api/news"   # hermes real port = 48580 (NOT 48571), path /api/news (items[].title)
UA = {"User-Agent": "Mozilla/5.0"}

MIN_USD = 1500
WINDOWS_M = [30, 120, 360]       # +30m, +2h, +6h
FAST_BLOCK = ("up or down", "5m", "hourly")


def _g(url, timeout=12, headers=UA):
    return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout))


def ends_soon(cid, hours=8):
    """True if the market resolves within `hours` — skip, else resolution
    contaminates the reversion measure (the mid jumps to 0/1, not a snap-back)."""
    if not cid:
        return False
    try:
        m = _g("https://gamma-api.polymarket.com/markets?" + urllib.parse.urlencode({"condition_ids": cid}))
        end = (m[0].get("endDate") or "")[:19] if m else ""
        ts = calendar.timegm(time.strptime(end, "%Y-%m-%dT%H:%M:%S"))
        return (ts - time.time()) < hours * 3600
    except Exception:
        return False


def mid_of(token_id):
    try:
        bk = _g(f"https://clob.polymarket.com/book?token_id={token_id}")
        bids, asks = bk.get("bids") or [], bk.get("asks") or []
        bb = float(bids[-1]["price"]) if bids else None
        ba = float(asks[0]["price"]) if asks else None
        if bb is not None and ba is not None:
            return round((bb + ba) / 2, 4), round(ba - bb, 4)
    except Exception:
        pass
    return None, None


_news_terms = None
def news_quiet(title):
    """True if no recent hermes headline shares a salient word with the market title."""
    global _news_terms
    if _news_terms is None:
        try:
            d = _g(HERMES, timeout=6)
            items = d.get("items", []) if isinstance(d, dict) else (d if isinstance(d, list) else [])
            _news_terms = " ".join((it.get("title", "") if isinstance(it, dict) else str(it)) for it in items).lower()
        except Exception:
            _news_terms = ""                          # hermes down -> treat as quiet (fail open)
    words = [w for w in (title or "").lower().split() if len(w) >= 5]
    return not any(w in _news_terms for w in words)


def detect(st):
    try:
        trades = _g("https://data-api.polymarket.com/trades?limit=500")
    except Exception:
        return 0
    new = 0
    for t in trades:
        usd = float(t.get("size", 0)) * float(t.get("price", 0))
        if usd < MIN_USD:
            continue
        title = t.get("title") or ""
        if any(b in title.lower() for b in FAST_BLOCK):
            continue
        tok = t.get("asset"); cid = t.get("conditionId")
        key = t.get("transactionHash")
        if not tok or not key or key in st["pending"]:
            continue
        if not news_quiet(title):
            continue
        if ends_soon(cid):                             # skip same-day/near-resolution (no clean reversion)
            continue
        mid, spread = mid_of(tok)
        if mid is None:
            continue
        side = 1 if t.get("side") == "BUY" else -1     # shock direction on this token
        st["pending"][key] = {"t0": int(time.time()), "tok": tok, "cid": cid, "title": title,
                              "shock_mid": mid, "spread": spread, "side": side,
                              "usd": round(usd), "price": float(t.get("price", 0)), "marks": {}}
        new += 1
        print(f"  SHOCK ${round(usd)}  {t.get('side')} mid={mid:.3f}  {title[:42]}")
    return new


def revisit(st):
    done = 0
    for key, ev in list(st["pending"].items()):
        age_m = (time.time() - ev["t0"]) / 60.0
        due = [w for w in WINDOWS_M if w <= age_m and str(w) not in ev["marks"]]
        if due:
            mid, _ = mid_of(ev["tok"])
            if mid is not None:
                for w in due:
                    ev["marks"][str(w)] = mid
        if age_m >= WINDOWS_M[-1]:
            rec = dict(ev); rec["key"] = key
            with open(LOG, "a") as f:
                f.write(json.dumps(rec) + "\n")
            del st["pending"][key]; done += 1
    return done


def load_state():
    if os.path.exists(STATE):
        try:
            return json.load(open(STATE))
        except Exception:
            pass
    return {"pending": {}}


def save_state(st):
    tmp = STATE + ".tmp"; json.dump(st, open(tmp, "w")); os.replace(tmp, STATE)


def _read_log():
    return [json.loads(l) for l in open(LOG)] if os.path.exists(LOG) else []


def summarize():
    recs = _read_log()
    n = len(recs)
    if not n:
        return {"n": 0}
    rev = {w: [] for w in WINDOWS_M}
    for r in recs:
        for w in WINDOWS_M:
            m = r["marks"].get(str(w))
            if m is not None:
                # reversion = mid moved AGAINST the shock direction (net of spread)
                rev[w].append(-(m - r["shock_mid"]) * r["side"] - (r.get("spread") or 0))
    def avg(x): return round(100 * sum(x) / len(x), 2) if x else None
    return {"n": n, "reversion_c": {w: avg(rev[w]) for w in WINDOWS_M},
            "rev_positive_share": {w: (round(sum(1 for v in rev[w] if v > 0) / len(rev[w]), 2) if rev[w] else None) for w in WINDOWS_M}}


def export_obsidian(st):
    s = summarize()
    L = ["# Tapeshock Logger — liquidity-shock reversion timescale",
         "",
         "> READ-ONLY logger (no trades). After a lone large news-quiet print, does the mid",
         "> revert (net of spread), and over what timescale? Settles whether a SLOW version is viable.",
         f"> Updated {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())} · pending={len(st['pending'])} · matured={s.get('n',0)}",
         ""]
    if s.get("n"):
        L += ["| window | mean reversion (net) | % reverted |", "|---|---|---|"]
        for w in WINDOWS_M:
            r = s["reversion_c"][w]; p = s["rev_positive_share"][w]
            L.append(f"| +{w}m | {'' if r is None else f'{r:+.2f}¢'} | {'' if p is None else f'{int(p*100)}%'} |")
        L += ["", "**Read:** a slow leg is viable only if reversion is >0 NET of spread at a window "
              "a 60s loop can actually act on (+30m or later). If reversion is ~0 or negative, "
              "the shock was information, not noise — confirms the kill."]
    else:
        L += ["_No matured shocks yet (each needs 6h). Large news-quiet prints are rare — "
              "this logger gathers them passively._"]
    os.makedirs(os.path.dirname(VAULT), exist_ok=True)
    open(VAULT, "w").write("\n".join(L) + "\n")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "once"
    st = load_state()
    if cmd == "once":
        nd = revisit(st)
        nn = detect(st)
        save_state(st)
        export_obsidian(st)
        print(f"tapeshock: new_shocks={nn}  matured={nd}  pending={len(st['pending'])}")
    elif cmd == "report":
        export_obsidian(st)
        print(json.dumps(summarize(), indent=2))
    else:
        print("usage: tapeshock_logger.py once|report")


if __name__ == "__main__":
    main()
