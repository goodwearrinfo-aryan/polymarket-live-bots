#!/usr/bin/env python3
"""
open_trades_audit.py — cross-check every OPEN paper position against live data.
Read-only. Run by the polymarket-6h-routine scheduled task (and manually).

Per open position, fetches the live market from gamma (CLOB fallback for
resolved/vanished markets) and flags:
  STALE_GONE   — market not found on gamma OR clob (cannot price)
  RESOLVED     — market closed/resolved but position still open (exit machinery
                 should have caught it; if many, the exit scan is broken)
  PAST_END     — endDate in the past but market not closed (padded-date lie OK
                 for sports; flag only >2 days past)
  DEEP_RED     — unrealized loss > 50% of cost (stop machinery may be blind)
  AGED         — open > 7 days on a leg with max_hold_hours < 168

Output: stdout table + open_trades_audit.json (consumed by the 6h routine).
Run: python3 open_trades_audit.py
"""
import json, os, time, datetime, urllib.request

BASE  = os.path.expanduser("~/Documents/polymarket")
STATE = os.path.join(BASE, "scalp_lab_state.json")
OUT_F = os.path.join(BASE, "open_trades_audit.json")
GAMMA = "https://gamma-api.polymarket.com/markets"
CLOB  = "https://clob.polymarket.com"
UA    = {"User-Agent": "Mozilla/5.0"}


def get(url):
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=12) as r:
            return json.loads(r.read())
    except Exception:
        return None


def fetch_market(cond_id):
    """gamma first; CLOB fallback for resolved/vanished markets.
    Returns (yes_price, closed, end_iso, source) or None."""
    rows = get(f"{GAMMA}?condition_ids={cond_id}")
    if rows:
        m = rows[0]
        try:
            yes = float(json.loads(m.get("outcomePrices", "[]"))[0])
        except Exception:
            yes = None
        return yes, bool(m.get("closed")), m.get("endDate"), "gamma"
    cm = get(f"{CLOB}/markets/{cond_id}")
    if cm and cm.get("tokens"):
        tok = cm["tokens"][0]
        yes = float(tok.get("price")) if tok.get("price") is not None else None
        if tok.get("winner"):
            yes = 1.0
        return yes, bool(cm.get("closed")), cm.get("end_date_iso"), "clob"
    return None


def hours_since(iso):
    try:
        dt = datetime.datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return (datetime.datetime.now(datetime.timezone.utc) - dt).total_seconds() / 3600
    except Exception:
        return None


def main():
    state = json.load(open(STATE))
    flags, cache = [], {}
    n_open = n_checked = 0
    for leg, book in state.items():
        if not isinstance(book, dict):
            continue
        for p in book.get("open", []):
            n_open += 1
            cid = p.get("id")
            if cid not in cache:
                cache[cid] = fetch_market(cid)
                time.sleep(0.15)
            mk = cache[cid]
            rec = {"leg": leg, "q": (p.get("q") or "")[:55], "id": cid,
                   "side": p.get("side"), "entry": p.get("entry_fill"),
                   "opened_at": p.get("opened_at")}
            if mk is None:
                rec["flag"] = "STALE_GONE"
                flags.append(rec)
                continue
            n_checked += 1
            yes, closed, end_iso, src = mk
            out_px = None
            if yes is not None:
                out_px = yes if p.get("side") in ("YES", "PAIR") else 1 - yes
                rec["now"] = round(out_px, 3)
            if closed:
                rec["flag"] = "RESOLVED"
                flags.append(rec)
                continue
            if end_iso:
                h_past = hours_since(end_iso)
                if h_past is not None and h_past > 48:
                    rec["flag"] = "PAST_END"
                    rec["days_past_end"] = round(h_past / 24, 1)
                    flags.append(rec)
                    continue
            ef = p.get("entry_fill")
            if out_px is not None and ef:
                if (ef - out_px) / ef > 0.5:
                    rec["flag"] = "DEEP_RED"
                    rec["unreal_pct"] = round(100 * (out_px - ef) / ef, 1)
                    flags.append(rec)
                    continue
            age_h = hours_since(p.get("opened_at", ""))
            if age_h is not None and age_h > 168:
                rec["flag"] = "AGED"
                rec["age_days"] = round(age_h / 24, 1)
                flags.append(rec)

    by_flag = {}
    for f in flags:
        by_flag.setdefault(f["flag"], []).append(f)

    print(f"OPEN-TRADES AUDIT — {n_open} open, {n_checked} priced live, "
          f"{len(flags)} flagged\n")
    for flag in ("RESOLVED", "STALE_GONE", "DEEP_RED", "PAST_END", "AGED"):
        rows = by_flag.get(flag, [])
        if not rows:
            continue
        print(f"  [{flag}] x{len(rows)}")
        for r in rows[:15]:
            extra = (f" now={r.get('now')}" if "now" in r else "") + \
                    (f" {r.get('unreal_pct')}%" if "unreal_pct" in r else "") + \
                    (f" {r.get('days_past_end')}d past end" if "days_past_end" in r else "") + \
                    (f" {r.get('age_days')}d old" if "age_days" in r else "")
            print(f"    {r['leg']:<14} {r['side']:<4} @{r['entry']}{extra}  {r['q']}")
        if len(rows) > 15:
            print(f"    ... +{len(rows)-15} more")
        print()

    verdict = "OK" if not by_flag.get("RESOLVED") and not by_flag.get("STALE_GONE") \
        else "ATTENTION"
    if len(by_flag.get("RESOLVED", [])) > 10:
        verdict = "BROKEN — exit scan not closing resolved markets"
    print(f"VERDICT: {verdict}")

    json.dump({"ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
               "n_open": n_open, "n_checked": n_checked,
               "n_flagged": len(flags), "verdict": verdict,
               "counts": {k: len(v) for k, v in by_flag.items()},
               "flags": flags},
              open(OUT_F, "w"), indent=1)
    print(f"wrote {OUT_F}")


if __name__ == "__main__":
    main()
