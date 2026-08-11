#!/usr/bin/env python3
"""
measure_reprice.py — Does a resolution-latency edge even exist? (read-only)

THE QUESTION: an edge from "watch the real-world fact, act before the book
reprices" only exists if the book reprices SLOWLY. This measures the book's
own reprice DURATION on already-resolved markets — the upper bound on any
latency edge. No external truth feed needed; we measure the book against itself.

METHOD: for each resolved market, pull 1-minute YES price history and find the
decisive move: the LAST tick still in the "uncertain" band [0.35, 0.65], then
the FIRST later tick that is "decided" (>=0.92 or <=0.08). The minutes between
is how long the book took to converge. Aggregate the distribution.

READING IT:
  median reprice >> 10 min  -> SLOW book. A faster real-world signal could
                               plausibly front-run it. Latency edge worth building.
  median reprice ~ 1-2 min  -> book reprices almost instantly. Latency edge is
                               dead; don't build it.

Stdlib only. Runs on the Mac (needs Polymarket network). Usage:
  python3 measure_reprice.py [--max-markets 150] [--fidelity 1]
"""
import sys, json, time, urllib.request, urllib.parse, argparse, statistics as st

GAMMA = "https://gamma-api.polymarket.com"
CLOB  = "https://clob.polymarket.com"
UNCERTAIN = (0.35, 0.65)
DECIDED_HI, DECIDED_LO = 0.92, 0.08

def _get(url, params=None, tries=6):
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    for a in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "reprice/1.0"})
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except Exception as e:
            if a == tries - 1:
                sys.stderr.write(f"[http] giving up: {url[:90]} {e}\n")
            time.sleep(2.0 * (a + 1))
    return None

def find_transition(series):
    """series = [(ts_sec, prob)] oldest first. Returns (t_uncertain, t_decided,
    minutes) for the last 'uncertain' tick -> first subsequent 'decided' tick,
    or None. Returning the timestamps (not just minutes) lets us re-pull a
    fine-grained window around the move."""
    last_uncertain = None
    for i, (ts, p) in enumerate(series):
        if UNCERTAIN[0] <= p <= UNCERTAIN[1]:
            last_uncertain = i
    if last_uncertain is None:
        return None  # never traded through the middle (already lopsided)
    t0 = series[last_uncertain][0]
    for ts, p in series[last_uncertain + 1:]:
        if p >= DECIDED_HI or p <= DECIDED_LO:
            return (t0, ts, (ts - t0) / 60.0)
    return None  # never reached a decided state in the data

def reprice_minutes(series):
    r = find_transition(series)
    return None if r is None else r[2]

def median_bar_spacing_min(series):
    """Median gap (minutes) between consecutive bars — exposes the TRUE
    sampling resolution so a '10 min' floor can't masquerade as a real move."""
    if len(series) < 2:
        return None
    gaps = [(series[i][0] - series[i-1][0]) / 60.0 for i in range(1, len(series))]
    gaps = [g for g in gaps if g > 0]
    return st.median(gaps) if gaps else None

def fetch_history(token, interval=None, start=None, end=None, fidelity=1):
    params = {"market": token, "fidelity": fidelity}
    if start is not None and end is not None:
        params["startTs"] = int(start)
        params["endTs"] = int(end)
    else:
        params["interval"] = interval or "max"
    d = _get(f"{CLOB}/prices-history", params)
    hist = (d or {}).get("history", []) if isinstance(d, dict) else []
    return sorted((int(h["t"]), float(h["p"])) for h in hist if "t" in h and "p" in h)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-markets", type=int, default=150)
    ap.add_argument("--fidelity", type=int, default=1)  # minutes per bar (fine)
    ap.add_argument("--no-refine", action="store_true",
                    help="skip the fine-grained second pass (debug only)")
    a = ap.parse_args()

    durations = []          # refined (or coarse-fallback) reprice minutes
    coarse_durations = []   # what the OLD single-pass method would have reported
    fine_spacings = []      # actual bar spacing achieved in the refine window
    refined_n = 0
    examined = offset = 0
    PAGE = 100
    print(f"measuring book reprice duration on up to {a.max_markets} resolved markets…", flush=True)
    empty_pages = 0
    while examined < a.max_markets:
        mkts = _get(f"{GAMMA}/markets", {"closed": "true", "limit": PAGE,
                    "offset": offset, "order": "volume", "ascending": "false"})
        offset += PAGE
        if not mkts:
            empty_pages += 1
            if empty_pages >= 3:
                print("  3 page fetches failed/empty in a row — network likely down; stopping.", flush=True)
                break
            print(f"  page fetch failed (offset {offset-PAGE}); skipping ahead and retrying…", flush=True)
            continue
        empty_pages = 0
        for m in mkts:
            if examined >= a.max_markets:
                break
            try:
                toks = m.get("clobTokenIds")
                toks = json.loads(toks) if isinstance(toks, str) else toks
                token = str(toks[0])
            except Exception:
                continue
            series = fetch_history(token, interval="max", fidelity=a.fidelity)
            examined += 1
            if len(series) < 10:
                continue
            tr = find_transition(series)          # coarse locate
            if tr is None:
                continue
            t0, t1, coarse_min = tr
            coarse_durations.append(coarse_min)
            best = coarse_min                      # fallback if refine fails
            if not a.no_refine:
                # Re-pull a tight window around the coarse move so fidelity=1
                # actually delivers 1-min bars (interval=max gets coarsened).
                fseries = fetch_history(token, start=t0 - 2*3600,
                                        end=t1 + 1800, fidelity=1)
                if len(fseries) >= 5:
                    sp = median_bar_spacing_min(fseries)
                    if sp is not None:
                        fine_spacings.append(sp)
                    ftr = find_transition(fseries)
                    if ftr is not None:
                        best = ftr[2]
                        refined_n += 1
                time.sleep(0.2)
            durations.append(best)
            if examined % 25 == 0:
                print(f"  examined {examined}, usable {len(durations)} "
                      f"(refined {refined_n})…", flush=True)
            time.sleep(0.2)

    def stats(xs):
        xs = sorted(xs)
        def pct(q): return xs[min(len(xs) - 1, int(len(xs) * q))]
        return (st.median(xs), pct(.10), pct(.25), pct(.75), pct(.90))

    print(f"\n=== RESULT: {len(durations)} markets with a measurable uncertain->decided move ===")
    if not durations:
        print("No usable transitions. Try --fidelity 1 or a larger --max-markets.")
        return

    # Resolution check — this is what makes the number trustworthy.
    if fine_spacings:
        sp_med, *_ = stats(fine_spacings)
        print(f"  refine pass: {refined_n}/{len(durations)} moves re-measured at "
              f"median {sp_med:.1f}-min bar spacing (target ~1.0).")
        if sp_med > 3:
            print("  ⚠️ refine window STILL coarse (>3 min/bar) — API didn't honor")
            print("     fidelity=1 even on a bounded window; treat result as upper bound.")
    else:
        print("  ⚠️ no refine data — reporting coarse single-pass numbers only.")

    if coarse_durations:
        cm, *_ = stats(coarse_durations)
        print(f"  (for contrast, OLD coarse method median = {cm:.1f} min)")

    med, p10, p25, p75, p90 = stats(durations)
    print(f"  reprice duration (minutes), REFINED:")
    print(f"    p10={p10:.1f}  p25={p25:.1f}  MEDIAN={med:.1f}  p75={p75:.1f}  p90={p90:.1f}")
    under2 = sum(1 for x in durations if x <= 2) / len(durations)
    print(f"    {under2:.0%} of markets repriced in <=2 min")
    print("\n=== VERDICT ===")
    if med <= 2:
        print(" Book reprices almost instantly (median <=2 min). A latency edge is")
        print(" effectively DEAD — you cannot beat a book that snaps in one tick.")
    elif med <= 10:
        print(f" Borderline (median {med:.0f} min). Edge only if YOUR signal is faster")
        print(" than a few minutes AND beats the spread. Marginal; prototype cautiously.")
    else:
        print(f" Book is SLOW (median {med:.0f} min). A faster real-world signal could")
        print(" plausibly front-run it. Latency edge is worth a real prototype.")
    print("\n NOTE: trust the verdict only if the refine bar spacing above is ~1 min.")
    print(" If it's still ~10, the API coarsened even the bounded window and this")
    print(" remains an UPPER bound; live tick data would be the final word.")

if __name__ == "__main__":
    main()
