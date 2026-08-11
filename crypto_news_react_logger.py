#!/usr/bin/env python3
"""crypto_news_react_logger.py — READ-ONLY forward-return logger (NOT a trading leg).

Tests ONE honest hypothesis the cheap way: does a fresh crypto HEADLINE precede a
forward PRICE move in BTC/ETH, and does the headline's crude sentiment sign predict
the direction? (News -> price.)

HONEST PRIOR — likely NULL. On a liquid, bot-priced market a PUBLIC headline is the
definition of already-priced; every attention/news leg on this project's venue has
died (calibrated mids). This logger exists to let the DATA say yes/no forward-only,
without risking a cent — not because we expect a yes. A non-zero bucket here is a
LEAD to verify (n>=30, bootstrap CI excludes 0 AFTER cost, holds OOS), never an edge.

Method (no lookahead, like gtrends_logger / candle_knowledge_logger):
  observe NOW: pull keyless crypto-news RSS, take each NEW headline (dedup by link),
  map it to an asset (btc/eth) by keyword, tag a crude keyword sentiment (pos/neg/neu),
  and snapshot that asset's spot price NOW (keyless Binance ticker, CoinGecko fallback).
  Then FORWARD-record the realized return at +15m / +60m / +240m. The forward return is
  the only judge; its sign+size per sentiment/burst bucket is the whole result.

Report views:
  - FORWARD log (the truth): no-lookahead samples accumulated from deployment onward.
  - SENTIMENT bucket table: mean forward return split by pos/neg/neu, per horizon.
  - BURST bucket: high headline-rate hours vs calm, per horizon.
  Early n is tiny — read as a hint, never a verdict.

PAPER ONLY, read-only, keyless (RSS + Binance/CoinGecko). stdlib only.
Usage: python3 crypto_news_react_logger.py once | report
"""
import os, sys, json, time, re, html, urllib.request, urllib.error
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "crypto_news_react_state.json")
LOG = os.path.join(HERE, "crypto_news_react_log.jsonl")
VAULT = os.path.expanduser("~/Documents/PolymarketVault/Reports/crypto_news_react.md")
UA = {"User-Agent": "Mozilla/5.0 (news-react-logger; keyless research)"}

FEEDS = [
    "https://www.coindesk.com/arc/outboundfeeds/rss",
    "https://cointelegraph.com/rss",
    "https://decrypt.co/feed",
    "https://cryptoslate.com/feed/",
]

# asset -> (keyword regex, Binance symbol, CoinGecko id)
ASSETS = {
    "btc": (re.compile(r"\b(bitcoin|btc|satoshi)\b", re.I), "BTCUSDT", "bitcoin"),
    "eth": (re.compile(r"\b(ethereum|ether|eth\b)", re.I), "ETHUSDT", "ethereum"),
}
# crude keyless sentiment lexicon (headline-level only; NOT an LLM, NOT an edge)
POS = re.compile(r"\b(surge|soar|rally|jump|gain|bull|record|approv|adopt|inflow|"
                 r"upgrade|breakout|high|pump|rip|moon|beats?)\b", re.I)
NEG = re.compile(r"\b(crash|plunge|drop|fall|slump|bear|hack|exploit|ban|lawsuit|"
                 r"sec\b|sell.?off|liquidat|outflow|dump|fear|reject|delay|down)\b", re.I)

HORIZONS_M = [15, 60, 240]
RESOLVE_TOL_S = 90            # resolve a horizon once now >= obs + H - 90s
PENDING_MAX_AGE_S = 6 * 3600 # all horizons due by 4h; drop stragglers after 6h
MAX_HEADLINES_PER_RUN = 40   # politeness / noise cap


# ---------- keyless fetchers (all fail-soft) ----------
def _get(url, timeout=20):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _parse_feed(url):
    """Return list of (title, link, epoch_pub) from an RSS/Atom feed; [] on failure."""
    try:
        raw = _get(url)
    except Exception:
        return []
    out = []
    try:
        root = ET.fromstring(raw)
    except Exception:
        return []
    # RSS <item> and Atom <entry>
    for item in root.iter():
        tag = item.tag.split("}")[-1]
        if tag not in ("item", "entry"):
            continue
        title = link = pub = None
        for ch in item:
            ctag = ch.tag.split("}")[-1]
            if ctag == "title":
                title = (ch.text or "").strip()
            elif ctag == "link":
                link = (ch.text or "").strip() or ch.attrib.get("href", "")
            elif ctag in ("pubDate", "published", "updated"):
                pub = (ch.text or "").strip()
        if not title or not link:
            continue
        epoch = None
        if pub:
            try:
                epoch = int(parsedate_to_datetime(pub).timestamp())
            except Exception:
                epoch = None
        out.append((html.unescape(title), link, epoch))
    return out


def _binance_price(symbol):
    try:
        raw = _get(f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}", 12)
        return float(json.loads(raw)["price"])
    except Exception:
        return None


def _coingecko_price(cg_id):
    try:
        raw = _get("https://api.coingecko.com/api/v3/simple/price"
                   f"?ids={cg_id}&vs_currencies=usd", 12)
        return float(json.loads(raw)[cg_id]["usd"])
    except Exception:
        return None


def _spot(asset):
    _re, sym, cg = ASSETS[asset]
    return _binance_price(sym) or _coingecko_price(cg)


def _sentiment(title):
    p, n = bool(POS.search(title)), bool(NEG.search(title))
    if p and not n:
        return "pos"
    if n and not p:
        return "neg"
    return "neu"


def _asset_of(title):
    for a, (rx, _s, _c) in ASSETS.items():
        if rx.search(title):
            return a
    return None


# ---------- state / log ----------
def _load_state():
    try:
        return json.load(open(STATE))
    except Exception:
        return {"pending": [], "seen": []}   # seen = list of headline links already observed


def _save_state(st):
    st["seen"] = st["seen"][-5000:]
    st["pending"] = st["pending"][-4000:]
    tmp = STATE + ".tmp"
    json.dump(st, open(tmp, "w"))
    os.replace(tmp, STATE)


def _append_log(rec):
    with open(LOG, "a") as f:
        f.write(json.dumps(rec) + "\n")


# ---------- once ----------
def run_once():
    st = _load_state()
    now = int(time.time())
    seen = set(st["seen"])

    # 1) observe: collect new headlines across feeds
    headlines = []
    for url in FEEDS:
        for title, link, epoch in _parse_feed(url):
            if link in seen:
                continue
            asset = _asset_of(title)
            if not asset:
                continue                       # only log headlines mapped to BTC/ETH
            headlines.append((title, link, asset))
    headlines = headlines[:MAX_HEADLINES_PER_RUN]

    burst = len(headlines)                      # this-run headline count (burst proxy)
    spot_cache = {}
    new_pending = 0
    for title, link, asset in headlines:
        if asset not in spot_cache:
            spot_cache[asset] = _spot(asset)
        px = spot_cache[asset]
        seen.add(link)
        if px is None:
            continue
        st["pending"].append({
            "asset": asset, "link": link, "title": title[:200],
            "sent": _sentiment(title), "obs_t": now, "obs_px": px,
            "burst": burst, "done": [],
        })
        new_pending += 1

    # 2) resolve: for each pending, record any horizon now due
    still = []
    resolved = 0
    spot_now = {}
    for s in st["pending"]:
        due_all = True
        for H in HORIZONS_M:
            if H in s["done"]:
                continue
            if now >= s["obs_t"] + H * 60 - RESOLVE_TOL_S:
                a = s["asset"]
                if a not in spot_now:
                    spot_now[a] = _spot(a)
                px_now = spot_now[a]
                if px_now and s["obs_px"]:
                    ret = (px_now - s["obs_px"]) / s["obs_px"]
                    _append_log({
                        "asset": a, "sent": s["sent"], "burst": s["burst"],
                        "h_min": H, "obs_t": s["obs_t"], "res_t": now,
                        "ret": round(ret, 6), "title": s["title"],
                    })
                    s["done"].append(H)
                    resolved += 1
            else:
                due_all = False
        # keep if horizons remain and not too old
        if len(s["done"]) < len(HORIZONS_M) and now - s["obs_t"] < PENDING_MAX_AGE_S:
            still.append(s)
    st["pending"] = still
    st["seen"] = list(seen)
    _save_state(st)
    print(f"crypto_news_react once: +{new_pending} pending (burst={burst}), "
          f"resolved {resolved} horizons, {len(still)} pending open")


# ---------- report ----------
def _read_log():
    rows = []
    try:
        for line in open(LOG):
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    except FileNotFoundError:
        pass
    return rows


def _bucket(rows, keyfn):
    b = {}
    for r in rows:
        b.setdefault((keyfn(r), r["h_min"]), []).append(r["ret"])
    return b


def _fmt(vals):
    n = len(vals)
    if n == 0:
        return "n=0"
    m = sum(vals) / n
    if n > 1:
        var = sum((x - m) ** 2 for x in vals) / (n - 1)
        se = (var / n) ** 0.5
    else:
        se = float("nan")
    t = (m / se) if se and se == se and se > 0 else float("nan")
    return f"n={n:<4d} mean={m*100:+.3f}% t={t:+.2f}"


def write_report():
    rows = _read_log()
    os.makedirs(os.path.dirname(VAULT), exist_ok=True)
    lines = [
        "# Crypto News → Price Reaction (forward-return logger)",
        "",
        "type: report",
        "status: research",
        "",
        f"_Forward-only, no-lookahead. {len(rows)} resolved samples. "
        "PAPER, read-only, keyless. Prior = NULL; a non-zero bucket is a LEAD to verify "
        "(n≥30, bootstrap CI excludes 0 after cost, holds OOS), never an edge._",
        "",
        "## By sentiment × horizon",
    ]
    sb = _bucket(rows, lambda r: r["sent"])
    for sent in ("pos", "neg", "neu"):
        for H in HORIZONS_M:
            lines.append(f"- **{sent} / +{H}m**: {_fmt(sb.get((sent, H), []))}")
    lines += ["", "## By headline burst (this-run count ≥3 = burst) × horizon"]
    bb = _bucket(rows, lambda r: "burst" if r.get("burst", 0) >= 3 else "calm")
    for k in ("burst", "calm"):
        for H in HORIZONS_M:
            lines.append(f"- **{k} / +{H}m**: {_fmt(bb.get((k, H), []))}")
    lines.append("")
    tmp = VAULT + ".tmp"
    open(tmp, "w").write("\n".join(lines))
    os.replace(tmp, VAULT)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "once"
    if cmd == "report":
        write_report()
        print(f"crypto_news_react report: {len(_read_log())} samples -> {VAULT}")
    else:
        run_once()


if __name__ == "__main__":
    main()
