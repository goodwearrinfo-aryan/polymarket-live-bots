#!/usr/bin/env python3
"""
backtest_nearres.py — out-of-sample backtest of the nearres thesis on RESOLVED
markets, no API key needed (gamma closed listing + CLOB prices-history).

Method:
  1. Paginate gamma ?closed=true (recent first), keyword-filter esports.
  2. For each market: CLOB token + minute-level price history.
  3. Resolution time T_res = first bar entering the terminal pin (|p - outcome|
     <= 0.02) that STAYS pinned to series end. Padded endDates are irrelevant —
     resolution is derived from price, not metadata.
  4. Simulated entry: first bar in [T_res - 4h, T_res) where the favorite's mid
     is inside [band_lo, band_hi]. Fill = mid + HALF_SPREAD.
  5. Exits mirror live config: target +9c (LIMIT fill at target - HALF_SPREAD,
     a limit fills at its price even on a favorable gap), stop -3c TRIGGER but
     fills at the REALIZED bar mid - HALF_SPREAD (models gap-through: esports
     favorites jump past the stop in one tick, so the stop fills far below the
     trigger), else settle at outcome. Intra-bar tie: stop first (conservative).
  6. Bootstrap 95% CI on per-trade P&L ($2 bets, same as live).

Read-only research. Run: python3 backtest_nearres.py [n_markets]
"""
import json, math, random, sys, time, urllib.request

GAMMA = "https://gamma-api.polymarket.com/markets"
CLOB  = "https://clob.polymarket.com"
UA    = {"User-Agent": "Mozilla/5.0"}
HALF_SPREAD = 0.015
BET = 2.0
GAIN, STOP = 0.09, 0.03
WINDOW_H = 4   # default; also tested at 2h (see __main__)
ESPORTS_KW = ("counter-strike", "cs2", "valorant", "league of legends", "lol:",
              "dota", "overwatch", "iem ", "pgl major", "blast premier",
              "vct ", "lcq ", "lcs ", "lck ", "lpl ", "esport")
SPORTS_KW  = ("tennis", "atp", "wta", "mlb", "nba", "nhl", "nfl", "soccer",
              "premier league", "la liga", "serie a", "bundesliga",
              "championships:", "open:", " vs. ")
RESULTS_F  = "backtest_results.json"
SUMMARY_F  = "backtest_summary.json"

def get(url):
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except Exception:
        return None

def closed_markets(max_markets, kws, skip_ids=()):
    out, offset = [], 0
    while len(out) < max_markets and offset < 3000:
        page = get(f"{GAMMA}?closed=true&order=volume24hr&ascending=false"
                   f"&limit=100&offset={offset}")
        if not page:
            break
        for m in page:
            q = (m.get("question") or "").lower()
            cid = m.get("conditionId")
            if cid and cid not in skip_ids and any(k in q for k in kws):
                out.append(m)
        offset += 100
        time.sleep(0.25)
    return out[:max_markets]

def simulate(market, band_lo, band_hi, window_h=WINDOW_H):
    cid = market["conditionId"]
    cm = get(f"{CLOB}/markets/{cid}")
    if not cm or not cm.get("closed") or not cm.get("tokens"):
        return None
    tok = cm["tokens"][0]
    outcome = 1.0 if tok.get("winner") else 0.0
    if tok.get("price") not in (0, 1, 0.0, 1.0):       # not cleanly settled
        if abs(float(tok.get("price") or 0.5) - round(float(tok.get("price") or 0.5))) > 0.02:
            return None
        outcome = float(round(float(tok["price"])))
    time.sleep(0.12)
    h = (get(f"{CLOB}/prices-history?market={tok['token_id']}"
             f"&interval=1w&fidelity=10") or {}).get("history", [])
    if len(h) < 12:
        return None

    # T_res: first bar of the terminal pin that persists to the end
    t_res = None
    for i in range(len(h) - 1, -1, -1):
        if abs(h[i]["p"] - outcome) > 0.02:
            t_res = h[i + 1]["t"] if i + 1 < len(h) else None
            break
    else:
        t_res = h[0]["t"]                               # pinned the whole series
    if t_res is None:
        return None

    # entry: first bar in the pre-resolution window with favorite in band
    entry = None
    for i, bar in enumerate(h):
        if not (t_res - window_h * 3600 <= bar["t"] < t_res):
            continue
        p = bar["p"]
        side_yes = p >= 0.5
        mid = p if side_yes else 1 - p
        if band_lo <= mid <= band_hi:
            entry = (i, side_yes, mid)
            break
    if entry is None:
        return None
    i0, side_yes, mid0 = entry
    fill = mid0 + HALF_SPREAD
    size = BET / fill

    # walk forward: stop first on tie (conservative)
    for bar in h[i0 + 1:]:
        p = bar["p"]
        mid = p if side_yes else 1 - p
        if mid <= mid0 - STOP:
            # GAP-THROUGH HONEST (2026-06-14): book the stop at the REALIZED bar
            # mid (cross the spread to exit), NOT the trigger level. Esports
            # favorites gap 0.93 -> ~0.30 in one tick; the prior `mid0 - STOP`
            # fill assumed a clean -3c exit that live cannot get, overstating the
            # edge. Live n=28 proved this: 3 gapped stops cost -$1.02 vs the
            # -$0.25 the clean-fill model assumed.
            return ("stop", round((max(0.001, mid - HALF_SPREAD) - fill) * size, 4), mid0)
        if mid >= mid0 + GAIN:
            return ("target", round((mid0 + GAIN - HALF_SPREAD - fill) * size, 4), mid0)
    settle = outcome if side_yes else 1 - outcome
    return ("resolve", round((settle - fill) * size, 4), mid0)

def report(label, results):
    pnls = [p for _, p in results]
    if not pnls:
        print(f"{label}: no trades")
        return
    w = sum(1 for p in pnls if p > 0)
    random.seed(11)
    means = sorted(sum(random.choices(pnls, k=len(pnls))) / len(pnls)
                   for _ in range(10000))
    reasons = {}
    for r, _ in results:
        reasons[r] = reasons.get(r, 0) + 1
    print(f"{label}: n={len(pnls)} wr={w/len(pnls)*100:.0f}% "
          f"pnl=${sum(pnls):+.2f} avg=${sum(pnls)/len(pnls):+.4f} "
          f"CI95=[{means[250]:+.4f},{means[9750]:+.4f}] "
          f"{'EXCLUDES 0 ✓' if means[250] > 0 else 'includes 0'} {reasons}")

if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    # accumulate: skip markets already simulated in prior runs (24/7 mode)
    try:
        store = json.load(open(RESULTS_F))
    except Exception:
        store = {"done": {}, "trades": {"esports_hi": [], "esports_lo": [], "sports_hi": []}}
    done = set(store["done"])

    groups = [("esports_hi", ESPORTS_KW, 0.88, 0.95),
              ("esports_lo", ESPORTS_KW, 0.80, 0.8799),
              ("sports_hi",  SPORTS_KW,  0.88, 0.95)]
    fresh = {}
    for label, kws, *_ in groups:
        if kws is ESPORTS_KW and "esports" in fresh:
            continue
        key = "esports" if kws is ESPORTS_KW else "sports"
        fresh[key] = closed_markets(n, kws, skip_ids=done)
    print(f"new resolved markets: esports={len(fresh.get('esports', []))} "
          f"sports={len(fresh.get('sports', []))}")

    for label, kws, lo, hi in groups:
        key = "esports" if kws is ESPORTS_KW else "sports"
        for m in fresh.get(key, []):
            r = simulate(m, lo, hi)
            if r:
                store["trades"][label].append(
                    {"cid": m["conditionId"], "q": (m.get("question") or "")[:60],
                     "reason": r[0], "pnl": r[1],
                     "entry": round(r[2], 4)})

    # 2h window test: re-simulate esports_hi markets at window_h=2
    # Uses the same already-fetched market list (no extra API calls)
    if "esports_hi_2h" not in store["trades"]:
        store["trades"]["esports_hi_2h"] = []
    _2h_done = {t["cid"] for t in store["trades"]["esports_hi_2h"]}
    for m in fresh.get("esports", []):
        if m["conditionId"] in _2h_done:
            continue
        r2 = simulate(m, 0.88, 0.95, window_h=2)
        if r2:
            store["trades"]["esports_hi_2h"].append(
                {"cid": m["conditionId"], "q": (m.get("question") or "")[:60],
                 "reason": r2[0], "pnl": r2[1], "entry": round(r2[2], 4)})
    for key in fresh:
        for m in fresh[key]:
            store["done"][m["conditionId"]] = 1
    json.dump(store, open(RESULTS_F, "w"))

    summary = {"updated": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
               "markets_processed": len(store["done"]), "bands": {}}
    print()
    for label, _, lo, hi in groups:
        rows = [(t["reason"], t["pnl"]) for t in store["trades"][label]]
        report(f"{label} [{lo},{hi}] 4h", rows)
        pnls = [p for _, p in rows]
        if pnls:
            random.seed(11)
            means = sorted(sum(random.choices(pnls, k=len(pnls))) / len(pnls)
                           for _ in range(10000))
            summary["bands"][label] = {
                "n": len(pnls),
                "wr": round(sum(1 for p in pnls if p > 0) / len(pnls) * 100, 1),
                "pnl": round(sum(pnls), 2),
                "ci_lo": round(means[250], 4), "ci_hi": round(means[9750], 4)}
    # 2h window comparison (Section A / Section D experiment)
    rows_2h = [(t["reason"], t["pnl"]) for t in store["trades"].get("esports_hi_2h", [])]
    if rows_2h:
        report("esports_hi [0.88,0.95] 2h", rows_2h)
        pnls_2h = [p for _, p in rows_2h]
        random.seed(11)
        means_2h = sorted(sum(random.choices(pnls_2h, k=len(pnls_2h))) / len(pnls_2h)
                          for _ in range(10000))
        summary["bands"]["esports_hi_2h"] = {
            "n": len(pnls_2h),
            "wr": round(sum(1 for p in pnls_2h if p > 0) / len(pnls_2h) * 100, 1),
            "pnl": round(sum(pnls_2h), 2),
            "ci_lo": round(means_2h[250], 4), "ci_hi": round(means_2h[9750], 4)}
        rows_4h = [(t["reason"], t["pnl"]) for t in store["trades"].get("esports_hi", [])]
        if rows_4h:
            pnls_4h = [p for _, p in rows_4h]
            avg_4h = sum(pnls_4h) / len(pnls_4h)
            avg_2h = sum(pnls_2h) / len(pnls_2h)
            print(f"  2h vs 4h $/trade: {avg_2h:+.4f} vs {avg_4h:+.4f}  "
                  f"{'2h BETTER' if avg_2h > avg_4h else '4h better or equal'}")

    # DSR (Bailey & Lopez de Prado 2014): with N_legs tested, the effective
    # significance threshold for Sharpe rises by sqrt(ln(N_legs)).
    # nearres is 1 of ~90 legs ever tried -> correction = sqrt(ln(90)) ≈ 2.20;
    # raw SR must exceed SR_min * 2.20 to clear DSR.  SR_min = 0 (any edge) ->
    # DSR > 0 iff raw_SR * (1 - sqrt(ln(N)/n) * z) > 0, which is equivalent to
    # CI_lo_dsr > 0 (the bootstrap already gives a size-corrected CI).
    # Here we compute a simple deflated threshold: adjusted SR floor = SR * correction.
    N_LEGS = 90  # total independent leg strategies ever trialled
    for label in summary["bands"]:
        b = summary["bands"][label]
        n = b["n"]
        if n < 2:
            continue
        pnls_b = [t["pnl"] for t in store["trades"][label]]
        mu  = sum(pnls_b) / n
        std = math.sqrt(sum((p - mu) ** 2 for p in pnls_b) / (n - 1)) or 1e-9
        raw_sr  = mu / std * math.sqrt(n)
        correction = math.sqrt(math.log(max(N_LEGS, 2)))
        # DSR < 0 when |raw_sr| < correction; we report DSR as the deflated value
        dsr = raw_sr - correction  # Bailey & LdP formulation (z-score space)
        b["raw_sr"]    = round(raw_sr, 3)
        b["dsr"]       = round(dsr, 3)
        b["dsr_passes"] = dsr > 0
        print(f"  DSR {label}: raw_SR={raw_sr:.3f}  correction={correction:.3f}  "
              f"DSR={dsr:.3f}  {'PASSES ✓' if dsr > 0 else 'FAILS (not yet significant)'}")
    json.dump(summary, open(SUMMARY_F, "w"), indent=1)
