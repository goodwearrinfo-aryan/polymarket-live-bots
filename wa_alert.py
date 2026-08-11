#!/usr/bin/env python3
"""wa_alert.py — send bot alerts to WhatsApp via the local OpenWA gateway.

Fail-soft by design: if the gateway is down, the session is unlinked, or the
network hiccups, send_whatsapp() returns (False, reason) and NEVER raises — a
broken alert channel must never take down the bot (Lesson: silence ≠ success,
but a crash is worse). Pairs with the existing iMessage path; use notify() to
hit both.

Config: wa_config.json (non-secret) + the API key from
~/Library/Application Support/PolymarketBot/openwa.key (chmod 600, NOT synced)
or the WA_API_KEY env var. Key is NEVER read from or written to the synced repo.

  python3 wa_alert.py "message"                 # send to default recipient
  python3 wa_alert.py "message" 9199xxxxxxxx     # send to a specific number
  python3 wa_alert.py --health                   # gateway + session link status
  python3 wa_alert.py --selftest                 # send a test ping
"""
import json, os, sys, urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(BASE, "wa_config.json")
KEY_FILE = os.path.expanduser("~/Library/Application Support/PolymarketBot/openwa.key")


def _cfg():
    with open(CONFIG) as f:
        return json.load(f)


def _api_key():
    k = os.environ.get("WA_API_KEY")
    if k:
        return k.strip()
    try:
        with open(KEY_FILE) as f:
            return f.read().strip()
    except Exception:
        return ""


def _req(method, path, body=None, timeout=12):
    cfg = _cfg()
    url = cfg["base_url"].rstrip("/") + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Content-Type": "application/json",
        "X-API-Key": _api_key(),
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def health():
    """(linked_bool, detail_dict). Never raises."""
    try:
        cfg = _cfg()
        s = _req("GET", f"/api/sessions/{cfg['session_id']}")
        linked = s.get("status") in ("connected", "authenticated") or bool(s.get("phone"))
        return linked, {"status": s.get("status"), "phone": s.get("phone"),
                        "name": s.get("pushName")}
    except Exception as e:
        return False, {"error": f"{type(e).__name__}: {e}"}


def send_whatsapp(text, recipient=None):
    """(ok_bool, detail). Fail-soft — returns (False, reason) instead of raising."""
    try:
        cfg = _cfg()
        chat = recipient or cfg["recipient"]
        if "@" not in chat:
            chat = chat.lstrip("+").replace(" ", "") + "@c.us"
        out = _req("POST", f"/api/sessions/{cfg['session_id']}/messages/send-text",
                   {"chatId": chat, "text": text})
        ok = bool(out.get("id") or out.get("messageId") or out.get("success"))
        return ok, out
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def notify(text, recipient=None, also_imessage=True):
    """Send to WhatsApp (and optionally iMessage). Returns dict of channel results.
    Both channels fail-soft and independent — one down never blocks the other."""
    res = {}
    ok, detail = send_whatsapp(text, recipient)
    res["whatsapp"] = {"ok": ok, "detail": detail}
    if also_imessage:
        res["imessage"] = {"ok": _imessage(text)}
    return res


def _imessage(text, targets=("krisharyan@icloud.com", "+918449447444")):
    """Fail-soft iMessage via osascript (argv-passed; never f-string the body).
    Targets BOTH handles so basket-lock alerts reach phone+email even when the OpenWA
    WhatsApp gateway is down (verified down 2026-06-21 — this path was email-only before)."""
    import subprocess
    osa = ('on run argv\ntell application "Messages"\n'
           'set s to 1st service whose service type = iMessage\n'
           'send (item 2 of argv) to buddy (item 1 of argv) of s\nend tell\nend run')
    ok = True
    for t in targets:
        try:
            r = subprocess.run(["osascript", "-e", osa, t, text],
                               capture_output=True, text=True, timeout=30)
            ok = ok and r.returncode == 0
        except Exception:
            ok = False
    return ok


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__); return
    if args[0] == "--health":
        linked, d = health()
        print(json.dumps({"linked": linked, **d}, indent=2))
        sys.exit(0 if linked else 1)
    if args[0] == "--selftest":
        linked, d = health()
        if not linked:
            print(json.dumps({"linked": False, "skipped_send": True, **d}, indent=2))
            sys.exit(1)
        ok, detail = send_whatsapp("✅ OpenWA self-test from the Polymarket bot — channel live.")
        print(json.dumps({"sent": ok, "detail": detail}, indent=2))
        sys.exit(0 if ok else 1)
    text = args[0]
    recipient = args[1] if len(args) > 1 else None
    ok, detail = send_whatsapp(text, recipient)
    print(json.dumps({"sent": ok, "detail": detail}, indent=2))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
