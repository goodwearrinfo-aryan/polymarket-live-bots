#!/usr/bin/env python3
"""open_interest.py — read-only KEYLESS data logger: cross-venue perp open interest.

OPEN-SOURCE data only (ccxt public endpoints, no keys). For BTC/ETH/SOL across venues, pulls notional
open interest ($) per venue + aggregate. OI buildup = leverage/positioning; a sharp OI drop with a
price move = a liquidation cascade. Companion to funding_landscape. Writes Obsidian + JSONL history.
Read-only research instrumentation — NOT a trading leg. Stdlib + ccxt. Run: open_interest.py
"""
import os, json, datetime as _dt

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "open_interest_log.jsonl")
VAULT = os.path.expanduser("~/Documents/PolymarketVault/Reports/open_interest.md")

ASSETS = ("BTC", "ETH", "SOL")
VENUES = {
    "binance":     ("binance",     "{}/USDT:USDT"),
    "bybit":       ("bybit",       "{}/USDT:USDT"),
    "okx":         ("okx",         "{}/USDT:USDT"),
    "hyperliquid": ("hyperliquid", "{}/USDC:USDC"),
}


def oi_usd(ccxt_id, tpl, asset):
    """Notional OI in USD for one venue/asset (keyless). None on failure."""
    try:
        import ccxt
        ex = getattr(ccxt, ccxt_id)({"enableRateLimit": True})
        sym = tpl.format(asset)
        d = ex.fetch_open_interest(sym)
        val = d.get("openInterestValue")
        if val is None:                       # some venues give only coin amount → × mark price
            amt = d.get("openInterestAmount")
            try:
                px = ex.fetch_ticker(sym).get("last")
                val = float(amt) * float(px) if (amt and px) else None
            except Exception:
                val = None
        return round(float(val), 0) if val else None
    except Exception:
        return None


def collect():
    return {a: {v: oi_usd(cid, tpl, a) for v, (cid, tpl) in VENUES.items()} for a in ASSETS}


def render_md(rows, ts):
    L = ["---", "type: open_interest", f"updated: {ts}", "---", "",
         "# Cross-venue perp open interest (keyless)", "",
         f"_Updated {ts}. Notional OI ($M) by venue/asset. Buildup = leverage; a sharp drop with a "
         f"price move = liquidation cascade. Companion to funding_landscape. Open-source ccxt. Read-only._",
         "", "| asset | " + " | ".join(VENUES) + " | total |",
         "|-------|" + "|".join(["------"] * (len(VENUES) + 1)) + "|"]
    for a in ASSETS:
        cells, tot = [], 0.0
        for v in VENUES:
            x = rows[a].get(v)
            if x:
                cells.append(f"${x/1e6:,.0f}M"); tot += x
            else:
                cells.append("—")
        L.append(f"| {a} | " + " | ".join(cells) + f" | ${tot/1e6:,.0f}M |")
    return "\n".join(L) + "\n"


def main():
    ts = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = collect()
    try:
        open(LOG, "a").write(json.dumps({"ts": ts, "oi_usd": rows}) + "\n")
    except Exception as e:
        print(f"[log FAIL] {e}")
    try:
        os.makedirs(os.path.dirname(VAULT), exist_ok=True)
        open(VAULT, "w").write(render_md(rows, ts))
    except Exception as e:
        print(f"[vault FAIL] {e}")
    ok = sum(1 for a in rows for v in rows[a] if rows[a][v])
    print(f"[{ts}] open_interest: {ok}/{len(ASSETS)*len(VENUES)} venue·asset fetched -> {VAULT}")


if __name__ == "__main__":
    main()
