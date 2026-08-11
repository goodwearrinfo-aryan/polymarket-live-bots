#!/usr/bin/env python3
"""
lawyer_db_train.py — periodic "training" agent for the Personal Lawyer knowledge base.

Non-destructive and idempotent (safe to re-run on a schedule):
  1. Re-runs the bulk IndianKanoon judgment seeder (dedups by tid — only NEW judgments
     since the last run are inserted; catches freshly published case law).
  2. Embeds any new sections/judgments via Ollama (skips already-embedded rows).
  3. If anything actually changed, hands off to lawyer_deploy_sync.sh to sync + redeploy.

Logs a one-line summary to stderr/stdout for the launchd job's log.
"""
import os
import sys
import sqlite3
import subprocess

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main():
    os.environ.setdefault(
        "INDIANKANOON_TOKEN",
        next((l.split("=", 1)[1].strip() for l in open(os.path.expanduser("~/Documents/polymarket/.env"))
              if l.startswith("INDIANKANOON_TOKEN=")), "")
    )

    import extract_lawdb as e
    conn = sqlite3.connect(e.DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    e.ensure_judgments_table(conn)

    before = conn.execute("SELECT COUNT(*) FROM judgments").fetchone()[0]
    new_count = e.seed_judgments_bulk(conn)
    after = conn.execute("SELECT COUNT(*) FROM judgments").fetchone()[0]
    conn.close()

    print(f"[lawyer_db_train] judgments before={before} after={after} new={new_count}")

    if new_count == 0:
        print("[lawyer_db_train] no new case law — skipping embed/deploy")
        return

    # embed only the new rows (embed_lawdb.py skips already-embedded ones)
    print("[lawyer_db_train] embedding new rows...")
    subprocess.run([sys.executable, os.path.expanduser("~/Documents/polymarket/embed_lawdb.py")],
                    check=True)

    print("[lawyer_db_train] handing off to deploy_sync...")
    subprocess.run(["bash", os.path.expanduser("~/Documents/polymarket/lawyer_deploy_sync.sh")],
                    check=True)
    print("[lawyer_db_train] done")


if __name__ == "__main__":
    main()
