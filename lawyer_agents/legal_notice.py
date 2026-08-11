"""
Legal Notice Agent — draft demand notices, Section 138 cheque bounce notices,
consumer complaints, employer notices for Indian law.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from llm_client import chat as llm_chat

NOTICE_TYPES = {
    "demand": "civil demand notice for money recovery",
    "cheque_bounce": "Section 138 Negotiable Instruments Act cheque dishonour notice",
    "consumer": "consumer complaint under Consumer Protection Act 2019",
    "employment": "employment termination / notice period / dues recovery notice",
    "landlord": "eviction / rent recovery notice from landlord",
    "tenant": "tenant rights notice to landlord for repairs/deposit return",
    "deficiency": "service deficiency notice to bank/insurance/telecom company",
    "rera": "RERA complaint notice to builder for possession delay",
}

SYSTEM_PROMPT = """You are an expert Indian lawyer drafting formal legal notices.
Draft notices that are:
- Properly formatted with sender/recipient/date/subject headers
- Cite the exact applicable Indian law sections (IPC/CrPC/BNS/Consumer Protection Act etc.)
- Include a specific demand with a 15-day or 30-day reply deadline
- Close with "failing which we will be constrained to initiate appropriate legal proceedings"
- Professional but firm in tone
Always end with: DISCLAIMER: This is an AI-generated draft. Have a licensed advocate review before sending."""

def draft_notice(notice_type, details, lang=None):
    """
    details: dict with sender_name, sender_address, recipient_name,
             recipient_address, facts, demand_amount (if applicable)
    """
    ntype = NOTICE_TYPES.get(notice_type, notice_type)
    prompt = f"""Draft a formal Indian legal notice for: {ntype}

Details provided:
{chr(10).join(f'- {k}: {v}' for k, v in details.items())}

Draft a complete, ready-to-send legal notice with all proper legal citations."""

    resp = llm_chat([{"role": "user", "content": prompt}], system=SYSTEM_PROMPT)
    return {"ok": True, "notice_type": ntype, "draft": resp, "agent": "legal_notice"}

def interactive():
    print("=== Legal Notice Agent ===")
    print("Types:", ", ".join(NOTICE_TYPES.keys()))
    ntype = input("Notice type: ").strip()
    details = {}
    for field in ["sender_name", "sender_address", "recipient_name", "recipient_address", "facts"]:
        val = input(f"{field}: ").strip()
        if val:
            details[field] = val
    result = draft_notice(ntype, details)
    print("\n" + result["draft"])

if __name__ == "__main__":
    interactive()
