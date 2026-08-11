#!/usr/bin/env python3
"""whalexit_logger.py — READ-ONLY logger (NOT a trading leg, places NO trades).

Question: when a profiled SHARP wallet SELLS / exits a slow-market position, does
price follow it DOWN? Smart-money EXITS may carry more information than entries
(you exit when you learn the thesis broke). Distinct from sharpdrift (entry
consensus) — this is the single-wallet exit signal.

Mechanism (logging only): each cycle, for WR>=65% vetted wallets, pull /activity,
keep TRADE/SELL >= $200 in last 6h on SLOW liquid markets. Snapshot the sold
outcome's mid; forward-record at +6/24/48h. A real signal ⇒ the mid DROPS after
the sharp exits (drift < 0). Anti-control: a random other holder's side shouldn't.

Reuses sharpdrift_probe's vetted-wallet + market plumbing. PAPER ONLY, read-only.
Usage: python3 whalexit_logger.py once | report
"""
import os, sys, json, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import sharpdrift_probe as sd   # reuse load_whitelist, base_addr, market_by_cid, slow_liquid_mid

STATE = os.path.join(HERE, "whalexit_state.json")
LOG = os.path.join(HERE, "whalexit_log.jsonl")
VAULT = os.path.expanduser("~/Documents/PolymarketVault/Reports/whalexit.md")
MIN_USD = 200
LOOKBACK_H = 6
WINDOWS_H = [6, 24, 48]


def recent_sells(addr):
    import urllib.request, urllib.parse, json as _j
    try:
        u = "https://data-api.polymarket.com/activity?" + urllib.parse.urlencode({"user": addr, "limit": 60})
        rows = _j.load(urllib.request.urlopen(urllib.request.Request(u, headers=sd.UA), timeout=15))
    except Exception:
        return []
    cut = time.time() - LOOKBACK_H * 3600
    return [r for r in rows if r.get("type") == "TRADE" and r.get("side") == "SELL"
            and float(r.get("usdcSize") or 0) >= MIN_USD and int(r.get("timestamp") or 0) >= cut]


def load_state():
    if os.path.exists(STATE):
        try:
            return json.load(open(STATE))
        except Exception:
            pass
    return {"pending": {}}


def save_state(st):
    tmp = STATE + ".tmp"; json.dump(st, open(tmp, "w")); os.replace(tmp, STATE)


def detect(st):
    wl = sd.load_whitelist()
    new = 0
    for addr in wl:
        for r in recent_sells(addr):
            cid = r.get("conditionId"); oi = r.get("outcomeIndex")
            if cid is None or oi is None:
                continue
            key = f"{cid}|{oi}|{sd.base_addr(addr)}"
            if key in st["pending"]:
                continue
            mk = sd.market_by_cid(cid)
            mid = sd.slow_liquid_mid(mk, oi)
            if mid is None:
                continue
            st["pending"][key] = {"t0": int(time.time()), "mid0": mid, "outcome": r.get("outcome"),
                                  "title": r.get("title"), "wr": wl[addr]["wr"], "marks": {}}
            new += 1
            print(f"  SHARP-EXIT  WR{wl[addr]['wr']}  sold {r.get('outcome')}@{mid:.3f}  {(r.get('title') or '')[:42]}")
    return new


def revisit(st):
    done = 0
    for key, ev in list(st["pending"].items()):
        cid, oi = key.split("|")[0], int(key.split("|")[1])
        age_h = (time.time() - ev["t0"]) / 3600
        due = [w for w in WINDOWS_H if w <= age_h and str(w) not in ev["marks"]]
        if due:
            mk = sd.market_by_cid(cid)
            op = mk.get("outcomePrices") if mk else None
            op = json.loads(op) if isinstance(op, str) else op
            if op and oi < len(op):
                for w in due:
                    ev["marks"][str(w)] = round(float(op[oi]), 4)
        if age_h >= WINDOWS_H[-1]:
            rec = dict(ev); rec["key"] = key
            with open(LOG, "a") as f:
                f.write(json.dumps(rec) + "\n")
            del st["pending"][key]; done += 1
    return done


def _read_log():
    return [json.loads(l) for l in open(LOG)] if os.path.exists(LOG) else []


def summarize():
    recs = _read_log()
    n = len(recs)
    if not n:
        return {"n": 0}
    drift = {w: [] for w in WINDOWS_H}
    for r in recs:
        for w in WINDOWS_H:
            m = r["marks"].get(str(w))
            if m is not None:
                drift[w].append(m - r["mid0"])      # want < 0 (price fell after the sharp exited)
    def avg(x): return round(100 * sum(x) / len(x), 2) if x else None
    return {"n": n, "post_exit_drift_c": {w: avg(drift[w]) for w in WINDOWS_H},
            "fell_share": {w: (round(sum(1 for d in drift[w] if d < 0) / len(drift[w]), 2) if drift[w] else None) for w in WINDOWS_H}}


def export_obsidian(st):
    s = summarize()
    L = ["# Whalexit Logger — does price follow sharp EXITS down?",
         "",
         "> READ-ONLY logger (no trades). After a WR≥65% wallet SELLS a slow market, the mid",
         "> should DROP if exits carry information. Real signal ⇒ post-exit drift <0, fell-share >55%.",
         f"> Updated {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())} · pending={len(st['pending'])} · matured={s.get('n',0)}",
         ""]
    if s.get("n"):
        L += ["| window | post-exit drift | % fell |", "|---|---|---|"]
        for w in WINDOWS_H:
            d = s["post_exit_drift_c"][w]; f = s["fell_share"][w]
            L.append(f"| +{w}h | {'' if d is None else f'{d:+.2f}¢'} | {'' if f is None else f'{int(f*100)}%'} |")
        L += ["", "**Read:** edge ⇒ drift clearly <0 and fell-share >55%, beating round-trip spread. "
              "If drift ≈0, sharp exits are noise (or just profit-taking on a still-fair price)."]
    else:
        L += ["_No matured sharp exits yet (each needs 48h). Accumulating._"]
    os.makedirs(os.path.dirname(VAULT), exist_ok=True)
    open(VAULT, "w").write("\n".join(L) + "\n")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "once"
    st = load_state()
    if cmd == "once":
        nd = revisit(st); nn = detect(st); save_state(st); export_obsidian(st)
        print(f"whalexit: new_exits={nn}  matured={nd}  pending={len(st['pending'])}")
    elif cmd == "report":
        export_obsidian(st); print(json.dumps(summarize(), indent=2))
    else:
        print("usage: whalexit_logger.py once|report")


if __name__ == "__main__":
    main()
