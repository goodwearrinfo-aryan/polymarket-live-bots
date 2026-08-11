#!/usr/bin/env python3
"""
embed_lawdb.py — Build semantic embeddings over lawdb.sqlite using local Ollama.
Model: all-minilm (384-dim), $0 cost.
"""

import sqlite3
import struct
import time
import json
import sys
import subprocess
import urllib.request
import urllib.error

OLLAMA_BASE = "http://localhost:11434"
EMBED_MODEL = "all-minilm"
DB_PATH = "/Users/aryanagarwal/Documents/lawdb.sqlite"
MAX_PROMPT_CHARS = 512
SNIPPET_CHARS = 200
PROGRESS_EVERY = 50


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def check_ollama():
    """Return True if Ollama is reachable."""
    try:
        req = urllib.request.Request(f"{OLLAMA_BASE}/api/tags")
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status == 200
    except Exception:
        return False


def pull_model(model: str):
    """Pull model via Ollama REST API (streaming); wait for completion."""
    print(f"[embed_lawdb] Pulling model '{model}' — this may take a minute...")
    payload = json.dumps({"name": model, "stream": False}).encode()
    req = urllib.request.Request(
        f"{OLLAMA_BASE}/api/pull",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            body = r.read().decode()
            # Non-streaming returns a single JSON object
            result = json.loads(body)
            status = result.get("status", "")
            print(f"[embed_lawdb] Pull status: {status}")
    except Exception as e:
        print(f"[embed_lawdb] WARNING: pull request error: {e}")
        print("[embed_lawdb] Continuing — model may already be present.")


def model_present(model: str) -> bool:
    """Check if model is already in local Ollama library."""
    try:
        req = urllib.request.Request(f"{OLLAMA_BASE}/api/tags")
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read().decode())
        names = [m.get("name", "") for m in data.get("models", [])]
        return any(model in n for n in names)
    except Exception:
        return False


def get_embedding(prompt: str) -> list:
    """Call Ollama embeddings API; return list of floats."""
    payload = json.dumps({"model": EMBED_MODEL, "prompt": prompt}).encode()
    req = urllib.request.Request(
        f"{OLLAMA_BASE}/api/embeddings",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.loads(r.read().decode())
    return data["embedding"]


def floats_to_blob(vec: list) -> bytes:
    """Pack float list as little-endian float32 bytes."""
    try:
        import numpy as np
        return np.array(vec, dtype=np.float32).tobytes()
    except ImportError:
        return struct.pack(f"<{len(vec)}f", *vec)


# ---------------------------------------------------------------------------
# DB setup
# ---------------------------------------------------------------------------

def ensure_embeddings_table(conn: sqlite3.Connection):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS embeddings (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            source_type  TEXT    NOT NULL,   -- 'section' or 'judgment'
            source_id    INTEGER NOT NULL,
            text_snippet TEXT,
            embedding    BLOB    NOT NULL,
            UNIQUE(source_type, source_id)
        )
    """)
    conn.commit()


def already_embedded_ids(conn: sqlite3.Connection, source_type: str) -> set:
    rows = conn.execute(
        "SELECT source_id FROM embeddings WHERE source_type = ?", (source_type,)
    ).fetchall()
    return {r[0] for r in rows}


# ---------------------------------------------------------------------------
# Embedding loops
# ---------------------------------------------------------------------------

def embed_sections(conn: sqlite3.Connection) -> tuple[int, int]:
    done_ids = already_embedded_ids(conn, "section")
    rows = conn.execute(
        "SELECT id, act, section_number, title, body FROM sections"
    ).fetchall()

    skipped = 0
    embedded = 0
    errors = 0

    for i, (sid, act, section_number, title, body) in enumerate(rows):
        if sid in done_ids:
            skipped += 1
            continue

        act = act or ""
        section_number = section_number or ""
        title = title or ""
        body = body or ""

        prompt = f"{act} section {section_number}: {title}. {body}"
        prompt = prompt[:MAX_PROMPT_CHARS]
        snippet = prompt[:SNIPPET_CHARS]

        try:
            vec = get_embedding(prompt)
            blob = floats_to_blob(vec)
            conn.execute(
                "INSERT OR IGNORE INTO embeddings (source_type, source_id, text_snippet, embedding) VALUES (?,?,?,?)",
                ("section", sid, snippet, blob),
            )
            conn.commit()
            embedded += 1
        except Exception as e:
            print(f"  [ERROR] section id={sid}: {e}")
            errors += 1
            continue

        total_done = embedded + skipped
        if embedded > 0 and embedded % PROGRESS_EVERY == 0:
            print(f"  [sections] {embedded} embedded so far (row {i+1}/{len(rows)}, {skipped} skipped, {errors} errors)")

    print(f"  [sections] DONE — {embedded} embedded, {skipped} skipped, {errors} errors")
    return embedded, errors


def embed_judgments(conn: sqlite3.Connection) -> tuple[int, int]:
    done_ids = already_embedded_ids(conn, "judgment")
    rows = conn.execute(
        "SELECT id, title, court, date, headline FROM judgments"
    ).fetchall()

    skipped = 0
    embedded = 0
    errors = 0

    for i, (jid, title, court, date, headline) in enumerate(rows):
        if jid in done_ids:
            skipped += 1
            continue

        title = title or ""
        court = court or ""
        date = date or ""
        headline = headline or ""

        prompt = f"{title} ({court}, {date}): {headline}"
        prompt = prompt[:MAX_PROMPT_CHARS]
        snippet = prompt[:SNIPPET_CHARS]

        try:
            vec = get_embedding(prompt)
            blob = floats_to_blob(vec)
            conn.execute(
                "INSERT OR IGNORE INTO embeddings (source_type, source_id, text_snippet, embedding) VALUES (?,?,?,?)",
                ("judgment", jid, snippet, blob),
            )
            conn.commit()
            embedded += 1
        except Exception as e:
            print(f"  [ERROR] judgment id={jid}: {e}")
            errors += 1
            continue

        if embedded > 0 and embedded % PROGRESS_EVERY == 0:
            print(f"  [judgments] {embedded} embedded so far (row {i+1}/{len(rows)}, {skipped} skipped, {errors} errors)")

    print(f"  [judgments] DONE — {embedded} embedded, {skipped} skipped, {errors} errors")
    return embedded, errors


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 60)
    print("embed_lawdb.py — Ollama semantic embeddings over lawdb.sqlite")
    print("=" * 60)

    # 1. Check Ollama
    print(f"\n[1] Checking Ollama at {OLLAMA_BASE} ...")
    if not check_ollama():
        print("\nOllama is NOT running. To start it:")
        print("  ollama serve")
        print("Then re-run this script.")
        sys.exit(1)
    print("    Ollama is running.")

    # 2. Pull model if needed
    print(f"\n[2] Checking model '{EMBED_MODEL}' ...")
    if model_present(EMBED_MODEL):
        print(f"    Model '{EMBED_MODEL}' already present — skipping pull.")
    else:
        pull_model(EMBED_MODEL)

    # 3. Open DB, create table
    print(f"\n[3] Opening {DB_PATH} ...")
    conn = sqlite3.connect(DB_PATH)
    ensure_embeddings_table(conn)
    print("    'embeddings' table ready.")

    # Quick sanity: test one embedding
    print(f"\n[4] Sanity-check embedding (test prompt) ...")
    try:
        test_vec = get_embedding("test")
        print(f"    OK — embedding dim={len(test_vec)}")
    except Exception as e:
        print(f"    FAILED: {e}")
        conn.close()
        sys.exit(1)

    t0 = time.time()

    # 5. Embed sections
    print(f"\n[5] Embedding sections ...")
    sec_embedded, sec_errors = embed_sections(conn)

    # 6. Embed judgments
    print(f"\n[6] Embedding judgments ...")
    jud_embedded, jud_errors = embed_judgments(conn)

    elapsed = time.time() - t0
    total_embedded = sec_embedded + jud_embedded
    total_errors = sec_errors + jud_errors

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"  Sections embedded : {sec_embedded}  (errors: {sec_errors})")
    print(f"  Judgments embedded: {jud_embedded}  (errors: {jud_errors})")
    print(f"  Total embedded    : {total_embedded}")
    print(f"  Total errors      : {total_errors}")
    print(f"  Time taken        : {elapsed:.1f}s  ({elapsed/60:.1f} min)")
    if total_embedded > 0:
        print(f"  Avg per embedding : {elapsed/total_embedded:.2f}s")

    # Verify in DB
    count = conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
    print(f"  Rows in embeddings table: {count}")
    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
