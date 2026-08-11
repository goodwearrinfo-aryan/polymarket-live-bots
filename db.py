"""
PostgreSQL persistence layer for scalp_lab and polymarket_bot.
Replaces JSON read/write for trade state only.
All other state (brain, cache, wallet_signal_pnl, etc.) remains in JSON.
"""
import json
import psycopg2
from psycopg2.extras import execute_values, RealDictCursor

DB_NAME = "aryanagarwal"

def _conn():
    return psycopg2.connect(dbname=DB_NAME)

def _ensure_tables(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id SERIAL PRIMARY KEY,
            token_id TEXT,
            condition_id TEXT,
            title TEXT,
            strategy TEXT,
            side TEXT,
            entry_price NUMERIC,
            size NUMERIC,
            cost_usdc NUMERIC,
            confidence NUMERIC,
            volume NUMERIC,
            trader_count INTEGER,
            category TEXT,
            avg_rank_pct NUMERIC,
            wallets JSONB,
            days_to_close NUMERIC,
            high_water_mark NUMERIC,
            opened_at TEXT,
            closed_at TEXT,
            close_price NUMERIC,
            pnl_usdc NUMERIC,
            reason TEXT,
            dry BOOLEAN,
            status TEXT,
            news_headline TEXT,
            news_sentiment NUMERIC,
            news_momentum TEXT,
            res_margin NUMERIC,
            res_pyes NUMERIC,
            res_has NUMERIC
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS lab_trades (
            id SERIAL PRIMARY KEY,
            leg TEXT,
            trade_id TEXT,
            question TEXT,
            strategy TEXT,
            side TEXT,
            entry_mid NUMERIC,
            entry_fill NUMERIC,
            size NUMERIC,
            cost NUMERIC,
            vol NUMERIC,
            cat TEXT,
            opened_at TEXT,
            closed_at TEXT,
            exit_fill NUMERIC,
            exit_price NUMERIC,
            pnl NUMERIC,
            result TEXT,
            reason TEXT,
            status TEXT,
            UNIQUE(leg, trade_id, opened_at)
        )
    """)


# ── polymarket_bot: trades ────────────────────────────────────────────────────

def bot_load_state():
    """Returns (open_positions_dict, closed_list) from PostgreSQL."""
    try:
        con = _conn()
        cur = con.cursor(cursor_factory=RealDictCursor)
        _ensure_tables(cur)
        con.commit()
        cur.execute("SELECT * FROM trades WHERE status = 'open'")
        open_rows = cur.fetchall()
        cur.execute("SELECT * FROM trades WHERE status = 'closed' ORDER BY id")
        closed_rows = cur.fetchall()
        con.close()

        def to_dict(row):
            d = dict(row)
            d.pop('id', None)
            d.pop('status', None)
            if isinstance(d.get('wallets'), str):
                d['wallets'] = json.loads(d['wallets'])
            return d

        open_pos = {r['token_id']: to_dict(r) for r in open_rows}
        closed = [to_dict(r) for r in closed_rows]
        return open_pos, closed
    except Exception as e:
        import sys
        sys.stderr.write(f"[db] bot_load_state failed: {e} — falling back to empty\n")
        return {}, []


def bot_save_state(positions: dict, closed: list):
    """Write full bot state to PostgreSQL (upsert)."""
    try:
        con = _conn()
        cur = con.cursor()
        _ensure_tables(cur)

        # Clear and rewrite (positions dict is small, ~10-50 rows max)
        cur.execute("DELETE FROM trades")

        def trade_row(t, status):
            return (
                t.get('token_id'), t.get('condition_id'), t.get('title'),
                t.get('strategy'), t.get('side'), t.get('entry_price'),
                t.get('size'), t.get('cost_usdc'), t.get('confidence'),
                t.get('volume'), t.get('trader_count'), t.get('category'),
                t.get('avg_rank_pct'),
                json.dumps(t.get('wallets') or []),
                t.get('days_to_close'), t.get('high_water_mark'),
                t.get('opened_at'), t.get('closed_at'),
                t.get('close_price'), t.get('pnl_usdc'), t.get('reason'),
                t.get('dry'), status,
                t.get('news_headline'), t.get('news_sentiment'), t.get('news_momentum'),
                t.get('res_margin'), t.get('res_pyes'), t.get('res_has'),
            )

        rows = [trade_row(t, 'open') for t in positions.values()]
        rows += [trade_row(t, 'closed') for t in closed]

        if rows:
            execute_values(cur, """
                INSERT INTO trades (
                    token_id, condition_id, title, strategy, side,
                    entry_price, size, cost_usdc, confidence, volume,
                    trader_count, category, avg_rank_pct, wallets,
                    days_to_close, high_water_mark, opened_at, closed_at,
                    close_price, pnl_usdc, reason, dry, status,
                    news_headline, news_sentiment, news_momentum,
                    res_margin, res_pyes, res_has
                ) VALUES %s
            """, rows)

        con.commit()
        con.close()
    except Exception as e:
        import sys
        sys.stderr.write(f"[db] bot_save_state failed: {e}\n")


# ── scalp_lab: lab_trades ─────────────────────────────────────────────────────

def lab_load_state(legs):
    """Returns state dict {leg: {open: [...], closed: [...]}} from PostgreSQL."""
    try:
        con = _conn()
        cur = con.cursor(cursor_factory=RealDictCursor)
        _ensure_tables(cur)
        con.commit()
        cur.execute("SELECT * FROM lab_trades ORDER BY id")
        rows = cur.fetchall()
        con.close()

        state = {leg: {"open": [], "closed": []} for leg in legs}
        for row in rows:
            d = dict(row)
            leg = d.pop('leg', None)
            status = d.pop('status', 'open')
            d.pop('id', None)
            if leg not in state:
                state[leg] = {"open": [], "closed": []}
            # map db cols back to scalp_lab field names
            t = {
                'id':         d.get('trade_id'),
                'q':          d.get('question'),
                'cat':        d.get('cat'),
                'strategy':   d.get('strategy'),
                'side':       d.get('side'),
                'entry_mid':  float(d['entry_mid']) if d.get('entry_mid') is not None else None,
                'entry_fill': float(d['entry_fill']) if d.get('entry_fill') is not None else None,
                'size':       float(d['size']) if d.get('size') is not None else None,
                'cost':       float(d['cost']) if d.get('cost') is not None else None,
                'vol':        float(d['vol']) if d.get('vol') is not None else None,
                'opened_at':  d.get('opened_at'),
            }
            if status == 'closed':
                t.update({
                    'closed_at':  d.get('closed_at'),
                    'exit_fill':  float(d['exit_fill']) if d.get('exit_fill') is not None else None,
                    'exit_price': float(d['exit_price']) if d.get('exit_price') is not None else None,
                    'pnl_usdc':   float(d['pnl']) if d.get('pnl') is not None else None,
                    'result':     d.get('result'),
                    'reason':     d.get('reason'),
                })
                state[leg]['closed'].append(t)
            else:
                state[leg]['open'].append(t)

        return state
    except Exception as e:
        import sys
        sys.stderr.write(f"[db] lab_load_state failed: {e} — returning empty\n")
        return {leg: {"open": [], "closed": []} for leg in legs}


def lab_save_state(state: dict):
    """Write full lab state to PostgreSQL (clear + rewrite)."""
    try:
        con = _conn()
        cur = con.cursor()
        _ensure_tables(cur)
        cur.execute("DELETE FROM lab_trades")

        rows = []
        for leg, data in state.items():
            for t in data.get('open', []):
                rows.append((
                    leg, t.get('id'), t.get('q'), t.get('strategy'), t.get('side'),
                    t.get('entry_mid'), t.get('entry_fill'), t.get('size'),
                    t.get('cost'), t.get('vol'), t.get('cat'),
                    t.get('opened_at'), None, None, None, None, None, None, 'open'
                ))
            for t in data.get('closed', []):
                rows.append((
                    leg, t.get('id'), t.get('q'), t.get('strategy'), t.get('side'),
                    t.get('entry_mid'), t.get('entry_fill'), t.get('size'),
                    t.get('cost'), t.get('vol'), t.get('cat'),
                    t.get('opened_at'), t.get('closed_at'),
                    t.get('exit_fill'), t.get('exit_price'),
                    t.get('pnl_usdc') or t.get('pnl'),
                    t.get('result'), t.get('reason'), 'closed'
                ))

        if rows:
            execute_values(cur, """
                INSERT INTO lab_trades (
                    leg, trade_id, question, strategy, side,
                    entry_mid, entry_fill, size, cost, vol, cat,
                    opened_at, closed_at, exit_fill, exit_price,
                    pnl, result, reason, status
                ) VALUES %s
                ON CONFLICT (leg, trade_id, opened_at) DO UPDATE SET
                    status = EXCLUDED.status,
                    closed_at = EXCLUDED.closed_at,
                    exit_fill = EXCLUDED.exit_fill,
                    exit_price = EXCLUDED.exit_price,
                    pnl = EXCLUDED.pnl,
                    result = EXCLUDED.result,
                    reason = EXCLUDED.reason
            """, rows)

        con.commit()
        con.close()
    except Exception as e:
        import sys
        sys.stderr.write(f"[db] lab_save_state failed: {e}\n")
