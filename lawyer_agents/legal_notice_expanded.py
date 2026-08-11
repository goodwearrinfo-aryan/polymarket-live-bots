"""
Expanded Legal Notice Generator — 20+ notice types, professional drafting.
Extends legal_notice.py with more types and higher quality drafts.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from llm_client import chat as llm_chat

NOTICE_TYPES = {
    # Consumer / Money
    "demand_money": "Demand for repayment of money lent / outstanding dues",
    "cheque_bounce": "Section 138 NI Act — dishonour of cheque — 30-day demand notice (MANDATORY before court filing)",
    "consumer_complaint": "Consumer Protection Act 2019 — defective goods/services, unfair trade practice",
    "insurance_rejection": "Challenge rejection/repudiation of insurance claim as arbitrary/illegal",
    "emi_dispute": "Dispute over EMI deduction, bank charges, loan account irregularities",
    # Property / Housing
    "landlord_eviction": "Landlord notice to tenant for non-payment or lease expiry (Transfer of Property Act + state Rent Control)",
    "tenant_rights": "Tenant notice to landlord for essential services, illegal eviction, security deposit refund",
    "property_encroachment": "Notice to remove encroachment from property",
    "builder_possession": "Notice to builder for delayed possession + compensation (RERA S.18)",
    "society_maintenance": "Housing society notice for unpaid maintenance/dues",
    "property_damage": "Demand for compensation for property damaged by neighbor/third party",
    # Employment
    "wrongful_termination": "Notice challenging illegal/wrongful termination",
    "unpaid_salary": "Demand for unpaid salary, PF, gratuity, full & final settlement",
    "non_compete_violation": "Notice against former employee violating non-compete/NDA",
    "workplace_harassment": "Notice under POSH Act / workplace harassment (to employer/ICC)",
    # Defamation / Reputation
    "defamation": "Notice for defamatory statements (oral/written/online) — civil/criminal",
    "takedown_notice": "Notice to platform/person to remove defamatory/illegal online content",
    # Cyber / IT
    "data_breach": "Notice under DPDP Act 2023 / IT Act for unauthorised data processing/breach",
    "cyber_fraud": "Notice to bank/accused for cyber fraud, phishing, UPI fraud",
    # Business / Commercial
    "breach_of_contract": "Notice for breach of contract — supply, service, partnership, joint venture",
    "vendor_default": "Notice to vendor/supplier for defective delivery or non-delivery",
    "copyright_infringement": "Copyright infringement notice (Copyright Act 1957 S.51)",
    "trademark_infringement": "Trademark infringement / passing off notice (Trade Marks Act 1999)",
    # Medical
    "medical_negligence": "Notice for medical negligence causing injury/death",
    # Miscellaneous
    "noise_nuisance": "Notice for noise/nuisance under IPC/BNS and Environment Protection Act",
    "rera_complaint": "RERA notice to builder for defects, delay, non-compliance",
    "road_accident": "Motor accident compensation notice (Motor Vehicles Act 1988 S.166)",
}

SYSTEM_PROMPT = """You are a senior Indian advocate drafting formal legal notices.
Draft notice in proper legal format:
1. LETTERHEAD: "LEGAL NOTICE" as heading, date, reference number
2. TO: Noticee's full address
3. THROUGH: Advocate's name (leave blank for self-representation)
4. FROM: Client name and address
5. SUBJECT: Clear subject line
6. BODY:
   - "Under instructions from and on behalf of my client [NAME]..."
   - Factual background (numbered paragraphs)
   - Legal violations with specific Act/Section
   - Demand/remedy sought
   - Time period (typically 15-30 days)
   - Warning: legal proceedings if not complied
7. CLOSING: "Your early compliance will save you from legal expenses..."
8. Signature block for advocate/sender

Use formal legal English. Cite specific sections. Be firm but professional.
DISCLAIMER: AI-drafted notice. Review with a lawyer before sending for high-stakes matters."""

def draft(notice_type, details, sender_name, sender_address, receiver_name, receiver_address,
          demand_amount=None, deadline_days=30, lang=None):
    notice_desc = NOTICE_TYPES.get(notice_type, notice_type)

    # Special: cheque bounce has strict statutory requirements
    cheque_note = ""
    if notice_type == "cheque_bounce":
        cheque_note = """CRITICAL CHEQUE BOUNCE NOTICE REQUIREMENTS:
1. Must be sent within 30 days of cheque return memo from bank
2. Drawer has 15 days from receipt to pay
3. If no payment in 15 days → file complaint in JMFC within 30 days of expiry
4. Include: cheque number, date, amount, bank, reason for dishonour, demand for payment
5. Send by SPEED POST (proof of service crucial) + courier"""

    prompt = f"""Draft a formal legal notice for:
TYPE: {notice_desc}
SENDER (Client): {sender_name}, {sender_address}
RECEIVER (Noticee): {receiver_name}, {receiver_address}
AMOUNT CLAIMED: {'₹' + str(demand_amount) + ' (+ interest + legal costs)' if demand_amount else 'as per facts'}
DEADLINE TO COMPLY: {deadline_days} days from receipt
{cheque_note}

FACTS AND CIRCUMSTANCES:
{details}

Draft a COMPLETE, ready-to-send legal notice. Include specific Indian law sections applicable.
The notice must:
- Establish facts clearly
- State legal position and violations
- Make specific demand with amount if applicable
- Give {deadline_days}-day deadline
- Warn of legal proceedings (civil/criminal as appropriate)
- Be firm and professional"""

    resp = llm_chat([{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}])

    result = {
        "ok": True,
        "notice_type": notice_type,
        "description": notice_desc,
        "draft": resp,
        "sending_tips": _sending_tips(notice_type, deadline_days),
        "agent": "legal_notice_expanded"
    }

    if notice_type == "cheque_bounce":
        result["cheque_bounce_critical"] = {
            "step1": "Send notice within 30 days of bank return memo (today if recent)",
            "step2": "Send by SPEED POST with AD (delivery confirmation) — keep all receipts",
            "step3": "If no payment in 15 days → file Section 138 NI Act complaint in JMFC",
            "step4": "File complaint within 30 days of 15-day notice period expiry",
            "step5": "Court fee: nominal. But SUCCESS rate high — criminal pressure works"
        }

    if lang and lang != "english":
        try:
            from lang import from_english
            result["draft_translated"] = from_english(resp, lang)
        except:
            pass

    return result

def _sending_tips(notice_type, deadline_days):
    tips = [
        f"Send by: SPEED POST with Acknowledgement Due (AD) — legal proof of delivery",
        "Also send by: Email with read receipt + WhatsApp (screenshot as evidence)",
        "Keep: Original notice copy, postal receipt, AD card when returned",
        f"Deadline: {deadline_days} days from the date noticee RECEIVES it (not from sending)",
        "If no response: file case in appropriate court/forum"
    ]
    if notice_type in ["defamation", "trademark_infringement", "copyright_infringement"]:
        tips.append("ALSO send to platform (if online content) with DMCA/IT Act takedown request")
    if notice_type == "medical_negligence":
        tips.append("Collect all medical records, bills, prescriptions BEFORE sending notice")
        tips.append("Also file complaint with State Medical Council simultaneously")
    return tips

def list_types():
    return {k: v for k, v in NOTICE_TYPES.items()}

if __name__ == "__main__":
    import json
    print("Available notice types:", len(NOTICE_TYPES))
    print(json.dumps(list(NOTICE_TYPES.keys()), indent=2))
