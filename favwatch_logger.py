#!/usr/bin/env python3
"""favwatch_logger.py — READ-ONLY logger (NOT a trading leg, places NO trades).

Two questions in one, on HEAVY FAVORITES (0.85-0.97) near resolution:
  (a) DRIFT: in the final days, does the favorite drift UP toward $1 (under-
      priced favorite / favorite-longshot bias) or FADE? (buy-favorite signal)
  (b) GAP: at resolution, when the favorite LOSES, how far does it crater?
      This quantifies the gap-through that killed nearres — turning the project's
      #1 lesson from a story into a measured loss distribution.

Mechanism: detect binary markets with a favorite in [0.85,0.97], endDate within
FAV_MAX_DAYS, vol>=MIN_VOL. Snapshot favorite mid; forward-record drift at
+12/+24h; at resolution (on-chain via ctf_resolution) record won/lost and the
realized settle. A leg is viable ONLY if mean favorite P&L (buy at entry, settle
0/1) is >0 AFTER the loss tails — i.e. the win rate must beat the entry price by
enough to cover the gaps. PAPER ONLY, read-only.
Usage: python3 favwatch_logger.py once | report
"""
import os, sys, json, time, urllib.request, urllib.parse
import calendar

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ctf_resolution as ctf
STATE = os.path.join(HERE, "favwatch_state.json")
LOG = os.path.join(HERE, "favwatch_log.jsonl")
VAULT = os.path.expanduser("~/Documents/PolymarketVault/Reports/favwatch.md")
UA = {"User-Agent": "Mozilla/5.0"}

FAV_LO, FAV_HI = 0.85, 0.97
FAV_MAX_DAYS = 4
MIN_VOL = 50_000
MAX_TRACK_DAYS = 10
WINDOWS_H = [12, 24]


def _g(url, timeout=12):
    return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout))


def book_ask_spread(token_id):
    """Live best ASK + spread from the CLOB book — the price you'd actually PAY to
    buy the favorite. Spread-honesty is the whole point (it's what killed nearres).
    Live + universal + no rate-limit (better than oddpool here, which is recent-only/throttled)."""
    try:
        bk = _g(f"https://clob.polymarket.com/book?token_id={token_id}")
        bids, asks = bk.get("bids") or [], bk.get("asks") or []
        ba = float(asks[0]["price"]) if asks else None     # asks asc -> [0] best
        bb = float(bids[-1]["price"]) if bids else None
        sp = round(ba - bb, 4) if (ba is not None and bb is not None) else None
        return ba, sp
    except Exception:
        return None, None


def detect(st):
    now = time.time()
    lo = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
    hi = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + FAV_MAX_DAYS * 86400))
    try:                                  # end_date_min/max = the markets actually resolving soon
        ms = _g("https://gamma-api.polymarket.com/markets?" + urllib.parse.urlencode(
            {"closed": "false", "active": "true", "end_date_min": lo, "end_date_max": hi, "limit": 300}))
    except Exception:
        return 0
    new = 0
    for m in ms:
        cid = m.get("conditionId")
        if not cid or cid in st["pending"]:
            continue
        try:
            ts = calendar.timegm(time.strptime((m.get("endDate") or "")[:19], "%Y-%m-%dT%H:%M:%S"))
        except Exception:
            continue
        if not (0 < (ts - now) < FAV_MAX_DAYS * 86400) or float(m.get("volume") or 0) < MIN_VOL:
            continue
        op = m.get("outcomePrices"); op = json.loads(op) if isinstance(op, str) else op
        if not op or len(op) != 2:
            continue
        fav_idx = 0 if float(op[0]) >= float(op[1]) else 1
        fav = float(op[fav_idx])
        if not (FAV_LO <= fav <= FAV_HI):
            continue
        # spread-honest entry: the real ASK you'd pay for the favorite token, now.
        toks = m.get("clobTokenIds"); toks = json.loads(toks) if isinstance(toks, str) else toks
        fav_ask, fav_spread = (book_ask_spread(str(toks[fav_idx])) if toks and len(toks) == 2 else (None, None))
        st["pending"][cid] = {"t0": int(time.time()), "end_ts": int(ts), "fav_idx": fav_idx,
                              "fav0": fav, "fav_ask": fav_ask, "fav_spread": fav_spread,
                              "title": m.get("question"), "marks": {}}
        new += 1
        ask_s = f" ask={fav_ask:.3f}" if fav_ask is not None else ""
        print(f"  FAVORITE  mid={fav:.3f}{ask_s}  ends {(m.get('endDate') or '')[:10]}  {(m.get('question') or '')[:38]}")
    return new


def resolve(st):
    done = 0
    for cid, ev in list(st["pending"].items()):
        age_d = (time.time() - ev["t0"]) / 86400
        age_h = (time.time() - ev["t0"]) / 3600
        # drift marks pre-resolution
        due = [w for w in WINDOWS_H if w <= age_h and str(w) not in ev["marks"]]
        if due and time.time() < ev["end_ts"]:
            try:
                mk = _g("https://gamma-api.polymarket.com/markets?" + urllib.parse.urlencode({"condition_ids": cid}))
                op = mk[0].get("outcomePrices") if mk else None
                op = json.loads(op) if isinstance(op, str) else op
                if op:
                    for w in due:
                        ev["marks"][str(w)] = round(float(op[ev["fav_idx"]]), 4)
            except Exception:
                pass
        if time.time() < ev["end_ts"] - 3600 and age_d < MAX_TRACK_DAYS:
            continue
        try:
            win = ctf.winning_outcome(cid)
        except Exception:
            win = None
        if win is None and age_d < MAX_TRACK_DAYS:
            continue
        rec = dict(ev); rec["cid"] = cid; rec["win_idx"] = win
        if win in (0, 1):
            fav_won = (win == ev["fav_idx"])
            rec["fav_won"] = fav_won
            settle = 1.0 if fav_won else 0.0
            entry = ev.get("fav_ask") if ev.get("fav_ask") is not None else ev["fav0"]
            rec["entry_used"] = round(entry, 4)
            rec["fav_pnl"] = round(settle - entry, 4)                # spread-honest (real ask) buy-favorite P&L
            rec["fav_pnl_mid"] = round(settle - ev["fav0"], 4)       # mid-based, for comparison (optimistic)
            rec["outcome"] = "fav_won" if fav_won else "fav_LOST(gap)"
        else:
            rec["outcome"] = "unresolved_timeout"; rec["fav_pnl"] = None
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
    recs = [r for r in _read_log() if r.get("fav_pnl") is not None]
    n = len(recs)
    if not n:
        return {"n": 0}
    won = sum(1 for r in recs if r.get("fav_won"))
    pnl = [r["fav_pnl"] for r in recs]
    pnl_mid = [r.get("fav_pnl_mid", r["fav_pnl"]) for r in recs]
    losses = [r["fav_pnl"] for r in recs if not r.get("fav_won")]
    mean_entry = sum(r["fav0"] for r in recs) / n
    spreads = [r["fav_spread"] for r in recs if r.get("fav_spread") is not None]
    return {"n": n, "fav_win_pct": round(100 * won / n, 1), "mean_entry": round(mean_entry, 3),
            "mean_fav_pnl_c": round(100 * sum(pnl) / n, 2),            # spread-honest (real ask)
            "mean_fav_pnl_MID_c": round(100 * sum(pnl_mid) / n, 2),    # mid-based (optimistic)
            "mean_spread_paid_c": round(100 * sum(spreads) / len(spreads), 2) if spreads else None,
            "mean_loss_gap_c": round(100 * sum(losses) / len(losses), 2) if losses else None,
            "calibration": f"won {round(100*won/n,1)}% vs priced {round(100*mean_entry,1)}%"}


def export_obsidian(st):
    s = summarize()
    L = ["# Favwatch Logger — heavy-favorite drift + resolution gap",
         "",
         "> READ-ONLY logger (no trades). Do favorites (0.85-0.97) near resolution pay when bought,",
         "> AFTER the gap-through losses? Buy-favorite P&L = settle(0/1) − entry.",
         f"> Updated {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())} · pending={len(st['pending'])} · resolved={s.get('n',0)}",
         ""]
    if s.get("n"):
        L += [f"- **mean buy-favorite P&L (spread-honest, real ask): {s['mean_fav_pnl_c']}¢/exit** (THE number)",
              f"  - vs mid-based (optimistic): {s.get('mean_fav_pnl_MID_c')}¢ · spread paid: {s.get('mean_spread_paid_c')}¢",
              f"- calibration: {s['calibration']}",
              f"- **mean loss when favorite craters: {s['mean_loss_gap_c']}¢** (the gap-through tail)",
              "", "**Read:** favorites are well-calibrated, so mean P&L should sit near 0 minus spread. "
              "If win% materially beats the priced %, there's underpricing — but the loss-gap tail must "
              "not eat it (this is exactly where nearres died). Needs n≥30."]
    else:
        L += ["_No resolved favorites yet. Accumulating drift marks + awaiting resolution._"]
    L += ["", "## Pending favorites", ""]
    for ev in list(st["pending"].values())[:20]:
        L.append(f"- {ev['fav0']:.3f} · {(ev['title'] or '')[:52]}")
    os.makedirs(os.path.dirname(VAULT), exist_ok=True)
    open(VAULT, "w").write("\n".join(L) + "\n")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "once"
    st = load_state()
    if cmd == "once":
        nd = resolve(st); nn = detect(st); save_state(st); export_obsidian(st)
        print(f"favwatch: new_favorites={nn}  resolved={nd}  pending={len(st['pending'])}")
    elif cmd == "report":
        export_obsidian(st); print(json.dumps(summarize(), indent=2))
    else:
        print("usage: favwatch_logger.py once|report")


if __name__ == "__main__":
    main()
