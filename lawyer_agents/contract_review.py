"""
Contract Review Agent — flag risky clauses in Indian contracts.
Paste any contract text → get risk-flagged analysis.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from llm_client import chat as llm_chat

RISK_CATEGORIES = [
    "one-sided indemnity or unlimited liability",
    "automatic renewal traps (evergreen clauses)",
    "unilateral amendment rights by one party",
    "jurisdiction outside India or inconvenient forum",
    "non-compete too broad in scope/duration/geography",
    "IP ownership — employer/party claims all inventions",
    "notice period too long or unfair termination clause",
    "missing payment terms or penalty-free delayed payment",
    "force majeure too narrow or absent",
    "dispute resolution missing (no arbitration/mediation clause)",
    "liquidated damages disproportionate",
    "confidentiality clause with no time limit",
    "missing limitation of liability cap",
    "unfair penalty / lock-in period",
    "governing law is foreign law"
]

SYSTEM_PROMPT = f"""You are a senior Indian contract lawyer reviewing a contract for a CLIENT (not the drafter).
Your job is to FLAG RISKS, not validate the contract.

For each risk found:
1. Quote the exact clause (first 30 words)
2. Explain why it is risky for the client
3. Suggest a fix or negotiating position
4. Rate severity: HIGH / MEDIUM / LOW

Categories to check:
{chr(10).join(f'- {r}' for r in RISK_CATEGORIES)}

Also note: missing clauses that SHOULD be there for protection.
End with a 1-paragraph overall risk summary.
DISCLAIMER: AI analysis only. Have a licensed advocate review before signing."""

def review(contract_text, party_role="party signing"):
    if len(contract_text) < 50:
        return {"ok": False, "error": "Contract text too short. Please paste the actual contract."}

    prompt = f"""Review this contract from the perspective of: {party_role}

CONTRACT TEXT:
{contract_text[:8000]}

Flag all risky clauses, missing protections, and unfair terms."""

    resp = llm_chat([{"role": "user", "content": prompt}], system=SYSTEM_PROMPT)
    return {
        "ok": True,
        "party_role": party_role,
        "review": resp,
        "agent": "contract_review",
        "tip": "Upload PDF text by copy-pasting from the PDF viewer."
    }

if __name__ == "__main__":
    sample = "This Agreement is entered into between Company A and Employee. Employee agrees to a 3-year non-compete post termination covering all of India in any related field. Company may amend this agreement at any time without notice. All disputes shall be resolved in Courts of England."
    print(review(sample, "employee")["review"][:500])
