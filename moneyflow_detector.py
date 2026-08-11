#!/usr/bin/env python3
"""moneyflow_detector.py — READ-ONLY money-flow / volume DETECTOR + forward-logger (places NO trades).

Ties the project's existing keyless flow signals into ONE 5-step pipeline
(ingest -> features -> detect -> forward-log -> alert) across THREE venues, 24/7,
on a launchd schedule. NO API keys — all sources keyless/free:
  crypto : Binance public REST  (via orderflow_signals.py)  — RVOL + CVD/net-aggressive-delta + book imbalance
  stocks : yfinance             (keyless)                    — RVOL (today vs 20d mean vol) + day-return direction
  poly   : Polymarket data-api  (public trades tape)         — net taker USD flow per token vs gross

HONEST by construction (the project's scars baked in):
  - NO-LOOKAHEAD: a detection snapshots price NOW; the realized forward return is
    filled on a LATER cycle (never the same cycle). The jsonl log is the truth.
  - This is MEASUREMENT, not a trading leg. A signal earns "edge" only if, after
    n>=30 forward samples, its mean fwd-return CI excludes 0 net of cost AND beats a
    plain volume-spike control. Default expectation: NULL — volume spikes are largely
    priced in (see the candle-KB null: cluster CI straddles 0).
  - Alerts are DEDUP'd (cooldown per venue:symbol:signal) so a standing anomaly does
    not re-page every cycle. FAIL-SOFT: one bad symbol never kills the run (exit 0).

PAPER ONLY, read-only, stdlib + (lazy) orderflow_signals / yfinance / wa_alert.
Usage:
  python3 moneyflow_detector.py once  crypto|stocks|polymarket|all
  python3 moneyflow_detector.py report [crypto|stocks|polymarket|all]

Env overrides (for testing the detect->log->alert path on a live cycle):
  MF_RVOL_SPIKE / MF_RVOL_STOCK / MF_POLY_GROSS / MF_POLY_NET  — lower to force detections
  MONEYFLOW_NOALERT=1  — log alerts to file but do NOT deliver (no iMessage/WhatsApp spam)
"""
import os, sys, json, time, statistics, urllib.request
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
_DIR = os.environ.get("MF_STATE_DIR", HERE)   # overridable for testing without polluting the real log
# PER-VENUE state files: the 3 launchd jobs run concurrently; a shared state file would
# lose updates under a read-modify-write race. One file per venue = no cross-job contention.
LOG = os.path.join(_DIR, "moneyflow_log.jsonl")       # append-only, small records — safe to share
ALERT_LOG = os.path.join(_DIR, "moneyflow_alerts.log")
POLY_BASELINE = os.path.join(_DIR, "moneyflow_poly_baseline.json")  # per-token trailing flow history
VAULT = os.path.expanduser("~/Documents/PolymarketVault/Reports/moneyflow.md")
UA = {"User-Agent": "Mozilla/5.0"}


def _envf(name, default):
    try:
        return float(os.environ.get(name, default))
    except Exception:
        return default


# horizons (label, seconds) per venue for the no-lookahead forward return
HORIZONS = {
    "crypto":     [("1h", 3600),  ("4h", 14400)],
    "stocks":     [("1d", 86400), ("3d", 259200)],
    "polymarket": [("1h", 3600),  ("6h", 21600)],
}

# per-signal horizon overrides — intraday signals resolve on a shorter clock than 15m-candle signals
SIG_HORIZONS = {
    ("crypto", "intraday_volspike"):  [("15m", 900), ("1h", 3600)],
    ("crypto", "intraday_flowsurge"): [("15m", 900), ("1h", 3600)],
    ("polymarket", "flow_rvol_surge"): [("30m", 1800), ("2h", 7200)],
}


def _horizons(venue, sig):
    return SIG_HORIZONS.get((venue, sig), HORIZONS[venue])

CRYPTO_COINS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]
STOCK_TICKERS = ["SPY", "QQQ", "AAPL", "NVDA", "TSLA", "MSFT", "AMZN", "META"]

# detection thresholds (conservative — mostly-null forward research, do not spam)
RVOL_SPIKE = _envf("MF_RVOL_SPIKE", 3.0)     # crypto 15m-candle volume-spike trigger
RVOL_INTRADAY = _envf("MF_RVOL_INTRADAY", 5.0)  # crypto sub-15m (1m-candle) surge trigger — noisier, higher bar
RVOL_STOCK = _envf("MF_RVOL_STOCK", 2.0)     # equities unusual-volume trigger
DELTA_CONFIRM = 25.0                          # |delta_pct| that confirms crypto direction
POLY_MIN_GROSS = _envf("MF_POLY_GROSS", 3000.0)
POLY_MIN_NET = _envf("MF_POLY_NET", 2000.0)
POLY_RVOL = _envf("MF_POLY_RVOL", 4.0)               # poly flow-surge vs the token's OWN trailing baseline
POLY_MIN_CYCLE_GROSS = _envf("MF_POLY_CYCLE_GROSS", 800.0)  # floor so a tiny-baseline blip can't fire a surge
POLY_HIST = 30                                        # trailing per-token flow history length (cycles ~= 60 min)
POLY_MAX_TOKENS = 500                                 # hard cap on baseline-state size
ALERT_COOLDOWN = 6 * 3600                      # do not re-alert same venue:symbol:signal within 6h
MAX_PENDING = 600                              # fail-safe cap on the pending list per venue


# ── tiny helpers ─────────────────────────────────────────────────────────────
def _get(url, timeout=12):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def _binance_klines(symbol, interval, limit):
    """Keyless Binance klines at an arbitrary interval (orderflow_signals is hardcoded to 15m)."""
    raw = _get(f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}")
    return [{"t": k[0], "c": float(k[4]), "v": float(k[5])} for k in raw]


def _sign(x):
    return 1 if x > 0 else (-1 if x < 0 else 0)


def _median(xs):
    return statistics.median(xs) if xs else 0.0


def _state_path(venue):
    return os.path.join(_DIR, f"moneyflow_state_{venue}.json")


def load_state(venue):
    try:
        return json.load(open(_state_path(venue)))
    except Exception:
        return {"pending": [], "last_alert": {}}


def save_state(st, venue):
    p = _state_path(venue)
    tmp = p + ".tmp"
    json.dump(st, open(tmp, "w"))
    os.replace(tmp, p)   # atomic — crash-safe; per-venue file = no cross-job race


def append_log(rec):
    import errno, time
    line = json.dumps(rec) + "\n"
    for _ in range(4):
        try:
            with open(LOG, "a") as f:
                f.write(line)
            return
        except OSError as e:
            if e.errno != errno.EDEADLK:      # EDEADLK = file locked by a VM/Docker share or iCloud sync
                raise
            time.sleep(0.25)
    # still locked after retries -> skip this write (fail-soft; never crash the detector)


def _load_json(path, default):
    try:
        return json.load(open(path))
    except Exception:
        return default


def _save_json(path, obj):
    tmp = path + ".tmp"
    json.dump(obj, open(tmp, "w"))
    os.replace(tmp, path)   # atomic


# ── venue scanners: each returns [{symbol, price, ref, signals:[{name,strong,dir}], snapshot}] ──
def scan_crypto():
    import orderflow_signals as o
    out = []
    for sym in CRYPTO_COINS:
        try:
            kl = o.get_klines(sym, limit=200)
            if len(kl) < 60:
                continue
            vols = [k["v"] for k in kl]
            closes = [k["c"] for k in kl]
            # last CLOSED 15m candle = index -2 (Binance's last element is the in-progress candle)
            cur_v, base = vols[-2], _median(vols[-50:-2])
            rvol = round(cur_v / base, 2) if base > 0 else 0.0
            ret = (closes[-2] - closes[-3]) / closes[-3] * 100.0 if closes[-3] else 0.0
            of = o.get_of(sym, klines_24h=kl[-96:]) or {}
            dpct = of.get("delta_pct", 0.0) or 0.0
            imb = of.get("book_imb")
            sigs = []
            if rvol >= RVOL_SPIKE:
                sigs.append({"name": "volspike", "strong": False, "dir": _sign(ret)})
                if _sign(ret) and _sign(ret) == _sign(dpct) and abs(dpct) >= DELTA_CONFIRM:
                    # volume spike AND aggressive flow agree with the candle = real money moving in
                    sigs.append({"name": "flowsurge", "strong": True, "dir": _sign(ret)})
            # intraday (sub-15m): rolling RVOL on the last CLOSED 1m candle vs trailing 60×1m median.
            # This is NOT gated by the 15m close, so a fast surge surfaces within one 2-min poll.
            rvol1 = ret1 = None
            try:
                kl1 = _binance_klines(sym, "1m", 120)
                if len(kl1) >= 70:
                    v1 = [k["v"] for k in kl1]
                    c1 = [k["c"] for k in kl1]
                    base1 = _median(v1[-62:-2])
                    rvol1 = round(v1[-2] / base1, 2) if base1 > 0 else 0.0
                    ret1 = round((c1[-2] - c1[-3]) / c1[-3] * 100.0, 3) if c1[-3] else 0.0
                    if rvol1 >= RVOL_INTRADAY:
                        sigs.append({"name": "intraday_volspike", "strong": False, "dir": _sign(ret1)})
                        if _sign(ret1) and _sign(ret1) == _sign(dpct) and abs(dpct) >= DELTA_CONFIRM:
                            sigs.append({"name": "intraday_flowsurge", "strong": True, "dir": _sign(ret1)})
            except Exception as e:
                sys.stderr.write(f"[moneyflow:crypto] {sym} 1m: {e}\n")
            if sigs:
                out.append({"symbol": sym, "price": round(closes[-2], 6), "ref": sym, "signals": sigs,
                            "snapshot": {"rvol": rvol, "ret_pct": round(ret, 3),
                                         "rvol_1m": rvol1, "ret_1m_pct": ret1,
                                         "delta_pct": round(dpct, 2),
                                         "book_imb": round(imb, 4) if imb is not None else None}})
        except Exception as e:
            sys.stderr.write(f"[moneyflow:crypto] {sym}: {e}\n")
    return out


def crypto_price(sym):
    try:
        import orderflow_signals as o
        return float(o.get_klines(sym, limit=3)[-1]["c"])
    except Exception:
        return None


def scan_stocks():
    import warnings
    warnings.filterwarnings("ignore")
    import yfinance as yf
    out = []
    try:
        df = yf.download(STOCK_TICKERS, period="40d", interval="1d",
                         progress=False, threads=False, auto_adjust=False)
    except Exception as e:
        sys.stderr.write(f"[moneyflow:stocks] download failed: {e}\n")
        return out
    for t in STOCK_TICKERS:
        try:
            vol = df["Volume"][t].dropna()
            close = df["Close"][t].dropna()
            if len(vol) < 22 or len(close) < 3:
                continue
            cur_v, base = float(vol.iloc[-1]), float(vol.iloc[-21:-1].mean())
            rvol = round(cur_v / base, 2) if base > 0 else 0.0
            ret = (float(close.iloc[-1]) - float(close.iloc[-2])) / float(close.iloc[-2]) * 100.0
            if rvol >= RVOL_STOCK:
                out.append({"symbol": t, "price": round(float(close.iloc[-1]), 4), "ref": t,
                            "signals": [{"name": "unusual_volume", "strong": rvol >= 3.0, "dir": _sign(ret)}],
                            "snapshot": {"rvol": rvol, "ret_pct": round(ret, 3)}})
        except Exception as e:
            sys.stderr.write(f"[moneyflow:stocks] {t}: {e}\n")
    return out


def stock_price(sym):
    try:
        import warnings
        warnings.filterwarnings("ignore")
        import yfinance as yf
        df = yf.download([sym], period="5d", interval="1d", progress=False, threads=False, auto_adjust=False)
        return float(df["Close"][sym].dropna().iloc[-1])
    except Exception:
        return None


def scan_poly():
    out = []
    try:
        trades = _get("https://data-api.polymarket.com/trades?limit=1000")
    except Exception as e:
        sys.stderr.write(f"[moneyflow:poly] trades fetch failed: {e}\n")
        return out
    # De-overlap the global tape by timestamp: only count trades NEWER than the last cycle, so a
    # token's per-cycle gross is a true flow RATE (overlapping polls would otherwise double-count).
    bl = _load_json(POLY_BASELINE, {"last_ts": 0, "hist": {}})
    last_ts = bl.get("last_ts", 0)
    hist = bl.get("hist", {})
    ts_all = [int(t.get("timestamp", 0) or 0) for t in trades]
    max_ts = max(ts_all) if ts_all else last_ts
    new = [t for t in trades if int(t.get("timestamp", 0) or 0) > last_ts]

    cyc = {}   # asset -> this-cycle net/gross USD + label (NEW trades only)
    for tr in new:
        try:
            asset = tr.get("asset")
            if not asset:
                continue
            usd = float(tr.get("size", 0)) * float(tr.get("price", 0))
            if usd <= 0:
                continue
            signed = usd if tr.get("side") == "BUY" else -usd
            a = cyc.setdefault(asset, {"net": 0.0, "gross": 0.0, "last": None,
                                       "title": (tr.get("title") or "")[:60], "outcome": tr.get("outcome", "")})
            a["net"] += signed
            a["gross"] += usd
            a["last"] = float(tr.get("price", 0))
        except Exception:
            continue

    seen = set()
    for asset, a in cyc.items():
        seen.add(asset)
        h = hist.get(asset, [])
        base = _median(h) if len(h) >= 5 else None   # need >=5 cycles before judging "unusual for this market"
        onesided = abs(a["net"]) / a["gross"] if a["gross"] else 0.0
        d = 1 if a["net"] > 0 else -1                 # net taker money flowing INTO this token
        sigs = []
        # (1) ABSOLUTE one-sided surge — original signal; catches big flow even with no history
        if a["gross"] >= POLY_MIN_GROSS and abs(a["net"]) >= POLY_MIN_NET and onesided >= 0.6:
            sigs.append({"name": "flowsurge", "strong": abs(a["net"]) >= 2 * POLY_MIN_NET, "dir": d})
        # (2) RELATIVE surge vs this token's OWN trailing baseline (RVOL-for-flow)
        snap = {"net_usd": round(a["net"]), "cycle_gross": round(a["gross"]), "onesided": round(onesided, 2)}
        if base and base > 0 and a["gross"] >= POLY_MIN_CYCLE_GROSS and onesided >= 0.55:
            frvol = a["gross"] / base
            if frvol >= POLY_RVOL:
                snap = {**snap, "flow_rvol": round(frvol, 2), "base_gross": round(base)}
                sigs.append({"name": "flow_rvol_surge", "strong": frvol >= 2 * POLY_RVOL, "dir": d})
        if sigs:
            out.append({"symbol": f"{a['title']} [{a['outcome']}]", "ref": asset,
                        "price": round(a["last"], 4), "signals": sigs, "snapshot": snap})
        h.append(round(a["gross"], 1))           # push AFTER detect — baseline never includes the current obs
        hist[asset] = h[-POLY_HIST:]

    # tokens that went quiet this cycle: record a 0 so the baseline decays; prune the long-dead
    for asset in list(hist.keys()):
        if asset not in seen:
            hist[asset] = (hist[asset] + [0.0])[-POLY_HIST:]
        if len(hist[asset]) >= POLY_HIST and sum(hist[asset]) == 0:
            del hist[asset]
    if len(hist) > POLY_MAX_TOKENS:               # hard cap: keep the most-active tokens
        hist = dict(sorted(hist.items(), key=lambda kv: sum(kv[1]), reverse=True)[:POLY_MAX_TOKENS])

    _save_json(POLY_BASELINE, {"last_ts": max_ts, "hist": hist})
    return out


def poly_price(ref):
    try:
        bk = _get(f"https://clob.polymarket.com/book?token_id={ref}")
        bids, asks = bk.get("bids") or [], bk.get("asks") or []
        if not bids or not asks:
            return None
        return round((max(float(b["price"]) for b in bids) + min(float(a["price"]) for a in asks)) / 2, 4)
    except Exception:
        return None


SCANNERS = {"crypto": (scan_crypto, crypto_price),
            "stocks": (scan_stocks, stock_price),
            "polymarket": (scan_poly, poly_price)}


def _key(venue, symbol, signame):
    return f"{venue}:{symbol}:{signame}"


def _send_alert(rec):
    arrow = "↑" if rec["dir"] > 0 else "↓"
    snap = ", ".join(f"{k}={v}" for k, v in rec["snapshot"].items())
    body = (f"MoneyFlow [{rec['venue']}] {rec['sig'].upper()} {arrow}\n"
            f"{rec['symbol']}\n{snap}\n(forward-research signal — NOT a trade)")
    if os.environ.get("MONEYFLOW_NOALERT") != "1":
        try:
            import wa_alert
            wa_alert.notify(body)
        except Exception as e:
            sys.stderr.write(f"[moneyflow] alert deliver failed: {e}\n")
    try:   # always mirror to a local alert log (auditable; loud-on-failure happens elsewhere)
        with open(ALERT_LOG, "a") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {body}\n---\n")
    except Exception:
        pass


def once(venue):
    if venue not in SCANNERS:
        print(f"[moneyflow] unknown venue: {venue}")
        return
    scan, price_fn = SCANNERS[venue]
    st = load_state(venue)
    pending, last_alert, now = st.get("pending", []), st.get("last_alert", {}), int(time.time())

    # 1) RESOLVE matured pending detections of THIS venue (no-lookahead forward return)
    still, resolved_n = [], 0
    for p in pending:
        if p["venue"] != venue:
            still.append(p)
            continue
        hz = _horizons(venue, p["sig"])          # per-signal horizons (intraday resolves faster)
        maxt = hz[-1][1]
        due = [h for (h, t) in hz if p["fwd"].get(h) is None and now - p["ts"] >= t]
        if due:
            cur = price_fn(p["ref"])
            if cur is not None and p.get("price"):
                for h in due:
                    raw = (cur - p["price"]) / p["price"] * 100.0
                    p["fwd"][h] = round(raw * p["dir"], 4)   # signed by flow direction
        if all(p["fwd"].get(h) is not None for (h, _t) in hz):
            append_log({**p, "resolved_ts": now}); resolved_n += 1
        elif now - p["ts"] > maxt + 6 * 3600:                 # give up: log as expired, drop
            append_log({**p, "resolved_ts": now, "expired": True}); resolved_n += 1
        else:
            still.append(p)

    # 2) SCAN -> open new detections (one open pending per key to avoid autocorrelated overlap)
    open_keys = {_key(p["venue"], p["symbol"], p["sig"]) for p in still}
    dets = scan() or []
    opened, alerts = 0, []
    for d in dets:
        for s in d["signals"]:
            k = _key(venue, d["symbol"], s["name"])
            if k in open_keys:
                continue
            rec = {"id": f"{venue}-{now}-{opened}", "venue": venue, "symbol": d["symbol"],
                   "sig": s["name"], "strong": bool(s["strong"]), "dir": s["dir"] or 1,
                   "ts": now, "price": d.get("price"), "ref": d.get("ref", d["symbol"]),
                   "snapshot": d["snapshot"], "fwd": {}}
            still.append(rec); open_keys.add(k); opened += 1
            if s["strong"] and now - last_alert.get(k, 0) >= ALERT_COOLDOWN:   # 3) dedup'd alert
                alerts.append(rec); last_alert[k] = now

    # fail-safe: bound pending growth (drop oldest of this venue)
    vp = [p for p in still if p["venue"] == venue]
    if len(vp) > MAX_PENDING:
        drop = set(id(p) for p in sorted(vp, key=lambda x: x["ts"])[:len(vp) - MAX_PENDING])
        still = [p for p in still if id(p) not in drop]

    st["pending"], st["last_alert"] = still, last_alert
    save_state(st, venue)
    for rec in alerts:
        _send_alert(rec)

    print(f"[moneyflow:{venue}] detections={len(dets)} opened={opened} resolved={resolved_n} "
          f"pending={sum(1 for p in still if p['venue'] == venue)} alerts={len(alerts)}")


def _recent_alerts(n=12):
    try:
        blocks = [b.strip() for b in open(ALERT_LOG).read().split("\n---\n") if b.strip()]
        return blocks[-n:]
    except Exception:
        return []


def _pending_counts():
    out = {}
    for v in ("crypto", "stocks", "polymarket"):
        try:
            st = json.load(open(_state_path(v)))
            out[v] = len([p for p in st.get("pending", []) if p.get("venue") == v])
        except Exception:
            out[v] = 0
    return out


def report(venue="all"):
    rows = []
    try:
        for line in open(LOG):
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    except OSError:
        pass   # no resolved samples yet, OR log transiently locked (VM/iCloud) — warm-up note, fail-soft
    if venue != "all":
        rows = [r for r in rows if r.get("venue") == venue]
    agg = defaultdict(lambda: defaultdict(list))
    for r in rows:
        for h, v in (r.get("fwd") or {}).items():
            if v is not None:
                agg[(r["venue"], r["sig"])][h].append(v)
    lines = [f"forward-return KB ({venue}) — {len(rows)} resolved samples",
             "(fwd return SIGNED by flow direction; +mean = money-flow predicted the move)", ""]
    if agg:
        for (vn, sig), hs in sorted(agg.items()):
            parts = [f"{h}: mean={sum(v) / len(v):+.3f}% n={len(v)}" for h, v in sorted(hs.items())]
            lines.append(f"  {vn:11s} {sig:16s} | " + " | ".join(parts))
    else:
        lines.append("  (warming up — no detection has matured to its forward horizon yet)")
    pend = _pending_counts()
    lines += ["", "open detections awaiting resolution: " + ", ".join(f"{k}={v}" for k, v in pend.items()), "",
              "EDGE only if a signal's mean fwd CI>0 net of cost at n>=30 AND beats a vol-spike control. Default: NULL."]
    out = "\n".join(lines)
    print(out)

    # ── mirror to Obsidian vault (standing preference: wire everything to the vault) ──
    body = ["---", "type: report", "status: forward-research",
            f"updated: {time.strftime('%Y-%m-%d %H:%M:%S')}", "tags: [moneyflow, forward-research]", "---", "",
            "# Money-Flow / Volume Detector", "",
            "Keyless 24/7 detector across crypto (Binance) / stocks (yfinance) / Polymarket. "
            "Forward-research logger — **NOT a trading edge** (default expectation: NULL).", "",
            "## Forward-return knowledge base", "```", out, "```", "", "## Recent alerts", ""]
    body += ["- " + a.replace("\n", " · ") for a in _recent_alerts()] or ["- (none yet)"]
    try:
        os.makedirs(os.path.dirname(VAULT), exist_ok=True)
        with open(VAULT, "w") as f:
            f.write("\n".join(body) + "\n")
    except Exception as e:
        sys.stderr.write(f"[moneyflow] vault write failed: {e}\n")


def main():
    args = sys.argv[1:]
    mode = args[0] if args else "report"
    arg2 = args[1] if len(args) > 1 else "all"
    if mode == "once":
        for v in (["crypto", "stocks", "polymarket"] if arg2 == "all" else [arg2]):
            once(v)
    elif mode == "report":
        report(arg2)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
