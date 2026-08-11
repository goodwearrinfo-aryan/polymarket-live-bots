"""
Trademark & IP Agent — trademark class finder, filing guide, copyright, patent basics.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from llm_client import chat as llm_chat

# Nice Classification (condensed — key classes for Indian businesses)
TM_CLASSES = {
    1: "Chemicals, adhesives", 2: "Paints, varnishes, preservatives",
    3: "Cosmetics, cleaning products, soaps", 4: "Lubricants, candles, fuels",
    5: "Pharmaceuticals, medical preparations, dietary supplements",
    6: "Metals, hardware, locks, pipes", 7: "Machines, engines, tools",
    8: "Hand tools, cutlery", 9: "Electronics, software, computers, apps, smartphones",
    10: "Medical/surgical devices", 11: "Lighting, heating, cooking appliances",
    12: "Vehicles, automobile parts", 13: "Firearms, ammunition",
    14: "Jewelry, watches", 15: "Musical instruments",
    16: "Paper, stationery, books, printing", 17: "Rubber, plastics",
    18: "Leather goods, bags, wallets, luggage", 19: "Building materials",
    20: "Furniture, picture frames", 21: "Kitchen/household utensils, glassware",
    22: "Ropes, bags, tents", 23: "Threads, yarns", 24: "Textiles, bed/table linen",
    25: "Clothing, footwear, headgear, apparel, fashion",
    26: "Lace, embroidery, buttons, ribbons", 27: "Carpets, mats, floor coverings",
    28: "Games, toys, sports equipment", 29: "Meat, fish, dairy, edible oils",
    30: "Coffee, tea, sugar, flour, baked goods, chocolates, snacks",
    31: "Fresh fruits, vegetables, live animals, grains",
    32: "Beer, mineral water, fruit juices, soft drinks",
    33: "Alcoholic beverages (except beer)", 34: "Tobacco, cigarettes",
    35: "Advertising, business management, retail, e-commerce, HR services",
    36: "Insurance, financial services, banking, real estate",
    37: "Construction, repair, installation services",
    38: "Telecommunications, internet services",
    39: "Transport, travel, shipping, logistics",
    40: "Treatment of materials, printing, food processing",
    41: "Education, entertainment, sports, cultural activities",
    42: "IT services, software development, research, SaaS, AI/ML",
    43: "Restaurant, hotel, catering, food/beverage services",
    44: "Medical, veterinary, beauty, spa services",
    45: "Legal services, security services, personal social services"
}

TM_FEES = {
    "individuals_startups_sme": {"online": 4500, "physical": 5000, "note": "50% discount for individuals, startups (DPIIT registered), small enterprises"},
    "others": {"online": 9000, "physical": 10000, "note": "Standard fee for companies, firms, etc."},
}

SYSTEM_PROMPT = """You are an expert Indian IP lawyer specializing in trademarks, copyrights, and patents.
Cite the Trade Marks Act 1999, Copyright Act 1957, Patents Act 1970, and IP India rules.
Be practical: include the exact IP India portal steps, fees, and realistic timelines.
DISCLAIMER: IP matters benefit greatly from professional advice. Consult a trademark attorney."""

def find_class(business_description):
    matches = []
    desc_lower = business_description.lower()
    keywords = {
        9: ["app", "software", "saas", "tech", "mobile", "digital", "electronics", "computer"],
        35: ["ecommerce", "retail", "advertising", "marketplace", "b2b", "business services"],
        36: ["fintech", "finance", "insurance", "lending", "banking", "payments", "upi"],
        41: ["edtech", "education", "e-learning", "coaching", "training", "online learning"],
        42: ["it services", "ai", "machine learning", "cloud", "cybersecurity", "development"],
        43: ["restaurant", "food delivery", "cafe", "catering", "hotel", "hospitality"],
        44: ["health", "telemedicine", "wellness", "beauty", "clinic", "medical services"],
        25: ["clothing", "fashion", "apparel", "wear", "shoes", "footwear", "garment"],
        30: ["food", "snack", "bakery", "confectionery", "sweets", "biscuit"],
    }
    for cls, kws in keywords.items():
        if any(kw in desc_lower for kw in kws):
            matches.append({"class": cls, "description": TM_CLASSES[cls], "confidence": "high"})
    if not matches:
        # LLM fallback
        prompt = f"Which Nice Classification trademark classes (1-45) apply to this Indian business: {business_description}? List up to 3 classes with explanation."
        resp = llm_chat([{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}])
        return {"ok": True, "analysis": resp, "all_classes": TM_CLASSES, "agent": "trademark_ip"}
    return {"ok": True, "recommended_classes": matches, "fees": TM_FEES, "agent": "trademark_ip"}

def filing_guide(mark_name, business_type, classes, applicant_type="startup"):
    prompt = f"""Trademark filing guide for India (IP India portal — ipindia.gov.in):
Mark: "{mark_name}"
Business: {business_type}
Classes: {classes}
Applicant: {applicant_type}

Provide:
1. Trademark search before filing (how to do it on IP India — critical first step)
2. Types of marks (word mark vs device mark vs composite) — which to choose
3. Step-by-step online filing on ipindia.gov.in
4. Fee: {TM_FEES.get(applicant_type if applicant_type in TM_FEES else 'others')}
5. Timeline: application → examination → opposition period → registration
6. Common rejection reasons and how to avoid
7. ™ vs ® — when you can use each
8. What happens if someone else uses a similar mark
9. Should you file a device mark too? Pros/cons
10. International protection — Madrid Protocol for Indian businesses going global"""
    resp = llm_chat([{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}])
    return {
        "ok": True, "mark": mark_name, "guide": resp,
        "portal": "https://ipindiaonline.gov.in/tmrpublicsearch/frmmain.aspx (trademark search)",
        "filing_portal": "https://ipindiaonline.gov.in",
        "fees": TM_FEES, "agent": "trademark_ip"
    }

def copyright_guide(work_type, creator_name):
    prompt = f"""Copyright guidance for India:
Work type: {work_type}
Creator: {creator_name}

Explain:
1. Does copyright arise automatically? (Yes — from creation, no registration needed)
2. Should they register? Why? (Recommended for court evidence)
3. How to register at copyright.gov.in — steps and fee
4. Duration of copyright in India
5. What is protected (and what is not — ideas, facts, titles)
6. Fair use / fair dealing exceptions in India
7. Copyright infringement — remedies (civil + criminal under Copyright Act)
8. Work-for-hire rules if created by employees/contractors
9. Assignment vs licensing of copyright"""
    resp = llm_chat([{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}])
    return {"ok": True, "work_type": work_type, "guide": resp, "portal": "https://copyright.gov.in", "agent": "trademark_ip"}

if __name__ == "__main__":
    import json
    print(json.dumps(find_class("food delivery app and restaurant aggregator"), indent=2))
