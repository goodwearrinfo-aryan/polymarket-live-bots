#!/usr/bin/env python3
"""
nearres_oos_gamma.py — OOS validation via Gamma prices-history API.

⚠️ CONTAMINATED / SUPERSEDED (2026-06-14, see Lesson 12) — DO NOT TRUST ITS P&L.
Books a FIXED +9¢ win / −3¢ loss (see ~line 186): pnl = (BET/entry)*(GAIN if won
else -STOP). It assumes every loss is a clean -3¢ stop, but esports favorites gap
past the stop in one tick (real loss 45–65¢). This optimism manufactured a fake edge.
Canonical gap-honest OOS = backtest_nearres.py (esports_hi NEW = −$0.088/exit, DSR FAIL).
Kept for history only.

For each of the 644 resolved esports markets in markets.parquet (Nov 2024–May 2026),
fetches hourly price history from gamma-api.polymarket.com, simulates nearres
entries in the last 4h window, and reports WR + bootstrap CI.

Run: python3 nearres_oos_gamma.py
Outputs: /tmp/nearres_oos_results.csv + summary printed to stdout
"""
import ast, json, os, random, ssl, sys, time, urllib.request
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ── Config ────────────────────────────────────────────────────────────────────
NEARRES_HI   = 0.88
NEARRES_MAX  = 0.95
NEARRES_GAIN = 0.09
NEARRES_STOP = 0.03
BET          = 2.0
WINDOW_H     = 4          # hours before resolution to look for entry
RATE_LIMIT   = 0.25       # seconds between requests
TIMEOUT      = 15
CACHE_FILE   = "/tmp/nearres_oos_price_cache.json"
OUT_CSV      = "/tmp/nearres_oos_results.csv"

ESPORTS_KW = [
    'counter-strike','cs2','cs:','valorant','league of legends',
    'lol:','dota','overwatch','r6 siege','rainbow six',
    'iem ','pgl major','blast premier','vct ','lcq ','lcs ','lck ','lpl ',
    'esport',' vs ',
]

WIN_START_TS = 1730419200  # 2024-11-01 UTC
WIN_END_TS   = 1748649600  # 2026-05-31 UTC

# ── Helpers ───────────────────────────────────────────────────────────────────
_ctx = ssl.create_default_context()
_ctx.check_hostname = False
_ctx.verify_mode = ssl.CERT_NONE


def is_esports(q: str) -> bool:
    q = (q or '').lower()
    return any(k in q for k in ESPORTS_KW)


def fetch_json(url: str, retries=3) -> dict | list | None:
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=TIMEOUT, context=_ctx) as r:
                return json.loads(r.read())
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
            else:
                return None


def fetch_price_history(token_id: str) -> list[tuple[int, float]]:
    """Return list of (unix_ts, price) from hourly history, or []."""
    url = (
        f"https://gamma-api.polymarket.com/prices-history"
        f"?market={token_id}&interval=max&fidelity=60"
    )
    data = fetch_json(url)
    if not data or 'history' not in data:
        return []
    return [(int(pt['t']), float(pt['p'])) for pt in data['history']]


def parse_outcome(prices_str) -> str | None:
    try:
        lst = ast.literal_eval(str(prices_str))
        vals = [float(x) for x in lst]
        if vals[0] == 1.0: return 'YES'
        if vals[0] == 0.0: return 'NO'
    except Exception:
        pass
    return None


# ── Load focus markets ────────────────────────────────────────────────────────
print("Loading markets.parquet...")
mkt_path = os.path.expanduser("/tmp/sii-wangzj/markets.parquet")
if not os.path.exists(mkt_path):
    print(f"ERROR: {mkt_path} not found — run markets.parquet download first")
    sys.exit(1)

markets = pd.read_parquet(mkt_path)
markets['end_date'] = pd.to_datetime(markets['end_date'], errors='coerce', utc=True)
markets['end_ts']   = markets['end_date'].astype('int64') // 1_000_000_000
markets['outcome']  = markets['outcome_prices'].apply(parse_outcome)

focus = markets[
    markets['closed'].fillna(False).astype(bool) &
    markets['end_ts'].between(WIN_START_TS, WIN_END_TS) &
    markets['outcome'].notna() &
    markets['question'].apply(is_esports)
].copy().reset_index(drop=True)

print(f"Focus esports markets: {len(focus)}")

# ── Load / init price cache ───────────────────────────────────────────────────
cache: dict[str, list] = {}
if os.path.exists(CACHE_FILE):
    try:
        cache = json.load(open(CACHE_FILE))
        print(f"Loaded price cache: {len(cache)} entries")
    except Exception:
        cache = {}

# ── Fetch price histories ─────────────────────────────────────────────────────
total = len(focus)
t0 = time.time()
errors = 0

for i, (_, row) in enumerate(focus.iterrows()):
    cid = row['condition_id']
    if cid in cache:
        continue

    token_id = str(row['token1']) if pd.notna(row.get('token1')) else None
    if not token_id:
        cache[cid] = []
        continue

    hist = fetch_price_history(token_id)
    cache[cid] = hist
    if not hist:
        errors += 1

    if (i + 1) % 50 == 0 or i == total - 1:
        elapsed = time.time() - t0
        rate = (i + 1) / elapsed
        eta = (total - i - 1) / rate if rate > 0 else 0
        print(f"  {i+1}/{total} fetched  errors={errors}  "
              f"t={elapsed:.0f}s  ETA={eta:.0f}s")
        # save cache periodically
        json.dump(cache, open(CACHE_FILE, 'w'))

    time.sleep(RATE_LIMIT)

json.dump(cache, open(CACHE_FILE, 'w'))
print(f"Price fetch done. {len(cache)} cached, {errors} empty.")

# ── Simulate nearres entries ──────────────────────────────────────────────────
sims = []
no_data = 0
no_entry = 0

for _, row in focus.iterrows():
    cid = row['condition_id']
    end_ts = int(row['end_ts'])
    outcome = row['outcome']
    q = row['question']

    hist = cache.get(cid, [])
    if not hist:
        no_data += 1
        continue

    win_start = end_ts - WINDOW_H * 3600
    in_win = sorted((ts, pr) for ts, pr in hist if win_start <= ts <= end_ts)

    entry_price = entry_side = None
    for ts, pr in in_win:
        if NEARRES_HI <= pr <= NEARRES_MAX:
            entry_price = pr
            entry_side = 'YES'
        elif NEARRES_HI <= (1 - pr) <= NEARRES_MAX:
            entry_price = 1 - pr
            entry_side = 'NO'

    if entry_price is None:
        no_entry += 1
        continue

    won = (
        (entry_side == 'YES' and outcome == 'YES') or
        (entry_side == 'NO'  and outcome == 'NO')
    )
    pnl = (BET / entry_price) * (NEARRES_GAIN if won else -NEARRES_STOP)
    sims.append({
        'condition_id': cid,
        'question': q[:80],
        'side': entry_side,
        'entry': round(entry_price, 4),
        'outcome': outcome,
        'won': won,
        'pnl': round(pnl, 4),
    })

print(f"\nSimulation: n={len(sims)}  no_data={no_data}  no_entry={no_entry}")

if not sims:
    print("No simulated trades — check network / cache")
    sys.exit(0)

# ── Results ───────────────────────────────────────────────────────────────────
df = pd.DataFrame(sims)
df.to_csv(OUT_CSV, index=False)

pnls = df['pnl'].tolist()
wins = df['won'].sum()
wr   = wins / len(df)
mean = sum(pnls) / len(df)

rng   = random.Random(42)
N     = len(pnls)
boots = sorted([sum(pnls[rng.randrange(N)] for _ in range(N)) / N for _ in range(3000)])
lo, hi = boots[75], boots[2925]

print(f"\n{'='*50}")
print(f"nearres OOS  n={len(df)}  WR={wr:.1%}  mean=${mean:+.4f}  total=${sum(pnls):+.2f}")
print(f"Bootstrap 95% CI: [{lo:+.4f}, {hi:+.4f}]")
print("EDGE CONFIRMED" if lo > 0 else "CI includes 0 — not confirmed yet")
print(f"{'='*50}")
print(f"\nSaved: {OUT_CSV}")

print("\nSample trades:")
for _, r in df.head(10).iterrows():
    print(f"  {'W' if r['won'] else 'L'} {r['side']}@{r['entry']:.3f}→{r['outcome']}  "
          f"${r['pnl']:+.4f}  {r['question']}")
