"""
Investor Term Sheet & Funding Law Agent — SAFE, CCD, SHA, anti-dilution, ESOP.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from llm_client import chat as llm_chat

TERM_SHEET_GLOSSARY = {
    "Pre-money valuation": "Company value BEFORE new investment. If pre-money = ₹9Cr and investor puts in ₹1Cr, post-money = ₹10Cr and investor owns 10%.",
    "Post-money valuation": "Company value AFTER investment = pre-money + investment amount.",
    "SAFE": "Simple Agreement for Future Equity — investor gives money now, gets shares at next priced round. No current valuation needed. Popular for early angels.",
    "CCD": "Compulsorily Convertible Debentures — debt that mandatorily converts to equity. Common in Indian VC rounds due to FEMA/RBI rules.",
    "CCPS": "Compulsorily Convertible Preference Shares — preferred shares that convert to equity. Gives investors liquidation preference.",
    "Liquidation Preference": "1x non-participating = investor gets their money back first before founders in exit. Participating = investor gets money back AND shares in remaining proceeds.",
    "Anti-dilution": "Protects investor if future round is at lower valuation (down round). Full ratchet (aggressive) vs weighted average (founder-friendly).",
    "Pro-rata rights": "Investor can invest in future rounds to maintain their ownership %.",
    "Board seat": "Investor gets a board seat. Understand: what decisions require board approval? What is the voting structure?",
    "Drag-along": "If majority shareholders want to sell, they can force minority to sell too. Check threshold.",
    "Tag-along": "If founders sell shares, investors have right to join the sale at same terms.",
    "ROFR": "Right of First Refusal — if you want to sell shares, you must offer to existing investors first.",
    "ESOP pool": "Often created pre-investment (dilutes founders, not investors). Understand: before or after money?",
    "Vesting": "Founders and employees earn shares over time (typically 4 years, 1-year cliff). Reverse vesting for founders.",
    "Information rights": "Investor's right to receive financial statements, reports, audit. Standard clause.",
}

SYSTEM_PROMPT = """You are a senior Indian startup lawyer and VC term sheet specialist.
Know India-specific rules: FEMA, RBI pricing guidelines for foreign investment, Companies Act 2013 CCPS/CCD structures, SEBI angel fund regulations.
Be founder-friendly but honest about market norms. DISCLAIMER: Get independent legal advice before signing any term sheet."""

def explain_term_sheet(term_sheet_text):
    prompt = f"""Analyze this investment term sheet from a FOUNDER'S perspective:

{term_sheet_text[:5000]}

For each key term:
1. What it means in plain English
2. Is it founder-friendly, standard, or investor-heavy?
3. What to push back on or negotiate
4. Red flags or unusual clauses
5. Overall assessment: is this a fair term sheet for an Indian startup?

End with: Top 3 things to negotiate and the 1 thing that's a dealbreaker if present."""
    resp = llm_chat([{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}])
    return {
        "ok": True, "analysis": resp,
        "glossary": TERM_SHEET_GLOSSARY,
        "india_note": "India uses CCD/CCPS (not common stock) for VC rounds due to FEMA regulations. SAFE is used for angels but has FEMA reporting requirements.",
        "agent": "investor_terms"
    }

def esop_guide(company_stage, total_shares, esop_pool_pct):
    prompt = f"""ESOP (Employee Stock Option Plan) guide for Indian startup:
Stage: {company_stage}
Total shares: {total_shares:,}
Proposed ESOP pool: {esop_pool_pct}% ({int(total_shares * esop_pool_pct / 100):,} options)

Explain:
1. Companies Act 2013 ESOP rules — Section 62(1)(b), Rule 12
2. Tax treatment for employees: FMV at exercise (perquisite tax) vs sale (capital gains)
3. Vesting schedule — 4-year standard, cliff period, acceleration on exit
4. Exercise price — how to set it (typically at face value for early employees)
5. ESOP trust structure vs direct issuance
6. How to communicate ESOP to employees in plain language
7. What happens to ESOP on acquisition/IPO
8. Founder ESOP vs employee ESOP — differences
9. FEMA implications if foreign investors are present"""
    resp = llm_chat([{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}])
    return {"ok": True, "guide": resp, "esop_options": int(total_shares * esop_pool_pct / 100), "agent": "investor_terms"}

if __name__ == "__main__":
    import json
    r = esop_guide("Series A", 1000000, 10)
    print("ESOP options:", r["esop_options"])
