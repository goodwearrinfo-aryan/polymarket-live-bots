"""
Court Locator — identify correct court/forum for a dispute + fetch eCourts case status.
"""
import sys, os, urllib.request, urllib.parse, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from llm_client import chat as llm_chat

FORUM_RULES = [
    {
        "keywords": ["consumer", "defective product", "service deficiency", "refund refused", "ecommerce"],
        "forum": "District Consumer Disputes Redressal Commission",
        "claim_threshold": "Up to Rs.50 lakh",
        "higher": "State Commission (Rs.50L-2Cr) -> NCDRC (>Rs.2Cr)",
        "fee": "Rs.100-Rs.5,000 sliding scale"
    },
    {
        "keywords": ["cheque bounce", "138", "dishonour", "negotiable"],
        "forum": "Judicial Magistrate First Class (JMFC)",
        "fee": "Rs.200 court fee on complaint",
        "note": "File within 30 days of cause. Private complaint under Section 200 CrPC/BNSS."
    },
    {
        "keywords": ["labour", "dismissal", "wrongful termination", "wages", "pf", "provident fund", "gratuity"],
        "forum": "Labour Court / Industrial Tribunal (for <250 workers) | High Court Writ (for govt servants)",
        "note": "PF disputes -> EPFO Grievance Portal first, then PF Tribunal"
    },
    {
        "keywords": ["rera", "builder", "possession delay", "flat", "apartment", "real estate"],
        "forum": "State RERA Authority (specific to state where project is registered)",
        "note": "File at rera.gov.in or state RERA portal. No court fee for most states."
    },
    {
        "keywords": ["family", "divorce", "custody", "maintenance", "matrimonial"],
        "forum": "Family Court (cities with Family Courts Act 1984) | Civil Court otherwise",
        "note": "Try mediation first under Section 89 CPC. Maintenance under BNSS Section 144 in JMFC."
    },
    {
        "keywords": ["property", "land", "title", "possession", "trespass", "encroachment"],
        "forum": "Civil Court (original jurisdiction) | Revenue Court for agricultural land",
        "claim_threshold": "Civil Court jurisdiction based on property value"
    },
    {
        "keywords": ["income tax", "it notice", "assessment", "demand"],
        "forum": "CIT(Appeals) -> ITAT (Income Tax Appellate Tribunal) -> High Court",
        "fee": "No fee for CIT Appeals; ITAT Rs.500-Rs.10,000"
    },
    {
        "keywords": ["cybercrime", "online fraud", "upi fraud", "scam", "phishing"],
        "forum": "Cyber Crime Cell (cybercrime.gov.in) | Local Police Station (FIR under BNS/IT Act)",
        "note": "File at cybercrime.gov.in first. If money lost within 24h, call 1930 (National Helpline)."
    },
    {
        "keywords": ["bank", "insurance", "rbi", "ombudsman", "nbfc"],
        "forum": "Banking Ombudsman (RBI) / Insurance Ombudsman (IRDAI) — free, no lawyer needed",
        "note": "File at cms.rbi.org.in or ombudsman.irdai.gov.in. 30-day wait period after bank reply."
    },
]

def locate(dispute_description, claim_amount=None, state=None):
    desc_lower = dispute_description.lower()
    matched = []
    for rule in FORUM_RULES:
        if any(kw in desc_lower for kw in rule["keywords"]):
            matched.append(rule)

    if matched:
        primary = matched[0]
        return {
            "ok": True,
            "recommended_forum": primary["forum"],
            "details": {k: v for k, v in primary.items() if k != "keywords"},
            "alternative_forums": [m["forum"] for m in matched[1:3]],
            "agent": "court_locator"
        }

    # LLM fallback
    prompt = f"""Which Indian court/tribunal/forum should handle this dispute?
Dispute: {dispute_description}
Claim amount: {claim_amount or 'not specified'}
State: {state or 'not specified'}

Identify: correct forum, jurisdiction, filing fee, procedure, and any time limits."""
    resp = llm_chat([{"role": "user", "content": prompt}], system="You are an Indian procedural law expert.")
    return {"ok": True, "analysis": resp, "agent": "court_locator"}

def ecourts_status(state_code, district_code, case_type, case_number, case_year):
    """Fetch eCourts case status (keyless public API)."""
    url = "https://services.ecourts.gov.in/ecourtindiaAPI/api/getCaseStatus"
    params = {
        "state_code": state_code, "dist_code": district_code,
        "case_type": case_type, "reg_no": case_number, "reg_year": case_year
    }
    try:
        req = urllib.request.Request(
            url + "?" + urllib.parse.urlencode(params),
            headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            return {"ok": True, "data": json.load(r), "agent": "court_locator"}
    except Exception as e:
        return {
            "ok": False,
            "error": str(e),
            "note": "eCourts API may require session/captcha. Visit ecourts.gov.in directly."
        }

if __name__ == "__main__":
    r = locate("My builder has not given possession of my flat for 3 years despite full payment")
    print(json.dumps(r, indent=2))
