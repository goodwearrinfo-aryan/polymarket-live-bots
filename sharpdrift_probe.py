#!/usr/bin/env python3
"""sharpdrift_probe.py — READ-ONLY logger (NOT a trading leg, places NO trades).

Answers ONE empirical question before any leg is built: does CONSENSUS among
identity-vetted high-win-rate wallets actually PRECEDE price on slow markets,
or is it the dead whale-follow family in an identity costume?

Mechanism (logging only):
  - whitelist = insider_wallets.json + whale_candidates.json wallets with WR>=65%.
  - each cycle, pull data-api /activity for each, keep TRADE/BUY >= $200 in last 6h.
  - bucket by (conditionId, outcome). A CONSENSUS EVENT = >=2 SYBIL-INDEPENDENT
    wallets on the SAME outcome of the SAME slow+liquid market (endDate>=7d,
    not Up/Down|crypto-5m|same-day-sports, vol>=$50k, mid in [0.10,0.90], spread<=3c).
  - snapshot the outcome mid at t0; forward-record the mid at +1h/+6h/+24h/+48h.
  - record drift net of round-trip spread, PLUS the ANTI-CONTROL (the opposite
    outcome — must drift NEGATIVE if the signal is real) and the raw move.

Graduation gate (NOT met by this probe — it only gathers evidence): only if
weeks of logs show drift>round-trip on a majority of events AND the anti-control
is clearly negative does this become a $1.50 paper leg under the normal
n>=30 + bootstrap CI>0 + DSR + controls-negative gate.

PAPER ONLY: read-only gamma/data-api, no keys, no orders. Stdlib only.
Usage: python3 sharpdrift_probe.py once   |   report
"""
import os, sys, json, re, time, urllib.request, urllib.parse
import calendar

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "sharpdrift_state.json")
LOG = os.path.join(HERE, "sharpdrift_log.jsonl")
VAULT = os.path.expanduser("~/Documents/PolymarketVault/Reports/sharpdrift.md")
UA = {"User-Agent": "Mozilla/5.0"}

WR_MIN = 65            # whitelist win-rate floor (%)
MIN_USD = 200          # min trade conviction size
LOOKBACK_H = 6         # only consider buys this fresh
SLOW_MIN_DAYS = 7      # market must resolve >= this far out (no gap-through soon)
MIN_VOL = 50_000
MID_LO, MID_HI = 0.10, 0.90
MAX_SPREAD = 0.03
WINDOWS_H = [1, 6, 24, 48]
SLOW_BLOCK = ("up or down", " up ", " down ", "5m", "1h ", "hourly")  # exclude fast/coinflip


def _get(url, timeout=15):
    return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout))


def base_addr(a):
    """Strip the -<timestamp> proxy suffix so sybil-variant addresses of one
    entity collapse to a single identity (independence proxy)."""
    return (a or "").lower().split("-")[0]


def load_whitelist():
    wl = {}
    p = os.path.join(HERE, "insider_wallets.json")
    if os.path.exists(p):
        for addr, label in json.load(open(p)).items():
            m = re.search(r"(\d+)pct", label or "")
            wr = int(m.group(1)) if m else 0
            if wr >= WR_MIN:
                wl[addr.lower()] = {"wr": wr, "label": label}
    p = os.path.join(HERE, "whale_candidates.json")
    if os.path.exists(p):
        for w in json.load(open(p)):
            if w.get("win_rate", 0) >= WR_MIN and w["addr"].lower() not in wl:
                wl[w["addr"].lower()] = {"wr": w["win_rate"], "label": f"whale-{w['win_rate']}pct"}
    return wl


def recent_buys(addr):
    try:
        u = "https://data-api.polymarket.com/activity?" + urllib.parse.urlencode({"user": addr, "limit": 60})
        rows = _get(u)
    except Exception:
        return []
    cut = time.time() - LOOKBACK_H * 3600
    out = []
    for r in rows:
        if (r.get("type") == "TRADE" and r.get("side") == "BUY"
                and float(r.get("usdcSize") or 0) >= MIN_USD
                and int(r.get("timestamp") or 0) >= cut):
            out.append(r)
    return out


_mkcache = {}
def market_by_cid(cid):
    if cid in _mkcache:
        return _mkcache[cid]
    try:
        u = "https://gamma-api.polymarket.com/markets?" + urllib.parse.urlencode({"condition_ids": cid})
        m = _get(u)
        res = m[0] if m else None
    except Exception:
        res = None
    _mkcache[cid] = res
    return res


def slow_liquid_mid(mk, outcome_idx):
    """Return outcome mid if the market is slow + liquid + tradeable, else None."""
    if not mk:
        return None
    q = (mk.get("question") or "").lower()
    if any(b in q for b in SLOW_BLOCK):
        return None
    end = mk.get("endDate") or mk.get("endDateIso")
    if end:
        try:
            ts = calendar.timegm(time.strptime(end[:19], "%Y-%m-%dT%H:%M:%S"))
            if (ts - time.time()) < SLOW_MIN_DAYS * 86400:
                return None
        except Exception:
            pass
    if float(mk.get("volume") or 0) < MIN_VOL:
        return None
    op = mk.get("outcomePrices")
    op = json.loads(op) if isinstance(op, str) else op
    if not op or outcome_idx >= len(op):
        return None
    mid = float(op[outcome_idx])
    if not (MID_LO <= mid <= MID_HI):
        return None
    spread = mk.get("spread")
    if spread is not None and float(spread) > MAX_SPREAD:
        return None
    return mid


def load_state():
    if os.path.exists(STATE):
        try:
            return json.load(open(STATE))
        except Exception:
            pass   # corrupt state -> re-seed fresh (atomic writes make this rare;
                   # without this the watchdog's `|| true` would silently stall it forever)
    return {"pending": {}}


def save_state(st):
    tmp = STATE + ".tmp"; json.dump(st, open(tmp, "w")); os.replace(tmp, STATE)


_clusters = None
def cluster_id(addr):
    """Map a wallet to its coordination cluster (insider_cotrade.py). Two wallets in
    the SAME cluster (bot farm / info-sharing ring) are ONE opinion, not two — so
    consensus must count distinct cluster_ids, not addresses. Falls back to the
    base address if no cluster map exists yet."""
    global _clusters
    if _clusters is None:
        p = os.path.join(HERE, "insider_clusters.json")
        try:
            _clusters = json.load(open(p)) if os.path.exists(p) else {}
        except Exception:
            _clusters = {}
    return _clusters.get(base_addr(addr), base_addr(addr))


def detect(wl, st):
    """Find new consensus events; snapshot t0."""
    buckets = {}   # (cid, outcomeIdx) -> {ids:set(cluster), outcome, title, slug}
    for addr in wl:
        for r in recent_buys(addr):
            cid = r.get("conditionId"); oi = r.get("outcomeIndex")
            if cid is None or oi is None:
                continue
            key = f"{cid}|{oi}"
            b = buckets.setdefault(key, {"addrs": set(), "outcome": r.get("outcome"),
                                         "title": r.get("title"), "slug": r.get("eventSlug")})
            b["addrs"].add(cluster_id(addr))         # distinct CLUSTERS, not raw addresses
    new = 0
    for key, b in buckets.items():
        if len(b["addrs"]) < 2:                       # need >=2 independent identities
            continue
        if key in st["pending"]:
            continue
        cid, oi = key.split("|"); oi = int(oi)
        mk = market_by_cid(cid)
        mid = slow_liquid_mid(mk, oi)
        if mid is None:
            continue
        anti = 1.0 - mid                              # binary anti-control (opposite outcome)
        st["pending"][key] = {"t0": int(time.time()), "mid0": mid, "anti0": round(anti, 4),
                              "outcome": b["outcome"], "title": b["title"], "slug": b["slug"],
                              "n_wallets": len(b["addrs"]), "marks": {}}
        new += 1
        print(f"  CONSENSUS {len(b['addrs'])}w  {b['outcome']}@{mid:.3f}  {(b['title'] or '')[:48]}")
    return new


def revisit(st):
    """Fill elapsed forward windows; finalize at 48h -> append to log."""
    done = 0
    for key, ev in list(st["pending"].items()):
        cid, oi = key.split("|"); oi = int(oi)
        age_h = (time.time() - ev["t0"]) / 3600.0
        due = [w for w in WINDOWS_H if w <= age_h and str(w) not in ev["marks"]]
        if due:
            mk = market_by_cid(cid)
            op = mk.get("outcomePrices") if mk else None
            op = json.loads(op) if isinstance(op, str) else op
            if op and oi < len(op):
                mid = float(op[oi])
                for w in due:
                    ev["marks"][str(w)] = round(mid, 4)
        if age_h >= WINDOWS_H[-1]:                    # finalize
            rec = dict(ev); rec["key"] = key; rec["finalized"] = int(time.time())
            with open(LOG, "a") as f:
                f.write(json.dumps(rec) + "\n")
            del st["pending"][key]; done += 1
    return done


def _read_log():
    if not os.path.exists(LOG):
        return []
    return [json.loads(l) for l in open(LOG) if l.strip()]


def summarize():
    recs = _read_log()
    n = len(recs)
    if not n:
        return {"n": 0}
    drift = {w: [] for w in WINDOWS_H}; anti = {w: [] for w in WINDOWS_H}
    for r in recs:
        for w in WINDOWS_H:
            m = r["marks"].get(str(w))
            if m is not None:
                d = m - r["mid0"]                     # signal-side drift (want >0)
                drift[w].append(d)
                anti[w].append((1 - m) - r["anti0"])  # anti-control (want <0)
    def avg(x): return round(sum(x) / len(x), 4) if x else None
    return {"n": n,
            "drift": {w: avg(drift[w]) for w in WINDOWS_H},
            "anti": {w: avg(anti[w]) for w in WINDOWS_H},
            "pos_share": {w: (round(sum(1 for d in drift[w] if d > 0) / len(drift[w]), 2) if drift[w] else None) for w in WINDOWS_H}}


def export_obsidian(st):
    s = summarize()
    L = ["# Sharpdrift Probe — vetted-wallet consensus → price drift",
         "",
         "> READ-ONLY logger (no trades). Does consensus among WR≥65% identity-vetted wallets",
         "> PRECEDE price on slow markets? Signal-side drift should be >0 AND anti-control <0.",
         f"> Updated {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())} · pending={len(st['pending'])} · finalized={s.get('n',0)}",
         "", "## Forward drift (¢ per window, net of nothing yet — raw mid move)", ""]
    if s.get("n"):
        L += ["| window | signal drift | anti-control | % positive |",
              "|---|---|---|---|"]
        for w in WINDOWS_H:
            d = s["drift"][w]; a = s["anti"][w]; p = s["pos_share"][w]
            L.append(f"| +{w}h | {'' if d is None else f'{d*100:+.2f}¢'} | {'' if a is None else f'{a*100:+.2f}¢'} | {'' if p is None else f'{int(p*100)}%'} |")
        L += ["", "**Read:** real edge ⇒ signal drift >0, anti-control <0, %positive >55, "
              "and it must clear round-trip spread (~1–2¢) to matter. Verdict needs n≥30."]
    else:
        L += ["_No finalized events yet (each needs 48h to mature). Pending events accumulating._"]
    L += ["", "## Pending consensus events", ""]
    for ev in list(st["pending"].values())[:25]:
        age = (time.time() - ev["t0"]) / 3600
        L.append(f"- {ev['n_wallets']}w · {ev['outcome']}@{ev['mid0']:.3f} · {age:.1f}h old · {(ev['title'] or '')[:50]}")
    os.makedirs(os.path.dirname(VAULT), exist_ok=True)
    open(VAULT, "w").write("\n".join(L) + "\n")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "once"
    st = load_state()
    if cmd == "once":
        wl = load_whitelist()
        nd = revisit(st)
        nn = detect(wl, st)
        save_state(st)
        export_obsidian(st)
        print(f"sharpdrift: whitelist={len(wl)}  new_consensus={nn}  finalized={nd}  pending={len(st['pending'])}")
    elif cmd == "report":
        export_obsidian(st)
        print(json.dumps(summarize(), indent=2))
        print(f"pending={len(st['pending'])}  -> {VAULT}")
    else:
        print("usage: sharpdrift_probe.py once|report")


if __name__ == "__main__":
    main()
