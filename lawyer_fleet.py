#!/usr/bin/env python3
"""
Lawyer Fleet Server — port 8766
Routes to all specialized agents with credit enforcement.
"""
import sys, os, json, urllib.parse, hashlib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
import lawyer_credits as credits

# Import agents lazily (at request time) so the server starts even if one agent fails
AGENTS = {
    "legal_notice": ("lawyer_agents.legal_notice", "draft_notice"),
    "rti": ("lawyer_agents.rti_agent", "draft_rti"),
    "deadline": ("lawyer_agents.deadline_agent", "calculate"),
    "document": ("lawyer_agents.document_drafter", "draft"),
    "court": ("lawyer_agents.court_locator", "locate"),
    "chat": ("lawyer", "chat"),  # existing lawyer.py
}

def _import_fn(module_name, fn_name):
    import importlib
    m = importlib.import_module(module_name)
    return getattr(m, fn_name)

def _user_id(handler):
    """Extract user_id from cookie or generate one."""
    cookies = handler.headers.get("Cookie", "")
    for part in cookies.split(";"):
        part = part.strip()
        if part.startswith("lid="):
            return part[4:]
    # Generate from IP
    ip = handler.client_address[0]
    return hashlib.md5(ip.encode()).hexdigest()[:16]

FLEET_UI = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Legal Services Fleet</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { background: #0a0a0a; color: #e0e0e0; font-family: system-ui, sans-serif; min-height: 100vh; }
.header { background: #1a1a2e; padding: 20px; text-align: center; border-bottom: 1px solid #333; }
.header h1 { color: #f0a500; font-size: 1.6rem; }
.header p { color: #888; font-size: 0.85rem; margin-top: 4px; }
.quota-bar { background: #111; border: 1px solid #333; border-radius: 8px; padding: 12px 16px; margin: 12px; font-size: 0.85rem; color: #aaa; }
.quota-bar span { color: #f0a500; font-weight: bold; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 16px; padding: 16px; }
.card { background: #1a1a1a; border: 1px solid #333; border-radius: 12px; padding: 20px; cursor: pointer; transition: border-color 0.2s; }
.card:hover { border-color: #f0a500; }
.card h2 { color: #f0a500; font-size: 1rem; margin-bottom: 8px; }
.card p { color: #999; font-size: 0.82rem; line-height: 1.5; }
.badge { display: inline-block; background: #1a3a1a; color: #4caf50; border-radius: 4px; padding: 2px 8px; font-size: 0.7rem; margin-top: 8px; }
.modal { display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.8); z-index: 100; align-items: center; justify-content: center; }
.modal.open { display: flex; }
.modal-box { background: #1a1a1a; border: 1px solid #444; border-radius: 16px; padding: 24px; width: 90%; max-width: 600px; max-height: 80vh; overflow-y: auto; }
.modal-box h2 { color: #f0a500; margin-bottom: 16px; }
label { display: block; color: #aaa; font-size: 0.82rem; margin-bottom: 4px; margin-top: 12px; }
input, textarea, select { width: 100%; background: #111; border: 1px solid #444; color: #e0e0e0; border-radius: 8px; padding: 10px; font-size: 0.9rem; }
textarea { height: 100px; resize: vertical; }
.btn { background: #f0a500; color: #000; border: none; border-radius: 8px; padding: 12px 24px; font-size: 0.95rem; font-weight: bold; cursor: pointer; margin-top: 16px; width: 100%; }
.btn:hover { background: #ffb730; }
.result { background: #0d1f0d; border: 1px solid #2d4a2d; border-radius: 8px; padding: 16px; margin-top: 16px; font-size: 0.82rem; white-space: pre-wrap; line-height: 1.6; color: #c8e6c9; max-height: 400px; overflow-y: auto; }
.close-btn { background: #333; color: #fff; border: none; border-radius: 8px; padding: 8px 20px; cursor: pointer; margin-top: 12px; }
.link { color: #f0a500; cursor: pointer; text-decoration: underline; font-size: 0.82rem; }
</style>
</head>
<body>
<div class="header">
  <h1>Indian Legal Services</h1>
  <p>AI-powered legal assistance for all 22 Indian languages</p>
  <p style="margin-top:8px;font-size:0.78rem;">
    <a class="link" href="/admin">Admin Panel</a> &nbsp;|&nbsp;
    <a class="link" href="/upgrade">Get Pro</a> &nbsp;|&nbsp;
    <span style="color:#4caf50;">WhatsApp: msg <em>hi</em> to bot</span>
  </p>
</div>
<div class="quota-bar" id="quota">Loading usage...</div>
<div class="grid">
  <div class="card" onclick="openModal('chat')">
    <h2>Legal Q&amp;A</h2>
    <p>Ask any legal question in Hindi, Tamil, Telugu, Kannada, Bengali or any Indian language. Grounded in Indian law.</p>
    <span class="badge">FREE · 10/day</span>
  </div>
  <div class="card" onclick="openModal('notice')">
    <h2>Draft Legal Notice</h2>
    <p>Cheque bounce (S.138), demand notice, consumer complaint, landlord/tenant, RERA builder notice.</p>
    <span class="badge">FREE · 10/day</span>
  </div>
  <div class="card" onclick="openModal('rti')">
    <h2>RTI Application</h2>
    <p>Generate a complete RTI application under RTI Act 2005 with correct PIO addressing and fee note.</p>
    <span class="badge">FREE · 10/day</span>
  </div>
  <div class="card" onclick="openModal('deadline')">
    <h2>Limitation Calculator</h2>
    <p>Check filing deadlines: consumer forum 2yr, cheque bounce 30d, money recovery 3yr, property 12yr.</p>
    <span class="badge">FREE · 10/day</span>
  </div>
  <div class="card" onclick="openModal('document')">
    <h2>Draft Documents</h2>
    <p>Rent agreement (11-month), affidavit, NDA, Will, Power of Attorney, Vakalatnama.</p>
    <span class="badge">FREE · 10/day</span>
  </div>
  <div class="card" onclick="openModal('court')">
    <h2>Court Locator</h2>
    <p>Which court/forum to file in? Consumer Forum, Labour Court, Family Court, RERA, Banking Ombudsman.</p>
    <span class="badge">FREE · 10/day</span>
  </div>
  <div class="card" onclick="openModal('contract')">
    <h2>📋 Contract Review</h2>
    <p>Paste any contract → AI flags risky clauses, unfair terms, missing protections. Employment, NDA, rent, vendor agreements.</p>
    <span class="badge">FREE · 10/day</span>
  </div>
  <div class="card" onclick="openModal('tax')">
    <h2>🧾 Tax Notice Decoder</h2>
    <p>Paste your Income Tax notice (143(1), 148, 148A, 139(9), 156) → instant explanation + exact action steps + deadline.</p>
    <span class="badge">FREE · 10/day</span>
  </div>
  <div class="card" onclick="openModal('gains')">
    <h2>📈 Capital Gains Calculator</h2>
    <p>LTCG/STCG for equity, property, gold, debt MF, crypto. ₹1.25L exemption, Section 115BBH, indexation rules.</p>
    <span class="badge">FREE · instant</span>
  </div>
  <div class="card" onclick="openModal('labour')">
    <h2>👷 Labour Rights</h2>
    <p>Check minimum wage, PF/ESI applicability, gratuity calculator, notice period, leave entitlement for your state.</p>
    <span class="badge">FREE · 10/day</span>
  </div>
  <div class="card" onclick="openModal('fir')">
    <h2>🚔 FIR &amp; Criminal Guide</h2>
    <p>How to file FIR. What if police refuse? Rights of accused under BNSS 2023. BNS sections for your offence.</p>
    <span class="badge">FREE · 10/day</span>
  </div>
  <div class="card" onclick="openModal('startup')">
    <h2>🚀 Startup Legal</h2>
    <p>Pvt Ltd vs LLP vs OPC comparison, ROC compliance calendar, GST registration, DPIIT Startup India, ESOP basics.</p>
    <span class="badge">FREE · 10/day</span>
  </div>
  <div class="card" onclick="openModal('posh')">
    <h2>🛡️ POSH / Workplace Safety</h2>
    <p>Sexual harassment at workplace (POSH Act 2013). ICC complaint process, employer obligations, ICC vs Local Committee.</p>
    <span class="badge">FREE · 10/day</span>
  </div>
  <div class="card" onclick="openModal('succession')">
    <h2>🏺 Succession &amp; Inheritance</h2>
    <p>Who inherits what under Hindu/Muslim/Christian law? Succession certificate process, daughter's equal rights, no inheritance tax.</p>
    <span class="badge">FREE · 10/day</span>
  </div>
  <div class="card" onclick="openModal('banking')">
    <h2>🏦 Banking &amp; UPI Fraud</h2>
    <p>Step-by-step recovery: 1930 helpline, RBI zero-liability rule, bank escalation, RBI Ombudsman, FIR. Time-critical.</p>
    <span class="badge">URGENT · FREE</span>
  </div>
  <div class="card" onclick="openModal('property')">
    <h2>🏠 Property Law</h2>
    <p>Due diligence checklist, stamp duty calculator (all states), encumbrance certificate, RERA verification guide.</p>
    <span class="badge">FREE · 10/day</span>
  </div>
  <div class="card" onclick="openModal('divorce')">
    <h2>💔 Divorce &amp; Family Law</h2>
    <p>Mutual vs contested divorce, maintenance rights, child custody, PWDVA 2005 protection orders. All religions.</p>
    <span class="badge">FREE · 10/day</span>
  </div>
  <div class="card" onclick="openModal('police_complaint')">
    <h2>🚔 Police Complaint / FIR</h2>
    <p>Draft FIR/police complaint for 18+ crime types under BNS 2023. Cheating, fraud, assault, domestic violence, cybercrime, theft, stalking, missing person.</p>
    <span class="badge urgent">URGENT available · FREE</span>
  </div>
  <div class="card" onclick="openModal('magistrate_petition')">
    <h2>⚖️ Magistrate Petition (FIR Refused)</h2>
    <p>Draft Section 175(3) BNSS 2023 petition when police refuse to register FIR. Magistrate can ORDER police to register FIR.</p>
    <span class="badge">FREE · police override</span>
  </div>
  <div class="card" onclick="openModal('bail')">
    <h2>🔓 Bail Guide + Application</h2>
    <p>Regular bail / anticipatory bail (before arrest) / default bail (60-day rule). Draft bail application for court. "Bail is the rule, jail is the exception."</p>
    <span class="badge urgent">URGENT · FREE</span>
  </div>
  <div class="card" onclick="openModal('notice_expanded')">
    <h2>📜 Legal Notice (25+ Types)</h2>
    <p>Expanded notice drafting: cheque bounce (S.138), defamation, medical negligence, trademark, copyright, data breach, cyber fraud, motor accident, RERA, and 20+ more.</p>
    <span class="badge">FREE · 10/day</span>
  </div>
</div>

<!-- Modals -->
<div class="modal" id="modal-chat">
  <div class="modal-box">
    <h2>Legal Q&amp;A</h2>
    <label>Your question (any language)</label>
    <textarea id="chat-q" placeholder="What is bail? / मुझे जमानत कैसे मिलेगी?"></textarea>
    <button class="btn" onclick="ask('chat')">Ask</button>
    <div class="result" id="result-chat" style="display:none"></div>
    <button class="close-btn" onclick="closeModal('chat')">Close</button>
  </div>
</div>

<div class="modal" id="modal-notice">
  <div class="modal-box">
    <h2>Draft Legal Notice</h2>
    <label>Notice type</label>
    <select id="notice-type">
      <option value="demand">Demand notice (money recovery)</option>
      <option value="cheque_bounce">Cheque bounce (Section 138 NI Act)</option>
      <option value="consumer">Consumer complaint notice</option>
      <option value="landlord">Landlord eviction / rent notice</option>
      <option value="tenant">Tenant rights notice</option>
      <option value="rera">RERA builder notice</option>
    </select>
    <label>Your name &amp; address</label>
    <input id="notice-sender" placeholder="Ramesh Kumar, 123 MG Road, Mumbai 400001">
    <label>Recipient name &amp; address</label>
    <input id="notice-recipient" placeholder="ABC Company, 456 Park Street, Delhi 110001">
    <label>Facts &amp; background</label>
    <textarea id="notice-facts" placeholder="Describe what happened..."></textarea>
    <button class="btn" onclick="ask('notice')">Draft Notice</button>
    <div class="result" id="result-notice" style="display:none"></div>
    <button class="close-btn" onclick="closeModal('notice')">Close</button>
  </div>
</div>

<div class="modal" id="modal-rti">
  <div class="modal-box">
    <h2>RTI Application</h2>
    <label>What information do you need?</label>
    <textarea id="rti-info" placeholder="e.g. Status of my PF withdrawal application submitted on 1 Jan 2026"></textarea>
    <label>Subject (which government office?)</label>
    <input id="rti-subject" placeholder="EPFO / Income Tax Department / Municipal Corporation...">
    <label>Your name &amp; address</label>
    <input id="rti-applicant" placeholder="Your full name, address">
    <button class="btn" onclick="ask('rti')">Generate RTI</button>
    <div class="result" id="result-rti" style="display:none"></div>
    <button class="close-btn" onclick="closeModal('rti')">Close</button>
  </div>
</div>

<div class="modal" id="modal-deadline">
  <div class="modal-box">
    <h2>Limitation Calculator</h2>
    <label>Type of dispute</label>
    <select id="dl-type">
      <option value="consumer_complaint">Consumer complaint (2 years)</option>
      <option value="cheque_bounce_138">Cheque bounce criminal (30 days after notice)</option>
      <option value="money_recovery">Money recovery / loan (3 years)</option>
      <option value="property_recovery">Property recovery (12 years)</option>
      <option value="contract_breach">Contract breach (3 years)</option>
      <option value="motor_accident">Motor accident claim (3 years)</option>
      <option value="rti_first_appeal">RTI first appeal (30 days)</option>
      <option value="labour_industrial">Labour / dismissal dispute (3 years)</option>
    </select>
    <label>Date of incident / cause of action (YYYY-MM-DD)</label>
    <input id="dl-date" type="date">
    <button class="btn" onclick="ask('deadline')">Calculate Deadline</button>
    <div class="result" id="result-deadline" style="display:none"></div>
    <button class="close-btn" onclick="closeModal('deadline')">Close</button>
  </div>
</div>

<div class="modal" id="modal-document">
  <div class="modal-box">
    <h2>Draft Legal Document</h2>
    <label>Document type</label>
    <select id="doc-type">
      <option value="rent_agreement">Rent / Lease Agreement (11-month)</option>
      <option value="affidavit">Affidavit</option>
      <option value="nda">Non-Disclosure Agreement (NDA)</option>
      <option value="will">Will / Testament</option>
      <option value="power_of_attorney">Power of Attorney</option>
      <option value="vakalatnama">Vakalatnama</option>
    </select>
    <label>Describe what you need (names, amounts, details)</label>
    <textarea id="doc-details" placeholder="e.g. Landlord: Ramesh Kumar, Tenant: Suresh Sharma, Flat 4B Andheri West, Rent: Rs 25,000, Deposit: Rs 75,000, from July 2026, Maharashtra"></textarea>
    <button class="btn" onclick="ask('document')">Draft Document</button>
    <div class="result" id="result-document" style="display:none"></div>
    <button class="close-btn" onclick="closeModal('document')">Close</button>
  </div>
</div>

<div class="modal" id="modal-court">
  <div class="modal-box">
    <h2>Court Locator</h2>
    <label>Describe your dispute</label>
    <textarea id="court-desc" placeholder="My builder has not given possession of my flat for 3 years despite full payment of Rs 80 lakh"></textarea>
    <label>Claim amount (optional)</label>
    <input id="court-amount" placeholder="e.g. Rs 50,000 or Rs 25 lakh">
    <label>State</label>
    <input id="court-state" placeholder="e.g. Maharashtra, Delhi, Karnataka">
    <button class="btn" onclick="ask('court')">Find Court</button>
    <div class="result" id="result-court" style="display:none"></div>
    <button class="close-btn" onclick="closeModal('court')">Close</button>
  </div>
</div>

<div class="modal" id="modal-contract">
  <div class="modal-box">
    <h2>📋 Contract Review</h2>
    <label>Your role</label>
    <select id="contract-role">
      <option value="party signing">Party signing the contract</option>
      <option value="employer">Employer</option>
      <option value="employee">Employee</option>
      <option value="landlord">Landlord</option>
      <option value="tenant">Tenant</option>
      <option value="vendor">Vendor / Service provider</option>
      <option value="client">Client / Buyer</option>
    </select>
    <label>Paste contract text</label>
    <textarea id="contract-text" style="height:160px" placeholder="Paste the full contract or the clauses you want reviewed..."></textarea>
    <button class="btn" onclick="ask('contract')">Review Contract</button>
    <div class="result" id="result-contract" style="display:none"></div>
    <button class="close-btn" onclick="closeModal('contract')">Close</button>
  </div>
</div>

<div class="modal" id="modal-tax">
  <div class="modal-box">
    <h2>🧾 Tax Notice Decoder</h2>
    <label>Paste your Income Tax notice text</label>
    <textarea id="tax-notice" style="height:140px" placeholder="Paste the notice text or section numbers (e.g. u/s 143(1), 148, 148A, 139(9), 156)..."></textarea>
    <label>Relevant facts (optional)</label>
    <textarea id="tax-facts" placeholder="e.g. Filed ITR on 31 Jul 2025, total income Rs 8.5L, salary + FD interest..."></textarea>
    <button class="btn" onclick="ask('tax')">Decode Notice</button>
    <div class="result" id="result-tax" style="display:none"></div>
    <button class="close-btn" onclick="closeModal('tax')">Close</button>
  </div>
</div>

<div class="modal" id="modal-gains">
  <div class="modal-box">
    <h2>📈 Capital Gains Calculator</h2>
    <label>Asset type</label>
    <select id="gains-asset">
      <option value="equity">Equity shares / Equity MF</option>
      <option value="property">Property / Land</option>
      <option value="gold">Gold / Jewellery</option>
      <option value="debt_mf">Debt MF</option>
      <option value="crypto">Crypto / VDA</option>
    </select>
    <label>Buy price per unit (₹)</label>
    <input id="gains-buy" type="number" placeholder="e.g. 100">
    <label>Sell price per unit (₹)</label>
    <input id="gains-sell" type="number" placeholder="e.g. 250">
    <label>Buy date (YYYY-MM-DD)</label>
    <input id="gains-buydate" type="date">
    <label>Sell date (YYYY-MM-DD)</label>
    <input id="gains-selldate" type="date">
    <label>Number of units</label>
    <input id="gains-units" type="number" placeholder="e.g. 100" value="1">
    <button class="btn" onclick="ask('gains')">Calculate Tax</button>
    <div class="result" id="result-gains" style="display:none"></div>
    <button class="close-btn" onclick="closeModal('gains')">Close</button>
  </div>
</div>

<div class="modal" id="modal-labour">
  <div class="modal-box">
    <h2>👷 Labour Rights</h2>
    <label>State</label>
    <input id="labour-state" placeholder="e.g. Maharashtra, Delhi, Karnataka">
    <label>Sector / Industry</label>
    <input id="labour-sector" placeholder="e.g. IT, Manufacturing, Retail, Construction">
    <label>Monthly salary (₹)</label>
    <input id="labour-salary" type="number" placeholder="e.g. 25000">
    <label>Years of service</label>
    <input id="labour-years" type="number" placeholder="e.g. 5.5">
    <label>No. of employees in company</label>
    <input id="labour-employees" type="number" placeholder="e.g. 100">
    <button class="btn" onclick="ask('labour')">Check Rights</button>
    <div class="result" id="result-labour" style="display:none"></div>
    <button class="close-btn" onclick="closeModal('labour')">Close</button>
  </div>
</div>

<div class="modal" id="modal-fir">
  <div class="modal-box">
    <h2>🚔 FIR &amp; Criminal Guide</h2>
    <label>Describe your situation</label>
    <textarea id="fir-situation" placeholder="e.g. I was cheated by an online seller who took Rs 50,000 and didn't deliver goods..."></textarea>
    <label>State (for local police procedure)</label>
    <input id="fir-state" placeholder="e.g. Maharashtra, Delhi">
    <label>Have you already tried the police station?</label>
    <select id="fir-tried">
      <option value="false">No — first time</option>
      <option value="true">Yes — police refused / no action</option>
    </select>
    <button class="btn" onclick="ask('fir')">Get Guide</button>
    <div class="result" id="result-fir" style="display:none"></div>
    <button class="close-btn" onclick="closeModal('fir')">Close</button>
  </div>
</div>

<div class="modal" id="modal-startup">
  <div class="modal-box">
    <h2>🚀 Startup Legal</h2>
    <label>Stage</label>
    <select id="startup-stage">
      <option value="idea">Idea stage (pre-incorporation)</option>
      <option value="incorporated">Newly incorporated</option>
      <option value="seed">Seed / Angel funded</option>
      <option value="series_a">Series A+</option>
    </select>
    <label>Entity type</label>
    <select id="startup-entity">
      <option value="pvt_ltd">Private Limited Company</option>
      <option value="llp">LLP</option>
      <option value="opc">OPC (One Person Company)</option>
      <option value="partnership">Partnership</option>
      <option value="proprietorship">Sole Proprietorship</option>
    </select>
    <label>State of incorporation</label>
    <input id="startup-state" placeholder="e.g. Karnataka, Maharashtra">
    <label>Sector</label>
    <input id="startup-sector" placeholder="e.g. SaaS, FinTech, EdTech, E-commerce">
    <button class="btn" onclick="ask('startup')">Get Checklist</button>
    <div class="result" id="result-startup" style="display:none"></div>
    <button class="close-btn" onclick="closeModal('startup')">Close</button>
  </div>
</div>

<div class="modal" id="modal-posh">
  <div class="modal-box">
    <h2>🛡️ POSH / Workplace Safety</h2>
    <label>Describe the situation</label>
    <textarea id="posh-situation" placeholder="Describe what happened at the workplace..."></textarea>
    <label>Your role</label>
    <select id="posh-role">
      <option value="complainant">Complainant (victim)</option>
      <option value="employer">Employer / HR</option>
      <option value="icc_member">ICC member</option>
      <option value="witness">Witness</option>
    </select>
    <label>Does your organisation have an ICC?</label>
    <select id="posh-icc">
      <option value="true">Yes</option>
      <option value="false">No / Not sure</option>
    </select>
    <button class="btn" onclick="ask('posh')">Get Guide</button>
    <div class="result" id="result-posh" style="display:none"></div>
    <button class="close-btn" onclick="closeModal('posh')">Close</button>
  </div>
</div>

<div class="modal" id="modal-succession">
  <div class="modal-box">
    <h2>🏺 Succession &amp; Inheritance</h2>
    <label>Religion of deceased</label>
    <select id="succession-religion">
      <option value="Hindu">Hindu (includes Sikh, Jain, Buddhist)</option>
      <option value="Muslim">Muslim</option>
      <option value="Christian">Christian</option>
      <option value="Parsi">Parsi</option>
    </select>
    <label>Surviving relatives (list all)</label>
    <textarea id="succession-relatives" placeholder="e.g. spouse, 2 sons, 1 daughter, mother (father deceased), 1 brother"></textarea>
    <label>Assets (brief list)</label>
    <textarea id="succession-assets" placeholder="e.g. flat in Delhi, bank FDs Rs 20L, shares, gold jewellery"></textarea>
    <button class="btn" onclick="ask('succession')">Who Inherits?</button>
    <div class="result" id="result-succession" style="display:none"></div>
    <button class="close-btn" onclick="closeModal('succession')">Close</button>
  </div>
</div>

<div class="modal" id="modal-banking">
  <div class="modal-box">
    <h2>🏦 Banking &amp; UPI Fraud</h2>
    <label>Type of fraud</label>
    <select id="banking-type">
      <option value="upi_fraud">UPI fraud</option>
      <option value="card_fraud">Debit/Credit card fraud</option>
      <option value="netbanking_fraud">Net banking / OTP fraud</option>
      <option value="cheque_fraud">Cheque fraud</option>
      <option value="loan_fraud">Fake loan / KYC fraud</option>
    </select>
    <label>Amount lost (₹)</label>
    <input id="banking-amount" type="number" placeholder="e.g. 50000">
    <label>Days since fraud occurred</label>
    <input id="banking-days" type="number" placeholder="e.g. 2">
    <label>Your bank</label>
    <input id="banking-bank" placeholder="e.g. SBI, HDFC, ICICI">
    <label>Additional details</label>
    <textarea id="banking-details" placeholder="What happened? How was money taken?"></textarea>
    <button class="btn" onclick="ask('banking')">Get Recovery Steps</button>
    <div class="result" id="result-banking" style="display:none"></div>
    <button class="close-btn" onclick="closeModal('banking')">Close</button>
  </div>
</div>

<div class="modal" id="modal-property">
  <div class="modal-box">
    <h2>🏠 Property Law</h2>
    <label>Property type</label>
    <select id="property-type">
      <option value="flat">Flat / Apartment</option>
      <option value="plot">Plot / Land</option>
      <option value="villa">Villa / Independent house</option>
      <option value="commercial">Commercial property</option>
    </select>
    <label>State</label>
    <input id="property-state" placeholder="e.g. Maharashtra, Karnataka, Delhi">
    <label>Property value (₹)</label>
    <input id="property-value" type="number" placeholder="e.g. 8500000">
    <label>Transaction type</label>
    <select id="property-resale">
      <option value="true">Resale</option>
      <option value="false">New / Under construction</option>
    </select>
    <button class="btn" onclick="ask('property')">Run Due Diligence</button>
    <div class="result" id="result-property" style="display:none"></div>
    <button class="close-btn" onclick="closeModal('property')">Close</button>
  </div>
</div>

<div class="modal" id="modal-divorce">
  <div class="modal-box">
    <h2>💔 Divorce &amp; Family Law</h2>
    <label>Describe your situation</label>
    <textarea id="divorce-situation" placeholder="Briefly describe what's happening (separated for X months, grounds, assets, children...)"></textarea>
    <label>Religion</label>
    <select id="divorce-religion">
      <option value="Hindu">Hindu</option>
      <option value="Muslim">Muslim</option>
      <option value="Christian">Christian</option>
      <option value="Parsi">Parsi</option>
      <option value="Special Marriage Act">Special Marriage Act (inter-faith)</option>
    </select>
    <label>Type of divorce</label>
    <select id="divorce-mutual">
      <option value="true">Mutual consent</option>
      <option value="false">Contested</option>
      <option value="null">Not sure yet</option>
    </select>
    <label>Children involved?</label>
    <select id="divorce-children">
      <option value="false">No</option>
      <option value="true">Yes</option>
    </select>
    <label>Domestic violence involved?</label>
    <select id="divorce-dv">
      <option value="false">No</option>
      <option value="true">Yes</option>
    </select>
    <button class="btn" onclick="ask('divorce')">Get Guide</button>
    <div class="result" id="result-divorce" style="display:none"></div>
    <button class="close-btn" onclick="closeModal('divorce')">Close</button>
  </div>
</div>

<div class="modal" id="modal-police_complaint">
  <div class="modal-box">
    <h2>🚔 Police Complaint / FIR</h2>
    <label>Crime Type</label>
    <select id="pc-crime_type">
      <option value="online_fraud">Online Fraud</option>
      <option value="cheating">Cheating</option>
      <option value="assault">Assault / Battery</option>
      <option value="domestic_violence">Domestic Violence</option>
      <option value="cybercrime">Cybercrime</option>
      <option value="theft">Theft</option>
      <option value="stalking">Stalking / Harassment</option>
      <option value="missing_person">Missing Person</option>
      <option value="kidnapping">Kidnapping</option>
      <option value="murder">Murder / Attempt to Murder</option>
      <option value="rape">Sexual Assault / Rape</option>
      <option value="extortion">Extortion / Blackmail</option>
      <option value="property_dispute">Property Dispute / Trespass</option>
      <option value="forgery">Forgery / Document Fraud</option>
      <option value="defamation">Defamation</option>
      <option value="drug_offence">Drug Offence</option>
      <option value="eve_teasing">Eve Teasing / Molestation</option>
      <option value="road_accident">Road Accident / Hit and Run</option>
    </select>
    <label>Your Name</label>
    <input id="pc-complainant_name" placeholder="Full name as per ID">
    <label>Your Address</label>
    <input id="pc-complainant_address" placeholder="Full address with pin code">
    <label>Accused Name (if known)</label>
    <input id="pc-accused_name" placeholder="Leave blank if unknown">
    <label>Accused Address (if known)</label>
    <input id="pc-accused_address" placeholder="Leave blank if unknown">
    <label>Incident Date</label>
    <input id="pc-incident_date" placeholder="e.g. 15 June 2026">
    <label>Incident Location</label>
    <input id="pc-incident_location" placeholder="Where did it happen?">
    <label>Facts / What happened</label>
    <textarea id="pc-facts" placeholder="Describe what happened in detail..."></textarea>
    <label>Property / Amount Lost (if any)</label>
    <input id="pc-property_lost" placeholder="e.g. Rs 50,000 transferred via UPI">
    <label>State</label>
    <input id="pc-state" placeholder="e.g. Maharashtra">
    <button class="btn" onclick="ask('police_complaint')">Draft Complaint</button>
    <div class="result" id="result-police_complaint" style="display:none"></div>
    <button class="close-btn" onclick="closeModal('police_complaint')">Close</button>
  </div>
</div>

<div class="modal" id="modal-magistrate_petition">
  <div class="modal-box">
    <h2>⚖️ Magistrate Petition (S.175 BNSS)</h2>
    <p style="color:#f0a500;font-size:0.82rem;">Use when police REFUSE to register FIR.</p>
    <label>Your Name</label>
    <input id="mp-complainant_name" placeholder="Full name">
    <label>Your Address</label>
    <input id="mp-complainant_address" placeholder="Full address">
    <label>Accused Name</label>
    <input id="mp-accused_name" placeholder="Full name of accused">
    <label>Crime Type</label>
    <input id="mp-crime_type" placeholder="e.g. online_fraud, cheating, assault">
    <label>Facts</label>
    <textarea id="mp-facts" placeholder="What happened and why police refused to register FIR..."></textarea>
    <button class="btn" onclick="ask('magistrate_petition')">Draft Petition</button>
    <div class="result" id="result-magistrate_petition" style="display:none"></div>
    <button class="close-btn" onclick="closeModal('magistrate_petition')">Close</button>
  </div>
</div>

<div class="modal" id="modal-bail">
  <div class="modal-box">
    <h2>🔓 Bail Guide + Application</h2>
    <label>Situation (describe briefly)</label>
    <textarea id="bail-situation" placeholder="e.g. my brother was arrested yesterday for cheating case, police custody since 2 days..."></textarea>
    <label>Crime Type</label>
    <input id="bail-crime_type" placeholder="e.g. cheating, assault, drug offence">
    <label>Already Arrested?</label>
    <select id="bail-already_arrested">
      <option value="false">No</option>
      <option value="true">Yes</option>
    </select>
    <label>Apprehending Arrest?</label>
    <select id="bail-apprehending_arrest">
      <option value="false">No</option>
      <option value="true">Yes — want anticipatory bail</option>
    </select>
    <label>Days in Custody (if arrested)</label>
    <input id="bail-days_custody" type="number" value="0" min="0">
    <button class="btn" onclick="ask('bail_guide')">Get Bail Guide</button>
    <hr style="border-color:#333;margin:16px 0">
    <h3 style="color:#f0a500">Draft Bail Application</h3>
    <label>Applicant Name</label>
    <input id="ba-applicant_name" placeholder="Name of arrested person">
    <label>Address</label>
    <input id="ba-address" placeholder="Residential address">
    <label>FIR Number</label>
    <input id="ba-fir_number" placeholder="e.g. FIR No. 123/2026">
    <label>Police Station</label>
    <input id="ba-ps_name" placeholder="Police station name">
    <label>District</label>
    <input id="ba-district" placeholder="e.g. Mumbai City">
    <label>Grounds for Bail</label>
    <textarea id="ba-grounds" placeholder="e.g. First offence, cooperated with investigation, no flight risk, has family dependents..."></textarea>
    <label>Bail Type</label>
    <select id="ba-bail_type">
      <option value="regular_bail">Regular Bail (after arrest)</option>
      <option value="anticipatory_bail">Anticipatory Bail (before arrest)</option>
      <option value="default_bail">Default Bail (60-day rule)</option>
    </select>
    <button class="btn" onclick="ask('bail_application')">Draft Application</button>
    <div class="result" id="result-bail_guide" style="display:none"></div>
    <div class="result" id="result-bail_application" style="display:none"></div>
    <button class="close-btn" onclick="closeModal('bail')">Close</button>
  </div>
</div>

<div class="modal" id="modal-notice_expanded">
  <div class="modal-box">
    <h2>📜 Legal Notice (25+ Types)</h2>
    <label>Notice Type</label>
    <select id="ne-notice_type">
      <option value="demand_money">Demand Money</option>
      <option value="cheque_bounce">Cheque Bounce (S.138)</option>
      <option value="defamation">Defamation</option>
      <option value="medical_negligence">Medical Negligence</option>
      <option value="trademark_infringement">Trademark Infringement</option>
      <option value="copyright_infringement">Copyright Infringement</option>
      <option value="data_breach">Data Breach / Privacy Violation</option>
      <option value="cyber_fraud">Cyber Fraud</option>
      <option value="motor_accident">Motor Accident Compensation</option>
      <option value="rera_builder">RERA Builder Delay</option>
      <option value="consumer_complaint">Consumer Complaint</option>
      <option value="landlord_tenant">Landlord / Tenant Dispute</option>
      <option value="employment_termination">Wrongful Employment Termination</option>
      <option value="salary_dues">Salary / Dues Recovery</option>
      <option value="property_encroachment">Property Encroachment</option>
      <option value="noise_pollution">Noise / Environmental Nuisance</option>
      <option value="insurance_claim">Insurance Claim Rejection</option>
      <option value="loan_recovery">Loan Recovery</option>
      <option value="harassment">Harassment / Stalking</option>
      <option value="breach_of_contract">Breach of Contract</option>
    </select>
    <label>Your Name (Sender)</label>
    <input id="ne-sender_name" placeholder="Your full name">
    <label>Your Address</label>
    <input id="ne-sender_address" placeholder="Your full address">
    <label>Recipient Name</label>
    <input id="ne-receiver_name" placeholder="Name of person/company to send notice to">
    <label>Recipient Address</label>
    <input id="ne-receiver_address" placeholder="Their full address">
    <label>Amount Involved (₹, if applicable)</label>
    <input id="ne-amount" type="number" placeholder="0">
    <label>Details / Facts</label>
    <textarea id="ne-details" placeholder="Describe what happened, dates, amounts, what you want them to do..."></textarea>
    <label>Deadline (days to respond)</label>
    <input id="ne-deadline_days" type="number" value="30" min="7">
    <button class="btn" onclick="ask('notice_expanded')">Draft Notice</button>
    <div class="result" id="result-notice_expanded" style="display:none"></div>
    <button class="close-btn" onclick="closeModal('notice_expanded')">Close</button>
  </div>
</div>

<script>
const uid = localStorage.getItem('lid') || (()=>{const id='u'+Math.random().toString(36).slice(2,10); localStorage.setItem('lid',id); return id;})();
document.cookie = `lid=${uid};path=/;max-age=31536000`;

async function loadQuota() {
  const r = await fetch('/quota?uid='+uid);
  const d = await r.json();
  const el = document.getElementById('quota');
  if(d.tier === 'pro') {
    el.innerHTML = `<span>PRO</span> — ${d.credits} credits remaining | ${d.total_queries} total queries`;
  } else {
    const rem = Math.max(0, 10 - (d.daily_used||0));
    el.innerHTML = `Free tier: <span>${rem}/10</span> queries remaining today | <a class="link" href="/upgrade">Get Pro</a>`;
  }
}
loadQuota();

function openModal(id) { document.getElementById('modal-'+id).classList.add('open'); }
function closeModal(id) { document.getElementById('modal-'+id).classList.remove('open'); }
document.querySelectorAll('.modal').forEach(m => m.addEventListener('click', e => { if(e.target===m) m.classList.remove('open'); }));

async function ask(type) {
  const resultEl = document.getElementById('result-'+type);
  resultEl.style.display='block';
  resultEl.textContent = 'Thinking...';

  let payload = {uid};
  if(type==='chat') payload.question = document.getElementById('chat-q').value;
  else if(type==='notice') payload = {...payload, notice_type: document.getElementById('notice-type').value,
    sender: document.getElementById('notice-sender').value, recipient: document.getElementById('notice-recipient').value,
    facts: document.getElementById('notice-facts').value};
  else if(type==='rti') payload = {...payload, info_sought: document.getElementById('rti-info').value,
    subject: document.getElementById('rti-subject').value, applicant: document.getElementById('rti-applicant').value};
  else if(type==='deadline') payload = {...payload, cause: document.getElementById('dl-type').value,
    date: document.getElementById('dl-date').value};
  else if(type==='document') payload = {...payload, doc_type: document.getElementById('doc-type').value,
    details: document.getElementById('doc-details').value};
  else if(type==='court') payload = {...payload, dispute: document.getElementById('court-desc').value,
    amount: document.getElementById('court-amount').value, state: document.getElementById('court-state').value};
  else if(type==='contract') payload = {...payload, text: document.getElementById('contract-text').value,
    role: document.getElementById('contract-role').value};
  else if(type==='tax') payload = {...payload, notice_text: document.getElementById('tax-notice').value,
    facts: document.getElementById('tax-facts').value};
  else if(type==='gains') payload = {...payload, asset_type: document.getElementById('gains-asset').value,
    buy_price: document.getElementById('gains-buy').value, sell_price: document.getElementById('gains-sell').value,
    buy_date: document.getElementById('gains-buydate').value, sell_date: document.getElementById('gains-selldate').value,
    units: document.getElementById('gains-units').value};
  else if(type==='labour') payload = {...payload, state: document.getElementById('labour-state').value,
    sector: document.getElementById('labour-sector').value, salary: document.getElementById('labour-salary').value,
    years: document.getElementById('labour-years').value, employees: document.getElementById('labour-employees').value};
  else if(type==='fir') payload = {...payload, situation: document.getElementById('fir-situation').value,
    state: document.getElementById('fir-state').value,
    already_tried: document.getElementById('fir-tried').value === 'true'};
  else if(type==='startup') payload = {...payload, stage: document.getElementById('startup-stage').value,
    entity: document.getElementById('startup-entity').value, state: document.getElementById('startup-state').value,
    sector: document.getElementById('startup-sector').value};
  else if(type==='posh') payload = {...payload, situation: document.getElementById('posh-situation').value,
    role: document.getElementById('posh-role').value,
    has_icc: document.getElementById('posh-icc').value === 'true'};
  else if(type==='succession') payload = {...payload, religion: document.getElementById('succession-religion').value,
    relatives: document.getElementById('succession-relatives').value,
    assets: document.getElementById('succession-assets').value};
  else if(type==='banking') payload = {...payload, fraud_type: document.getElementById('banking-type').value,
    amount: document.getElementById('banking-amount').value, days: document.getElementById('banking-days').value,
    bank: document.getElementById('banking-bank').value, details: document.getElementById('banking-details').value};
  else if(type==='property') payload = {...payload, property_type: document.getElementById('property-type').value,
    state: document.getElementById('property-state').value, value: document.getElementById('property-value').value,
    is_resale: document.getElementById('property-resale').value === 'true'};
  else if(type==='divorce') payload = {...payload, situation: document.getElementById('divorce-situation').value,
    religion: document.getElementById('divorce-religion').value,
    is_mutual: document.getElementById('divorce-mutual').value === 'null' ? null : document.getElementById('divorce-mutual').value === 'true',
    children: document.getElementById('divorce-children').value === 'true',
    dv: document.getElementById('divorce-dv').value === 'true'};

  else if(type==='police_complaint') payload = {...payload,
    crime_type: document.getElementById('pc-crime_type').value,
    complainant_name: document.getElementById('pc-complainant_name').value,
    complainant_address: document.getElementById('pc-complainant_address').value,
    accused_name: document.getElementById('pc-accused_name').value,
    accused_address: document.getElementById('pc-accused_address').value,
    incident_date: document.getElementById('pc-incident_date').value,
    incident_location: document.getElementById('pc-incident_location').value,
    facts: document.getElementById('pc-facts').value,
    property_lost: document.getElementById('pc-property_lost').value,
    state: document.getElementById('pc-state').value};

  else if(type==='magistrate_petition') payload = {...payload,
    complainant_name: document.getElementById('mp-complainant_name').value,
    complainant_address: document.getElementById('mp-complainant_address').value,
    accused_name: document.getElementById('mp-accused_name').value,
    crime_type: document.getElementById('mp-crime_type').value,
    facts: document.getElementById('mp-facts').value};

  else if(type==='bail_guide') payload = {...payload,
    situation: document.getElementById('bail-situation').value,
    crime_type: document.getElementById('bail-crime_type').value,
    already_arrested: document.getElementById('bail-already_arrested').value === 'true',
    apprehending_arrest: document.getElementById('bail-apprehending_arrest').value === 'true',
    days_custody: parseInt(document.getElementById('bail-days_custody').value) || 0};

  else if(type==='bail_application') payload = {...payload,
    applicant_name: document.getElementById('ba-applicant_name').value,
    address: document.getElementById('ba-address').value,
    crime_type: document.getElementById('bail-crime_type').value,
    fir_number: document.getElementById('ba-fir_number').value,
    ps_name: document.getElementById('ba-ps_name').value,
    district: document.getElementById('ba-district').value,
    grounds: document.getElementById('ba-grounds').value,
    bail_type: document.getElementById('ba-bail_type').value};

  else if(type==='notice_expanded') payload = {...payload,
    notice_type: document.getElementById('ne-notice_type').value,
    sender_name: document.getElementById('ne-sender_name').value,
    sender_address: document.getElementById('ne-sender_address').value,
    receiver_name: document.getElementById('ne-receiver_name').value,
    receiver_address: document.getElementById('ne-receiver_address').value,
    amount: document.getElementById('ne-amount').value || null,
    details: document.getElementById('ne-details').value,
    deadline_days: parseInt(document.getElementById('ne-deadline_days').value) || 30};

  const r = await fetch('/fleet/'+type, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload)});
  const d = await r.json();

  if(!d.ok && d.error && d.error.includes('limit')) {
    resultEl.textContent = 'WARNING: ' + d.error + '\n\nVisit /upgrade to add credits (Pro tier — unlimited queries)';
    resultEl.style.background = '#1f0d0d';
  } else {
    // Rich result: try common text fields; fall back to pretty JSON
    const text = d.draft || d.analysis || d.answer || d.result || d.summary || d.guide ||
                 d.checklist_text || d.rights || d.steps || d.calculation;
    if(text && typeof text === 'string') {
      resultEl.textContent = text;
    } else {
      resultEl.textContent = JSON.stringify(d, null, 2);
    }
  }
  loadQuota();
}
</script>
</body>
</html>"""

ADMIN_UI = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Admin Panel — Lawyer Fleet</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { background: #0a0a0a; color: #e0e0e0; font-family: system-ui, sans-serif; padding: 24px; }
h1 { color: #f0a500; margin-bottom: 16px; }
h2 { color: #f0a500; font-size: 1rem; margin: 20px 0 8px; }
.card { background: #1a1a1a; border: 1px solid #333; border-radius: 12px; padding: 16px; margin-bottom: 12px; }
table { width: 100%; border-collapse: collapse; font-size: 0.82rem; }
th { text-align: left; color: #f0a500; padding: 6px 10px; border-bottom: 1px solid #333; }
td { padding: 6px 10px; border-bottom: 1px solid #1e1e1e; color: #ccc; }
.badge { display: inline-block; background: #1a3a1a; color: #4caf50; border-radius: 4px; padding: 2px 8px; font-size: 0.7rem; }
.badge.dead { background: #3a1a1a; color: #f44; }
.btn { background: #f0a500; color: #000; border: none; border-radius: 6px; padding: 8px 18px; font-size: 0.85rem; font-weight: bold; cursor: pointer; margin-right: 8px; }
pre { background: #111; border: 1px solid #333; border-radius: 8px; padding: 12px; font-size: 0.78rem; overflow-x: auto; color: #ccc; margin-top: 8px; }
a { color: #f0a500; }
</style>
</head>
<body>
<h1>Lawyer Fleet — Admin Panel</h1>
<p style="color:#888;font-size:0.82rem;">Port 8766 &nbsp;|&nbsp; <a href="/">Back to Fleet</a></p>

<h2>Service Health</h2>
<div class="card" id="health-card">Loading...</div>

<h2>Agent Registry (17 agents)</h2>
<div class="card">
<table>
<thead><tr><th>Route</th><th>Module</th><th>Function</th></tr></thead>
<tbody>
<tr><td>/fleet/chat</td><td>lawyer</td><td>chat</td></tr>
<tr><td>/fleet/notice</td><td>lawyer_agents.legal_notice</td><td>draft_notice</td></tr>
<tr><td>/fleet/rti</td><td>lawyer_agents.rti_agent</td><td>draft_rti</td></tr>
<tr><td>/fleet/deadline</td><td>lawyer_agents.deadline_agent</td><td>calculate</td></tr>
<tr><td>/fleet/document</td><td>lawyer_agents.document_drafter</td><td>draft</td></tr>
<tr><td>/fleet/court</td><td>lawyer_agents.court_locator</td><td>locate</td></tr>
<tr><td>/fleet/contract_review</td><td>lawyer_agents.contract_review</td><td>review</td></tr>
<tr><td>/fleet/tax_notice</td><td>lawyer_agents.tax_notice</td><td>decode</td></tr>
<tr><td>/fleet/capital_gains</td><td>lawyer_agents.tax_notice</td><td>capital_gains</td></tr>
<tr><td>/fleet/labour</td><td>lawyer_agents.labour_rights</td><td>rights_check</td></tr>
<tr><td>/fleet/fir</td><td>lawyer_agents.fir_guide</td><td>guide</td></tr>
<tr><td>/fleet/startup</td><td>lawyer_agents.startup_legal</td><td>checklist</td></tr>
<tr><td>/fleet/posh</td><td>lawyer_agents.posh_guide</td><td>guide</td></tr>
<tr><td>/fleet/succession</td><td>lawyer_agents.succession</td><td>who_inherits</td></tr>
<tr><td>/fleet/banking_fraud</td><td>lawyer_agents.banking_fraud</td><td>recover</td></tr>
<tr><td>/fleet/property</td><td>lawyer_agents.property_law</td><td>due_diligence</td></tr>
<tr><td>/fleet/divorce</td><td>lawyer_agents.divorce_guide</td><td>guide</td></tr>
</tbody>
</table>
</div>

<h2>WhatsApp Bot</h2>
<div class="card" id="wa-card">Checking...</div>

<h2>Quick API Tests</h2>
<div class="card">
  <button class="btn" onclick="testHealth()">Health Check</button>
  <button class="btn" onclick="testAgent('chat', {question:'What is bail?'})">Test Chat</button>
  <button class="btn" onclick="testAgent('property', {property_type:'flat',state:'Maharashtra',value:8500000})">Test Property</button>
  <pre id="test-out">Click a button to test...</pre>
</div>

<h2>Credits / Quota</h2>
<div class="card">
  <input id="uid-input" placeholder="Enter user ID to inspect" style="background:#111;border:1px solid #444;color:#e0e0e0;border-radius:6px;padding:8px;width:260px;font-size:0.85rem;">
  <button class="btn" style="margin-left:8px" onclick="checkQuota()">Check Quota</button>
  <pre id="quota-out"></pre>
</div>

<script>
const uid = localStorage.getItem('lid') || 'admin';

async function loadHealth() {
  try {
    const r = await fetch('/health');
    const d = await r.json();
    document.getElementById('health-card').innerHTML =
      '<span class="badge">ONLINE</span> &nbsp; Port 8766 &nbsp;|&nbsp; ' +
      d.agents.length + ' agents registered<br><br>' +
      '<strong>Agents:</strong> ' + d.agents.join(', ');
  } catch(e) {
    document.getElementById('health-card').innerHTML = '<span class="badge dead">ERROR</span> ' + e;
  }
}

async function checkWA() {
  try {
    const r = await fetch('http://localhost:2785/api/isConnected');
    document.getElementById('wa-card').innerHTML = '<span class="badge">OpenWA ONLINE :2785</span> — WhatsApp bot active';
  } catch(e) {
    document.getElementById('wa-card').innerHTML = '<span class="badge dead">OpenWA OFFLINE</span> — Start with: python3 ~/Documents/polymarket/lawyer_whatsapp.py';
  }
}

async function testHealth() {
  const r = await fetch('/health');
  document.getElementById('test-out').textContent = JSON.stringify(await r.json(), null, 2);
}

async function testAgent(agent, payload) {
  payload.uid = uid;
  const r = await fetch('/fleet/'+agent, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload)});
  document.getElementById('test-out').textContent = JSON.stringify(await r.json(), null, 2);
}

async function checkQuota() {
  const u = document.getElementById('uid-input').value || uid;
  const r = await fetch('/quota?uid='+u);
  document.getElementById('quota-out').textContent = JSON.stringify(await r.json(), null, 2);
}

loadHealth();
checkWA();
</script>
</body>
</html>"""

class FleetHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args): pass

    def _json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _html(self, html):
        body = html.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/" or path == "":
            self._html(FLEET_UI)
        elif path == "/quota":
            params = urllib.parse.parse_qs(self.path.split("?",1)[-1] if "?" in self.path else "")
            uid = params.get("uid", ["anon"])[0]
            self._json(credits.stats(uid))
        elif path == "/upgrade":
            self._html("<h2>Pro Tier — Send Rs199/month via UPI to lawyer@upi and WhatsApp your UPI ref to +91-XXXXXXXXXX. Credits added within 1 hour.</h2>")
        elif path == "/health":
            import tools_registry
            self._json({"ok": True, "service": "lawyer-fleet", "port": 8766,
                        "named_agents": list(AGENTS.keys()) + [
                            "contract_review","tax_notice","capital_gains","labour","fir",
                            "startup","posh","succession","banking_fraud","property","divorce",
                            "gst","trademark","debt_recovery","msme","biz_registration",
                            "dpdp","arbitration","investor_terms","roc_calendar",
                            "police_complaint","magistrate_petition","sp_complaint","bns_info",
                            "bail_guide","bail_application","notice_expanded","notice_types",
                            "courtroom","analyze"],
                        "generic_tools_count": len(tools_registry.list_tools()),
                        "generic_tools_hint": "POST /fleet/<module>.<func> with {params:{...}} "
                                              "for any tool listed via POST /fleet/tools_list"})
        elif path == "/admin":
            self._html(ADMIN_UI)
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}
        uid = body.get("uid", "anon")
        path = self.path.split("?")[0]

        if not path.startswith("/fleet/"):
            self._json({"error": "unknown endpoint"}, 404)
            return

        agent_key = path[7:]  # strip /fleet/

        # Check credits
        ok, msg = credits.consume(uid, agent_key, str(body)[:200])
        if not ok:
            self._json({"ok": False, "error": msg})
            return

        try:
            result = self._dispatch(agent_key, body)
            self._json(result)
        except Exception as e:
            self._json({"ok": False, "error": str(e)})

    def _dispatch(self, agent_key, body):
        if agent_key == "chat":
            from lawyer import chat
            q = body.get("question", "")
            lang = body.get("lang")
            return chat(q, lang=lang)

        elif agent_key == "notice":
            fn = _import_fn("lawyer_agents.legal_notice", "draft_notice")
            return fn(body.get("notice_type","demand"), {
                "sender_name": body.get("sender",""),
                "recipient_name": body.get("recipient",""),
                "facts": body.get("facts",""),
            })

        elif agent_key == "rti":
            fn = _import_fn("lawyer_agents.rti_agent", "draft_rti")
            return fn(body.get("subject",""), body.get("info_sought",""),
                      body.get("applicant",""), body.get("applicant",""))

        elif agent_key == "deadline":
            fn = _import_fn("lawyer_agents.deadline_agent", "calculate")
            return fn(body.get("cause",""), body.get("date",""))

        elif agent_key == "document":
            fn = _import_fn("lawyer_agents.document_drafter", "draft")
            details = {}
            raw = body.get("details","")
            if raw:
                details["description"] = raw
            return fn(body.get("doc_type","rent_agreement"), details)

        elif agent_key == "court":
            fn = _import_fn("lawyer_agents.court_locator", "locate")
            return fn(body.get("dispute",""), body.get("amount"), body.get("state"))

        elif agent_key == "contract_review":
            fn = _import_fn("lawyer_agents.contract_review", "review")
            return fn(body.get("text", ""), body.get("role", "party signing"))

        elif agent_key == "tax_notice":
            fn = _import_fn("lawyer_agents.tax_notice", "decode")
            return fn(body.get("notice_text", ""), body.get("facts", ""))

        elif agent_key == "capital_gains":
            fn = _import_fn("lawyer_agents.tax_notice", "capital_gains")
            return fn(body.get("asset_type","equity"), float(body.get("buy_price",0)),
                      float(body.get("sell_price",0)), body.get("buy_date",""),
                      body.get("sell_date",""), int(body.get("units",1)))

        elif agent_key == "labour":
            fn = _import_fn("lawyer_agents.labour_rights", "rights_check")
            return fn(body.get("state",""), body.get("sector",""),
                      float(body.get("salary",0)), float(body.get("years",0)), int(body.get("employees",50)))

        elif agent_key == "fir":
            fn = _import_fn("lawyer_agents.fir_guide", "guide")
            return fn(body.get("situation",""), body.get("state"), body.get("already_tried", False))

        elif agent_key == "startup":
            fn = _import_fn("lawyer_agents.startup_legal", "checklist")
            return fn(body.get("stage","incorporated"), body.get("entity","pvt_ltd"), body.get("state",""), body.get("sector",""))

        elif agent_key == "posh":
            fn = _import_fn("lawyer_agents.posh_guide", "guide")
            return fn(body.get("situation",""), body.get("role","complainant"), body.get("has_icc", True))

        elif agent_key == "succession":
            fn = _import_fn("lawyer_agents.succession", "who_inherits")
            return fn(body.get("religion","Hindu"), body.get("relatives",""), body.get("assets",""))

        elif agent_key == "banking_fraud":
            fn = _import_fn("lawyer_agents.banking_fraud", "recover")
            return fn(body.get("fraud_type","upi_fraud"), float(body.get("amount",0)), int(body.get("days",1)), body.get("bank",""), body.get("details",""))

        elif agent_key == "property":
            fn = _import_fn("lawyer_agents.property_law", "due_diligence")
            return fn(body.get("property_type","flat"), body.get("state","Maharashtra"), float(body.get("value",0)), body.get("is_resale",True))

        elif agent_key == "divorce":
            fn = _import_fn("lawyer_agents.divorce_guide", "guide")
            return fn(body.get("situation",""), body.get("religion","Hindu"), body.get("is_mutual"), body.get("children",False), body.get("dv",False))

        elif agent_key == "gst":
            fn = _import_fn("lawyer_agents.gst_compliance", "compliance_check")
            return fn(float(body.get("turnover",0)), body.get("state",""), body.get("sector",""), body.get("interstate",False))

        elif agent_key == "gst_notice":
            fn = _import_fn("lawyer_agents.gst_compliance", "decode_notice")
            return fn(body.get("notice_type",""), body.get("facts",""))

        elif agent_key == "gst_late_fee":
            fn = _import_fn("lawyer_agents.gst_compliance", "late_fee_calc")
            return fn(body.get("return_type","GSTR-3B"), int(body.get("days_late",0)), float(body.get("turnover",0)), body.get("nil_return",False))

        elif agent_key == "trademark":
            fn = _import_fn("lawyer_agents.trademark_ip", "find_class")
            return fn(body.get("description",""))

        elif agent_key == "trademark_file":
            fn = _import_fn("lawyer_agents.trademark_ip", "filing_guide")
            return fn(body.get("mark",""), body.get("business",""), body.get("classes",""), body.get("applicant_type","startup"))

        elif agent_key == "copyright":
            fn = _import_fn("lawyer_agents.trademark_ip", "copyright_guide")
            return fn(body.get("work_type",""), body.get("creator",""))

        elif agent_key == "debt_recovery":
            fn = _import_fn("lawyer_agents.debt_recovery", "recovery_plan")
            return fn(float(body.get("amount",0)), body.get("debtor_type","individual"), int(body.get("days_overdue",0)), body.get("is_msme",False), body.get("has_cheque",False))

        elif agent_key == "msme":
            fn = _import_fn("lawyer_agents.msme_benefits", "check_eligibility")
            return fn(float(body.get("investment",0)), float(body.get("turnover",0)), body.get("sector",""), body.get("state",""))

        elif agent_key == "biz_registration":
            fn = _import_fn("lawyer_agents.business_registration", "registration_guide")
            return fn(body.get("entity_type","pvt_ltd"), int(body.get("founders",2)), body.get("state",""), body.get("sector",""), body.get("foreign_investment",False))

        elif agent_key == "dpdp":
            fn = _import_fn("lawyer_agents.dpdp_compliance", "compliance_audit")
            return fn(body.get("business_type",""), body.get("data_collected",""), int(body.get("user_count",0)), body.get("processes_children",False), body.get("cross_border",False))

        elif agent_key == "arbitration":
            fn = _import_fn("lawyer_agents.arbitration", "recommend_route")
            return fn(body.get("dispute_type",""), float(body.get("amount",0)), body.get("has_arb_clause",False), body.get("parties",""), body.get("urgency","medium"))

        elif agent_key == "arb_clause":
            fn = _import_fn("lawyer_agents.arbitration", "draft_arbitration_clause")
            return fn(body.get("governing_law","India"), body.get("seat","Delhi"), body.get("institution","DIAC"))

        elif agent_key == "investor_terms":
            fn = _import_fn("lawyer_agents.investor_terms", "explain_term_sheet")
            return fn(body.get("term_sheet_text",""))

        elif agent_key == "esop":
            fn = _import_fn("lawyer_agents.investor_terms", "esop_guide")
            return fn(body.get("stage",""), int(body.get("total_shares",1000000)), float(body.get("esop_pct",10)))

        elif agent_key == "roc_calendar":
            fn = _import_fn("lawyer_agents.roc_compliance", "calendar")
            return fn(body.get("entity_type","pvt_ltd"), body.get("inc_date",""), float(body.get("turnover",0)), body.get("foreign_investment",False))

        elif agent_key == "roc_penalty":
            fn = _import_fn("lawyer_agents.roc_compliance", "penalty_calc")
            return fn(body.get("form",""), int(body.get("days_late",0)))


        elif agent_key == "police_complaint":
            fn = _import_fn("lawyer_agents.police_complaint", "draft_complaint")
            return fn(body.get("crime_type","online_fraud"), body.get("facts",""), body.get("complainant_name",""), body.get("complainant_address",""), body.get("accused_name","Unknown"), body.get("accused_address",""), body.get("incident_date",""), body.get("incident_location",""), body.get("witnesses","None"), body.get("property_lost",""), body.get("state",""), body.get("lang"))

        elif agent_key == "magistrate_petition":
            fn = _import_fn("lawyer_agents.police_complaint", "draft_magistrate_petition")
            return fn(body.get("complainant_name",""), body.get("complainant_address",""), body.get("accused_name",""), body.get("crime_type",""), body.get("facts",""))

        elif agent_key == "sp_complaint":
            fn = _import_fn("lawyer_agents.police_complaint", "draft_sp_complaint")
            return fn(body.get("complainant_name",""), body.get("crime_type",""), body.get("ps_name",""), body.get("facts",""), body.get("district",""))

        elif agent_key == "bns_info":
            fn = _import_fn("lawyer_agents.police_complaint", "get_bns_info")
            result = fn(body.get("crime_type",""))
            return {"ok": True, "info": result, "agent": "police_complaint"}

        elif agent_key == "bail_guide":
            fn = _import_fn("lawyer_agents.bail_guide", "guide")
            return fn(body.get("situation",""), body.get("crime_type",""), body.get("already_arrested",False), body.get("apprehending_arrest",False), int(body.get("days_custody",0)))

        elif agent_key == "bail_application":
            fn = _import_fn("lawyer_agents.bail_guide", "draft_bail_application")
            return fn(body.get("applicant_name",""), body.get("address",""), body.get("crime_type",""), body.get("fir_number",""), body.get("ps_name",""), body.get("district",""), body.get("grounds",""), body.get("bail_type","regular_bail"))

        elif agent_key == "notice_expanded":
            fn = _import_fn("lawyer_agents.legal_notice_expanded", "draft")
            return fn(body.get("notice_type","demand_money"), body.get("details",""), body.get("sender_name",""), body.get("sender_address",""), body.get("receiver_name",""), body.get("receiver_address",""), body.get("amount"), int(body.get("deadline_days",30)), body.get("lang"))

        elif agent_key == "notice_types":
            fn = _import_fn("lawyer_agents.legal_notice_expanded", "list_types")
            return {"ok": True, "notice_types": fn(), "agent": "legal_notice_expanded"}

        elif agent_key == "courtroom":
            from lawyer import courtroom
            return courtroom(body.get("case", ""))

        elif agent_key == "analyze":
            from lawyer import analyze_document
            return analyze_document(body.get("text", ""), body.get("hint", ""))

        elif agent_key == "tools_list":
            import tools_registry
            return {"ok": True, "tools": tools_registry.list_tools()}

        elif "." in agent_key:
            # generic fallback — any of the 47 auto-discovered lawyer_agents functions,
            # addressed as "module.func" (matches tools_registry's key format), so every
            # tool works through this fleet even without a hand-written dispatch case above
            import tools_registry
            result = tools_registry.call(agent_key, body.get("params", {}))
            return {"ok": True, "result": result, "agent": agent_key}

        return {"ok": False, "error": f"Unknown agent: {agent_key}"}

def run(port=8766):
    server = ThreadingHTTPServer(("", port), FleetHandler)
    server.daemon_threads = True
    print(f"Lawyer fleet running on http://localhost:{port}")
    server.serve_forever()

if __name__ == "__main__":
    run()
