#!/usr/bin/env python3
"""india_macro.py — read-only KEYLESS data logger: India macro + crypto premium, with HISTORY.

OPEN-SOURCE, keyless:
  • USD/INR FX — frankfurter.app (ECB data): latest + ~90-day historical backfill (trend + vol).
  • India crypto premium — CoinDCX public ticker (INR pairs) vs global (USDT pairs) × USD/INR.
    A persistent BTC/ETH premium-or-discount on Indian venues reflects capital-control friction —
    a real, India-specific structural signal (not a price forecast).
Writes Obsidian + JSONL history every run. Read-only research instrumentation. Stdlib only.
Run: india_macro.py
"""
import os, json, urllib.request, datetime as _dt

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "india_macro_log.jsonl")
VAULT = os.path.expanduser("~/Documents/PolymarketVault/Reports/india_macro.md")


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    return json.loads(urllib.request.urlopen(req, timeout=30).read())


def usd_inr():
    """Latest USD/INR + ~90d history from frankfurter (keyless, ECB)."""
    out = {}
    try:
        out["latest"] = float(_get("https://api.frankfurter.app/latest?from=USD&to=INR")["rates"]["INR"])
    except Exception as e:
        out["err"] = str(e); return out
    try:
        today = _dt.date.today()
        start = (today - _dt.timedelta(days=90)).isoformat()
        h = _get(f"https://api.frankfurter.app/{start}..{today.isoformat()}?from=USD&to=INR")
        series = sorted((d, float(r["INR"])) for d, r in h.get("rates", {}).items())
        out["hist"] = series
        if series:
            vals = [v for _, v in series]
            out["d90_first"], out["d90_last"] = vals[0], vals[-1]
            out["d90_min"], out["d90_max"] = min(vals), max(vals)
    except Exception as e:
        out["hist_err"] = str(e)
    return out


def india_crypto_premium(usdinr):
    """BTC/ETH premium on CoinDCX (INR) vs global USDT price × USD/INR. Keyless."""
    out = {}
    if not usdinr:
        return out
    try:
        tk = _get("https://api.coindcx.com/exchange/ticker")
        by = {t.get("market"): t for t in tk if isinstance(t, dict)}
        for asset in ("BTC", "ETH"):
            inr = by.get(f"{asset}INR"); usdt = by.get(f"{asset}USDT")
            try:
                p_inr = float(inr["last_price"]); p_usdt = float(usdt["last_price"])
                fair_inr = p_usdt * usdinr
                prem = (p_inr / fair_inr - 1) * 100
                out[asset] = {"inr": round(p_inr, 0), "usdt": round(p_usdt, 1),
                              "fair_inr": round(fair_inr, 0), "premium_pct": round(prem, 2)}
            except Exception:
                pass
    except Exception as e:
        out["err"] = str(e)
    return out


def render_md(fx, prem, ts):
    L = ["---", "type: india_macro", f"updated: {ts}", "---", "",
         "# India macro + crypto premium (keyless, with history)", "",
         f"_Updated {ts}. USD/INR (frankfurter/ECB) + India crypto premium (CoinDCX vs global). "
         f"A persistent premium/discount = capital-control friction, an India-specific signal. Read-only._", ""]
    if fx.get("latest"):
        L.append(f"- **USD/INR: {fx['latest']:.3f}**")
        if "d90_first" in fx:
            chg = (fx["d90_last"] - fx["d90_first"]) / fx["d90_first"] * 100
            L.append(f"- 90d: {fx['d90_first']:.2f} → {fx['d90_last']:.2f} ({chg:+.2f}%), "
                     f"range [{fx['d90_min']:.2f}, {fx['d90_max']:.2f}] ({len(fx.get('hist',[]))} days)")
    else:
        L.append(f"- USD/INR fetch failed ({fx.get('err','?')})")
    if prem and any(k in prem for k in ("BTC", "ETH")):
        L += ["", "## India crypto premium (CoinDCX INR vs global)", "",
              "| asset | INR price | fair (global×FX) | premium |", "|------|------|------|------|"]
        for a in ("BTC", "ETH"):
            if a in prem:
                p = prem[a]
                L.append(f"| {a} | ₹{p['inr']:,.0f} | ₹{p['fair_inr']:,.0f} | **{p['premium_pct']:+.2f}%** |")
    else:
        L += ["", f"- India crypto premium unavailable ({prem.get('err','no INR pairs')})"]
    return "\n".join(L) + "\n"


def main():
    ts = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    fx = usd_inr()
    prem = india_crypto_premium(fx.get("latest"))
    rec = {"ts": ts, "usd_inr": fx.get("latest"),
           "usd_inr_90d": {"first": fx.get("d90_first"), "last": fx.get("d90_last"),
                           "min": fx.get("d90_min"), "max": fx.get("d90_max")},
           "crypto_premium": {a: prem[a]["premium_pct"] for a in ("BTC", "ETH") if a in prem}}
    try:
        open(LOG, "a").write(json.dumps(rec) + "\n")
    except Exception as e:
        print(f"[log FAIL] {e}")
    try:
        os.makedirs(os.path.dirname(VAULT), exist_ok=True)
        open(VAULT, "w").write(render_md(fx, prem, ts))
    except Exception as e:
        print(f"[vault FAIL] {e}")
    print(f"[{ts}] india_macro: USD/INR={fx.get('latest')} premium={rec['crypto_premium']} -> {VAULT}")


if __name__ == "__main__":
    main()
