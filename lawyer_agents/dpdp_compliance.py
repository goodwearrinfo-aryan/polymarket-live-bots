"""
DPDP & Cyber Law Agent — India's DPDP Act 2023, IT Act obligations, data breach response.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from llm_client import chat as llm_chat

DPDP_KEY_PROVISIONS = {
    "consent": "Every business processing personal data MUST get informed, specific, free consent. Pre-ticked boxes are invalid.",
    "data_fiduciary": "Any entity that decides HOW and WHY personal data is processed is a 'Data Fiduciary' with compliance obligations.",
    "data_principal_rights": ["Right to access information", "Right to correction and erasure", "Right to grievance redressal", "Right to nominate"],
    "notice": "Give clear notice before/at time of collecting data — what data, why, how long retained.",
    "purpose_limitation": "Data collected for purpose X CANNOT be used for purpose Y without fresh consent.",
    "data_localisation": "Govt may restrict cross-border transfer of specific data categories (notified separately).",
    "penalties": "Up to ₹250Cr per violation. Up to ₹10,000Cr for systemic failures.",
    "significant_data_fiduciary": "High-volume or high-sensitivity processors — additional obligations (DPIA, DPO, audits).",
    "children_data": "Consent of parent/guardian mandatory for processing data of children (<18). No targeted ads to children.",
}

SYSTEM_PROMPT = """You are an expert in India's Digital Personal Data Protection Act 2023, IT Act 2000 (as amended), and cybersecurity compliance.
Be practical and actionable. Reference the DPDP Act 2023, IT Act Section 43A, 66, 72A, and SPDI Rules 2011.
DISCLAIMER: Data protection compliance is complex. Consult a legal expert for your specific situation."""

def compliance_audit(business_type, data_collected, user_count, processes_children=False, cross_border=False):
    prompt = f"""DPDP Act 2023 compliance assessment:
Business: {business_type}
Personal data collected: {data_collected}
Approximate users: {user_count:,}
Processes children's data (<18): {processes_children}
Transfers data outside India: {cross_border}

Assess:
1. Are you a Data Fiduciary? (yes/no + why)
2. Could you be a Significant Data Fiduciary? (triggers)
3. Consent mechanism checklist — what needs to change on your forms/apps
4. Privacy notice — what 5 things must it say?
5. Data retention policy — what to specify
6. User rights implementation — how to handle erasure/access requests
7. {"Children's data special requirements" if processes_children else "Data breach response plan (mandatory reporting to CERT-In within 6 hours)"}
8. {'Cross-border data transfer rules and standard contractual clauses' if cross_border else 'Third-party data processor agreements needed'}
9. Penalty exposure if non-compliant (₹250Cr scale)
10. Priority action list — what to do in next 30 days"""
    resp = llm_chat([{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}])
    return {
        "ok": True, "analysis": resp,
        "key_obligations": DPDP_KEY_PROVISIONS,
        "cert_in_breach_deadline": "6 hours to report breach to CERT-In (cybersecuritylaw.in/cert-in)",
        "agent": "dpdp_compliance"
    }

def it_act_obligations(issue_type, facts):
    sections = {
        "data_breach": "Section 43A SPDI Rules — compensation for negligent handling of sensitive data",
        "hacking": "Section 66 — 3 years imprisonment for unauthorized access",
        "cyber_stalking": "Section 354D IPC / BNS — 1-3 years for cyberstalking",
        "online_defamation": "Section 500 IPC/BNS + Section 66A struck down — but civil defamation under civil law",
        "fake_profile": "Section 66C (identity theft) + 66D (personation by computer)",
        "revenge_porn": "Section 67 IT Act + POCSO if minor",
        "phishing_fraud": "Section 66D IT Act + Section 420 BNS",
        "business_email_compromise": "Sections 66, 66D IT Act + BNS fraud provisions",
    }
    prompt = f"""IT Act / Cyber law guidance for:
Issue: {issue_type}
Facts: {facts}
Relevant section: {sections.get(issue_type.lower().replace(' ','_'), 'general IT Act')}

Provide:
1. Applicable legal sections (IT Act + BNS 2023)
2. Immediate steps to take
3. Evidence preservation
4. Filing complaint — cybercrime.gov.in + police
5. Civil remedies available
6. Timeline and what to expect"""
    resp = llm_chat([{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}])
    return {"ok": True, "guidance": resp, "sections_map": sections, "agent": "dpdp_compliance"}

if __name__ == "__main__":
    import json
    r = compliance_audit("EdTech platform", "name, email, age, location, learning progress", 50000, True, False)
    print("Analysis preview:", r["analysis"][:300])
