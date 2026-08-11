#!/usr/bin/env python3
"""Extract India law data into ~/Documents/lawdb.sqlite."""

import os
import re
import json
import sqlite3
import urllib.request
import urllib.parse
import urllib.error

DB_PATH = os.path.expanduser("~/Documents/lawdb.sqlite")
TOKEN = os.environ.get("INDIANKANOON_TOKEN", "")

GITHUB_SOURCES = [
    {
        "act": "IPC",
        "url": "https://raw.githubusercontent.com/civictech-India/Indian-Law-Penal-Code-Json/main/ipc.json",
        "schema": "ipc",
    },
    {
        "act": "CrPC",
        "url": "https://raw.githubusercontent.com/civictech-India/Indian-Law-Penal-Code-Json/main/crpc.json",
        "schema": "ipc",
    },
    {
        "act": "CPC",
        "url": "https://raw.githubusercontent.com/civictech-India/Indian-Law-Penal-Code-Json/main/cpc.json",
        "schema": "cpc",
    },
    {
        "act": "IEA",
        "url": "https://raw.githubusercontent.com/civictech-India/Indian-Law-Penal-Code-Json/main/iea.json",
        "schema": "ipc",
    },
    {
        "act": "HMA",
        "url": "https://raw.githubusercontent.com/civictech-India/Indian-Law-Penal-Code-Json/main/hma.json",
        "schema": "ipc",
    },
    {
        "act": "MVA",
        "url": "https://raw.githubusercontent.com/civictech-India/Indian-Law-Penal-Code-Json/main/MVA.json",
        "schema": "ipc",
    },
    {
        "act": "NIA",
        "url": "https://raw.githubusercontent.com/civictech-India/Indian-Law-Penal-Code-Json/main/nia.json",
        "schema": "ipc",
    },
]

IK_TOPICS = [
    "article 21 right to life",
    "bail application criminal procedure",
    "consumer protection act",
    "property dispute land acquisition",
    "cheque bounce section 138",
    "domestic violence act",
    "right to information RTI",
    "motor accident compensation",
    "income tax evasion",
    "labour law unfair dismissal",
]

IK_SEARCH_URL = "https://api.indiankanoon.org/search/"
IK_DOC_URL = "https://api.indiankanoon.org/doc/{tid}/"


def strip_html(text):
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def fetch_url(url, data=None, headers=None):
    req = urllib.request.Request(url, data=data, headers=headers or {})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8")


def ik_post(url, params):
    data = urllib.parse.urlencode(params).encode()
    headers = {
        "Authorization": f"Token {TOKEN}",
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": "curl/7.88.1",
    }
    return fetch_url(url, data=data, headers=headers)


def setup_db(conn):
    cur = conn.cursor()
    cur.executescript("""
        DROP TABLE IF EXISTS sections;
        DROP TABLE IF EXISTS judgments;

        CREATE TABLE sections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            act TEXT NOT NULL,
            chapter TEXT,
            section_number TEXT,
            title TEXT,
            body TEXT,
            source_url TEXT,
            UNIQUE(act, section_number)
        );

        CREATE TABLE judgments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tid TEXT UNIQUE,
            title TEXT,
            court TEXT,
            date TEXT,
            headline TEXT,
            full_text TEXT,
            topic_query TEXT
        );
    """)
    conn.commit()
    print("Tables created (sections, judgments).")


def load_github_sources(conn):
    cur = conn.cursor()
    for src in GITHUB_SOURCES:
        act = src["act"]
        url = src["url"]
        schema = src["schema"]
        print(f"  Fetching {act} from GitHub...", end=" ", flush=True)
        try:
            raw = fetch_url(url)
            data = json.loads(raw)
        except Exception as e:
            print(f"FAILED: {e}")
            continue

        # data may be a list or dict with a key
        if isinstance(data, dict):
            # find first list value
            for v in data.values():
                if isinstance(v, list):
                    data = v
                    break
            else:
                data = [data]

        inserted = 0
        for item in data:
            if schema == "ipc":
                chapter = item.get("chapter", "")
                section_number = str(item.get("section", ""))
                title = item.get("section_title", "")
                body = item.get("section_desc", "")
            else:  # cpc
                chapter = ""
                section_number = str(item.get("section", ""))
                title = item.get("title", "")
                body = item.get("description", "")

            cur.execute(
                """INSERT INTO sections (act, chapter, section_number, title, body, source_url)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(act, section_number) DO UPDATE SET
                       chapter=excluded.chapter,
                       title=excluded.title,
                       body=excluded.body,
                       source_url=excluded.source_url""",
                (act, chapter, section_number, title, body, url),
            )
            inserted += 1

        conn.commit()
        print(f"{inserted} sections inserted.")


def load_ik_judgments(conn):
    cur = conn.cursor()
    for topic in IK_TOPICS:
        print(f"  IK search: '{topic}'...", end=" ", flush=True)
        try:
            resp = ik_post(IK_SEARCH_URL, {"formInput": topic, "pagenum": 0})
            results = json.loads(resp)
        except Exception as e:
            print(f"FAILED search: {e}")
            continue

        docs = results.get("docs", [])
        print(f"{len(docs)} results. Fetching full text...", flush=True)

        for doc in docs[:10]:
            tid = str(doc.get("tid", ""))
            title = doc.get("title", "")
            court = doc.get("docsource", "")
            date = doc.get("publishdate", "")
            headline = strip_html(doc.get("headline", ""))

            # fetch full text
            full_text = ""
            try:
                doc_resp = ik_post(IK_DOC_URL.format(tid=tid), {})
                doc_data = json.loads(doc_resp)
                full_text = strip_html(doc_data.get("doc", ""))
            except Exception as e:
                full_text = f"[fetch error: {e}]"

            cur.execute(
                """INSERT INTO judgments (tid, title, court, date, headline, full_text, topic_query)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(tid) DO UPDATE SET
                       title=excluded.title,
                       court=excluded.court,
                       date=excluded.date,
                       headline=excluded.headline,
                       full_text=excluded.full_text,
                       topic_query=excluded.topic_query""",
                (tid, title, court, date, headline, full_text, topic),
            )
            print(f"    [{tid}] {title[:60]}")

        conn.commit()


BULK_TOPICS = [
    "fundamental rights constitution india",
    "article 19 freedom of speech",
    "article 21 right to life dignity",
    "article 14 equality before law",
    "habeas corpus writ petition",
    "bail anticipatory bail criminal",
    "section 302 murder IPC",
    "section 420 cheating fraud IPC",
    "section 498A domestic cruelty wife",
    "section 138 cheque dishonour bounce",
    "consumer forum complaint deficiency service",
    "property dispute title deed ownership",
    "landlord tenant eviction rent",
    "contract breach damages specific performance",
    "divorce maintenance alimony family court",
    "child custody guardianship minor",
    "succession inheritance will probate",
    "labour dispute wrongful termination employment",
    "sexual harassment workplace POSH act",
    "motor accident claim MACT compensation",
    "medical negligence doctor hospital",
    "cyber crime IT act section 66",
    "income tax assessment penalty",
    "GST evasion tax fraud",
    "SEBI insider trading securities",
    "company winding up insolvency IBC",
    "FIR quashing high court section 482",
    "anticipatory bail section 438 CrPC",
    "police custody illegal detention",
    "right to information RTI appeal",
    "environmental pollution PIL",
    "land acquisition compensation",
    "trademark infringement intellectual property",
    "defamation criminal civil",
    "contempt of court",
    "rape section 376 IPC",
    "POCSO child abuse minor",
    "dowry death section 304B",
    "NRI property dispute overseas Indian",
    "armed forces tribunal military service",
]


def ensure_judgments_table(conn):
    """Create judgments table if it doesn't exist (non-destructive)."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS judgments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tid TEXT UNIQUE,
            title TEXT,
            court TEXT,
            date TEXT,
            headline TEXT,
            full_text TEXT,
            topic_query TEXT
        )
    """)
    conn.commit()


def seed_judgments_bulk(conn):
    """Bulk-seed judgments from 40 topics x 3 pages = up to 1,200 judgments."""
    import time as _time

    cur = conn.cursor()
    ensure_judgments_table(conn)

    total_topics = len(BULK_TOPICS)
    total_new = 0
    total_skipped = 0

    for t_idx, topic in enumerate(BULK_TOPICS, start=1):
        print(f"\ntopic {t_idx}/{total_topics}: '{topic}'")

        for page in range(3):
            print(f"  page {page + 1}/3 ...", end=" ", flush=True)
            try:
                resp = ik_post(IK_SEARCH_URL, {"formInput": topic, "pagenum": page})
                results = json.loads(resp)
            except Exception as e:
                print(f"FAILED search: {e}")
                continue

            docs = results.get("docs", [])
            print(f"{len(docs)} docs", flush=True)

            for j_idx, doc in enumerate(docs, start=1):
                tid = str(doc.get("tid", "")).strip()
                if not tid:
                    continue

                # Skip if already in DB
                existing = cur.execute(
                    "SELECT 1 FROM judgments WHERE tid = ?", (tid,)
                ).fetchone()
                if existing:
                    total_skipped += 1
                    continue

                title = doc.get("title", "")
                court = doc.get("docsource", "")
                date = doc.get("publishdate", "")
                headline = strip_html(doc.get("headline", ""))

                # Fetch full text
                full_text = ""
                try:
                    _time.sleep(0.3)
                    doc_resp = ik_post(IK_DOC_URL.format(tid=tid), {})
                    doc_data = json.loads(doc_resp)
                    full_text = strip_html(doc_data.get("doc", ""))
                except Exception as e:
                    full_text = f"[fetch error: {e}]"

                cur.execute(
                    """INSERT INTO judgments (tid, title, court, date, headline, full_text, topic_query)
                       VALUES (?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(tid) DO NOTHING""",
                    (tid, title, court, date, headline, full_text, topic),
                )
                total_new += 1
                print(f"    topic {t_idx}/{total_topics}, page {page + 1}/3, judgment {j_idx} [{tid}] {title[:55]}")

            conn.commit()

    print(f"\n=== seed_judgments_bulk DONE ===")
    print(f"  New judgments inserted : {total_new}")
    print(f"  Already existed (skip) : {total_skipped}")
    return total_new


def main():
    if not TOKEN:
        print("WARNING: INDIANKANOON_TOKEN not set — IK fetch will fail.")

    print(f"\nOpening DB: {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")

    setup_db(conn)

    print("\n--- GitHub statutory JSON sources ---")
    load_github_sources(conn)

    print("\n--- IndianKanoon judgments ---")
    load_ik_judgments(conn)

    # summary
    cur = conn.cursor()
    total_sections = cur.execute("SELECT COUNT(*) FROM sections").fetchone()[0]
    total_judgments = cur.execute("SELECT COUNT(*) FROM judgments").fetchone()[0]
    conn.close()

    db_size = os.path.getsize(DB_PATH)
    print(f"\n=== SUMMARY ===")
    print(f"Total sections  : {total_sections}")
    print(f"Total judgments : {total_judgments}")
    print(f"DB size         : {db_size / 1024:.1f} KB  ({DB_PATH})")


if __name__ == "__main__":
    main()
