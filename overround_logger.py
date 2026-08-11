#!/usr/bin/env python3
"""overround_logger.py — READ-ONLY logger (NOT a trading leg, places NO trades).

Question: in multi-outcome mutually-exclusive events (N-candidate races, Fed
buckets), the YES prices of all outcomes should sum to ~1 + overround. This logs
the actual sum across events to answer: (a) does any event ever sum to < 1.00
(a true Dutch-book — buy every YES, guaranteed >$1 at resolution), and (b) how
big is the typical overround (the cost of any basket strategy)?

This is the empirical backstop to relval/cohpair (killed because 2-horse sums
came out >1). A SNAPSHOT logger — the sum is observable now, no forward window.

Mechanism: gamma /events with >=3 mutually-exclusive binary markets sharing one
event; sum YES mid (and YES best-ask for the executable sum). Log per event each
day. PAPER ONLY, read-only. Usage: python3 overround_logger.py once | report
"""
import os, sys, json, time, urllib.request, urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "overround_state.json")
LOG = os.path.join(HERE, "overround_log.jsonl")
VAULT = os.path.expanduser("~/Documents/PolymarketVault/Reports/overround.md")
UA = {"User-Agent": "Mozilla/5.0"}

MIN_OUTCOMES = 3
MIN_EVENT_VOL = 100_000


def _g(url, timeout=20):
    return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout))


def scan(st):
    try:
        ev = _g("https://gamma-api.polymarket.com/events?" + urllib.parse.urlencode(
            {"closed": "false", "limit": 80, "order": "volume", "ascending": "false"}))
    except Exception:
        return 0, 0
    seen = set(st.get("seen_today", []))
    day = time.strftime("%Y-%m-%d", time.gmtime())
    if st.get("day") != day:
        seen = set(); st["day"] = day            # one sample per event per day
    new = 0; dutch = 0
    for e in ev:
        eid = str(e.get("id"))
        if eid in seen:
            continue
        mks = [m for m in (e.get("markets") or []) if not m.get("closed")]
        if len(mks) < MIN_OUTCOMES:
            continue
        # must be mutually-exclusive (negRisk) to interpret the sum as a probability basket
        if not any(m.get("negRisk") for m in mks):
            continue
        if float(e.get("volume") or 0) < MIN_EVENT_VOL:
            continue
        yes_mids, yes_asks = [], []
        for m in mks:
            op = m.get("outcomePrices"); op = json.loads(op) if isinstance(op, str) else op
            if not op:
                continue
            yes_mids.append(float(op[0]))
            ba = m.get("bestAsk")
            yes_asks.append(float(ba) if ba not in (None, "") else float(op[0]))
        if len(yes_mids) < MIN_OUTCOMES:
            continue
        smid = round(sum(yes_mids), 4); sask = round(sum(yes_asks), 4)
        # A TRUE Dutch book needs an EXHAUSTIVE set (exactly one YES resolves $1).
        # Multi-outcome (n>=3) low sums are almost always a MISSING FIELD (winner could
        # be unlisted), NOT free money — only flag exhaustive binary-pairs (n==2) at ask<1.
        real_dutch = (len(yes_mids) == 2 and sask < 1.0)
        rec = {"eid": eid, "title": (e.get("title") or "")[:60], "n_out": len(yes_mids),
               "sum_mid": smid, "sum_ask": sask, "overround_mid": round(smid - 1, 4),
               "dutch_book": real_dutch, "ts": int(time.time())}
        with open(LOG, "a") as f:
            f.write(json.dumps(rec) + "\n")
        seen.add(eid); new += 1
        if real_dutch:
            dutch += 1
            print(f"  ⚠️ REAL DUTCH-BOOK (n=2, ask-sum={sask}<1)  {rec['title']}")
        elif new <= 5:
            tag = "  (n>=3 low sum = likely missing field, not edge)" if smid < 1 else ""
            print(f"  overround {(smid-1)*100:+.1f}%  ({len(yes_mids)} out)  {rec['title']}{tag}")
    st["seen_today"] = list(seen)
    return new, dutch


def load_state():
    if os.path.exists(STATE):
        try:
            return json.load(open(STATE))
        except Exception:
            pass
    return {"seen_today": [], "day": ""}


def save_state(st):
    tmp = STATE + ".tmp"; json.dump(st, open(tmp, "w")); os.replace(tmp, STATE)


def _read_log():
    return [json.loads(l) for l in open(LOG)] if os.path.exists(LOG) else []


def summarize():
    recs = _read_log()
    n = len(recs)
    if not n:
        return {"n": 0}
    # overround is only meaningful on exhaustive sets — report binary-pairs (n==2,
    # guaranteed exhaustive, the cohpair case) separately from multi-outcome.
    pairs = [r for r in recs if r["n_out"] == 2]
    real_dutch = [r for r in recs if r.get("dutch_book")]
    pair_or = [r["sum_ask"] - 1 for r in pairs]
    return {"n_samples": n, "n_binary_pairs": len(pairs),
            "mean_pair_ask_overround_pct": round(100 * sum(pair_or) / len(pairs), 2) if pairs else None,
            "min_pair_ask_sum": round(min((r["sum_ask"] for r in pairs), default=0), 4),
            "real_dutch_books_n2_executable": len(real_dutch),
            "note": "n>=3 low sums excluded — they're missing-field, not Dutch books"}


def export_obsidian(st):
    s = summarize()
    L = ["# Overround Logger — multi-outcome basket sum (Dutch-book watch)",
         "",
         "> READ-ONLY logger (no trades). Sum of all YES prices in mutually-exclusive events.",
         "> sum<1 at the ASK = a true Dutch book (buy every YES, settle to exactly $1 > cost).",
         f"> Updated {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())} · samples={s.get('n_samples',0)}",
         ""]
    if s.get("n_samples"):
        L += [f"- samples: {s['n_samples']} events · binary-pairs (exhaustive): {s['n_binary_pairs']}",
              f"- **mean binary-pair ask-overround: {s['mean_pair_ask_overround_pct']}%** (the cohpair case — >0 = buy-pair loses)",
              f"- min binary-pair ask-sum: {s['min_pair_ask_sum']} · **real executable Dutch books (n=2, ask<1): {s['real_dutch_books_n2_executable']}**",
              "", "**Read:** binary-pair ask-sum >1 (positive overround) ⇒ relval/cohpair structurally lose, "
              "confirming the kill. A real Dutch book (n=2 ask-sum <1) would be free money — watch the count. "
              "Multi-outcome low sums are EXCLUDED (missing field, not edge)."]
    else:
        L += ["_No multi-outcome events sampled yet._"]
    os.makedirs(os.path.dirname(VAULT), exist_ok=True)
    open(VAULT, "w").write("\n".join(L) + "\n")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "once"
    st = load_state()
    if cmd == "once":
        new, dutch = scan(st); save_state(st); export_obsidian(st)
        print(f"overround: new_samples={new}  dutch_books={dutch}  total={summarize().get('n_samples',0)}")
    elif cmd == "report":
        export_obsidian(st); print(json.dumps(summarize(), indent=2))
    else:
        print("usage: overround_logger.py once|report")


if __name__ == "__main__":
    main()
