#!/usr/bin/env python3
"""
lock_server.py — tiny read-only HTTP endpoint exposing recent basket-lock events.

Runs on the VPS next to ws_basket_watch.py. The Mac relay (behind home NAT, can't
receive a POST) POLLS this outbound. Token-gated, read-only.

PAPER / DETECTION ONLY — it serves an event log, it never trades.

  python3 lock_server.py                 # serve on 0.0.0.0:<port>
Config (relay_config.json): {"token": "...", "port": 8123}
Env override for testing: LOCK_LOG=/path/to/events.jsonl  LOCK_PORT=NNNN
"""
import json, os, time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs
from pathlib import Path

BASE = Path(__file__).resolve().parent
CFG = BASE / "relay_config.json"


def _cfg():
    try:
        return json.loads(CFG.read_text())
    except Exception:
        return {}


LOG = Path(os.environ.get("LOCK_LOG", BASE / "basket_locks.jsonl"))
TOKEN = _cfg().get("token", "changeme")
PORT = int(os.environ.get("LOCK_PORT", _cfg().get("port", 8123)))


def recent(n=300):
    if not LOG.exists():
        return []
    out = []
    for line in LOG.read_text().splitlines()[-n:]:
        try:
            out.append(json.loads(line))
        except Exception:
            pass
    return out


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path != "/events" or q.get("token", [""])[0] != TOKEN:
            self.send_response(403)
            self.end_headers()
            self.wfile.write(b"forbidden")
            return
        since = float(q.get("since", ["0"])[0] or 0)
        ev = [e for e in recent() if float(e.get("ts", 0)) > since]
        body = json.dumps({"events": ev, "now": time.time()}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    print(f"[lock_server] serving {LOG.name} on :{PORT} (token-gated)", flush=True)
    HTTPServer(("0.0.0.0", PORT), H).serve_forever()
