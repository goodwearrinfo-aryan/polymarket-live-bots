"""
Banking & UPI Fraud Recovery Agent — RBI Ombudsman, bank escalation, cybercrime.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from llm_client import chat as llm_chat

ESCALATION_PATH = {
    "upi_fraud": [
        ("STEP 1 — IMMEDIATE (within hours)", "Call bank helpline. Request FREEZE on beneficiary account. Call 1930 (National Cybercrime Helpline)."),
        ("STEP 2 — Same day", "File complaint at cybercrime.gov.in with: transaction ID, beneficiary VPA, screenshot proof"),
        ("STEP 3 — Next day", "Visit bank branch. Submit written complaint to Branch Manager with all transaction details. Get complaint reference number."),
        ("STEP 4 — Day 3", "Email complaint to bank's nodal officer (email on RBI website) if branch doesn't register complaint"),
        ("STEP 5 — Day 30", "If bank does not resolve in 30 days, file RBI Banking Ombudsman complaint at cms.rbi.org.in"),
        ("STEP 6 — Appeal", "If Ombudsman rejects, appeal to Appellate Authority (Deputy Governor, RBI) within 30 days"),
    ],
    "bank_account_fraud": [
        ("STEP 1", "Immediately call bank to freeze account + change net banking password + block card"),
        ("STEP 2", "File FIR at police station (cognizable offence under BNS 2023)"),
        ("STEP 3", "Send written complaint to bank within 3 working days of noticing fraud"),
        ("STEP 4 — RBI Rule", "If you reported within 3 days: ZERO LIABILITY (bank must refund). 4-7 days: partial liability. >7 days: liability depends on negligence."),
        ("STEP 5", "If bank refuses: RBI Ombudsman at cms.rbi.org.in"),
    ],
    "insurance_fraud": [
        ("STEP 1", "File complaint with insurer in writing (keep copy)"),
        ("STEP 2 — 30 days wait", "If not resolved: IRDAI Bima Bharosa portal (bimabharosa.irdai.gov.in)"),
        ("STEP 3", "Insurance Ombudsman (free): insuranceombudsman.gov.in"),
        ("STEP 4", "Consumer Forum for claims ≤₹50L"),
    ]
}

ZERO_LIABILITY_RULE = """RBI's Limited Liability Framework (Circular 2017):
- Fraud due to bank's negligence/third party breach: ZERO liability, full refund regardless of when reported
- Customer reported within 3 working days: ZERO liability
- Customer reported within 4-7 working days: Liability capped at ₹5,000–25,000 depending on account type
- Customer reported after 7 working days: Liability as per bank's approved policy"""

SYSTEM_PROMPT = """You are an expert in Indian banking law, RBI regulations, and fraud recovery.
Cite specific RBI circulars, sections of Payment and Settlement Systems Act, IT Act Section 66C/66D.
Include exact portal links and helpline numbers.
Be urgent about time-sensitive steps (first 24-48 hours are critical for recovery)."""

def recover(fraud_type, amount, days_since_fraud, bank_name="", details=""):
    escalation = ESCALATION_PATH.get(fraud_type, ESCALATION_PATH["upi_fraud"])

    prompt = f"""Banking fraud recovery guidance:
Fraud type: {fraud_type}
Amount lost: ₹{amount:,}
Days since fraud: {days_since_fraud}
Bank: {bank_name or 'not specified'}
Details: {details}

Assess:
1. Recovery likelihood given {days_since_fraud} days elapsed
2. Specific steps to take TODAY
3. Zero-liability rule — does it apply here?
4. Legal sections violated (BNS 66C/66D / IT Act)
5. Expected timeline for resolution
6. If bank refuses to cooperate — exact escalation path with portal links"""

    resp = llm_chat([{"role":"user","content":prompt}], system=SYSTEM_PROMPT)
    return {
        "ok": True,
        "recovery_analysis": resp,
        "escalation_steps": escalation,
        "zero_liability_rule": ZERO_LIABILITY_RULE,
        "critical_contacts": {
            "cybercrime_helpline": "1930 (24x7)",
            "cybercrime_portal": "cybercrime.gov.in",
            "rbi_ombudsman": "cms.rbi.org.in",
            "irdai_ombudsman": "bimabharosa.irdai.gov.in (insurance)",
        },
        "urgency_note": "EVERY HOUR COUNTS for UPI fraud — the fraudster withdraws immediately. Call 1930 first.",
        "agent": "banking_fraud"
    }

if __name__ == "__main__":
    import json
    r = recover("upi_fraud", 50000, 1, "SBI", "Received fake KYC update SMS, clicked link, OTP entered")
    print(r["escalation_steps"][0])
