#!/usr/bin/env python3
"""gtrends_logger.py — READ-ONLY logger (NOT a trading leg, places NO trades).

Tests ONE honest hypothesis the cheap way: does a SPIKE in Google Trends search
interest for an asset predict its forward PRICE move? (Attention -> price.) The
prior here is a likely NULL — every attention leg on this venue (wikivol, redditbuzz,
ytbuzz) died because calibrated mids already price public attention in. This logger
exists to let the DATA say yes/no without risking a cent, not because we expect a yes.

The HONEST way (no lookahead, like candle_knowledge_logger):
  observe NOW: pull hourly Google Trends interest (keyless, pytrends 'now 7-d'), take
  the last COMPLETE hour as the observation, compute its spike features from the PRIOR
  hours in the SAME response (z-score vs trailing week, delta vs prev hour). Snapshot the
  asset spot price NOW (keyless CoinGecko). Then FORWARD-record the realized close-to-now
  return at +1h / +4h / +24h. The forward return is the only judge; its SIGN tells us
  whether attention spikes precede momentum (+) or mean-reversion (-) or nothing (~0).

Two views in the report:
  - FORWARD log (the truth): no-lookahead samples accumulated from deployment onward.
    Slow at first (a few per asset per hour) — settles for free over weeks.
  - SPIKE-bucket table: forward-return mean split by spike (z>=1) vs calm (z<1), per asset
    and pooled. Early n is tiny — read as a hint, never a verdict.

Graduation gate (this is MEASUREMENT, not a leg): promote to a real leg ONLY if, after
n>=30 FORWARD samples in a bucket, its mean forward return CI (bootstrap, leg_health.py)
excludes 0 AFTER a realistic cost AND it holds out of sample. |mean/SE|>2 here is a hint.
Expect a null; that is a successful, honest outcome.

PAPER ONLY, read-only. stdlib + pytrends only. Keyless (Google Trends + CoinGecko).
Usage: python3 gtrends_logger.py once | report
"""
import os, sys, json, time, math, urllib.request, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "gtrends_state.json")
LOG = os.path.join(HERE, "gtrends_log.jsonl")            # forward-resolved samples
VAULT = os.path.expanduser("~/Documents/PolymarketVault/Reports/gtrends_knowledge.md")
UA = {"User-Agent": "Mozilla/5.0"}

# asset: (Google Trends query term, CoinGecko id). Kept to 5 to stay polite with Trends.
ASSETS = {
    "bitcoin":  ("Bitcoin",  "bitcoin"),
    "ethereum": ("Ethereum", "ethereum"),
    "solana":   ("Solana",   "solana"),
    "dogecoin": ("Dogecoin", "dogecoin"),
    "xrp":      ("XRP",      "ripple"),
}
HORIZONS_H = [1, 4, 24]          # forward horizons in hours
RESOLVE_TOL_S = 20 * 60          # resolve a horizon once now >= obs + H - 20min
PENDING_MAX_AGE_S = 36 * 3600    # drop a pending sample 36h after obs (all horizons due by 24h)
TRENDS_SLEEP_S = 3.0             # polite gap between per-term Trends requests (429 guard)


# ---------- keyless fetchers (all fail-soft) ----------
def _trends_series(term):
    """Hourly interest for one term over the last 7d, on its OWN 0-100 scale.
    Returns list of (epoch_s, value, is_partial) or None on failure."""
    from pytrends.request import TrendReq          # imported here so a missing dep fails soft
    pt = TrendReq(hl="en-US", tz=0, timeout=(10, 25))   # NB: no retries= (urllib3 2.x bug)
    pt.build_payload([term], timeframe="now 7-d")
    df = pt.interest_over_time()
    if df is None or df.empty or term not in df.columns:
        return None
    out = []
    for ts, row in df.iterrows():
        out.append((int(ts.timestamp()), int(row[term]), bool(row.get("isPartial", False))))
    return out


def _coingecko_prices(ids):
    """Keyless spot USD for a list of CoinGecko ids -> {id: price} (fail-soft, {} on error)."""
    url = ("https://api.coingecko.com/api/v3/simple/price?ids="
           + ",".join(ids) + "&vs_currencies=usd")
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=20) as r:
            d = json.load(r)
        return {k: float(v["usd"]) for k, v in d.items() if "usd" in v}
    except Exception:
        return {}


# ---------- signal features (computed from the SAME response: no lookahead) ----------
def _spike_features(series):
    """From an hourly series, take the last COMPLETE hour as the observation and compute
    its features from the PRIOR complete hours only. Returns dict or None."""
    complete = [(t, v) for (t, v, partial) in series if not partial]
    if len(complete) < 25:                      # need a trailing baseline
        return None
    obs_t, obs_v = complete[-1]
    prior = [v for (_t, v) in complete[:-1]]
    mean = sum(prior) / len(prior)
    var = sum((v - mean) ** 2 for v in prior) / len(prior)
    sd = math.sqrt(var)
    z = (obs_v - mean) / sd if sd > 1e-9 else 0.0
    delta = obs_v - prior[-1]
    return {"obs_t": obs_t, "level": obs_v, "baseline_mean": round(mean, 2),
            "z": round(z, 3), "delta": delta}


# ---------- state ----------
def _load_state():
    try:
        return json.load(open(STATE))
    except Exception:
        return {"pending": [], "seen": []}      # seen = list of "asset@obs_t" already logged


def _save_state(st):
    st["seen"] = st["seen"][-2000:]             # bound growth
    tmp = STATE + ".tmp"
    json.dump(st, open(tmp, "w"))
    os.replace(tmp, STATE)


def _append_log(rec):
    with open(LOG, "a") as f:
        f.write(json.dumps(rec) + "\n")


# ---------- once ----------
def run_once():
    st = _load_state()
    now = int(time.time())                      # forward-clock anchor for samples made this run
    seen = set(st["seen"])
    prices = _coingecko_prices([cg for (_term, cg) in ASSETS.values()])

    # 1) observe: create new pending samples for any fresh complete hour
    new_pending = 0
    for asset, (term, cg) in ASSETS.items():
        try:
            series = _trends_series(term)
        except Exception as e:
            print(f"  trends FAIL {asset}: {type(e).__name__}: {str(e)[:80]}")
            series = None
        if not series:
            continue
        feat = _spike_features(series)
        if not feat:
            continue
        key = f"{asset}@{feat['obs_t']}"
        if key in seen:
            continue                            # already captured this hour
        px = prices.get(cg)
        if px is None:
            continue                            # need an entry price to forward-resolve
        st["pending"].append({
            "asset": asset, "obs_t": now, "trends_hour": feat["obs_t"], "obs_price": px,
            "level": feat["level"], "z": feat["z"], "delta": feat["delta"],
            "baseline_mean": feat["baseline_mean"], "horizons_left": list(HORIZONS_H),
        })
        seen.add(key)
        new_pending += 1
        time.sleep(TRENDS_SLEEP_S)              # be polite to Trends between terms

    # 2) forward-resolve: any pending horizon whose time has come, priced at now
    resolved = 0
    still = []
    for p in st["pending"]:
        cg = ASSETS[p["asset"]][1]
        px_now = prices.get(cg)
        left = []
        for H in p["horizons_left"]:
            due = p["obs_t"] + H * 3600 - RESOLVE_TOL_S
            if now >= due and px_now is not None and p["obs_price"]:
                fwd_ret = (px_now - p["obs_price"]) / p["obs_price"]
                _append_log({
                    "resolved_ts": now, "asset": p["asset"], "obs_t": p["obs_t"],
                    "trends_hour": p.get("trends_hour"),
                    "horizon_h": H, "elapsed_h": round((now - p["obs_t"]) / 3600, 2),
                    "level": p["level"], "z": p["z"], "delta": p["delta"],
                    "baseline_mean": p["baseline_mean"],
                    "price_obs": p["obs_price"], "price_now": px_now,
                    "fwd_ret": round(fwd_ret, 6),
                })
                resolved += 1
            else:
                left.append(H)
        p["horizons_left"] = left
        if left and (now - p["obs_t"]) < PENDING_MAX_AGE_S:
            still.append(p)                     # keep; else drop (resolved or too old)
    st["pending"] = still
    st["seen"] = list(seen)
    _save_state(st)

    write_report()
    print(f"gtrends: +{new_pending} pending, {resolved} resolved, "
          f"{len(st['pending'])} open -> {VAULT}")


# ---------- report ----------
def _read_log():
    if not os.path.exists(LOG):
        return []
    out = []
    for ln in open(LOG):
        ln = ln.strip()
        if ln:
            try:
                out.append(json.loads(ln))
            except Exception:
                pass
    return out


def _bucket_stats(rows):
    """mean fwd_ret, n, SE, t for a list of rows."""
    n = len(rows)
    if n == 0:
        return (0.0, 0, 0.0, 0.0)
    rets = [r["fwd_ret"] for r in rows]
    mean = sum(rets) / n
    if n > 1:
        var = sum((x - mean) ** 2 for x in rets) / (n - 1)
        se = math.sqrt(var / n)
    else:
        se = 0.0
    t = mean / se if se > 1e-12 else 0.0
    return (mean, n, se, t)


def write_report():
    log = _read_log()
    L = ["# Google Trends -> Price — forward-return knowledge logger", "",
         "> READ-ONLY, no-lookahead. Does a search-interest SPIKE predict the forward move?",
         "> Prior = likely NULL (attention is priced in on calibrated mids — see strategy graveyard).",
         "> Graduation: a bucket needs n>=30 AND bootstrap CI>0 after cost AND holds OOS. |t|>2 = hint only.",
         "", f"Total forward samples: **{len(log)}**", ""]
    if not log:
        L += ["_No resolved samples yet — accumulating. First +1h samples resolve ~1h after first run._"]
    else:
        # per horizon: spike (z>=1) vs calm (z<1), pooled across assets
        L += ["## Pooled: spike (z>=1) vs calm (z<1), by horizon", "",
              "| horizon | bucket | n | mean fwd_ret | SE | t |",
              "|---|---|---:|---:|---:|---:|"]
        for H in HORIZONS_H:
            hr = [r for r in log if r["horizon_h"] == H]
            for name, sub in [("spike z>=1", [r for r in hr if r["z"] >= 1.0]),
                              ("calm  z<1", [r for r in hr if r["z"] < 1.0])]:
                m, n, se, t = _bucket_stats(sub)
                L.append(f"| +{H}h | {name} | {n} | {m*100:+.3f}% | {se*100:.3f}% | {t:+.2f} |")
        # per asset, all horizons pooled
        L += ["", "## Per asset (all horizons pooled)", "",
              "| asset | n | mean fwd_ret | t |", "|---|---:|---:|---:|"]
        for a in ASSETS:
            sub = [r for r in log if r["asset"] == a]
            m, n, se, t = _bucket_stats(sub)
            L.append(f"| {a} | {n} | {m*100:+.3f}% | {t:+.2f} |")
    L += ["", f"_updated {time.strftime('%Y-%m-%d %H:%M', time.localtime())} · paper · keyless_"]
    os.makedirs(os.path.dirname(VAULT), exist_ok=True)
    tmp = VAULT + ".tmp"
    open(tmp, "w").write("\n".join(L) + "\n")
    os.replace(tmp, VAULT)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "once"
    if cmd == "report":
        write_report()
        log = _read_log()
        print(f"gtrends report: {len(log)} forward samples -> {VAULT}")
    else:
        run_once()


if __name__ == "__main__":
    main()
