"""
FIR Guide — how to file FIR, what to do if police refuse, rights of accused.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from llm_client import chat as llm_chat

RIGHTS_OF_ACCUSED = """
Under Indian Constitution + BNSS 2023 / BNS 2023:
- Right to know grounds of arrest (Article 22, BNSS S.47)
- Right to inform family/friend within 8-12 hours (BNSS S.58)
- Right to legal counsel of choice (Article 22(1))
- Right to free legal aid if cannot afford lawyer (Legal Services Authority Act)
- Right to be produced before Magistrate within 24 hours (Article 22(2), BNSS S.57)
- Right to bail in bailable offences (BNSS S.478)
- Right to remain silent (cannot be forced to be witness against self — Article 20(3))
- Right against double jeopardy (Article 20(2))
- Right to medical examination within 24 hours if requested (BNSS S.53)
"""

FIR_STEPS = {
    "normal": [
        "Go to the police station having jurisdiction (where offence occurred)",
        "Give a written complaint to the Station House Officer (SHO)",
        "Insist FIR be registered — police MUST register FIR for cognizable offences (SC: Lalita Kumari 2013)",
        "Get a free copy of the FIR immediately (Section 173(2) BNSS)",
        "Note the FIR number",
        "Follow up on investigation progress"
    ],
    "if_refused": [
        "Send written complaint by speed post to SP/SSP of the district",
        "Email complaint to SP + DIG + IGP (all IPS officers have official email)",
        "File complaint before Judicial Magistrate under Section 175(3) BNSS (formerly 156(3) CrPC) — Magistrate can order FIR registration",
        "File writ petition in High Court (in extreme cases)",
        "Approach State Human Rights Commission if police misconduct",
        "NHRC (National Human Rights Commission) for serious violations"
    ],
    "cyber_crime": [
        "File at cybercrime.gov.in (national portal — works for any state)",
        "Call 1930 (National Cyber Crime Helpline) if financial fraud within 24 hours",
        "Also file at local police cyber cell for parallel action",
        "Preserve all screenshots, transaction IDs, chat records"
    ]
}

SYSTEM_PROMPT = """You are an expert Indian criminal lawyer.
Cite specific sections of BNS 2023 (replaces IPC), BNSS 2023 (replaces CrPC), and BSA 2023 (replaces IEA).
Be practical and actionable. Include:
- Which offence sections apply under BNS 2023
- Whether it is a cognizable/bailable offence
- Steps to file FIR
- What evidence to preserve
- Rights of the complainant
DISCLAIMER: AI guidance. Consult a criminal lawyer for your case."""

def guide(situation, state=None, already_tried_filing=False):
    prompt = f"""Indian FIR and criminal complaint guidance for:
Situation: {situation}
State: {state or 'not specified'}
Already tried filing FIR: {already_tried_filing}

Provide:
1. Is this a cognizable offence (FIR mandatory)?
2. Applicable BNS 2023 sections
3. Which police station has jurisdiction
4. Steps to file FIR / what to say
5. Evidence to preserve NOW
6. {'Steps if police refuse FIR' if already_tried_filing else 'What happens after FIR'}
7. Timeline of what to expect"""

    resp = llm_chat([{"role":"system","content":SYSTEM_PROMPT},{"role":"user","content":prompt}])
    return {
        "ok": True,
        "guidance": resp,
        "rights_of_accused": RIGHTS_OF_ACCUSED,
        "fir_steps": FIR_STEPS["if_refused" if already_tried_filing else "normal"],
        "emergency": "For cyber fraud: Call 1930 IMMEDIATELY. Every hour of delay reduces recovery chances.",
        "agent": "fir_guide"
    }

if __name__ == "__main__":
    r = guide("My employer has not paid salary for 3 months despite promises", "Delhi", False)
    print(r["guidance"][:500])
