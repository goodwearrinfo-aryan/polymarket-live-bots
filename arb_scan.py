#!/usr/bin/env python3
"""arb_scan.py — live risk-free arbitrage scanner (PolyTerm-style). PAPER / read-only.

A binary market's YES and NO tokens together pay exactly $1 at resolution. So if you
can BUY both asks for < $1 (YES_ask + NO_ask < 1), you lock a guaranteed profit no
matter the outcome. Unlike the fade's *statistical* edge, this is a DETERMINISTIC
mispricing — it either exists or it doesn't, and it can't fake-profit. This just
surfaces the cheapest pairs for a human; it places no orders.

Caveats it prints: gross of fees, and assumes both asks are fillable at size — a 0.5c
"edge" on 10 shares of depth is noise. Real arb on a liquid venue is rare and fleeting.
"""
import sys, json, time, urllib.request, urllib.parse

GAMMA = "https://gamma-api.polymarket.com/markets"
CLOB = "https://clob.polymarket.com"
UA = "arb-scan/1.0"; TIMEOUT = 10
MIN_EDGE = 0.01          # require >=1c gross before it's worth a second look
PACE = 0.2; _last = [0.0]


def http_get(url, params=None, retries=2):
    if params:
        url += "?" + urllib.parse.urlencode(params)
    for a in range(retries + 1):
        w = PACE - (time.time() - _last[0])
        if w > 0:
            time.sleep(w)
        _last[0] = time.time()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return json.loads(r.read())
        except Exception as e:
            if a < retries and getattr(e, "code", None) == 429:
                time.sleep(1.0 * (a + 1)); continue
            return None
    return None


def ask(token):
    """Cost to BUY this outcome right now (the ask)."""
    d = http_get(f"{CLOB}/price", {"token_id": token, "side": "buy"})
    try:
        return float(d["price"]) if isinstance(d, dict) and d.get("price") is not None else None
    except Exception:
        return None


def fetch_markets(pages=4, per=100):
    out = []
    for p in range(pages):
        raw = http_get(GAMMA, {"closed": "false", "active": "true", "order": "volumeNum",
                               "ascending": "false", "limit": per, "offset": p * per}) or []
        if not raw:
            break
        for m in raw:
            toks = m.get("clobTokenIds")
            toks = json.loads(toks) if isinstance(toks, str) else toks
            if toks and len(toks) == 2:
                out.append({"q": (m.get("question") or "")[:60], "yes": toks[0], "no": toks[1],
                            "vol": float(m.get("volumeNum") or 0)})
        if len(raw) < per:
            break
    return out


def main():
    mkts = fetch_markets()
    print(f"ARB SCAN — {len(mkts)} live binary markets · risk-free if YES_ask + NO_ask < 1\n")
    hits = []
    for m in mkts:
        try:
            ya, na = ask(m["yes"]), ask(m["no"])
            if ya is None or na is None:
                continue
            edge = 1.0 - (ya + na)
            if edge >= MIN_EDGE:
                hits.append((edge, ya + na, m))
        except Exception:
            continue   # one bad market never kills the scan
    hits.sort(key=lambda x: x[0], reverse=True)   # by edge only — never compare dicts
    if not hits:
        print(f"No risk-free arb found (>= {MIN_EDGE*100:.0f}% gross). Expected on a liquid venue —")
        print("these get arbed away in seconds. Absence of arb = the book is efficient, which is healthy.")
        return
    print(f"{'edge':>6} {'YES+NO':>7}  market")
    for edge, cost, m in hits[:15]:
        print(f"{edge*100:>5.1f}% {cost:>7.3f}  {m['q']}")
    print("\nNOTE: gross of fees/slippage; assumes both asks fill at size. Verify depth before believing it. Paper only.")


if __name__ == "__main__":
    main()
