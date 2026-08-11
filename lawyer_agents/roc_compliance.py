"""
ROC Annual Compliance Calendar — Companies Act 2013 deadlines, penalties, forms.
"""
import sys, os, datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from llm_client import chat as llm_chat

ROC_DEADLINES = [
    {"form": "DIR-3 KYC", "due": "30 Sep (annual)", "who": "Every director with DIN", "fee_if_late": "₹5,000 penalty + DIN deactivated", "priority": "HIGH"},
    {"form": "MGT-7 / MGT-7A", "due": "60 days from AGM (usually Nov 30)", "who": "All Pvt Ltd companies", "desc": "Annual return", "fee_if_late": "₹100/day penalty", "priority": "HIGH"},
    {"form": "AOC-4", "due": "30 days from AGM (usually Oct 30)", "who": "All Pvt Ltd companies", "desc": "Financial statements filing", "fee_if_late": "₹100/day penalty", "priority": "HIGH"},
    {"form": "ADT-1", "due": "15 days from AGM", "who": "All companies", "desc": "Auditor appointment", "fee_if_late": "₹100/day", "priority": "MEDIUM"},
    {"form": "AGM", "due": "30 Sep (within 6 months of FY close)", "who": "All companies", "desc": "Annual General Meeting must be held", "fee_if_late": "₹1L + ₹5,000/day for officers in default", "priority": "HIGH"},
    {"form": "INC-20A", "due": "180 days from incorporation", "who": "Companies incorporated after Nov 2019", "desc": "Commencement of business declaration — ONE TIME", "fee_if_late": "₹50,000 + ₹1,000/day. Company may be struck off", "priority": "CRITICAL (one-time)"},
    {"form": "MSME-1", "due": "Apr 30 (for Oct-Mar) / Oct 31 (for Apr-Sep)", "who": "Companies with MSME dues >45 days", "desc": "Half-yearly return on MSME payments", "fee_if_late": "₹25,000 penalty", "priority": "MEDIUM"},
    {"form": "DPT-3", "due": "30 Jun (annual)", "who": "All companies", "desc": "Return of deposits / transactions not considered deposits", "fee_if_late": "₹5,000 + ₹500/day", "priority": "MEDIUM"},
    {"form": "BEN-2", "due": "30 days of change in beneficial ownership", "who": "Companies with beneficial owners holding >10%", "desc": "Declaration of Significant Beneficial Ownership", "fee_if_late": "₹50,000 + ₹200/day", "priority": "MEDIUM"},
    {"form": "LLP-11", "due": "30 May (annual)", "who": "All LLPs", "desc": "LLP annual return", "fee_if_late": "₹100/day (no cap)", "priority": "HIGH — for LLPs"},
    {"form": "LLP-8", "due": "30 Oct (annual)", "who": "All LLPs with turnover >₹40L or contribution >₹25L", "desc": "Statement of accounts & solvency", "fee_if_late": "₹100/day", "priority": "HIGH — for LLPs"},
    {"form": "ACTIVE (INC-22A)", "due": "ONE TIME — file immediately if not done", "who": "Companies incorporated before Dec 2017", "desc": "Active Company Tagging — registers registered office", "fee_if_late": "₹10,000 + ₹200/day", "priority": "CRITICAL if pending"},
]

SYSTEM_PROMPT = "You are a Company Secretary (CS) expert in Companies Act 2013 compliance and ROC filings."

def calendar(entity_type, incorporation_date, turnover=0, has_foreign_investment=False):
    today = datetime.date.today()
    fy_end = datetime.date(today.year if today.month > 3 else today.year - 1, 3, 31)

    prompt = f"""ROC compliance calendar for:
Entity: {entity_type}
Incorporated: {incorporation_date}
Turnover: ₹{turnover:,}
Foreign investment: {has_foreign_investment}
Today: {today}
Financial year end: {fy_end}

Generate:
1. Upcoming compliance deadlines in next 6 months (specific dates)
2. Any overdue filings that need immediate attention
3. One-time filings if recently incorporated (INC-20A, ACTIVE, ADT-1)
4. {'FEMA/RBI reporting requirements for foreign investment' if has_foreign_investment else 'Board meeting frequency requirements'}
5. Estimated professional fees for full-year compliance
6. Consequences of non-compliance (strike-off, name removal, director DIN deactivation)
7. MCA21 v3 portal tips — how to track all pending filings"""
    resp = llm_chat([{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}])

    # Filter deadlines by entity type
    relevant = [d for d in ROC_DEADLINES if entity_type.lower() in ["pvt_ltd", "llp"] and
                (("LLP" not in d.get("desc","") and entity_type.lower() == "pvt_ltd") or
                 ("LLP" in d.get("who","") and entity_type.lower() == "llp") or
                 "LLP" not in d.get("who",""))]

    return {
        "ok": True, "entity": entity_type,
        "compliance_analysis": resp,
        "all_deadlines": ROC_DEADLINES,
        "mca_portal": "mca.gov.in/mcafoportal/login.do",
        "fy_end": str(fy_end),
        "agent": "roc_compliance"
    }

def penalty_calc(form, days_late):
    d = next((x for x in ROC_DEADLINES if x["form"] == form), None)
    if not d:
        return {"ok": False, "error": f"Unknown form. Known forms: {[x['form'] for x in ROC_DEADLINES]}"}
    fee_text = d.get("fee_if_late", "See MCA portal")
    return {"ok": True, "form": form, "days_late": days_late, "penalty_info": fee_text,
            "note": "Pay via MCA portal → MCA services → Pay later fees. Additional prosecution possible for serious defaults.", "agent": "roc_compliance"}

if __name__ == "__main__":
    import json
    print(json.dumps(penalty_calc("MGT-7", 30), indent=2))
    print("Total ROC deadlines tracked:", len(ROC_DEADLINES))
