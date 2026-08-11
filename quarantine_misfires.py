#!/usr/bin/env python3
"""
quarantine_misfires.py — move nearres non-esports exits → nearres_misfire.

Uses load_state() → modify → save_state() so the change survives the DELETE+rewrite
cycle. Run between watchdog cycles (or the next cycle will undo a raw UPDATE).
Idempotent: already-quarantined records are skipped.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db as _db, json

ESPORTS_KW = (
    "counter-strike","cs2","cs:","valorant","league of legends","lol:","dota",
    "overwatch","r6 siege","rainbow six","iem ","pgl major","blast premier",
    "vct ","lcq ","lcs ","lck ","lpl ","esport"," vs ",
)

# Include nearres_misfire so it gets seeded in the state
LEGS_NEEDED = ("nearres", "nearres_misfire")

def is_esports(q: str) -> bool:
    return any(k in (q or "").lower() for k in ESPORTS_KW)

def main():
    # Load full state (so lab_save_state writes everything correctly)
    # We need all LEGS to avoid wiping legs not in our subset
    # Read them from the DB directly to get the full leg list
    import psycopg2
    from psycopg2.extras import RealDictCursor
    con = _db._conn()
    cur = con.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT DISTINCT leg FROM lab_trades")
    all_legs = [r['leg'] for r in cur.fetchall()] + ['nearres_misfire']
    con.close()

    state = _db.lab_load_state(all_legs)

    nr_closed = state.get('nearres', {}).get('closed', [])
    misfires = [t for t in nr_closed if not is_esports(t.get('q', ''))]

    if not misfires:
        print("No misfires — nothing to do.")
        return

    print(f"Found {len(misfires)} misfire(s):")
    for t in misfires:
        print(f"  reason={t.get('reason')} q={str(t.get('q',''))[:65]}")

    # Move to nearres_misfire
    state['nearres_misfire'] = state.get('nearres_misfire') or {"open": [], "closed": []}
    mf_ids = {t.get('id') for t in state['nearres_misfire']['closed']}

    moved = 0
    keep = []
    for t in nr_closed:
        if not is_esports(t.get('q', '')) and t.get('id') not in mf_ids:
            t2 = dict(t, strategy='nearres_misfire')
            state['nearres_misfire']['closed'].append(t2)
            moved += 1
        else:
            keep.append(t)

    state['nearres']['closed'] = keep
    _db.lab_save_state(state)
    print(f"Moved {moved} record(s) → nearres_misfire and saved to DB.")

    # Quick health check
    nr_after = [t for t in keep if t.get('reason') in ('target','stop','time')]
    pnls = []
    for t in nr_after:
        if t.get('reason') == 'target':
            pnl = (t.get('size') or 0) * 0.09
        else:
            pnl = -(t.get('size') or 0) * 0.03
        pnls.append(pnl)
    n = len(pnls)
    if n:
        wr = sum(1 for p in pnls if p > 0) / n
        mean = sum(pnls) / n
        print(f"\nnearres health after quarantine: n={n} WR={wr:.1%} mean={mean:+.4f} total={sum(pnls):+.2f}")
    else:
        # Use honest_report for priced exits (handles resolved etc.)
        try:
            import honest_report as hr
            STATE = os.path.expanduser('~/Documents/polymarket/scalp_lab_state.json')
            tmp = STATE + '.tmp'
            with open(tmp, 'w') as f:
                json.dump(state, f, default=str)
            os.replace(tmp, STATE)
            s2 = json.load(open(STATE))
            c2 = [t for t in s2.get('nearres',{}).get('closed',[])
                  if t.get('reason') in ('target','stop','time','resolved')]
            p2 = [hr.honest_pnl('nearres', t) for t in c2]
            if p2:
                wr2 = sum(1 for p in p2 if p > 0) / len(p2)
                mean2 = sum(p2) / len(p2)
                print(f"\nnearres health after quarantine: n={len(p2)} WR={wr2:.1%} mean={mean2:+.4f}")
        except Exception as e:
            print(f"health check skipped: {e}")

if __name__ == "__main__":
    main()
