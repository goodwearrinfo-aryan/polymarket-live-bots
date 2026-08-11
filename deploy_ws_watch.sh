#!/usr/bin/env bash
# deploy_ws_watch.sh — install the real-time basket-arb watcher as a SEPARATE
# systemd service on this VPS. Independent of cloud_bootstrap.sh (the copy bot).
#
# PAPER / DETECTION ONLY. No orders, no keys, no funds — read-only market data.
#
# On the VPS (after you have the code there):  sudo bash deploy_ws_watch.sh
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
PY="$(command -v python3)"

echo "[1/4] SPEED CHECK — measuring exchange->this-box latency (the only real speed lever)..."
"$PY" - <<'PYEOF'
import json, time, threading, urllib.request, sys, subprocess
try:
    import websocket
except Exception:
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "websocket-client"])
    import websocket
try:
    rows = json.loads(urllib.request.urlopen(
        "https://gamma-api.polymarket.com/markets?closed=false&limit=20", timeout=15).read())
except Exception as e:
    print(f"   could not reach gamma ({e}) — is this region blocked? proceeding anyway"); raise SystemExit(0)
toks = []
for m in rows:
    try:
        toks += [str(x) for x in json.loads(m.get("clobTokenIds") or "[]")]
    except Exception:
        pass
toks = toks[:40]
lat = []
def oo(ws): ws.send(json.dumps({"assets_ids": toks, "type": "market"}))
def om(ws, msg):
    now = time.time() * 1000
    try: d = json.loads(msg)
    except Exception: return
    for e in (d if isinstance(d, list) else [d]):
        ts = e.get("timestamp")
        if ts:
            try:
                l = now - int(ts)
                if -5000 < l < 60000: lat.append(l)
            except Exception: pass
ws = websocket.WebSocketApp("wss://ws-subscriptions-clob.polymarket.com/ws/market",
                            on_open=oo, on_message=om)
threading.Thread(target=ws.run_forever, daemon=True).start()
time.sleep(8)
try: ws.close()
except Exception: pass
if lat:
    lat.sort(); med = lat[len(lat)//2]
    verdict = "GOOD (<30ms)" if med < 30 else ("OK (<80ms)" if med < 80 else "SLOW — try a region closer to Polymarket's servers")
    print(f"   median exchange->box latency: {med:.0f} ms  (n={len(lat)})  -> {verdict}")
else:
    print("   could not measure latency (no timestamped events) — proceeding")
PYEOF

echo "[2/4] deps (websocket-client)..."
"$PY" -m pip install -q --upgrade websocket-client

echo "[3/4] installing SEPARATE systemd services (ws-basket-watch + lock-server)..."
for svc in ws-basket-watch lock-server; do
  sed "s#/root/polymarket#$DIR#g; s#/usr/bin/python3#$PY#g" \
      "$DIR/$svc.service" > "/etc/systemd/system/$svc.service"
done
systemctl daemon-reload
systemctl enable ws-basket-watch lock-server >/dev/null 2>&1 || true
systemctl restart ws-basket-watch lock-server

echo "[4/4] status:"
sleep 5
systemctl --no-pager status ws-basket-watch lock-server | head -20 || true
echo ""
echo "DONE — both SEPARATE from the copy bot (polymarket-bot.service)."
echo "  detect log: journalctl -u ws-basket-watch -f"
echo "  serve log:  journalctl -u lock-server -f   (Mac relay polls this)"
echo "  NEXT on the Mac: set vps_url + token in relay_config.json, then load com.aryan.lock-relay."
echo "  Open the lock_server port to YOUR Mac's IP only (ufw allow from <mac_ip> to any port \$(python3 -c \"import json;print(json.load(open('relay_config.json')).get('port',8123))\")). token also gates it."
echo "  alerts on VPS itself: Gmail App Password in $DIR/.smtp_creds.json (WhatsApp/iMessage are Mac-only)."
echo "  PAPER/DETECTION ONLY — it tells you where the lock is; it never trades."
