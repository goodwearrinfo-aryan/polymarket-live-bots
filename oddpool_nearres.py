#!/usr/bin/env python3
"""
oddpool_nearres.py — Cross-reference Oddpool active esports markets
against open nearres positions, and surface new nearres candidates.

Usage:
    python3 oddpool_nearres.py

Outputs:
  - Open nearres positions with current Oddpool price + spread
  - New esports markets eligible for nearres entry (price 0.88-0.95, <4h to close)
"""
import json, os, sys, time, urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ── Config ────────────────────────────────────────────────────────────────────
ODDPOOL_KEY  = os.environ.get('ODDPOOL_KEY', '')
if not ODDPOOL_KEY:
    env_path = os.path.join(os.path.dirname(__file__), '.env')
    for line in open(env_path):
        if line.startswith('ODDPOOL_KEY='):
            ODDPOOL_KEY = line.strip().split('=', 1)[1]

NEARRES_HI   = 0.88
NEARRES_MAX  = 0.95
NEARRES_WINDOW_H = 4

ESPORTS_KW = [
    'counter-strike', 'cs2', 'valorant', 'league of legends',
    'dota', 'overwatch', 'r6 siege', 'rainbow six',
    'iem ', 'pgl major', 'blast premier', 'vct ', 'lcs ', 'lck ', 'lpl ',
    ' vs ', 'esport',
]

# ── Helpers ───────────────────────────────────────────────────────────────────
def oddpool_get(path, params=None):
    qs = ('?' + '&'.join(f'{k}={v}' for k, v in params.items())) if params else ''
    url = f'https://api.oddpool.com{path}{qs}'
    req = urllib.request.Request(url, headers={'X-API-Key': ODDPOOL_KEY, 'User-Agent': 'polymarket-bot/1.0'})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f'  WARN oddpool {path}: {e}')
        return None

def is_esports(title):
    t = (title or '').lower()
    return any(k in t for k in ESPORTS_KW)

def hours_to_close(close_ts_str):
    if not close_ts_str:
        return None
    try:
        dt = datetime.fromisoformat(close_ts_str.replace('Z', '+00:00'))
        diff = (dt - datetime.now(timezone.utc)).total_seconds() / 3600
        return round(diff, 2)
    except Exception:
        return None

# ── Load open positions from DB ───────────────────────────────────────────────
def load_open_positions():
    try:
        import db
        state = db.lab_load_state(['nearres', 'nearresno'])
        positions = state.get('positions', {})
        nearres = [
            {'market_id': mid, **pos}
            for mid, pos in positions.items()
            if pos.get('leg') in ('nearres', 'nearresno') and not pos.get('closed')
        ]
        return nearres
    except Exception as e:
        print(f'DB unavailable ({e}), skipping open position cross-ref')
        return []

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    now = datetime.now(timezone.utc)
    print(f'\n=== Oddpool × nearres  {now.strftime("%Y-%m-%d %H:%M UTC")} ===\n')

    # 1. Cross-ref open positions
    open_pos = load_open_positions()
    if open_pos:
        print(f'── Open nearres positions ({len(open_pos)}) ──')
        open_cids = {p['market_id'] for p in open_pos}
        for pos in open_pos:
            print(f"  {pos['leg']:12s} {pos['side']}@{pos['entry_price']:.3f}  {pos['question'][:60]}")
        print()

    # 2. Surface nearres candidates
    print('── New nearres candidates (price 0.88-0.95, <4h to close) ──')
    candidates = []

    markets_resp = oddpool_get('/search/markets', {
        'q': 'esports',
        'status': 'active',
        'limit': 100,
    })
    all_markets = markets_resp if isinstance(markets_resp, list) else (markets_resp or {}).get('markets', [])

    for mkt in all_markets:
        if not is_esports((mkt.get('title') or '') + ' ' + (mkt.get('event_title') or '')):
            continue

        mkt_id     = mkt.get('market_id') or mkt.get('condition_id')
        close_time = mkt.get('close_time') or mkt.get('end_date_iso')
        yes_ask    = mkt.get('yes_ask') or mkt.get('best_ask')
        no_ask     = mkt.get('no_ask')
        question   = mkt.get('title') or mkt.get('question', '')
        event_title = mkt.get('event_title', '')

        if not yes_ask:
            continue
        try:
            yes_ask = float(yes_ask)
            no_ask  = float(no_ask) if no_ask else round(1 - yes_ask, 4)
        except (TypeError, ValueError):
            continue

        h = hours_to_close(close_time)
        if h is None or h > NEARRES_WINDOW_H or h < 0:
            continue

        side = entry = None
        if NEARRES_HI <= yes_ask <= NEARRES_MAX:
            side, entry = 'YES', yes_ask
        elif NEARRES_HI <= no_ask <= NEARRES_MAX:
            side, entry = 'NO', no_ask

        if side:
            candidates.append({
                'event': event_title,
                'question': question,
                'market_id': mkt_id,
                'side': side,
                'entry': entry,
                'hours_left': h,
                'volume': mkt.get('volume', 0),
            })

    if candidates:
        candidates.sort(key=lambda x: x['hours_left'])
        for c in candidates:
            print(f"  {c['hours_left']:4.1f}h  {c['side']}@{c['entry']:.3f}  vol=${c['volume']:,.0f}  {c['question'][:55]}")
            print(f"         event: {c['event']}")
    else:
        print('  None right now (no active markets in 0.88-0.95 within 4h)')

    # 3. Summary
    print(f'\nNearres candidates found: {len(candidates)}')
    print(f'Open nearres positions:   {len(open_pos)}')

if __name__ == '__main__':
    main()
