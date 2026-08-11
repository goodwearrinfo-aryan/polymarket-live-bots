"""
Succession & Inheritance Guide — who inherits what under Indian personal laws.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from llm_client import chat as llm_chat

HINDU_SUCCESSION_CLASS1 = ["Spouse", "Sons", "Daughters", "Mother", "Widow of predeceased son", "Son of predeceased son", "Daughter of predeceased son"]
MUSLIM_HEIRS_SHARERS = {"Husband": "1/2 (no children) / 1/4 (with children)", "Wife": "1/4 (no children) / 1/8 (with children)", "Daughter": "1/2 (sole heir) / share residual with son", "Mother": "1/6 or 1/3", "Father": "1/6"}

SYSTEM_PROMPT = """You are an expert in Indian succession and inheritance laws.
Cover: Hindu Succession Act 1956 (amended 2005), Muslim Personal Law (Shariat Application Act), Indian Succession Act 1925 (Christians/Parsis).
Be specific about who inherits, in what proportion, and what property types are affected.
Include: ancestral vs self-acquired property distinction, daughter's equal rights (2005 amendment), agricultural land restrictions in some states.
DISCLAIMER: Succession matters are complex. Consult a lawyer for estate planning."""

def who_inherits(religion, deceased_relatives, assets, has_will=False, state=""):
    prompt = f"""Succession planning / inheritance calculation under Indian law:
Religion: {religion}
Deceased relatives (who has passed): {deceased_relatives}
Surviving family: implicit from above
Assets: {assets}
Has Will: {has_will}
State: {state or 'not specified'}

Calculate:
1. Which law applies (Hindu Succession Act / Muslim Personal Law / Indian Succession Act)
2. Who are the legal heirs and in what proportion
3. Is any property ancestral (coparcenary) vs self-acquired?
4. Do daughters have equal rights? (Post-2005 amendment details)
5. What documents needed to claim inheritance (succession certificate, probate, etc.)
6. Any state-specific restrictions (e.g. agricultural land laws)
7. Tax implications of inheritance (India has NO inheritance tax, but share transfers may have stamp duty)"""

    resp = llm_chat([{"role":"system","content":SYSTEM_PROMPT},{"role":"user","content":prompt}])
    return {
        "ok": True,
        "analysis": resp,
        "hindu_class1_heirs": HINDU_SUCCESSION_CLASS1,
        "muslim_key_shares": MUSLIM_HEIRS_SHARERS,
        "no_inheritance_tax": "India has NO inheritance tax. Gifts above ₹50,000 from non-relatives are taxable as income for recipient.",
        "documents_needed": ["Death certificate", "Legal heir certificate / succession certificate (court-issued)", "Property documents", "Aadhaar/PAN of all heirs"],
        "agent": "succession"
    }

if __name__ == "__main__":
    r = who_inherits("Hindu", "Father died in 2024 (mother alive, 2 sons, 1 daughter)", "Self-acquired house in Delhi worth Rs 1 crore, savings of Rs 20 lakh, no Will")
    print(r["analysis"][:500])
