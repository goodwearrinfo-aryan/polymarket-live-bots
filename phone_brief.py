#!/usr/bin/env python3
"""
phone_brief.py — one-page "pocket brief" PDF for the phone.

Reads the live state files and writes a concise, phone-readable summary
(best strategies, the real edge + its caveat, current paper P&L, the shortcut
.command files, hard rules) to the iCloud folder so it syncs to your phone.

Run on the Mac:  python3 phone_brief.py
"""
import os, sys, json
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
ICLOUD = os.path.expanduser("~/Library/Mobile Documents/com~apple~CloudDocs/PolymarketBot")

try:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
except ImportError:
    os.system(f"{sys.executable} -m pip install reportlab --break-system-packages -q")
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle


def jload(p, default):
    try:
        return json.load(open(os.path.join(BASE, p)))
    except Exception:
        return default


def strat_pnl(state, key):
    c = state.get(key, {}).get("closed", [])
    o = len(state.get(key, {}).get("open", []))
    n = len(c); pnl = sum(r.get("pnl_usdc", 0) for r in c)
    w = sum(1 for r in c if r.get("pnl_usdc", 0) > 0)
    return o, n, (w / n * 100 if n else 0), pnl


def main():
    lab = jload("scalp_lab_state.json", {})
    eng = jload("scalp_engine_state.json", {})
    edge = jload("edge_trader_state.json", {"open": [], "closed": []})

    rows = [["Strategy", "Open", "Closed", "Win%", "Paper P&L", "Trust?"]]
    def add(name, st, key, trust):
        o, n, wr, pnl = strat_pnl(st, key)
        rows.append([name, str(o), str(n), f"{wr:.0f}%", f"${pnl:+.2f}", trust])
    add("EDGE-ML (house pick)", {"edge": edge}, "edge", "after recalib")
    add("fade", lab, "fade", "control broken")
    add("fastfade", lab, "fastfade", "control broken")
    add("scalp", lab, "scalp", "control broken")
    add("allin (control)", lab, "allin", "should be NEG")
    add("dip (KILLED)", lab, "dip", "dead")
    add("scalp-maker", eng, "maker", "FANTASY")
    add("scalp-taker (KILLED)", eng, "taker", "honest")

    styles = getSampleStyleSheet()
    h = ParagraphStyle("h", parent=styles["Heading2"], textColor=colors.HexColor("#0d3b66"), spaceAfter=4)
    body = ParagraphStyle("b", parent=styles["Normal"], fontSize=10, leading=14)
    small = ParagraphStyle("s", parent=styles["Normal"], fontSize=8.5, leading=12, textColor=colors.HexColor("#444"))

    os.makedirs(ICLOUD, exist_ok=True)
    out = os.path.join(ICLOUD, "Polymarket Pocket Brief.pdf")
    doc = SimpleDocTemplate(out, pagesize=A4, topMargin=14*mm, bottomMargin=12*mm,
                            leftMargin=14*mm, rightMargin=14*mm)
    el = []
    el.append(Paragraph("Polymarket Bot — Pocket Brief", styles["Title"]))
    el.append(Paragraph(datetime.now().strftime("%a %d %b %Y, %H:%M") + " · paper only, no real money", small))
    el.append(Spacer(1, 6))

    el.append(Paragraph("Strategies (live paper)", h))
    t = Table(rows, colWidths=[42*mm, 14*mm, 16*mm, 14*mm, 24*mm, 30*mm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0d3b66")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#bbbbbb")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f2f5f9")]),
    ]))
    el.append(t)
    el.append(Spacer(1, 8))

    el.append(Paragraph("The real edge (and its catch)", h))
    el.append(Paragraph(
        "An ML model that predicts market outcomes beat the market price on clean, "
        "leak-free data (AUC 0.82 vs 0.75) and the divergence strategy backtested at "
        "<b>+$0.116/trade, 69% win, out-of-sample</b>, beating a correctly-negative control. "
        "BUT the model was found <b>overconfident</b> (said ~0.4% on a 41% market) — so its live "
        "bets ('EDGE-ML') were NOT trustworthy. It has been <b>recalibrated (isotonic)</b>; "
        "the bet is whether the ranking edge survives once probabilities are honest.", body))
    el.append(Spacer(1, 6))

    el.append(Paragraph("Don't be fooled", h))
    el.append(Paragraph(
        "• <b>scalp-maker</b> P&L is FANTASY (assumes limit fills you won't get). Ignore it.<br/>"
        "• The lab's greens (fade/fastfade/scalp) are <b>inflated</b> — the 'allin' control is "
        "positive, which it shouldn't be after spread, so the marking is optimistic.<br/>"
        "• Win rate is vanity. Optimize DOLLARS. Need 20–30 CLOSED trades before any verdict.", body))
    el.append(Spacer(1, 6))

    el.append(Paragraph("Shortcuts (double-click on the Mac)", h))
    el.append(Paragraph(
        "• <b>run_phase1.command</b> — collect markets + honest grouped eval (edge vs price)<br/>"
        "• <b>run_phase3.command</b> — backtest the edge in dollars vs the control<br/>"
        "• <b>run_recalibrate.command</b> — retrain with calibration + sanity check<br/>"
        "• <b>run_sanity_check.command</b> — is the model sane or saying ~0 for everything?<br/>"
        "• <b>watchdog.command</b> — (re)start the keep-alive (bot + lab + engine + edge)", body))
    el.append(Spacer(1, 6))
    el.append(Paragraph("Next: recalibrate → confirm sane probs → let EDGE-ML gather 20–30 closed "
                        "trades → compare to control → only then tiny real money.", small))

    doc.build(el)
    # also drop a copy next to the project so it can be reviewed without iCloud
    try:
        import shutil
        shutil.copy(out, os.path.join(BASE, "Polymarket Pocket Brief.pdf"))
    except Exception:
        pass
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
