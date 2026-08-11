#!/usr/bin/env python3
"""
market_indices.py — keyless India + global index / macro board → Obsidian + JSONL.

Sources (no API keys, per the project's keyless-path rule):
  - CNBC quote API (primary): Nifty 50, Bank Nifty, S&P/Nasdaq/Dow/Nikkei/FTSE, USDINR, gold, crude.
  - Yahoo chart v8 (query2): Sensex (^BSESN) — CNBC has no Sensex symbol.
Each source cross-falls-back to the other; every fetch is fail-soft (a dead instrument
just shows "—" that cycle). Writes a Markdown board to the vault + an append-only JSONL
tape (capped). launchd-scheduled — STDOUT goes to /tmp (NOT ~/Documents: launchd can't
open a log path under ~/Documents → EX_CONFIG/78, see watchdog-tcc-selfheal lesson).
Paper-only infra: read-only market data, no orders, no funds.
"""
import json, os, time, urllib.request, urllib.parse
from datetime import datetime, timezone

POLY  = os.path.dirname(os.path.abspath(__file__))
VAULT = os.path.join(os.path.expanduser("~"), "Documents/PolymarketVault")
LOG   = os.path.join(POLY, "market_indices_log.jsonl")
BOARD = os.path.join(VAULT, "Markets.md")
UA    = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
         "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
MAX_LOG = 5000

# group -> [(label, source, symbol)]
INSTRUMENTS = [
    ("🇮🇳 India", [("Sensex", "yahoo", "^BSESN"), ("Nifty 50", "cnbc", ".NSEI"),
                   ("Bank Nifty", "cnbc", ".NSEBANK")]),
    ("🌐 Global indices", [("S&P 500", "cnbc", ".SPX"), ("Nasdaq", "cnbc", ".IXIC"),
                   ("Dow Jones", "cnbc", ".DJI"), ("Nikkei 225", "cnbc", ".N225"),
                   ("FTSE 100", "cnbc", ".FTSE"), ("DAX", "cnbc", ".GDAXI"),
                   ("CAC 40", "cnbc", ".FCHI"), ("Hang Seng", "cnbc", ".HSI"),
                   ("Shanghai", "cnbc", ".SSEC"), ("KOSPI", "cnbc", ".KS11"),
                   ("ASX 200", "cnbc", ".AXJO"), ("TSX", "cnbc", ".GSPTSE")]),
    ("💱 FX", [("USD/INR", "cnbc", "INR="), ("EUR/USD", "cnbc", "EUR="),
              ("GBP/USD", "cnbc", "GBP="), ("USD/JPY", "cnbc", "JPY="),
              ("USD/CNY", "cnbc", "CNY=")]),
    ("🛢 Commodities", [("Gold", "cnbc", "@GC.1"), ("WTI Crude", "cnbc", "@CL.1"),
                        ("Silver", "cnbc", "@SI.1"), ("Nat Gas", "cnbc", "@NG.1")]),
    ("₿ Crypto", [("Bitcoin", "cnbc", "BTC.CM="), ("Ether", "cnbc", "ETH.CM=")]),
    ("📉 Rates & Vol", [("US 10Y", "cnbc", "US10Y"), ("VIX", "cnbc", ".VIX"),
                        ("Dollar Index", "cnbc", ".DXY")]),
]


def _fmt(v):
    """Magnitude-aware formatting: 4dp for FX/yields (<10), 2dp mid, comma-int for indices/crypto."""
    a = abs(v)
    return f"{v:,.4f}" if a < 10 else (f"{v:,.2f}" if a < 1000 else f"{v:,.0f}")


def _get(url, extra=None):
    h = {"User-Agent": UA, "Accept": "application/json"}
    if extra:
        h.update(extra)
    with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=10) as r:
        return r.read().decode("utf-8", "replace")


def _f(x):
    try:
        return float(str(x).replace(",", "").replace("%", "").replace("+", ""))
    except Exception:
        return None


def fetch_cnbc(sym):
    url = ("https://quote.cnbc.com/quote-html-webservice/restQuote/symbolType/symbol"
           f"?symbols={urllib.parse.quote(sym)}&requestMethod=itv&noform=1&partnerId=2&fund=1&output=json")
    q = json.loads(_get(url))["FormattedQuoteResult"]["FormattedQuote"][0]
    if q.get("last") in (None, ""):
        raise ValueError("no last")
    return {"last": _f(q.get("last")), "change_pct": _f(q.get("change_pct")), "name": q.get("name")}


def fetch_yahoo(sym):
    # Yahoo rate-limits per-IP (429) intermittently; try both hosts with a short backoff
    # so the one-call-per-cycle scheduled job reliably gets Sensex (^BSESN — Yahoo-only).
    err = None
    for host in ("query1", "query2"):
        for _ in range(2):
            try:
                url = (f"https://{host}.finance.yahoo.com/v8/finance/chart/"
                       f"{urllib.parse.quote(sym)}?interval=1d&range=1d")
                m = json.loads(_get(url))["chart"]["result"][0]["meta"]
                last = m.get("regularMarketPrice")
                prev = m.get("chartPreviousClose") or m.get("previousClose")
                if last is not None:
                    cp = ((last - prev) / prev * 100) if prev else None
                    return {"last": last, "change_pct": cp, "name": m.get("symbol")}
            except Exception as e:
                err = e
                time.sleep(0.8)
    raise err or ValueError("yahoo failed")


def fetch(source, sym):
    primary, backup = (fetch_cnbc, fetch_yahoo) if source == "cnbc" else (fetch_yahoo, fetch_cnbc)
    for fn in (primary, backup):
        try:
            r = fn(sym)
            if r and r.get("last") is not None:
                return r
        except Exception:
            continue
    return None


def latest():
    """Covariate accessor (for legs + brain): the most recent snapshot as a dict
    {instrument: {last, chg_pct}, 'ts': ...}. Returns {} if no tape yet. Read-only,
    macro CONTEXT only — NOT a trading signal (indices aren't Polymarket-tradeable;
    predictive legs on calibrated mids are the strategy graveyard, Lesson 15)."""
    try:
        last = None
        with open(LOG, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    last = line
        return json.loads(last) if last else {}
    except Exception:
        return {}


def summary_line():
    """Compact one-liner for BRAIN.md live state, e.g.
    'Markets: Sensex 77,300 (+0.3%) · Nifty 24,128 (+0.2%) · S&P 7,420 (-1.2%) · USD/INR 94.4'."""
    d = latest()
    if not d:
        return "Markets: (no data yet)"
    def cell(k):
        v = d.get(k)
        if not isinstance(v, dict) or v.get("last") is None:
            return None
        cp = v.get("chg_pct")
        return f"{k} {v['last']:,.0f}" + (f" ({cp:+.1f}%)" if cp is not None else "")
    parts = [c for c in (cell(k) for k in
             ("Sensex", "Nifty 50", "S&P 500", "Nasdaq", "Bitcoin", "USD/INR", "Gold")) if c]
    return "Markets: " + " · ".join(parts) if parts else "Markets: (no data yet)"


def main():
    ts = datetime.now(timezone.utc).isoformat()[:16] + "Z"
    rec = {"ts": ts}
    lines = ["---", "type: board", f"updated: {ts[:10]}", "tags:\n  - markets", "---", "",
             "# Markets", f"> [!info] Live India + global index & macro board · {ts} · keyless (CNBC + Yahoo). "
             "Auto-refreshed by `market_indices.py`. · 3-month study → [[Markets Analysis]]", ""]
    total = sum(len(its) for _, its in INSTRUMENTS)
    for group, items in INSTRUMENTS:
        lines += [f"## {group}", "", "| Instrument | Last | Chg % |", "|---|--:|--:|"]
        for label, source, sym in items:
            q = fetch(source, sym)
            if q and q.get("last") is not None:
                cp = q.get("change_pct")
                rec[label] = {"last": q["last"], "chg_pct": round(cp, 3) if cp is not None else None}
                if cp is not None:
                    mark = "🟢" if cp > 0 else ("🔴" if cp < 0 else "⚪")
                    lines.append(f"| {label} | {_fmt(q['last'])} | {mark} {cp:+.2f}% |")
                else:
                    lines.append(f"| {label} | {_fmt(q['last'])} | — |")
            else:
                lines.append(f"| {label} | — | _(fetch failed)_ |")
            time.sleep(0.25)
        lines.append("")

    os.makedirs(VAULT, exist_ok=True)
    with open(BOARD + ".tmp", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    os.replace(BOARD + ".tmp", BOARD)

    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
    try:
        ls = open(LOG, encoding="utf-8").readlines()
        if len(ls) > MAX_LOG:
            with open(LOG + ".tmp", "w", encoding="utf-8") as f:
                f.writelines(ls[-MAX_LOG:])
            os.replace(LOG + ".tmp", LOG)
    except Exception:
        pass

    got = sum(1 for g, its in INSTRUMENTS for l, s, sy in its if l in rec)
    print(f"market_indices: {got}/{total} instruments fetched · board → {BOARD}")


if __name__ == "__main__":
    main()
