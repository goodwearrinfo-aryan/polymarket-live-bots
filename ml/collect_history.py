#!/usr/bin/env python3
"""
ml/collect_history.py — build a labeled training set from Polymarket history.

For each RESOLVED market it pulls the YES-token price history from the CLOB,
samples several "as-of" moments, and for each writes:
  features (see features.py)  +  label_move (prob moves >5% in next 24h)  +  final outcome.

Output: ml/dataset.csv  (append-only, resumable — processed markets tracked in ml/.done_markets)

Usage:
  python3 collect_history.py [--max-markets 150] [--category crypto|politics|sports|all] [--horizon 24] [--threshold 0.05]

Runs on the Mac (needs internet). stdlib only.
"""
import os, sys, csv, json, time, argparse, urllib.parse, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
import features as F  # noqa: E402

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
DATASET = os.path.join(HERE, "dataset.csv")
DONE = os.path.join(HERE, ".done_markets")
UA = "polymarket-ml-collector/1.0"
TIMEOUT = 12


def _get(url, params=None):
    if params:
        url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except Exception as e:
            if attempt == 2:
                sys.stderr.write(f"GET fail {url[:70]}: {e}\n")
                return None
            time.sleep(1.5 * (attempt + 1))


def _parse_json_field(v):
    if isinstance(v, (list, dict)):
        return v
    try:
        return json.loads(v)
    except Exception:
        return None


def _to_sec(dt):
    if not dt:
        return None
    try:
        s = str(dt).replace("Z", "+00:00")
        from datetime import datetime
        return int(datetime.fromisoformat(s).timestamp())
    except Exception:
        return None


def price_history(token_id):
    d = _get(f"{CLOB}/prices-history", {"market": token_id, "interval": "max", "fidelity": 60})
    if not d:
        return []
    hist = d.get("history") if isinstance(d, dict) else d
    out = []
    for pt in hist or []:
        t = pt.get("t") or pt.get("timestamp")
        p = pt.get("p") or pt.get("price")
        if t is not None and p is not None:
            out.append((int(t), float(p)))
    return sorted(out)


def category_of(mkt):
    tags = " ".join(str(t) for t in (mkt.get("tags") or [])).lower()
    q = (mkt.get("question") or "").lower()
    blob = tags + " " + q
    if any(k in blob for k in ("bitcoin", "ethereum", "crypto", "btc", "eth", "solana")):
        return "crypto"
    if any(k in blob for k in ("election", "president", "senate", "congress", "minister", "vote", "poll")):
        return "politics"
    if any(k in blob for k in ("nba", "nfl", "soccer", "tennis", "ufc", "mlb", "match", "vs.", "game")):
        return "sports"
    return "other"


def load_done():
    try:
        return set(open(DONE).read().split())
    except FileNotFoundError:
        return set()


def mark_done(cid):
    with open(DONE, "a") as f:
        f.write(cid + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-markets", type=int, default=400)   # distinct markets matter, not rows
    ap.add_argument("--category", default="all")
    ap.add_argument("--horizon", type=float, default=24.0)
    ap.add_argument("--threshold", type=float, default=0.05)
    ap.add_argument("--samples-per-market", type=int, default=3)  # cap rows/market (was ~7-43 -> leak)
    a = ap.parse_args()

    done = load_done()
    new_file = not os.path.exists(DATASET)
    fout = open(DATASET, "a", newline="")
    writer = csv.writer(fout)
    if new_file:
        writer.writerow(["condition_id", "category", "as_of", "resolve"] +
                        F.FEATURE_NAMES + ["label_move", "final_outcome"])

    processed = rows = 0
    offset = 0
    PAGE = 100
    while processed < a.max_markets:
        markets = _get(f"{GAMMA}/markets", {
            "closed": "true", "limit": PAGE, "offset": offset,
            "order": "volume", "ascending": "false"})
        offset += PAGE
        if not markets:
            break
        for mkt in markets:
            if processed >= a.max_markets:
                break
            cid = mkt.get("conditionId")
            if not cid or cid in done:
                continue
            cat = category_of(mkt)
            if a.category != "all" and cat != a.category:
                continue
            tokens = _parse_json_field(mkt.get("clobTokenIds"))
            prices = _parse_json_field(mkt.get("outcomePrices"))
            if not tokens:
                mark_done(cid); continue
            yes_token = str(tokens[0])
            final = None
            if isinstance(prices, list) and prices:
                try:
                    final = 1 if float(prices[0]) > 0.5 else 0
                except Exception:
                    final = None
            resolve = _to_sec(mkt.get("endDate")) or _to_sec(mkt.get("closedTime"))
            vol = float(mkt.get("volumeNum") or mkt.get("volume") or 0)
            liq = float(mkt.get("liquidityNum") or mkt.get("liquidity") or 0)

            series = price_history(yes_token)
            mark_done(cid)
            processed += 1
            if len(series) < 8 or not resolve:
                continue

            start = series[0][0]
            lo = start + int(24 * 3600)             # need 24h of history behind
            hi = resolve - int(a.horizon * 3600)    # need horizon of future ahead
            if hi <= lo:
                continue
            # Take only a few EVENLY-SPACED as-of points per market (capped) so one
            # market can't dominate the dataset with dozens of correlated rows.
            k = max(1, a.samples_per_market)
            sample_ts = [hi] if k == 1 else [int(lo + (hi - lo) * i / (k - 1)) for i in range(k)]
            for t in sample_ts:
                feats = F.compute(series, t, resolve, vol, liq, 0.0)
                lbl = F.label_move(series, t, a.horizon, a.threshold)
                if feats and lbl is not None:
                    writer.writerow([cid, cat, t, resolve] +
                                    [round(x, 6) for x in F.row_vector(feats)] +
                                    [lbl, final if final is not None else ""])
                    rows += 1
            if processed % 20 == 0:
                fout.flush()
                print(f"  processed {processed} markets, {rows} rows…")
            time.sleep(0.2)  # be gentle

    fout.close()
    print(f"done: +{processed} markets, +{rows} rows -> {DATASET}")


if __name__ == "__main__":
    main()
