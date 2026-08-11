"""
Startup Legal Checklist — company registration, compliance calendar, IP, employment law for Indian startups.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from llm_client import chat as llm_chat

ENTITY_COMPARISON = {
    "private_limited": {
        "name": "Private Limited Company (Pvt Ltd)",
        "pros": ["Separate legal entity", "Limited liability", "Easy VC/angel investment", "Credibility with clients"],
        "cons": ["ROC compliance mandatory", "AGM required", "Annual audit mandatory"],
        "min_directors": 2, "min_shareholders": 2,
        "registration_cost": "₹7,000–15,000 (professional fees + govt fees)",
        "time": "7–15 working days via SPICe+ portal",
        "best_for": "Startups seeking external investment or with multiple founders"
    },
    "llp": {
        "name": "Limited Liability Partnership (LLP)",
        "pros": ["Lower compliance than Pvt Ltd", "Flexible profit sharing", "No mandatory audit <₹40L turnover"],
        "cons": ["Cannot issue equity shares", "Not preferred by VC investors"],
        "min_partners": 2,
        "registration_cost": "₹5,000–10,000",
        "time": "5–10 working days",
        "best_for": "Service businesses, professional practices, small teams"
    },
    "opc": {
        "name": "One Person Company (OPC)",
        "pros": ["Single founder", "Separate legal entity", "Limited liability"],
        "cons": ["Cannot raise equity investment", "Must convert to Pvt Ltd if turnover >₹2Cr"],
        "registration_cost": "₹5,000–8,000",
        "best_for": "Solo founders, freelancers wanting corporate structure"
    },
    "sole_proprietorship": {
        "name": "Sole Proprietorship",
        "pros": ["Zero registration cost", "Simple", "No compliance"],
        "cons": ["No limited liability", "No investment", "No separate legal entity"],
        "best_for": "Early testing, freelancers, very small businesses"
    }
}

COMPLIANCE_CALENDAR = {
    "pvt_ltd": [
        {"month": "April", "task": "Advance Tax Q1 installment (15% of annual tax)", "due": "15 June"},
        {"month": "July", "task": "ITR filing for previous FY", "due": "31 July (extended 31 Oct for audit cases)"},
        {"month": "September", "task": "AGM must be held", "due": "30 September"},
        {"month": "October", "task": "AOC-4 (annual accounts) filing with ROC", "due": "30 October (60 days from AGM)"},
        {"month": "November", "task": "MGT-7A (annual return) filing with ROC", "due": "60 days from AGM"},
        {"month": "April", "task": "DIR-3 KYC for all directors", "due": "30 September each year"},
    ],
    "gst": [
        {"task": "GSTR-1 (outward supplies)", "frequency": "Monthly (11th) or Quarterly (13th of following month)", "threshold": "All GST registered"},
        {"task": "GSTR-3B (summary return)", "frequency": "Monthly (20th)", "threshold": "All GST registered"},
        {"task": "GSTR-9 (annual return)", "frequency": "Annual (31 Dec)", "threshold": "Turnover >₹2Cr"},
        {"task": "GST Registration", "threshold": "Mandatory: ₹40L turnover (goods), ₹20L (services), ₹10L (NE states)", "note": "Voluntary registration possible below threshold"},
    ]
}

SYSTEM_PROMPT = """You are a startup lawyer + CA advising Indian founders.
Be practical, cost-conscious, and actionable. Include:
- Exact portal/website to use
- Approximate cost
- Timeline
- Common mistakes to avoid
- Priority order (what to do first)
DISCLAIMER: Consult a CA and lawyer for your specific situation."""

def checklist(stage, entity_type="pvt_ltd", state="", sector="", founders=2):
    prompt = f"""Generate a complete legal/compliance checklist for an Indian startup:
Stage: {stage} (idea/incorporated/revenue/funding)
Entity: {entity_type}
State: {state or 'not specified'}
Sector: {sector or 'general'}
Number of founders: {founders}

Include in priority order:
1. Immediate legal steps for this stage
2. IP protection (trademark, copyright, patents)
3. Employment agreements and ESOP framework
4. Key contracts needed (client, vendor, NDA)
5. Compliance deadlines for next 12 months
6. Common mistakes founders make at this stage"""

    resp = llm_chat([{"role":"user","content":prompt}], system=SYSTEM_PROMPT)
    return {
        "ok": True,
        "stage": stage,
        "entity": ENTITY_COMPARISON.get(entity_type, {}),
        "compliance_calendar": COMPLIANCE_CALENDAR,
        "checklist": resp,
        "agent": "startup_legal",
        "dpiit_tip": "Register for DPIIT Startup India recognition at startupindia.gov.in for tax benefits under Section 80IAC and easier FDI."
    }

if __name__ == "__main__":
    import json
    r = checklist("incorporated", "pvt_ltd", "Karnataka", "SaaS", 2)
    print(r["checklist"][:500])
