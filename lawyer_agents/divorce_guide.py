"""
Divorce & Family Law Guide — Indian personal laws, maintenance, custody.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from llm_client import chat as llm_chat

DIVORCE_TYPES = {
    "mutual_consent_hindu": {
        "act": "Hindu Marriage Act 1955, Section 13B",
        "eligibility": "Both parties agree. Married 1+ year.",
        "process": ["File joint petition in Family Court", "First motion — court records consent", "6-month cooling off period (can be waived by HC)", "Second motion — court grants divorce"],
        "timeline": "6-18 months (waiver possible)",
        "cost": "₹5,000–50,000 (advocate fees vary)"
    },
    "contested_hindu": {
        "act": "Hindu Marriage Act 1955, Section 13",
        "grounds": ["Adultery (S.13(1)(i))", "Cruelty (S.13(1)(ia))", "Desertion 2+ years (S.13(1)(ib))", "Conversion to another religion", "Mental disorder", "Communicable disease", "Renounced world"],
        "timeline": "3-7 years in Family Court (often longer)",
        "cost": "₹50,000–5,00,000+"
    },
    "muslim_talaq": {
        "act": "Muslim Personal Law + Muslim Women (Protection of Rights on Marriage) Act 2019",
        "note": "Triple talaq (instant talaq-ul-biddat) is ILLEGAL since 2019. Criminalises the husband. Valid forms: Talaq-ul-Sunnat (revocable), Khula (wife-initiated), Mubarat (mutual).",
        "process": "For Muslim divorce: Talaq must be pronounced in successive months with attempts at reconciliation. Register with family court."
    },
    "christian_divorce": {
        "act": "Indian Divorce Act 1869 (amended 2001)",
        "grounds": ["Adultery", "Cruelty", "Desertion 2+ years", "Unsound mind", "Incurable leprosy/venereal disease"],
        "mutual": "Section 10A — mutual consent divorce available (2001 amendment)"
    },
    "special_marriage_act": {
        "act": "Special Marriage Act 1954 (inter-religious / civil marriage)",
        "grounds": "Similar to HMA. Section 28 — mutual consent divorce."
    }
}

SYSTEM_PROMPT = """You are a compassionate and expert Indian family law attorney.
Cover: appropriate personal law, divorce grounds, maintenance under BNSS Section 144 (formerly CrPC 125), child custody under Guardians & Wards Act, domestic violence protection under PWDVA 2005.
Be supportive but also realistic about timelines and costs.
DISCLAIMER: Family law is sensitive and fact-specific. Please consult a lawyer confidentially."""

def guide(situation, religion="Hindu", is_mutual=None, has_children=False, domestic_violence=False):
    prompt = f"""Family law guidance:
Situation: {situation}
Religion: {religion}
Mutual divorce possible: {is_mutual}
Children involved: {has_children}
Domestic violence present: {domestic_violence}

Provide:
1. Applicable divorce law and grounds
2. Step-by-step process and realistic timeline
3. Maintenance/alimony rights (permanent + interim)
4. Child custody — factors court considers, types of custody
5. {'IMMEDIATE safety steps — Protection Order under PWDVA 2005' if domestic_violence else 'Division of marital assets'}
6. Cost estimate
7. Documents to gather NOW
8. Can the 6-month cooling period be waived?"""

    resp = llm_chat([{"role":"system","content":SYSTEM_PROMPT},{"role":"user","content":prompt}])
    return {
        "ok": True,
        "guidance": resp,
        "divorce_types": DIVORCE_TYPES,
        "emergency": "If facing domestic violence: Call 181 (Women Helpline) or 100. File for Protection Order under PWDVA 2005 — can be granted within 24 hours by Magistrate." if domestic_violence else None,
        "helplines": ["Women Helpline: 181", "iCall: 9152987821", "NCW: 7827170170"],
        "agent": "divorce_guide"
    }

if __name__ == "__main__":
    r = guide("My wife and I agree to divorce after 3 years of marriage. We have no children and no major assets.", "Hindu", True, False)
    print(r["guidance"][:600])
