#!/usr/bin/env python3
"""
lock_relay.py — Mac-side relay: poll the VPS lock_server, fire WhatsApp+iMessage.

The VPS detects locks fast but the Mac (home NAT, sleeps when idle) can't receive
a push — so the Mac POLLS the VPS outbound and relays new locks through your
existing channels (wa_alert: WhatsApp + iMessage).

PAPER / DETECTION ONLY — it relays a notification; it never trades.

  python3 lock_relay.py            # one poll (launchd StartInterval)
  python3 lock_relay.py --loop     # continuous (KeepAlive daemon)
Config (relay_config.json): {"vps_url": "http://VPS_IP:8123", "token": "..."}
"""
import json, os, sys, time, urllib.request, urllib.parse
from pathlib import Path

try:
    import wa_alert
except Exception:
    wa_alert = None

BASE = Path(__file__).resolve().parent
CFG = BASE / "relay_config.json"
STATE = BASE / "lock_relay_state.json"


def _cfg():
    try:
        return json.loads(CFG.read_text())
    except Exception:
        return {}


def _last_ts():
    try:
        return float(json.loads(STATE.read_text()).get("since", 0))
    except Exception:
        return 0.0


def _save_ts(t):
    try:
        STATE.write_text(json.dumps({"since": t}))
    except Exception:
        pass


def _notify(text):
    if wa_alert:
        try:
            wa_alert.notify(text)
            return
        except Exception as e:
            print(f"  notify failed: {e}")
    print("  [alert] " + text)


def poll_once():
    c = _cfg()
    url = c.get("vps_url")
    if not url:
        print("[relay] no vps_url in relay_config.json — idle (set it after the VPS is up)")
        return
    since = _last_ts()
    q = urllib.parse.urlencode({"token": c.get("token", ""), "since": since})
    try:
        d = json.loads(urllib.request.urlopen(f"{url}/events?{q}", timeout=10).read())
    except Exception as e:
        print(f"[relay] poll failed: {e}")
        return
    ev = d.get("events", [])
    mx = since
    for e in ev:
        ts = float(e.get("ts", 0))
        mx = max(mx, ts)
        edge = e.get("edge", 0)
        _notify(f"⚡ [VPS] basket lock: {edge*100:.1f}% — {e.get('slug', '')} "
                f"(n={e.get('n', '?')}, Σask {e.get('sum_ask', '?')}). Paper/detection only.")
        print(f"  relayed: {e.get('slug')}")
    if mx > since:
        _save_ts(mx)
    print(f"[relay] {len(ev)} new event(s) since {since:.0f}")


def main():
    if "--loop" in sys.argv:
        interval = int(os.environ.get("RELAY_POLL", "8"))
        while True:
            poll_once()
            time.sleep(interval)
    else:
        poll_once()


if __name__ == "__main__":
    main()
