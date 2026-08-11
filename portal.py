#!/usr/bin/env python3
"""
portal.py — ALICE-style live portal that broadcasts EVERYTHING we have.

A single keyless stdlib HTTP server (no deps) that aggregates every live data
source in the project and serves a dark trading-terminal dashboard (styled like
OpenAlice: bg #0b0e14, cards #131722, gold #f0b90b / blue #3b82f6 / green
#23b99a / red #be4138). The page polls /api/state every 4s = live broadcast.

Sources (each fail-soft — one dead source never breaks the portal):
  - Polymarket scalp_lab  (scalp_lab_state.json)        realized P&L, legs, open
  - Futures bot           (futures-bot/paper_state.json + log)  MAIN/RUNNER/LEV
  - Bloomberg ports feed  (claud/ports_data.json)       KPIs + MTM movers
  - Money-flow detector   (moneyflow_log.jsonl)         recent signals
  - New-market logger     (newmarket_log.jsonl)         recent fresh markets
  - Resolved trades       (scalp_lab_state.json)        held-to-settlement subset
  - Crypto ticker         (keyless CoinGecko, cached)   BTC/ETH/SOL live
  - Fleet health          (launchctl, cached)           com.aryan.* exit codes

Run:  python3 portal.py [PORT]   (default 8090)   then open http://localhost:8090
PAPER ONLY, read-only on all bot state. $0, keyless.
"""
import json, os, sys, time, subprocess, urllib.request
import threading, socket, ssl, base64, hashlib, struct
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from datetime import datetime as _datetime

# Live price cache updated by Binance WS thread
_live_prices = {}  # sym -> {"p": price, "c": pct_24h, "ts": time}
_live_funding = {}  # sym -> {"rate": annualized_rate, "ts": time}
_watchdog_alive = {"alive": False, "pid": None, "checked": 0}
_live_liquidations = []   # recent liquidation events (capped at 50)
_live_mark = {}           # sym -> {mark, index, funding_rate, ts}
_live_oi = {}  # sym -> {oi_usdt, ts}
_live_sentiment = {}  # fear_greed value/classification, btc_dom, total_mcap
_live_iv = {}  # "BTC" -> {iv, ts}, "ETH" -> {iv, ts}

def _binance_ws_thread(host="stream.binance.com", port=9443, streams=None):
    """Connect to a Binance WebSocket. Default (no args) = SPOT miniTicker prices.
    forceOrder (liquidations) + markPrice are FUTURES streams that ONLY deliver on
    fstream.binance.com — a second thread is started below with the futures host for those
    (subscribing to them on the spot host silently delivers nothing — the old liquidations bug)."""
    import time as _time
    SYMS = ["btcusdt", "ethusdt", "solusdt"]
    if streams is None:
        streams = "/".join(f"{s}@miniTicker" for s in SYMS)
    path = f"/stream?streams={streams}"
    while True:
        try:
            ctx = ssl.create_default_context()
            sock = socket.create_connection((host, port), timeout=15)
            ssock = ctx.wrap_socket(sock, server_hostname=host)
            # WebSocket handshake
            key = base64.b64encode(os.urandom(16)).decode()
            handshake = (f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\n"
                         f"Upgrade: websocket\r\nConnection: Upgrade\r\n"
                         f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
            ssock.send(handshake.encode())
            resp = b""
            while b"\r\n\r\n" not in resp:
                resp += ssock.recv(1024)
            # Read frames
            buf = b""
            while True:
                chunk = ssock.recv(4096)
                if not chunk:
                    break
                buf += chunk
                while len(buf) >= 2:
                    fin_op = buf[0]; mask_len = buf[1]
                    pay_len = mask_len & 0x7f
                    off = 2
                    if pay_len == 126:
                        if len(buf) < 4: break
                        pay_len = struct.unpack(">H", buf[2:4])[0]; off = 4
                    elif pay_len == 127:
                        if len(buf) < 10: break
                        pay_len = struct.unpack(">Q", buf[2:10])[0]; off = 10
                    if len(buf) < off + pay_len: break
                    payload = buf[off:off + pay_len]
                    buf = buf[off + pay_len:]
                    try:
                        msg = json.loads(payload.decode("utf-8", errors="ignore"))
                        d = msg.get("data") or msg
                        e_type = d.get("e") or msg.get("e") or ""
                        sym = (d.get("s") or "").upper()

                        if e_type == "24hrMiniTicker" or (sym and d.get("c")):
                            p = float(d.get("c") or 0)
                            o = float(d.get("o") or p)
                            pct = round((p - o) / o * 100, 2) if o else 0
                            _live_prices[sym] = {"p": round(p, 4), "c": pct, "ts": _time.time()}

                        elif e_type == "forceOrder":
                            o = d.get("o") or {}
                            _live_liquidations.append({
                                "sym": (o.get("s") or sym or "")[:12],
                                "side": o.get("S", ""),
                                "qty": float(o.get("q") or 0),
                                "price": float(o.get("p") or 0),
                                "ts": int(d.get("T") or d.get("E") or (_time.time()*1000)) // 1000
                            })
                            if len(_live_liquidations) > 50:
                                _live_liquidations.pop(0)

                        elif e_type == "markPriceUpdate":
                            _live_mark[sym] = {
                                "mark": float(d.get("p") or 0),
                                "index": float(d.get("i") or 0),
                                "funding_rate": float(d.get("r") or 0),
                                "next_ts": int(d.get("T") or 0) // 1000,
                                "ts": _time.time()
                            }
                    except Exception:
                        pass
        except Exception:
            pass
        time.sleep(5)  # reconnect delay

def _funding_thread():
    """Poll Binance futures for BTC/ETH/SOL funding rates every 60s."""
    SYMS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    while True:
        for sym in SYMS:
            try:
                url = f"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={sym}"
                req = urllib.request.Request(url, headers={"User-Agent": "portal"})
                d = json.loads(urllib.request.urlopen(req, timeout=8).read())
                rate_8h = float(d.get("lastFundingRate") or 0)
                rate_ann = round(rate_8h * 3 * 365 * 100, 2)  # annualized %
                next_ts = int(d.get("nextFundingTime") or 0) // 1000
                _live_funding[sym] = {"rate_8h": round(rate_8h * 100, 4),
                                      "rate_ann": rate_ann, "next_ts": next_ts, "ts": time.time()}
            except Exception:
                pass
        time.sleep(60)

def _watchdog_thread():
    """Check if the scalp watchdog nohup orphan is alive every 30s."""
    while True:
        try:
            out = subprocess.run(["pgrep", "-f", "watchdog_loop.sh"],
                                 capture_output=True, text=True, timeout=5).stdout.strip()
            pids = [p for p in out.splitlines() if p.strip()]
            _watchdog_alive["alive"] = len(pids) > 0
            _watchdog_alive["pid"] = pids[0] if pids else None
            _watchdog_alive["checked"] = int(time.time())
        except Exception:
            _watchdog_alive["alive"] = False
        time.sleep(30)

def _oi_thread():
    """Poll Binance futures open interest every 60s."""
    SYMS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    while True:
        for sym in SYMS:
            try:
                url = f"https://fapi.binance.com/fapi/v1/openInterest?symbol={sym}"
                req = urllib.request.Request(url, headers={"User-Agent": "portal"})
                d = json.loads(urllib.request.urlopen(req, timeout=8).read())
                price = (_live_prices.get(sym) or {}).get("p") or 0
                oi_qty = float(d.get("openInterest") or 0)
                _live_oi[sym] = {"oi_qty": round(oi_qty, 1),
                                  "oi_usdt": round(oi_qty * price / 1e9, 2),  # in $B
                                  "ts": time.time()}
            except Exception:
                pass
        time.sleep(60)

def _sentiment_thread():
    """Poll Fear & Greed (alternative.me) and BTC dominance (CoinGecko) every 30 min."""
    while True:
        try:
            req = urllib.request.Request("https://api.alternative.me/fng/?limit=1",
                                          headers={"User-Agent": "portal"})
            d = json.loads(urllib.request.urlopen(req, timeout=8).read())
            entry = (d.get("data") or [{}])[0]
            _live_sentiment["fg_value"] = int(entry.get("value") or 0)
            _live_sentiment["fg_class"] = entry.get("value_classification") or ""
            _live_sentiment["fg_ts"] = time.time()
        except Exception:
            pass
        try:
            url = "https://api.coingecko.com/api/v3/global"
            req = urllib.request.Request(url, headers={"User-Agent": "portal"})
            d = json.loads(urllib.request.urlopen(req, timeout=8).read())
            data = d.get("data") or {}
            pcts = data.get("market_cap_percentage") or {}
            _live_sentiment["btc_dom"] = round(pcts.get("btc") or 0, 1)
            _live_sentiment["eth_dom"] = round(pcts.get("eth") or 0, 1)
            _live_sentiment["total_mcap_b"] = round((data.get("total_market_cap") or {}).get("usd", 0) / 1e9)
            _live_sentiment["mcap_chg_24h"] = round(data.get("market_cap_change_percentage_24h_usd") or 0, 2)
        except Exception:
            pass
        time.sleep(1800)

def _iv_thread():
    """Poll Deribit DVOL (volatility index) for BTC/ETH every 5 minutes.

    Uses /get_volatility_index_data which returns the canonical DVOL value —
    the equivalent of VIX for crypto. Each row is [timestamp_ms, open, high, low, close].
    We take the close of the most-recent completed hourly candle.
    """
    CURRENCIES = ["BTC", "ETH"]
    while True:
        now_ms  = int(time.time() * 1000)
        # 2 hours of hourly candles; we only need the last one
        start_ms = now_ms - 2 * 3600 * 1000
        for asset in CURRENCIES:
            try:
                url = (
                    f"https://www.deribit.com/api/v2/public/get_volatility_index_data"
                    f"?currency={asset}&start_timestamp={start_ms}"
                    f"&end_timestamp={now_ms}&resolution=3600"
                )
                req = urllib.request.Request(url, headers={"User-Agent": "portal"})
                d   = json.loads(urllib.request.urlopen(req, timeout=10).read())
                rows = (d.get("result") or {}).get("data") or []
                if rows:
                    # last row: [ts_ms, open, high, low, close]
                    iv = float(rows[-1][4])
                    _live_iv[asset] = {"iv": round(iv, 1), "ts": time.time()}
            except Exception:
                pass
        time.sleep(300)

def _dt(s):
    """Parse an ISO timestamp to datetime (for the equity-curve time axis)."""
    try:
        return _datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None

HOME = os.path.expanduser("~")
PM   = os.path.join(HOME, "Documents", "polymarket")
PORTS = os.path.join(HOME, "Documents", "Codex", "2026-05-28", "claud", "ports_data.json")
FUT  = os.path.join(HOME, "Documents", "futures-bot")
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8090

_cache = {}
def _cached(key, ttl, fn):
    now = time.time()
    hit = _cache.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    try:
        val = fn()
    except Exception as e:
        val = {"error": str(e)[:120]}
    _cache[key] = (now, val)
    return val

def _age(path):
    try:
        return round(time.time() - os.path.getmtime(path))
    except Exception:
        return None

def _fresh(age, limit):
    return age is not None and age <= limit

# ---------- sources (all fail-soft) ----------
def src_scalp():
    p = os.path.join(PM, "scalp_lab_state.json")
    st = json.load(open(p))
    tot = 0.0; opn = 0; closed = 0
    legs = {}
    for leg, cfg in st.items():
        if not isinstance(cfg, dict):
            continue
        c = cfg.get("closed", []) or []
        s = sum(float(e.get("pnl_usdc") or 0) for e in c if isinstance(e, dict))
        n = len(c)
        if n:
            legs[leg] = {"n": n, "pnl": round(s, 2)}
        tot += s; closed += n
        opn += len(cfg.get("open", []) or []) if isinstance(cfg.get("open"), list) else 0
    top = sorted(legs.items(), key=lambda kv: kv[1]["pnl"], reverse=True)
    age = _age(p)
    return {"realized": round(tot, 2), "open": opn, "closed": closed,
            "legs": [{"leg": k, **v} for k, v in top[:6]],
            "worst": [{"leg": k, **v} for k, v in sorted(legs.items(), key=lambda kv: kv[1]["pnl"])[:4]],
            "age_s": age, "fresh": _fresh(age, 3600)}

def src_resolved():
    p = os.path.join(PM, "scalp_lab_state.json")
    st = json.load(open(p))
    res = []
    for leg, cfg in st.items():
        if not isinstance(cfg, dict):
            continue
        for e in cfg.get("closed", []) or []:
            if isinstance(e, dict) and (e.get("reason") or "").lower() == "resolved":
                res.append(float(e.get("pnl_usdc") or 0))
    wins = sum(1 for x in res if x > 0)
    return {"n": len(res), "pnl": round(sum(res), 2),
            "wr": round(wins / len(res) * 100) if res else 0}

def src_analyst():
    st = {}
    try:
        st = json.load(open(os.path.join(PM, "analyst_agent_state.json")))   # live MTM snapshot
    except Exception:
        pass
    pf = os.path.join(PM, "analyst_positions.json")
    pos = []
    try:
        pj = json.load(open(pf))
        for p in (pj.get("positions") or [])[:6]:
            pos.append({"q": (p.get("q") or "")[:42], "side": p.get("side"), "edge": p.get("edge_pct")})
    except Exception:
        pass
    age = _age(pf)
    return {"realized": round(st.get("realized", 0) or 0, 2), "unreal": round(st.get("unrealized", 0) or 0, 2),
            "total": round(st.get("total", 0) or 0, 2), "n_open": st.get("n_open"), "n_settled": st.get("n_settled"),
            "positions": pos, "age_s": age, "fresh": _fresh(age, 14400)}

def src_futures():
    p = os.path.join(FUT, "paper_state.json")
    d = json.load(open(p))
    main_eq = float(d.get("equity") or 0)
    closed = d.get("closed_trades") or (len(d.get("closed", [])) if isinstance(d.get("closed"), list) else 0)
    run = lev = None; scan = None
    try:
        for line in reversed(open(os.path.join(FUT, "futures_bot.log")).read().splitlines()):
            if "scan #" in line and "done" in line:
                scan = line.strip()[-200:]
                import re
                r = re.search(r"req=\$?([\-\d.]+)", line); run = float(r.group(1)) if r else None
                lv = re.search(r"lev_eq=\$?([\-\d.]+)", line); lev = float(lv.group(1)) if lv else None
                break
    except Exception:
        pass
    age = _age(p)
    return {"main": round(main_eq, 2), "runner": run, "lev": lev, "closed": closed,
            "scan": scan, "age_s": age, "fresh": _fresh(age, 1800)}

def src_ports():
    d = json.load(open(PORTS))
    h = d.get("headline", {}) or {}
    pos = d.get("positions", []) or []
    marked = [p for p in pos if p.get("unreal") is not None]
    win = sorted(marked, key=lambda p: -(p["unreal"]))[:4]
    los = sorted(marked, key=lambda p: (p["unreal"]))[:4]
    age = _age(PORTS)
    return {"headline": h, "gen": d.get("generated_at"),
            "winners": [{"q": (p.get("q") or "")[:46], "u": round(p["unreal"], 2)} for p in win],
            "losers": [{"q": (p.get("q") or "")[:46], "u": round(p["unreal"], 2)} for p in los],
            "age_s": age, "fresh": _fresh(age, 7200)}

def _tail_jsonl(path, n):
    rows = []
    try:
        for line in open(path).read().splitlines()[-400:]:
            line = line.strip()
            if line:
                try: rows.append(json.loads(line))
                except Exception: pass
    except Exception:
        pass
    return rows[-n:][::-1]

def src_moneyflow():
    p = os.path.join(PM, "moneyflow_log.jsonl")
    rows = _tail_jsonl(p, 8)
    age = _age(p)
    return {"recent": rows, "age_s": age, "fresh": _fresh(age, 7200), "count": len(rows)}

def src_newmarket():
    p = os.path.join(PM, "newmarket_log.jsonl")
    rows = _tail_jsonl(p, 8)
    # surface tradeable leads if present
    leads = [r for r in rows if r.get("tradeable_lead")]
    age = _age(p)
    return {"recent": rows, "leads": leads[:5], "age_s": age, "fresh": _fresh(age, 3600), "count": len(rows)}

def src_crypto():
    def go():
        # prefer live WS prices (updated every tick, < 2s stale)
        if _live_prices:
            nm = {"BTCUSDT": "bitcoin", "ETHUSDT": "ethereum", "SOLUSDT": "solana"}
            out = {}
            for sym, cg in nm.items():
                v = _live_prices.get(sym)
                if v:
                    age_s = round(time.time() - v["ts"])
                    out[cg] = {"p": v["p"], "c": v["c"], "live": True, "age_s": age_s}
            if out:
                return out
        # fallback to CoinGecko
        url = "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum,solana&vs_currencies=usd&include_24hr_change=true"
        req = urllib.request.Request(url, headers={"User-Agent": "portal"})
        d = json.loads(urllib.request.urlopen(req, timeout=8).read())
        return {k: {"p": v.get("usd"), "c": round(v.get("usd_24h_change", 0), 2), "live": False}
                for k, v in d.items()}
    return _cached("crypto", 30, go)

def src_funding():
    if not _live_funding:
        return {"error": "no data yet", "age_s": None, "fresh": False}
    nm = {"BTCUSDT": "BTC", "ETHUSDT": "ETH", "SOLUSDT": "SOL"}
    rates = []
    oldest = time.time()
    for sym, label in nm.items():
        v = _live_funding.get(sym)
        if v:
            rates.append({"sym": label, "rate_8h": v["rate_8h"], "rate_ann": v["rate_ann"],
                          "next_ts": v.get("next_ts")})
            oldest = min(oldest, v["ts"])
    age = round(time.time() - oldest) if rates else None
    return {"rates": rates, "age_s": age, "fresh": _fresh(age, 180)}

def src_candles():
    def go():
        out = {}
        cg = {"BTCUSDT": "bitcoin", "ETHUSDT": "ethereum", "SOLUSDT": "solana"}
        for sym in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
            try:
                url = f"https://api.binance.com/api/v3/klines?symbol={sym}&interval=5m&limit=90"
                req = urllib.request.Request(url, headers={"User-Agent": "portal"})
                k = json.loads(urllib.request.urlopen(req, timeout=8).read())
                out[sym] = [{"time": int(c[0] // 1000), "open": float(c[1]), "high": float(c[2]),
                             "low": float(c[3]), "close": float(c[4])} for c in k]
            except Exception:                       # fallback: keyless CoinGecko OHLC (coarser)
                url = f"https://api.coingecko.com/api/v3/coins/{cg[sym]}/ohlc?vs_currency=usd&days=1"
                req = urllib.request.Request(url, headers={"User-Agent": "portal"})
                d = json.loads(urllib.request.urlopen(req, timeout=8).read())
                out[sym] = [{"time": int(c[0] // 1000), "open": c[1], "high": c[2],
                             "low": c[3], "close": c[4]} for c in d]
        return out
    return _cached("candles", 20, go)

def src_equity():
    """Cumulative-P&L curves from each book's closed trades (time-series for charts)."""
    def curve(points):                      # points = [(iso_ts, pnl), ...]
        rows = []
        for ts, p in points:
            d = _dt(ts)
            if d:
                rows.append((int(d.timestamp()), float(p or 0)))
        rows.sort()
        out = {}; cum = 0.0                  # dedup by second (lightweight-charts needs unique asc time)
        for t, p in rows:
            cum += p
            out[t] = round(cum, 2)
        return [{"time": t, "value": v} for t, v in sorted(out.items())]
    def go():
        res = {}
        try:
            st = json.load(open(os.path.join(PM, "scalp_lab_state.json")))
            pts = [(e["closed_at"], e.get("pnl_usdc")) for leg, cfg in st.items()
                   if isinstance(cfg, dict) for e in (cfg.get("closed", []) or [])
                   if isinstance(e, dict) and e.get("closed_at")]
            res["scalp"] = curve(pts)
        except Exception:
            res["scalp"] = []
        try:
            d = json.load(open(os.path.join(FUT, "paper_state.json")))
            res["futures"] = curve([(t.get("ts"), t.get("pnl")) for t in d.get("closed", []) if t.get("ts")])
        except Exception:
            res["futures"] = []
        return res
    return _cached("equity", 30, go)

def src_fleet():
    def go():
        out = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=8).stdout
        ok = bad = total = 0
        fails = []
        for line in out.splitlines():
            if "com.aryan." not in line:
                continue
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            total += 1
            code = parts[1]
            if code in ("0", "-"):
                ok += 1
            else:
                bad += 1
                fails.append({"job": parts[2].replace("com.aryan.", ""), "code": code})
        return {"total": total, "ok": ok, "bad": bad, "fails": fails[:8]}
    return _cached("fleet", 60, go)

def src_algos():
    p = os.path.join(PM, "algo_lab_state.json")
    d = json.load(open(p))
    age = _age(p)
    rlb = d.get("robust_leaderboard", [])
    return {"results": [{"name": r["name"], "pnl": r["avg_pnl"], "by_asset": r.get("by_asset", {}),
                         "trades": r["trades"], "pos": r["pos"], "reg_now": r.get("reg_now"),
                         "robust": r.get("robust", False), "min_asset": r.get("min_asset")}
                        for r in d.get("leaderboard", [])],
            "buyhold": d.get("buyhold_pct"), "beat": d.get("beat_buyhold"),
            "days": d.get("age_days"), "assets": list(d.get("assets", {}).keys()),
            "champion": (d.get("champion") or {}).get("name"),
            "regime": d.get("regime"), "regime_leader": d.get("regime_leader"),
            "robust_leader": d.get("robust_leader"), "robust_count": len(rlb), "robust_lb": rlb[:5],
            "age_s": age, "fresh": _fresh(age, 1800)}

def src_race():
    def go():
        d = json.load(open(os.path.join(PM, "algo_lab_state.json")))
        assets = d.get("assets", {})
        out = []
        for r in d.get("leaderboard", [])[:6]:
            agg, cnt = {}, {}                          # average the per-asset equity curves by timestamp
            for a in assets:
                for pt in assets[a].get("algos", {}).get(r["name"], {}).get("curve", []):
                    agg[pt["time"]] = agg.get(pt["time"], 0) + pt["value"]
                    cnt[pt["time"]] = cnt.get(pt["time"], 0) + 1
            out.append({"name": r["name"], "pnl": r["avg_pnl"],
                        "curve": [{"time": t, "value": round(agg[t] / cnt[t], 3)} for t in sorted(agg)]})
        return {"series": out, "days": d.get("age_days")}
    return _cached("race", 15, go)

def src_provider():
    p = os.path.join(PM, "provider_health_state.json")
    d = json.load(open(p))
    tiers = {}
    try:
        for line in reversed(open(os.path.join(PM, "provider_health_log.jsonl")).read().splitlines()):
            line = line.strip()
            if line:
                tiers = json.loads(line).get("tiers", {})
                break
    except Exception:
        pass
    age = _age(p)
    return {"status": d.get("status") or d.get("raw") or "UNKNOWN", "down": d.get("down", []),
            "streak": d.get("streak", 0), "tiers": tiers,
            "updated": (d.get("updated") or "")[:16], "age_s": age, "fresh": _fresh(age, 14400)}

def src_arb():
    bp = os.path.join(PM, "basket_paper_book.json")
    b = json.load(open(bp))
    opn = b.get("open", {}) or {}
    cld = b.get("closed", {}) or {}
    locks = []
    for slug, v in (opn.items() if isinstance(opn, dict) else []):
        if isinstance(v, dict):
            locks.append({"q": (v.get("title") or slug)[:42], "kind": v.get("kind", ""),
                          "edge": round(float(v.get("edge") or v.get("net_edge") or 0), 4),
                          "cost": round(float(v.get("lock_cost") or 0), 3),
                          "n": v.get("n_outcomes") or 0})
    locks.sort(key=lambda x: -(x["edge"]))
    da_open = 0; da_scan = ""
    try:
        da = json.load(open(os.path.join(PM, "dataarb_state.json")))
        da_open = len(da.get("open", []) or [])
        da_scan = str(da.get("last_scan", ""))[:16]
    except Exception:
        pass
    age = _age(bp)
    return {"open": len(locks), "closed": len(cld), "locks": locks[:6],
            "dataarb_open": da_open, "last_scan": da_scan, "age_s": age, "fresh": _fresh(age, 14400)}

def src_edgehunt():
    p = os.path.join(PM, "edge_hunter_dossiers.json")
    d = json.load(open(p))
    dos = d.get("dossiers") or []
    age = _age(p)
    return {"count": len(dos), "last_hunt": str(d.get("last_hunt", ""))[:16],
            "dossiers": [{"q": (x.get("question") or x.get("q") or x.get("market") or "")[:44],
                          "verdict": str(x.get("verdict") or x.get("status") or "")[:20]}
                         for x in dos[:5]], "age_s": age, "fresh": _fresh(age, 43200)}

def src_alerts():
    p = os.path.join(PM, "pnl_alert.log")
    lines = []
    try:
        for l in open(p).read().splitlines()[-200:]:
            l = l.strip()
            if l: lines.append(l[-110:])
    except Exception:
        pass
    age = _age(p)
    return {"recent": lines[-8:][::-1], "count": len(lines), "age_s": age, "fresh": _fresh(age, 7200)}

def src_dsr():
    import statistics
    p = os.path.join(PM, "scalp_lab_state.json")
    st = json.load(open(p))
    legs = []
    for leg, cfg in st.items():
        if not isinstance(cfg, dict): continue
        c = cfg.get("closed", []) or []
        pnls = [float(e.get("pnl_usdc") or 0) for e in c if isinstance(e, dict)]
        n = len(pnls)
        if n < 2: continue
        mean = sum(pnls) / n
        wr = round(sum(1 for x in pnls if x > 0) / n * 100)
        try:
            std = statistics.stdev(pnls)
            ci_lo = round(mean - 1.96 * std / (n ** 0.5), 4)
        except Exception:
            ci_lo = None
        gap = max(0, 30 - n)
        status = "GATE" if (n >= 30 and ci_lo is not None and ci_lo > 0) else ("KILL" if (n >= 5 and mean < -0.05) else ("ACCUM" if gap > 0 else "REVIEW"))
        legs.append({"leg": leg[:28], "n": n, "mean": round(mean, 3), "wr": wr, "gap": gap, "ci_lo": ci_lo, "status": status})
    legs.sort(key=lambda x: x["mean"], reverse=True)
    age = _age(p)
    return {"legs": legs, "total": len(legs), "age_s": age, "fresh": _fresh(age, 3600)}

def src_edgebots():
    eb = os.path.join(HOME, "Documents", "edge-bots")
    def load(f):
        try: return json.load(open(os.path.join(eb, f)))
        except: return {}
    fs = load("funding_state.json")
    bs = load("basis_state.json")
    gs = load("gate_state.json")
    ps = load("profit_state.json")
    bots = []
    for name, state, key in [
        ("funding", fs, "funding_collected"),
        ("basis", bs, "funding_collected"),
    ]:
        eq = float(state.get("equity", 1000) or 1000)
        pnl = round(eq - 1000, 2)
        n_open = len(state.get("positions", {}) or {})
        n_closed = len(state.get("closed", []) or [])
        bots.append({"name": name, "pnl": pnl, "equity": round(eq, 2), "open": n_open, "closed": n_closed})
    verdicts = gs.get("verdicts", {})
    near = ps.get("near", {})
    age = _age(os.path.join(eb, "gate_state.json"))
    return {"bots": bots, "verdicts": verdicts, "near": near,
            "age_s": age, "fresh": _fresh(age, 3600)}

def src_liquidations():
    recent = list(_live_liquidations[-10:])[::-1]  # most recent first
    # tag big ones (>$500k notional)
    for r in recent:
        r["big"] = (r["qty"] * r["price"]) > 500_000
    # The feed is Binance FUTURES (!forceOrder@arr on fstream). That host is geo-restricted
    # in some regions (e.g. India): the WS handshake succeeds but 0 data flows. Be honest about
    # an empty feed rather than implying a live-but-quiet one.
    note = "" if recent else "no feed — Binance futures (fstream) geo-restricted on this network"
    return {"recent": recent, "count": len(_live_liquidations),
            "fresh": len(recent) > 0, "note": note}

def src_cockpit_watchdog():
    """OpenAlice cockpit 24/7 watch-and-heal daemon (oa_watchdog.py -> watchdog.json)."""
    p = os.path.expanduser("~/openalice/ui/public/watchdog.json")
    d = json.load(open(p)); age = _age(p); jobs = d.get("jobs", [])
    return {"ok": d.get("ok"), "monitored": len(jobs), "healed": d.get("healed", []),
            "unhealed": d.get("unhealed", []), "problems": (d.get("problems") or [])[:6],
            "bad": [j.get("job") for j in jobs if not j.get("ok")],
            "age_s": age, "fresh": _fresh(age, 600)}

def src_honesty():
    """OpenAlice honesty sentinel (oa_honesty_sentinel.py -> honesty.json): data-correctness flags."""
    p = os.path.expanduser("~/openalice/ui/public/honesty.json")
    d = json.load(open(p)); age = _age(p); fl = d.get("flags", [])
    return {"ok": d.get("ok"), "n_flags": len(fl), "flags": fl[:8],
            "age_s": age, "fresh": _fresh(age, 400)}

def src_oa_fleet():
    """OpenAlice paper cockpit fleet (oa_fleet.py -> fleet.json): every paper account + structural sub-total."""
    p = os.path.expanduser("~/openalice/ui/public/fleet.json")
    d = json.load(open(p)); age = _age(p)
    accts = sorted(d.get("accounts", []), key=lambda a: -(a.get("pnl") or -1e9))
    top = [{"label": a.get("label"), "pnl": a.get("pnl"), "type": a.get("type")} for a in accts[:5]]
    levs = [(a.get("leverage") or 0, a.get("label")) for a in accts if a.get("leverage") is not None]
    mlev, mlev_acct = (max(levs) if levs else (0, None))
    return {"total_pnl": d.get("total_pnl"), "equity": d.get("total_equity"), "n": d.get("n_accounts"),
            "struct_pnl": d.get("structural_pnl"), "struct_beats": d.get("structural_beats"),
            "struct_n": d.get("structural_n"), "reset_at": d.get("reset_at"),
            "max_lev": mlev, "max_lev_acct": mlev_acct,
            "top": top, "age_s": age, "fresh": _fresh(age, 120)}

def src_oa_legs():
    """OpenAlice 5:1 candle legs (oa_legs_runner.py -> legs.json): target-hit vs breakeven."""
    p = os.path.expanduser("~/openalice/ui/public/legs.json")
    d = json.load(open(p)); age = _age(p)
    legs = [{"leg": l.get("leg"), "pos": l.get("pos"), "w": l.get("wins", 0),
             "n": (l.get("wins", 0) + l.get("losses", 0))} for l in d.get("legs", [])]
    return {"rr": d.get("rr"), "breakeven": d.get("breakeven"), "btc": d.get("btc"),
            "legs": legs, "age_s": age, "fresh": _fresh(age, 180)}

def src_market_structure():
    """OI, mark price, IV, Fear&Greed, dominance in one card."""
    oi = {}
    for sym in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
        v = _live_oi.get(sym)
        if v: oi[sym[:3]] = {"oi_b": v["oi_usdt"], "ts": v["ts"]}
    mark = {}
    for sym in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
        v = _live_mark.get(sym)
        if v: mark[sym[:3]] = {"mark": v["mark"], "index": v["index"],
                                "rate": round(v["funding_rate"] * 100, 4)}
    iv = {k: v for k, v in _live_iv.items()}
    sent = dict(_live_sentiment)
    age = None
    if sent.get("fg_ts"):
        age = round(time.time() - sent["fg_ts"])
    return {"oi": oi, "mark": mark, "iv": iv, "sentiment": sent,
            "age_s": age, "fresh": _fresh(age, 3600)}

def src_hermes():
    """Tail the local Hermes news RSS proxy on :48580."""
    try:
        req = urllib.request.Request("http://localhost:48580/api/news?limit=8",
                                      headers={"User-Agent": "portal"})
        d = json.loads(urllib.request.urlopen(req, timeout=4).read())
        items = d if isinstance(d, list) else (d.get("items") or d.get("news") or d.get("articles") or [])
        news = [{"title": (x.get("title") or x.get("headline") or "")[:80],
                 "source": (x.get("source") or x.get("feed") or "")[:20],
                 "ts": str(x.get("published") or x.get("ts") or x.get("date") or "")[:16]}
                for x in items[:8]]
        return {"news": news, "count": len(news), "fresh": True, "age_s": 0}
    except Exception as e:
        # try alternate endpoint
        try:
            req = urllib.request.Request("http://localhost:48580/",
                                          headers={"User-Agent": "portal"})
            raw = urllib.request.urlopen(req, timeout=4).read().decode("utf-8", errors="ignore")
            # just surface that it's reachable
            return {"news": [], "count": 0, "raw_len": len(raw), "fresh": True, "age_s": 0}
        except Exception as e2:
            return {"error": str(e2)[:80], "news": [], "count": 0, "fresh": False}

def src_edgebots_live():
    """Read edge-bots funding_state.json for live equity and top positions."""
    eb = os.path.join(HOME, "Documents", "edge-bots")
    results = []
    for name, fname in [("funding", "funding_state.json"), ("basis", "basis_state.json")]:
        try:
            d = json.load(open(os.path.join(eb, fname)))
            eq = round(float(d.get("equity") or 1000), 2)
            positions = d.get("positions") or {}
            pos_list = []
            if isinstance(positions, dict):
                for sym, p in list(positions.items())[:4]:
                    ann = round(float(p.get("ann0") or 0) * 100, 1)
                    funding = round(float(p.get("funding") or 0), 3)
                    pos_list.append({"sym": sym.replace("/USDT:USDT",""), "ann": ann, "funding": funding})
            closed = d.get("closed") or []
            total_pnl = round(sum(float(t.get("pnl") or 0) for t in closed), 2)
            age = _age(os.path.join(eb, fname))
            results.append({"name": name, "equity": eq, "pnl": round(eq-1000, 2),
                             "n_open": len(positions), "n_closed": len(closed),
                             "total_closed_pnl": total_pnl,
                             "top_pos": pos_list, "age_s": age})
        except Exception as e:
            results.append({"name": name, "error": str(e)[:60]})
    return {"bots": results, "fresh": all(r.get("age_s", 9999) < 600 for r in results if not r.get("error"))}

def src_cryptomovers():
    p = "/tmp/crypto_movers_latest.json"
    d = json.load(open(p))
    age = _age(p)
    return {
        "n_coins": d.get("n_coins", 0),
        "n_movers": d.get("n_movers", 0),
        "avg_chg24h": d.get("avg_chg24h"),
        "top_gainer": d.get("top_gainer"),
        "top_loser": d.get("top_loser"),
        "movers": (d.get("movers") or [])[:6],
        "age_s": age, "fresh": _fresh(age, 1800)
    }

def src_fiidii():
    p = os.path.join(PM, "fii_dii_log.jsonl")
    rows = _tail_jsonl(p, 5)
    age = _age(p)
    return {"recent": rows, "count": len(rows), "age_s": age, "fresh": _fresh(age, 86400)}

def src_macd():
    p = os.path.join(PM, "macd_paper_state.json")
    d = json.load(open(p))
    age = _age(p)
    return {"positions": d.get("positions", {}), "signals": d.get("signals", {}),
            "realized_pnl": d.get("realized_pnl", 0), "ts": d.get("ts", 0),
            "age_s": age, "fresh": _fresh(age, 600)}

def src_vol_regime():
    p = os.path.join(PM, "vol_regime_latest.json")
    d = json.load(open(p))
    age = _age(p)
    d["age_s"] = age
    d["fresh"] = _fresh(age, 86400)
    return d

def src_tournament():
    try:
        req = urllib.request.Request("http://localhost:3002/tournament.json",
                                     headers={"User-Agent": "portal"})
        return json.loads(urllib.request.urlopen(req, timeout=3).read())
    except Exception as e:
        return {"error": str(e)}

def aggregate():
    def safe(fn):
        try: return fn()
        except Exception as e: return {"error": str(e)[:140]}
    scalp = safe(src_scalp); fut = safe(src_futures); analyst = safe(src_analyst)
    total_paper = round((scalp.get("realized") or 0) + ((fut.get("main") or 0) - 1000)
                        + (analyst.get("realized") or 0), 2)
    return {
        "ts": int(time.time()),
        "scalp": scalp, "resolved": safe(src_resolved), "futures": fut, "analyst": analyst,
        "ports": safe(src_ports), "moneyflow": safe(src_moneyflow),
        "newmarket": safe(src_newmarket), "crypto": safe(src_crypto),
        "fleet": safe(src_fleet), "algos": safe(src_algos),
        "provider": safe(src_provider), "arb": safe(src_arb),
        "edgehunt": safe(src_edgehunt), "alerts": safe(src_alerts),
        "dsr": safe(src_dsr), "edgebots": safe(src_edgebots),
        "cryptomovers": safe(src_cryptomovers), "fiidii": safe(src_fiidii),
        "macd": safe(src_macd), "vol_regime": safe(src_vol_regime),
        "watchdog": dict(_watchdog_alive),
        "funding": safe(src_funding),
        "liquidations": safe(src_liquidations),
        "cockpit_watchdog": safe(src_cockpit_watchdog),
        "honesty": safe(src_honesty),
        "oa_fleet": safe(src_oa_fleet),
        "oa_legs": safe(src_oa_legs),
        "market_structure": safe(src_market_structure),
        "hermes": safe(src_hermes),
        "edgebots_live": safe(src_edgebots_live),
        "tournament": safe(src_tournament),
        "total_paper_pnl": total_paper,
    }

# ---------- HTTP ----------
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _send(self, body, ctype):
        b = body.encode() if isinstance(body, str) else body
        self.send_response(200); self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b))); self.end_headers()
        self.wfile.write(b)
    def do_GET(self):
        if self.path.startswith("/api/candles"):
            self._send(json.dumps(src_candles()), "application/json")
        elif self.path.startswith("/api/race"):
            self._send(json.dumps(src_race()), "application/json")
        elif self.path.startswith("/api/equity"):
            self._send(json.dumps(src_equity()), "application/json")
        elif self.path.startswith("/api/stream"):
            # SSE: push state every 1.5s
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            try:
                while True:
                    data = json.dumps(aggregate())
                    msg = f"data: {data}\n\n".encode()
                    self.wfile.write(msg)
                    self.wfile.flush()
                    time.sleep(1.5)
            except Exception:
                pass
            return
        elif self.path.startswith("/api/state"):
            self._send(json.dumps(aggregate()), "application/json")
        elif self.path == "/dashboard" or self.path == "/dashboard/":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(DASHBOARD_HTML.encode())
            return
        else:
            self._send(PAGE, "text/html; charset=utf-8")

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>P&amp;L Dashboard · ALICE</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0b0e14;color:#e8eaed;font:13px/1.5 -apple-system,Segoe UI,Roboto,Inter,sans-serif;padding:16px}
h1{font-size:15px;font-weight:800;letter-spacing:.4px;color:#f0b90b;margin-bottom:4px}
.sub{font-size:11px;color:#8b94a3;margin-bottom:16px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:12px}
.card{background:#131722;border:1px solid #24262c;border-radius:12px;padding:14px 16px}
.card-title{font:700 10px/1 ui-monospace,monospace;letter-spacing:.8px;text-transform:uppercase;color:#8b94a3;margin-bottom:12px;display:flex;align-items:center;gap:6px}
.dot{width:7px;height:7px;border-radius:50%;flex-shrink:0}
.dot.ok{background:#23b99a;box-shadow:0 0 6px #23b99a}
.dot.warn{background:#f0b90b;box-shadow:0 0 6px #f0b90b}
.dot.dead{background:#be4138;box-shadow:0 0 6px #be4138}
.row{display:flex;justify-content:space-between;align-items:center;padding:4px 0;border-bottom:1px solid #ffffff09;font-size:12px}
.row:last-child{border-bottom:none}
.label{color:#8b94a3}
.val{font:600 12px ui-monospace,monospace}
.pos{color:#23b99a}
.neg{color:#be4138}
.amb{color:#f0b90b}
.hero-pnl{text-align:center;padding:10px 0 14px}
.hero-pnl .num{font:800 36px ui-monospace,monospace}
.hero-pnl .cap{font-size:11px;color:#8b94a3;margin-top:2px}
.section-head{font:700 11px/1 -apple-system,sans-serif;color:#8b94a3;letter-spacing:.5px;text-transform:uppercase;margin:18px 0 8px}
a.navlink{color:#f0b90b;text-decoration:none;font-size:11px;font-weight:600}
a.navlink:hover{text-decoration:underline}
#refresh-ts{font-size:10px;color:#8b94a3;margin-left:auto}
.topbar{display:flex;align-items:center;gap:10px;margin-bottom:14px;flex-wrap:wrap}
.brand{font:800 17px -apple-system,sans-serif;color:#e8eaed}
.brand b{color:#f0b90b}
.pill{font:600 10px ui-monospace,monospace;padding:4px 9px;border-radius:999px;border:1px solid #24262c;background:#0e0f12;color:#8b94a3}
table{width:100%;border-collapse:collapse;font-size:11px}
th{text-align:left;font:600 10px ui-monospace,monospace;color:#8b94a3;padding:3px 4px;border-bottom:1px solid #24262c}
td{padding:3px 4px;border-bottom:1px solid #ffffff07;font:500 11px ui-monospace,monospace}
tr:last-child td{border-bottom:none}
.tag{display:inline-block;font:700 9px ui-monospace,monospace;padding:1px 5px;border-radius:4px}
.tag.gate{background:#23b99a22;color:#23b99a;border:1px solid #23b99a55}
.tag.kill{background:#be413822;color:#be4138;border:1px solid #be413855}
.tag.accum{background:#f0b90b22;color:#f0b90b;border:1px solid #f0b90b55}
.tag.review{background:#3b82f622;color:#3b82f6;border:1px solid #3b82f655}
</style>
</head>
<body>
<div class="topbar">
  <div class="brand">ALICE · <b>P&amp;L Dashboard</b></div>
  <a class="navlink" href="/">← Live Portal</a>
  <span id="refresh-ts">loading…</span>
</div>

<div id="root"><div style="color:#8b94a3;padding:40px;text-align:center">Loading…</div></div>

<script>
const $ = id => document.getElementById(id);
const money = v => (v == null ? '—' : (v >= 0 ? '+' : '') + '$' + v.toFixed(2));
const pct = v => (v == null ? '—' : (v >= 0 ? '+' : '') + v.toFixed(2) + '%');
const clr = v => v == null ? '' : (v > 0 ? 'pos' : v < 0 ? 'neg' : '');
const dotCls = fresh => fresh ? 'dot ok' : 'dot warn';
const tag = s => { const c = (s||'').toLowerCase(); return `<span class="tag ${c}">${(s||'?').toUpperCase()}</span>`; };

function row(label, valHtml) {
  return `<div class="row"><span class="label">${label}</span><span class="val">${valHtml}</span></div>`;
}

function card(title, dotOk, body) {
  return `<div class="card">
    <div class="card-title"><span class="${dotOk ? 'dot ok' : 'dot warn'}"></span>${title}</div>
    ${body}
  </div>`;
}

function render(d) {
  const arb = d.arb || {};
  const macd = d.macd || {};
  const eb = d.edgebots_live || {};
  const ebBots = eb.bots || [];
  const ms = d.market_structure || {};
  const sent = ms.sentiment || {};
  const oi = ms.oi || {};
  const mark = ms.mark || {};
  const iv = ms.iv || {};
  const cr = d.crypto || {};
  const fut = d.futures || {};
  const analyst = d.analyst || {};
  const vr = d.vol_regime || {};
  const fr = d.funding || {};
  const frRates = fr.rates || [];

  // --- Totals ---
  const totalPnl = d.total_paper_pnl;
  const arbPnl = round2((d.scalp || {}).realized);
  const macdPnl = round2(macd.realized_pnl);
  const futPnl = round2(((fut.main || 1000) - 1000));
  const analystPnl = round2((analyst.realized));
  const ebPnl = ebBots.reduce((s, b) => s + (b.pnl || 0), 0);

  // --- Section: summary ---
  const summaryHtml = `
    <div class="hero-pnl">
      <div class="num ${clr(totalPnl)}">${money(totalPnl)}</div>
      <div class="cap">Total paper P&amp;L across all tracks</div>
    </div>
    ${row('Scalp / arb track', `<span class="${clr(arbPnl)}">${money(arbPnl)}</span>`)}
    ${row('MACD algo realized', `<span class="${clr(macdPnl)}">${money(macdPnl)}</span>`)}
    ${row('Futures track', `<span class="${clr(futPnl)}">${money(futPnl)}</span>`)}
    ${row('Analyst realized', `<span class="${clr(analystPnl)}">${money(analystPnl)}</span>`)}
    ${row('Edge bots (funding+basis)', `<span class="${clr(ebPnl)}">${money(round2(ebPnl))}</span>`)}
  `;

  // --- Section: arb ---
  const bestEdge = (arb.locks && arb.locks.length) ? arb.locks[0].edge : null;
  const locks = (arb.locks || []).slice(0, 4);
  const arbHtml = `
    ${row('Open basket locks', `<span class="amb">${arb.open ?? '—'}</span>`)}
    ${row('Closed locks', `<span>${arb.closed ?? '—'}</span>`)}
    ${row('DataArb open', `<span>${arb.dataarb_open ?? '—'}</span>`)}
    ${row('Best lock edge', `<span class="${bestEdge > 0 ? 'pos' : ''}">${bestEdge != null ? (bestEdge * 100).toFixed(2) + '%' : '—'}</span>`)}
    ${locks.length ? `<div style="margin-top:8px;font-size:11px;color:#8b94a3">Top locks:</div>` + locks.map(l =>
      `<div class="row" style="padding-left:4px"><span class="label" style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${l.q}</span><span class="val pos">${(l.edge*100).toFixed(2)}%</span></div>`
    ).join('') : ''}
  `;

  // --- Section: MACD ---
  const macdSigs = macd.signals || {};
  const macdPos = macd.positions || {};
  const syms = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT'];
  const macdHtml = `
    ${row('Realized P&L', `<span class="${clr(macdPnl)}">${money(macdPnl)}</span>`)}
    <table style="margin-top:8px">
      <tr><th>Sym</th><th>Signal</th><th>RSI</th><th>Hist</th><th>Pos</th></tr>
      ${syms.map(s => {
        const sig = macdSigs[s] || {};
        const pos = macdPos[s];
        const sigDir = sig.signal || '—';
        const sigCls = sigDir === 'LONG' ? 'pos' : sigDir === 'SHORT' ? 'neg' : '';
        return `<tr>
          <td>${s.replace('USDT','')}</td>
          <td class="${sigCls}">${sigDir}</td>
          <td>${sig.rsi != null ? sig.rsi.toFixed(1) : '—'}</td>
          <td>${sig.macd_hist != null ? sig.macd_hist.toFixed(2) : '—'}</td>
          <td>${pos ? `<span class="pos">${pos.side || '?'} ${pos.size || ''}</span>` : '—'}</td>
        </tr>`;
      }).join('')}
    </table>
  `;

  // --- Section: Edge bots ---
  const ebHtml = ebBots.length ? ebBots.map(b => {
    const pnlCls = clr(b.pnl);
    return `<div class="row"><span class="label" style="text-transform:capitalize">${b.name}</span>
      <span class="val ${pnlCls}">${money(b.pnl)} · eq $${b.equity ?? '—'}</span></div>
      <div class="row" style="padding-left:8px;font-size:11px"><span class="label">${b.n_open ?? 0} open · ${b.n_closed ?? 0} closed</span></div>
      ${(b.top_pos || []).map(p => `<div class="row" style="padding-left:12px"><span class="label">${p.sym}</span>
        <span class="val ${p.ann >= 0 ? 'pos' : 'neg'}">${pct(p.ann)} ann · $${p.funding} collected</span></div>`).join('')}`;
  }).join('<div style="border-top:1px solid #24262c;margin:6px 0"></div>')
  : '<div class="row"><span class="label">No data</span></div>';

  // --- Section: Market Structure ---
  const msHtml = `
    ${row('Fear &amp; Greed', `<span class="${(sent.value||50)<30?'neg':(sent.value||50)>70?'pos':'amb'}">${sent.value ?? '—'} · ${sent.classification || '—'}</span>`)}
    ${row('BTC dom / ETH dom', `<span class="amb">${sent.btc_dom ?? '—'}% / ${sent.eth_dom ?? '—'}%</span>`)}
    ${row('BTC OI', `<span>${oi.BTC ? '$' + (oi.BTC.oi_b / 1e9).toFixed(2) + 'B' : '—'}</span>`)}
    ${row('ETH OI', `<span>${oi.ETH ? '$' + (oi.ETH.oi_b / 1e9).toFixed(2) + 'B' : '—'}</span>`)}
    ${row('BTC DVOL', `<span class="amb">${vr.btc_dvol != null ? vr.btc_dvol + '%' : '—'}</span>`)}
    ${row('ETH DVOL', `<span class="amb">${vr.eth_dvol != null ? vr.eth_dvol + '%' : '—'}</span>`)}
    ${row('IV regime (BTC)', `<span class="amb">${iv.BTC ? (iv.BTC.iv ?? iv.BTC).toFixed(1) + '%' : '—'}</span>`)}
    ${row('Vol regime', `<span>${vr.regime || '—'} · BTC HV ${vr.btc_hv != null ? vr.btc_hv + '%' : '—'}</span>`)}
  `;

  // --- Section: funding rates ---
  const frHtml = frRates.length ? `
    <table>
      <tr><th>Sym</th><th>8h rate</th><th>Ann rate</th></tr>
      ${frRates.map(r => `<tr>
        <td>${r.sym}</td>
        <td class="${r.rate_8h > 0 ? 'pos' : r.rate_8h < 0 ? 'neg' : ''}">${r.rate_8h}%</td>
        <td class="${r.rate_ann > 0 ? 'pos' : r.rate_ann < 0 ? 'neg' : ''}">${r.rate_ann}%</td>
      </tr>`).join('')}
    </table>
  ` : '<div class="row"><span class="label">No funding data</span></div>';

  // --- Section: Crypto prices ---
  const coins = [['bitcoin','BTC'],['ethereum','ETH'],['solana','SOL']];
  const cryptoHtml = `
    <table>
      <tr><th>Coin</th><th>Price</th><th>24h chg</th></tr>
      ${coins.map(([id, sym]) => {
        const c = cr[id] || {};
        return `<tr>
          <td>${sym}</td>
          <td>$${c.p != null ? c.p.toLocaleString('en-US', {maximumFractionDigits:2}) : '—'}</td>
          <td class="${clr(c.c)}">${pct(c.c)}</td>
        </tr>`;
      }).join('')}
    </table>
  `;

  // --- Section: Tournament leaderboard ---
  const t = d.tournament || {};
  let tournamentHtml;
  if (t.error || !t.board) {
    tournamentHtml = `<div class="row"><span class="label">NO FEED</span><span class="val">${t.error || 'no data'}</span></div>`;
  } else {
    const typeTag = (b) => b.smart
      ? '<span style="color:#23b99a;font-weight:700">EDGE</span>'
      : '<span style="color:#666">DIR</span>';
    const rows = t.board.map((b, i) => `
      <tr style="${i === 0 ? 'background:#1a2010' : ''}">
        <td style="color:#666">${String(i + 1).padStart(2, '0')}</td>
        <td style="color:#f0b90b">${b.name}</td>
        <td>${typeTag(b)}</td>
        <td style="color:#666;font-size:11px">${b.tag}</td>
        <td style="color:${b.pos === 'active' || b.pos === 'LONG' ? '#23b99a' : '#be4138'}">${b.pos.toUpperCase()}</td>
        <td style="color:${b.ret_pct > 0 ? '#23b99a' : '#be4138'};text-align:right">${b.ret_pct > 0 ? '+' : ''}${b.ret_pct.toFixed(3)}%</td>
        <td style="text-align:right">${b.winpct.toFixed(1)}</td>
        <td style="text-align:right;color:#666">${b.cyc}</td>
      </tr>`).join('');
    tournamentHtml = `
      <div class="row" style="margin-bottom:6px">
        <span class="label">Leader</span><span class="val" style="color:#f0b90b">${t.leader || '—'}</span>
        <span class="label" style="margin-left:12px">BTC</span><span class="val">$${(t.btc||0).toLocaleString()}</span>
        <span class="label" style="margin-left:12px">ETH</span><span class="val">$${(t.eth||0).toLocaleString()}</span>
        <span class="label" style="margin-left:12px">BTC fund</span><span class="val">${t.funding_btc != null ? (t.funding_btc * 1e5).toFixed(3) + 'e-5' : '—'}</span>
        <span class="label" style="margin-left:12px">Cycles</span><span class="val">${t.cycles||'—'}</span>
      </div>
      <table>
        <tr><th>#</th><th>Strategy</th><th>Type</th><th>Tag</th><th>Pos</th><th style="text-align:right">Ret%</th><th style="text-align:right">Win%</th><th style="text-align:right">Cyc</th></tr>
        ${rows}
      </table>`;
  }

  $('root').innerHTML = `
    <div class="grid">
      ${card('Total P&amp;L Summary', true, summaryHtml)}
      ${card('Strategy Tournament · OpenAlice', !t.error && !!t.board, tournamentHtml)}
      ${card('Arb Track · Basket Locks', arb.fresh, arbHtml)}
      ${card('MACD Algo Lab', macd.fresh, macdHtml)}
      ${card('Edge Bots · Funding / Basis', eb.fresh, ebHtml)}
      ${card('Market Structure', ms.fresh, msHtml)}
      ${card('Perp Funding Rates', fr.fresh, frHtml)}
      ${card('Crypto Prices · Live', true, cryptoHtml)}
    </div>
  `;

  $('refresh-ts').textContent = 'Updated ' + new Date().toLocaleTimeString();
}

function round2(v) { return v != null ? Math.round(v * 100) / 100 : null; }

async function refresh() {
  try {
    const d = await fetch('/api/state').then(r => r.json());
    render(d);
  } catch(e) {
    $('refresh-ts').textContent = 'fetch error: ' + e.message;
  }
}

setInterval(refresh, 5000);
refresh();
</script>
</body>
</html>"""

PAGE = r"""<!DOCTYPE html><html data-theme="dark"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ALICE · Live Portal</title>
<script src="https://unpkg.com/lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js"></script>
<style>
:root{--bg:#0b0e14;--card:#131722;--bg2:#0e0f12;--bd:#24262c;--tx:#e8eaed;--mut:#8b94a3;
--acc:#3b82f6;--amb:#f0b90b;--grn:#23b99a;--red:#be4138;--pur:#6b5bc2}
*{box-sizing:border-box;margin:0}
html{scroll-behavior:smooth}
body{background:var(--bg);color:var(--tx);font:13px/1.45 -apple-system,Segoe UI,Roboto,Inter,sans-serif;padding:14px;-webkit-font-smoothing:antialiased;-moz-osx-font-smoothing:grayscale;text-rendering:optimizeLegibility}
::-webkit-scrollbar{width:10px;height:10px}
::-webkit-scrollbar-track{background:transparent}
::-webkit-scrollbar-thumb{background:#2a2d35;border-radius:8px;border:2px solid var(--bg)}
::-webkit-scrollbar-thumb:hover{background:#3a3e47}
a{color:inherit;text-decoration:none}
.top{display:flex;align-items:center;gap:14px;margin-bottom:14px;flex-wrap:wrap}
.brand{font-weight:800;font-size:18px;letter-spacing:.3px}
.brand b{color:var(--amb)}
.pill{font:600 11px/1 ui-monospace,monospace;padding:6px 10px;border-radius:999px;border:1px solid var(--bd);background:var(--bg2);color:var(--mut)}
.hero{margin-left:auto;text-align:right}
.hero .big{font:800 26px ui-monospace,monospace}
.hero small{color:var(--mut)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:12px}
.card{background:var(--card);border:1px solid var(--bd);border-radius:12px;padding:13px 14px;min-height:120px}
.card h3{font-size:11px;letter-spacing:.6px;text-transform:uppercase;color:var(--mut);font-weight:700;display:flex;align-items:center;gap:8px;margin-bottom:10px}
.dot{width:8px;height:8px;border-radius:50%;background:var(--grn);box-shadow:0 0 8px var(--grn)}
.dot.stale{background:var(--amb);box-shadow:0 0 8px var(--amb)}
.dot.dead{background:var(--red);box-shadow:0 0 8px var(--red)}
.age{margin-left:auto;font:500 10px ui-monospace,monospace;color:var(--mut)}
.kv{display:flex;justify-content:space-between;padding:3px 0;border-bottom:1px dashed #ffffff0d}
.kv b{font:600 13px ui-monospace,monospace}
.num{font:700 22px ui-monospace,monospace}
.pos{color:var(--grn)}.neg{color:var(--red)}.amb{color:var(--amb)}
.row{display:flex;justify-content:space-between;gap:8px;padding:2px 0;font-size:12px}
.row span:first-child{color:var(--mut);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.mono{font-family:ui-monospace,monospace}
.tick{display:flex;gap:16px;flex-wrap:wrap}
.tick .a{font:700 14px ui-monospace,monospace}
.warn{color:var(--amb);font-size:11px;margin-top:6px}
.foot{color:var(--mut);font-size:11px;margin-top:14px;text-align:center}
.candles{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:12px}
.ch{background:var(--card);border:1px solid var(--bd);border-radius:12px;padding:10px 12px 8px}
.ch .lbl{display:flex;align-items:center;gap:8px;font:700 12px ui-monospace,monospace;color:var(--mut);margin-bottom:6px}
.ch .cpx{margin-left:auto;font:700 13px ui-monospace,monospace}
.chart{width:100%;height:200px}
.equity{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:12px}
.eq{height:180px}
@media(max-width:900px){.candles,.equity{grid-template-columns:1fr}}
.card{transition:transform .18s cubic-bezier(.2,.7,.3,1),box-shadow .18s ease,border-color .35s ease}
.card:hover{transform:translateY(-2px);box-shadow:0 8px 24px #00000055;border-color:#3a3e47}
.card.in{animation:cardin .4s cubic-bezier(.2,.7,.3,1) both}
.card.upd{animation:cardpulse .7s ease}
@keyframes cardin{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}
@keyframes cardpulse{0%{box-shadow:0 0 0 0 #3b82f644}60%{box-shadow:0 0 0 3px #3b82f600}100%{box-shadow:0 0 0 0 #3b82f600}}
.num,.kv b,.row b,.hero .big,.cpx{transition:color .35s ease}
.row{transition:background .15s ease;border-radius:5px}
.row:hover{background:#ffffff07}
.dot{animation:dotpulse 2.4s ease-in-out infinite}
.dot.stale,.dot.dead{animation:none}
@keyframes dotpulse{0%,100%{box-shadow:0 0 5px var(--grn);opacity:.85}50%{box-shadow:0 0 11px var(--grn);opacity:1}}
.liveage{color:var(--grn)!important;font-weight:700}
.pill{transition:color .3s ease,border-color .3s ease,background .3s ease}
.chart,.eq{transition:opacity .3s ease}
</style></head><body>
<div class="top">
  <div class="brand">▲ <b>ALICE</b> Live Portal</div>
  <div class="pill" id="clock">—</div>
  <div class="pill" id="wdpill">⚙ wd —</div>
  <div class="pill" id="cryptobar">crypto…</div>
  <div class="hero"><div class="big" id="hpnl">—</div><small>total paper P&L (all books)</small></div>
</div>
<div class="candles">
  <div class="ch"><div class="lbl">BTC/USDT · 5m <span class="cpx" id="px-BTCUSDT">—</span></div><div class="chart" id="ch-BTCUSDT"></div></div>
  <div class="ch"><div class="lbl">ETH/USDT · 5m <span class="cpx" id="px-ETHUSDT">—</span></div><div class="chart" id="ch-ETHUSDT"></div></div>
  <div class="ch"><div class="lbl">SOL/USDT · 5m <span class="cpx" id="px-SOLUSDT">—</span></div><div class="chart" id="ch-SOLUSDT"></div></div>
</div>
<div class="equity">
  <div class="ch"><div class="lbl">Scalp · cumulative P&L <span class="cpx" id="eq-scalp">—</span></div><div class="eq" id="ch-scalp"></div></div>
  <div class="ch"><div class="lbl">Futures · cumulative P&L <span class="cpx" id="eq-futures">—</span></div><div class="eq" id="ch-futures"></div></div>
</div>
<div class="ch" style="margin-bottom:12px">
  <div class="lbl">🏁 Algo Race · avg of BTC/ETH/SOL · since-inception % (top 6) <span class="cpx" id="race-days"></span></div>
  <div id="ch-race" style="width:100%;height:240px"></div>
  <div id="race-legend" style="font:600 11px ui-monospace,monospace;color:var(--mut);margin-top:6px;display:flex;flex-wrap:wrap;gap:4px 12px"></div>
</div>
<div class="grid" id="grid">loading…</div>
<div class="foot">keyless · read-only · paper · SSE push every 1.5s — broadcasting <span id="srccount">—</span> live sources</div>
<script>
const $=s=>document.querySelector(s);
const money=x=>(x==null?'—':(x>=0?'+':'')+'$'+(+x).toFixed(2));
const cls=x=>x==null?'':x>=0?'pos':'neg';
function dot(s){return s&&s.fresh?'<span class="dot"></span>':(s&&s.error?'<span class="dot dead"></span>':'<span class="dot stale"></span>')}
function age(s){if(!s)return'';if(s.error)return'<span class="age">err</span>';if(s.age_s==null)return'';const a=s.age_s;const t=a<60?'live':a<5400?Math.round(a/60)+'m ago':Math.round(a/3600)+'h ago';return'<span class="age'+(a<60?' liveage':'')+'">'+t+'</span>'}
function card(title,s,body){const id='c-'+title.replace(/[^a-zA-Z0-9]+/g,'-').toLowerCase();return`<div class="card" id="${id}"><h3>${dot(s)} ${title} ${age(s)}</h3>${s&&s.error?'<div class="warn">⚠ '+s.error+'</div>':body}</div>`}
function rows(arr,f){return (arr&&arr.length?arr:[]).map(f).join('')||'<div class="row"><span>—</span></div>'}
// keyed diff paint: only touch cards whose HTML actually changed → no flicker, hover/scroll preserved
let _cards={};
function paint(arr){
 const grid=$('#grid'); let prev=null; const seen={};
 for(const htmlStr of arr){
  const t=document.createElement('template'); t.innerHTML=htmlStr.trim();
  const node=t.content.firstChild; const id=node.id; seen[id]=1;
  const ex=document.getElementById(id);
  if(!ex){ if(prev) prev.after(node); else grid.prepend(node); node.classList.add('in'); prev=node; }
  else { if(_cards[id]!==htmlStr){ ex.innerHTML=node.innerHTML; ex.classList.remove('upd'); void ex.offsetWidth; ex.classList.add('upd'); } prev=ex; }
  _cards[id]=htmlStr;
 }
 for(const c of [...grid.children]){ if(c.id&&!seen[c.id]){ c.remove(); delete _cards[c.id]; } }
}

async function tick(){let d;try{d=await(await fetch('/api/state')).json()}catch(e){return}render(d)}
function render(d){
 $('#clock').textContent=new Date(d.ts*1000).toLocaleTimeString();
 $('#hpnl').textContent=money(d.total_paper_pnl); $('#hpnl').className='big '+cls(d.total_paper_pnl);
 // watchdog pill
 const wd=d.watchdog||{};
 $('#wdpill').textContent='⚙ wd '+(wd.alive?'●':'○');
 $('#wdpill').style.color=wd.alive?'var(--grn)':'var(--red)';
 // crypto bar
 const c=d.crypto||{}; const nm={bitcoin:'BTC',ethereum:'ETH',solana:'SOL'};
 $('#cryptobar').innerHTML=Object.keys(nm).map(k=>c[k]?`${nm[k]} $${(+c[k].p).toLocaleString()} <span class="${cls(c[k].c)}">${c[k].c>=0?'+':''}${c[k].c}%</span>${c[k].live?'<span style="color:var(--grn);font-size:10px"> ●</span>':''}`:''  ).filter(Boolean).join(' · ')||'crypto —';
 const g=[];
 // Algo Lab — live 10-algo race (the "who wins" board)
 const al=d.algos||{};
 g.push(card('Algo Lab · live race',al,
  `${al.champion?`<div class="row"><span class="amb">🏆 champion</span><b class="amb mono">${al.champion}</b></div>`:''}
   <div class="row"><span>${(al.results||[]).length} algos · ${(al.assets||[]).length} assets · ${al.days??0}d</span><b class="mono ${al.beat?'amb':''}">${al.beat??'—'} beat B&H</b></div>
   <div class="row"><span>📊 <b class="${al.regime=='up'?'pos':al.regime=='down'?'neg':'amb'}">${(al.regime||'—').toUpperCase()}</b> regime → best:</span><b class="mono amb">${al.regime_leader||'—'}</b></div>
   <div class="row"><span>🛡 robust <span style="color:var(--mut);font-size:10px">(all 3 assets +)</span></span><b class="mono ${al.robust_count?'pos':''}">${al.robust_count??0}/${(al.results||[]).length} → ${al.robust_leader||'none'}</b></div>
   <div style="margin-top:4px">${rows(al.results,(r,i)=>`<div class="row"><span>${i+1}. ${r.name}${r.robust?' <span class="pos" title="positive on all 3 assets">✓</span>':''}${r.pos?' <span class="amb">●</span>':''}</span><b class="${cls(r.pnl)} mono">${r.pnl>=0?'+':''}${r.pnl}%</b></div>`)}</div>`));
 // Polymarket scalp
 const sc=d.scalp||{};
 g.push(card('Polymarket scalp',sc,
  `<div class="num ${cls(sc.realized)}">${money(sc.realized)}</div>
   <div class="row"><span>open positions</span><b class="mono">${sc.open??'—'}</b></div>
   <div class="row"><span>closed exits</span><b class="mono">${sc.closed??'—'}</b></div>
   <div style="margin-top:8px">${rows(sc.legs,l=>`<div class="row"><span>${l.leg} (${l.n})</span><b class="${cls(l.pnl)} mono">${money(l.pnl)}</b></div>`)}</div>`));
 // Futures
 const f=d.futures||{};
 g.push(card('Futures bot',f,
  `<div class="row"><span>MAIN</span><b class="${cls((f.main||0)-1000)} mono">$${(f.main||0).toFixed(2)}</b></div>
   <div class="row"><span>RUNNER (off)</span><b class="${cls((f.runner||0)-1000)} mono">${f.runner!=null?'$'+f.runner.toFixed(2):'—'}</b></div>
   <div class="row"><span>LEV (off)</span><b class="${cls((f.lev||0)-1000)} mono">${f.lev!=null?'$'+f.lev.toFixed(2):'—'}</b></div>
   <div class="row"><span>closed</span><b class="mono">${f.closed??'—'}</b></div>`));
 // Resolved trades
 const r=d.resolved||{};
 g.push(card('Resolved (held to settle)',r,
  `<div class="num ${cls(r.pnl)}">${money(r.pnl)}</div>
   <div class="row"><span>resolved trades</span><b class="mono">${r.n??'—'}</b></div>
   <div class="row"><span>win rate</span><b class="mono amb">${r.wr??'—'}%</b></div>`));
 // Analyst track (evidence-based >5% edge book)
 const an=d.analyst||{};
 g.push(card('Analyst track',an,
  `<div class="num ${cls(an.total)}">${money(an.total)}</div>
   <div class="row"><span>realized</span><b class="${cls(an.realized)} mono">${money(an.realized)}</b></div>
   <div class="row"><span>unrealized MTM</span><b class="${cls(an.unreal)} mono">${money(an.unreal)}</b></div>
   <div class="row"><span>open / settled</span><b class="mono">${an.n_open??'—'} / ${an.n_settled??'—'}</b></div>
   <div style="margin-top:6px">${rows(an.positions,p=>`<div class="row"><span>${p.q}</span><b class="mono amb">${p.side||''}${p.edge!=null?' +'+p.edge+'%':''}</b></div>`)}</div>`));
 // Bloomberg ports
 const p=d.ports||{}; const h=p.headline||{};
 g.push(card('Ports terminal',p,
  `${Object.keys(h).slice(0,3).map(k=>`<div class="row"><span>${k}</span><b class="mono">${h[k]}</b></div>`).join('')||'<div class="row"><span>no headline</span></div>'}
   <div style="margin-top:6px;color:var(--grn);font-size:11px">▲ movers</div>
   ${rows(p.winners,w=>`<div class="row"><span>${w.q}</span><b class="pos mono">${money(w.u)}</b></div>`)}
   ${rows(p.losers,w=>`<div class="row"><span>${w.q}</span><b class="neg mono">${money(w.u)}</b></div>`)}`));
 // Money-flow
 const mf=d.moneyflow||{};
 g.push(card('Money-flow signals',mf,
  `<div class="row"><span>recent records</span><b class="mono">${mf.count??0}</b></div>
   ${rows(mf.recent,x=>`<div class="row"><span>${(x.asset||x.symbol||x.venue||x.event||'rec')}</span><b class="mono">${(x.direction||x.signal||x.kind||'')}</b></div>`)}`));
 // New markets
 const nm2=d.newmarket||{};
 g.push(card('New-market radar',nm2,
  `<div class="row"><span>tracked snapshots</span><b class="mono">${nm2.count??0}</b></div>
   <div class="row"><span>tradeable leads</span><b class="mono ${nm2.leads&&nm2.leads.length?'amb':''}">${nm2.leads?nm2.leads.length:0}</b></div>
   ${rows((nm2.recent||[]).slice(0,5),x=>`<div class="row"><span>${(x.question||x.q||x.slug||'mkt').slice(0,38)}</span><b class="mono">${x.kind||''}</b></div>`)}`));
 // Fleet
 const fl=d.fleet||{};
 g.push(card('Agent fleet',fl,
  `<div class="num ${fl.bad?'amb':'pos'}">${fl.ok??'—'}/${fl.total??'—'}</div>
   <div class="row"><span>jobs healthy</span><b class="mono">${fl.ok??0}</b></div>
   <div class="row"><span>failing</span><b class="mono ${fl.bad?'neg':''}">${fl.bad??0}</b></div>
   ${rows(fl.fails,x=>`<div class="row"><span>${x.job}</span><b class="neg mono">exit ${x.code}</b></div>`)}`));
 // LLM Provider Health
 const pv=d.provider||{};
 const pvOk=pv.status==='HEALTHY';
 const TIERS=['nvidia','groq','cerebras','sambanova','openrouter','ollama'];
 const tierDots=TIERS.map(t=>{const up=(pv.tiers||{})[t];return`<span style="color:${up?'var(--grn)':'var(--red)'}">${up?'●':'○'}</span><span style="color:var(--mut);font-size:11px">${t}</span>`;}).join('&nbsp;');
 g.push(card('LLM provider chain',pv,
  `<div class="row"><span>status</span><b class="mono ${pvOk?'pos':'amb'}">${pv.status||'—'}</b></div>
   <div class="row"><span>down</span><b class="neg mono">${(pv.down||[]).join(', ')||'none'}</b></div>
   <div class="row"><span>degraded streak</span><b class="mono">${pv.streak??'—'} checks</b></div>
   <div style="margin-top:6px;display:flex;flex-wrap:wrap;gap:4px 10px;font:600 11px ui-monospace,monospace">${tierDots}</div>`));
 // OpenAlice cockpit guards — 24/7 watch-heal + honesty sentinel ($0)
 const cw=d.cockpit_watchdog||{}, hn=d.honesty||{};
 const guardsBad=((cw.unhealed||[]).length>0)||((hn.n_flags||0)>0);
 g.push(card('Cockpit guards', {fresh:(cw.fresh&&hn.fresh), age_s:Math.max(cw.age_s||0,hn.age_s||0)},
  `<div class="num ${guardsBad?'neg':'pos'}">${guardsBad?'CHECK':'OK'}</div>
   <div class="row"><span>🛡 watchdog</span><b class="mono ${(cw.unhealed||[]).length?'neg':'pos'}">${cw.ok?'ok':'—'} · ${cw.monitored??0} monitored</b></div>
   <div class="row"><span>self-healed (last cycle)</span><b class="mono ${(cw.healed||[]).length?'amb':''}">${(cw.healed||[]).length}</b></div>
   ${(cw.unhealed||[]).length?`<div class="row"><span>UNHEALED</span><b class="neg mono">${cw.unhealed.join(', ')}</b></div>`:''}
   <div class="row"><span>✅ honesty sentinel</span><b class="mono ${(hn.n_flags||0)?'neg':'pos'}">${hn.ok?'clean':(hn.n_flags+' flag(s)')}</b></div>
   ${rows((hn.flags||[]).slice(0,4),x=>`<div class="row"><span class="neg" style="font-size:11px">${String(x).slice(0,50)}</span></div>`)}`));
 // OpenAlice paper cockpit fleet
 const of=d.oa_fleet||{};
 g.push(card('OpenAlice cockpit', of,
  `<div class="num ${(of.total_pnl||0)>=0?'pos':'neg'}">$${Math.round(of.total_pnl||0).toLocaleString()}</div>
   <div class="row"><span>fleet equity</span><b class="mono">$${Math.round(of.equity||0).toLocaleString()}</b></div>
   <div class="row"><span>accounts</span><b class="mono">${of.n??0}</b></div>
   <div class="row"><span>structural vs control</span><b class="mono ${of.struct_beats?'pos':'amb'}">${of.struct_beats??0}/${of.struct_n??0} beat random</b></div>
   <div class="row"><span>max leverage</span><b class="mono ${(of.max_lev||0)>3?'neg':'pos'}">${(of.max_lev??0).toFixed(1)}x ${(of.max_lev||0)>3?'⚠ '+(of.max_lev_acct||''):''}</b></div>
   ${rows((of.top||[]).slice(0,4),x=>`<div class="row"><span>${x.label} <span style="color:var(--mut);font-size:11px">${x.type||''}</span></span><b class="mono ${(x.pnl||0)>=0?'pos':'neg'}">$${Math.round(x.pnl||0)}</b></div>`)}`));
 // OpenAlice 5:1 candle legs
 const ol=d.oa_legs||{};
 g.push(card('Candle legs · 5:1 bracket', ol,
  `<div class="row"><span>reward:risk</span><b class="mono">${ol.rr??5}:1 · breakeven ${ol.breakeven??16.7}%</b></div>
   ${rows((ol.legs||[]),x=>`<div class="row"><span>${x.leg} <span style="color:var(--mut);font-size:11px">${x.pos||''}</span></span><b class="mono ${x.w&&x.w/x.n>0.167?'pos':''}">${x.w}/${x.n} tgt-hit</b></div>`)}`));
 // Arb track (basket locks + dataarb)
 const ab=d.arb||{};
 g.push(card('Arb track · basket locks',ab,
  `<div class="row"><span>open locks</span><b class="mono ${ab.open?'amb':''}">${ab.open??'—'}</b></div>
   <div class="row"><span>closed / dataarb open</span><b class="mono">${ab.closed??0} / ${ab.dataarb_open??0}</b></div>
   <div style="margin-top:6px">${rows(ab.locks||[],l=>`<div class="row"><span title="${l.q}">${l.q}</span><b class="mono amb">${l.kind} ${l.edge>0?'+':''}${(l.edge*100).toFixed(1)}% · ${l.n}way</b></div>`)}</div>`));
 // Edge hunt
 const eh=d.edgehunt||{};
 g.push(card('Edge hunt · resolution leads',eh,
  `<div class="row"><span>last hunt</span><b class="mono">${eh.last_hunt||'—'}</b></div>
   <div class="row"><span>dossiers found</span><b class="mono ${eh.count?'amb':''}">${eh.count??0}</b></div>
   ${rows(eh.dossiers||[],x=>`<div class="row"><span>${x.q||'—'}</span><b class="mono">${x.verdict}</b></div>`)}`));
 // Alerts
 const al2=d.alerts||{};
 g.push(card('Alert log · pnl_alert',al2,
  `<div class="row"><span>logged alerts</span><b class="mono">${al2.count??0}</b></div>
   <div style="margin-top:4px">${(al2.recent||[]).map(l=>`<div style="font:11px ui-monospace,monospace;color:var(--mut);padding:1px 0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${l}</div>`).join('')||'<div class="row"><span>—</span></div>'}</div>`));
 // DSR gate distances
 const dr=d.dsr||{};
 const statusCls={GATE:'pos',KILL:'neg',ACCUM:'mut',REVIEW:'amb'};
 g.push(card('DSR · gate distances',dr,
  `<div class="row"><span>tracked legs</span><b class="mono">${dr.total??'—'}</b></div>
   <div style="margin-top:4px">${rows(dr.legs||[],l=>`<div class="row"><span>${l.leg} (n=${l.n}, gap=${l.gap})</span><b class="mono ${statusCls[l.status]||''}">${l.mean>=0?'+':''}${l.mean} <span style="font-size:10px">${l.status}</span></b></div>`)}</div>`));
 // Edge-bots scoreboard
 const eb=d.edgebots||{};
 const verdMap={ACCUMULATING:'amb',PASS:'pos',FAIL:'neg'};
 g.push(card('Edge-bots · funding/basis',eb,
  `${rows(eb.bots||[],b=>`<div class="row"><span>${b.name} (${b.open} open / ${b.closed} closed)</span><b class="mono ${cls(b.pnl)}">${money(b.pnl)}</b></div>`)}
   <div style="margin-top:6px">${Object.entries(eb.verdicts||{}).map(([k,v])=>`<div class="row"><span>${k}</span><b class="mono ${verdMap[v]||''}">${v}</b></div>`).join('')}</div>
   ${eb.near&&Object.keys(eb.near).length?'<div class="warn">⚡ '+Object.values(eb.near)[0]+'</div>':''}`));
 // Crypto movers
 const cm=d.cryptomovers||{};
 const tg=cm.top_gainer||{}; const tl=cm.top_loser||{};
 g.push(card('Crypto movers · 30 coins',cm,
  `<div class="row"><span>avg 24h</span><b class="mono ${cls(cm.avg_chg24h)}">${cm.avg_chg24h!=null?(cm.avg_chg24h>=0?'+':'')+cm.avg_chg24h+'%':'—'}</b></div>
   <div class="row"><span>🟢 top gainer</span><b class="pos mono">${tg.id||'—'} +${tg.pct||0}%</b></div>
   <div class="row"><span>🔴 top loser</span><b class="neg mono">${tl.id||'—'} ${tl.pct||0}%</b></div>
   <div class="row"><span>big movers (>3%)</span><b class="mono amb">${cm.n_movers??0}</b></div>
   <div style="margin-top:4px">${rows(cm.movers||[],m=>`<div class="row"><span>${m.sym||m.id}</span><b class="mono ${cls(m.pct)}">${m.pct>=0?'+':''}${m.pct}%</b></div>`)}</div>`));
 // FII/DII flow
 const fd=d.fiidii||{};
 g.push(card('FII/DII · institutional flow',fd,
  `<div class="row"><span>records logged</span><b class="mono">${fd.count??0}</b></div>
   ${rows(fd.recent||[],r=>{const fii=r.fii_net_cr!=null?r.fii_net_cr:(r.fii!=null?r.fii:null);const dii=r.dii_net_cr!=null?r.dii_net_cr:(r.dii!=null?r.dii:null);return`<div class="row"><span>${(r.date||r.ts||'').toString().slice(0,10)}</span><b class="mono"><span class="${cls(fii)}">FII ${fii!=null?(fii>=0?'+':'')+fii:'—'}</span> <span class="${cls(dii)}">DII ${dii!=null?(dii>=0?'+':'')+dii:'—'}</span></b></div>`;})}` ));
 // Perp funding rates card
 const fr=d.funding||{};
 g.push(card('Perp funding rates · live',fr,
  `${rows(fr.rates||[],r=>`<div class="row"><span>${r.sym} · 8h</span><b class="mono ${r.rate_8h>=0?'neg':'pos'}">${r.rate_8h>=0?'+':''}${r.rate_8h}% <span style="color:var(--mut)">(${r.rate_ann>=0?'+':''}${r.rate_ann}% ann)</span></b></div>`)}`));
 // Market Structure (OI + IV + Fear&Greed + dominance)
 const ms=d.market_structure||{};
 const sent=ms.sentiment||{};
 const fgv=sent.fg_value??null;
 const fgCls=fgv==null?'':fgv>=60?'pos':fgv<=40?'neg':'amb';
 g.push(card('Market structure · live',ms,
  `<div class="row"><span>😨 Fear &amp; Greed</span><b class="mono ${fgCls}">${fgv??'—'} <span style="font-size:11px">${sent.fg_class||''}</span></b></div>
   <div class="row"><span>BTC dom / ETH dom</span><b class="mono">${sent.btc_dom??'—'}% / ${sent.eth_dom??'—'}%</b></div>
   <div class="row"><span>total mcap</span><b class="mono ${cls(sent.mcap_chg_24h)}">$${sent.total_mcap_b??'—'}B <span style="font-size:10px">${sent.mcap_chg_24h!=null?(sent.mcap_chg_24h>=0?'+':'')+sent.mcap_chg_24h+'%':''}</span></b></div>
   <div style="margin-top:6px">${Object.entries(ms.oi||{}).map(([s,v])=>`<div class="row"><span>${s} OI</span><b class="mono">$${v.oi_b}B</b></div>`).join('')}</div>
   <div style="margin-top:4px">${Object.entries(ms.iv||{}).map(([s,v])=>`<div class="row"><span>${s} ATM IV</span><b class="mono amb">${v.iv??'—'}%</b></div>`).join('')}</div>`));
 // Liquidations feed
 const lq=d.liquidations||{};
 g.push(card('Liquidations · live',lq,
  `<div class="row"><span>events captured</span><b class="mono">${lq.count??0}</b></div>
   <div style="margin-top:4px">${rows(lq.recent||[],r=>{const notional=(r.qty*r.price/1000).toFixed(0);return`<div class="row"><span>${r.sym} ${r.side=='BUY'?'SHORT liq':'LONG liq'}${r.big?' 🔥':''}</span><b class="mono ${r.side=='BUY'?'pos':'neg'}">$${notional}k</b></div>`})}`));
 // Hermes news
 const hm=d.hermes||{};
 g.push(card('Hermes · breaking news',hm,
  `<div class="row"><span>headlines</span><b class="mono">${hm.count??0}</b></div>
   <div style="margin-top:4px">${rows(hm.news||[],x=>`<div class="row"><span style="font-size:11px">${x.title}</span><b class="mono" style="font-size:10px;white-space:nowrap">${x.source}</b></div>`)}`));
 // Edge-bots live equity
 const el=d.edgebots_live||{};
 g.push(card('Edge-bots · live equity',el,
  `${rows(el.bots||[],b=>b.error?`<div class="row"><span>${b.name}</span><b class="neg mono">err</b></div>`:`
   <div class="row"><span>${b.name} · $${b.equity} (${b.n_open} open / ${b.n_closed} closed)</span><b class="mono ${cls(b.pnl)}">${b.pnl>=0?'+':''}$${b.pnl}</b></div>
   ${(b.top_pos||[]).map(p=>`<div class="row" style="padding-left:10px"><span>${p.sym}</span><b class="mono ${p.ann>=0?'neg':'pos'}">${p.ann>=0?'+':''}${p.ann}% ann · $${p.funding} collected</b></div>`).join('')}`)}`));
 // MACD Signals
 const mc=d.macd||{};
 const SIG_COL={'BUY':'var(--grn)','SELL':'var(--red)','HOLD':'var(--mut)'};
 g.push(card('MACD Signals · BTC/ETH/SOL',mc,
  `<div class="row"><span>realized P&L</span><b class="${cls(mc.realized_pnl)} mono">${money(mc.realized_pnl)}</b></div>
   <div style="margin-top:6px">${Object.entries(mc.signals||{}).map(([sym,s])=>{
    const col=SIG_COL[s.signal]||'var(--mut)';
    const pos=(mc.positions||{})[sym]||{};
    const upnl=pos.unrealized_pnl!=null?pos.unrealized_pnl:null;
    return `<div class="row"><span class="mono" style="color:var(--mut)">${sym.replace('USDT','')}</span>
     <span style="color:${col};font-weight:700;font-family:ui-monospace,monospace">${s.signal}</span>
     <span class="mono" style="color:var(--mut)">hist ${(+s.hist).toFixed(3)}</span>
     <span class="mono" style="color:var(--mut)">${pos.side||'flat'}</span>
     <b class="${cls(upnl)} mono">${upnl!=null?money(upnl):''}</b></div>`;
   }).join('')}</div>`));
 // Vol Regime
 const vr=d.vol_regime||{};
 const VR_COL={'elevated':'var(--red)','normal':'var(--amb)','suppressed':'var(--grn)'};
 const vrCol=VR_COL[vr.iv_regime]||'var(--mut)';
 g.push(card('Vol Regime · IV vs RV',vr,
  `<div class="row"><span>IV regime</span><b class="mono" style="color:${vrCol}">${(vr.iv_regime||'—').toUpperCase()}</b></div>
   <div class="row"><span>BTC DVOL</span><b class="mono amb">${vr.btc_dvol??'—'}%</b></div>
   <div class="row"><span>ETH DVOL</span><b class="mono amb">${vr.eth_dvol??'—'}%</b></div>
   <div class="row"><span>BTC IV-RV spread</span><b class="mono ${(vr.iv_rv_spread_btc||0)>0?'pos':'neg'}">${vr.iv_rv_spread_btc??'—'}%</b></div>
   <div class="row"><span>ETH IV-RV spread</span><b class="mono ${(vr.iv_rv_spread_eth||0)>0?'pos':'neg'}">${vr.iv_rv_spread_eth??'—'}%</b></div>
   <div class="row"><span>BTC HV20</span><b class="mono">${vr.btc_hv_pct??'—'}%</b></div>
   <div class="row"><span>ETH HV20</span><b class="mono">${vr.eth_hv_pct??'—'}%</b></div>`));
 // Strategy Tournament · OpenAlice (live paper race, who-wins leaderboard)
 const tm=d.tournament||{};
 g.push(card('🏆 Strategy Tournament · OpenAlice',tm,
  (tm.board&&tm.board.length)?
  `<div class="row"><span class="amb">🥇 leader</span><b class="amb mono">${tm.leader||'—'}</b></div>
   <div class="row"><span>${tm.cycles??'—'} cycles · ${tm.board.length} strats</span><b class="mono">BTC funding ${tm.funding_btc!=null?(tm.funding_btc*100).toFixed(4)+'%':'—'}</b></div>
   <div style="margin-top:6px">${tm.board.slice(0,10).map((b,i)=>`<div class="row"><span>${i+1}. ${b.smart?'🧠':'🎲'} ${b.name}</span><b class="mono"><span class="${b.ret_pct>=0?'pos':'neg'}">${b.ret_pct>=0?'+':''}${(b.ret_pct||0).toFixed(2)}%</span> <span class="age" style="margin:0">${(b.winpct||0).toFixed(0)}%w</span></b></div>`).join('')}</div>
   <div class="warn" style="color:var(--mut)">🧠 structural edge · 🎲 directional (coin-flip). Watch win% over days.</div>`
  :`<div class="row"><span>tournament</span><b class="mono">offline (:3002)</b></div>`));
 paint(g);
 $('#srccount').textContent=Object.keys(d).filter(k=>d[k]&&typeof d[k]==='object'&&!d[k].error).length;
}
const es=new EventSource('/api/stream');
es.onmessage=e=>{try{render(JSON.parse(e.data))}catch(err){}};
es.onerror=()=>{}; // silent fallback — browser reconnects automatically
tick(); // initial load via REST (instant, before SSE first push)
// live candles — lightweight-charts (same lib OpenAlice uses)
const CH={}, SY=[['BTCUSDT','BTC'],['ETHUSDT','ETH'],['SOLUSDT','SOL']];
function initCandles(){
 if(!window.LightweightCharts){return setTimeout(initCandles,250)}
 SY.forEach(([sym])=>{
  const el=document.getElementById('ch-'+sym); if(!el)return;
  const chart=LightweightCharts.createChart(el,{height:200,width:el.clientWidth,
   layout:{background:{type:'solid',color:'#0e0f12'},textColor:'#8b94a3',fontSize:10},
   grid:{vertLines:{color:'#1a1b2150'},horzLines:{color:'#1a1b2150'}},
   timeScale:{timeVisible:true,borderColor:'#24262c'},rightPriceScale:{borderColor:'#24262c'},
   crosshair:{mode:0}});
  const s=chart.addCandlestickSeries({upColor:'#23b99a',downColor:'#be4138',
   wickUpColor:'#23b99a',wickDownColor:'#be4138',borderVisible:false});
  CH[sym]={chart,s};
  new ResizeObserver(()=>chart.applyOptions({width:el.clientWidth})).observe(el);
 });
 loadCandles(); setInterval(loadCandles,20000);
}
async function loadCandles(){
 let d; try{d=await(await fetch('/api/candles')).json()}catch(e){return}
 SY.forEach(([sym])=>{
  if(CH[sym]&&d[sym]&&d[sym].length){
   CH[sym].s.setData(d[sym]);
   const last=d[sym][d[sym].length-1], up=last.close>=last.open;
   const lbl=document.getElementById('px-'+sym);
   if(lbl){lbl.textContent='$'+(+last.close).toLocaleString();lbl.className='cpx '+(up?'pos':'neg')}
  }
 });
}
initCandles();
// bot equity curves — baseline series (green above $0, red below)
const EQ={};
function initEquity(){
 if(!window.LightweightCharts){return setTimeout(initEquity,250)}
 ['scalp','futures'].forEach(k=>{
  const el=document.getElementById('ch-'+k); if(!el)return;
  const chart=LightweightCharts.createChart(el,{height:180,width:el.clientWidth,
   layout:{background:{type:'solid',color:'#0e0f12'},textColor:'#8b94a3',fontSize:10},
   grid:{vertLines:{color:'#1a1b2150'},horzLines:{color:'#1a1b2150'}},
   timeScale:{timeVisible:false,borderColor:'#24262c'},rightPriceScale:{borderColor:'#24262c'},crosshair:{mode:0}});
  const s=chart.addBaselineSeries({baseValue:{type:'price',price:0},lineWidth:2,
   topLineColor:'#23b99a',topFillColor1:'#23b99a55',topFillColor2:'#23b99a05',
   bottomLineColor:'#be4138',bottomFillColor1:'#be413805',bottomFillColor2:'#be413855'});
  EQ[k]={chart,s};
  new ResizeObserver(()=>chart.applyOptions({width:el.clientWidth})).observe(el);
 });
 loadEquity(); setInterval(loadEquity,30000);
}
async function loadEquity(){
 let d; try{d=await(await fetch('/api/equity')).json()}catch(e){return}
 ['scalp','futures'].forEach(k=>{
  if(EQ[k]&&d[k]&&d[k].length){
   EQ[k].s.setData(d[k]); EQ[k].chart.timeScale().fitContent();
   const last=d[k][d[k].length-1].value, lbl=document.getElementById('eq-'+k);
   if(lbl){lbl.textContent=(last>=0?'+':'')+'$'+last.toFixed(2);lbl.className='cpx '+(last>=0?'pos':'neg')}
  }
 });
}
initEquity();
// algo race — top-6 since-inception equity curves (multi-line)
let RACE=null, RS=[]; const RACE_COLORS=['#f0b90b','#3b82f6','#23b99a','#be4138','#6b5bc2','#e8eaed'];
function initRace(){
 if(!window.LightweightCharts){return setTimeout(initRace,250)}
 const el=document.getElementById('ch-race'); if(!el)return;
 RACE=LightweightCharts.createChart(el,{height:240,width:el.clientWidth,
  layout:{background:{type:'solid',color:'#0e0f12'},textColor:'#8b94a3',fontSize:10},
  grid:{vertLines:{color:'#1a1b2130'},horzLines:{color:'#1a1b2130'}},
  timeScale:{timeVisible:false,borderColor:'#24262c'},rightPriceScale:{borderColor:'#24262c'},crosshair:{mode:0}});
 new ResizeObserver(()=>RACE.applyOptions({width:el.clientWidth})).observe(el);
 loadRace(); setInterval(loadRace,20000);
}
async function loadRace(){
 let d; try{d=await(await fetch('/api/race')).json()}catch(e){return}
 RS.forEach(s=>{try{RACE.removeSeries(s)}catch(e){}}); RS=[];
 const leg=[];
 (d.series||[]).forEach((s,i)=>{
  const col=RACE_COLORS[i%RACE_COLORS.length];
  const ls=RACE.addLineSeries({color:col,lineWidth:2,priceLineVisible:false,lastValueVisible:false});
  ls.setData(s.curve||[]); RS.push(ls);
  leg.push(`<span style="color:${col}">●</span> ${s.name} <span class="${s.pnl>=0?'pos':'neg'}">${s.pnl>=0?'+':''}${s.pnl}%</span>`);
 });
 try{RACE.timeScale().fitContent()}catch(e){}
 const L=document.getElementById('race-legend'); if(L)L.innerHTML=leg.join('&nbsp;&nbsp;');
 const rd=document.getElementById('race-days'); if(rd&&d.days!=null)rd.textContent=d.days+'d';
}
initRace();
</script></body></html>"""

# ── Mobile bridge: :8800 → ALICE portal at :8090 ─────────────────────────────
MOBILE_PAGE = """<!DOCTYPE html><html><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<meta http-equiv="refresh" content="0;url=http://localhost:8090/">
<title>ALICE Mobile</title>
<style>
body{margin:0;background:#0b0e14;color:#e8eaed;font:15px -apple-system,sans-serif;
  display:flex;flex-direction:column;align-items:center;justify-content:center;min-height:100vh;text-align:center}
a{color:#3b82f6;font-size:18px}
p{color:#8b94a3;margin-top:8px;font-size:13px}
</style></head><body>
<a href="http://localhost:8090/">Open ALICE Portal</a>
<p>Redirecting to ALICE&nbsp;Live Portal&nbsp;(:8090)&hellip;</p>
</body></html>"""

class MobileH(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        b = MOBILE_PAGE.encode()
        # /api/* → proxy through to :8090 so widgets on LAN still work
        if self.path.startswith("/api/") or self.path in ("/dashboard", "/dashboard/") or self.path == "/":
            try:
                req = urllib.request.Request(
                    f"http://127.0.0.1:{PORT}{self.path}",
                    headers={"User-Agent": "mobile-bridge"})
                resp = urllib.request.urlopen(req, timeout=10)
                body = resp.read()
                self.send_response(200)
                self.send_header("Content-Type", resp.headers.get("Content-Type", "text/html"))
                self.send_header("Content-Length", str(len(body)))
                self.end_headers(); self.wfile.write(body)
            except Exception as e:
                err = json.dumps({"error": str(e)}).encode()
                self.send_response(502)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(err)))
                self.end_headers(); self.wfile.write(err)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers(); self.wfile.write(b)

def _mobile_server_thread():
    try:
        srv = ThreadingHTTPServer(("0.0.0.0", 8800), MobileH)
        srv.serve_forever()
    except Exception as e:
        print(f"[mobile-bridge :8800] failed: {e}", file=sys.stderr)

# ── iMessage alert thread ─────────────────────────────────────────────────────
_ALERT_PHONE   = "+918449447444"
_ALERT_LIQ_USD = 100_000          # fire if single liquidation notional > $100k
_ALERT_FG_LO   = 15               # extreme-fear threshold
_ALERT_FG_HI   = 85               # extreme-greed threshold
_ALERT_FG_COOL = 1800             # 30-min cooldown between fg alerts (seconds)

_alert_state = {"last_liq_ts": 0, "last_fg_alert": 0, "last_fg_side": None}


def _send_imessage(body: str):
    """Send iMessage to ALERT_PHONE via osascript. Fail-soft."""
    osa = (
        'on run argv\n'
        'tell application "Messages"\n'
        '  set s to 1st service whose service type = iMessage\n'
        '  send (item 2 of argv) to buddy (item 1 of argv) of s\n'
        'end tell\n'
        'end run'
    )
    try:
        subprocess.run(
            ["osascript", "-e", osa, _ALERT_PHONE, body],
            capture_output=True, timeout=10
        )
    except Exception:
        pass


def _alerts_thread():
    """Daemon thread: every 30s check liquidations + Fear & Greed for alert triggers."""
    while True:
        try:
            now = time.time()

            # ── 1. Large-liquidation alert ──────────────────────────────────
            if _live_liquidations:
                newest = max(_live_liquidations, key=lambda e: e.get("ts", 0))
                notional = newest.get("qty", 0) * newest.get("price", 0)
                ts = newest.get("ts", 0)
                if notional > _ALERT_LIQ_USD and ts > _alert_state["last_liq_ts"]:
                    _alert_state["last_liq_ts"] = ts
                    side  = newest.get("side", "?").upper()
                    sym   = newest.get("sym", "?")
                    qty   = newest.get("qty", 0)
                    price = newest.get("price", 0)
                    msg = (
                        f"ALICE LIQUIDATION ALERT\n"
                        f"{side} {sym}  ${notional:,.0f} notional\n"
                        f"qty={qty:.4f}  px={price:,.2f}"
                    )
                    _send_imessage(msg)

            # ── 2. Fear & Greed extreme alert ───────────────────────────────
            fg = _live_sentiment.get("fg_value")
            if fg is not None:
                if fg < _ALERT_FG_LO:
                    side = "EXTREME_FEAR"
                elif fg > _ALERT_FG_HI:
                    side = "EXTREME_GREED"
                else:
                    side = None

                if side and (
                    now - _alert_state["last_fg_alert"] > _ALERT_FG_COOL
                    or _alert_state["last_fg_side"] != side
                ):
                    _alert_state["last_fg_alert"] = now
                    _alert_state["last_fg_side"]  = side
                    cls = _live_sentiment.get("fg_class", "")
                    msg = (
                        f"ALICE F&G ALERT  {side}\n"
                        f"Fear & Greed Index = {fg}  ({cls})\n"
                        f"Threshold: <{_ALERT_FG_LO} or >{_ALERT_FG_HI}"
                    )
                    _send_imessage(msg)

        except Exception:
            pass

        time.sleep(30)


# Start background live-data threads (daemon=True so they die with the process)
for _fn in (_binance_ws_thread, _funding_thread, _watchdog_thread,
            _oi_thread, _sentiment_thread, _iv_thread, _alerts_thread,
            _mobile_server_thread):
    _t = threading.Thread(target=_fn, daemon=True)
    _t.start()
# Futures WS (separate host): all-market liquidations (!forceOrder@arr) + markPrice — these
# only deliver on fstream.binance.com, not the spot host the default thread uses.
_fut_streams = "!forceOrder@arr/" + "/".join(f"{s}@markPrice@1s" for s in ("btcusdt", "ethusdt", "solusdt"))
threading.Thread(target=_binance_ws_thread, daemon=True,
                 kwargs={"host": "fstream.binance.com", "port": 443, "streams": _fut_streams}).start()

if __name__ == "__main__":
    # Dual-stack bind (2026-06-29): was ("127.0.0.1", PORT) = IPv4 loopback ONLY.
    # macOS resolves `localhost` to ::1 (IPv6) first, so a browser opening
    # http://localhost:8090 hit IPv6, found nothing listening, and showed
    # "page won't load" (while curl quietly fell back to IPv4). Binding "::"
    # with IPV6_V6ONLY off accepts BOTH ::1 and 127.0.0.1 (and LAN), so the
    # portal loads in any browser and is reachable from the phone for daily use.
    import socket as _socket
    class _DualStackHTTPServer(ThreadingHTTPServer):
        address_family = _socket.AF_INET6
        def server_bind(self):
            try:
                self.socket.setsockopt(_socket.IPPROTO_IPV6, _socket.IPV6_V6ONLY, 0)
            except OSError:
                pass  # not all platforms allow toggling; fall back to v6-only
            super().server_bind()
    srv = _DualStackHTTPServer(("::", PORT), H)
    print(f"ALICE portal live on http://localhost:{PORT}  (Ctrl-C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
