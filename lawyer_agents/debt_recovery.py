"""
Debt Recovery Agent — recovery of commercial dues, MSME Samadhaan, DRT, NCLT.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from llm_client import chat as llm_chat

RECOVERY_ROUTES = {
    "below_1_lakh": {
        "forum": "Small Claims / District Consumer Forum (for consumer disputes)",
        "cost": "₹100–500 court fee", "timeline": "3-12 months"
    },
    "1_to_20_lakh": {
        "forum": "Civil Court (original side) OR Lok Adalat (if agreed)",
        "cost": "Ad valorem court fee (1-2% of claim)", "timeline": "1-3 years (Civil) / 1 day (Lok Adalat if settled)"
    },
    "msme_dues": {
        "forum": "MSME Samadhaan Portal (facilitation council) — FREE, time-bound 75+15 days",
        "cost": "Free", "timeline": "90 days", "portal": "samadhaan.msme.gov.in"
    },
    "above_20_lakh_bank": {
        "forum": "Debt Recovery Tribunal (DRT) — for bank/FI dues >₹20L",
        "cost": "₹12,000 filing fee for banks/FIs", "timeline": "1-2 years"
    },
    "insolvency_corporate": {
        "forum": "NCLT — Corporate Insolvency Resolution Process (CIRP) for defaults >₹1Cr",
        "cost": "₹2,000 filing fee for operational creditors",
        "timeline": "180+90 days max", "note": "Powerful but irreversible — debtor goes into IBC process"
    },
    "cheque_bounce": {
        "forum": "Criminal court JMFC (S.138 NI Act) — criminal pressure + civil parallel",
        "cost": "₹200 court fee", "timeline": "6-24 months"
    }
}

SYSTEM_PROMPT = """You are an expert Indian commercial litigation and debt recovery lawyer.
Cover: NI Act S.138, MSME Samadhaan, DRT, NCLT/IBC, civil suits, Lok Adalat.
Be practical about which route gives the best result for the specific amount and debtor type.
Include exact portal links and filing fees. DISCLAIMER: Consult a lawyer before initiating legal action."""

def recovery_plan(amount, debtor_type, days_overdue, is_msme=False, has_cheque=False):
    routes = []
    if has_cheque:
        routes.append(("Priority: S.138 NI Act", RECOVERY_ROUTES["cheque_bounce"]))
    if is_msme and amount > 0:
        routes.append(("MSME Samadhaan (if you are MSME)", RECOVERY_ROUTES["msme_dues"]))
    if amount < 100000:
        routes.append(("Small claims", RECOVERY_ROUTES["below_1_lakh"]))
    elif amount <= 2000000:
        routes.append(("Civil court", RECOVERY_ROUTES["1_to_20_lakh"]))
    elif debtor_type in ["company", "corporate"]:
        routes.append(("NCLT/IBC", RECOVERY_ROUTES["insolvency_corporate"]))

    prompt = f"""Debt recovery strategy:
Amount owed: ₹{amount:,}
Debtor type: {debtor_type}
Days overdue: {days_overdue}
Creditor is MSME: {is_msme}
Cheque available: {has_cheque}

Recommend:
1. Best recovery route for this situation (cost-benefit analysis)
2. Pre-litigation steps (legal notice → demand → negotiation)
3. Exact filing steps for recommended route
4. Timeline to get money back
5. How to strengthen position with evidence now
6. Interim relief options (attachment before judgment)
7. If debtor is a company — MCA check for assets, DIN status
8. Lok Adalat / mediation — when to consider
9. Enforcement of decree once obtained"""
    resp = llm_chat([{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}])
    return {
        "ok": True, "strategy": resp,
        "recommended_routes": routes,
        "pre_litigation": "Send legal notice first (30 days) — many disputes settle to avoid litigation costs",
        "msme_samadhaan": "samadhaan.msme.gov.in — free, fastest route if you are MSME",
        "agent": "debt_recovery"
    }

if __name__ == "__main__":
    import json
    r = recovery_plan(350000, "individual", 120, is_msme=True, has_cheque=True)
    print("Routes:", r["recommended_routes"])
