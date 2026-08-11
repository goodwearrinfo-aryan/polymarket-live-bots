"""
Document Drafter Agent — generates common Indian legal documents.
Supports: rent agreement, affidavit, NDA, will, vakalatnama, power of attorney.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from llm_client import chat as llm_chat

TEMPLATES = {
    "rent_agreement": {
        "desc": "11-month lease agreement (avoids registration requirement in most states)",
        "fields": ["landlord_name", "tenant_name", "property_address", "monthly_rent", "security_deposit", "start_date", "state"],
    },
    "affidavit": {
        "desc": "General-purpose sworn affidavit for courts, passport, bank, etc.",
        "fields": ["deponent_name", "deponent_address", "purpose", "facts_to_declare", "state"],
    },
    "nda": {
        "desc": "Non-Disclosure Agreement with Indian law jurisdiction",
        "fields": ["party_a_name", "party_a_address", "party_b_name", "party_b_address", "purpose", "duration_years", "state"],
    },
    "will": {
        "desc": "Last Will and Testament under Indian Succession Act / Hindu Succession Act",
        "fields": ["testator_name", "testator_address", "religion", "assets_description", "beneficiaries", "executor_name"],
    },
    "power_of_attorney": {
        "desc": "General or Special Power of Attorney",
        "fields": ["grantor_name", "grantor_address", "attorney_name", "attorney_address", "powers_granted", "state"],
    },
    "vakalatnama": {
        "desc": "Vakalatnama — authority to legal counsel to appear in court",
        "fields": ["client_name", "client_address", "advocate_name", "advocate_enrollment", "court", "case_details"],
    },
}

SYSTEM_PROMPT = """You are an expert Indian lawyer drafting legal documents.
Produce complete, properly formatted documents with:
- Appropriate recitals and witnessing clauses
- Correct applicable Indian law references (Transfer of Property Act, Registration Act, etc.)
- Stamp duty notice where required
- Signature/witness/notarization blocks
- State-specific customizations when state is provided
DISCLAIMER: AI-generated draft. Have a licensed advocate review before executing."""

def draft(doc_type, fields):
    template = TEMPLATES.get(doc_type)
    if not template:
        return {"ok": False, "error": f"Unknown document type. Available: {list(TEMPLATES.keys())}"}

    prompt = f"""Draft a complete {doc_type.replace('_', ' ')} — {template['desc']}.

Details:
{chr(10).join(f'- {k}: {v}' for k, v in fields.items())}

Produce a complete, execution-ready document."""

    resp = llm_chat([{"role": "user", "content": prompt}], system=SYSTEM_PROMPT)
    return {
        "ok": True,
        "doc_type": doc_type,
        "draft": resp,
        "agent": "document_drafter"
    }

def list_templates():
    return {k: v["desc"] for k, v in TEMPLATES.items()}

if __name__ == "__main__":
    r = draft("rent_agreement", {
        "landlord_name": "Ramesh Kumar",
        "tenant_name": "Suresh Sharma",
        "property_address": "Flat 4B, Andheri West, Mumbai 400053",
        "monthly_rent": "Rs.25,000",
        "security_deposit": "Rs.75,000",
        "start_date": "2026-07-01",
        "state": "Maharashtra"
    })
    print(r["draft"][:500])
