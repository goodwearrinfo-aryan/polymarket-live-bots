"""
POSH Guide — Prevention of Sexual Harassment at Workplace Act 2013.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from llm_client import chat as llm_chat

ICC_PROCEDURE = [
    "File written complaint to Internal Complaints Committee (ICC) within 3 months of incident",
    "ICC must have 4+ members including an external NGO/advocate member (woman Presiding Officer)",
    "ICC must inquire and submit report within 60 days",
    "Employer must act on ICC report within 60 days",
    "If no ICC or <10 employees: file with Local Committee (LC) at district level",
    "You can also request conciliation (but no monetary settlement without your consent)",
    "Appeal to court within 90 days if dissatisfied with ICC/LC order"
]

WHAT_IS_SH = [
    "Unwelcome physical contact or advances",
    "Demand or request for sexual favours",
    "Making sexually coloured remarks",
    "Showing pornography",
    "Any other unwelcome physical, verbal, or non-verbal conduct of a sexual nature",
    "Implied or explicit threat affecting employment for rejecting sexual advances",
    "Creating a hostile, intimidating, or offensive work environment"
]

SYSTEM_PROMPT = """You are an expert on India's POSH Act 2013 (Prevention, Prohibition and Redressal of Sexual Harassment at Workplace).
Be supportive, clear, and empowering. Cite specific sections.
Cover: what constitutes SH, ICC/LC process, timeline, remedies available, employer obligations.
Important: The complainant's identity must remain confidential (Section 16).
DISCLAIMER: This is informational. Please also contact a trusted person/legal aid for support."""

def guide(situation, role="complainant", employer_has_icc=True):
    prompt = f"""POSH Act guidance for:
Situation: {situation}
Role: {role} (complainant/respondent/employer/HR)
Has Internal Complaints Committee: {employer_has_icc}

Provide:
1. Does this constitute sexual harassment under POSH Act? (specific behaviours)
2. What steps to take immediately
3. ICC/LC complaint process and timeline
4. Evidence to preserve
5. Interim remedies available (transfer, leave, etc.)
6. What compensation/redressal can be ordered
7. Confidentiality protections
8. If employer has no ICC or <10 employees — Local Committee process"""

    resp = llm_chat([{"role":"user","content":prompt}], system=SYSTEM_PROMPT)
    return {
        "ok": True,
        "guidance": resp,
        "what_counts_as_sh": WHAT_IS_SH,
        "icc_procedure": ICC_PROCEDURE,
        "important": "Your identity as complainant CANNOT be disclosed (Section 16 POSH Act). Violation by employer is punishable.",
        "helplines": ["iCall (TISS): 9152987821", "Vanita Samakhya: 040-27545678", "NCW: 7827170170"],
        "agent": "posh_guide"
    }

if __name__ == "__main__":
    r = guide("My manager sends inappropriate messages on WhatsApp after hours and touched my shoulder at office", "complainant")
    print(r["guidance"][:500])
