#!/usr/bin/env python3
"""funding_logger.py — READ-ONLY logger (NOT a trading leg, places NO trades).

Records the perp FUNDING-RATE tape for the majors via ccxt (keyless). Funding is what perp
longs pay shorts each interval: extreme POSITIVE funding = crowded longs (reversion-bearish
hypothesis), extreme NEGATIVE = crowded shorts (bullish). This logs the cross-sectional
funding snapshot each run into an append-only tape; the forward "does funding predict the
next move" test runs once the tape fills (same posture as deribit_vol — measurement first).

Read-only, stdlib + ccxt. Graduation gate (per hard rules): funding only becomes a signal
(leg) if, on the accumulated tape, a forward return CI clears 0 AFTER cost AND holds OOS.
Usage: python3 funding_logger.py once | report"""
import os, sys, json, time

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "funding_log.jsonl")
VAULT = os.path.expanduser("~/Documents/PolymarketVault/Reports/funding.md")

COINS = ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "AVAX"]
# perp venues to SWEEP every run (funding differs per venue -> cross-venue spread is the datum)
EXCHANGES = ["bybit", "okx", "binanceusdm", "gate", "bitget", "kucoinfutures", "hyperliquid"]


def _interval_hours(fr):
    """Parse the venue's funding interval ('8h','1h','4h') from the ccxt funding-rate dict.
    Honest annualisation depends on this — venues fund at different cadences (most 8h,
    hyperliquid 1h). Defaults to 8h when absent."""
    iv = fr.get("interval") if isinstance(fr, dict) else None
    if isinstance(iv, str) and iv.endswith("h"):
        try:
            return float(iv[:-1]) or 8.0
        except Exception:
            return 8.0
    return 8.0


def _annual_pct(rate, hours):
    """Annualise a per-interval funding rate honestly using the venue's own cadence."""
    return round(float(rate) * (24.0 / hours) * 365.0 * 100.0, 2)


def fetch_funding():
    """Sweep ALL venues. Return {coin: {venue: {funding, annual_pct, interval_h, sym}}}.
    Per-coin multi-venue so the cross-venue funding spread (where carry is richest) is visible."""
    try:
        import ccxt
    except Exception as e:
        sys.stderr.write(f"[funding] ccxt import failed: {e}\n")
        return {}
    by_coin = {c: {} for c in COINS}
    for exname in EXCHANGES:
        try:
            ex = getattr(ccxt, exname)({"enableRateLimit": True, "timeout": 15000})
        except Exception:
            continue
        for c in COINS:
            for sym in (f"{c}/USDT:USDT", f"{c}/USDC:USDC", f"{c}/USD:USD"):
                try:
                    fr = ex.fetch_funding_rate(sym)
                except Exception:
                    continue
                rate = fr.get("fundingRate") if isinstance(fr, dict) else None
                if rate is not None:
                    h = _interval_hours(fr)
                    by_coin[c][exname] = {"funding": round(float(rate), 8),
                                          "annual_pct": _annual_pct(rate, h),
                                          "interval_h": h, "sym": sym}
                    break
    return {c: v for c, v in by_coin.items() if v}   # drop coins no venue priced


def _carry(by_coin):
    """Per-coin cross-venue summary: richest positive-funding venue (best short-perp carry),
    most-negative venue, and the annualised spread between them."""
    out = {}
    for c, venues in by_coin.items():
        items = [(vn, d["annual_pct"]) for vn, d in venues.items()]
        hi = max(items, key=lambda kv: kv[1])   # most you'd collect shorting the perp here
        lo = min(items, key=lambda kv: kv[1])
        out[c] = {"best_venue": hi[0], "best_annual": hi[1],
                  "worst_venue": lo[0], "worst_annual": lo[1],
                  "spread_annual": round(hi[1] - lo[1], 2), "n_venues": len(venues)}
    return out


def once():
    by_coin = fetch_funding()
    if not by_coin:
        print("funding: no perp funding source reachable (venues geo-blocked?) — skipped")
        report()
        return
    carry = _carry(by_coin)
    # backward-compat top-level `funding`/`annual_pct` = the BEST-carry venue per coin
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "exchanges": sorted({vn for v in by_coin.values() for vn in v}),
           "funding": {c: by_coin[c][carry[c]["best_venue"]]["funding"] for c in by_coin},
           "annual_pct": {c: carry[c]["best_annual"] for c in by_coin},
           "by_venue": by_coin, "carry": carry}
    _append_log(rec)
    report()
    # richest harvestable carry across the whole sweep
    top = max(carry.items(), key=lambda kv: kv[1]["best_annual"])
    wide = max(carry.items(), key=lambda kv: kv[1]["spread_annual"])
    nv = len(rec["exchanges"])
    print(f"funding: {len(by_coin)} coins x {nv} venues · richest carry {top[0]} "
          f"{top[1]['best_annual']}%/yr (short on {top[1]['best_venue']}) · "
          f"widest x-venue spread {wide[0]} {wide[1]['spread_annual']}%/yr")


def _append_log(rec, retries=5):
    """Append one snapshot, tolerant of a transient concurrent-writer lock.
    funding_log.jsonl lives under ~/Documents (iCloud machinery), so a manual run
    overlapping the scheduled run can hit OSError EDEADLK/EAGAIN on open-for-append.
    Retry briefly rather than crash the whole run and lose the snapshot."""
    line = json.dumps(rec) + "\n"
    for i in range(retries):
        try:
            with open(LOG, "a") as f:
                f.write(line)
            return True
        except OSError as e:
            if i == retries - 1:
                sys.stderr.write(f"[funding] log append failed after {retries} tries: {e}\n")
                return False
            time.sleep(0.4 * (i + 1))   # back off; let the other writer finish
    return False


def _read_log():
    if not os.path.exists(LOG):
        return []
    rows = []
    for line in open(LOG):
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except Exception:
                pass
    return rows


def report():
    rows = _read_log()
    L = ["# Funding-rate tape — perp crowding (read-only)",
         "",
         "> READ-ONLY logger (no trades). Perp funding = longs pay shorts each interval. "
         "Extreme +ve = crowded longs (reversion-bearish hypothesis); extreme −ve = crowded shorts. "
         "Forward 'funding predicts the move' test runs once the tape fills.",
         f"> Updated {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())} · {len(rows)} snapshots", ""]
    if not rows:
        L += ["_No snapshots yet._"]
        os.makedirs(os.path.dirname(VAULT), exist_ok=True)
        open(VAULT, "w").write("\n".join(L) + "\n")
        return
    last = rows[-1]
    carry = last.get("carry") or {}
    by_venue = last.get("by_venue") or {}
    venues = last.get("exchanges") or ([last["exchange"]] if last.get("exchange") else [])
    L += ["## Latest snapshot · " + str(last.get("ts")) + " · venues: " + ", ".join(venues), ""]
    if carry:
        # cross-venue carry view: where is the richest short-perp carry, and how dispersed
        L += ["### Cross-venue carry (richest short-perp funding per coin)", "",
              "| Coin | Best carry | on venue | Most −ve | on venue | x-venue spread |",
              "|------|-----------:|----------|---------:|----------|---------------:|"]
        for c, k in sorted(carry.items(), key=lambda kv: -kv[1]["best_annual"]):
            L.append(f"| {c} | {k['best_annual']}%/yr | {k['best_venue']} | "
                     f"{k['worst_annual']}%/yr | {k['worst_venue']} | {k['spread_annual']}%/yr |")
        L += ["", "> _Best carry_ = annualised funding you'd COLLECT shorting that perp (vs long spot). "
              "Gross of tx/borrow cost and assumes funding persists — a snapshot, not a locked yield. "
              "Funding flips negative in a bear regime.", ""]
        # full per-venue grid for the richest coin
        top = max(carry.items(), key=lambda kv: kv[1]["best_annual"])[0]
        tv = by_venue.get(top, {})
        if tv:
            L += [f"### {top} funding by venue", "",
                  "| Venue | Funding/interval | Interval | Annualised |",
                  "|-------|-----------------:|:--------:|-----------:|"]
            for vn, d in sorted(tv.items(), key=lambda kv: -kv[1]["annual_pct"]):
                L.append(f"| {vn} | {round(d['funding']*100,4)}% | {d.get('interval_h',8)}h | {d['annual_pct']}%/yr |")
            L.append("")
    else:
        # legacy single-venue rows (pre-multi-venue logs)
        fund = last.get("funding") or {}
        ann = last.get("annual_pct") or {}
        L += ["| Coin | Funding/interval | Annualised | Crowding |",
              "|------|-----------------:|-----------:|----------|"]
        for c, r in sorted(fund.items(), key=lambda kv: -kv[1]):
            a = ann.get(c, 0.0)
            crowd = "⬆ longs" if r > 0.0001 else ("⬇ shorts" if r < -0.0001 else "neutral")
            L.append(f"| {c} | {round(r*100,4)}% | {a}%/yr | {crowd} |")
    L += ["", "_Funding only becomes a tradeable signal at bootstrap CI>0 after cost + OOS "
          "(leg_health.py). This is the tape, not an edge._"]
    os.makedirs(os.path.dirname(VAULT), exist_ok=True)
    open(VAULT, "w").write("\n".join(L) + "\n")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "once"
    if cmd == "once":
        once()
    elif cmd == "report":
        report()
    else:
        print("usage: funding_logger.py once|report")


if __name__ == "__main__":
    main()
