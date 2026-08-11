"""
Credit/fee system for the personal lawyer.
Free tier: 10 queries/day per user.
Pro tier: unlimited (add credits manually or via UPI).
Uses SQLite — zero infra.
"""
import sqlite3, os, datetime, hashlib, secrets

DB = os.path.expanduser("~/Documents/lawdb.sqlite")

def _conn():
    conn = sqlite3.connect(DB)
    conn.execute("""CREATE TABLE IF NOT EXISTS credits (
        user_id TEXT PRIMARY KEY,
        display_name TEXT,
        tier TEXT DEFAULT 'free',
        credits INTEGER DEFAULT 0,
        daily_used INTEGER DEFAULT 0,
        daily_reset TEXT DEFAULT '',
        total_queries INTEGER DEFAULT 0,
        created_at TEXT DEFAULT (date('now'))
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS query_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT,
        agent TEXT,
        question TEXT,
        cost INTEGER DEFAULT 1,
        ts TEXT DEFAULT (datetime('now'))
    )""")
    conn.commit()
    return conn

FREE_DAILY = 10
PRO_COST_PER_QUERY = 1  # 1 credit per query

def get_or_create_user(user_id, display_name="Anonymous"):
    conn = _conn()
    today = datetime.date.today().isoformat()
    row = conn.execute("SELECT * FROM credits WHERE user_id=?", (user_id,)).fetchone()
    if not row:
        conn.execute("INSERT INTO credits (user_id, display_name, tier, credits, daily_used, daily_reset) VALUES (?,?,?,?,?,?)",
                     (user_id, display_name, "free", 0, 0, today))
        conn.commit()
        return {"user_id": user_id, "tier": "free", "credits": 0, "daily_used": 0, "daily_reset": today}

    cols = ["user_id","display_name","tier","credits","daily_used","daily_reset","total_queries","created_at"]
    d = dict(zip(cols, row))
    # Reset daily counter if new day
    if d["daily_reset"] != today:
        conn.execute("UPDATE credits SET daily_used=0, daily_reset=? WHERE user_id=?", (today, user_id))
        conn.commit()
        d["daily_used"] = 0
    return d

def check_quota(user_id):
    """Returns (allowed: bool, reason: str, remaining: dict)"""
    u = get_or_create_user(user_id)
    today = datetime.date.today().isoformat()

    if u["tier"] == "pro":
        if u["credits"] > 0:
            return True, "pro_credits", {"credits": u["credits"], "daily_used": u["daily_used"]}
        return False, "no_credits", {"credits": 0}

    # Free tier — 10/day
    used = u["daily_used"]
    if used < FREE_DAILY:
        return True, "free_tier", {"daily_remaining": FREE_DAILY - used, "daily_used": used}
    return False, "daily_limit", {"daily_remaining": 0, "upgrade_hint": "Add credits to continue"}

def consume(user_id, agent, question):
    """Consume 1 credit/free query. Returns (ok, message)."""
    allowed, reason, info = check_quota(user_id)
    if not allowed:
        return False, f"Daily limit reached ({FREE_DAILY}/day on free tier). {info.get('upgrade_hint','')}"

    conn = _conn()
    today = datetime.date.today().isoformat()

    if reason == "free_tier":
        conn.execute("UPDATE credits SET daily_used=daily_used+1, total_queries=total_queries+1 WHERE user_id=?", (user_id,))
    elif reason == "pro_credits":
        conn.execute("UPDATE credits SET credits=credits-1, daily_used=daily_used+1, total_queries=total_queries+1 WHERE user_id=?", (user_id,))

    conn.execute("INSERT INTO query_log (user_id, agent, question) VALUES (?,?,?)",
                 (user_id, agent, question[:500]))
    conn.commit()
    return True, "ok"

def add_credits(user_id, amount, upgrade_to_pro=False):
    """Add credits to a user (admin action)."""
    conn = _conn()
    get_or_create_user(user_id)
    tier = "pro" if upgrade_to_pro else None
    if tier:
        conn.execute("UPDATE credits SET credits=credits+?, tier=? WHERE user_id=?", (amount, tier, user_id))
    else:
        conn.execute("UPDATE credits SET credits=credits+? WHERE user_id=?", (amount, user_id))
    conn.commit()
    return {"ok": True, "added": amount, "user_id": user_id}

def stats(user_id):
    u = get_or_create_user(user_id)
    allowed, reason, info = check_quota(user_id)
    return {**u, "quota_allowed": allowed, "quota_reason": reason, "quota_info": info}

def admin_report():
    conn = _conn()
    users = conn.execute("SELECT user_id, tier, total_queries, daily_used FROM credits ORDER BY total_queries DESC").fetchall()
    total_queries = conn.execute("SELECT COUNT(*) FROM query_log").fetchone()[0]
    return {"total_users": len(users), "total_queries": total_queries, "users": users}

if __name__ == "__main__":
    # Demo
    uid = "test_user_001"
    print(stats(uid))
    ok, msg = consume(uid, "legal_notice", "What is bail?")
    print(f"Consumed: {ok} — {msg}")
    print(f"Stats after: {stats(uid)}")
