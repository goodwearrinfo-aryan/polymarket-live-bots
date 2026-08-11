"""
Arbitration & Business Dispute Agent — arbitration vs litigation, NCLT, Lok Adalat, mediation.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from llm_client import chat as llm_chat

DISPUTE_ROUTES = {
    "arbitration": {
        "governed_by": "Arbitration and Conciliation Act 1996 (amended 2019)",
        "pros": ["Confidential", "Faster than courts", "Enforceable in 170+ countries (New York Convention)", "Expert arbitrator for technical disputes", "Party autonomy — choose rules/seat/language"],
        "cons": ["Expensive (arbitrator fees)", "No interim relief as easily as courts", "Limited appeal rights"],
        "timeline": "6-24 months", "cost": "₹1-10L+ (arbitrator fees + institution fees)",
        "when": "Commercial contracts with arbitration clause, B2B disputes, international contracts"
    },
    "nclt": {
        "governed_by": "Companies Act 2013, IBC 2016",
        "for": "Company law disputes, insolvency, oppression/mismanagement, class action",
        "timeline": "1-3 years", "cost": "₹5,000-20,000 filing fee"
    },
    "lok_adalat": {
        "for": "Settled disputes — both parties agree. Motor accident, bank recovery, matrimonial (not divorce), labour",
        "timeline": "1 day (if both agree)",
        "cost": "No fee. Settled decree is final (cannot appeal)",
        "magic": "Fees paid to court earlier are REFUNDED if Lok Adalat award is accepted"
    },
    "mediation": {
        "governed_by": "Mediation Act 2023",
        "for": "Commercial, family, corporate, consumer disputes",
        "timeline": "60 days (commercial) / 90 days (others)",
        "cost": "₹5,000-50,000 mediator fees",
        "note": "Pre-litigation mediation now mandatory for commercial disputes in many courts"
    },
    "consumer_forum": {"for": "B2C disputes up to ₹50L. B2C only.", "cost": "₹100-5,000"},
    "civil_court": {"for": "All civil disputes. Slow but full process.", "timeline": "3-10 years", "cost": "Ad valorem"}
}

SYSTEM_PROMPT = """You are an expert Indian commercial dispute resolution lawyer.
Cover: Arbitration Act 1996, Companies Act, IBC 2016, Mediation Act 2023, Commercial Courts Act.
Be strategic — advise which route gives best result for the specific dispute. DISCLAIMER: Consult a lawyer before initiating disputes."""

def recommend_route(dispute_type, amount, contract_has_arb_clause, parties, urgency):
    prompt = f"""Business dispute resolution strategy:
Dispute: {dispute_type}
Amount: ₹{amount:,}
Arbitration clause in contract: {contract_has_arb_clause}
Parties: {parties}
Urgency: {urgency}

Recommend:
1. Best dispute resolution route (with reasoning)
2. If arbitration: institutional (DIAC/MCIA/ICC) vs ad hoc; which seat/rules to choose
3. Interim relief options — how to get urgent injunctions
4. Timeline to realistic resolution
5. Cost estimate
6. Enforcement — how to convert award/decree to money
7. Negotiation strategy before formal dispute
8. Evidence preservation steps NOW
9. Whether to send legal notice first (and template approach)"""
    resp = llm_chat([{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}])
    return {
        "ok": True, "strategy": resp,
        "routes_comparison": DISPUTE_ROUTES,
        "icc_india": "iccwbo.org | DIAC: delhiarbitration.org | MCIA: mcia.in",
        "agent": "arbitration"
    }

def draft_arbitration_clause(governing_law="India", seat="Delhi", institution="DIAC", language="English"):
    clause = f"""ARBITRATION CLAUSE (recommended for {institution}):

Any dispute, controversy or claim arising out of or relating to this Agreement, including
any question regarding its existence, validity, breach or termination, shall be referred
to and finally resolved by arbitration administered by the {institution} (Delhi International
Arbitration Centre) under the {institution} Arbitration Rules in force at the time of
filing the Request for Arbitration.

The seat of arbitration shall be {seat}, India.
The language of arbitration shall be {language}.
The tribunal shall consist of [one/three] arbitrator(s).
The law governing this Agreement shall be the laws of {governing_law}.
The award shall be final and binding on all parties."""
    return {
        "ok": True, "clause": clause,
        "alternatives": {
            "MCIA": "Mumbai Centre for International Arbitration (mcia.in)",
            "DIAC": "Delhi International Arbitration Centre (delhiarbitration.org)",
            "ICC": "International Chamber of Commerce (iccwbo.org) — for international contracts"
        },
        "note": "Add this clause in your contracts BEFORE disputes arise. Cannot add retroactively without mutual agreement.",
        "agent": "arbitration"
    }

if __name__ == "__main__":
    import json
    print(json.dumps(draft_arbitration_clause(), indent=2))
