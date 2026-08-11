#!/usr/bin/env python3
"""
measure_reprice_by_segment.py — WHERE is the slow-reprice tail?

measure_reprice.py showed the book is bimodal: ~67% of markets snap to a
decided price in <=2 min, but ~33% take ~91 min. A latency edge can only
exist in that slow tail. This stratifies reprice duration by category and
volume bucket so a watcher can be pointed at the segments that are actually
slow (and liquid enough to trade).

Read-only. Stdlib only. Usage:
  python3 ml/measure_reprice_by_segment.py [--max-markets 200]
"""
import sys, json, time, urllib.request, urllib.parse, argparse, statistics as st
from collections import defaultdict

GAMMA = "https://gamma-api.polymarket.com"
CLOB  = "https://clob.polymarket.com"
UNCERTAIN = (0.35, 0.65)
DECIDED_HI, DECIDED_LO = 0.92, 0.08

def _get(url, params=None, tries=5):
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    for a in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "reprice-by-seg/1.0"})
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except Exception as e:
            if a == tries - 1:
                sys.stderr.write(f"[http] giving up: {url[:90]} {e}\n")
            time.sleep(2.0 * (a + 1))
    return None

def find_transition(series):
    last_uncertain = None
    for i, (ts, p) in enumerate(series):
        if UNCERTAIN[0] <= p <= UNCERTAIN[1]:
            last_uncertain = i
    if last_uncertain is None:
        return None
    t0 = series[last_uncertain][0]
    for ts, p in series[last_uncertain + 1:]:
        if p >= DECIDED_HI or p <= DECIDED_LO:
            return (ts - t0) / 60.0
    return None

def median_bar_spacing_min(series):
    if len(series) < 2:
        return None
    gaps = [(series[i][0] - series[i-1][0]) / 60.0 for i in range(1, len(series))]
    gaps = [g for g in gaps if g > 0]
    return st.median(gaps) if gaps else None

def fetch_history(token, start=None, end=None, fidelity=1):
    params = {"market": token, "fidelity": fidelity}
    if start is not None and end is not None:
        params["startTs"] = int(start)
        params["endTs"] = int(end)
    else:
        params["interval"] = "max"
    d = _get(f"{CLOB}/prices-history", params)
    hist = (d or {}).get("history", []) if isinstance(d, dict) else []
    return sorted((int(h["t"]), float(h["p"])) for h in hist if "t" in h and "p" in h)

def volume_bucket(vol):
    if vol >= 1_000_000: return ">=1M"
    if vol >= 100_000: return "100K-1M"
    if vol >= 10_000: return "10K-100K"
    return "<10K"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-markets", type=int, default=200)
    a = ap.parse_args()

    by_cat = defaultdict(list)
    by_vol = defaultdict(list)
    all_slow = []
    examined = offset = 0
    empty_pages = 0
    print(f"stratifying reprice duration on up to {a.max_markets} resolved markets…", flush=True)
    while examined < a.max_markets:
        mkts = _get(f"{GAMMA}/markets", {"closed": "true", "limit": 100,
                    "offset": offset, "order": "volume", "ascending": "false"})
        offset += 100
        if not mkts:
            empty_pages += 1
            if empty_pages >= 3:
                print("  3 empty pages — network down? stopping.", flush=True)
                break
            continue
        empty_pages = 0
        for m in mkts:
            if examined >= a.max_markets:
                break
            try:
                toks = m.get("clobTokenIds")
                toks = json.loads(toks) if isinstance(toks, str) else toks
                token = str(toks[0])
                vol = float(m.get("volumeNum") or m.get("volume") or 0)
            except Exception:
                continue
            series = fetch_history(token, fidelity=1)
            examined += 1
            if len(series) < 10:
                continue
            r = find_transition(series)
            if r is None:
                continue
            # refine with a bounded window
            # (coarse locate again inside the window is expensive; use direct)
            cat = (m.get("category") or "other").lower()
            by_cat[cat].append(r)
            by_vol[volume_bucket(vol)].append(r)
            if r > 5:
                all_slow.append((r, vol, cat, (m.get("question") or "")[:60]))
            time.sleep(0.2)

    def pct(xs, q):
        xs = sorted(xs)
        return xs[min(len(xs) - 1, int(len(xs) * q))]

    def row(label, xs):
        if not xs:
            print(f"  {label:<12} n=0")
            return
        med = st.median(xs)
        p75 = pct(xs, 0.75)
        slow = sum(1 for x in xs if x > 5) / len(xs)
        print(f"  {label:<12} n={len(xs):<4} med={med:>6.1f}m  p75={p75:>6.1f}m  slow(>5m)={slow:>4.0%}")

    print(f"\n=== REPRICE DURATION BY SEGMENT (usable {sum(len(v) for v in by_cat.values())}) ===")
    print("\nby category:")
    for cat in sorted(by_cat, key=lambda c: -len(by_cat[c])):
        row(cat, by_cat[cat])
    print("\nby volume:")
    for vb in sorted(by_vol, key=lambda v: -len(by_vol[v])):
        row(vb, by_vol[vb])

    allvals = [x for v in by_cat.values() for x in v]
    if allvals:
        print(f"\noverall: n={len(allvals)}  med={st.median(allvals):.1f}m  "
              f"p75={pct(allvals, 0.75):.1f}m  p90={pct(allvals, 0.90):.1f}m")

    print("\nslowest markets (reprice >5 min, useful to eyeball which categories are stale):")
    for r, vol, cat, q in sorted(all_slow, reverse=True)[:15]:
        print(f"  {r:>6.1f}m  vol=${vol:>10.0f}  {cat:<12} {q}")

    print("\nREAD: a latency edge only exists in rows with high 'slow(>5m)' AND")
    print("enough volume to trade. If slow rows are all <10K volume, the edge")
    print("is untradeable (spread/fees eat it). If slow rows have 100K+ volume,")
    print("point the watcher at that category.")

if __name__ == "__main__":
    main()
