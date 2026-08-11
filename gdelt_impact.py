#!/usr/bin/env python3
"""
gdelt_impact.py — GDELT entity ingestion + market-impact memory (2026-06-13).

The hard information-edge build:
  1. Pull the latest GDELT 2.0 GKG file (global news, 15-min cadence, keyless).
  2. Extract entities (persons / orgs / locations) + tone per article.
  3. Match entities against live Polymarket questions.
  4. Log every match to sqlite (info_impact.db) with the market's price at
     match time — then on later runs fill in the price 1h / 6h / 24h after.
Over weeks this becomes the empirical answer to "how much does news about X
actually move markets about X" — calibration data nobody else has.

READ-ONLY research: never trades, never touches scalp_lab state.
Run every 15 min via launchd (com.aryan.polymarket-gdelt).

  python3 gdelt_impact.py            # one ingest + impact-update cycle
  python3 gdelt_impact.py --report   # show what the memory has learned so far
"""
import csv, io, json, os, re, sqlite3, sys, urllib.request, zipfile
from datetime import datetime, timezone

BASE   = os.path.dirname(os.path.abspath(__file__))
DB     = os.path.join(BASE, "info_impact.db")
GAMMA  = "https://gamma-api.polymarket.com/markets"
LASTUP = "http://data.gdeltproject.org/gdeltv2/lastupdate.txt"
MIN_VOL = 30_000
csv.field_size_limit(10_000_000)

# Entities too generic to mean anything against market questions.
ENTITY_STOP = {"united states", "new york", "white house", "los angeles",
               "washington", "america", "european union", "united kingdom",
               "wall street", "federal reserve bank", "supreme court"}


def get(url, timeout=30, binary=False):
    req = urllib.request.Request(url, headers={"User-Agent": "gdelt-impact/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
    return data if binary else data.decode()


def db_init():
    c = sqlite3.connect(DB)
    c.execute("""CREATE TABLE IF NOT EXISTS matches(
        id INTEGER PRIMARY KEY,
        ts_utc TEXT, gkg_file TEXT, article_url TEXT, source TEXT,
        entity TEXT, tone REAL,
        market_id TEXT, question TEXT,
        yes_at_match REAL, chg_at_match REAL, vol REAL,
        yes_1h REAL, yes_6h REAL, yes_24h REAL,
        UNIQUE(article_url, market_id))""")
    c.execute("CREATE TABLE IF NOT EXISTS seen_files(f TEXT PRIMARY KEY)")
    return c


def live_markets():
    out, offset = {}, 0
    while offset <= 700:
        batch = json.loads(get(f"{GAMMA}?active=true&closed=false&order=volumeNum"
                               f"&ascending=false&limit=100&offset={offset}"))
        if not batch: break
        for m in batch:
            try:
                yes = float(json.loads(m.get("outcomePrices", "[]"))[0])
                vol = float(m.get("volumeNum") or m.get("volume") or 0)
                chg = float(m.get("oneDayPriceChange") or 0)
            except Exception:
                continue
            if vol < MIN_VOL or not (0.03 <= yes <= 0.97): continue
            out[m["id"]] = {"q": m.get("question", ""), "yes": yes,
                            "vol": vol, "chg": chg,
                            "q_low": " " + m.get("question", "").lower() + " "}
        if len(batch) < 100: break
        offset += 100
    return out


def latest_gkg():
    line = [l for l in get(LASTUP).splitlines() if ".gkg.csv.zip" in l][0]
    url = line.split()[-1]
    fname = url.rsplit("/", 1)[-1]
    return fname, url


def parse_gkg(blob):
    """Yield (article_url, source, tone, entities) per GKG record."""
    z = zipfile.ZipFile(io.BytesIO(blob))
    raw = z.read(z.namelist()[0]).decode("utf-8", "replace")
    for row in csv.reader(io.StringIO(raw), delimiter="\t"):
        if len(row) < 16: continue
        url_, src = row[4], row[3]
        persons = row[11]; orgs = row[13]
        try:
            tone = float(row[15].split(",")[0])
        except Exception:
            tone = 0.0
        ents = set()
        for field in (persons, orgs):
            for e in field.split(";"):
                e = e.strip().lower()
                if len(e) > 5 and e not in ENTITY_STOP:
                    ents.add(e)
        if ents and url_.startswith("http"):
            yield url_, src, tone, ents


def ingest(c, mkts):
    fname, url = latest_gkg()
    if c.execute("SELECT 1 FROM seen_files WHERE f=?", (fname,)).fetchone():
        print(f"[gdelt] {fname} already ingested"); return 0
    blob = get(url, timeout=60, binary=True)
    now = datetime.now(timezone.utc).isoformat()
    n = 0
    for aurl, src, tone, ents in parse_gkg(blob):
        for e in ents:
            for mid, m in mkts.items():
                if " " + e + " " in m["q_low"] or e in m["q_low"]:
                    try:
                        c.execute("""INSERT OR IGNORE INTO matches
                            (ts_utc,gkg_file,article_url,source,entity,tone,
                             market_id,question,yes_at_match,chg_at_match,vol)
                            VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                            (now, fname, aurl, src, e, tone, mid, m["q"],
                             m["yes"], m["chg"], m["vol"]))
                        n += c.total_changes and 1
                    except Exception:
                        pass
    c.execute("INSERT OR IGNORE INTO seen_files VALUES(?)", (fname,))
    c.commit()
    print(f"[gdelt] {fname}: {n} entity-market matches logged")
    return n


def impact_update(c, mkts):
    """Fill yes_1h/6h/24h for rows whose window has elapsed."""
    now = datetime.now(timezone.utc)
    filled = 0
    for col, hours in (("yes_1h", 1), ("yes_6h", 6), ("yes_24h", 24)):
        rows = c.execute(f"""SELECT id, market_id, ts_utc FROM matches
                             WHERE {col} IS NULL""").fetchall()
        for rid, mid, ts in rows:
            age_h = (now - datetime.fromisoformat(ts)).total_seconds() / 3600
            if age_h < hours: continue
            m = mkts.get(mid)
            if m is None: continue          # resolved/vanished — stays NULL
            c.execute(f"UPDATE matches SET {col}=? WHERE id=?", (m["yes"], rid))
            filled += 1
    c.commit()
    if filled: print(f"[impact] filled {filled} follow-up prices")


def report(c):
    total, = c.execute("SELECT COUNT(*) FROM matches").fetchone()
    done,  = c.execute("SELECT COUNT(*) FROM matches WHERE yes_24h IS NOT NULL").fetchone()
    print(f"matches logged: {total}   with 24h follow-up: {done}")
    print("\nbiggest 24h moves after news match:")
    for q, e, y0, y24, tone in c.execute("""
            SELECT question, entity, yes_at_match, yes_24h, tone FROM matches
            WHERE yes_24h IS NOT NULL
            ORDER BY ABS(yes_24h - yes_at_match) DESC LIMIT 10"""):
        print(f"  {y24-y0:+.2f}  tone {tone:+.1f}  [{e}] {q[:60]}")
    print("\nmean |24h move| by tone bucket:")
    for row in c.execute("""
            SELECT CASE WHEN tone < -5 THEN 'very neg' WHEN tone < 0 THEN 'neg'
                        WHEN tone < 5 THEN 'pos' ELSE 'very pos' END b,
                   COUNT(*), AVG(ABS(yes_24h - yes_at_match))
            FROM matches WHERE yes_24h IS NOT NULL GROUP BY b"""):
        print(f"  {row[0]:>9}: n={row[1]:<5} mean|Δ24h|={row[2]:.3f}")


def main():
    c = db_init()
    if "--report" in sys.argv:
        report(c); return
    mkts = live_markets()
    print(f"[gamma] {len(mkts)} live markets")
    ingest(c, mkts)
    impact_update(c, mkts)


if __name__ == "__main__":
    main()
