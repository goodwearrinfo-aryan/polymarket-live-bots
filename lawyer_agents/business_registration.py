"""
Business Registration Checker — verify companies on MCA21, check compliance status, director DIN.
"""
import sys, os, urllib.request, urllib.parse, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from llm_client import chat as llm_chat

MCA_BASE = "https://www.mca.gov.in"

REGISTRATION_STEPS = {
    "pvt_ltd": {
        "portal": "mca.gov.in/mcafoportal/viewCompanyMasterData.do (for verification)",
        "registration_portal": "spiceocp.mca.gov.in (for new registration)",
        "steps": [
            "Get DSC (Digital Signature Certificate) for all directors — from MCA empanelled CAs",
            "Apply for DIN (Director Identification Number) — free via SPICe+ form",
            "Name reservation via RUN (Reserve Unique Name) on MCA portal — ₹1,000",
            "File SPICe+ (INC-32) with MoA, AoA, INC-9 (declaration) — all in one form",
            "PAN + TAN allotted automatically with incorporation",
            "Certificate of Incorporation received in 7-15 working days",
            "Open bank account, apply for GST, MSME registration"
        ],
        "post_incorporation": ["GST registration", "MSME/Udyam", "DPIIT Startup recognition", "Professional Tax registration (state-specific)", "Shop & Establishment license", "Import-Export Code (if applicable)"]
    },
    "llp": {
        "portal": "llpregistration.mca.gov.in",
        "steps": [
            "Get DSC for all designated partners",
            "Apply for DPIN (Designated Partner Identification Number)",
            "Name reservation via RUN-LLP",
            "File FiLLiP form with LLP agreement",
            "Certificate of Incorporation in 5-10 working days"
        ]
    }
}

def verify_company(company_name_or_cin):
    """Check if company exists on MCA (keyless public search)."""
    try:
        # MCA public search
        encoded = urllib.parse.quote(company_name_or_cin)
        url = f"https://www.mca.gov.in/mcafoportal/viewCompanyMasterData.do"
        # Try the MCA API endpoint
        api_url = f"https://www.mca.gov.in/MCA21/dca/xmlreport/companySearch.json?company_name={encoded}&page=1&records=5"
        req = urllib.request.Request(api_url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.load(r)
        return {"ok": True, "source": "MCA21", "data": data, "agent": "business_registration"}
    except Exception as e:
        return {
            "ok": False, "error": str(e),
            "manual_check": f"Visit mca.gov.in → MCA Services → View Company/LLP Master Data → search '{company_name_or_cin}'",
            "note": "MCA portal often requires login for full data. Use the portal directly.",
            "agent": "business_registration"
        }

def registration_guide(entity_type, founders, state, sector, foreign_investment=False):
    info = REGISTRATION_STEPS.get(entity_type, REGISTRATION_STEPS["pvt_ltd"])
    prompt = f"""Business registration guide for India:
Entity type: {entity_type}
Number of founders: {founders}
State of operations: {state}
Sector: {sector}
Foreign investment planned: {foreign_investment}

Provide:
1. Step-by-step registration process (with MCA portal specifics)
2. Documents required
3. Cost breakdown (govt fees + professional fees estimate)
4. Timeline
5. Post-registration compliance in first 6 months
6. {'FDI regulations, FEMA compliance, RBI reporting for foreign investment' if foreign_investment else 'State-specific licenses/registrations needed for this sector'}
7. Common mistakes in registration
8. Tax identification: PAN, TAN, GST — what's needed and when
9. Banking: how to open a current account after incorporation"""
    resp = llm_chat([{"role": "system", "content": "You are an expert company secretary and business lawyer in India."}, {"role": "user", "content": prompt}])
    return {
        "ok": True, "entity_type": entity_type,
        "guide": resp, "steps": info.get("steps", []),
        "post_registration": info.get("post_incorporation", []),
        "portal": info.get("registration_portal", info.get("portal", "spiceocp.mca.gov.in")),
        "agent": "business_registration"
    }

if __name__ == "__main__":
    import json
    r = registration_guide("pvt_ltd", 2, "Karnataka", "SaaS / IT Services")
    print("Steps:", r["steps"][:3])
    print("Post-reg:", r["post_registration"])
