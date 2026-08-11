#!/usr/bin/env python3
"""
crypto_data_report.py — live crypto data view for the Polymarket bot.

Shows: (1) current CoinGecko spot + 24h/7d change for major coins,
and (2) the hottest crypto markets the bot is currently tracking,
ranked by the heatmap momentum_score (with net trader flow + elite entries).

Read-only. Pulls public CoinGecko prices; reads the bot's market_heatmap.json snapshot.
Usage: python3 crypto_data_report.py [path/to/market_heatmap.json]
"""
import urllib.request, urllib.parse, json, re, sys, os, datetime
from collections import Counter

UA = "Mozilla/5.0 (crypto-data-report)"

# Full coin roster — CoinGecko IDs → display symbol
COINS = {
    # ── Majors ────────────────────────────────────────────────────────────────
    "bitcoin":       "BTC",
    "ethereum":      "ETH",
    "binancecoin":   "BNB",
    "solana":        "SOL",
    "ripple":        "XRP",
    "dogecoin":      "DOGE",
    # ── Large caps ─────────────────────────────────────────────────────────────
    "toncoin":       "TON",
    "tron":          "TRX",
    "cardano":       "ADA",
    "avalanche-2":   "AVAX",
    "shiba-inu":     "SHIB",
    "polkadot":      "DOT",
    "chainlink":     "LINK",
    "near":          "NEAR",
    "litecoin":      "LTC",
    "uniswap":       "UNI",
    "cosmos":        "ATOM",
    # ── Mid caps / trending ────────────────────────────────────────────────────
    "sui":           "SUI",
    "aptos":         "APT",
    "arbitrum":      "ARB",
    "optimism":      "OP",
    "stellar":       "XLM",
    # ── Meme coins ─────────────────────────────────────────────────────────────
    "pepe":          "PEPE",
    "dogwifhat":     "WIF",
    "bonk":          "BONK",
    "floki":         "FLOKI",
}

CRYPTO_RE = re.compile(
    r'\b(bitcoin|btc|ethereum|eth|bnb|solana|sol|xrp|ripple|dogecoin|doge|crypto'
    r'|ton|toncoin|tron|trx|cardano|ada|avalanche|avax|shiba|shib'
    r'|polkadot|dot|chainlink|link|near|litecoin|ltc|uniswap|uni|cosmos|atom'
    r'|sui|aptos|apt|arbitrum|arb|optimism|stellar|xlm'
    r'|pepe|dogwifhat|wif|bonk|floki)\b', re.I)
UD_RE = re.compile(r'up or down', re.I)

COIN_LABEL = {
    "btc":"BTC","bitcoin":"BTC","eth":"ETH","ethereum":"ETH",
    "sol":"SOL","solana":"SOL","ripple":"XRP","xrp":"XRP",
    "doge":"DOGE","dogecoin":"DOGE","bnb":"BNB",
    "ton":"TON","toncoin":"TON","trx":"TRX","tron":"TRX",
    "ada":"ADA","cardano":"ADA","avax":"AVAX","avalanche":"AVAX",
    "shib":"SHIB","shiba":"SHIB","dot":"DOT","polkadot":"DOT",
    "link":"LINK","chainlink":"LINK","near":"NEAR","ltc":"LTC","litecoin":"LTC",
    "uni":"UNI","uniswap":"UNI","atom":"ATOM","cosmos":"ATOM",
    "sui":"SUI","apt":"APT","aptos":"APT","arb":"ARB","arbitrum":"ARB",
    "op":"OP","optimism":"OP","xlm":"XLM","stellar":"XLM",
    "pepe":"PEPE","wif":"WIF","dogwifhat":"WIF","bonk":"BONK","floki":"FLOKI",
}

def get(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode("utf-8","replace"))
    except Exception as e:
        return {"_err": str(e)}

def coin_of(title):
    m = CRYPTO_RE.search(title)
    if not m: return "?"
    return COIN_LABEL.get(m.group(1).lower(), m.group(1).upper())

# ── 1. live spot prices ───────────────────────────────────────────────────────
ids = ",".join(COINS.keys())
px = get(f"https://api.coingecko.com/api/v3/simple/price?ids={ids}"
         f"&vs_currencies=usd&include_24hr_change=true&include_7d_change=true&include_market_cap=true")

print("="*72)
print(" LIVE CRYPTO SPOT  (CoinGecko)   " + datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
print("="*72)

if "_err" in px:
    print("  price feed unavailable:", px["_err"])
else:
    # group into sections
    sections = [
        ("── Majors ─────────────────────────────────────────────────────────",
         ["bitcoin","ethereum","binancecoin","solana","ripple","dogecoin"]),
        ("── Large caps ──────────────────────────────────────────────────────",
         ["toncoin","tron","cardano","avalanche-2","shiba-inu","polkadot",
          "chainlink","near","litecoin","uniswap","cosmos"]),
        ("── Mid caps / trending ─────────────────────────────────────────────",
         ["sui","aptos","arbitrum","optimism","stellar"]),
        ("── Meme coins ──────────────────────────────────────────────────────",
         ["pepe","dogwifhat","bonk","floki"]),
    ]
    print(f"  {'coin':6s} {'price':>15s} {'24h':>9s} {'7d':>9s} {'mkt cap':>12s}")
    for header, coin_ids in sections:
        print(f"  {header}")
        for cid in coin_ids:
            sym = COINS.get(cid, cid.upper())
            d = px.get(cid, {})
            if not d:
                print(f"  {sym:6s}  (no data)"); continue
            p   = d.get("usd", 0)
            ch  = d.get("usd_24h_change", 0) or 0
            ch7 = d.get("usd_7d_change",  0) or 0
            mc  = d.get("usd_market_cap", 0) or 0
            a24 = "▲" if ch  >= 0 else "▼"
            a7  = "▲" if ch7 >= 0 else "▼"
            mc_str = f"{mc/1e9:7.2f}B" if mc >= 1e9 else f"{mc/1e6:7.2f}M"
            # format price smartly
            if p >= 1000:    p_str = f"{p:>15,.2f}"
            elif p >= 1:     p_str = f"{p:>15,.4f}"
            elif p >= 0.001: p_str = f"{p:>15,.6f}"
            else:            p_str = f"{p:>15,.8f}"
            print(f"  {sym:6s} {p_str} {a24}{abs(ch):>6.2f}% {a7}{abs(ch7):>6.2f}% {mc_str:>12s}")

# ── 2. hottest tracked crypto markets ────────────────────────────────────────
hm_path = sys.argv[1] if len(sys.argv)>1 else os.path.expanduser("~/Documents/polymarket/market_heatmap.json")
print("\n" + "="*72)
print(" CRYPTO MARKETS THE BOT IS TRACKING")
print(f" source: {hm_path}")
print("="*72)
try:
    hm = json.load(open(hm_path))
except Exception as e:
    print("  heatmap unavailable:", e); sys.exit(0)

crypto = [v for v in hm.values() if CRYPTO_RE.search(v.get("title",""))]
ud     = [v for v in crypto if UD_RE.search(v.get("title",""))]
print(f"  total markets tracked: {len(hm):,}   crypto: {len(crypto):,}   up/down: {len(ud):,}")

# coin breakdown
cc = Counter(coin_of(v["title"]) for v in crypto)
top10 = "  ".join(f"{c}={n}" for c,n in cc.most_common(10))
print(f"  by coin: {top10}")

# hottest by momentum_score
crypto.sort(key=lambda v: v.get("momentum_score",0), reverse=True)
print("\n  🔥 hottest crypto markets (by momentum_score):")
print(f"  {'mom':>7s} {'net':>6s} {'elite':>6s}  title")
for v in crypto[:20]:
    print(f"  {v.get('momentum_score',0):7.1f} {int(v.get('net_flow',0)):>6d} "
          f"{int(v.get('elite_opens',0)):>6d}  {v.get('title','')[:55]}")

# freshness
try:
    mt = max((v.get("last_activity","") for v in crypto), default="")
    print(f"\n  most recent crypto activity: {mt}")
except Exception:
    pass
