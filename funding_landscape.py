#!/usr/bin/env python3
"""funding_landscape.py — read-only KEYLESS data logger: the cross-venue perp funding landscape.

OPEN-SOURCE data only (ccxt public endpoints, no API keys). For BTC/ETH/SOL across multiple perp
venues, pulls the current funding rate, annualizes it (accounting for each venue's funding
interval — Hyperliquid is hourly, most others 8h), and flags which (venue, asset) pairs clear the
funding_basis FEE HURDLE — i.e. WHERE a delta-neutral carry would actually be +EV. This is the
live map that makes the funding_basis probe + the venue-edge question actionable.

Writes EVERY run (so it keeps accumulating data in Obsidian):
  • ~/Documents/PolymarketVault/Reports/funding_landscape.md   (live table, overwritten)
  • funding_landscape_log.jsonl                                 (append-only history)

Read-only research instrumentation — NOT a trading leg, no orders, no keys, no funds. Stdlib+ccxt.
Run: funding_landscape.py
"""
import os, json, time, datetime as _dt

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "funding_landscape_log.jsonl")
VAULT = os.path.expanduser("~/Documents/PolymarketVault/Reports/funding_landscape.md")

ASSETS = ("BTC", "ETH", "SOL")
# venue -> (ccxt id, symbol template, default funding interval hours)
VENUES = {
    "binance":     ("binance",     "{}/USDT:USDT", 8),
    "bybit":       ("bybit",       "{}/USDT:USDT", 8),
    "okx":         ("okx",         "{}/USDT:USDT", 8),
    "hyperliquid": ("hyperliquid", "{}/USDC:USDC", 1),
}
# funding_basis hurdle (annualized) a carry must beat — derived from its fee model:
#   8bps round-trip / 3-day hold x1.5 margin  ~= 14.6%/yr ;  3bps maker ~= 5.5%/yr
HURDLE_TAKER_YR = 14.6
HURDLE_MAKER_YR = 5.5


def _interval_hours(fr, default):
    iv = fr.get("interval") if isinstance(fr, dict) else None
    if isinstance(iv, str) and iv.endswith("h"):
        try:
            return float(iv[:-1])
        except ValueError:
            pass
    return default


def venue_asset(ccxt_id, sym_tpl, default_iv, asset):
    """Annualized funding % for one venue/asset (keyless). None on failure."""
    try:
        import ccxt
        ex = getattr(ccxt, ccxt_id)({"enableRateLimit": True})
        sym = sym_tpl.format(asset)
        fr = ex.fetch_funding_rate(sym)
        rate = fr.get("fundingRate")
        if rate is None:
            return None
        iv = _interval_hours(fr, default_iv)
        ann = float(rate) * (24.0 / iv) * 365.0 * 100.0
        oi_usd = None
        try:
            oi = ex.fetch_open_interest(sym)
            oi_usd = oi.get("openInterestValue") or None
        except Exception:
            pass
        return {"rate8h_pct": round(float(rate) * 100, 5), "interval_h": iv,
                "ann_pct": round(ann, 2), "oi_usd": oi_usd}
    except Exception:
        return None


def collect():
    rows = {}
    for vlabel, (cid, tpl, iv) in VENUES.items():
        rows[vlabel] = {a: venue_asset(cid, tpl, iv, a) for a in ASSETS}
    return rows


def _flag(ann):
    if ann is None:
        return ""
    a = abs(ann)
    if a >= HURDLE_TAKER_YR:
        return " ✓✓"          # clears even taker hurdle — a carry is +EV here now
    if a >= HURDLE_MAKER_YR:
        return " ✓"           # clears the maker hurdle (low-fee venue)
    return ""


def render_md(rows, ts):
    lines = ["---", "type: funding_landscape", f"updated: {ts}", "---", "",
             "# Cross-venue funding landscape (keyless)", "",
             f"_Updated {ts}. Annualized perp funding by venue/asset. "
             f"✓ clears the maker carry hurdle (~{HURDLE_MAKER_YR}%/yr), "
             f"✓✓ clears the taker hurdle (~{HURDLE_TAKER_YR}%/yr) → a delta-neutral "
             f"`funding_basis` carry is +EV there NOW. Read-only; open-source ccxt data._", "",
             "| asset | " + " | ".join(VENUES) + " |",
             "|-------|" + "|".join(["------"] * len(VENUES)) + "|"]
    best = None
    for a in ASSETS:
        cells = []
        for v in VENUES:
            d = rows[v].get(a)
            if not d:
                cells.append("—")
                continue
            ann = d["ann_pct"]
            cells.append(f"{ann:+.1f}%{_flag(ann)}")
            if best is None or abs(ann) > abs(best[2]):
                best = (v, a, ann, d["interval_h"])
        lines.append(f"| {a} | " + " | ".join(cells) + " |")
    lines += ["", "## Best carry right now"]
    if best and abs(best[2]) >= HURDLE_MAKER_YR:
        v, a, ann, iv = best
        which = "taker+maker" if abs(ann) >= HURDLE_TAKER_YR else "maker-only"
        side = "short perp / long spot" if ann > 0 else "long perp / short spot"
        lines.append(f"- **{a} on {v}: {ann:+.1f}%/yr** ({iv}h funding) → harvest = {side}. "
                     f"Clears {which} hurdle. funding_basis would fire here.")
    else:
        lines.append(f"- None clear even the maker hurdle (~{HURDLE_MAKER_YR}%/yr) — calm regime, "
                     f"carry is sub-fee everywhere. funding_basis correctly idle.")
    return "\n".join(lines) + "\n"


def main():
    ts = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = collect()
    # append-only history (keeps accumulating data)
    try:
        with open(LOG, "a") as f:
            f.write(json.dumps({"ts": ts, "rows": rows}) + "\n")
    except Exception as e:
        print(f"[log FAIL] {e}")
    # live Obsidian snapshot
    try:
        os.makedirs(os.path.dirname(VAULT), exist_ok=True)
        with open(VAULT, "w") as f:
            f.write(render_md(rows, ts))
    except Exception as e:
        print(f"[vault FAIL] {e}")
    ok = sum(1 for v in rows for a in rows[v] if rows[v][a])
    clears = sum(1 for v in rows for a in rows[v]
                 if rows[v][a] and abs(rows[v][a]["ann_pct"]) >= HURDLE_MAKER_YR)
    print(f"[{ts}] funding_landscape: {ok}/{len(VENUES)*len(ASSETS)} venue·asset fetched, "
          f"{clears} clear the maker hurdle -> {VAULT}")


if __name__ == "__main__":
    main()
