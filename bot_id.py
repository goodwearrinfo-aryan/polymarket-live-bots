#!/usr/bin/env python3
"""
bot_id.py — classify every watched wallet as BOT / HUMAN / HYBRID from up to
3 months of trade history. Read-only research (data-api public trades).

Features per wallet (window = last 90 days, up to MAX_TRADES):
  gap_p50 / pct_gaps_lt10s / pct_gaps_lt60s  — inter-trade cadence
  active_hours   — # of UTC hours holding >=1% of trades (bots ~ 20-24)
  hour_entropy   — Shannon entropy of hour-of-day distribution (bots ~ max 4.58)
  dow_entropy    — day-of-week entropy (bots uniform ~ 2.81 max)
  size_quant     — share of trades whose share-size repeats a top-5 fixed size
  days_active    — % of calendar days in span with >=1 trade

Verdict rules (transparent, no ML):
  BOT    — pct_gaps_lt60s>=30% AND active_hours>=18  (machine cadence, no sleep)
  HUMAN  — gap_p50>=300s AND active_hours<=14        (paced, sleeps)
  HYBRID — everything else (scripted bursts + manual, or thin data)

Output: stdout table + bot_id.json
Run: python3 bot_id.py [max_trades_per_wallet=3000]
"""
import json, math, os, sys, time, urllib.request
from collections import Counter

BASE  = os.path.expanduser("~/Documents/polymarket")
OUT_F = os.path.join(BASE, "bot_id.json")
UA    = {"User-Agent": "Mozilla/5.0"}
MAX_TRADES = int(sys.argv[1]) if len(sys.argv) > 1 else 3000
WINDOW_S   = 90 * 86400


def fetch(addr, limit):
    out, offset = [], 0
    cutoff = time.time() - WINDOW_S
    while len(out) < limit:
        take = min(500, limit - len(out))
        url = (f"https://data-api.polymarket.com/trades?user={addr}"
               f"&limit={take}&offset={offset}")
        try:
            req = urllib.request.Request(url, headers=UA)
            page = json.loads(urllib.request.urlopen(req, timeout=15).read())
        except Exception:
            break
        if not page:
            break
        out.extend(page)
        if int(page[-1].get("timestamp", 0)) < cutoff or len(page) < take:
            break
        offset += take
        time.sleep(0.25)
    return [t for t in out if int(t.get("timestamp", 0)) >= cutoff]


def entropy(counter):
    n = sum(counter.values())
    if n == 0:
        return 0.0
    return -sum((c / n) * math.log2(c / n) for c in counter.values() if c)


def quantile(v, q):
    return v[min(len(v) - 1, int(q * len(v)))] if v else None


def profile(addr):
    tr = fetch(addr, MAX_TRADES)
    if len(tr) < 10:
        return {"n": len(tr), "verdict": "THIN", "why": "fewer than 10 trades in 90d"}
    ts = sorted(int(t.get("timestamp", 0)) for t in tr)
    gaps = sorted(b - a for a, b in zip(ts, ts[1:]) if b > a)
    hours = Counter(time.gmtime(x).tm_hour for x in ts)
    dows  = Counter(time.gmtime(x).tm_wday for x in ts)
    sizes = Counter(round(float(t.get("size", 0)), 1) for t in tr)
    top5  = sum(c for _, c in sizes.most_common(5))
    span_days = max(1, (ts[-1] - ts[0]) / 86400)
    days = len({time.gmtime(x).tm_yday for x in ts})

    f = {
        "n": len(tr),
        "span_days": round(span_days, 1),
        "gap_p50_s": quantile(gaps, 0.5),
        "pct_lt10s": round(100 * sum(1 for g in gaps if g < 10) / len(gaps), 1) if gaps else 0,
        "pct_lt60s": round(100 * sum(1 for g in gaps if g < 60) / len(gaps), 1) if gaps else 0,
        "active_hours": sum(1 for c in hours.values() if c >= 0.01 * len(tr)),
        "hour_entropy": round(entropy(hours), 2),
        "dow_entropy": round(entropy(dows), 2),
        "size_quant_pct": round(100 * top5 / len(tr), 1),
        "days_active_pct": round(100 * days / span_days, 0) if span_days >= 1 else 100,
    }
    if f["pct_lt60s"] >= 30 and f["active_hours"] >= 18:
        f["verdict"] = "BOT"
        f["why"] = (f"{f['pct_lt60s']}% gaps<60s, trades {f['active_hours']}/24 UTC hours "
                    f"(no sleep), hour-entropy {f['hour_entropy']}/4.58")
    elif (f["gap_p50_s"] or 0) >= 300 and f["active_hours"] <= 14:
        f["verdict"] = "HUMAN"
        f["why"] = (f"median gap {f['gap_p50_s']}s, only {f['active_hours']} active "
                    f"hours (sleeps), hour-entropy {f['hour_entropy']}")
    else:
        f["verdict"] = "HYBRID"
        f["why"] = (f"mixed: {f['pct_lt60s']}% gaps<60s but {f['active_hours']} "
                    f"active hours — scripted bursts + manual, or thin sample")
    return f


def main():
    wallets = json.load(open(os.path.join(BASE, "insider_wallets.json")))
    report = {}
    print(f"{'label':<28}{'n':>6}{'p50gap':>8}{'<60s%':>7}{'hrs':>5}{'verdict':>9}")
    for addr, label in wallets.items():
        f = profile(addr)
        f["label"] = label
        report[addr] = f
        print(f"{label:<28}{f.get('n',0):>6}{str(f.get('gap_p50_s','-')):>8}"
              f"{str(f.get('pct_lt60s','-')):>7}{str(f.get('active_hours','-')):>5}"
              f"{f['verdict']:>9}")
        time.sleep(0.3)
    counts = Counter(v["verdict"] for v in report.values())
    print(f"\nTotals: {dict(counts)}")
    json.dump({"generated_window_days": 90, "wallets": report}, open(OUT_F, "w"), indent=1)
    print(f"wrote {OUT_F}")


if __name__ == "__main__":
    main()
