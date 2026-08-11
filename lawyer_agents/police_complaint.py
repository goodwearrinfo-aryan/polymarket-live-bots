"""
Police Complaint & FIR Drafting Agent — draft complaint letters, FIR text, escalation.
Covers all major crime types under BNS 2023 (replaces IPC).
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from llm_client import chat as llm_chat

# BNS 2023 (Bharatiya Nyaya Sanhita) — key sections replacing IPC
BNS_SECTIONS = {
    "cheating_fraud": {
        "section": "Section 318 BNS 2023 (= IPC 420)",
        "punishment": "7 years imprisonment + fine",
        "cognizable": True,
        "bailable": False,
        "description": "Cheating by personation, fraudulently inducing delivery of property"
    },
    "online_fraud": {
        "section": "Section 318 BNS + Section 66D IT Act 2000",
        "punishment": "7 years + fine (BNS); 3 years + ₹1L fine (IT Act)",
        "cognizable": True,
        "bailable": False,
        "description": "Cheating by personation via computer/online"
    },
    "theft": {
        "section": "Section 303 BNS 2023 (= IPC 379)",
        "punishment": "3 years + fine",
        "cognizable": True,
        "bailable": False,
        "description": "Dishonestly takes movable property without consent"
    },
    "robbery": {
        "section": "Section 309 BNS 2023 (= IPC 392)",
        "punishment": "10 years + fine; 14 years if highway",
        "cognizable": True,
        "bailable": False,
        "description": "Theft + force/threat against person"
    },
    "assault": {
        "section": "Section 115 BNS 2023 (= IPC 352)",
        "punishment": "3 months + ₹500 fine (simple); Section 118 for grievous hurt = 7 years",
        "cognizable": True,
        "bailable": True,
        "description": "Physical assault causing hurt"
    },
    "domestic_violence": {
        "section": "Section 85/86 BNS 2023 (= IPC 498A) + PWDVA 2005",
        "punishment": "3 years + fine (BNS). PWDVA: Protection Order, residence order, monetary relief",
        "cognizable": True,
        "bailable": False,
        "description": "Cruelty by husband or relatives; domestic violence"
    },
    "stalking": {
        "section": "Section 78 BNS 2023 (= IPC 354D)",
        "punishment": "1-3 years first offence; 3-5 years repeat",
        "cognizable": True,
        "bailable": True,
        "description": "Following, monitoring, contacting person who has indicated no interest"
    },
    "harassment_women": {
        "section": "Section 74/75/76/77 BNS 2023 (= IPC 354/354A/354B/354C)",
        "punishment": "1-7 years depending on nature",
        "cognizable": True,
        "bailable": True,
        "description": "Sexual harassment, voyeurism, outraging modesty"
    },
    "defamation": {
        "section": "Section 356 BNS 2023 (= IPC 499/500) — criminal; also civil tort",
        "punishment": "2 years + fine (criminal); damages (civil)",
        "cognizable": False,
        "bailable": True,
        "description": "False statement of fact harming reputation, published to third parties"
    },
    "cybercrime_hacking": {
        "section": "Section 66 IT Act 2000 + Section 316/317 BNS 2023",
        "punishment": "3 years + ₹5L fine (IT Act); 7 years (BNS if data theft)",
        "cognizable": True,
        "bailable": False,
        "description": "Unauthorized computer access, hacking, data theft"
    },
    "extortion": {
        "section": "Section 308 BNS 2023 (= IPC 383/384)",
        "punishment": "3-10 years + fine",
        "cognizable": True,
        "bailable": False,
        "description": "Intentionally putting person in fear to dishonestly induce delivery"
    },
    "blackmail": {
        "section": "Section 308 BNS 2023 + Section 67A IT Act (if digital content)",
        "punishment": "3-7 years",
        "cognizable": True,
        "bailable": False,
        "description": "Threatening to reveal private information; revenge porn"
    },
    "property_trespass": {
        "section": "Section 329/330 BNS 2023 (= IPC 441/442)",
        "punishment": "3 months to 2 years",
        "cognizable": False,
        "bailable": True,
        "description": "Criminal trespass on property; house trespass"
    },
    "cheque_bounce": {
        "section": "Section 138 Negotiable Instruments Act 1881",
        "punishment": "2 years + fine up to 2x cheque amount",
        "cognizable": False,
        "bailable": True,
        "description": "Cheque returned unpaid for insufficient funds. File in JMFC court, not police."
    },
    "missing_person": {
        "section": "Police must register Zero FIR immediately under BNSS (no section needed)",
        "punishment": "N/A — this is a missing person report",
        "cognizable": True,
        "bailable": None,
        "description": "Missing person — police must act immediately, especially if child/woman"
    },
    "kidnapping": {
        "section": "Section 137/140 BNS 2023 (= IPC 359/363)",
        "punishment": "7 years to life (depending on purpose)",
        "cognizable": True,
        "bailable": False,
        "description": "Taking person out of legal guardianship without consent"
    },
    "consumer_fraud": {
        "section": "Consumer Protection Act 2019 + Section 318 BNS (if criminal fraud)",
        "punishment": "Civil: refund/compensation/penalty. Criminal: 7 years",
        "cognizable": True,
        "bailable": False,
        "description": "Selling defective goods, fake products, misleading advertising"
    }
}

SYSTEM_PROMPT = """You are an expert Indian criminal lawyer drafting police complaints.
Use precise legal language but also make the complaint clear and factual.
Always cite BNS 2023 sections (not old IPC — BNS replaced IPC in July 2024).
Draft in this structure:
1. TO: (addressing officer)
2. FROM: (complainant details)
3. SUBJECT: heading
4. FACTS: numbered points, chronological
5. OFFENCES COMMITTED: specific BNS/IT Act sections
6. RELIEF SOUGHT: register FIR, investigate, arrest, recover property
7. DECLARATION: truth declaration
DISCLAIMER: Verify current BNS 2023 sections with a criminal lawyer before filing."""

def get_bns_info(crime_type):
    return BNS_SECTIONS.get(crime_type.lower().replace(" ", "_"))

def draft_complaint(
    crime_type, facts, complainant_name, complainant_address,
    accused_name="Unknown", accused_address="Unknown",
    incident_date="", incident_location="", witnesses="None",
    property_lost="", state="", lang=None
):
    bns = BNS_SECTIONS.get(crime_type.lower().replace(" ", "_"), {})
    section_ref = bns.get("section", f"applicable BNS 2023 sections for {crime_type}")

    if crime_type == "cheque_bounce":
        return {
            "ok": False,
            "note": "Cheque bounce (S.138 NI Act) is NOT a police FIR matter — it is filed DIRECTLY in the Judicial Magistrate Court. Use the legal_notice agent to send a demand notice first (mandatory 30-day window), then file complaint in JMFC within 30 days of notice expiry.",
            "correct_route": "Legal notice → Wait 15 days → File in JMFC court → NOT police",
            "agent": "police_complaint"
        }

    prompt = f"""Draft a formal police complaint / FIR request for filing at the police station.

Complainant: {complainant_name}, {complainant_address}
Accused: {accused_name}, {accused_address or 'address unknown'}
Crime type: {crime_type}
BNS section: {section_ref}
Date of incident: {incident_date or 'as described in facts'}
Location: {incident_location}
Witnesses: {witnesses}
Property/loss involved: {property_lost or 'as described'}
State: {state}

FACTS AS GIVEN BY COMPLAINANT:
{facts}

Draft a COMPLETE, formal police complaint letter suitable for submission to the Station House Officer (SHO).
Include:
1. Complete address block (To: SHO/Commissioner of Police/SP)
2. Subject line
3. Numbered factual paragraphs in chronological order
4. Specific BNS 2023 sections violated (NOT old IPC sections)
5. Specific reliefs: Register FIR, arrest/investigate accused, {'recover property worth stated amount' if property_lost else 'take appropriate legal action'}
6. For online crimes: add request to preserve digital evidence + write to CERT-In
7. Solemn declaration at end
8. Space for date, place, signature

Make it assertive but factual. No emotional language."""

    resp = llm_chat([{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}])

    result = {
        "ok": True,
        "crime_type": crime_type,
        "bns_reference": bns,
        "draft_complaint": resp,
        "filing_tips": _filing_tips(crime_type, state),
        "agent": "police_complaint"
    }

    if lang and lang != "english":
        try:
            from lang import from_english
            result["draft_complaint_translated"] = from_english(resp, lang)
        except:
            pass
    return result

def _filing_tips(crime_type, state):
    tips = [
        "Make 3 copies — one for police, one for yourself (get it stamped/signed), one backup",
        "Police MUST register FIR for cognizable offences (Supreme Court: Lalita Kumari 2013)",
        "If SHO refuses: send complaint by speed post to SP/SSP + email to IGP",
        "Get FIR copy FREE immediately after registration (Section 173(2) BNSS)",
        "For online crimes: ALSO file at cybercrime.gov.in (parallel complaint)"
    ]
    if crime_type in ["domestic_violence", "stalking", "harassment_women"]:
        tips.insert(0, "URGENT: Go to nearest police station OR call 100 / Women Helpline 181")
        tips.insert(1, "Can also file directly with Judicial Magistrate under PWDVA 2005")
    if crime_type in ["online_fraud", "cybercrime_hacking", "blackmail"]:
        tips.insert(0, "FIRST: Call 1930 (National Cyber Crime Helpline) — time-sensitive for financial fraud")
        tips.insert(1, "Preserve ALL digital evidence: screenshots, transaction IDs, emails, URLs")
    if crime_type == "missing_person":
        tips.insert(0, "EMERGENCY: Police must register missing person report IMMEDIATELY (no 24-hour wait rule — that is a myth)")
        tips.insert(1, "If refused: call 100 + contact SP/DM directly")
    return tips

def draft_magistrate_petition(complainant_name, complainant_address, accused_name, crime_type, facts, police_refused=True):
    """Draft Section 175(3) BNSS petition when police refuse to register FIR."""
    prompt = f"""Draft a petition under Section 175(3) of the Bharatiya Nagarik Suraksha Sanhita (BNSS) 2023
[formerly Section 156(3) CrPC] to the Judicial Magistrate First Class (JMFC) requesting direction to police to register FIR.

Petitioner: {complainant_name}, {complainant_address}
Accused: {accused_name}
Crime: {crime_type}
Police refused FIR: {police_refused}

Facts:
{facts}

Include:
1. Court heading (IN THE COURT OF JUDICIAL MAGISTRATE FIRST CLASS)
2. Application under Section 175(3) BNSS 2023
3. Brief background
4. Factual paragraphs
5. That police refused/failed to register FIR despite complaint
6. Prayer: Direction to concerned SHO to register FIR and investigate
7. Verification clause

This is more powerful than a police complaint — Magistrate CAN ORDER FIR registration."""

    resp = llm_chat([{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}])
    return {
        "ok": True,
        "petition_type": "Section 175(3) BNSS 2023 (formerly S.156(3) CrPC)",
        "petition": resp,
        "note": "File this in the JMFC court in whose jurisdiction the crime occurred. No court fee for Section 175(3).",
        "agent": "police_complaint"
    }

def draft_sp_complaint(complainant_name, crime_type, fir_refused_at, facts, sp_district):
    """Draft complaint to SP/SSP when SHO refuses FIR."""
    prompt = f"""Draft a formal complaint to the Superintendent of Police (SP) / Senior Superintendent of Police (SSP)
when the Station House Officer (SHO) at {fir_refused_at} police station refused to register FIR.

Complainant: {complainant_name}
Crime: {crime_type}
Facts: {facts}
SP District: {sp_district}

Structure:
1. To: SP/SSP, {sp_district} District
2. Subject: Complaint against SHO, {fir_refused_at} PS for refusal to register FIR
3. Reference: Lalita Kumari v. Govt. of UP (2013) 4 SCC 1 — mandatory FIR registration
4. Factual background
5. Date and manner of refusal by SHO
6. Request: Direct SHO to register FIR OR take up the matter personally
7. Also mention: sending copy to DIG/IGP/DM"""

    resp = llm_chat([{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}])
    return {
        "ok": True,
        "complaint_to": f"SP/SSP, {sp_district}",
        "draft": resp,
        "send_copies_to": ["DIG of range", "IGP of zone", "District Magistrate", "State Human Rights Commission if police misconduct"],
        "parallel_action": "Also file petition u/s 175(3) BNSS before JMFC simultaneously",
        "agent": "police_complaint"
    }

if __name__ == "__main__":
    import json
    info = get_bns_info("online_fraud")
    print(json.dumps(info, indent=2))
    tips = _filing_tips("online_fraud", "Maharashtra")
    print("Tips:", tips[:2])
