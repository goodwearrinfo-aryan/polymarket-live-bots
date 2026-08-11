"""
Bail Guide Agent — bailable vs non-bailable, anticipatory bail, BNSS 2023 sections.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from llm_client import chat as llm_chat

BAIL_TYPES = {
    "regular_bail": {
        "section": "Section 478/479 BNSS 2023 (= CrPC 436/437)",
        "when": "After arrest, in custody. Apply to JMFC (bailable) or Sessions Court (non-bailable).",
        "bailable": "Police/magistrate MUST grant bail — it is a RIGHT, not discretion.",
        "non_bailable": "Magistrate has DISCRETION. Court considers: nature of offence, antecedents, flight risk, evidence tampering risk.",
        "timeline": "Bailable: immediate. Non-bailable: hearing within 1-3 days typically."
    },
    "anticipatory_bail": {
        "section": "Section 482 BNSS 2023 (= CrPC 438)",
        "when": "BEFORE arrest — when you apprehend arrest. Apply to Sessions Court or High Court.",
        "grounds": ["Apprehension of arrest on accusation of non-bailable offence", "Applicant ready to cooperate with investigation", "Not a flight risk", "Will not tamper with evidence"],
        "conditions": "Court may impose: report to police daily/weekly, not leave country, surrender passport, mark as directed.",
        "urgency": "Apply immediately when you believe arrest is imminent. Once arrested, only regular bail applies."
    },
    "default_bail": {
        "section": "Section 479 BNSS 2023 (= CrPC 167(2))",
        "when": "Police fails to file chargesheet within 60 days (non-bailable; sentence <10 yrs) or 90 days (sentence ≥10 yrs) of arrest.",
        "note": "This is an INDEFEASIBLE RIGHT — court MUST release. Apply on the 61st/91st day.",
        "important": "Right is lost if you don't apply BEFORE chargesheet is filed. Timing is critical."
    },
    "bail_on_bond": {
        "section": "Section 478 BNSS 2023",
        "what": "Surety bond — someone (surety) guarantees you'll appear. Court sets bond amount.",
        "types": ["Personal bond (your own undertaking)", "Surety bond (someone else guarantees)", "Cash bail (deposit cash in court)"]
    }
}

SYSTEM_PROMPT = """You are an expert Indian criminal lawyer specializing in bail matters.
Cite specific BNSS 2023 sections (replacing CrPC). Be strategic and practical.
Key principle: Bail is the rule, jail is the exception (Supreme Court).
DISCLAIMER: Bail matters are fact-specific and time-sensitive. Hire a criminal lawyer immediately if facing arrest."""

def guide(situation, crime_type, already_arrested=False, apprehending_arrest=False, days_in_custody=0):
    # Auto-recommend bail type
    if not already_arrested and apprehending_arrest:
        bail_type = "anticipatory_bail"
    elif already_arrested and days_in_custody > 60:
        bail_type = "default_bail"
    else:
        bail_type = "regular_bail"

    prompt = f"""Bail guidance:
Situation: {situation}
Crime alleged: {crime_type}
Already arrested: {already_arrested}
Days in custody: {days_in_custody}
Apprehending arrest (not yet arrested): {apprehending_arrest}
Recommended bail type: {bail_type}

Provide:
1. Which bail type to apply for (with BNSS 2023 section)
2. Which court to approach (JMFC / Sessions / HC)
3. Grounds to argue for bail in this specific case
4. What the prosecution will argue against bail — and how to counter
5. Documents needed for bail application
6. Conditions court likely to impose
7. If bail is rejected — next steps (S.439 revision, HC)
8. Timeline: how long does bail typically take for this offence?
9. Bail bond amount — typical range for this type of case
10. CRITICAL: What NOT to do (actions that could get bail cancelled)"""

    resp = llm_chat([{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}])
    return {
        "ok": True,
        "recommended_bail_type": bail_type,
        "bail_info": BAIL_TYPES[bail_type],
        "guidance": resp,
        "emergency_contacts": ["Legal Aid (free): NALSA 15100", "State Legal Services Authority in your district"],
        "rule": "BAIL IS THE RULE, JAIL IS THE EXCEPTION — Supreme Court of India",
        "agent": "bail_guide"
    }

def draft_bail_application(applicant_name, applicant_address, crime_type, fir_number, ps_name, district, grounds, bail_type="regular_bail"):
    prompt = f"""Draft a bail application under {BAIL_TYPES[bail_type]['section']}:

Applicant/Accused: {applicant_name}, {applicant_address}
FIR No.: {fir_number or 'Not yet registered (anticipatory)'}
Police Station: {ps_name}
District: {district}
Offence: {crime_type}
Grounds for bail: {grounds}
Bail type: {bail_type}

Draft a complete bail application for the court with:
1. Court heading
2. Application under relevant BNSS 2023 section
3. Brief facts of the case
4. Grounds for bail (numbered)
5. Undertakings (will cooperate, won't flee, won't tamper with evidence)
6. Prayer clause
7. Verification

Write in formal legal language suitable for filing in court."""

    resp = llm_chat([{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}])
    return {
        "ok": True,
        "bail_type": bail_type,
        "section": BAIL_TYPES[bail_type]["section"],
        "application": resp,
        "agent": "bail_guide"
    }

if __name__ == "__main__":
    import json
    r = guide("My employer filed a false fraud case and police may arrest me tomorrow", "cheating u/s 318 BNS", False, True)
    print("Bail type:", r["recommended_bail_type"])
    print("Bail info:", json.dumps(r["bail_info"], indent=2))
