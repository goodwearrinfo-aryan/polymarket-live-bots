#!/usr/bin/env python3
"""build_status_pdf.py — regenerate polymarket_status.pdf from CURRENT state.

Honest realized (exit) P&L per leg (target exits capped at the limit fill),
open-position counts, and latency-probe status. Read-only. Reuses
honest_report.py so the numbers always match the report.
Run: python3 build_status_pdf.py
"""
import os, sys, json, re, datetime
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import honest_report as hr

OUT = os.path.join(HERE, "polymarket_status.pdf")

CONTROLS = {"allin", "coinflip", "coindown"}   # mirrors honest_report.py
TOP_N = 20   # individual table rows; the rest are aggregated (total still ties out)

def collect():
    """Same canonical accounting as honest_report.py: EVERY leg with a book in
    state (not a hardcoded legacy list), stale no-data exits excluded from
    counts (they book $0, so the total is unchanged). The TOTAL here must
    always equal honest_report.py's TOTAL line."""
    state = json.load(open(hr.find_state()))
    legs = hr.legs_roster(state)
    per_leg, total = [], 0.0
    for leg in legs:
        c = state.get(leg, {}).get("closed", [])
        o = state.get(leg, {}).get("open", [])
        priced = hr.priced_exits(c)
        if not priced and not o:
            continue
        realized = round(sum(hr.honest_pnl(leg, t) for t in priced), 4)
        wins = sum(1 for t in priced if hr.honest_pnl(leg, t) > 1e-9)
        wr = round(wins / len(priced) * 100, 1) if priced else 0.0
        total += realized
        per_leg.append((leg, len(priced), realized, wr, len(o)))
    # Table rows: top movers by |realized| + all controls, worst-first; the
    # remainder is one aggregate row so the column still sums to TOTAL.
    by_impact = sorted(per_leg, key=lambda r: -abs(r[2]))
    shown = {r[0] for r in by_impact[:TOP_N]} | (CONTROLS & {r[0] for r in per_leg})
    rows = sorted((r for r in per_leg if r[0] in shown), key=lambda r: r[2])
    rest = [r for r in per_leg if r[0] not in shown]
    agg = None
    if rest:
        agg = (f"({len(rest)} more legs)", sum(r[1] for r in rest),
               round(sum(r[2] for r in rest), 4), None, sum(r[4] for r in rest))
    return rows, agg, len(per_leg), round(total, 4)

def latency_status():
    p = os.path.join(HERE, "ml", "reprice.log")
    if not os.path.exists(p):
        return "not run yet"
    txt = open(p, errors="replace").read()
    if "=== VERDICT ===" in txt:
        m = re.search(r"MEDIAN=([0-9.]+)", txt)
        med = f"median {m.group(1)} min" if m else "verdict ready"
        return f"DONE — {med} (see ml/reprice.log)"
    return "pending — network-blocked (auto-retries daily)"

def build():
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import inch
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    rows, agg, n_legs, total = collect()
    lat = latency_status()
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    ss = getSampleStyleSheet()
    H1 = ParagraphStyle('H1', parent=ss['Title'], fontSize=17, spaceAfter=2)
    SUB = ParagraphStyle('SUB', parent=ss['Normal'], fontSize=9, textColor=colors.grey, spaceAfter=10)
    B = ParagraphStyle('B', parent=ss['Normal'], fontSize=9.5, leading=13, spaceAfter=6)
    SM = ParagraphStyle('SM', parent=ss['Normal'], fontSize=8, textColor=colors.grey)
    doc = SimpleDocTemplate(OUT, pagesize=letter, topMargin=0.7*inch, bottomMargin=0.6*inch,
                            leftMargin=0.8*inch, rightMargin=0.8*inch)
    net = hr.gamma_status()
    net_color = colors.HexColor('#2a9d4a') if net.startswith("OK") else colors.HexColor('#c0392b')
    NET = ParagraphStyle('NET', parent=ss['Normal'], fontSize=10, spaceAfter=10,
                         textColor=net_color, fontName='Helvetica-Bold')
    s = [Paragraph("Polymarket Paper Status", H1),
         Paragraph(f"Auto-generated {now} · realized = EXIT trades only, honest-capped, ALL {n_legs} legs, "
                   f"stale no-data exits excluded (booked $0) · same accounting as honest_report.py · "
                   f"paper, no real money", SUB),
         Paragraph(f"NETWORK (Mac &#8594; Polymarket): {net}", NET)]
    data = [["Leg", "Exits", "Realized $", "Win %", "Open", "Note"]]
    for leg, n, realized, wr, o in rows:
        note = "too few (<10)" if n < 10 else ("control" if leg in CONTROLS else "")
        if n >= 10 and realized <= 0 and wr >= 60:
            note = "win-rate trap"
        data.append([leg, str(n), f"{realized:+.3f}", f"{wr:.0f}%", str(o), note])
    if agg:
        label, n, realized, _, o = agg
        data.append([label, str(n), f"{realized:+.3f}", "", str(o), "smaller legs, aggregated"])
    data.append(["TOTAL", "", f"{total:+.3f}", "", "", f"realized, ALL {n_legs} legs"])
    t = Table(data, colWidths=[1.1*inch, 0.6*inch, 0.9*inch, 0.6*inch, 0.5*inch, 1.7*inch])
    t.setStyle(TableStyle([
        ('FONTSIZE', (0,0), (-1,-1), 8.5),
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1f4e79')),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTNAME', (0,-1), (-1,-1), 'Helvetica-Bold'),
        ('GRID', (0,0), (-1,-1), 0.4, colors.HexColor('#cccccc')),
        ('ROWBACKGROUNDS', (0,1), (-1,-2), [colors.white, colors.HexColor('#f2f5f9')]),
        ('TOPPADDING', (0,0), (-1,-1), 3), ('BOTTOMPADDING', (0,0), (-1,-1), 3)]))
    s.append(t)
    s.append(Spacer(1, 10))
    s.append(Paragraph(f"<b>Latency probe:</b> {lat}", B))
    s.append(Paragraph("<b>ML edge:</b> dead (price-alone AUC 0.747 vs model 0.775 = noise). edge_trader disabled.", B))
    s.append(Paragraph("Reminder: legs need ~10 exits before any number means anything; controls (allin, coinflip) should be negative.", SM))
    doc.build(s)
    print("WROTE", OUT, "| total realized", total, "| latency:", lat)

if __name__ == "__main__":
    build()
