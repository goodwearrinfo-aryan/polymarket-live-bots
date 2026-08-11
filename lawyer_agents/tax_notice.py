"""
Tax Notice Decoder — explain Indian Income Tax notices in plain English.
Covers: 143(1), 143(2), 148, 148A, 139(9), 156, 271, 245, 263, 264.
"""
import sys, os, re
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from llm_client import chat as llm_chat

NOTICE_GUIDE = {
    "143(1)": {
        "name": "Intimation u/s 143(1)",
        "what": "Automated processing result — income recomputed. May demand tax/interest or give refund.",
        "action": "Check the computation. If demand: pay via challan 280 within 30 days OR file rectification u/s 154 if error. If refund: verify bank details on portal.",
        "deadline": "30 days to respond to demand / 4 years to file rectification",
        "urgency": "MEDIUM"
    },
    "143(2)": {
        "name": "Scrutiny Notice u/s 143(2)",
        "what": "Your return is selected for detailed scrutiny. AO will examine specific items.",
        "action": "MUST respond within stated time (usually 15-30 days). Gather all supporting documents for questioned items. Consider hiring a CA/advocate.",
        "deadline": "As per notice — typically 15-30 days. STRICT.",
        "urgency": "HIGH"
    },
    "148": {
        "name": "Notice u/s 148 — Income Escaping Assessment",
        "what": "AO believes income escaped assessment for a past year (up to 3-10 years ago depending on amount).",
        "action": "File return within 3 months OR respond with written objection. After Finance Act 2021: AO must issue 148A notice first. Check if 148A procedure was followed.",
        "deadline": "3 months from notice date to file return",
        "urgency": "HIGH"
    },
    "148A": {
        "name": "Notice u/s 148A — Show Cause before Reopening",
        "what": "Pre-notice before 148. AO must give you opportunity to explain before reopening assessment.",
        "action": "Respond within 7 days explaining why income did NOT escape assessment. This is your best chance to kill the reopening. Strong response here can prevent 148 from being issued.",
        "deadline": "7 days — SHORT. Act immediately.",
        "urgency": "VERY HIGH"
    },
    "139(9)": {
        "name": "Defective Return Notice u/s 139(9)",
        "what": "Your ITR has a defect (mismatch, missing schedule, wrong form). Return may be treated as not filed.",
        "action": "Correct and resubmit the return within 15 days on e-filing portal.",
        "deadline": "15 days from notice date. If ignored, return is void.",
        "urgency": "HIGH"
    },
    "156": {
        "name": "Notice of Demand u/s 156",
        "what": "Formal demand for tax/interest/penalty. Payment must be made within 30 days.",
        "action": "Pay within 30 days (challan 280) to avoid interest u/s 220(2) @ 1%/month. If disputing: file Stay of Demand application with AO AND appeal u/s 246A to CIT(A).",
        "deadline": "30 days to pay or apply for stay",
        "urgency": "HIGH"
    },
    "245": {
        "name": "Notice u/s 245 — Refund Adjustment",
        "what": "Your refund is being adjusted against a past demand. Intimation, not a demand.",
        "action": "Verify the demand being adjusted. If wrong demand: file 154 rectification. If already paid: submit payment proof. Respond within 30 days.",
        "deadline": "30 days",
        "urgency": "MEDIUM"
    },
    "271": {
        "name": "Penalty Notice u/s 271/271(1)(c)",
        "what": "Penalty for concealment of income or furnishing inaccurate particulars. Up to 300% of tax.",
        "action": "Respond with detailed explanation. Bona fide mistakes are defensible. Hire a CA/advocate.",
        "deadline": "As specified in notice",
        "urgency": "VERY HIGH"
    }
}

def decode(notice_text_or_section, additional_facts=""):
    # Try to detect notice section
    text_lower = notice_text_or_section.lower().replace(" ", "")
    detected = None
    for key in NOTICE_GUIDE:
        if key.lower().replace(" ","").replace("/","") in text_lower or key.lower() in notice_text_or_section.lower():
            detected = key
            break

    if detected:
        guide = NOTICE_GUIDE[detected]
        base = {
            "ok": True,
            "section": detected,
            "notice_name": guide["name"],
            "what_it_means": guide["what"],
            "what_to_do": guide["action"],
            "deadline": guide["deadline"],
            "urgency": guide["urgency"],
            "agent": "tax_notice"
        }
        if additional_facts:
            # LLM for specific guidance
            prompt = f"Income tax notice u/s {detected}. Additional context: {additional_facts}. Give specific advice."
            base["specific_advice"] = llm_chat([{"role":"user","content":prompt}], system="You are a senior Indian chartered accountant and tax lawyer.")
        return base

    # LLM decode for unknown notices
    prompt = f"""Decode this Indian Income Tax notice and explain:
1. Which section/type is it?
2. What does it mean in simple terms?
3. What EXACT action must I take?
4. What is the deadline?
5. What happens if I ignore it?
6. Do I need a CA/advocate?

Notice text:
{notice_text_or_section[:3000]}

{f'Additional facts: {additional_facts}' if additional_facts else ''}"""

    resp = llm_chat([{"role":"user","content":prompt}], system="You are a senior Indian CA and tax lawyer. Be specific and actionable.")
    return {"ok": True, "analysis": resp, "agent": "tax_notice", "disclaimer": "Consult a CA for your specific situation."}

def capital_gains(asset_type, buy_price, sell_price, buy_date, sell_date, units=1, indexation=True):
    """Calculate LTCG/STCG under Indian Income Tax Act."""
    import datetime
    try:
        bd = datetime.date.fromisoformat(buy_date)
        sd = datetime.date.fromisoformat(sell_date)
    except:
        return {"ok": False, "error": "Use YYYY-MM-DD date format"}

    holding_days = (sd - bd).days
    holding_months = holding_days / 30.4

    # Determine LTCG vs STCG thresholds
    THRESHOLDS = {
        "equity": {"ltcg_months": 12, "ltcg_rate": 12.5, "stcg_rate": 20, "ltcg_exemption": 125000, "note": "Listed equity/equity MF. LTCG >1yr @12.5% (exempt up to ₹1.25L). STCG @20%."},
        "property": {"ltcg_months": 24, "ltcg_rate": 12.5, "stcg_rate": None, "ltcg_exemption": 0, "note": "Immovable property. LTCG >2yr @12.5% (no indexation from FY2024-25). STCG at slab rate."},
        "debt_mf": {"ltcg_months": 36, "ltcg_rate": None, "stcg_rate": None, "note": "Debt MF bought after Apr 2023: ALL gains taxed at slab rate (no LTCG benefit)."},
        "gold": {"ltcg_months": 24, "ltcg_rate": 12.5, "stcg_rate": None, "ltcg_exemption": 0, "note": "Physical gold/Gold MF. LTCG >2yr @12.5%. STCG at slab rate."},
        "crypto": {"ltcg_months": 0, "ltcg_rate": 30, "stcg_rate": 30, "ltcg_exemption": 0, "note": "VDA/Crypto: flat 30% on ALL gains under Section 115BBH. No deduction except cost. No loss set-off."},
    }

    info = THRESHOLDS.get(asset_type.lower(), THRESHOLDS["equity"])
    total_cost = buy_price * units
    total_sale = sell_price * units
    gross_gain = total_sale - total_cost

    is_lt = holding_months >= info["ltcg_months"] and info["ltcg_months"] > 0

    result = {
        "ok": True,
        "asset_type": asset_type,
        "buy_price": buy_price, "sell_price": sell_price, "units": units,
        "total_cost": total_cost, "total_sale": total_sale,
        "gross_gain": round(gross_gain, 2),
        "holding_days": holding_days,
        "holding_months": round(holding_months, 1),
        "gain_type": "LTCG" if is_lt else "STCG",
        "applicable_rate": f"{info['ltcg_rate']}%" if is_lt and info.get('ltcg_rate') else f"{info['stcg_rate']}%" if info.get('stcg_rate') else "Slab rate",
        "note": info["note"],
        "agent": "tax_notice"
    }

    if is_lt and info.get('ltcg_rate') and info.get('ltcg_exemption', 0) > 0:
        taxable = max(0, gross_gain - info['ltcg_exemption'])
        result["taxable_gain"] = round(taxable, 2)
        result["estimated_tax"] = round(taxable * info['ltcg_rate'] / 100, 2)
        result["exemption_applied"] = f"₹{info['ltcg_exemption']:,} annual exemption"
    elif is_lt and info.get('ltcg_rate'):
        result["estimated_tax"] = round(max(0, gross_gain) * info['ltcg_rate'] / 100, 2)

    return result

if __name__ == "__main__":
    print(decode("143(1)"))
    print(capital_gains("equity", 1000, 1500, "2022-01-01", "2024-06-01", units=100))
