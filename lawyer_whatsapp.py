#!/usr/bin/env python3
"""
WhatsApp bot for the lawyer fleet.
Connects to existing OpenWA on :2785.
Routes WhatsApp messages to the fleet agents.
"""
import urllib.request, json, time, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

OWA_URL = "http://localhost:2785"
FLEET_URL = "http://localhost:8766"
UA = "Mozilla/5.0"
POLL_INTERVAL = 10  # seconds
LOG = "/tmp/lawyer_whatsapp.log"

WELCOME = """👋 *Indian Legal Assistant* ⚖️

I can help with:
1️⃣ Legal Q&A (any Indian language)
2️⃣ Draft legal notice
3️⃣ RTI application
4️⃣ Limitation/deadline check
5️⃣ Court locator
6️⃣ Tax notice decode
7️⃣ Labour rights
8️⃣ FIR guide
9️⃣ Property / RERA
🔟 Divorce / family law

Type your question in any language, or type a number to get started.
_Free: 10 queries/day • More: visit http://localhost:8766_"""

COMMAND_MAP = {
    "1": "chat", "2": "notice", "3": "rti", "4": "deadline",
    "5": "court", "6": "tax", "7": "labour", "8": "fir",
    "9": "property", "10": "divorce", "0": "chat"
}

def _req(url, data=None, method="GET"):
    headers = {"User-Agent": UA, "Content-Type": "application/json"}
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)

def send_msg(to, text):
    try:
        _req(f"{OWA_URL}/api/sendText", {"to": to, "text": text[:4000]}, "POST")
    except Exception as e:
        log(f"Send failed to {to}: {e}")

def fleet_query(agent, payload):
    try:
        data = json.dumps(payload).encode()
        req = urllib.request.Request(f"{FLEET_URL}/fleet/{agent}", data=data,
              headers={"Content-Type": "application/json", "User-Agent": UA}, method="POST")
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except Exception as e:
        return {"ok": False, "error": str(e)}

def handle_message(sender, text, uid):
    text = text.strip()

    # Welcome
    if text.lower() in ["hi", "hello", "help", "start", "/start", "menu"]:
        return WELCOME

    # Number command
    if text in COMMAND_MAP:
        agent = COMMAND_MAP[text]
        if agent == "chat":
            return "Please type your legal question and I'll answer it."
        return f"Please describe your {agent} situation and I'll help."

    # Route to fleet/chat (auto-detect intent)
    r = fleet_query("chat", {"uid": uid, "question": text})
    if not r.get("ok"):
        return f"⚠️ {r.get('error', 'Error')}\n\nType *menu* to see all services."

    answer = r.get("answer", r.get("draft", r.get("analysis", "No answer")))
    lang = r.get("detected_lang", "")
    lang_note = f"\n_(Detected: {lang})_" if lang and lang != "english" else ""
    return f"{answer[:3500]}{lang_note}\n\n_⚖️ AI guidance only. Consult a lawyer for your case._"

def log(msg):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG, "a") as f:
        f.write(line + "\n")

def check_owa():
    try:
        _req(f"{OWA_URL}/api/isConnected")
        return True
    except:
        return False

def run():
    log("WhatsApp lawyer bot starting...")
    if not check_owa():
        log("OpenWA not running on :2785. Start it first.")
        return

    seen_ids = set()
    log("Polling for messages...")

    while True:
        try:
            msgs = _req(f"{OWA_URL}/api/getMessages?count=10")
            for msg in (msgs if isinstance(msgs, list) else []):
                msg_id = msg.get("id", {}).get("id", "") if isinstance(msg.get("id"), dict) else str(msg.get("id",""))
                if msg_id in seen_ids:
                    continue
                seen_ids.add(msg_id)

                # Skip own messages
                if msg.get("fromMe"):
                    continue

                sender = msg.get("from", "")
                body = msg.get("body", "").strip()
                if not body or not sender:
                    continue

                uid = "wa_" + sender.replace("@c.us","").replace("@g.us","")
                log(f"MSG from {sender}: {body[:50]}")

                reply = handle_message(sender, body, uid)
                send_msg(sender, reply)
                log(f"Replied to {sender}: {reply[:50]}")

        except Exception as e:
            log(f"Poll error: {e}")

        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    run()
