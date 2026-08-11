#!/usr/bin/env python3
"""
coin_updown_backtest.py — honest measurement of the crypto "Up or Down" markets.

WHY THIS EXISTS
---------------
The offline `ml/dataset.csv` has only 12 crypto rows and a MIN horizon of 24h, so the
short-horizon "Bitcoin/ETH/... Up or Down" coinflip markets (which resolve every 5 min)
are NOT in it and cannot be backtested there. This script pulls RESOLVED up/down markets
live from Gamma + CLOB and measures whether any naive strategy on them is +EV after a
spread haircut, with bootstrap 95% CIs (an edge only counts if the CI excludes 0 — the
project's significance hard-rule). Controls (always-YES / always-NO) MUST be ~symmetric;
favorite/fade are the real questions.

Two phases:
  A. BASE-RATE (cheap, large N): just outcomes from the market list. Is P(Up) == 0.5?
  B. EV / CALIBRATION (per-market price history): entry at open (~0.5) AND at a fixed
     lead time before close; PnL of always-YES / buy-favorite / fade-favorite after spread.

Paper research only. Reads public data; places no orders.
"""
import urllib.request, urllib.parse, json, re, datetime, time, sys, random, math

UA = "Mozilla/5.0 (coin-updown-backtest research)"
GAMMA = "https://gamma-api.polymarket.com/markets"
CLOB  = "https://clob.polymarket.com/prices-history"
COIN_RE = re.compile(r'\b(bitcoin|btc|ethereum|eth|bnb|solana|sol|xrp|ripple|dogecoin|doge)\b', re.I)
UD_RE   = re.compile(r'up or down', re.I)

# ── knobs ────────────────────────────────────────────────────────────────────
DAYS_BACK     = float(sys.argv[1]) if len(sys.argv) > 1 else 21.0   # date window
MAX_LIST_PAGES= 60          # 100 markets/page scanned for the cheap base-rate phase
MAX_PRICED    = 700         # cap markets we fetch price-history for (phase B runtime)
LEAD_SEC      = 120         # "late" entry: this many seconds before close
SPREAD        = 0.02        # round-trip spread haircut (you buy at mid + SPREAD/2)
PACE          = 0.18        # seconds between requests (under CLOB caps)
TIMEOUT       = 25
_last = [0.0]

def get(url, params=None):
    if params: url += "?" + urllib.parse.urlencode(params)
    w = PACE - (time.time() - _last[0])
    if w > 0: time.sleep(w)
    _last[0] = time.time()
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except Exception as e:
            code = getattr(e, "code", None)
            if attempt < 3 and (code == 429 or (code and 500 <= code < 600)):
                time.sleep(1.5 * (attempt + 1)); continue
            return None
    return None

def coin_of(q):
    m = COIN_RE.search(q)
    if not m: return "other"
    k = m.group(1).lower()
    return {"btc":"bitcoin","eth":"ethereum","sol":"solana","ripple":"xrp","doge":"dogecoin"}.get(k, k)

def jparse(x):
    try: return json.loads(x) if isinstance(x, str) else x
    except Exception: return None

# ── Phase A: collect resolved crypto up/down markets (outcomes only) ──────────
now = datetime.datetime.now(datetime.timezone.utc)
dmin = (now - datetime.timedelta(days=DAYS_BACK)).strftime("%Y-%m-%dT%H:%M:%SZ")
dmax = (now + datetime.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
print(f"[collect] window {dmin} .. {dmax}  (up to {MAX_LIST_PAGES} pages)")

mkts = {}   # condition_id -> market (dedup)
for pg in range(MAX_LIST_PAGES):
    d = get(GAMMA, {"closed":"true","limit":100,"offset":pg*100,
                    "end_date_min":dmin,"end_date_max":dmax,
                    "order":"endDate","ascending":"false"})
    if not d: break
    for m in d:
        q = m.get("question","")
        if UD_RE.search(q) and COIN_RE.search(q):
            mkts[m.get("conditionId") or m.get("condition_id") or q] = m
    if len(d) < 100: break
    if pg % 10 == 9: print(f"  ...page {pg+1}, crypto up/down so far: {len(mkts)}")

ud = list(mkts.values())
print(f"[collect] resolved crypto up/down markets: {len(ud)}")
if len(ud) < 30:
    print("[abort] too few markets to say anything honest (need >=30). Network may be flaky; retry.")
    sys.exit(0)

rows = []   # base-rate rows
for m in ud:
    op = jparse(m.get("outcomePrices"))
    if op not in (["1","0"], ["0","1"]):   # skip voids / non-clean
        continue
    yes_won = 1 if op[0] == "1" else 0
    rows.append({"coin": coin_of(m["question"]), "yes_won": yes_won,
                 "q": m["question"], "end": m.get("endDate"),
                 "toks": jparse(m.get("clobTokenIds"))})

# ── base-rate report ─────────────────────────────────────────────────────────
def wilson(k, n, z=1.96):
    if n == 0: return (0,0)
    p = k/n; d = 1+z*z/n
    c = (p+z*z/(2*n))/d; h = z*math.sqrt(p*(1-p)/n + z*z/(4*n*n))/d
    return (c-h, c+h)

n = len(rows); up = sum(r["yes_won"] for r in rows)
lo, hi = wilson(up, n)
print("\n================= PHASE A: BASE RATE (Up vs Down) =================")
print(f"  N = {n} resolved crypto up/down markets")
print(f"  P(Up resolves YES) = {up/n:.4f}   Wilson 95% CI [{lo:.4f}, {hi:.4f}]")
print(f"  -> {'INCLUDES 0.5 (no directional drift)' if lo <= 0.5 <= hi else 'EXCLUDES 0.5 (!) directional tilt'}")
from collections import Counter, defaultdict
bycoin = defaultdict(lambda: [0,0])
for r in rows: bycoin[r["coin"]][0]+=r["yes_won"]; bycoin[r["coin"]][1]+=1
print("  by coin:  " + "  ".join(f"{c}={u}/{t}({u/t:.0%})" for c,(u,t) in sorted(bycoin.items(), key=lambda x:-x[1][1])))

# ── Phase B: entry prices via prices-history → EV ────────────────────────────
priced = [r for r in rows if r.get("toks")]
random.seed(42); random.shuffle(priced)
priced = priced[:MAX_PRICED]
print(f"\n[pricehist] fetching price history for {len(priced)} markets (cap {MAX_PRICED})...")

def hist_for(tok, endts):
    h = get(CLOB, {"market":tok,"startTs":endts-3600,"endTs":endts,"fidelity":1})
    pts = h.get("history") if isinstance(h, dict) else h
    return pts or []

samples = []  # dicts: entry_open, entry_late, yes_won, coin
ok = 0
for i, r in enumerate(priced):
    try:
        endts = int(datetime.datetime.fromisoformat(r["end"].replace("Z","+00:00")).timestamp())
        pts = hist_for(r["toks"][0], endts)   # YES token midpoint series
        if len(pts) < 3: continue
        pts.sort(key=lambda p: p["t"])
        open_yes = pts[0]["p"]
        late = [p for p in pts if p["t"] <= endts - LEAD_SEC]
        late_yes = (late[-1]["p"] if late else pts[0]["p"])
        samples.append({"coin": r["coin"], "yes_won": r["yes_won"],
                        "open_yes": float(open_yes), "late_yes": float(late_yes)})
        ok += 1
    except Exception:
        pass
    if i % 100 == 99: print(f"  ...{i+1}/{len(priced)} done, {ok} usable")
print(f"[pricehist] usable priced samples: {len(samples)}")

def boot_ci(xs, B=2000, z=0.95):
    if not xs: return (0,0,0)
    n=len(xs); means=[]
    rng=random.Random(7)
    for _ in range(B):
        s=sum(xs[rng.randrange(n)] for _ in range(n))/n
        means.append(s)
    means.sort()
    return (sum(xs)/n, means[int((1-z)/2*B)], means[int((1+z)/2*B)])

def strat_pnl(samples, entry_key, mode):
    """PnL per $1 share after spread. mode: yes|no|fav|fade. Buy cost = price + SPREAD/2."""
    out=[]
    for s in samples:
        p_yes = s[entry_key]
        if mode=="yes":  side_p, won = p_yes,        s["yes_won"]
        elif mode=="no": side_p, won = 1-p_yes,      1-s["yes_won"]
        elif mode=="fav":
            if p_yes>=0.5: side_p, won = p_yes,   s["yes_won"]
            else:          side_p, won = 1-p_yes, 1-s["yes_won"]
        elif mode=="fade":
            if p_yes>=0.5: side_p, won = 1-p_yes, 1-s["yes_won"]
            else:          side_p, won = p_yes,   s["yes_won"]
        cost = side_p + SPREAD/2
        if cost>=1.0 or cost<=0: continue       # can't buy through the boundary
        out.append((1.0 if won else 0.0) - cost)
    return out

print("\n================= PHASE B: STRATEGY EV (after %.0f%% spread) =================" % (SPREAD*100))
print(f"  {'strategy':22s} {'N':>5s} {'$/trade':>9s} {'95% CI':>22s}  verdict")
for label, ek, mode in [
    ("always-YES (control)","late_yes","yes"),
    ("always-NO  (control)","late_yes","no"),
    ("buy-favorite @open",  "open_yes","fav"),
    ("fade-favorite @open", "open_yes","fade"),
    ("buy-favorite @-%ds"%LEAD_SEC, "late_yes","fav"),
    ("fade-favorite @-%ds"%LEAD_SEC,"late_yes","fade"),
]:
    xs = strat_pnl(samples, ek, mode)
    if not xs: print(f"  {label:22s}  (no samples)"); continue
    mean, lo, hi = boot_ci(xs)
    verdict = "REAL +EV" if lo>0 else ("LOSES" if hi<0 else "~flat / not sig")
    print(f"  {label:22s} {len(xs):5d} {mean:+9.4f}   [{lo:+.4f}, {hi:+.4f}]  {verdict}")

# calibration: does the favorite win at its implied rate?
print("\n  calibration @ late entry (favorite price bucket -> realized win rate):")
buck=defaultdict(lambda:[0,0])
for s in samples:
    fp = max(s["late_yes"], 1-s["late_yes"])
    fw = s["yes_won"] if s["late_yes"]>=0.5 else 1-s["yes_won"]
    b = round(fp*20)/20
    buck[b][0]+=fw; buck[b][1]+=1
for b in sorted(buck):
    w,t=buck[b]
    if t>=10: print(f"    fav price ~{b:.2f}: won {w/t:.2f} (n={t})  gap {w/t-b:+.2f}")

out={"generated_for":"coin up/down honest backtest","window_days":DAYS_BACK,
     "n_baserate":n,"p_up":up/n,"p_up_ci":[lo,hi],
     "n_priced":len(samples),"spread":SPREAD,"lead_sec":LEAD_SEC}
json.dump(out, open("coin_updown_backtest.json","w"), indent=2)
print("\n[done] summary -> coin_updown_backtest.json")
