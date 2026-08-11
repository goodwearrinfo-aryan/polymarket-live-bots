#!/usr/bin/env python3
"""send_report_imessage.py — regenerate both PDFs with live data and deliver them.
Run daily. Safe to call any time (no real orders, paper only).

2026-07-03 rewrite: scripted iMessage ATTACHMENT sends are broken on this Mac
(macOS Ventura+ AppleScript regression — osascript returns 0 but Messages
silently drops the file; user-confirmed, every form tried: buddy-of-service,
chat id, /tmp-staged copy). This script's old log lines said "sent" on exit
code alone — a lie. Plain iMessage TEXT still works.

Delivery now:
  1. EMAIL both PDFs to krisharyan@icloud.com via Mail.app AppleScript,
     then VERIFY the message actually landed in a Sent mailbox with
     attachment count >= 2. Only that verification counts as "sent".
  2. iMessage TEXT caption to both targets (works) pointing at the email.
  3. Copy both PDFs to the iCloud Polymarket-Feed folder (phone Files app).

Usage: python3 send_report_imessage.py
"""
import os, sys, shutil, subprocess, datetime, time

HERE       = os.path.dirname(os.path.abspath(__file__))
EMAIL_TO   = "krisharyan@icloud.com"
TEXT_TARGETS = ["krisharyan@icloud.com", "+918449447444"]
PDFS       = [
    os.path.join(HERE, "polymarket_bot_report.pdf"),
    os.path.join(HERE, "polymarket_status.pdf"),
]
FEED_DIR   = os.path.expanduser(
    "~/Library/Mobile Documents/com~apple~CloudDocs/Polymarket-Feed")


def run(script, label):
    # bot_report.py chains 3 sub-scripts at 90s each (sequential) — 120s could
    # clip it if one runs slow (observed: gate_watcher.py alone took 60-90s).
    r = subprocess.run([sys.executable, os.path.join(HERE, script)],
                       capture_output=True, text=True, timeout=300)
    print(f"[{label}] {'OK' if r.returncode == 0 else 'FAIL'}")
    if r.stdout.strip(): print(r.stdout.strip())
    if r.stderr.strip(): print(r.stderr.strip(), file=sys.stderr)
    return r.returncode == 0


def osascript(script, timeout=60):
    # Fail-soft on timeout: under launchd, scripting a COLD Mail.app can exceed
    # the timeout, and an uncaught TimeoutExpired would crash the whole job
    # BEFORE the iMessage-text/iCloud fallback runs (observed: the 09:00
    # scheduled run delivered nothing on a 60s Mail-send timeout). Return a
    # failure tuple instead so the caller degrades gracefully.
    try:
        r = subprocess.run(["osascript", "-e", script],
                           capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, "", f"osascript timed out after {timeout}s"
    return r.returncode == 0, (r.stdout or "").strip(), (r.stderr or "").strip()


def send_text_imessage(text, target):
    """Plain TEXT via iMessage — this path works (attachments do not)."""
    try:
        r = subprocess.run(
            ["osascript",
             "-e", "on run argv",
             "-e", 'tell application "Messages" to send (item 1 of argv) to buddy '
                   '(item 2 of argv) of (service 1 whose service type is iMessage)',
             "-e", "end run",
             text, target],
            capture_output=True, text=True, timeout=45)
    except subprocess.TimeoutExpired:
        return False
    return r.returncode == 0


def email_pdfs(subject, body, paths):
    """Send one email with all PDFs attached via Mail.app, staged in /tmp
    (avoids any ~/Documents TCC ambiguity for GUI apps)."""
    staged = []
    for p in paths:
        dst = os.path.join("/tmp", os.path.basename(p))
        shutil.copyfile(p, dst)
        staged.append(dst)
    attach_lines = "\n".join(
        f'    make new attachment with properties {{file name:(POSIX file "{p}")}} '
        f"at after the last paragraph" for p in staged)
    # 150s (not the 60s default): a cold Mail.app launch under launchd + the
    # attachment compose + delay + send routinely exceeds 60s on the first
    # scheduled run of the day. fail-soft in osascript() still guards a genuine
    # hang.
    ok, out, err = osascript(f'''
tell application "Mail"
  set msg to make new outgoing message with properties {{subject:"{subject}", content:"{body}
", visible:false}}
  tell msg
    make new to recipient at end of to recipients with properties {{address:"{EMAIL_TO}"}}
{attach_lines}
  end tell
  delay 2
  send msg
end tell
''', timeout=150)
    if not ok:
        print(f"  email send FAILED: {err}", file=sys.stderr)
    return ok


def verify_email_in_sent(subject, min_attachments, wait_s=60):
    """Poll the Sent mailboxes for the subject; require attachment count.
    This is the ONLY thing that counts as delivery — never the exit code."""
    deadline = time.time() + wait_s
    script = f'''
tell application "Mail"
  set found to "NO"
  repeat with acct in accounts
    repeat with mb in mailboxes of acct
      try
        if name of mb contains "Sent" then
          repeat with m in (messages 1 thru 5 of mb)
            try
              if subject of m contains "{subject}" then
                set found to "YES:" & (count of mail attachments of m)
              end if
            end try
          end repeat
        end if
      end try
    end repeat
  end repeat
  return found
end tell
'''
    while time.time() < deadline:
        time.sleep(10)
        ok, out, _ = osascript(script)
        if ok and out.startswith("YES:"):
            n = int(out.split(":", 1)[1] or 0)
            if n >= min_attachments:
                return True, n
    return False, 0


def main():
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"=== Daily report send — {now} ===")

    # 1. Run one bot cycle so data is fresh
    subprocess.run([sys.executable, os.path.join(HERE, "scalp_lab.py"), "once"],
                   capture_output=True, timeout=120)
    print("[scalp_lab] cycle done")

    # 2. Regenerate both PDFs
    ok1 = run("bot_report.py",       "bot_report")
    ok2 = run("build_status_pdf.py", "status_pdf")

    if not (ok1 and ok2):
        print("PDF generation failed — aborting send", file=sys.stderr)
        sys.exit(1)

    missing = [p for p in PDFS if not os.path.exists(p)]
    if missing:
        print(f"MISSING: {missing}", file=sys.stderr)
        sys.exit(1)

    errors = []

    # 3. iCloud feed copies (phone Files app — no push, always works)
    try:
        os.makedirs(FEED_DIR, exist_ok=True)
        for p in PDFS:
            shutil.copyfile(p, os.path.join(FEED_DIR, os.path.basename(p)))
        print(f"  iCloud feed refreshed ({len(PDFS)} PDFs)")
    except Exception as e:
        print(f"  iCloud feed copy failed: {e}", file=sys.stderr)
        errors.append("icloud-feed")

    # 4. Email both PDFs, then VERIFY in Sent (the only real proof)
    subject = f"Polymarket daily reports — {now} (paper)"
    sent_ok = False
    if email_pdfs(subject,
                  "Daily bot report + status PDFs attached. Paper only. "
                  "(iMessage attachments are broken on this Mac; email is the "
                  "verified channel. Copies also in iCloud Polymarket-Feed.)",
                  PDFS):
        sent_ok, n = verify_email_in_sent(subject, min_attachments=len(PDFS))
        if sent_ok:
            print(f"  email VERIFIED in Sent ({n} attachments) → {EMAIL_TO}")
        else:
            print("  email NOT verified in Sent within 60s", file=sys.stderr)
            errors.append("email-verify")
    else:
        errors.append("email-send")

    # 5. iMessage TEXT caption to both targets (text works; attachments don't)
    caption = (f"📄 Polymarket daily reports {now} — "
               f"{'PDFs emailed to ' + EMAIL_TO + ' (verified) ' if sent_ok else '⚠️ EMAIL DELIVERY FAILED '}"
               f"+ copies in iCloud Files → Polymarket-Feed. Paper only.")
    for target in TEXT_TARGETS:
        ok = send_text_imessage(caption, target)
        print(f"  caption text → {target}: {'sent' if ok else 'FAILED'}")
        if not ok:
            errors.append(f"text → {target}")

    if errors:
        print(f"\nErrors: {errors}", file=sys.stderr)
        sys.exit(1)
    print("All reports delivered (email verified in Sent + iCloud + captions).")


if __name__ == "__main__":
    main()
