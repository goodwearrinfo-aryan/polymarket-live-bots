#!/usr/bin/env python3
"""
market_history.py — last-3-month index analysis → Obsidian (paper/research only).

Pulls ~3 months of daily closes for India + global indices, computes descriptive
stats (return, annualized vol, max drawdown, trend vs 20/50d SMA, position-in-range),
a cross-index correlation matrix, and an ILLUSTRATIVE trend read per index. Writes a
Markdown analysis note to the vault.

HONESTY (project discipline — strategy graveyard / Lesson 15 / no-overfit):
  - This is DESCRIPTIVE 3-month, in-sample analysis. The "signal" column is an
    illustrative trend read, NOT a validated edge (no ≥30-exit / bootstrap-CI / DSR
    gate, single in-sample window). Do NOT treat as actionable real-money advice.
  - Paper/research only. Indices are not the bot's tradeable venue (Polymarket).

Keyless sources (this network blocks most history APIs):
  - Nasdaq api (api.nasdaq.com): reliable for COMP / NDX only.
  - Yahoo chart v8: covers all symbols incl. Indian (^BSESN/^NSEI) but rate-limits
    per-IP under heavy use — gentle spacing + dual-host retry; fail-soft per symbol.
launchd-scheduled; logs to /tmp (not ~/Documents — exit-78 lesson).
"""
import json, os, math, time, statistics, urllib.request, urllib.parse
from datetime import datetime, timezone, timedelta

POLY  = os.path.dirname(os.path.abspath(__file__))
VAULT = os.path.join(os.path.expanduser("~"), "Documents/PolymarketVault")
NOTE  = os.path.join(VAULT, "Markets Analysis.md")
UA    = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
         "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# (label, source, symbol) — source: 'nasdaq' (reliable, US), 'crypto' (Binance klines,
# reliable keyless) or 'yahoo' (all, rate-limited)
SYMBOLS = [
    ("Sensex",       "yahoo",  "^BSESN"),
    ("Nifty 50",     "yahoo",  "^NSEI"),
    ("Bank Nifty",   "yahoo",  "^NSEBANK"),
    ("S&P 500",      "yahoo",  "^GSPC"),
    ("Nasdaq Comp",  "nasdaq", "COMP"),
    ("Nasdaq 100",   "nasdaq", "NDX"),
    ("Dow Jones",    "yahoo",  "^DJI"),
    ("Nikkei 225",   "yahoo",  "^N225"),
    ("FTSE 100",     "yahoo",  "^FTSE"),
    ("Bitcoin",      "crypto", "BTCUSDT"),
    ("Ether",        "crypto", "ETHUSDT"),
]


def _get(url, extra=None, timeout=12):
    h = {"User-Agent": UA, "Accept": "application/json"}
    if extra:
        h.update(extra)
    with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def hist_nasdaq(sym, start, end):
    url = (f"https://api.nasdaq.com/api/quote/{urllib.parse.quote(sym)}/chart"
           f"?assetclass=index&fromdate={start}&todate={end}")
    d = json.loads(_get(url, {"Origin": "https://www.nasdaq.com", "Referer": "https://www.nasdaq.com/"}))
    ch = (d.get("data") or {}).get("chart") or []
    return [(int(p["x"]) // 1000, float(p["y"])) for p in ch if p.get("y") is not None]


def hist_crypto(sym):
    # Binance daily klines (keyless, reliable from this network; .us fallback for geo-block).
    # kline row = [openTime_ms, open, high, low, close, ...]; close = idx 4.
    for host in ("api.binance.com", "api.binance.us"):
        try:
            rows = json.loads(_get(f"https://{host}/api/v3/klines?symbol={sym}&interval=1d&limit=95"))
            if rows:
                return [(int(r[0]) // 1000, float(r[4])) for r in rows]
        except Exception:
            continue
    raise ValueError("binance klines failed")


def hist_yahoo(sym):
    err = None
    for host in ("query1", "query2"):
        for _ in range(2):
            try:
                url = (f"https://{host}.finance.yahoo.com/v8/finance/chart/"
                       f"{urllib.parse.quote(sym)}?interval=1d&range=3mo")
                r = json.loads(_get(url))["chart"]["result"][0]
                ts = r["timestamp"]
                cl = r["indicators"]["quote"][0]["close"]
                return [(t, c) for t, c in zip(ts, cl) if c is not None]
            except Exception as e:
                err = e
                time.sleep(1.0)
    raise err or ValueError("yahoo failed")


def get_hist(source, sym, start, end):
    if source == "crypto":
        try:
            b = hist_crypto(sym)
            return sorted(b) if b and len(b) >= 10 else None
        except Exception:
            return None
    primary = (lambda: hist_nasdaq(sym, start, end)) if source == "nasdaq" else (lambda: hist_yahoo(sym))
    backup  = (lambda: hist_yahoo(sym)) if source == "nasdaq" else (lambda: None)
    for fn in (primary, backup):
        try:
            bars = fn()
            if bars and len(bars) >= 10:
                return sorted(bars)
        except Exception:
            continue
    return None


def analyze(bars):
    closes = [c for _, c in bars]
    n = len(closes)
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, n) if closes[i - 1] > 0]
    ret_pct = (closes[-1] / closes[0] - 1) * 100
    vol = (statistics.pstdev(rets) * math.sqrt(252) * 100) if len(rets) > 1 else 0.0
    # max drawdown
    peak, mdd = closes[0], 0.0
    for c in closes:
        peak = max(peak, c)
        mdd = min(mdd, c / peak - 1)
    sma = lambda k: (sum(closes[-k:]) / k) if n >= k else None
    s20, s50 = sma(20), sma(50)
    lo, hi = min(closes), max(closes)
    pos = (closes[-1] - lo) / (hi - lo) * 100 if hi > lo else 50.0
    mom1m = (closes[-1] / closes[-21] - 1) * 100 if n >= 21 else ret_pct
    trend_up = bool(s50 and closes[-1] > s50)
    return {"n": n, "last": closes[-1], "ret_pct": ret_pct, "vol": vol, "mdd": mdd * 100,
            "pos": pos, "mom1m": mom1m, "above50": trend_up,
            "above20": bool(s20 and closes[-1] > s20), "rets": rets, "ts": [t for t, _ in bars]}


def signal(a):
    # ILLUSTRATIVE trend read — not a validated edge.
    if a["above50"] and a["mom1m"] > 0:
        return "🟢 constructive (above 50d, +1m momentum)"
    if not a["above50"] and a["mom1m"] < 0:
        return "🔴 weak (below 50d, -1m momentum)"
    return "🟡 mixed"


def correlations(data):
    labels = [l for l in data if data[l]["n"] >= 30]
    out = []
    for i, a in enumerate(labels):
        for b in labels[i + 1:]:
            # align on common dates
            ma = dict(zip(data[a]["ts"][1:], data[a]["rets"]))
            mb = dict(zip(data[b]["ts"][1:], data[b]["rets"]))
            common = sorted(set(ma) & set(mb))
            if len(common) >= 20:
                xa = [ma[t] for t in common]
                xb = [mb[t] for t in common]
                try:
                    out.append((a, b, statistics.correlation(xa, xb)))
                except Exception:
                    pass
    return sorted(out, key=lambda x: -abs(x[2]))[:8]


def main():
    ts = datetime.now(timezone.utc).isoformat()[:16] + "Z"
    end = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    start = (datetime.now(timezone.utc) - timedelta(days=95)).strftime("%Y-%m-%d")
    data, missing = {}, []
    for label, source, sym in SYMBOLS:
        bars = get_hist(source, sym, start, end)
        if bars:
            data[label] = analyze(bars)
        else:
            missing.append(label)
        time.sleep(2.5)  # gentle: avoid tripping Yahoo's per-IP rate limit

    L = ["---", "type: analysis", f"updated: {ts[:10]}", "tags:\n  - markets\n  - analysis", "---", "",
         "# Markets Analysis (last ~3 months)",
         f"> [!warning] DESCRIPTIVE in-sample analysis, paper/research only. The Signal column is an "
         f"illustrative trend read, **NOT a validated edge** (no ≥30-exit / bootstrap-CI / DSR gate; "
         f"single in-sample window). Not real-money advice. · {ts}", ""]
    if data:
        L += ["## Per-index (3-month)", "",
              "| Index | Last | 3M Return | Ann. Vol | Max DD | 1M Mom | Range pos | Signal |",
              "|---|--:|--:|--:|--:|--:|--:|---|"]
        for label, _, _ in SYMBOLS:
            if label not in data:
                continue
            a = data[label]
            L.append(f"| {label} | {a['last']:,.0f} | {a['ret_pct']:+.1f}% | {a['vol']:.0f}% | "
                     f"{a['mdd']:.1f}% | {a['mom1m']:+.1f}% | {a['pos']:.0f}% | {signal(a)} |")
        L.append("")
        cors = correlations(data)
        if cors:
            L += ["## Strongest daily-return correlations", ""]
            L += [f"- **{a} ↔ {b}**: {c:+.2f}" for a, b, c in cors]
            L.append("")
    else:
        L += ["## Per-index (3-month)", "",
              "_No history retrieved this run — keyless sources rate-limited/blocked from this "
              "network (Yahoo 429). Re-runs on the daily schedule fill in as the limit clears._", ""]
    if missing:
        L += [f"> [!note] Not retrieved this run (source rate-limited, fills on next run): "
              f"{', '.join(missing)}", ""]
    L += ["## Method", "- Source: Nasdaq API (COMP/NDX) + Yahoo chart v8 (rest), keyless, ~3mo daily closes.",
          "- Return = close[-1]/close[0]-1. Ann.Vol = stdev(daily log-ret)·√252. Max DD = worst peak-to-trough.",
          "- 1M Mom = 21-session change. Range pos = where last sits in the 3mo low–high band.",
          "- Signal: above-50d-SMA + 1m-momentum sign. Illustrative trend read only — see warning above.", "",
          "_Linked: [[Markets]] (live board) · [[Markets Paper]] (forward paper book)._"]

    os.makedirs(VAULT, exist_ok=True)
    with open(NOTE + ".tmp", "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    os.replace(NOTE + ".tmp", NOTE)
    # cache the analysis (single shared fetch) + drive the forward paper book
    try:
        # atomic write (tmp + os.replace) — market_paper.py re-reads this cache to drive the
        # forward paper book, so an in-place write that's truncated by a crash/concurrent run
        # would corrupt that input (overnight robustness audit 2026-06-21).
        _cache = os.path.join(POLY, "market_history_cache.json")
        with open(_cache + ".tmp", "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(_cache + ".tmp", _cache)
        import market_paper
        market_paper.run(data)
    except Exception as e:
        print(f"market_paper hook failed (non-fatal): {e}")
    print(f"market_history: {len(data)}/{len(SYMBOLS)} indices analyzed · note → {NOTE}"
          + (f" · missing: {', '.join(missing)}" if missing else ""))


if __name__ == "__main__":
    main()
