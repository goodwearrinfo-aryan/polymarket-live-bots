#!/usr/bin/env python3
"""
lab_api.py — read-only REST API over the scalp lab state.
GET-only by design: no mutation endpoint exists (paper-only by construction).

  GET /v1/status               summary: P&L since reset, gate, controls, counts
  GET /v1/legs                 stats for every focus leg (?all=1 for the full lab)
  GET /v1/legs/{name}          one leg: stats + open positions
  GET /v1/legs/{name}/exits    recent priced exits (?limit=50, max 200)
  GET /v1/health               watchdog/mirror/process health

Serves on 127.0.0.1:8787 (localhost only — set LAB_API_LAN=1 to bind the LAN
for phone access; do that only deliberately). Data is the state MIRROR
(scalp_lab_state.json), reloaded per request, so always current.
"""
import json, os, subprocess, time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

BASE  = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(BASE, "scalp_lab_state.json")
BASEF = os.path.join(BASE, "pnl_baseline.json")
PORT  = 8787

FOCUS = ["nearres", "sportres", "nearreslow", "nearresfade", "ytbuzz",
         "nohappen", "longshortbias", "clobimbal", "polyflup", "noevent",
         "newsno", "btc15no", "weatherno", "fogbuy", "crashbuy", "polvol",
         "latefade", "vlrtop", "gtrend", "bookpress", "kellyfav", "newslag",
         "midfield", "candlesig"]
CONTROLS = ["allin", "coinflip"]


def load():
    state = json.load(open(STATE))
    try:
        base = json.load(open(BASEF)).get("baseline", {})
    except Exception:
        base = {}
    return state, base


def leg_stats(leg, book, base):
    pr = [r for r in book.get("closed", []) if r.get("pnl_usdc") is not None]
    w  = [r for r in pr if r["pnl_usdc"] > 0]
    l  = [r for r in pr if r["pnl_usdc"] < 0]
    aw = sum(r["pnl_usdc"] for r in w) / len(w) if w else 0.0
    al = abs(sum(r["pnl_usdc"] for r in l) / len(l)) if l else 0.0
    return {
        "leg": leg,
        "exits": len(pr),
        "open": len(book.get("open", [])),
        "win_rate_pct": round(len(w) / len(pr) * 100, 1) if pr else None,
        "rr": round(aw / al, 2) if al else None,
        "pnl_since_reset": round(sum(r["pnl_usdc"] for r in pr) - base.get(leg, 0.0), 4),
        "pnl_lifetime": round(sum(r["pnl_usdc"] for r in pr), 4),
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "lab-api/1.0"

    def _send(self, code, payload):
        body = json.dumps(payload, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _err(self, code, err_code, message):
        self._send(code, {"error": {"code": err_code, "message": message}})

    def log_message(self, fmt, *args):  # quiet access log
        pass

    def do_GET(self):
        try:
            u = urlparse(self.path)
            parts = [p for p in u.path.split("/") if p]
            qs = parse_qs(u.query)

            if parts[:1] != ["v1"]:
                return self._err(404, "NOT_FOUND",
                                 "Unknown route. Try /v1/status, /v1/legs, /v1/health")
            route = parts[1:]

            if route == ["status"]:
                return self._send(200, self.r_status())
            if route == ["health"]:
                return self._send(200, self.r_health())
            if route == ["legs"]:
                return self._send(200, self.r_legs(all_legs="all" in qs))
            if len(route) == 2 and route[0] == "legs":
                return self.r_leg(route[1])
            if len(route) == 3 and route[0] == "legs" and route[2] == "exits":
                raw = qs.get("limit", ["50"])[0]
                if not raw.isdigit():
                    return self._err(400, "BAD_LIMIT",
                                     "limit must be a positive integer")
                return self.r_exits(route[1], min(int(raw), 200))
            return self._err(404, "NOT_FOUND", f"Unknown route: {u.path}")
        except Exception as e:
            return self._err(500, "INTERNAL", str(e))

    # ── route handlers ────────────────────────────────────────────────────
    def r_status(self):
        state, base = load()
        age = time.time() - os.path.getmtime(STATE)
        stats = [leg_stats(l, state[l], base) for l in FOCUS if l in state]
        ctrl = sum(r["pnl_usdc"] for lg in CONTROLS
                   for r in state.get(lg, {}).get("closed", [])
                   if r.get("pnl_usdc") is not None)
        nr = next((s for s in stats if s["leg"] == "nearres"), {})
        return {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "mirror_age_sec": round(age),
            "signal_pnl_since_reset": round(sum(s["pnl_since_reset"] for s in stats), 2),
            "open_positions": sum(s["open"] for s in stats),
            "nearres_gate": {"exits": nr.get("exits", 0), "needed": 30,
                             "win_rate_pct": nr.get("win_rate_pct")},
            "controls_lifetime_pnl": round(ctrl, 2),
            "controls_honest": ctrl < 0,
        }

    def r_health(self):
        age = time.time() - os.path.getmtime(STATE)
        wd = subprocess.run(["pgrep", "-f", "watchdog_loop.sh"],
                            capture_output=True).returncode == 0
        stray = subprocess.run(["pgrep", "-f", "scalp_lab.py run"],
                               capture_output=True).returncode == 0
        ok = wd and not stray and age < 300
        return {"ok": ok, "watchdog_running": wd, "stray_run_bots": stray,
                "mirror_age_sec": round(age)}

    def r_legs(self, all_legs=False):
        state, base = load()
        legs = sorted(state) if all_legs else FOCUS
        data = [leg_stats(l, state[l], base) for l in legs
                if l in state and not l.endswith("_misfire")]
        return {"data": [s for s in data if s["exits"] or s["open"]]}

    def r_leg(self, name):
        state, base = load()
        if name not in state:
            return self._err(404, "LEG_NOT_FOUND", f"No leg named '{name}'")
        s = leg_stats(name, state[name], base)
        s["open_positions"] = state[name].get("open", [])
        return self._send(200, s)

    def r_exits(self, name, limit):
        state, _ = load()
        if name not in state:
            return self._err(404, "LEG_NOT_FOUND", f"No leg named '{name}'")
        pr = [r for r in state[name].get("closed", [])
              if r.get("pnl_usdc") is not None]
        pr.sort(key=lambda r: str(r.get("closed_at", "")), reverse=True)
        return self._send(200, {"data": pr[:limit],
                                "total": len(pr), "limit": limit})


if __name__ == "__main__":
    import sys, time, errno
    host = "0.0.0.0" if os.environ.get("LAB_API_LAN") == "1" else "127.0.0.1"
    # Bind-retry: under launchd (KeepAlive) a transient holder — a dying sibling
    # instance, a TIME_WAIT socket, or an overlapping start — made the first bind
    # fail EADDRINUSE and the job exit-78-loop forever. Wait it out instead.
    ThreadingHTTPServer.allow_reuse_address = True
    srv = None
    for attempt in range(15):
        try:
            srv = ThreadingHTTPServer((host, PORT), Handler)
            break
        except OSError as e:
            if getattr(e, "errno", None) == errno.EADDRINUSE:
                sys.stderr.write(f"[lab-api] {host}:{PORT} busy (try {attempt+1}/15) — retry 3s\n")
                sys.stderr.flush()
                time.sleep(3)
                continue
            raise
    if srv is None:
        sys.stderr.write(f"[lab-api] could not bind {host}:{PORT} after 15 tries — exiting\n")
        sys.exit(1)
    print(f"lab-api listening on {host}:{PORT} (GET-only, read-only)")
    srv.serve_forever()
