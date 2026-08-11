#!/usr/bin/env python3
"""
Every-10-min lab report: per-leg P&L + trades opened/closed in the last window.
Writes "Lab Report.pdf" to iCloud (phone-viewable) and iMessages it as an
attachment. Launchd: com.aryan.polymarket-lab-pdf (StartInterval 600).
Paper trading only — this reports, never trades.
"""
import os, sys, json, subprocess, time
from datetime import datetime, timezone, timedelta

try:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet
except ImportError:
    os.system(f"{sys.executable} -m pip install reportlab --break-system-packages -q")
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet

BASE      = os.path.expanduser("~/Documents/polymarket")
STATE     = os.path.join(BASE, "scalp_lab_state.json")
OUT_DIR   = os.path.expanduser("~/Library/Mobile Documents/com~apple~CloudDocs/PolymarketBot-Lab")
PDF_PATH  = os.path.join(OUT_DIR, "Lab Report.pdf")
WINDOW_MIN = 10
TARGETS   = ["krisharyan@icloud.com"]
CONTROLS  = {"allin", "coinflip", "coindown"}


def parse_iso(s):
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def main():
    with open(STATE) as f:
        state = json.load(f)

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=WINDOW_MIN)

    rows, new_opens, new_closes = [], [], []
    grand, ctrl_pnl, sig_pnl = 0.0, 0.0, 0.0
    for leg, book in sorted(state.items()):
        if not isinstance(book, dict) or "open" not in book:
            continue
        open_, closed = book.get("open", []), book.get("closed", [])
        priced = [r for r in closed if r.get("pnl_usdc") is not None]
        pnl = sum(r["pnl_usdc"] for r in priced)
        wins = sum(1 for r in priced if r["pnl_usdc"] > 0)
        wr = wins / len(priced) * 100 if priced else 0
        grand += pnl
        if leg in CONTROLS: ctrl_pnl += pnl
        else:               sig_pnl  += pnl
        if open_ or priced:
            rows.append([leg, len(open_), len(priced), f"{wr:.0f}%", f"${pnl:+.2f}"])
        for p in open_:
            t = parse_iso(p.get("opened_at", ""))
            if t and t >= cutoff:
                new_opens.append([leg, (p.get("q") or "")[:55], p.get("side", ""),
                                  f"{p.get('entry_fill', 0):.2f}"])
        for r in closed:
            t = parse_iso(r.get("closed_at", ""))
            if t and t >= cutoff:
                new_closes.append([leg, (r.get("q") or "")[:50], r.get("reason", ""),
                                   f"${(r.get('pnl_usdc') or 0):+.2f}"])

    rows.sort(key=lambda r: float(r[4].replace("$", "").replace("+", "")), reverse=True)

    os.makedirs(OUT_DIR, exist_ok=True)
    # Render to a LOCAL temp path first — writing straight into the iCloud
    # folder deadlocks against fileproviderd (OSError 11, seen 2026-06-12).
    tmp_pdf = os.path.join("/tmp", "lab_report_render.pdf")
    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(tmp_pdf, pagesize=A4,
                            topMargin=12*mm, bottomMargin=12*mm,
                            leftMargin=12*mm, rightMargin=12*mm)
    story = [
        Paragraph(f"Polymarket Lab — {now.strftime('%Y-%m-%d %H:%M UTC')}", styles["Title"]),
        Paragraph(f"Grand total: <b>${grand:+.2f}</b> &nbsp; "
                  f"(controls ${ctrl_pnl:+.2f} | signal ${sig_pnl:+.2f}) — PAPER ONLY",
                  styles["Normal"]),
        Spacer(1, 6*mm),
    ]

    def table(data, header, col_widths):
        t = Table([header] + data, colWidths=col_widths)
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f2937")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTSIZE", (0, 0), (-1, -1), 7.5),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d1d5db")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f3f4f6")]),
        ]))
        return t

    if new_opens:
        story += [Paragraph(f"<b>NEW OPENS</b> (last {WINDOW_MIN} min)", styles["Heading3"]),
                  table(new_opens, ["Leg", "Market", "Side", "Fill"],
                        [22*mm, 100*mm, 14*mm, 14*mm]), Spacer(1, 4*mm)]
    if new_closes:
        story += [Paragraph(f"<b>NEW EXITS</b> (last {WINDOW_MIN} min)", styles["Heading3"]),
                  table(new_closes, ["Leg", "Market", "Reason", "P&L"],
                        [22*mm, 92*mm, 24*mm, 16*mm]), Spacer(1, 4*mm)]
    if not new_opens and not new_closes:
        story += [Paragraph(f"No new trades in the last {WINDOW_MIN} minutes.",
                            styles["Normal"]), Spacer(1, 4*mm)]

    story += [Paragraph("<b>PER-LEG P&L</b> (priced exits only)", styles["Heading3"]),
              table(rows, ["Leg", "Open", "Exits", "Win%", "P&L"],
                    [30*mm, 16*mm, 16*mm, 16*mm, 22*mm])]
    doc.build(story)
    # Atomic write into iCloud. open(dst,'wb')+truncate on an already-materialized
    # CloudDocs file raises OSError errno 11 "Resource deadlock avoided" against the
    # file provider (this job crashed ~1000min straight on it). Rename does not:
    # stage a unique temp in the SAME dir, then os.replace.
    import shutil, tempfile
    try:
        fd, stage = tempfile.mkstemp(dir=OUT_DIR, prefix=".labrpt-", suffix=".pdf")
        os.close(fd)
        shutil.copyfile(tmp_pdf, stage)
        os.replace(stage, PDF_PATH)
        print(f"PDF written: {PDF_PATH}  (opens {len(new_opens)}, exits {len(new_closes)})")
    except OSError as e:
        # last resort: unlink the locked dest then copy; never crash the launchd job
        try:
            if os.path.exists(PDF_PATH):
                os.unlink(PDF_PATH)
            shutil.copyfile(tmp_pdf, PDF_PATH)
            print(f"PDF written (after unlink): {PDF_PATH}")
        except OSError as e2:
            print(f"iCloud copy failed ({e2}); local render kept at {tmp_pdf}",
                  file=sys.stderr)
            try:
                os.path.exists(stage) and os.unlink(stage)
            except OSError:
                pass

    # Notify only when there are NEW trades — avoids 144 identical pings per
    # day. The iCloud copy is always refreshed above.
    # 2026-07-03: scripted iMessage ATTACHMENT sends silently drop on this Mac
    # (osascript exit 0, nothing delivered — the old "iMessage sent" lines were
    # lies). Plain TEXT sends work, and the PDF is already phone-viewable via
    # iCloud — so send a TEXT pointing at it instead of a doomed attachment.
    if new_opens or new_closes:
        note = (f"📊 Lab report updated — {len(new_opens)} new opens, "
                f"{len(new_closes)} new exits. PDF: Files app → iCloud Drive → "
                f"PolymarketBot-Lab → Lab Report.pdf")
        for tgt in TARGETS:
            sent = False
            for attempt in (1, 2):   # retry once — Messages can be slow to wake
                try:
                    r = subprocess.run(
                        ["osascript",
                         "-e", "on run argv",
                         "-e", 'tell application "Messages" to send (item 1 of '
                               'argv) to buddy (item 2 of argv) of (service 1 '
                               'whose service type is iMessage)',
                         "-e", "end run",
                         note, tgt],
                        capture_output=True, text=True, timeout=15)
                    if r.returncode == 0:
                        sent = True
                        print(f"iMessage text sent to {tgt} (attempt {attempt})")
                        break
                    print(f"iMessage FAILED ({tgt}, attempt {attempt}): "
                          f"{r.stderr.strip()}", file=sys.stderr)
                except Exception as e:
                    print(f"iMessage error ({tgt}, attempt {attempt}): {e}",
                          file=sys.stderr)
                time.sleep(5)
            if not sent:
                print(f"iMessage GAVE UP for {tgt} — check Messages sign-in",
                      file=sys.stderr)


if __name__ == "__main__":
    main()
