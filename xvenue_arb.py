#!/usr/bin/env python3
"""xvenue_arb.py — CROSS-VENUE arbitrage scanner: the same event priced differently on
Polymarket AND Kalshi = a risk-free lock backed by TWO independent liquidity pools
(scalable, unlike thin single-venue basket locks). The documented blocker was matching
the same event across different wording — solved with an LLM confirm step.

Lock: if YES is cheaper on venue A than B, buy YES@A + NO@B (= sell YES@B). One side
pays $1, the other you sold for more than you paid → locked gap. CAVEAT: Kalshi charges
trading fees (~1-2%) + you need capital on BOTH venues, so a real lock needs
gap > fees + spreads. Verified 2026-06-15: the big cross-venue gaps are FAKE (Polymarket
"will X win" vs Kalshi "who will run" — different resolution); genuine same-event pairs
price efficiently. So this runs as a STANDING SCANNER to catch the rare real divergence.

A VERDICT CACHE (xvenue_match_cache.json) means each pair is LLM-confirmed ONCE ever;
warm runs are ~free and just re-check live gaps on known same-event pairs. PAPER, no orders.

Usage:
  python3 xvenue_arb.py            # verbose scan (shows confirmations)
  python3 xvenue_arb.py --once     # quiet scan for the watchdog (capture + 1-line summary)
  python3 xvenue_arb.py --state    # show captured cross-venue locks
"""
import os, sys, json, re, time, argparse, urllib.request
import edge_common as ec

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "xvenue_match_cache.json")   # pair_key -> SAME/DIFF (never re-LLM a pair)
STATE = os.path.join(HERE, "xvenue_arb_state.json")     # captured confirmed locks
def _load_env():
    """Load ANTHROPIC_API_KEY from a sibling .env so the watchdog can run this keyless-in-env
    (key lives in futures-bot/.env; setdefault so a real env var still wins)."""
    for envp in (os.path.join(HERE, ".env"), os.path.expanduser("~/Documents/futures-bot/.env")):
        if not os.path.exists(envp):
            continue
        for line in open(envp):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()
KEY = os.environ.get("ANTHROPIC_API_KEY")
MAX_LLM = int(os.environ.get("XV_MAX_LLM", "20"))   # NEW confirmations per run (cache covers the rest)
MIN_OVERLAP = 3        # shared distinctive tokens to even consider a pair
MIN_GAP = 0.05         # mid gap to capture (must clear Kalshi fee + spreads)

STOP = set("will the to in a of on by be at for and or is are was 2026 2025 2027 2024 "
           "before after than market price get reach above below dip hit end any during "
           "who what next first round win election be vs".split())


def dtoks(s):
    return set(w for w in re.findall(r"[a-z0-9]+", s.lower()) if w not in STOP and len(w) > 3)


def _call(prompt, max_tokens=10):
    if not KEY:
        return None
    body = json.dumps({"model": "claude-opus-4-8", "max_tokens": max_tokens,
                       "messages": [{"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=body,
            headers={"x-api-key": KEY, "content-type": "application/json", "anthropic-version": "2023-06-01"})
    try:
        r = json.load(urllib.request.urlopen(req, timeout=30))
        return r["content"][0]["text"].strip() if "content" in r else None
    except Exception:
        return None


def candidates(poly, kal):
    """Best Kalshi match per Polymarket market by distinctive-token overlap (≥MIN_OVERLAP),
    sorted by gap desc — arb lives in the big gaps. Not capped; the cache + per-run LLM
    budget bound cost, so over a few runs every candidate gets a cached verdict."""
    ktok = [(k, dtoks(k["title"])) for k in kal]
    out = []
    for p in poly:
        pt = dtoks(p["q"])
        if len(pt) < MIN_OVERLAP:
            continue
        best = None
        for k, kt in ktok:
            ov = pt & kt
            if len(ov) >= MIN_OVERLAP and (best is None or len(ov) > best[0]):
                best = (len(ov), k, ov)
        if best:
            out.append((round(abs(p["yes"] - best[1]["yes"]), 3), p, best[1]))
    out.sort(key=lambda x: -x[0])
    return out


def confirm(p, k):
    prompt = (f'Two prediction markets:\nA (Polymarket): "{p["q"]}"\nB (Kalshi): "{k["title"]}"\n'
              f'Do BOTH resolve YES on the EXACT same real-world outcome — same entity, same '
              f'threshold, same timeframe, same resolution criteria? A small wording difference '
              f'is fine, but a different person/country/number/date is DIFFERENT.\n'
              f'Answer one word: SAME or DIFFERENT.')
    r = _call(prompt)
    if not r:
        return None
    u = r.upper()
    return "SAME" in u and "DIFFERENT" not in u


def _pair_key(p, k):
    return (p["q"].strip().lower() + " || " + k["title"].strip().lower())


def _load(path, default):
    if os.path.exists(path):
        try:
            return json.load(open(path))
        except Exception:
            pass
    return default


def _save(path, obj):
    tmp = path + ".tmp"
    json.dump(obj, open(tmp, "w"))
    os.replace(tmp, path)


def scan(verbose=False):
    """One scan: pull venues → candidates → cached/LLM confirm (budget-capped) → capture
    SAME pairs with gap≥MIN_GAP. Returns (n_cands, n_same, n_llm_spent, locks)."""
    poly = ec.poly_markets(pages=6, min_vol=20000)
    kal = ec.kalshi_markets(pages=6)
    cache = _load(CACHE, {})
    st = _load(STATE, {"locks": [], "last_scan": None})
    cands = candidates(poly, kal)
    spent, fails, same = 0, 0, []
    for gap, p, k in cands:
        key = _pair_key(p, k)
        if key in cache:
            v = cache[key]
        elif spent + fails < MAX_LLM:
            c = confirm(p, k)
            if c is None:                  # API/auth FAILURE — not a verdict; never cache
                fails += 1
                v = None
            else:
                v = "SAME" if c else "DIFF"
                cache[key] = v
                spent += 1
        else:
            v = None                       # out of budget this run; next run picks it up
        if v == "SAME":
            same.append((gap, p, k))
            if verbose:
                print(f"  SAME ✓ gap {gap:.2f}  P {p['yes']:.2f} [{p['q'][:38]}]  K {k['yes']:.2f} [{k['title'][:38]}]")
    _save(CACHE, cache)
    # capture / refresh confirmed locks above the gap bar
    have = {l["key"]: l for l in st["locks"]}
    new = 0
    for gap, p, k in same:
        if gap < MIN_GAP:
            continue
        key = _pair_key(p, k)
        rec = {"key": key, "gap": gap, "poly_q": p["q"][:90], "kalshi": k["title"][:90],
               "poly_yes": p["yes"], "kalshi_yes": k["yes"],
               "buy_yes_on": "Polymarket" if p["yes"] < k["yes"] else "Kalshi", "ts": int(time.time())}
        if key in have:
            have[key].update(rec)
        else:
            st["locks"].append(rec)
            new += 1
    st["last_scan"] = int(time.time())
    _save(STATE, st)
    return len(cands), len(same), spent, new, st, fails


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="quiet scan for the watchdog")
    ap.add_argument("--state", action="store_true", help="show captured cross-venue locks")
    a = ap.parse_args()
    if a.state:
        st = _load(STATE, {"locks": [], "last_scan": None})
        locks = sorted(st["locks"], key=lambda l: -l["gap"])
        print(f"\n  XVENUE locks captured: {len(locks)}")
        for l in locks[:20]:
            print(f"   gap {l['gap']:.2f}  buy YES@{l['buy_yes_on']:10}  P {l['poly_yes']:.2f}/K {l['kalshi_yes']:.2f}  [{l['poly_q'][:44]}]")
        if not locks:
            print("   (none yet — scanner waiting for a real same-event divergence)")
        sys.exit(0)
    if not KEY:
        print("ANTHROPIC_API_KEY required for the semantic matcher."); sys.exit(1)
    nc, ns, sp, new, st, fails = scan(verbose=not a.once)
    auth = "  ⚠️ AUTH FAILING — ANTHROPIC_API_KEY in .env is INVALID; scanner is BLIND" if (fails and not sp) else ""
    print(f"[{time.strftime('%Y-%m-%d %H:%MZ', time.gmtime())}] xvenue: {nc} candidates, "
          f"{ns} same-event, {sp} new confirms, {fails} API-fails, {new} new lock(s), "
          f"{len(st['locks'])} total → {STATE}{auth}")


if __name__ == "__main__":
    main()
