#!/usr/bin/env python3
"""serve_mobile.py — read-only LAN web view of the bot for an Android (or any) phone.

Serves ONLY ~/Documents/polymarket/mobile_view/ (curated safe artifacts: the
self-contained dashboard HTML + report PDFs). NEVER the project dir, so .env,
keys, wallets, and source are not exposed. Bind 0.0.0.0 so a phone on the SAME
WiFi can open http://<mac-lan-ip>:PORT/. Paper-only research; LAN, no auth —
fine on a home network, do not run on untrusted WiFi.

Usage:  python3 serve_mobile.py            # port 8800
        python3 serve_mobile.py --port 8810
"""
import os, argparse, http.server, socketserver, socket

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "mobile_view")


def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80)); ip = s.getsockname()[0]; s.close()
        return ip
    except Exception:
        return "127.0.0.1"


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=ROOT, **k)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")   # always show the freshest report
        super().end_headers()

    def log_message(self, fmt, *args):
        pass   # quiet


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8800)
    a = ap.parse_args()
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer(("0.0.0.0", a.port), Handler) as srv:
        ip = lan_ip()
        print(f"serving {ROOT}")
        print(f"  on this Mac:   http://127.0.0.1:{a.port}/")
        print(f"  on Android:    http://{ip}:{a.port}/    (same WiFi)")
        srv.serve_forever()
