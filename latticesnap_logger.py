#!/usr/bin/env python3
"""latticesnap_logger.py — READ-ONLY logger (NOT a trading leg, places NO trades).

Answers ONE empirical question before any leg is built: in a dense price-reach
ladder (e.g. "Will BTC reach $120k / $140k / $160k / ..."), when one rung gets
flow-shocked and a neighbor LAGS (breaking the smooth monotonic spacing), which
one is right — does the laggard catch up (edge: buy the laggard) or does the
shocked rung snap back (no edge: you'd have bought air)?

Mechanism (logging only):
  - gamma /events: find ladders = events with >=4 "reach/above $K" rungs, vol>=200k.
  - each cycle, snapshot every rung's YES mid + best ask.
  - BREAK = vs last snapshot, one rung's mid moved >=3c while an adjacent rung
    lagged <1c (anomalous local spacing that should re-cohere), and the gap
    exceeds round-trip cost measured AT THE ASK (not the mid).
  - forward-record at +6h which rung re-cohered: shocked snapped back (laggard
    right -> edge) vs laggard caught up (shocked right -> no edge), and the
    at-ask net you'd have realized buying the laggard.

Graduation gate (NOT met here): build the trader only if logs show >=1 qualifying
at-ask-positive break/week with laggard-correct >60%. Else retire (it's
cluster-arb with extra steps). PAPER ONLY, read-only, stdlib only.
Usage: python3 latticesnap_logger.py once | report
"""
import os, sys, json, re, time, urllib.request, urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "latticesnap_state.json")
LOG = os.path.join(HERE, "latticesnap_log.jsonl")
VAULT = os.path.expanduser("~/Documents/PolymarketVault/Reports/latticesnap.md")
UA = {"User-Agent": "Mozilla/5.0"}

MIN_RUNGS = 4
MIN_RUNG_VOL = 200_000
SHOCK = 0.03           # a rung moved >= this between snapshots
LAG = 0.01             # while its neighbor moved < this
RESOLVE_H = 6          # forward window to judge who was right
LADDER_KW = ("reach", "above", "hit", "dip to", "exceed")


def _get(url, timeout=20):
    return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout))


def strike_of(q):
    m = re.search(r"\$([\d,]+)", q or "")
    return int(m.group(1).replace(",", "")) if m else None


def fetch_ladders():
    """Return {event_id: [ {strike, mid, ask, vol, q} ...sorted by strike]}."""
    try:
        u = "https://gamma-api.polymarket.com/events?" + urllib.parse.urlencode(
            {"closed": "false", "limit": 60, "order": "volume", "ascending": "false"})
        ev = _get(u)
    except Exception:
        return {}
    ladders = {}
    for e in ev:
        mks = e.get("markets") or []
        rungs = []
        for m in mks:
            q = (m.get("question") or "")
            if not any(k in q.lower() for k in LADDER_KW):
                continue
            k = strike_of(q)
            if k is None or float(m.get("volume") or 0) < MIN_RUNG_VOL:
                continue
            op = m.get("outcomePrices"); op = json.loads(op) if isinstance(op, str) else op
            if not op:
                continue
            ask = m.get("bestAsk")
            rungs.append({"strike": k, "mid": float(op[0]),
                          "ask": float(ask) if ask not in (None, "") else None,
                          "vol": round(float(m.get("volume") or 0)), "q": q[:60]})
        # dedupe by strike (a ladder must be monotonic in price; duplicate strikes
        # are same-level/different-date markets — keep the highest-volume one).
        bystrike = {}
        for r in rungs:
            if r["strike"] not in bystrike or r["vol"] > bystrike[r["strike"]]["vol"]:
                bystrike[r["strike"]] = r
        rungs = sorted(bystrike.values(), key=lambda r: r["strike"])
        if len(rungs) >= MIN_RUNGS:
            ladders[str(e.get("id"))] = {"title": (e.get("title") or "")[:50], "rungs": rungs}
    return ladders


def load_state():
    if os.path.exists(STATE):
        try:
            return json.load(open(STATE))
        except Exception:
            pass   # corrupt state -> re-seed fresh (atomic writes make this rare;
                   # without this the watchdog's `|| true` would silently stall it forever)
    return {"snap": {}, "pending": {}}


def save_state(st):
    tmp = STATE + ".tmp"; json.dump(st, open(tmp, "w")); os.replace(tmp, STATE)


def detect(ladders, st):
    """Compare to last snapshot; record breaks. Then overwrite snapshot."""
    new = 0
    prev = st.get("snap", {})
    cur = {}
    for eid, L in ladders.items():
        cur[eid] = {str(r["strike"]): {"mid": r["mid"], "ask": r["ask"]} for r in L["rungs"]}
        pe = prev.get(eid)
        if not pe:
            continue
        rungs = L["rungs"]
        for i in range(len(rungs) - 1):
            a, b = rungs[i], rungs[i + 1]
            pa, pb = pe.get(str(a["strike"])), pe.get(str(b["strike"]))
            if not pa or not pb:
                continue
            da, db = a["mid"] - pa["mid"], b["mid"] - pb["mid"]
            # one shocked, neighbor lagged
            shocked, lagger = None, None
            if abs(da) >= SHOCK and abs(db) < LAG:
                shocked, lagger = a, b
            elif abs(db) >= SHOCK and abs(da) < LAG:
                shocked, lagger = b, a
            if not shocked:
                continue
            # gap must exceed round-trip at the ask (need the lagger's ask)
            rt = 0.02
            if lagger["ask"] is not None and shocked["ask"] is not None:
                rt = abs(lagger["ask"] - lagger["mid"]) + abs(shocked["ask"] - shocked["mid"]) + 0.005
            move = abs(shocked["mid"] - (pa["mid"] if shocked is a else pb["mid"]))
            if move < rt:
                continue
            bid = f"{eid}:{shocked['strike']}-{lagger['strike']}:{int(time.time())}"
            st["pending"][bid] = {"t0": int(time.time()), "title": L["title"],
                                  "shocked_strike": shocked["strike"], "lagger_strike": lagger["strike"],
                                  "shocked_mid0": shocked["mid"], "lagger_mid0": lagger["mid"],
                                  "lagger_ask0": lagger["ask"], "shock_dir": (1 if (da if shocked is a else db) > 0 else -1),
                                  "rt_cost": round(rt, 4), "judged": False}
            new += 1
            print(f"  BREAK  {L['title']}  shock@${shocked['strike']} lag@${lagger['strike']}  rt={rt:.3f}")
    st["snap"] = cur
    return new


def resolve(ladders, st):
    """At +RESOLVE_H, judge who was right + at-ask net of buying the laggard."""
    done = 0
    # build current mid lookup
    curmid = {}
    for eid, L in ladders.items():
        for r in L["rungs"]:
            curmid[(eid, r["strike"])] = r["mid"]
    for bid, ev in list(st["pending"].items()):
        if ev["judged"]:
            continue
        if (time.time() - ev["t0"]) / 3600.0 < RESOLVE_H:
            continue
        eid = bid.split(":")[0]
        lag_now = curmid.get((eid, ev["lagger_strike"]))
        shock_now = curmid.get((eid, ev["shocked_strike"]))
        if lag_now is None or shock_now is None:
            ev["judged"] = True; ev["outcome"] = "vanished"
        else:
            lag_moved = lag_now - ev["lagger_mid0"]
            shock_reverted = -(shock_now - ev["shocked_mid0"]) * ev["shock_dir"]   # >0 if it snapped back
            laggard_right = abs(lag_moved) > abs(shock_now - ev["shocked_mid0"])
            # at-ask net: bought laggard at ask0, mark at current mid
            entry = ev["lagger_ask0"] if ev["lagger_ask0"] is not None else ev["lagger_mid0"] + 0.01
            atask_net = round(lag_now - entry, 4)
            ev.update({"judged": True, "lagger_now": round(lag_now, 4), "shocked_now": round(shock_now, 4),
                       "lag_moved": round(lag_moved, 4), "shock_reverted": round(shock_reverted, 4),
                       "laggard_right": laggard_right, "atask_net": atask_net,
                       "outcome": "laggard_caught_up" if laggard_right else "shock_was_right"})
        rec = dict(ev); rec["bid"] = bid
        with open(LOG, "a") as f:
            f.write(json.dumps(rec) + "\n")
        del st["pending"][bid]; done += 1
    return done


def _read_log():
    return [json.loads(l) for l in open(LOG)] if os.path.exists(LOG) else []


def summarize():
    recs = [r for r in _read_log() if r.get("outcome") not in (None, "vanished")]
    n = len(recs)
    if not n:
        return {"n": 0}
    lr = sum(1 for r in recs if r.get("laggard_right"))
    nets = [r["atask_net"] for r in recs if r.get("atask_net") is not None]
    return {"n": n, "laggard_right_pct": round(100 * lr / n, 1),
            "avg_atask_net_cents": round(100 * sum(nets) / len(nets), 2) if nets else None,
            "atask_positive_share": round(sum(1 for x in nets if x > 0) / len(nets), 2) if nets else None}


def export_obsidian(st):
    s = summarize()
    L = ["# Latticesnap Logger — reach-ladder break re-coherence",
         "",
         "> READ-ONLY logger (no trades). When a ladder rung is flow-shocked and a neighbor lags,",
         "> does the laggard catch up (edge) or the shock snap back (no edge)?",
         f"> Updated {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())} · pending={len(st['pending'])} · judged={s.get('n',0)}",
         "", "## Verdict signal", ""]
    if s.get("n"):
        L += [f"- **laggard-correct: {s['laggard_right_pct']}%** (need >60% for an edge)",
              f"- **avg at-ask net: {s['avg_atask_net_cents']}¢** (must be >0 after the real ask)",
              f"- at-ask positive share: {s['atask_positive_share']}",
              "", "**Read:** edge ⇒ laggard-correct >60% AND avg at-ask net >0. Below that, retire — "
              "the break is noise and the spread eats it. Verdict needs n≥30 breaks."]
    else:
        L += ["_No judged breaks yet. Each break needs 6h to mature; breaks themselves are rare "
              "(the dead-panel warned qualifying breaks may be ~0/week — this logger settles that for free)._"]
    L += ["", "## Pending breaks", ""]
    for ev in list(st["pending"].values())[:25]:
        age = (time.time() - ev["t0"]) / 3600
        L.append(f"- {ev['title']} · shock@${ev['shocked_strike']} lag@${ev['lagger_strike']} · {age:.1f}h · rt={ev['rt_cost']}")
    os.makedirs(os.path.dirname(VAULT), exist_ok=True)
    open(VAULT, "w").write("\n".join(L) + "\n")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "once"
    st = load_state()
    if cmd == "once":
        ladders = fetch_ladders()
        nd = resolve(ladders, st)
        nn = detect(ladders, st)
        save_state(st)
        export_obsidian(st)
        print(f"latticesnap: ladders={len(ladders)}  new_breaks={nn}  judged={nd}  pending={len(st['pending'])}")
    elif cmd == "report":
        export_obsidian(st)
        print(json.dumps(summarize(), indent=2))
        print(f"pending={len(st['pending'])}  -> {VAULT}")
    else:
        print("usage: latticesnap_logger.py once|report")


if __name__ == "__main__":
    main()
