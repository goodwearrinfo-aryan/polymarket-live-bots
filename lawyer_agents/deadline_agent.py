"""
Deadline/Limitation Period Calculator — India's Limitation Act 1963.
Calculates filing deadlines for common causes of action.
"""
import sys, os, datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from llm_client import chat as llm_chat

# Key limitation periods under Schedule to Limitation Act 1963
LIMITATION_TABLE = {
    "cheque_bounce_138": {
        "period_days": 30,
        "start": "date of legal notice expiry (15 days after receipt)",
        "act": "Section 138 NI Act — file complaint within 30 days of cause arising"
    },
    "money_recovery": {
        "period_days": 365*3,
        "start": "date debt became due",
        "act": "Article 18/22 Limitation Act — 3 years"
    },
    "property_recovery": {
        "period_days": 365*12,
        "start": "date of dispossession",
        "act": "Article 65 Limitation Act — 12 years"
    },
    "tort_personal_injury": {
        "period_days": 365*3,
        "start": "date of injury",
        "act": "Article 82/83 — 3 years"
    },
    "consumer_complaint": {
        "period_days": 365*2,
        "start": "date of cause of action / deficiency",
        "act": "Section 69 Consumer Protection Act 2019 — 2 years (condonable)"
    },
    "contract_breach": {
        "period_days": 365*3,
        "start": "date of breach",
        "act": "Article 55 Limitation Act — 3 years"
    },
    "labour_industrial": {
        "period_days": 365*3,
        "start": "date of dismissal/dispute",
        "act": "Industrial Disputes Act + Limitation Act — 3 years"
    },
    "rti_first_appeal": {
        "period_days": 30,
        "start": "date of PIO order or expiry of 30-day window",
        "act": "Section 19(1) RTI Act — 30 days"
    },
    "rti_second_appeal": {
        "period_days": 90,
        "start": "date of FAA order or expiry",
        "act": "Section 19(3) RTI Act — 90 days"
    },
    "cheque_civil_suit": {
        "period_days": 365*3,
        "start": "date cheque dishonoured",
        "act": "Article 22 — 3 years for civil suit"
    },
    "motor_accident": {
        "period_days": 365*3,
        "start": "date of accident",
        "act": "Section 166 Motor Vehicles Act + Article 82 — 3 years (no limitation strictly under MV Act for fatal claims)"
    },
    "rent_arrears": {
        "period_days": 365*3,
        "start": "each month rent became due",
        "act": "Article 52 Limitation Act — 3 years from due date"
    },
}

def calculate(cause_of_action: str, event_date_str: str):
    """
    cause_of_action: one of the keys in LIMITATION_TABLE or free text
    event_date_str: 'YYYY-MM-DD'
    """
    info = LIMITATION_TABLE.get(cause_of_action.lower().replace(" ", "_"))

    try:
        event_date = datetime.date.fromisoformat(event_date_str)
    except Exception:
        return {"ok": False, "error": "Invalid date. Use YYYY-MM-DD format."}

    if info:
        deadline = event_date + datetime.timedelta(days=info["period_days"])
        today = datetime.date.today()
        days_left = (deadline - today).days
        return {
            "ok": True,
            "cause": cause_of_action,
            "event_date": event_date_str,
            "limitation_period": f"{info['period_days']//365} years" if info['period_days'] >= 365 else f"{info['period_days']} days",
            "deadline": deadline.isoformat(),
            "days_remaining": days_left,
            "status": "IN TIME" if days_left > 0 else "LIMITATION EXPIRED",
            "warning": "URGENT — less than 30 days left" if 0 < days_left < 30 else None,
            "legal_basis": info["act"],
            "starts_from": info["start"],
            "agent": "deadline_agent"
        }
    else:
        # LLM fallback for uncommon causes
        prompt = f"""Calculate the limitation period under Indian law for: {cause_of_action}
Event date: {event_date_str}
Provide: applicable act/article, limitation period in years/days, filing deadline date, and current status (in time / expired).
Be specific and cite the exact article of the Limitation Act 1963 or relevant special statute."""
        resp = llm_chat([{"role": "user", "content": prompt}], system="You are an expert on India's Limitation Act 1963.")
        return {"ok": True, "cause": cause_of_action, "analysis": resp, "agent": "deadline_agent"}

def list_causes():
    return list(LIMITATION_TABLE.keys())

if __name__ == "__main__":
    r = calculate("consumer_complaint", "2023-06-01")
    print(r)
    r2 = calculate("cheque_bounce_138", "2024-01-15")
    print(r2)
