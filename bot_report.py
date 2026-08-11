#!/usr/bin/env python3
"""bot_report.py — build polymarket_bot_report.pdf with fully live data.
Pulls: honest_report.py (P&L), gate_watcher.py (gate), fade_checkpoint.py (nearresfade CI).
Paper only — no real money."""
import os, sys, subprocess, datetime, re

HERE = os.path.dirname(os.path.abspath(__file__))
OUT  = os.path.join(HERE, "polymarket_bot_report.pdf")
NOW  = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
DATE = datetime.datetime.now().strftime("%A, %d %B %Y")


def _ascii(s):
    """ReportLab's built-in Courier has no emoji/box glyphs — map to ASCII."""
    repl = {"⏳": "[..]", "✅": "[OK]", "❌": "[X]", "⚠️": "[!]", "—": "--",
            "·": ".", "≥": ">=", "≤": "<=", "→": "->", "←": "<-", "🏁": "[GATE]"}
    for k, v in repl.items():
        s = s.replace(k, v)
    return s.encode("ascii", "replace").decode()


def _run(script):
    try:
        r = subprocess.run([sys.executable, os.path.join(HERE, script)],
                           capture_output=True, text=True, timeout=90)
        return (r.stdout + r.stderr).strip() or f"({script} produced no output)"
    except Exception as e:
        return f"({script} failed: {e})"


def build():
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import inch
    from reportlab.lib import colors
    from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer,
                                    HRFlowable, Preformatted)
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

    def esc(s): return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

    ss = getSampleStyleSheet()
    H1   = ParagraphStyle('H1',   parent=ss['Title'],   fontSize=18, spaceAfter=2)
    SUB  = ParagraphStyle('SUB',  parent=ss['Normal'],  fontSize=8.5, textColor=colors.grey, spaceAfter=8)
    H2   = ParagraphStyle('H2',   parent=ss['Heading2'],fontSize=12.5, spaceBefore=10, spaceAfter=4,
                           textColor=colors.HexColor('#1a3a5c'))
    B    = ParagraphStyle('B',    parent=ss['Normal'],  fontSize=9, leading=12.5, spaceAfter=4)
    MONO = ParagraphStyle('MONO', parent=ss['Code'],    fontSize=6.7, leading=7.8)

    honest  = _run("honest_report.py")
    gate    = _run("gate_watcher.py")
    fadeck  = _run("fade_checkpoint.py")

    # extract top-line numbers for summary
    total_match = re.search(r'TOTAL\s+([\-\+][\d.]+)', honest)
    total_pnl = total_match.group(1) if total_match else "?"

    s = []
    s.append(Paragraph("Polymarket Bot — Daily Report", H1))
    s.append(Paragraph(f"{DATE} · Generated {NOW} · PAPER ONLY, no real money", SUB))
    s.append(HRFlowable(width="100%", color=colors.lightgrey))

    s.append(Paragraph("1 · Realized P&amp;L (closed exits only)", H2))
    s.append(Paragraph(
        "Exit (closed) trades only. Open positions are unrealized and excluded. "
        "Controls (allin/coinflip) must be negative — if positive, accounting is broken.", B))
    s.append(Preformatted(_ascii(honest), MONO))

    s.append(Paragraph("2 · Gate progress (conviction + moonshot legs)", H2))
    s.append(Paragraph(
        "Gate passes when: >=50 exits, >=90% win rate, >=5x avg return, >=10% of trades hit 10x. "
        "Also tracking fade (>=30 exits), fastfade (>=40 exits).", B))
    s.append(Preformatted(_ascii(gate), MONO))

    s.append(Paragraph("3 · NearresFade gate (nearres replacement strategy)", H2))
    s.append(Paragraph(
        "Needs >=30 priced exits with 95% CI excluding 0. Controls must stay negative.", B))
    s.append(Preformatted(_ascii(fadeck), MONO))

    s.append(Paragraph("4 · Hard rules (always in effect)", H2))
    rules = [
        "Paper only. Never real orders, never API keys, never move funds.",
        "Optimize dollars, not win rate. A 90% win rate losing $ is a trap.",
        ">=30 closed exits + bootstrap CI > 0 before claiming any edge.",
        "Controls (allin, coinflip) MUST lose. If they don't -> marking bug.",
        "Don't churn config on mood. Each config change resets the experiment.",
    ]
    for r in rules:
        s.append(Paragraph("• " + esc(r), B))

    SimpleDocTemplate(OUT, pagesize=letter,
                      topMargin=0.6*inch, bottomMargin=0.6*inch,
                      leftMargin=0.6*inch, rightMargin=0.6*inch,
                      title=f"Polymarket Bot Daily Report {DATE}").build(s)
    print(f"wrote {OUT}")


if __name__ == "__main__":
    build()
