"""
Labour Rights Checker — minimum wage, PF/ESI, gratuity, leave entitlements.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from llm_client import chat as llm_chat

# PF/ESI thresholds (2024-25)
PF_WAGE_THRESHOLD = 15000  # mandatory if basic+DA ≤ 15,000
ESI_WAGE_THRESHOLD = 21000  # ESI mandatory if gross ≤ 21,000

def check_pf_esi(monthly_basic, monthly_gross, employee_count, state=""):
    result = {}
    # PF
    pf_applicable = employee_count >= 20  # EPF Act applies to establishments with 20+ employees
    if pf_applicable:
        pf_employee = round(min(monthly_basic, 15000) * 0.12, 2)
        pf_employer = round(min(monthly_basic, 15000) * 0.12, 2)  # 3.67% EPF + 8.33% EPS
        result["pf"] = {
            "applicable": True,
            "employee_contribution": pf_employee,
            "employer_contribution": pf_employer,
            "monthly_total": pf_employee + pf_employer,
            "note": "Capped at 12% of ₹15,000 wage ceiling. Employer's 8.33% goes to EPS (pension)."
        }
    else:
        result["pf"] = {"applicable": False, "note": f"EPF mandatory only for establishments with 20+ employees (yours: {employee_count})"}

    # ESI
    esi_applicable = monthly_gross <= ESI_WAGE_THRESHOLD and employee_count >= 10
    if esi_applicable:
        esi_employee = round(monthly_gross * 0.0075, 2)
        esi_employer = round(monthly_gross * 0.0325, 2)
        result["esi"] = {
            "applicable": True,
            "employee_contribution": esi_employee,
            "employer_contribution": esi_employer,
            "note": "Employee: 0.75% of gross. Employer: 3.25%. Covers medical insurance for employee + family."
        }
    else:
        result["esi"] = {"applicable": esi_applicable, "note": f"ESI: gross ₹{monthly_gross:,} {'exceeds' if monthly_gross > ESI_WAGE_THRESHOLD else 'below'} ₹21,000 threshold or <10 employees"}

    return result

def gratuity_calc(years_of_service, last_basic_da, months_per_year=26):
    """Gratuity under Payment of Gratuity Act 1972. Eligible after 5 years."""
    if years_of_service < 5:
        return {"eligible": False, "years": years_of_service, "note": "Gratuity requires minimum 5 years of continuous service (240 days/year)."}

    # Formula: (Last Basic+DA × 15 × years) / 26
    gratuity = (last_basic_da * 15 * years_of_service) / months_per_year
    max_exempt = 2000000  # ₹20 lakh tax-exempt
    taxable = max(0, gratuity - max_exempt)

    return {
        "eligible": True,
        "years_of_service": years_of_service,
        "last_basic_da": last_basic_da,
        "gratuity_amount": round(gratuity, 2),
        "tax_exempt_limit": max_exempt,
        "taxable_portion": round(taxable, 2),
        "formula": f"(₹{last_basic_da} × 15 × {years_of_service} years) ÷ 26",
        "note": "Payable within 30 days of separation. Employer cannot withhold beyond 30 days without interest @ 10% pa."
    }

def rights_check(state, sector, monthly_salary, years_service, employee_count):
    prompt = f"""Check Indian labour rights for:
State: {state}
Sector: {sector}
Monthly salary: ₹{monthly_salary:,}
Years of service: {years_service}
Company size: {employee_count} employees

Provide:
1. Applicable minimum wage for this state/sector (cite state notification)
2. Notice period entitlement under Industrial Disputes Act / Shops & Establishments Act
3. Leave entitlement (casual, sick, earned/privilege leave)
4. Any sector-specific protections
5. Is this salary above minimum wage? If not, employer is violating law.
6. Key rights this worker has if terminated

Be specific to {state} state laws."""
    resp = llm_chat([{"role":"user","content":prompt}], system="You are an expert Indian labour law attorney. Cite specific acts and state notifications.")

    pf_esi = check_pf_esi(monthly_salary * 0.5, monthly_salary, employee_count, state)
    grat = gratuity_calc(years_service, monthly_salary * 0.5)

    return {
        "ok": True,
        "analysis": resp,
        "pf_esi": pf_esi,
        "gratuity": grat,
        "agent": "labour_rights"
    }

if __name__ == "__main__":
    import json
    r = rights_check("Maharashtra", "IT/Software", 35000, 4, 50)
    print(json.dumps(r["pf_esi"], indent=2))
    print(json.dumps(r["gratuity"], indent=2))
