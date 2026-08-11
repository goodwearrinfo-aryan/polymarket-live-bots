#!/usr/bin/env python3
"""
lawdb_labour_gst.py — ADDITIVE, non-destructive ingest of curated Labour-law + GST
sections into ~/Documents/lawdb.sqlite, then embed ONLY the new sections via Ollama
all-minilm (384-dim, same model the rest of the DB uses).

Why curated (not auto-fetched): no clean public JSON exists for these acts (the
civictech GitHub 'ida.json' is actually the Divorce Act). These are hand-curated
summaries of the key operative provisions, clearly attributed as CURATED. They fill
the gap that made salary / labour / GST questions return irrelevant NIA/HMA sections.

Safe to re-run: sections use ON CONFLICT(act, section_number) DO NOTHING; embeddings
use ON CONFLICT(source_type, source_id) DO NOTHING. Never drops or overwrites.
"""
import os
import sqlite3
import json
import struct
import urllib.request

DB_PATH = os.path.expanduser("~/Documents/lawdb.sqlite")
OLLAMA_EMBED_URL = "http://localhost:11434/api/embeddings"
EMBED_MODEL = "all-minilm"
SRC = "curated:labour_gst_2026"

# (act, section_number, title, body)
SECTIONS = [
    # ---------- Payment of Wages Act, 1936 ----------
    ("Payment of Wages Act 1936", "5", "Time of payment of wages",
     "Wages must be paid before the expiry of the 7th day (establishments with <1000 workers) "
     "or 10th day (>=1000 workers) after the last day of the wage period. On termination of "
     "employment, wages must be paid before the expiry of the 2nd working day from termination."),
    ("Payment of Wages Act 1936", "7", "Deductions which may be made from wages",
     "Only deductions authorised by the Act may be made (fines, absence from duty, damage/loss, "
     "house accommodation, recovery of advances, etc.). Total deductions cannot exceed 50% of wages "
     "(75% where co-operative society deductions apply). All other deductions are illegal."),
    ("Payment of Wages Act 1936", "15", "Claims arising out of deductions or delay in payment",
     "An employee whose wages are unlawfully deducted or delayed may apply to the Authority "
     "(usually the Labour Commissioner / prescribed authority) within 12 months. The Authority "
     "can order payment of delayed/deducted wages plus compensation up to 10 times the deducted "
     "amount (delayed wages: compensation up to Rs 3,000)."),
    # ---------- Payment of Gratuity Act, 1972 ----------
    ("Payment of Gratuity Act 1972", "4", "Payment of gratuity",
     "Gratuity is payable to an employee on termination after 5 years of continuous service "
     "(the 5-year rule is waived on death or disablement). Gratuity = last drawn (basic + DA) "
     "x 15/26 x number of completed years of service. Payable within 30 days of it becoming due; "
     "simple interest applies for delayed payment."),
    ("Payment of Gratuity Act 1972", "7", "Determination of the amount of gratuity",
     "The employer must determine and pay gratuity within 30 days of it becoming payable, whether "
     "or not the employee applies. If delayed, interest at the prescribed rate is payable. Disputes "
     "go to the Controlling Authority (Labour Commissioner)."),
    # ---------- Minimum Wages Act, 1948 ----------
    ("Minimum Wages Act 1948", "12", "Payment of minimum rates of wages",
     "Where a minimum wage has been fixed for a scheduled employment, the employer must pay every "
     "employee at least that minimum rate, without unauthorised deductions. Paying below the notified "
     "minimum wage is an offence."),
    ("Minimum Wages Act 1948", "20", "Claims for wages below minimum",
     "An employee paid less than the minimum wage may apply to the prescribed Authority for the "
     "difference plus compensation up to 10 times the shortfall. Claims are generally filed within "
     "6 months."),
    # ---------- Industrial Disputes Act, 1947 ----------
    ("Industrial Disputes Act 1947", "25F", "Conditions precedent to retrenchment of workmen",
     "A workman in continuous service for 1 year cannot be retrenched unless (a) given 1 month's "
     "written notice (or wages in lieu), (b) paid retrenchment compensation of 15 days' average pay "
     "per completed year of service, and (c) notice served on the appropriate Government."),
    ("Industrial Disputes Act 1947", "2A", "Dismissal etc. of an individual workman to be an industrial dispute",
     "The dismissal, discharge, retrenchment or termination of an individual workman is itself an "
     "industrial dispute, and the workman may raise it directly before the Labour Court / Tribunal "
     "even without union backing."),
    ("Industrial Disputes Act 1947", "10", "Reference of disputes to Boards, Courts or Tribunals",
     "The appropriate Government may refer an industrial dispute for adjudication to a Labour Court "
     "or Industrial Tribunal. Conciliation before the Labour Commissioner is usually the first step "
     "before adjudication."),
    # ---------- EPF Act, 1952 ----------
    ("Employees Provident Funds Act 1952", "14B", "Power to recover damages for default in PF contribution",
     "Where an employer defaults in paying provident fund contributions, the authority may recover "
     "damages up to 100% of the arrears, plus interest. Non-payment of deducted PF is a serious "
     "offence and can attract prosecution."),
    ("Employees Provident Funds Act 1952", "7A", "Determination of moneys due from employers",
     "The PF Commissioner may hold an inquiry and determine the amount due from an employer towards "
     "provident fund, and recover it as arrears of land revenue."),
    # ---------- Code on Wages, 2019 ----------
    ("Code on Wages 2019", "17", "Time limit for payment of wages",
     "Wages must be paid: daily-rated - end of shift; weekly - last working day of the week; "
     "fortnightly - within 2 days of period end; monthly - before the 7th of the succeeding month. "
     "On removal/resignation, wages must be paid within 2 working days."),
    ("Code on Wages 2019", "45", "Claims and adjudication under the Code",
     "Claims for unpaid or under-paid wages, bonus or equal remuneration are decided by the authority "
     "appointed under the Code. Limitation for filing a claim is 3 years, and compensation up to 10 "
     "times the claim amount may be awarded."),
    # ---------- CGST Act, 2017 (GST) ----------
    ("CGST Act 2017", "22", "Persons liable for registration",
     "Every supplier whose aggregate turnover in a financial year exceeds Rs 40 lakh for goods "
     "(Rs 20 lakh for services; Rs 20 lakh / Rs 10 lakh in special-category states) must register "
     "under GST. Registration is also mandatory for inter-state suppliers and e-commerce operators."),
    ("CGST Act 2017", "16", "Eligibility and conditions for taking input tax credit",
     "A registered person can claim input tax credit (ITC) on inputs used in the course of business, "
     "subject to possessing a valid tax invoice, receipt of goods/services, the supplier having paid "
     "the tax, and filing the return. ITC on a given invoice must be claimed by 30 November of the "
     "following financial year."),
    ("CGST Act 2017", "47", "Levy of late fee for delayed returns",
     "Failure to file GSTR-1 / GSTR-3B by the due date attracts a late fee of Rs 50 per day "
     "(Rs 20 per day for nil returns), split between CGST and SGST, subject to prescribed caps. "
     "Interest at 18% p.a. also applies on the outstanding tax."),
    ("CGST Act 2017", "73", "Demand where tax not paid (no fraud)",
     "Where tax is unpaid / short-paid or ITC wrongly availed for reasons other than fraud, the "
     "officer issues a show-cause notice. If paid before notice, no penalty; if paid within 30 days "
     "of the notice, no penalty; otherwise penalty of 10% of tax or Rs 10,000, whichever is higher."),
    ("CGST Act 2017", "74", "Demand where tax not paid (fraud / wilful misstatement)",
     "Where tax is unpaid due to fraud, wilful misstatement or suppression of facts, a higher penalty "
     "regime applies (up to 100% of tax). Voluntary payment before notice attracts a reduced 15% "
     "penalty; within 30 days of notice, 25%."),
]


def _embed(text):
    payload = json.dumps({"model": EMBED_MODEL, "prompt": text}).encode()
    req = urllib.request.Request(
        OLLAMA_EMBED_URL, data=payload,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())["embedding"]


def _vec_to_blob(vec):
    return struct.pack(f"<{len(vec)}f", *vec)


def main():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    added, embedded = 0, 0
    for act, num, title, body in SECTIONS:
        cur.execute(
            """INSERT INTO sections (act, chapter, section_number, title, body, source_url)
               VALUES (?, '', ?, ?, ?, ?)
               ON CONFLICT(act, section_number) DO NOTHING""",
            (act, num, title, body, SRC),
        )
        if cur.rowcount:
            added += 1
        # get the row id (whether just-inserted or pre-existing)
        row = cur.execute(
            "SELECT id FROM sections WHERE act=? AND section_number=?", (act, num)
        ).fetchone()
        if not row:
            continue
        sid = row[0]
        # embed only if no embedding yet
        exists = cur.execute(
            "SELECT 1 FROM embeddings WHERE source_type='section' AND source_id=?", (sid,)
        ).fetchone()
        if exists:
            continue
        snippet = f"{title}. {body}"
        vec = _embed(snippet)
        cur.execute(
            """INSERT INTO embeddings (source_type, source_id, text_snippet, embedding)
               VALUES ('section', ?, ?, ?)
               ON CONFLICT(source_type, source_id) DO NOTHING""",
            (sid, snippet[:500], _vec_to_blob(vec)),
        )
        embedded += 1
        print(f"  + {act} s.{num} — {title}")

    con.commit()
    total_sec = cur.execute("SELECT COUNT(*) FROM sections").fetchone()[0]
    total_emb = cur.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
    con.close()
    print(f"\n=== DONE ===")
    print(f"  New sections added   : {added}")
    print(f"  New embeddings made  : {embedded}")
    print(f"  Total sections now   : {total_sec}")
    print(f"  Total embeddings now : {total_emb}")


if __name__ == "__main__":
    main()
