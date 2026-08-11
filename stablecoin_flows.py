#!/usr/bin/env python3
"""stablecoin_flows.py — read-only KEYLESS data logger: stablecoin supply = crypto liquidity regime.

OPEN-SOURCE DeFiLlama data (no API key). Total stablecoin market cap + 1d/7d/30d change is the
cleanest proxy for liquidity entering/leaving crypto (rising supply = risk-on fuel; contracting =
deleveraging). Includes a 30-point historical series. Writes Obsidian + JSONL history every run.
Read-only research instrumentation — NOT a trading leg. Stdlib only. Run: stablecoin_flows.py
"""
import os, json, urllib.request, datetime as _dt

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "stablecoin_flows_log.jsonl")
VAULT = os.path.expanduser("~/Documents/PolymarketVault/Reports/stablecoin_flows.md")


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    return json.loads(urllib.request.urlopen(req, timeout=30).read())


def _peg(a, k):
    try:
        return float((a.get(k) or {}).get("peggedUSD") or 0)
    except Exception:
        return 0.0


def collect():
    out = {}
    try:
        d = _get("https://stablecoins.llama.fi/stablecoins?includePrices=true")
        assets = d.get("peggedAssets", [])
        out["total"] = sum(_peg(a, "circulating") for a in assets)
        out["prev_day"] = sum(_peg(a, "circulatingPrevDay") for a in assets)
        out["prev_week"] = sum(_peg(a, "circulatingPrevWeek") for a in assets)
        out["prev_month"] = sum(_peg(a, "circulatingPrevMonth") for a in assets)
        top = sorted(assets, key=lambda a: _peg(a, "circulating"), reverse=True)[:6]
        out["top"] = [{"symbol": a.get("symbol"), "mcap": _peg(a, "circulating")} for a in top]
    except Exception as e:
        out["err_now"] = str(e)
    try:
        h = _get("https://stablecoins.llama.fi/stablecoincharts/all")
        pts = []
        for r in h[-30:]:
            v = r.get("totalCirculatingUSD") or r.get("totalCirculating") or {}
            val = v.get("peggedUSD") if isinstance(v, dict) else v
            if val:
                d = r.get("date")
                try:                                  # DeFiLlama dates are unix-epoch strings
                    d = _dt.datetime.fromtimestamp(int(d), _dt.timezone.utc).date().isoformat()
                except Exception:
                    pass
                pts.append([d, round(float(val) / 1e9, 2)])  # $B
        out["hist_b"] = pts
    except Exception as e:
        out["err_hist"] = str(e)
    return out


def _pct(now, prev):
    if not now or not prev:
        return None
    return round((now - prev) / prev * 100, 2)


def render_md(o, ts):
    tot = o.get("total")
    L = ["---", "type: stablecoin_flows", f"updated: {ts}", "---", "",
         "# Stablecoin supply — crypto liquidity regime (keyless)", "",
         f"_Updated {ts}. Total stablecoin market cap = liquidity fuel for crypto/risk markets. "
         f"Rising = risk-on inflow, contracting = deleveraging. Open-source DeFiLlama. Read-only._", ""]
    if tot:
        d1, d7, d30 = _pct(tot, o.get("prev_day")), _pct(tot, o.get("prev_week")), _pct(tot, o.get("prev_month"))
        regime = ("EXPANDING (risk-on fuel)" if (d7 or 0) > 0.3 else
                  "CONTRACTING (deleveraging)" if (d7 or 0) < -0.3 else "FLAT")
        L += [f"- **Total: ${tot/1e9:,.1f}B**  |  1d {d1:+}%  ·  7d {d7:+}%  ·  30d {d30:+}%",
              f"- **Regime: {regime}**", "",
              "| stablecoin | mcap ($B) |", "|------|------|"]
        for s in o.get("top", []):
            L.append(f"| {s['symbol']} | {s['mcap']/1e9:,.1f} |")
    else:
        L.append(f"- (current snapshot fetch failed: {o.get('err_now','?')})")
    hist = o.get("hist_b", [])
    if hist:
        first, last = hist[0][1], hist[-1][1]
        L += ["", "## 30-point history (total $B)",
              f"- {hist[0][0]} → {hist[-1][0]}: ${first:,.0f}B → ${last:,.0f}B "
              f"({(last-first)/first*100:+.1f}%)"]
    return "\n".join(L) + "\n"


def main():
    ts = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    o = collect()
    try:
        with open(LOG, "a") as f:
            f.write(json.dumps({"ts": ts, **{k: v for k, v in o.items() if k != "hist_b"}}) + "\n")
    except Exception as e:
        print(f"[log FAIL] {e}")
    try:
        os.makedirs(os.path.dirname(VAULT), exist_ok=True)
        open(VAULT, "w").write(render_md(o, ts))
    except Exception as e:
        print(f"[vault FAIL] {e}")
    t = o.get("total")
    print(f"[{ts}] stablecoin_flows: total=${t/1e9:.1f}B " if t else f"[{ts}] stablecoin_flows: fetch failed ",
          f"-> {VAULT}")


if __name__ == "__main__":
    main()
