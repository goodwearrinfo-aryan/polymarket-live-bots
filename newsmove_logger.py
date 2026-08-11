#!/usr/bin/env python3
"""newsmove_logger.py — READ-ONLY logger (NOT a trading leg, places NO trades).

Question: when a NEWS headline fires on a market's topic, does that market then
MOVE — and by enough, fast enough, that the headline-to-price lag is tradeable?
This is the information-latency edge (un-falsified category). Unblocked now that
hermes (:48580) is healthy.

Mechanism (logging only):
  - pull fresh hermes headlines (first_seen in the last LOOKBACK) + active high-vol markets.
  - a market is "news-hit" if a fresh headline shares >=2 salient words with its title.
  - snapshot the hit market's mid; forward-record |move| at +15m/+1h/+4h.
  - BASELINE control: random active markets with NO news hit — they move anyway, so
    news carries info only if news-hit |move| clearly EXCEEDS baseline |move|.

PAPER ONLY, read-only. Usage: python3 newsmove_logger.py once | report
"""
import os, sys, json, time, re, calendar, urllib.request, urllib.parse, random

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "newsmove_state.json")
LOG = os.path.join(HERE, "newsmove_log.jsonl")
VAULT = os.path.expanduser("~/Documents/PolymarketVault/Reports/newsmove.md")
HERMES = "http://127.0.0.1:48580/api/news"
UA = {"User-Agent": "Mozilla/5.0"}

LOOKBACK_H = 1            # only headlines this fresh count as a trigger
MIN_SHARED = 2           # salient words a headline must share with a market title
MIN_VOL = 50_000
WINDOWS_M = [15, 60, 240]
BASELINE_N = 8           # random no-news markets tracked per cycle as control
STOP = set("the a an of to in on for and or by with will be is are at as from this that "
           "win wins won game match vs market price 2026 june july your what which when who "
           "next end above below between have has".split())


def _g(url, timeout=12):
    return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout))


def salient(text):
    return {w for w in re.findall(r"[a-z]+", (text or "").lower()) if len(w) >= 5 and w not in STOP}


def fresh_headlines():
    try:
        d = _g(HERMES, timeout=6)
        items = d.get("items", []) if isinstance(d, dict) else []
    except Exception:
        return []
    cut = time.time() - LOOKBACK_H * 3600
    out = []
    for it in items:
        ts = it.get("first_seen_utc") or ""
        try:
            t = calendar.timegm(time.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S"))   # UTC, not local
        except Exception:
            continue
        if t >= cut:
            out.append((it.get("title", ""), salient(it.get("title", ""))))
    return out


def active_markets():
    try:
        return _g("https://gamma-api.polymarket.com/markets?" + urllib.parse.urlencode(
            {"closed": "false", "active": "true", "limit": 250, "order": "volume", "ascending": "false"}))
    except Exception:
        return []


def mid_of(mk):
    op = mk.get("outcomePrices"); op = json.loads(op) if isinstance(op, str) else op
    return float(op[0]) if op else None


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
    heads = fresh_headlines()
    mkts = active_markets()
    if not mkts:
        return 0, len(heads)
    new = 0
    liquid = [m for m in mkts if float(m.get("volume") or 0) >= MIN_VOL and m.get("conditionId")]
    hit_cids = set()
    for m in liquid:
        cid = m.get("conditionId")
        if cid in st["pending"]:
            continue
        mt = salient(m.get("question"))
        if not mt:
            continue
        for title, hs in heads:
            if len(mt & hs) >= MIN_SHARED:
                mid = mid_of(m)
                if mid is None:
                    continue
                st["pending"][cid] = {"t0": int(time.time()), "mid0": round(mid, 4), "kind": "news",
                                      "title": m.get("question"), "headline": title[:60], "marks": {}}
                hit_cids.add(cid); new += 1
                print(f"  NEWS-HIT  mid={mid:.3f}  '{(m.get('question') or '')[:34]}' <- '{title[:34]}'")
                break
    # baseline control: random liquid markets with NO news hit
    pool = [m for m in liquid if m.get("conditionId") not in hit_cids and m.get("conditionId") not in st["pending"]]
    for m in random.sample(pool, min(BASELINE_N, len(pool))):
        cid = m.get("conditionId"); mid = mid_of(m)
        if mid is None:
            continue
        st["pending"][cid] = {"t0": int(time.time()), "mid0": round(mid, 4), "kind": "baseline",
                              "title": m.get("question"), "marks": {}}
    return new, len(heads)


def revisit(st):
    done = 0
    mkts = {m.get("conditionId"): m for m in active_markets()}
    for cid, ev in list(st["pending"].items()):
        age_m = (time.time() - ev["t0"]) / 60.0
        due = [w for w in WINDOWS_M if w <= age_m and str(w) not in ev["marks"]]
        if due and cid in mkts:
            mid = mid_of(mkts[cid])
            if mid is not None:
                for w in due:
                    ev["marks"][str(w)] = round(mid, 4)
        if age_m >= WINDOWS_M[-1]:
            with open(LOG, "a") as f:
                f.write(json.dumps({**ev, "cid": cid}) + "\n")
            del st["pending"][cid]; done += 1
    return done


def _read_log():
    return [json.loads(l) for l in open(LOG)] if os.path.exists(LOG) else []


def summarize():
    recs = _read_log()
    if not recs:
        return {"n": 0}
    out = {"n": len(recs)}
    for kind in ("news", "baseline"):
        sub = [r for r in recs if r.get("kind") == kind]
        for w in WINDOWS_M:
            moves = [abs(r["marks"][str(w)] - r["mid0"]) for r in sub if r["marks"].get(str(w)) is not None]
            out[f"{kind}_move_{w}m_c"] = round(100 * sum(moves) / len(moves), 2) if moves else None
        out[f"{kind}_n"] = len(sub)
    return out


def export_obsidian(st):
    s = summarize()
    L = ["# Newsmove Logger — does a headline move its market (info latency)?",
         "",
         "> READ-ONLY (no trades). News-hit market |move| vs a BASELINE of no-news markets.",
         "> News carries tradeable info only if news-hit move clearly EXCEEDS baseline.",
         f"> Updated {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())} · pending={len(st['pending'])} · matured={s.get('n',0)}",
         ""]
    if s.get("n"):
        L += ["| window | NEWS-hit |move| | BASELINE |move| |", "|---|---|---|"]
        for w in WINDOWS_M:
            nv = s.get(f"news_move_{w}m_c"); bv = s.get(f"baseline_move_{w}m_c")
            L.append(f"| +{w}m | {'' if nv is None else f'{nv:.2f}¢'} | {'' if bv is None else f'{bv:.2f}¢'} |")
        L += ["", f"(news n={s.get('news_n',0)}, baseline n={s.get('baseline_n',0)})",
              "**Read:** edge ⇒ news |move| ≫ baseline AND the move persists past +15m (so a 60s "
              "loop can still catch it). If news ≈ baseline, the headline was already priced."]
    else:
        L += ["_No matured news/baseline pairs yet (each needs 4h). Accumulating._"]
    os.makedirs(os.path.dirname(VAULT), exist_ok=True)
    open(VAULT, "w").write("\n".join(L) + "\n")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "once"
    st = load_state()
    if cmd == "once":
        nd = revisit(st); nn, nh = detect(st); save_state(st); export_obsidian(st)
        print(f"newsmove: fresh_headlines={nh}  news_hits={nn}  matured={nd}  pending={len(st['pending'])}")
    elif cmd == "report":
        export_obsidian(st); print(json.dumps(summarize(), indent=2))
    else:
        print("usage: newsmove_logger.py once|report")


if __name__ == "__main__":
    main()
