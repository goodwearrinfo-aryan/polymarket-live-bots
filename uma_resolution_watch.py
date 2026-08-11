#!/usr/bin/env python3
"""uma_resolution_watch.py — KEYLESS resolution-arb watcher for Polymarket. Paper, ALERT-ONLY.

Polymarket resolves via UMA's optimistic oracle. During the ~2h challenge window a market's
PROPOSED outcome is already known but the executable orderbook may not have repriced — that gap
is a RESOLUTION-ARB (the complementary edge to settled-but-mispriced DATA-arb, e.g. the Hormuz
win). This watches it WITHOUT brittle on-chain log decoding: the Gamma API exposes
umaResolutionStatus='proposed' + the proposed winner KEYLESSLY. For each market in the proposed
window we read the LIVE CLOB book; if the proposed-winner side still trades far enough below 1.0
to clear the taker fee, we FLAG a lead.

HONEST by construction: resolution-arb is a COMPETED-FOR edge (many watch UMA). A flag is a LEAD
to verify (real? capturable net of fee? before faster bots?), NEVER an auto-trade. Default is
NULL — 'proposed' markets are rare and the crowd usually prices the proposal fast (the
BTC-$72,500 lesson: triggered but YES already 0.999, net −0.9c). Read-only; paper; alert-only.
"""
import json, os, time, subprocess, urllib.request

GAMMA = "https://gamma-api.polymarket.com/markets"
CLOB = "https://clob.polymarket.com"
STATE = os.path.expanduser("~/Documents/polymarket/uma_resolution_state.json")
LOG = "/tmp/uma_resolution_watch.log"
FEED = os.path.expanduser("~/openalice/ui/public/uma_resolution.json")
ALERT_TO = "krisharyan@icloud.com"
GAP_ASK = 0.90      # flag only if the proposed-winner best-ask is still below this
FEE = 0.01          # ~1% taker; net edge = (1.0 - ask) - FEE must be > 0

def _get(u, t=12):
    try: return json.load(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "x"}), timeout=t))
    except Exception: return None

def _jload(x, default):
    try: return json.loads(x) if isinstance(x, str) else (x if x is not None else default)
    except Exception: return default

def proposed_markets():
    """Markets currently in the UMA challenge window (oracle has PROPOSED, not yet final)."""
    d = _get(f"{GAMMA}?closed=true&limit=500&order=umaEndDate&ascending=false") or []
    return [m for m in d if m.get("umaResolutionStatus") == "proposed"]

def best_ask(token_id):
    """Lowest ask on the CLOB for an outcome token (the executable buy price), keyless."""
    b = _get(f"{CLOB}/book?token_id={token_id}")
    if not b: return None
    asks = b.get("asks") or []
    try: return min(float(a["price"]) for a in asks) if asks else None
    except Exception: return None

def scan():
    flags, checked = [], 0
    for m in proposed_markets():
        prices = _jload(m.get("outcomePrices"), [])
        outs = _jload(m.get("outcomes"), [])
        toks = _jload(m.get("clobTokenIds"), [])
        if not prices or not toks or len(toks) != len(prices):
            continue
        checked += 1
        wi = max(range(len(prices)), key=lambda i: float(prices[i] or 0))   # proposed winner = argmax
        ask = best_ask(toks[wi])
        if ask is not None and ask < GAP_ASK:
            net = round((1.0 - ask) - FEE, 4)
            if net > 0:
                flags.append({"q": (m.get("question") or "")[:90],
                              "proposed_winner": outs[wi] if wi < len(outs) else "?",
                              "winner_ask": round(ask, 4), "net_edge": net,
                              "umaEndDate": m.get("umaEndDate"), "slug": m.get("slug")})
    return {"ts": time.time(), "checked": checked, "flags": flags}

def imessage(body):
    s = ('tell application "Messages" to send "%s" to buddy "%s" of (service 1 whose service type is iMessage)'
         % (body.replace('"', "'"), ALERT_TO))
    try: return subprocess.run(["osascript", "-e", s], capture_output=True, text=True, timeout=20).returncode == 0
    except Exception: return False

def load_state():
    try: return json.load(open(STATE))
    except Exception: return {"alerted": {}}

def save_state(s):
    tmp = STATE + ".tmp"; json.dump(s, open(tmp, "w")); os.replace(tmp, STATE)

def cycle():
    out = scan()
    s = load_state()
    keys = {f["slug"] for f in out["flags"] if f.get("slug")}
    for k in [k for k in s["alerted"] if k not in keys]:
        del s["alerted"][k]                                # cleared -> can re-alert later
    new = [f for f in out["flags"] if f.get("slug") and f["slug"] not in s["alerted"]]
    if new:
        body = "[uma-resolution] %d resolution-arb LEAD(s): " % len(new) + " || ".join(
            "%s — proposed %s, ask %.2f, net +%.0fc" % (f["q"][:40], f["proposed_winner"], f["winner_ask"], f["net_edge"]*100) for f in new)
        if imessage(body[:380]):
            for f in new: s["alerted"][f["slug"]] = time.time()
    save_state(s)
    try:
        tmp = FEED + ".tmp"; json.dump(out, open(tmp, "w")); os.replace(tmp, FEED)
    except Exception: pass
    open(LOG, "a").write(time.strftime("%H:%M:%S ") +
                         ("NULL — %d proposed checked, 0 gaps" % out["checked"] if not out["flags"]
                          else "%d LEAD(s) / %d checked" % (len(out["flags"]), out["checked"])) + "\n")
    return out

if __name__ == "__main__":
    import sys
    if "--once" in sys.argv:
        print(json.dumps(cycle(), indent=1))
    else:
        while True:
            try: cycle()
            except Exception as e:
                open(LOG, "a").write(time.strftime("%H:%M:%S ") + "error: " + str(e) + "\n")
            time.sleep(120)   # ~2h challenge window → 2-min poll is plenty
