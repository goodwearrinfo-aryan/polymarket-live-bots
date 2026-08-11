#!/usr/bin/env python3
"""
Generate "Live Trades.pdf" from trade_log.json into a synced folder (iCloud
Drive) so it can be viewed on the phone. Refreshed periodically by the watchdog.

Usage:
  python3 trades_report.py [trade_log.json] [output_dir]
Defaults: trade_log.json next to this script; output to the iCloud PolymarketBot folder.
"""
import sys, os, json
from datetime import datetime

# reportlab — self-install if missing (the watchdog/Mac has internet)
try:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
except ImportError:
    os.system(f"{sys.executable} -m pip install reportlab --break-system-packages -q")
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

BASE = os.path.dirname(os.path.abspath(__file__))
TRADE_LOG = sys.argv[1] if len(sys.argv) > 1 else os.path.join(BASE, "trade_log.json")
OUT_DIR = sys.argv[2] if len(sys.argv) > 2 else os.path.expanduser(
    "~/Library/Mobile Documents/com~apple~CloudDocs/PolymarketBot")
CUTOFF = "2026-06-08T00:19:00"   # clean-sample start (after entry-filter upgrade: max_price 0.65, vol 50k, conf 0.60)


def load():
    with open(TRADE_LOG) as f:
        d = json.load(f)
    return d.get("open", []), d.get("closed", [])


def clip(s, n=42):
    s = str(s or "")
    return s if len(s) <= n else s[: n - 1] + "…"


def build():
    os.makedirs(OUT_DIR, exist_ok=True)
    open_pos, closed = load()
    clean = [t for t in closed
             if t.get("opened_at", "") >= CUTOFF and round(t.get("entry_price", 0.5), 3) != 0.5]
    w = sum(1 for t in clean if t.get("pnl_usdc", 0) > 0)
    wr = (w / len(clean) * 100) if clean else None
    pnl = sum(t.get("pnl_usdc", 0) for t in clean)

    styles = getSampleStyleSheet()
    h = ParagraphStyle("h", parent=styles["Title"], fontSize=18, spaceAfter=2)
    sub = ParagraphStyle("sub", parent=styles["Normal"], fontSize=9, textColor=colors.grey)
    # Big, prominent freshness stamp — so even if iOS Files caches a stale
    # mtime in the file browser, you can SEE when this PDF was generated.
    fresh = ParagraphStyle("fresh", parent=styles["Title"], fontSize=14,
                           textColor=colors.HexColor("#c0683f"), spaceAfter=4)
    sec = ParagraphStyle("sec", parent=styles["Heading2"], fontSize=12, spaceBefore=10, spaceAfter=4)

    # Single file overwritten in place. Freshness is read from the prominent
    # timestamp inside the PDF (orange line below the title), not the filename.
    now = datetime.now()
    stable_path = os.path.join(OUT_DIR, "Live Trades.pdf")
    doc = SimpleDocTemplate(stable_path,
                            pagesize=A4, topMargin=14 * mm, bottomMargin=12 * mm,
                            leftMargin=12 * mm, rightMargin=12 * mm)
    el = []
    el.append(Paragraph("Polymarket — Live Trades", h))
    el.append(Paragraph(f"⏱ Updated {now.strftime('%H:%M:%S')} · {now.strftime('%Y-%m-%d')}", fresh))
    el.append(Paragraph(
        f"paper/dry-run · open {len(open_pos)} · clean closed {len(clean)}"
        + (f" · win rate {wr:.0f}% · P&amp;L ${pnl:+.2f}" if clean else " · win rate n/a"), sub))
    el.append(Spacer(1, 6))

    def table(rows, header, widths):
        data = [header] + rows
        t = Table(data, colWidths=widths, repeatRows=1)
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2c2c2a")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTSIZE", (0, 0), (-1, -1), 7.5),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f1efe8")]),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#d3d1c7")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ]))
        return t

    # Open positions (newest first)
    el.append(Paragraph(f"Open positions ({len(open_pos)})", sec))
    op = sorted(open_pos, key=lambda t: t.get("opened_at", ""), reverse=True)[:45]
    rows = [[clip(t.get("title")), t.get("strategy", ""), t.get("side", ""),
             f"{t.get('entry_price', 0):.3f}", f"${t.get('cost_usdc', 0):.2f}"] for t in op]
    el.append(table(rows or [["—", "", "", "", ""]],
                    ["Market", "Strat", "Side", "Entry", "Cost"],
                    [92 * mm, 22 * mm, 14 * mm, 18 * mm, 20 * mm]))

    # Recent clean closed (newest first)
    el.append(Paragraph(f"Recent closed — clean sample ({len(clean)})", sec))
    cc = sorted(clean, key=lambda t: t.get("closed_at", ""), reverse=True)[:30]
    rows = [[clip(t.get("title")), t.get("strategy", ""),
             f"{t.get('entry_price', 0):.2f}", f"{t.get('close_price', 0):.2f}",
             f"${t.get('pnl_usdc', 0):+.2f}", clip(t.get("reason", ""), 12)] for t in cc]
    el.append(table(rows or [["no clean trades closed yet", "", "", "", "", ""]],
                    ["Market", "Strat", "Entry", "Close", "P&L", "Reason"],
                    [78 * mm, 20 * mm, 16 * mm, 16 * mm, 18 * mm, 24 * mm]))

    doc.build(el)
    print("wrote", stable_path)


if __name__ == "__main__":
    build()
