#!/usr/bin/env python3
"""deadline_logger.py — READ-ONLY logger (NOT a trading leg, places NO trades).

Question: do near-deadline LONGSHOTS systematically decay/resolve NO — i.e. is
there harvestable theta in taking NO on "X by [date]" markets still trading at
8-25c with little time left? The adversarial panel doubted enough such markets
exist. This logger measures (a) how many exist and (b) whether they resolve NO
often enough that take-NO would have paid.

Mechanism: detect open markets with endDate within the next DEADLINE_MAX_DAYS,
YES mid in the longshot band, vol>=MIN_VOL. Snapshot the YES mid; forward-track
to resolution (on-chain via ctf_resolution); record final outcome + the take-NO
P&L (you sell YES / buy NO at entry, settle at resolution).

PAPER ONLY, read-only, stdlib. Usage: python3 deadline_logger.py once | report
"""
import os, sys, json, time, urllib.request, urllib.parse
import calendar

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ctf_resolution as ctf
STATE = os.path.join(HERE, "deadline_state.json")
LOG = os.path.join(HERE, "deadline_log.jsonl")
VAULT = os.path.expanduser("~/Documents/PolymarketVault/Reports/deadline.md")
UA = {"User-Agent": "Mozilla/5.0"}

DEADLINE_MAX_DAYS = 5
YES_LO, YES_HI = 0.04, 0.30      # longshot band (still alive, not dead)
MIN_VOL = 20_000
MAX_TRACK_DAYS = 12              # give up if not resolved by then
# exclude scheduled-match/sports binaries: on those, take-NO-on-longshot == buy-favorite
# (that's favwatch's job). deadline is for genuine "X by [date]" theta where the
# catalyst window can close with no single scheduled event.
SPORTS_BLOCK = (" vs.", " vs ", "win on", "up or down", "o/u", "handicap", "to win the", "draw")


def _g(url, timeout=12):
    return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout))


def detect(st):
    now = time.time()
    lo = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
    hi = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + DEADLINE_MAX_DAYS * 86400))
    try:                                  # end_date_min/max = markets actually resolving soon
        ms = _g("https://gamma-api.polymarket.com/markets?" + urllib.parse.urlencode(
            {"closed": "false", "active": "true", "end_date_min": lo, "end_date_max": hi, "limit": 300}))
    except Exception:
        return 0
    new = 0
    for m in ms:
        cid = m.get("conditionId")
        if not cid or cid in st["pending"]:
            continue
        end = m.get("endDate") or ""
        try:
            ts = calendar.timegm(time.strptime(end[:19], "%Y-%m-%dT%H:%M:%S"))
        except Exception:
            continue
        if not (0 < (ts - now) < DEADLINE_MAX_DAYS * 86400):
            continue
        if float(m.get("volume") or 0) < MIN_VOL:
            continue
        if any(b in (m.get("question") or "").lower() for b in SPORTS_BLOCK):
            continue                              # scheduled match -> favwatch's domain, not theta
        op = m.get("outcomePrices"); op = json.loads(op) if isinstance(op, str) else op
        if not op:
            continue
        yes = float(op[0])
        if not (YES_LO <= yes <= YES_HI):
            continue
        st["pending"][cid] = {"t0": int(time.time()), "end_ts": int(ts), "yes0": yes,
                              "title": m.get("question")}
        new += 1
        print(f"  DEADLINE-LONGSHOT  YES@{yes:.3f}  ends {end[:10]}  {(m.get('question') or '')[:44]}")
    return new


def resolve(st):
    done = 0
    for cid, ev in list(st["pending"].items()):
        age_d = (time.time() - ev["t0"]) / 86400
        # only check on-chain once we're past the market's end (saves RPC calls)
        if time.time() < ev["end_ts"] - 3600 and age_d < MAX_TRACK_DAYS:
            continue
        try:
            win = ctf.winning_outcome(cid)
        except Exception:
            win = None
        if win is None and age_d < MAX_TRACK_DAYS:
            continue                                  # not resolved yet, keep waiting
        rec = dict(ev); rec["cid"] = cid; rec["win_idx"] = win
        if win in (0, 1):
            yes_won = (win == 0)
            # take-NO entry: buy NO at (1 - yes0); settles to $1 if YES loses, $0 if YES wins
            rec["takeno_pnl"] = round((0.0 if yes_won else 1.0) - (1.0 - ev["yes0"]), 4)
            rec["outcome"] = "YES_won" if yes_won else "YES_lost(NO_paid)"
        else:
            rec["outcome"] = "unresolved_timeout"; rec["takeno_pnl"] = None
        with open(LOG, "a") as f:
            f.write(json.dumps(rec) + "\n")
        del st["pending"][cid]; done += 1
    return done


def load_state():
    if os.path.exists(STATE):
        try:
            return json.load(open(STATE))
        except Exception:
            pass
    return {"pending": {}}


def save_state(st):
    tmp = STATE + ".tmp"; json.dump(st, open(tmp, "w")); os.replace(tmp, STATE)


def _read_log():
    return [json.loads(l) for l in open(LOG)] if os.path.exists(LOG) else []


def summarize():
    recs = [r for r in _read_log() if r.get("takeno_pnl") is not None]
    n = len(recs)
    if not n:
        return {"n": 0}
    no_paid = sum(1 for r in recs if "NO_paid" in r["outcome"])
    pnl = [r["takeno_pnl"] for r in recs]
    return {"n": n, "no_resolved_pct": round(100 * no_paid / n, 1),
            "mean_takeno_pnl_c": round(100 * sum(pnl) / n, 2),
            "win_share": round(sum(1 for p in pnl if p > 0) / n, 2)}


def export_obsidian(st):
    s = summarize()
    L = ["# Deadline Logger — near-deadline longshot theta (take-NO)",
         "",
         "> READ-ONLY logger (no trades). Do longshots near their deadline resolve NO often",
         "> enough that take-NO pays? Take-NO P&L = settle − (1 − YES0).",
         f"> Updated {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())} · pending={len(st['pending'])} · resolved={s.get('n',0)}",
         ""]
    if s.get("n"):
        L += [f"- **NO-resolved: {s['no_resolved_pct']}%** of tracked longshots",
              f"- **mean take-NO P&L: {s['mean_takeno_pnl_c']}¢/exit** · win share {s['win_share']}",
              "", "**Read:** edge ⇒ take-NO P&L >0 net of spread across n≥30. Beware: this is the "
              "favorite-longshot bias on BINARY markets — if any resolve YES near $0 you eat gap-through, "
              "so the mean must survive those tails, not just the win rate."]
    else:
        L += ["_No resolved near-deadline longshots yet. If few ever qualify, the panel was right "
              "that the population is too thin to matter._"]
    L += ["", "## Pending", ""]
    for ev in list(st["pending"].values())[:20]:
        L.append(f"- YES@{ev['yes0']:.3f} · {(ev['title'] or '')[:50]}")
    os.makedirs(os.path.dirname(VAULT), exist_ok=True)
    open(VAULT, "w").write("\n".join(L) + "\n")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "once"
    st = load_state()
    if cmd == "once":
        nd = resolve(st)
        nn = detect(st)
        save_state(st)
        export_obsidian(st)
        print(f"deadline: new_longshots={nn}  resolved={nd}  pending={len(st['pending'])}")
    elif cmd == "report":
        export_obsidian(st)
        print(json.dumps(summarize(), indent=2))
    else:
        print("usage: deadline_logger.py once|report")


if __name__ == "__main__":
    main()
