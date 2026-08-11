#!/usr/bin/env python3
"""setlock_logger.py — READ-ONLY complete-set arb logger (books NOTHING, paper, keyless).

WHY THIS LEG: the ONLY edge family that has ever paid on this venue is NON-PREDICTIVE
structural arb. basket_arb already screens negRisk complete-set locks but has a DOCUMENTED
exhaustiveness blind spot (Σ over the RETURNED legs is necessary-not-sufficient — a partial
field fakes a lock; made the locks book ~87% fiction on 2026-06-20). This logger is the
independent, CORRECT cross-check, with three hard lessons baked in:

  (1) EXHAUSTIVENESS — pull the FULL outcome set per negRisk EVENT (gamma /events, nested
      markets), so a closed/missing leg can NEVER masquerade as a lock.
  (2) EXECUTABLE-AT-SIZE — a Σmid<1 is a mirage, and so is a Σ over top-of-book asks
      (validated 2026-06-22: an OpenAI-IPO set showed +2.5% at top-of-book that collapsed to
      breakeven by $25). So we WALK the CLOB ask ladder and report net edge AT CAPACITY — the
      most capital that holds at >=1c net — not the top-of-book fantasy. (The nearres lesson.)
  (3) RESOLUTION-CONSISTENCY — flag legs whose endDates differ (must resolve as one unit).

PAYOUT PREMISE (validated 2026-06-22 on 7 resolved negRisk sets): a complete set pays exactly
$1.00 to the one winner, so net locked edge = 1 - Σ(ask) held to resolution, ZERO fee. A
logged LOCK is a LEAD, not a trade — hand to edge-verifier for final depth/sizing. Books nothing.

Usage: python3 setlock_logger.py once | report
"""
import os, sys, json, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "setlock_log.jsonl")
VAULT = os.path.expanduser("~/Documents/PolymarketVault/Reports/setlock.md")
UA = {"User-Agent": "Mozilla/5.0"}
GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
MIN_MEMBERS = 3        # a real complete set has >=3 mutually-exclusive outcomes
MID_PREFILTER = 1.02   # only CLOB-confirm events whose Σmid is plausibly lockable
EDGE_FLOOR = 0.01      # a lock must net >=1c per $1
MIN_CAPITAL = 20.0     # ...and hold >=$20 at that edge, else it's a top-of-book mirage
SHARE_STEPS = (1, 2, 5, 10, 20, 40, 80, 160, 320, 640)
MAX_CONFIRM = 40       # cap CLOB confirmations per cycle (politeness)


def _get(base, path):
    try:
        with urllib.request.urlopen(urllib.request.Request(base + path, headers=UA), timeout=25) as r:
            return json.loads(r.read())
    except Exception as e:
        sys.stderr.write(f"[setlock] {base}{path[:40]} -> {e}\n")
        return None


def _mid(m):
    op = m.get("outcomePrices")
    if isinstance(op, str):
        try: op = json.loads(op)
        except Exception: op = None
    if isinstance(op, list) and op:
        try: return float(op[0])
        except Exception: pass
    return None


def _yes_token(m):
    t = m.get("clobTokenIds")
    if isinstance(t, str):
        try: t = json.loads(t)
        except Exception: t = None
    return t[0] if isinstance(t, list) and t else None


def _clob_asks(token):
    """Full ask ladder, ascending price (best ask first). None if no book."""
    book = _get(CLOB, f"/book?token_id={token}")
    if not isinstance(book, dict):
        return None
    a = [(float(x["price"]), float(x["size"])) for x in (book.get("asks") or [])
         if x.get("price") not in (None, "") and float(x.get("size", 0)) > 0]
    return sorted(a) if a else None


def _fill_cost(ladder, n_shares):
    """Cost to buy n_shares walking up the ladder; None if depth runs out."""
    rem, cost = n_shares, 0.0
    for p, s in ladder:
        take = min(rem, s); cost += take * p; rem -= take
        if rem <= 1e-9:
            return cost
    return None


def _lock_depth(ladders):
    """Walk equal share-units across ALL legs.
    Returns (top_net, cap_net, cap_usd): net edge at top-of-book (1 share), and the net edge
    at the MAX capital that still clears EDGE_FLOOR. A complete set buys N shares of every leg;
    exactly one pays $N, so per-unit net = 1 - Σcost/N."""
    top_net = None
    cap_net, cap_usd = None, 0.0
    for N in SHARE_STEPS:
        costs = [_fill_cost(l, N) for l in ladders]
        if any(c is None for c in costs):      # a leg ran out of depth at this size
            break
        net = 1.0 - sum(costs) / N
        if N == 1:
            top_net = net
        if net >= EDGE_FLOOR:
            cap_net, cap_usd = net, sum(costs)
        else:
            break
    return top_net, cap_net, cap_usd


def scan():
    events = _get(GAMMA, "/events?closed=false&active=true&limit=500") or []
    cands = []
    for ev in events:
        if not isinstance(ev, dict) or not ev.get("negRisk"):
            continue
        mkts = [m for m in (ev.get("markets") or []) if isinstance(m, dict) and not m.get("closed")]
        if len(mkts) < MIN_MEMBERS:
            continue
        mids = [_mid(m) for m in mkts]
        if any(x is None for x in mids):       # incomplete field at the mid stage -> no lock claim
            continue
        smid = sum(mids)
        if smid <= MID_PREFILTER:
            cands.append((ev, mkts, round(smid, 4)))
    cands.sort(key=lambda c: c[2])

    locks, rejects = [], []
    confirmed = 0
    for ev, mkts, smid in cands:
        if confirmed >= MAX_CONFIRM:
            break
        confirmed += 1
        ladders, priced = [], 0
        for m in mkts:
            tok = _yes_token(m)
            lad = _clob_asks(tok) if tok else None
            if lad:
                ladders.append(lad); priced += 1
            else:
                ladders.append([])
        slug = ev.get("slug") or ev.get("title")
        if priced != len(mkts):                # partial field on the book -> the fake-lock failure mode
            rejects.append({"event": slug, "members": len(mkts), "sum_mid": smid,
                            "why": f"PARTIAL-FIELD ({priced}/{len(mkts)} on CLOB)", "ts": int(time.time())})
            continue
        top_net, cap_net, cap_usd = _lock_depth(ladders)
        if top_net is None:
            continue
        enddates = len({m.get("endDate") for m in mkts})
        rec = {"event": slug, "members": len(mkts), "sum_mid": smid,
               "top_net": round(top_net, 4),
               "cap_net": round(cap_net, 4) if cap_net is not None else None,
               "cap_usd": round(cap_usd, 2), "end_dates": enddates, "ts": int(time.time())}
        if cap_net is not None and cap_net >= EDGE_FLOOR and cap_usd >= MIN_CAPITAL:
            if enddates > 1:
                rec["flag"] = f"⚠️ {enddates} distinct endDates — confirm legs resolve as one unit"
            locks.append(rec)
        elif top_net >= EDGE_FLOOR:            # looked lockable on top-of-book, dies on depth
            rec["why"] = (f"TOP-OF-BOOK MIRAGE — +{top_net*100:.2f}% at top, only "
                          f"${cap_usd:.0f} holds ≥1c (need ≥${MIN_CAPITAL:.0f})")
            rejects.append(rec)
    return len(cands), confirmed, locks, rejects


def report(ncand, nconf, locks, rejects):
    with open(LOG, "a") as f:
        for r in locks:
            f.write(json.dumps({**r, "kind": "LOCK"}) + "\n")
    L = ["# Set-Lock logger — complete-set arb (exhaustiveness + DEPTH-aware, CLOB-confirmed)",
         "",
         "> READ-ONLY, books nothing. Full negRisk field + real CLOB ask-LADDER walk (top-of-book is a mirage).",
         f"> Updated {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())} · "
         f"mid-candidates={ncand} · CLOB-confirmed={nconf} · real locks={len(locks)} · rejected={len(rejects)}",
         "",
         f"## Real locks — hold ≥${MIN_CAPITAL:.0f} at ≥1c net on the executable ladder (LEADS — verify before sizing)",
         ""]
    if locks:
        L += ["| event | legs | top-of-book | net@capacity | capacity $ | endDates |",
              "|---|--:|--:|--:|--:|--:|"]
        for r in sorted(locks, key=lambda x: -x["cap_usd"]):
            fl = f"<br>{r['flag']}" if r.get("flag") else ""
            L.append(f"| {r['event']}{fl} | {r['members']} | +{r['top_net']} | **+{r['cap_net']}** | "
                     f"${r['cap_usd']:.0f} | {r['end_dates']} |")
    else:
        L += ["_none this cycle — the honest steady state (a top-of-book Σ<1 that won't hold size is NOT a lock)._"]
    L += ["", "## Rejected — looked lockable on mids/top-of-book but isn't (the failure modes this leg exists to catch)", ""]
    if rejects:
        L += ["| event | legs | why |", "|---|--:|---|"]
        for r in sorted(rejects, key=lambda x: x.get("event") or "")[:20]:
            L.append(f"| {r['event']} | {r.get('members','?')} | {r.get('why','')} |")
    else:
        L += ["_none_"]
    os.makedirs(os.path.dirname(VAULT), exist_ok=True)
    open(VAULT, "w").write("\n".join(L) + "\n")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "once"
    if cmd not in ("once", "report"):
        print("usage: setlock_logger.py once|report"); return
    ncand, nconf, locks, rejects = scan()
    report(ncand, nconf, locks, rejects)
    print(f"setlock: mid_candidates={ncand} clob_confirmed={nconf} "
          f"real_locks={len(locks)} rejected={len(rejects)} -> {LOG}")
    for r in locks[:5]:
        print(f"  LOCK {r['event']} +{r['cap_net']*100:.2f}% @ ${r['cap_usd']:.0f} cap "
              f"(top-of-book +{r['top_net']*100:.2f}%)")
    for r in rejects[:4]:
        print(f"  REJECT {r['event']}: {r.get('why','')}")


if __name__ == "__main__":
    main()
