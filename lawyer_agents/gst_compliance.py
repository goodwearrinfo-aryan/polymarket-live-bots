"""
GST Compliance Agent — filing calendar, late fee calculator, notice decoder, ITC rules.
"""
import sys, os, datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from llm_client import chat as llm_chat

GST_CALENDAR = {
    "GSTR-1": {"frequency": "Monthly (11th) / Quarterly (13th)", "who": "All registered taxpayers", "desc": "Outward supply details"},
    "GSTR-3B": {"frequency": "Monthly (20th)", "who": "All registered", "desc": "Summary return + tax payment"},
    "GSTR-9": {"frequency": "Annual — 31 Dec", "who": "Turnover >₹2Cr", "desc": "Annual return"},
    "GSTR-9C": {"frequency": "Annual — 31 Dec", "who": "Turnover >₹5Cr", "desc": "Reconciliation statement (CA certified)"},
    "GSTR-4": {"frequency": "Annual — 30 April", "who": "Composition scheme taxpayers", "desc": "Annual return for composition"},
    "GSTR-7": {"frequency": "Monthly — 10th", "who": "TDS deductors under GST", "desc": "TDS return"},
    "GSTR-ITC-04": {"frequency": "Quarterly — 25th of month after quarter", "who": "Principal/Job worker", "desc": "Job work return"},
    "PMT-06": {"frequency": "Monthly — 25th (QRMP scheme)", "who": "QRMP taxpayers", "desc": "Challan for monthly tax payment"},
}

LATE_FEE = {
    "GSTR-1": {"per_day": 50, "nil_per_day": 20, "max": 10000},
    "GSTR-3B": {"per_day": 50, "nil_per_day": 20, "max": 10000, "interest": "18% pa on tax due"},
    "GSTR-9": {"per_day": 200, "nil_per_day": 200, "max_pct": 0.25},  # 0.25% of turnover
    "GSTR-4": {"per_day": 50, "nil_per_day": 20, "max": 2000},
}

GST_NOTICES = {
    "DRC-01": "Show Cause Notice for tax demand. Respond within 30 days with DRC-06 (reply) or DRC-03 (voluntary payment).",
    "DRC-01A": "Summary notice before DRC-01. You have 30 days to reply. Respond to avoid formal SCN.",
    "ASMT-10": "Scrutiny of returns notice. Explain discrepancies between GSTR-1 and GSTR-3B or between books and returns.",
    "ASMT-11": "Your reply to ASMT-10. File within 15 days.",
    "CMP-05": "Show cause notice if department believes you wrongly opted for composition scheme.",
    "ADT-01": "Audit notice. Department will audit your books for a specific period. Cooperate fully.",
    "REG-17": "Show cause notice for cancellation of GST registration. Respond within 7 days.",
    "REG-23": "Notice if application for revocation of cancellation is deficient.",
}

SYSTEM_PROMPT = """You are a senior GST practitioner and chartered accountant.
Cite specific CGST Act sections, Rules, and Circulars.
Cover: registration threshold, composition scheme eligibility, ITC rules, e-invoicing, e-way bill.
Be practical and specific. DISCLAIMER: Consult a GST practitioner for your specific case."""

def late_fee_calc(return_type, days_late, turnover=0, is_nil_return=False):
    info = LATE_FEE.get(return_type.upper())
    if not info:
        return {"ok": False, "error": f"Unknown return type. Known: {list(LATE_FEE.keys())}"}
    rate = info["nil_per_day"] if is_nil_return else info["per_day"]
    fee = days_late * rate
    if "max" in info:
        fee = min(fee, info["max"])
    elif "max_pct" in info and turnover:
        fee = min(fee, turnover * info["max_pct"] / 100)
    return {
        "ok": True, "return_type": return_type, "days_late": days_late,
        "late_fee": round(fee, 2), "rate_per_day": rate,
        "note": info.get("interest", ""), "agent": "gst_compliance"
    }

def decode_notice(notice_type, facts=""):
    base = GST_NOTICES.get(notice_type.upper())
    if base and not facts:
        return {"ok": True, "notice": notice_type, "meaning": base, "agent": "gst_compliance"}
    prompt = f"""GST notice analysis:
Notice type: {notice_type}
{f'Facts: {facts}' if facts else ''}
Base guidance: {base or 'unknown notice type'}

Provide:
1. What this notice means
2. Exact action required + deadline
3. Documents to gather
4. Draft response approach
5. Consequences of ignoring it"""
    resp = llm_chat([{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}])
    return {"ok": True, "notice": notice_type, "analysis": resp, "agent": "gst_compliance"}

def compliance_check(turnover, state, sector, is_interstate=False):
    prompt = f"""GST compliance check for:
Annual turnover: ₹{turnover:,}
State: {state}
Sector: {sector}
Interstate supply: {is_interstate}

Check and advise:
1. Is GST registration mandatory? (threshold: ₹40L goods / ₹20L services / ₹10L NE states)
2. Composition scheme eligibility (≤₹1.5Cr) — pros/cons
3. QRMP scheme eligibility (≤₹5Cr) — monthly vs quarterly filing
4. E-invoicing mandatory? (threshold ₹5Cr)
5. E-way bill requirement for this business
6. ITC eligibility — what can be claimed
7. Reverse charge mechanism applicability
8. Monthly compliance calendar for this business
9. Common GST mistakes for {sector} sector to avoid"""
    resp = llm_chat([{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}])
    return {
        "ok": True, "analysis": resp,
        "calendar": GST_CALENDAR,
        "einvoicing_mandatory": turnover >= 50000000,  # ₹5Cr
        "composition_eligible": turnover <= 15000000 and not is_interstate,
        "agent": "gst_compliance"
    }

if __name__ == "__main__":
    import json
    print(json.dumps(late_fee_calc("GSTR-3B", 45, is_nil_return=False), indent=2))
    print(json.dumps(decode_notice("DRC-01"), indent=2))
