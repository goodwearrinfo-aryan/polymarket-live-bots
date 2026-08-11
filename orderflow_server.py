#!/usr/bin/env python3
"""
orderflow_server.py — Live order flow dashboard on :8090.

Background thread refreshes all 8 coins every 30s (Binance public REST, no key).
GET /      → dashboard HTML
GET /api   → JSON payload

Usage:  python3 orderflow_server.py
Then open:  http://localhost:8090
"""
import json, os, sys, time, threading, concurrent.futures, urllib.request
from http.server import HTTPServer, BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import candle_signals as cs
import orderflow_signals as ofs

PORT = 8090
REFRESH = 30        # seconds between data refreshes
UA = {"User-Agent": "Mozilla/5.0"}

COINS = [
    ("bitcoin",     "BTCUSDT",  "BTC"),
    ("ethereum",    "ETHUSDT",  "ETH"),
    ("solana",      "SOLUSDT",  "SOL"),
    ("binancecoin", "BNBUSDT",  "BNB"),
    ("ripple",      "XRPUSDT",  "XRP"),
    ("dogecoin",    "DOGEUSDT", "DOGE"),
    ("cardano",     "ADAUSDT",  "ADA"),
    ("avalanche-2", "AVAXUSDT", "AVAX"),
]


# ── data layer ─────────────────────────────────────────────────────────────────

def _klines(sym, limit=500):
    url = (f"https://api.binance.com/api/v3/klines"
           f"?symbol={sym}&interval=15m&limit={limit}")
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
        return [{"t": int(c[0]), "o": float(c[1]), "h": float(c[2]),
                 "l": float(c[3]), "c": float(c[4]), "v": float(c[5])} for c in data]
    except Exception as e:
        sys.stderr.write(f"[of-server] klines {sym}: {e}\n")
        return []


def _compute_coin(coin_id, sym, ticker):
    klines = _klines(sym)
    if len(klines) < 60:
        return None

    closes = [k["c"] for k in klines]
    price  = closes[-1]
    chg24h = (closes[-1] / closes[-97] - 1) if len(closes) >= 97 and closes[-97] > 0 else None

    e20 = cs._ema(closes, 20)
    e50 = cs._ema(closes, 50)
    trend = "flat"
    if e20 and e50:
        if e20[-1] > e50[-1] * 1.002:
            trend = "up"
        elif e20[-1] < e50[-1] * 0.998:
            trend = "down"

    rsi = cs._rsi(closes[-30:]) if len(closes) >= 30 else 50.0

    of = ofs.get_of(sym, klines_24h=klines[-96:])

    return {
        "ticker":    ticker,
        "price":     round(price, 6),
        "chg24h":    round(chg24h, 5) if chg24h is not None else None,
        "trend":     trend,
        "rsi":       rsi,
        "delta":     of.get("delta"),
        "delta_pct": of.get("delta_pct"),
        "book_imb":  of.get("book_imb"),
        "poc_dist":  of.get("poc_dist"),
    }


_cache: dict = {"data": None}
_lock = threading.Lock()


def _refresh():
    order = {ticker: i for i, (_, _, ticker) in enumerate(COINS)}
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
        futs = {ex.submit(_compute_coin, cid, sym, t): t for cid, sym, t in COINS}
        for fut in concurrent.futures.as_completed(futs):
            r = fut.result()
            if r:
                results.append(r)
    results.sort(key=lambda x: order.get(x["ticker"], 99))
    payload = {"coins": results, "ts": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())}
    with _lock:
        _cache["data"] = payload
    print(f"[of-server] refreshed {len(results)}/{len(COINS)} coins @ {payload['ts']}", flush=True)


def _bg_loop():
    while True:
        try:
            _refresh()
        except Exception as e:
            print(f"[of-server] refresh error: {e}", flush=True)
        time.sleep(REFRESH)


# ── HTML ───────────────────────────────────────────────────────────────────────

HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Order Flow Live</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#070b0f;color:#b4bcd0;font-family:'Courier New',Courier,monospace;font-size:13px;min-width:820px}

header{background:#0c1117;border-bottom:1px solid #1a2233;padding:10px 20px;display:flex;justify-content:space-between;align-items:center;position:sticky;top:0;z-index:10}
h1{color:#4d9de0;font-size:14px;letter-spacing:3px;font-weight:normal}
#hdr-right{display:flex;gap:16px;align-items:center;font-size:11px;color:#3d4f6e}
.dot{display:inline-block;width:6px;height:6px;border-radius:50%;background:#3fb950;margin-right:3px;animation:pulse 2s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}
#next{color:#2a3a50}

#summary{background:#0c1117;padding:5px 20px;border-bottom:1px solid #12192a;display:flex;gap:28px;font-size:11px;color:#3d4f6e;letter-spacing:.5px}
.sval{font-weight:bold}

table{width:100%;border-collapse:collapse}
thead th{background:#0a0e15;color:#2e4a70;font-size:10px;letter-spacing:1.5px;font-weight:normal;text-transform:uppercase;padding:7px 16px;border-bottom:1px solid #12192a;text-align:right;white-space:nowrap}
thead th:first-child{text-align:left}
tbody tr{border-bottom:1px solid #0c1117;transition:background .1s}
tbody tr:hover{background:#0c1117}
td{padding:11px 16px;text-align:right;vertical-align:middle;white-space:nowrap}
td:first-child{text-align:left}

.pos{color:#3fb950}.neg{color:#f85149}.neu{color:#4a5a70}
.rh{color:#f85149}.rl{color:#3fb950}
.tu{color:#3fb950}.td{color:#f85149}.tf{color:#4a5a70}
.bid{color:#3fb950}.ask{color:#f85149}
.pa{color:#c9813a}.pb{color:#8b63c7}

.tk{font-weight:bold;color:#d0d8e8;letter-spacing:1px;font-size:14px}

.db{display:inline-flex;align-items:center;justify-content:center;width:96px;height:8px;background:#111825;border-radius:4px;position:relative}
.dfb{position:absolute;top:0;right:50%;height:8px;background:#3fb950;border-radius:4px;max-width:48px}
.dsb{position:absolute;top:0;left:50%;height:8px;background:#f85149;border-radius:4px;max-width:48px}
.dc{position:absolute;left:50%;width:1px;height:8px;background:#1a2233;transform:translateX(-50%)}

#loading{position:fixed;inset:0;background:rgba(7,11,15,.93);display:flex;flex-direction:column;align-items:center;justify-content:center;color:#4d9de0;letter-spacing:2px;gap:12px}
#loading p{font-size:12px;color:#2e4a70}
.sp{display:inline-block;width:24px;height:24px;border:2px solid #1a2233;border-top-color:#4d9de0;border-radius:50%;animation:spin 1s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}

.err{color:#f85149;font-size:11px;padding:20px;text-align:center}
</style>
</head>
<body>

<div id="loading">
  <div class="sp"></div>
  <div>FETCHING ORDER FLOW</div>
  <p>Connecting to Binance...</p>
</div>

<header>
  <h1>ORDER FLOW LIVE</h1>
  <div id="hdr-right">
    <span><span class="dot"></span>LIVE</span>
    <span id="ts" class="neu">—</span>
    <span id="next">↻ —</span>
  </div>
</header>

<div id="summary">
  <span>MARKET&nbsp;Δ&nbsp;<span class="sval" id="s-delta">—</span></span>
  <span>BID-HEAVY&nbsp;<span class="sval pos" id="s-bid">—</span></span>
  <span>ASK-HEAVY&nbsp;<span class="sval neg" id="s-ask">—</span></span>
  <span>ABOVE&nbsp;POC&nbsp;<span class="sval pa" id="s-pa">—</span></span>
  <span>BELOW&nbsp;POC&nbsp;<span class="sval pb" id="s-pb">—</span></span>
  <span>OB&gt;70&nbsp;<span class="sval rh" id="s-ob">—</span></span>
  <span>OS&lt;30&nbsp;<span class="sval rl" id="s-os">—</span></span>
</div>

<table>
  <thead>
    <tr>
      <th>COIN</th>
      <th>PRICE</th>
      <th>24H&nbsp;CHG</th>
      <th colspan="2">DELTA&nbsp;15m&nbsp;(bar&nbsp;/&nbsp;val)</th>
      <th>DOM&nbsp;5-LVL</th>
      <th>dPOC&nbsp;DIST</th>
      <th>RSI&nbsp;14</th>
      <th>TREND</th>
    </tr>
  </thead>
  <tbody id="tbody">
    <tr><td colspan="9" class="neu" style="text-align:center;padding:30px">Loading...</td></tr>
  </tbody>
</table>

<script>
const REFRESH = 30;
let cd = REFRESH;
let timerID = null;

function fmtPrice(p) {
  if (p == null) return '—';
  if (p >= 10000) return '$' + Math.round(p).toLocaleString('en');
  if (p >= 100)  return '$' + p.toFixed(1);
  if (p >= 1)    return '$' + p.toFixed(3);
  return '$' + p.toFixed(6);
}

function fmtDelta(d, ticker) {
  if (d == null) return '—';
  const s = d > 0 ? '+' : '';
  const a = Math.abs(d);
  if (a >= 100000) return s + (d/1000).toFixed(0)+'K';
  if (a >= 10000)  return s + (d/1000).toFixed(1)+'K';
  if (a >= 1000)   return s + (d/1000).toFixed(1)+'K';
  if (a >= 1)      return s + d.toFixed(1);
  return s + d.toFixed(3);
}

function deltaBar(pct) {
  if (pct == null) return '<div class="db"><div class="dc"></div></div>';
  const c = Math.max(-100, Math.min(100, pct));
  const w = Math.abs(c) / 100 * 48;
  if (c >= 0) return `<div class="db"><div class="dfb" style="width:${w}px"></div><div class="dc"></div></div>`;
  return `<div class="db"><div class="dsb" style="width:${w}px"></div><div class="dc"></div></div>`;
}

function render(data) {
  const coins = data.coins || [];

  // summary
  const totalDelta = coins.reduce((s,c) => s + (c.delta || 0), 0);
  const bidH  = coins.filter(c => c.book_imb != null && c.book_imb > 1.1).length;
  const askH  = coins.filter(c => c.book_imb != null && c.book_imb < 0.9).length;
  const pocA  = coins.filter(c => c.poc_dist != null && c.poc_dist > 0).length;
  const pocB  = coins.filter(c => c.poc_dist != null && c.poc_dist < 0).length;
  const obCnt = coins.filter(c => c.rsi > 70).length;
  const osCnt = coins.filter(c => c.rsi < 30).length;

  const dSign = totalDelta >= 0 ? '+' : '';
  document.getElementById('s-delta').textContent = dSign + fmtDelta(totalDelta);
  document.getElementById('s-delta').className = 'sval ' + (totalDelta >= 0 ? 'pos' : 'neg');
  document.getElementById('s-bid').textContent  = bidH + '/' + coins.length;
  document.getElementById('s-ask').textContent  = askH + '/' + coins.length;
  document.getElementById('s-pa').textContent   = pocA;
  document.getElementById('s-pb').textContent   = pocB;
  document.getElementById('s-ob').textContent   = obCnt;
  document.getElementById('s-os').textContent   = osCnt;
  document.getElementById('ts').textContent     = data.ts || '—';

  // rows
  document.getElementById('tbody').innerHTML = coins.map(c => {
    const chgCls  = c.chg24h > 0 ? 'pos' : c.chg24h < 0 ? 'neg' : 'neu';
    const chgStr  = c.chg24h != null ? (c.chg24h > 0 ? '+' : '') + (c.chg24h*100).toFixed(2)+'%' : '—';
    const dCls    = (c.delta||0) >= 0 ? 'pos' : 'neg';
    const domCls  = c.book_imb == null ? 'neu' : c.book_imb > 1.1 ? 'bid' : c.book_imb < 0.9 ? 'ask' : 'neu';
    const domStr  = c.book_imb != null ? c.book_imb.toFixed(2)+'×' : '—';
    const pocCls  = c.poc_dist == null ? 'neu' : c.poc_dist > 0 ? 'pa' : 'pb';
    const pocStr  = c.poc_dist != null ? (c.poc_dist>0?'+':'') + (c.poc_dist*100).toFixed(2)+'%' : '—';
    const rsiCls  = c.rsi > 70 ? 'rh' : c.rsi < 30 ? 'rl' : 'neu';
    const rsiStr  = c.rsi != null ? Math.round(c.rsi) : '—';
    const trCls   = c.trend === 'up' ? 'tu' : c.trend === 'down' ? 'td' : 'tf';
    const trStr   = c.trend === 'up' ? '↑ UP' : c.trend === 'down' ? '↓ DN' : '— FL';

    return `<tr>
      <td><span class="tk">${c.ticker}</span></td>
      <td>${fmtPrice(c.price)}</td>
      <td class="${chgCls}">${chgStr}</td>
      <td>${deltaBar(c.delta_pct)}</td>
      <td class="${dCls}" style="min-width:56px">${fmtDelta(c.delta, c.ticker)}</td>
      <td class="${domCls}">${domStr}</td>
      <td class="${pocCls}">${pocStr}</td>
      <td class="${rsiCls}">${rsiStr}</td>
      <td class="${trCls}">${trStr}</td>
    </tr>`;
  }).join('');

  document.getElementById('loading').style.display = 'none';
}

async function doRefresh() {
  try {
    const r = await fetch('/api');
    if (!r.ok) throw new Error(r.status);
    const data = await r.json();
    render(data);
    cd = REFRESH;
  } catch(e) {
    console.error('fetch /api:', e);
    document.getElementById('next').textContent = '↻ ERR';
  }
}

function tick() {
  cd--;
  document.getElementById('next').textContent = '↻ ' + Math.max(0, cd) + 's';
  if (cd <= 0) doRefresh();
}

doRefresh().then(() => {
  if (timerID) clearInterval(timerID);
  timerID = setInterval(tick, 1000);
});
</script>
</body>
</html>"""


# ── HTTP handler ───────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # suppress per-request logs

    def _send(self, code, content_type, body):
        b = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", len(b))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._send(200, "text/html; charset=utf-8", HTML)
        elif self.path == "/api":
            with _lock:
                data = _cache["data"]
            if data is None:
                self._send(503, "application/json", '{"error":"initialising"}')
            else:
                self._send(200, "application/json", json.dumps(data))
        else:
            self._send(404, "text/plain", "not found")


# ── main ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"[of-server] starting initial data fetch...", flush=True)
    # first refresh in foreground so the page is ready immediately
    try:
        _refresh()
    except Exception as e:
        print(f"[of-server] initial fetch error: {e}", flush=True)

    # background refresh loop
    t = threading.Thread(target=_bg_loop, daemon=True)
    t.start()

    print(f"[of-server] serving on http://localhost:{PORT}", flush=True)
    try:
        HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
    except KeyboardInterrupt:
        print("[of-server] stopped", flush=True)
