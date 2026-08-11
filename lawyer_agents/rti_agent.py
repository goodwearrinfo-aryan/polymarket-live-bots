"""
RTI Agent — Right to Information Act 2005 application generator.
Maps central/state ministry -> correct PIO, drafts complete RTI application.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from llm_client import chat as llm_chat

CENTRAL_MINISTRIES = {
    "income tax": "Central Public Information Officer, Income Tax Department",
    "passport": "Central Public Information Officer, Regional Passport Office",
    "pf": "Central Public Information Officer, EPFO Regional Office",
    "aadhaar": "Central Public Information Officer, UIDAI",
    "railway": "Central Public Information Officer, Indian Railways (Zonal HQ)",
    "bank": "Central Public Information Officer, Reserve Bank of India",
    "court": "Central Public Information Officer, High Court Registry",
}

SYSTEM_PROMPT = """You are an expert in India's RTI Act 2005.
Draft RTI applications that:
- Address the correct CPIO/SPIO designation
- Ask specific, narrow questions (RTI is for information, not opinions)
- Include the RTI fee note: Rs.10 by postal order/IPO/court fee stamp (BPL applicants: fee exempt with proof)
- Specify the 30-day response deadline under Section 7(1)
- Include appeal path: First Appellate Authority under Section 19(1), then CIC/SIC under Section 19(3)
- Are polite but unambiguous
DISCLAIMER: AI-generated draft. Verify the current PIO address before filing."""

def draft_rti(subject, information_sought, applicant_name, applicant_address, authority=None):
    prompt = f"""Draft a complete RTI application under the Right to Information Act 2005.

Subject: {subject}
Information Sought: {information_sought}
Applicant: {applicant_name}, {applicant_address}
Public Authority: {authority or 'determine from subject'}

Draft a complete RTI application ready to file."""

    resp = llm_chat([{"role": "user", "content": prompt}], system=SYSTEM_PROMPT)
    return {
        "ok": True,
        "draft": resp,
        "fee_note": "Rs.10 by IPO/postal order payable to Accounts Officer. BPL: attach certificate for exemption.",
        "deadline": "Authority must reply within 30 days (48 hours for life/liberty matters)",
        "agent": "rti_agent"
    }

if __name__ == "__main__":
    r = draft_rti(
        subject="PF balance and withdrawal status",
        information_sought="Current EPF balance, any pending withdrawals, employer contribution status for last 3 years",
        applicant_name="Test Applicant",
        applicant_address="123 Test Street, Mumbai 400001"
    )
    print(r["draft"])
