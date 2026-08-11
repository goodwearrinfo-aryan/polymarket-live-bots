#!/usr/bin/env python3
"""
insider_score.py — flag/score every SHARP-wallet trade alert.
Read-only research; consumed by insider_watch.format_alert.

score_trade(addr, label, trade) -> (tier, score, tags)
  tier : "🔥 STRONG" / "👀 NOTABLE" / "💤 noise" / "🚱 FADE-SOURCE"
  score: 0-100
  tags : list of human-readable reasons

Components (all from data we already collect):
  WALLET  — net P&L from wallet_deep.json (position-level truth, all wallets)
            or WR parsed from the ONCHAIN/SHARP label as fallback.
  SIZE    — trade USD vs that wallet's median bet (wallet_algo.json);
            >=5x median or >=$500 = conviction bet.
  BAND    — entry price: profitable wallets earn at 0.00-0.30 (wallet_deep
            anatomy, 2026-06-12); entries >=0.40 are where they bleed.
  CAT-FIT — is this market in a category where THIS wallet makes money?
  NEWS    — does the market title overlap recent hermes_news headlines
            (:48580, local, instant)? News-hot markets reprice fast.
"""
import json, os, re, time, urllib.request

BASE   = os.path.expanduser("~/Documents/polymarket")
HERMES = "http://127.0.0.1:48580/api/news"

_cache = {"deep": None, "algo": None, "news": {"ts": 0, "words": set()}}

_CATS = {
    "esports":  ("counter-strike", "cs2", "valorant", "league of legends", "dota",
                 "iem ", "blast", "vct ", "lck", "lpl", "esport", "(bo3", "(bo5"),
    "sports":   ("nba", "nfl", "mlb", "nhl", "tennis", "atp", "wta", "soccer",
                 "spread:", "premier league", "ufc", " vs. ", " vs ", "o/u"),
    "crypto":   ("bitcoin", "btc", "ethereum", "eth ", "solana", "sol ", "xrp",
                 "crypto", "up or down"),
    "politics": ("election", "president", "senate", "congress", "trump",
                 "nominee", "governor", "parliament", "minister", "mayor"),
}

_STOP = {"will", "the", "win", "2026", "be", "on", "by", "in", "of", "to", "a",
         "and", "or", "vs", "for", "at", "up", "down", "june", "july"}


def _load(name, fname):
    if _cache[name] is None:
        try:
            _cache[name] = json.load(open(os.path.join(BASE, fname)))
        except Exception:
            _cache[name] = {}
    return _cache[name]


def _news_words(ttl=300):
    c = _cache["news"]
    if time.time() - c["ts"] < ttl:
        return c["words"]
    words = set()
    try:
        with urllib.request.urlopen(HERMES, timeout=2) as r:
            for it in json.loads(r.read()).get("items", [])[:400]:
                for w in re.findall(r"[a-z]{4,}", (it.get("title") or "").lower()):
                    if w not in _STOP:
                        words.add(w)
    except Exception:
        pass
    c.update({"ts": time.time(), "words": words})
    return words


def _cat_of(title):
    t = (title or "").lower()
    for cat, kws in _CATS.items():
        if any(k in t for k in kws):
            return cat
    return "other"


def score_trade(addr, label, trade):
    deep = _load("deep", "wallet_deep.json").get(addr, {})
    algo = _load("algo", "wallet_algo.json").get(addr, {})
    title = trade.get("title") or ""
    price = float(trade.get("price") or 0)
    usd   = price * float(trade.get("size") or 0)

    score, tags = 0, []

    # WALLET quality (0-40)
    net = deep.get("net")
    if net is None:
        m = re.search(r"(\d+)pct", label or "")
        wr = int(m.group(1)) if m else 50
        score += 20 if wr >= 70 else 10 if wr >= 60 else 0
        tags.append(f"wallet WR~{wr}% (unprofiled)")
        fade_source = False
    else:
        fade_source = net < -1000
        if net > 20000:
            score += 40; tags.append(f"wallet net +${net/1000:.0f}k (top tier)")
        elif net > 0:
            score += 20; tags.append(f"wallet net +${net:,.0f}")
        else:
            tags.append(f"wallet net ${net:,.0f} (NET LOSER)")

    # SIZE conviction (0-25)
    med = algo.get("usd_median") or 10
    if usd >= max(500, 5 * med):
        score += 25; tags.append(f"${usd:,.0f} = {usd/med:.0f}x wallet median (CONVICTION)")
    elif usd >= 2 * med:
        score += 10; tags.append(f"${usd:,.0f} = {usd/med:.1f}x median")
    else:
        tags.append(f"${usd:,.0f} routine size")

    # BAND (0-15): profitable wallets earn cheap
    if trade.get("side") == "BUY":
        if price <= 0.30:
            score += 15; tags.append(f"entry {price:.2f} in winners' band (0-0.30)")
        elif price >= 0.40:
            tags.append(f"entry {price:.2f} in losers' band (0.40+)")

    # CAT-FIT (0-10)
    cat = _cat_of(title)
    cat_net = (deep.get("by_cat") or {}).get(cat, {}).get("net")
    if cat_net is not None:
        if cat_net > 0:
            score += 10; tags.append(f"{cat}: wallet's winning category (+${cat_net:,.0f})")
        else:
            tags.append(f"{cat}: wallet LOSES here (${cat_net:,.0f})")

    # NEWS heat (0-10)
    t_words = {w for w in re.findall(r"[a-z]{4,}", title.lower()) if w not in _STOP}
    overlap = t_words & _news_words()
    if len(overlap) >= 2:
        score += 10; tags.append(f"news-hot: {', '.join(sorted(overlap)[:3])}")

    if fade_source:
        tier = "🚱 FADE-SOURCE"
    elif score >= 65:
        tier = "🔥 STRONG"
    elif score >= 40:
        tier = "👀 NOTABLE"
    else:
        tier = "💤 noise"
    return tier, score, tags


if __name__ == "__main__":
    demo = {"title": "Will Brazil win on 2026-06-13?", "price": 0.22,
            "size": 3000, "side": "BUY"}
    t, s, tg = score_trade("0x84cfffc3f16dcc353094de30d4a45226eccd2f63",
                           "SHARP-80pct-$112k", demo)
    print(t, s, "|", "; ".join(tg))
