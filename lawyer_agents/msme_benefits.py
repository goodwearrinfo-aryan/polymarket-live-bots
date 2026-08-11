"""
MSME Benefits Agent — Udyam registration, priority lending, MSMED Act protections, government schemes.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from llm_client import chat as llm_chat

MSME_THRESHOLDS = {
    "micro": {"investment": "Up to ₹1 Cr", "turnover": "Up to ₹5 Cr"},
    "small": {"investment": "Up to ₹10 Cr", "turnover": "Up to ₹50 Cr"},
    "medium": {"investment": "Up to ₹50 Cr", "turnover": "Up to ₹250 Cr"},
}

MSME_BENEFITS = [
    ("Priority Sector Lending", "Banks must lend 7.5% of ANBC to micro/small enterprises. Better loan access."),
    ("CGTMSE Scheme", "Collateral-free loans up to ₹2Cr via Credit Guarantee Fund Trust for Micro & Small Enterprises."),
    ("45-Day Payment Protection", "MSMED Act S.15-17: buyers MUST pay within 45 days or pay compound interest at 3x RBI rate."),
    ("MSME Samadhaan", "Free, fast dispute resolution for payment delays. Facilitation Council order in 90 days."),
    ("GeM Portal Priority", "Government e-Marketplace gives preference to MSMEs for government procurement."),
    ("Zero Defect Zero Effect (ZED)", "Certification for quality — subsidies up to 80% for Micro, 60% for Small."),
    ("CLCSS", "Credit Linked Capital Subsidy Scheme: 15% subsidy on technology upgrades up to ₹1Cr."),
    ("ISO Certification Reimbursement", "Reimbursement of ISO certification charges for MSMEs."),
    ("Trademark/Patent Fee Reduction", "50% discount on trademark filing fees for DPIIT-recognized Startups and MSMEs."),
    ("State-specific benefits", "Most states offer: land allocation, power tariff concessions, stamp duty exemptions, tax holidays for new MSME units."),
    ("Income Tax Benefits", "Section 80IAC: 3-year tax holiday for eligible Startups. Section 80JJA: employment generation deduction."),
    ("ESI/PF Benefits", "Government pays employer's PF contribution (12%) for new employees earning ≤₹15,000 for 3 years under PMRPY scheme."),
]

SYSTEM_PROMPT = """You are an MSME development expert and CA familiar with all central and state government schemes for small businesses.
Be specific about eligibility, application process, and portal links. DISCLAIMER: Schemes change — verify on official portals."""

def check_eligibility(investment, turnover, sector, state=""):
    category = None
    inv_cr = investment / 10000000
    turn_cr = turnover / 10000000
    if inv_cr <= 1 and turn_cr <= 5:
        category = "micro"
    elif inv_cr <= 10 and turn_cr <= 50:
        category = "small"
    elif inv_cr <= 50 and turn_cr <= 250:
        category = "medium"

    prompt = f"""MSME benefits analysis:
Investment in plant & machinery: ₹{investment:,} (₹{investment/10000000:.1f}Cr)
Annual turnover: ₹{turnover:,} (₹{turnover/10000000:.1f}Cr)
Sector: {sector}
State: {state or 'not specified'}
MSME Category: {category or 'Not MSME (exceeds limits)'}

Provide:
1. MSME category and Udyam Registration process (udyamregistration.gov.in — free, Aadhaar-based)
2. Top 5 most impactful benefits for this specific business
3. State-specific schemes available in {state or 'your state'}
4. CGTMSE collateral-free loan eligibility
5. GeM portal registration steps for government sales
6. 45-day payment protection — how to enforce it against large buyers
7. Any sector-specific MSME schemes for {sector}
8. Annual compliance requirements after Udyam registration"""

    resp = llm_chat([{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}])
    return {
        "ok": True,
        "category": category,
        "threshold": MSME_THRESHOLDS.get(category, {}),
        "analysis": resp,
        "key_benefits": MSME_BENEFITS[:5],
        "registration_portal": "udyamregistration.gov.in (free, instant, Aadhaar-based)",
        "samadhaan_portal": "samadhaan.msme.gov.in (payment dispute resolution)",
        "agent": "msme_benefits"
    }

if __name__ == "__main__":
    import json
    r = check_eligibility(5000000, 30000000, "IT Services / Software", "Karnataka")
    print("Category:", r["category"])
    print("Top benefit:", r["key_benefits"][0])
