#!/usr/bin/env python3
"""send_mail.py — actually SEND the report by email via Gmail SMTP, in the
background (the Gmail MCP connector can only DRAFT; SMTP can send AND attach).

Reads credentials from .smtp_creds.json which YOU create — Claude is not allowed
to type your password, so it leaves a placeholder for you to fill. Safe no-op if
the creds file is missing or still has the placeholder, so the watchdog can call
it every day harmlessly until you add the password.

Usage: python3 send_mail.py [pdf_path] [subject]
"""
import os, sys, json, ssl, smtplib, subprocess, datetime
from email.message import EmailMessage

HERE = os.path.dirname(os.path.abspath(__file__))
CREDS = os.path.join(HERE, ".smtp_creds.json")
DEFAULT_PDF = os.path.join(HERE, "polymarket_bot_report.pdf")


def load_creds():
    if not os.path.exists(CREDS):
        return None, "no .smtp_creds.json (create it with your Gmail App Password)"
    try:
        c = json.load(open(CREDS))
    except Exception as e:
        return None, f"bad creds json: {e}"
    pw = (c.get("app_password") or "").replace(" ", "")
    if not pw or "PASTE" in pw.upper():
        return None, "app_password placeholder not replaced yet"
    c["app_password"] = pw
    c.setdefault("to", c.get("user"))
    return c, None


def body_text():
    try:
        r = subprocess.run(["python3", os.path.join(HERE, "honest_report.py")],
                           capture_output=True, text=True, timeout=90)
        rep = r.stdout.strip() or "(honest_report produced no output)"
    except Exception as e:
        rep = f"(honest_report.py failed: {e})"
    return ("Polymarket paper bot — automated background send. PAPER ONLY, no real money.\n"
            "Live realized P&L (closed-exit trades, honest-capped) below; full report attached.\n\n"
            + rep)


def main():
    pdf = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PDF
    subject = sys.argv[2] if len(sys.argv) > 2 else \
        f"Polymarket bot — auto report {datetime.datetime.now():%Y-%m-%d %H:%M}"
    c, err = load_creds()
    if err:
        print(f"[send_mail] SKIPPED ({err})")
        return 0  # no-op, not an error — lets the watchdog call it safely

    msg = EmailMessage()
    msg["From"] = c["user"]; msg["To"] = c["to"]; msg["Subject"] = subject
    msg.set_content(body_text())
    if os.path.exists(pdf):
        with open(pdf, "rb") as f:
            msg.add_attachment(f.read(), maintype="application", subtype="pdf",
                               filename=os.path.basename(pdf))
    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as s:
            s.starttls(context=ctx)
            s.login(c["user"], c["app_password"])
            s.send_message(msg)
        att = os.path.basename(pdf) if os.path.exists(pdf) else "(no pdf)"
        print(f"[send_mail] SENT to {c['to']} | subject={subject!r} | attached={att}")
        return 0
    except smtplib.SMTPAuthenticationError:
        print("[send_mail] AUTH FAILED — the App Password is wrong/expired, or 2FA isn't on. "
              "Regenerate at myaccount.google.com -> Security -> App passwords.")
        return 1
    except Exception as e:
        print(f"[send_mail] send failed: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
