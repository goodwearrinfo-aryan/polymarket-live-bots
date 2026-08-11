#!/usr/bin/env python3
"""
desk_data.py — Tier-2 keyless "desk read" enrichers for info_bot.

Tier-1 (info_bot) gives every topic price + web + finance-news. Tier-2 deepens the
three market DOMAINS with the keyless data this project already has wired, turning
"price + news" into a desk read — no new keys, just wiring:

  crypto_desk()  Fear & Greed index, Binance perp funding + open interest (positioning),
                 Deribit ATM IV + 25Δ skew (via deribit_vol), DefiLlama stablecoin
                 supply flows + aggregate DeFi TVL.
  macro_desk()   US Treasury par yield curve (+2s10s), the latest BLS CPI print
                 (YoY/MoM), and a DATED upcoming-auction calendar (catalysts, not
                 commentary) — the keyless stand-in for an events calendar.
  geo_desk()     IMF PortWatch chokepoint traffic (7d-MA transit calls; the Hormuz
                 data-arb that has actually paid) + maritime headline feed.

Discipline carried from the rest of the fleet:
  • FAIL-SOFT — every sub-source is optional and self-reports under `degraded`, so one
    dead feed never sinks the brief ("silence != success").
  • QUOTA-AWARE — results are file-cached under the same ~/.info_swarm root info_bot
    uses, so a frequent scheduled cadence respects free-tier caps (BLS anonymous =
    25 queries/day is the binding constraint). ttl<=0 -> always live.
  • REUSE — leans on data_sources.fetch, deribit_vol.term_structure and
    analyst_data_gate.fetch_portwatch rather than re-implementing fetchers.

Keyless, read-only, paper. Reads public data, never trades.

  import desk_data; print(desk_data.crypto_desk())
  python3 desk_data.py            # live self-test of all three desks
"""
import os, sys, json, time, hashlib, re
import datetime as _dt
import urllib.request
import data_sources as DS

_CACHE_ROOT = os.path.expanduser("~/.info_swarm")
_UA = {"User-Agent": "polymarket-bot/1.0 (keyless)"}


def _http_json(url, timeout=15):
    req = urllib.request.Request(url, headers=_UA)
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())


def _cached(ns, key, ttl, fetch):
    """File cache mirroring info_bot._cached: reuse within ttl seconds so a frequent
    cadence doesn't blow a provider's free quota. ttl<=0 -> always live. Atomic write;
    only caches successful (ok:True) results."""
    if ttl <= 0:
        return fetch()
    fp = None
    try:
        d = os.path.join(_CACHE_ROOT, ns)
        os.makedirs(d, exist_ok=True)
        fp = os.path.join(d, hashlib.sha1(key.encode()).hexdigest()[:16] + ".json")
        if os.path.exists(fp) and (time.time() - os.path.getmtime(fp)) < ttl:
            with open(fp) as f:
                out = json.load(f)
            out["_cached"] = True
            return out
    except Exception:
        fp = None
    out = fetch()
    if isinstance(out, dict) and out.get("ok") and fp:
        try:
            tmp = fp + ".tmp"
            with open(tmp, "w") as f:
                json.dump(out, f)
            os.replace(tmp, fp)
        except Exception:
            pass
    return out


def _assemble(parts):
    """Run each (label, fn) fail-soft; collect real results + a degraded ledger.
    ok=True iff at least one sub-source produced data."""
    out = {"degraded": []}
    for label, fn in parts:
        try:
            v = fn()
            if v:
                out[label] = v
            else:
                out["degraded"].append(f"{label}: empty")
        except Exception as e:
            out["degraded"].append(f"{label}: {type(e).__name__}: {e}")
    out["ok"] = any(k not in ("degraded", "ok", "_cached") for k in out)
    return out


# ───────────────────────── crypto desk ─────────────────────────

def _fear_greed():
    j = _http_json("https://api.alternative.me/fng/?limit=8")
    data = j.get("data") or []
    if not data:
        return None
    cur = data[0]
    out = {"value": int(cur["value"]), "label": cur.get("value_classification")}
    if len(data) > 7:
        wk = int(data[7]["value"])
        out["week_ago"] = wk
        out["delta_7d"] = out["value"] - wk
    return out


def _binance_perp(sym):
    """Funding rate (+ annualized), spot-perp basis, and 24h open-interest change."""
    pi = _http_json(f"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={sym}")
    fr = float(pi["lastFundingRate"])
    mark, index = float(pi["markPrice"]), float(pi["indexPrice"])
    rec = {"funding_rate": fr,                              # per 8h
           "funding_annualized_pct": round(fr * 3 * 365 * 100, 2),
           "basis_bps": round((mark - index) / index * 1e4, 1) if index else None}
    try:
        oi = _http_json(f"https://fapi.binance.com/futures/data/openInterestHist"
                        f"?symbol={sym}&period=1h&limit=24")
        if oi:
            latest = float(oi[-1]["sumOpenInterestValue"])
            first = float(oi[0]["sumOpenInterestValue"])
            rec["oi_usd"] = latest
            rec["oi_chg_24h_pct"] = round((latest - first) / first * 100, 2) if first else None
    except Exception:
        pass
    return rec


def _binance_perps(syms):
    out = {}
    for s in syms:
        try:
            out[s] = _binance_perp(s)
        except Exception:
            continue
    return out or None


def _deribit_vol():
    """Front-expiry ATM implied vol + 25%-OTM put/call skew + VRP, BTC & ETH (keyless)."""
    import deribit_vol as DV
    out = {}
    for cur, sym, idx_name in DV.ASSETS:
        try:
            idx, rows = DV.term_structure(cur, sym, idx_name)
        except Exception:
            continue
        if not rows:
            continue
        near = min(rows, key=lambda r: r["dte"])
        out[cur] = {
            "spot": round(idx, 0),
            "front_dte": round(near["dte"], 1),
            "atm_iv": round(near["atm_iv"], 1),
            "skew_25d": round(near["skew"], 1) if near.get("skew") is not None else None,
            "vrp": round(near["vrp"], 1) if near.get("vrp") is not None else None,
        }
    return out or None


def _defillama():
    """Aggregate stablecoin supply (+1d flow) and total DeFi TVL — liquidity/risk proxy."""
    out = {}
    try:
        j = _http_json("https://stablecoins.llama.fi/stablecoins?includePrices=true", timeout=25)
        assets = j.get("peggedAssets") or []

        def _su(a, key):
            return float((a.get(key) or {}).get("peggedUSD") or 0)
        tot = sum(_su(a, "circulating") for a in assets)
        prev = sum(_su(a, "circulatingPrevDay") for a in assets)
        if tot:
            out["stablecoin_mcap_usd"] = round(tot)
            if prev:
                out["stablecoin_chg_1d_usd"] = round(tot - prev)
                out["stablecoin_chg_1d_pct"] = round((tot - prev) / prev * 100, 2)
    except Exception:
        pass
    try:
        chains = _http_json("https://api.llama.fi/v2/chains", timeout=20)
        tvl = sum(float(c.get("tvl") or 0) for c in chains)
        if tvl:
            out["defi_tvl_usd"] = round(tvl)
            top = max(chains, key=lambda c: float(c.get("tvl") or 0))
            out["top_chain"] = {"name": top.get("name"), "tvl_usd": round(float(top.get("tvl") or 0))}
    except Exception:
        pass
    return out or None


def crypto_desk(ttl=0, assets=("BTCUSDT", "ETHUSDT")):
    """Fear&Greed + perp funding/OI + Deribit IV/skew + stablecoin/TVL flows."""
    return _cached("desk_crypto", "|".join(assets), ttl, lambda: _assemble([
        ("fear_greed", _fear_greed),
        ("perp", lambda: _binance_perps(assets)),
        ("deribit_vol", _deribit_vol),
        ("defillama", _defillama),
    ]))


# ───────────────────────── macro desk ─────────────────────────

def _treasury_yields():
    """Latest US Treasury par yield curve (3M/2Y/10Y/30Y) + 2s10s spread, keyless XML."""
    import xml.etree.ElementTree as ET
    ns = {"a": "http://www.w3.org/2005/Atom",
          "m": "http://schemas.microsoft.com/ado/2007/08/dataservices/metadata",
          "d": "http://schemas.microsoft.com/ado/2007/08/dataservices"}
    base = ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
            "pages/xml?data=daily_treasury_yield_curve&field_tdr_date_value=")
    this_year = _dt.date.today().year
    entries = []
    for year in (this_year, this_year - 1):   # early-January: current year may be empty
        raw = urllib.request.urlopen(urllib.request.Request(base + str(year),
                                     headers={"User-Agent": "Mozilla/5.0"}), timeout=20).read()
        entries = ET.fromstring(raw).findall("a:entry", ns)
        if entries:
            break
    if not entries:
        return None
    props = entries[-1].find("a:content/m:properties", ns)

    def g(tag):
        el = props.find(f"d:{tag}", ns)
        try:
            return float(el.text)
        except Exception:
            return None
    date_el = props.find("d:NEW_DATE", ns)
    out = {"date": (date_el.text[:10] if date_el is not None and date_el.text else None),
           "y3m": g("BC_3MONTH"), "y2y": g("BC_2YEAR"),
           "y10y": g("BC_10YEAR"), "y30y": g("BC_30YEAR")}
    if out["y2y"] is not None and out["y10y"] is not None:
        out["spread_2s10s_bps"] = round((out["y10y"] - out["y2y"]) * 100, 1)
    return out


def _bls_cpi():
    """Latest BLS CPI-U (CUUR0000SA0) index level with YoY + MoM (anonymous tier)."""
    j = _http_json("https://api.bls.gov/publicAPI/v2/timeseries/data/CUUR0000SA0", timeout=20)
    series = (j.get("Results", {}).get("series") or [])
    if not series:
        return None
    data = series[0].get("data") or []

    def _f(x):
        try:
            return float(x)
        except (TypeError, ValueError):
            return None
    pts = [(d["year"], d["period"], _f(d.get("value")), d.get("periodName"))
           for d in data if str(d.get("period", "")).startswith("M")]
    pts = [p for p in pts if p[2] is not None]   # BLS sometimes returns '-' for a value
    if not pts:
        return None
    pts.sort(key=lambda p: (p[0], p[1]), reverse=True)   # newest first
    latest = pts[0]
    out = {"index": latest[2], "month": f"{latest[3]} {latest[0]}"}
    if len(pts) > 1 and pts[1][2]:
        out["mom_pct"] = round((latest[2] / pts[1][2] - 1) * 100, 2)
    yoy = next((p for p in pts if p[0] == str(int(latest[0]) - 1) and p[1] == latest[1]), None)
    if yoy and yoy[2]:
        out["yoy_pct"] = round((latest[2] / yoy[2] - 1) * 100, 2)
    return out


def _treasury_calendar(days=10):
    """Dated upcoming Treasury auctions within `days` — the keyless 'what's on the
    calendar' line (real catalysts, not commentary)."""
    j = _http_json("https://www.treasurydirect.gov/TA_WS/securities/upcoming?format=json", timeout=20)
    if not isinstance(j, list):
        return None
    today = _dt.date.today()
    horizon = today + _dt.timedelta(days=days)
    items = []
    for r in j:
        ad = r.get("auctionDate")
        if not ad:
            continue
        try:
            d = _dt.date.fromisoformat(ad[:10])
        except Exception:
            continue
        if today <= d <= horizon:
            lbl = f"{r.get('securityTerm', '')} {r.get('securityType', '')}".strip()
            items.append((d.isoformat(), lbl))
    items.sort()
    # de-dupe same date+label, keep order
    seen, uniq = set(), []
    for d, lbl in items:
        k = (d, lbl)
        if k not in seen:
            seen.add(k)
            uniq.append({"date": d, "event": lbl})
    return uniq[:6] or None


def macro_desk(ttl=0):
    """Treasury yield curve + latest CPI print + dated upcoming-auction calendar."""
    return _cached("desk_macro", "v1", ttl, lambda: _assemble([
        ("yields", _treasury_yields),
        ("cpi", _bls_cpi),
        ("calendar", _treasury_calendar),
    ]))


# ──────────────────────── geopolitics desk ────────────────────────

def _portwatch(ports):
    """IMF PortWatch 7d-MA transit calls per chokepoint (reuses analyst_data_gate)."""
    import analyst_data_gate as A
    out = {}
    for p in ports:
        try:
            r = A.fetch_portwatch(p)
        except Exception:
            continue
        if isinstance(r, dict) and r.get("ok"):
            out[p] = {"ma7_transit_calls": r.get("ma7"),
                      "max_30d": r.get("max_30d"),
                      "latest_date": r.get("latest_date"),
                      "data_lag_days": r.get("data_lag_days"),
                      "stale_warn": r.get("stale_warn")}
    return out or None


def _shipping_news(n=3):
    """Top maritime headlines (gCaptain RSS) — a geo feed independent of the LLM web search."""
    raw = DS.fetch("gCaptain — maritime news RSS", timeout=15).decode("utf-8", "replace")
    titles = re.findall(r"<title>\s*(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?\s*</title>", raw, re.S)
    titles = [re.sub(r"\s+", " ", t).strip() for t in titles if t.strip()]
    if not titles:
        return None
    # the feed name appears in <channel><title> AND <image><title> -> drop every copy of it
    channel = titles[0]
    return [t for t in titles[1:] if t != channel][:n] or None


def geo_desk(ttl=0, ports=("Strait of Hormuz", "Suez Canal", "Bab el-Mandeb Strait", "Panama Canal")):
    """Chokepoint transit traffic (the Hormuz data-arb) + maritime headlines."""
    return _cached("desk_geo", "|".join(ports), ttl, lambda: _assemble([
        ("chokepoints", lambda: _portwatch(ports)),
        ("shipping_news", _shipping_news),
    ]))


# ───────────────────── info_bot integration hooks ─────────────────────
# Kept here (not in info_bot) so the Tier-2 fetch + render logic lives in one module
# and info_bot's touch stays a one-liner each.

def gather(ctx, crypto=False, macro=False, geo=False, ttl=0):
    """Fetch the enabled desks into an info_bot `ctx` dict, fail-soft and self-reporting.
    Mutates ctx (adds crypto_desk/macro_desk/geo_desk + sources_used/degraded)."""
    for on, key, fn in ((crypto, "crypto_desk", crypto_desk),
                        (macro, "macro_desk", macro_desk),
                        (geo, "geo_desk", geo_desk)):
        if not on:
            continue
        try:
            d = fn(ttl=ttl)
        except Exception as e:
            ctx.setdefault("degraded", []).append(f"{key}: {type(e).__name__}: {e}")
            continue
        if d.get("ok"):
            ctx[key] = d
            ctx.setdefault("sources_used", []).append(key + (",cached" if d.get("_cached") else ""))
        ctx.setdefault("degraded", []).extend(f"{key}:{x}" for x in d.get("degraded", []))
    return ctx


def _b(usd):
    """Compact $ in billions."""
    try:
        return f"${usd/1e9:.1f}B"
    except Exception:
        return "n/a"


def render(ctx):
    """Render any desks present in `ctx` into context-block lines for the LLM prompt."""
    lines = []
    cd = ctx.get("crypto_desk")
    if cd:
        lines.append("CRYPTO DESK (keyless positioning / vol / flows):")
        fg = cd.get("fear_greed")
        if fg:
            wk = f", {fg['delta_7d']:+d} vs 7d ago" if fg.get("delta_7d") is not None else ""
            lines.append(f"  - Fear & Greed: {fg['value']} ({fg.get('label')}){wk}")
        for sym, p in (cd.get("perp") or {}).items():
            oi = (f", OI {_b(p['oi_usd'])} ({p['oi_chg_24h_pct']:+.2f}% 24h)"
                  if p.get("oi_usd") is not None else "")
            basis = f", basis {p['basis_bps']}bps" if p.get("basis_bps") is not None else ""
            lines.append(f"  - {sym} perp: funding {p['funding_rate']*100:+.4f}%/8h "
                         f"({p['funding_annualized_pct']:+.1f}% ann){basis}{oi}")
        for cur, v in (cd.get("deribit_vol") or {}).items():
            lines.append(f"  - {cur} options: ATM IV {v['atm_iv']}% ({v['front_dte']}d to exp), "
                         f"25Δ skew(P−C) {v.get('skew_25d')}, VRP(IV−RV) {v.get('vrp')}")
        dl = cd.get("defillama")
        if dl:
            if dl.get("stablecoin_mcap_usd"):
                pct = (f"{dl['stablecoin_chg_1d_pct']:+.2f}% 1d"
                       if dl.get("stablecoin_chg_1d_pct") is not None else "n/a")
                lines.append(f"  - Stablecoin supply {_b(dl['stablecoin_mcap_usd'])} ({pct})")
            if dl.get("defi_tvl_usd"):
                top = dl.get("top_chain") or {}
                tail = f"; top chain {top.get('name')} {_b(top.get('tvl_usd'))}" if top else ""
                lines.append(f"  - DeFi TVL {_b(dl['defi_tvl_usd'])}{tail}")
        lines.append("")
    md = ctx.get("macro_desk")
    if md:
        lines.append("MACRO DESK (keyless rates / inflation / calendar):")
        y = md.get("yields")
        if y:
            sp = f"; 2s10s {y['spread_2s10s_bps']}bps" if y.get("spread_2s10s_bps") is not None else ""
            lines.append(f"  - UST par curve ({y.get('date')}): 3M {y.get('y3m')}%, 2Y {y.get('y2y')}%, "
                         f"10Y {y.get('y10y')}%, 30Y {y.get('y30y')}%{sp}")
        c = md.get("cpi")
        if c:
            seg = [f"index {c.get('index')}"]
            if c.get("yoy_pct") is not None:
                seg.append(f"{c['yoy_pct']:+.2f}% YoY")
            if c.get("mom_pct") is not None:
                seg.append(f"{c['mom_pct']:+.2f}% MoM")
            lines.append(f"  - CPI-U ({c.get('month')}): " + ", ".join(seg) + " [BLS CUUR0000SA0]")
        cal = md.get("calendar")
        if cal:
            lines.append("  - Calendar (upcoming Treasury auctions): "
                         + "; ".join(f"{e['date']} {e['event']}" for e in cal))
        lines.append("")
    gd = ctx.get("geo_desk")
    if gd:
        lines.append("GEOPOLITICS DESK (keyless chokepoint traffic + maritime):")
        for port, v in (gd.get("chokepoints") or {}).items():
            warn = f"  ⚠️ {v['stale_warn']}" if v.get("stale_warn") else ""
            lines.append(f"  - {port}: 7d-MA {v.get('ma7_transit_calls')} transit calls "
                         f"(max30d {v.get('max_30d')}; as of {v.get('latest_date')}, "
                         f"lag {v.get('data_lag_days')}d){warn}")
        for h in (gd.get("shipping_news") or []):
            lines.append(f"  - maritime: {h}")
        lines.append("")
    return lines


if __name__ == "__main__":
    for name, fn in (("CRYPTO", crypto_desk), ("MACRO", macro_desk), ("GEO", geo_desk)):
        print(f"\n{'='*60}\n  {name} DESK\n{'='*60}")
        t = time.time()
        d = fn()
        print(f"  ({time.time()-t:.1f}s, ok={d.get('ok')})")
        print(json.dumps({k: v for k, v in d.items() if k != "ok"}, indent=2, default=str))
