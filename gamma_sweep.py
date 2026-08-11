#!/usr/bin/env python3
"""
gamma_sweep.py — full-coverage ACTIVE-market sweep via gamma deep-pagination.

Why gamma (not CLOB): gamma /markets is server-side filtered to active+open
(`closed=false&active=true`) and ordered by volume — exactly the universe we want.
CLOB /markets is keyless+1000/page BUT unfiltered and historical-first, so the open
markets are buried behind ~100k closed ones (a 60k-row scan surfaced only 12 active).
Gamma has 5000+ active markets (offset=5000 still returns rows); edge_common.poly_markets
only paged the top 800. This deep-pages gamma's active feed (100/page) with patient
backoff — a transient 429 retries the SAME offset instead of truncating — then runs the
data-gate over the FULL active set and flags settled-but-mispriced arb in the long tail.

Run:
  python3 gamma_sweep.py                  # full sweep → counts + arb flags + cache
  python3 gamma_sweep.py --max-pages 200  # depth cap (default 150 = up to 15k markets)
Cache: gamma_markets_full.json
"""
import json, os, sys, time, urllib.parse, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import edge_common as ec
import analyst_data_gate as adg

CACHE = os.path.join(HERE, "gamma_markets_full.json")
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}


def _page(offset, per_page=100, page_tries=6):
    """One gamma page; retry the SAME offset on throttle so the sweep never
    truncates on a transient failure. None only if every retry failed."""
    url = ec.GAMMA + "?" + urllib.parse.urlencode({
        "closed": "false", "active": "true",
        "order": "volumeNum", "ascending": "false",
        "limit": per_page, "offset": offset,
    })
    for i in range(page_tries):
        try:
            return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=25))
        except Exception:
            time.sleep(2.0 * (i + 1))
    return None


def fetch_all(max_pages=150, per_page=100):
    out, seen = [], set()
    failed = 0
    for p in range(max_pages):
        raw = _page(p * per_page, per_page)
        if raw is None:
            failed += 1
            if failed >= 3:
                print(f"  [stop] 3 pages failed all retries at offset {p*per_page} "
                      f"— gamma hard-throttling; INCOMPLETE", file=sys.stderr)
                break
            time.sleep(5)
            continue
        failed = 0
        for m in raw:
            cid = str(m.get("conditionId") or m.get("id"))
            if not cid or cid in seen:
                continue
            prices = m.get("outcomePrices")
            if isinstance(prices, str):
                try:
                    prices = json.loads(prices)
                except Exception:
                    prices = None
            if not prices or len(prices) < 2:
                continue
            seen.add(cid)
            out.append({"id": cid, "q": (m.get("question") or "")[:160],
                        "yes": float(prices[0]), "no": float(prices[1]),
                        "vol": float(m.get("volumeNum") or m.get("volume") or 0),
                        "end": m.get("endDate")})
        if len(raw) < per_page:     # genuine end of the active list
            break
        if p % 10 == 9:
            print(f"  …{len(out)} active markets after {p+1} pages")
    return out, p + 1


def main():
    max_pages = 150
    if "--max-pages" in sys.argv:
        max_pages = int(sys.argv[sys.argv.index("--max-pages") + 1])
    print(f"gamma sweep — deep-paginating ACTIVE markets (100/page, max {max_pages} pages)…")
    mkts, pages = fetch_all(max_pages=max_pages)
    with open(CACHE, "w") as f:
        json.dump(mkts, f)
    print(f"\nFULL ACTIVE SET: {len(mkts)} open binary markets across {pages} pages "
          f"(vs poly_markets' top-800). Cached → {os.path.basename(CACHE)}\n")

    matched = live = 0
    arbs, near = [], []
    for m in mkts:
        src, params = adg.classify(m["q"])
        if not src:
            continue
        matched += 1
        try:
            dos = adg.fetch_dossier(src, params, m["id"])
        except Exception:
            dos = None
        if not dos or not dos.get("ok"):
            continue
        live += 1
        yes, trig = m["yes"], dos.get("already_triggered")
        if trig and yes < 0.95:
            arbs.append(("YES-underpriced (data already forces YES)", m, dos))
        elif (trig is False) and yes > 0.97:
            arbs.append(("YES-overpriced (data can't trigger)", m, dos))
        elif trig is False and yes > 0.85:
            near.append((m, dos))
    print(f"DATA-RESOLVABLE in full active set: {matched} matched a source, {live} with LIVE data "
          f"(was 44 on the top-800 sweep).")
    print(f"SETTLED-BUT-MISPRICED ARB candidates: {len(arbs)}\n")
    for kind, m, dos in arbs:
        print(f"  ★ [{kind}] YES={m['yes']} vol=${m['vol']:,.0f} — {m['q'][:72]}")
        print(f"     {json.dumps({k: dos.get(k) for k in ('symbol','current','threshold','direction','already_triggered','distance_pct') if k in dos})}")
    if not arbs:
        print("  (no clean arb — data-resolvable universe efficiently priced)")
    if near:
        print(f"\n  watchlist (untriggered but market YES>0.85, n={len(near)}):")
        for m, dos in near[:12]:
            print(f"    YES={m['yes']} — {m['q'][:66]}")


if __name__ == "__main__":
    main()
