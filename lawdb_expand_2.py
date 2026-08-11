#!/usr/bin/env python3
"""
lawdb_expand_2.py — ADDITIVE curated expansion #2: high-value statutes not yet in the DB.
Adds key sections of BNS 2023 (the new criminal code), Arbitration & Conciliation Act,
RERA 2016, POSH Act 2013, and the IBC 2016. Same non-destructive pattern as
lawdb_labour_gst.py — ON CONFLICT DO NOTHING; embeds only new sections via Ollama all-minilm.

Curated summaries of key operative provisions, tagged SRC. Not full statute text.
"""
import os, sqlite3, json, struct, urllib.request

DB_PATH = os.path.expanduser("~/Documents/lawdb.sqlite")
OLLAMA_EMBED_URL = "http://localhost:11434/api/embeddings"
EMBED_MODEL = "all-minilm"
SRC = "curated:expand2_2026"

SECTIONS = [
    # ---------- Bharatiya Nyaya Sanhita (BNS) 2023 — replaced IPC on 1 July 2024 ----------
    ("Bharatiya Nyaya Sanhita 2023", "103", "Punishment for murder",
     "Whoever commits murder shall be punished with death or imprisonment for life, and a fine. "
     "(Corresponds to the former Section 302 IPC.)"),
    ("Bharatiya Nyaya Sanhita 2023", "111", "Organised crime",
     "Defines and punishes organised crime (syndicate activity, extortion, contract killing, "
     "cyber-crime, trafficking) — a new offence with no direct IPC equivalent. Punishment ranges "
     "up to death/life where it causes death, else 5 years to life."),
    ("Bharatiya Nyaya Sanhita 2023", "115", "Voluntarily causing hurt",
     "Whoever voluntarily causes hurt is punished with imprisonment up to 1 year, or fine up to "
     "Rs 10,000, or both. (Corresponds to the former Section 323 IPC.)"),
    ("Bharatiya Nyaya Sanhita 2023", "318", "Cheating",
     "Cheating and dishonestly inducing delivery of property is punishable with imprisonment up to "
     "7 years and fine. (Corresponds to the former Section 420 IPC.)"),
    ("Bharatiya Nyaya Sanhita 2023", "63", "Rape (definition)",
     "Defines the offence of rape. (Corresponds to the former Section 375 IPC.)"),
    ("Bharatiya Nyaya Sanhita 2023", "64", "Punishment for rape",
     "Rigorous imprisonment of not less than 10 years, extendable to life, and fine. "
     "(Corresponds to the former Section 376 IPC.)"),
    ("Bharatiya Nyaya Sanhita 2023", "85", "Cruelty by husband or relatives",
     "Subjecting a woman to cruelty by her husband or his relatives is punishable with imprisonment "
     "up to 3 years and fine. (Corresponds to the former Section 498A IPC.)"),
    ("Bharatiya Nyaya Sanhita 2023", "351", "Criminal intimidation",
     "Threatening another with injury to person, reputation or property; punishable with up to 2 "
     "years, or fine, or both (more where the threat is of death/grievous hurt). (Former Section 506 IPC.)"),
    # ---------- Arbitration and Conciliation Act, 1996 ----------
    ("Arbitration and Conciliation Act 1996", "7", "Arbitration agreement",
     "An arbitration agreement must be in writing (a signed document, exchange of letters/emails, "
     "or a contract clause). It is the foundation of jurisdiction for the arbitral tribunal."),
    ("Arbitration and Conciliation Act 1996", "8", "Power to refer parties to arbitration",
     "A judicial authority before which an action is brought in a matter covered by an arbitration "
     "agreement shall refer the parties to arbitration if a party so applies not later than "
     "submitting its first statement on the substance of the dispute."),
    ("Arbitration and Conciliation Act 1996", "11", "Appointment of arbitrators",
     "Parties are free to agree a procedure for appointing arbitrator(s). Failing agreement, a party "
     "may apply to the Supreme Court (international) or High Court (domestic) to make the appointment."),
    ("Arbitration and Conciliation Act 1996", "34", "Application for setting aside an arbitral award",
     "An award may be set aside only on limited grounds (incapacity, invalid agreement, lack of "
     "notice, award beyond scope, or conflict with public policy/patent illegality). Application must "
     "be made within 3 months (extendable by 30 days) of receiving the award."),
    ("Arbitration and Conciliation Act 1996", "36", "Enforcement of award",
     "Where the time to challenge under s.34 has expired or the challenge is refused, the award is "
     "enforced as a decree of the court. A mere s.34 challenge no longer automatically stays enforcement."),
    # ---------- Real Estate (Regulation and Development) Act, 2016 (RERA) ----------
    ("RERA 2016", "18", "Return of amount and compensation for delay",
     "If the promoter fails to hand over possession by the agreed date, the allottee may withdraw and "
     "claim a full refund with interest, or (if staying) claim interest for every month of delay until "
     "possession. This is the core buyer-protection remedy for delayed projects."),
    ("RERA 2016", "13", "No deposit above 10% without agreement",
     "A promoter cannot accept more than 10% of the flat cost as an advance/application fee without "
     "first entering into a written agreement for sale registered under the Act."),
    ("RERA 2016", "31", "Filing of complaints with the Authority or adjudicating officer",
     "Any aggrieved person may file a complaint with the RERA Authority or adjudicating officer for "
     "violations of the Act against a promoter, allottee or agent."),
    # ---------- Sexual Harassment of Women at Workplace (POSH) Act, 2013 ----------
    ("POSH Act 2013", "4", "Constitution of Internal Complaints Committee",
     "Every employer of a workplace with 10 or more workers must constitute an Internal Complaints "
     "Committee (ICC), headed by a senior woman employee, with an external member from an NGO/expert "
     "on women's issues."),
    ("POSH Act 2013", "9", "Complaint of sexual harassment",
     "An aggrieved woman may make a written complaint to the ICC within 3 months of the incident "
     "(extendable by a further 3 months for sufficient cause)."),
    ("POSH Act 2013", "11", "Inquiry into complaint",
     "The ICC inquires into the complaint following principles of natural justice, completes the "
     "inquiry within 90 days, and forwards findings to the employer, who must act within 60 days."),
    ("POSH Act 2013", "19", "Duties of employer",
     "The employer must provide a safe working environment, display the penal consequences of "
     "harassment, organise awareness, constitute the ICC, and treat sexual harassment as misconduct."),
    # ---------- Insolvency and Bankruptcy Code, 2016 (IBC) ----------
    ("Insolvency and Bankruptcy Code 2016", "7", "Initiation of CIRP by financial creditor",
     "A financial creditor may file an application before the NCLT to initiate the Corporate "
     "Insolvency Resolution Process on a default of Rs 1 crore or more."),
    ("Insolvency and Bankruptcy Code 2016", "9", "Application by operational creditor",
     "On default, an operational creditor may, after serving a demand notice and waiting 10 days, "
     "apply to the NCLT to initiate CIRP against the corporate debtor."),
    ("Insolvency and Bankruptcy Code 2016", "12", "Time-limit for completion of CIRP",
     "The CIRP must be completed within 180 days (extendable by 90 days), with an overall outer limit "
     "of 330 days including litigation."),
]


def _embed(text):
    payload = json.dumps({"model": EMBED_MODEL, "prompt": text}).encode()
    req = urllib.request.Request(OLLAMA_EMBED_URL, data=payload,
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())["embedding"]


def main():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    added = embedded = 0
    for act, num, title, body in SECTIONS:
        cur.execute(
            """INSERT INTO sections (act, chapter, section_number, title, body, source_url)
               VALUES (?, '', ?, ?, ?, ?) ON CONFLICT(act, section_number) DO NOTHING""",
            (act, num, title, body, SRC))
        if cur.rowcount:
            added += 1
        row = cur.execute("SELECT id FROM sections WHERE act=? AND section_number=?",
                          (act, num)).fetchone()
        if not row:
            continue
        sid = row[0]
        if cur.execute("SELECT 1 FROM embeddings WHERE source_type='section' AND source_id=?",
                       (sid,)).fetchone():
            continue
        snippet = f"{title}. {body}"
        vec = _embed(snippet)
        cur.execute(
            """INSERT INTO embeddings (source_type, source_id, text_snippet, embedding)
               VALUES ('section', ?, ?, ?) ON CONFLICT(source_type, source_id) DO NOTHING""",
            (sid, snippet[:500], struct.pack(f"<{len(vec)}f", *vec)))
        embedded += 1
        print(f"  + {act} s.{num} — {title}")
    con.commit()
    total = cur.execute("SELECT COUNT(*) FROM sections").fetchone()[0]
    con.close()
    print(f"\n=== DONE === added {added} sections, embedded {embedded}. Total sections: {total}")


if __name__ == "__main__":
    main()
