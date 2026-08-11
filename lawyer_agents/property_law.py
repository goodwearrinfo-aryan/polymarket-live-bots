"""
Property Law Agent — title due diligence checklist, stamp duty calculator, RERA lookup guidance.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from llm_client import chat as llm_chat

# Stamp duty rates by state (approximate, 2024-25, verify before use)
STAMP_DUTY = {
    "maharashtra": {"rate": 6, "women_discount": 1, "registration": 1, "note": "1% rebate for women buyers. Registration: 1% (max ₹30,000). Mumbai Metro cess extra."},
    "delhi": {"rate": 6, "women_discount": 4, "registration": 1, "note": "4% for women. Registration: 1%. Circle rate restrictions apply."},
    "karnataka": {"rate": 5, "women_discount": 0, "registration": 1, "note": "5% up to ₹45L, 5.6% above. +0.5% registration."},
    "tamil_nadu": {"rate": 7, "women_discount": 0, "registration": 4, "note": "7% stamp + 4% registration = 11% total. High."},
    "gujarat": {"rate": 4.9, "women_discount": 0, "registration": 1, "note": "4.9% stamp + 1% registration. Lower than others."},
    "rajasthan": {"rate": 6, "women_discount": 5, "registration": 1, "note": "5% for women. + 1% registration."},
    "uttar_pradesh": {"rate": 7, "women_discount": 6, "registration": 1, "note": "6% for women. + 1% registration."},
    "west_bengal": {"rate": 7, "women_discount": 0, "registration": 1, "note": "7% + 1% registration. Highest in East India."},
    "telangana": {"rate": 5, "women_discount": 0, "registration": 0.5, "note": "5% stamp + 0.5% registration."},
}

DUE_DILIGENCE_CHECKLIST = [
    {"doc": "Title Deed / Sale Deed", "check": "Chain of title for minimum 30 years. Each transfer properly stamped and registered.", "critical": True},
    {"doc": "Encumbrance Certificate", "check": "From Sub-Registrar's office — confirms no mortgage/lien/charge on property. Get for 30 years.", "critical": True},
    {"doc": "Property Tax Receipts", "check": "Last 3 years paid. Seller must provide all paid receipts.", "critical": True},
    {"doc": "Khata / Mutation", "check": "Revenue record in seller's name (Khata in Karnataka/AP, Mutation in other states).", "critical": True},
    {"doc": "RERA Registration", "check": "For under-construction: verify project on state RERA portal. Builder must display RERA number.", "critical": True},
    {"doc": "Approved Building Plan", "check": "Sanction from local municipal authority. No unauthorized construction.", "critical": True},
    {"doc": "Occupancy Certificate (OC)", "check": "For completed buildings — confirms building constructed as per approved plan.", "critical": True},
    {"doc": "No Objection Certificates", "check": "Electricity, water, society NOC if apartment. Check for court cases on property.", "critical": False},
    {"doc": "7/12 Extract / Patta", "check": "Agricultural land: 7/12 extract (Maharashtra) / Patta (Tamil Nadu) confirms ownership.", "critical": False},
    {"doc": "Housing Society Documents", "check": "If flat: share certificate, society NOC, maintenance dues cleared.", "critical": False},
]

SYSTEM_PROMPT = "You are a property lawyer specializing in Indian real estate transactions. Be specific about documents and procedures for the state mentioned."

def due_diligence(property_type, state, transaction_value, is_resale=True):
    state_key = state.lower().replace(" ", "_")
    stamp = STAMP_DUTY.get(state_key)

    if stamp and transaction_value:
        duty_amount = round(transaction_value * stamp["rate"] / 100, 0)
        reg_amount = round(transaction_value * stamp["registration"] / 100, 0)
        women_amount = round(transaction_value * stamp["women_discount"] / 100, 0) if stamp.get("women_discount") else 0
        stamp_info = {
            "state": state,
            "stamp_duty_rate": f"{stamp['rate']}%",
            "stamp_duty_amount": f"₹{duty_amount:,.0f}",
            "registration_fee": f"₹{reg_amount:,.0f}",
            "total_cost": f"₹{duty_amount + reg_amount:,.0f}",
            "women_buyer_saving": f"₹{women_amount:,.0f}" if women_amount else "No specific discount",
            "note": stamp["note"]
        }
    else:
        stamp_info = {"note": f"Stamp duty for {state} not in database. Check state government website."}

    prompt = f"""Property purchase due diligence for:
Type: {property_type}
State: {state}
Value: ₹{transaction_value:,}
Resale: {is_resale}

Provide state-specific checklist:
1. Documents to verify (with how to verify each)
2. Red flags / common scams in {state} property market
3. Government portals for verification
4. RERA requirements
5. Registration process step-by-step
6. Post-purchase mutations/khata transfer process"""

    resp = llm_chat([{"role":"system","content":SYSTEM_PROMPT},{"role":"user","content":prompt}])
    return {
        "ok": True,
        "stamp_duty": stamp_info,
        "due_diligence_checklist": DUE_DILIGENCE_CHECKLIST,
        "analysis": resp,
        "agent": "property_law"
    }

if __name__ == "__main__":
    import json
    r = due_diligence("2BHK flat", "Maharashtra", 8500000, True)
    print(json.dumps(r["stamp_duty"], indent=2))
