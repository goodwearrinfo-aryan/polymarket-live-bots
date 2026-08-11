#!/usr/bin/env python3
"""monoarb.py — MONOTONICITY / CONSISTENCY ARB leg (non-predictive, +EV by construction).

The idea: for ordered markets on the SAME event, prices MUST be monotonic.
  • "reach $X by D" (up): higher X cannot be MORE likely than lower X.   [cross-STRIKE]
  • "dip to $X by D" (down): lower X cannot be MORE likely than higher X. [cross-STRIKE]
  • cross-DATE (added 2026-06-18): for the SAME (asset, dir, threshold), a cumulative
    "touch by D" market with a LATER deadline is a time-superset of an earlier one, so its
    YES must be >= the earlier deadline's. Earlier priced ABOVE later = a date-fragmentation
    lock. This is a DISTINCT failure mode from the cross-strike overround (time fragmentation
    vs strike fragmentation), so it diversifies the track's drivers. Guarded to cumulative
    reach/dip/hit/touch markets only — point-in-time "above $X ON D" closes are NOT date-monotone.
  • duplicate markets (same threshold+deadline) must price equally.
When the book violates this (a harder outcome priced ABOVE an easier one), it's a LOGICAL
LOCK — buy the underpriced (easier) YES + buy NO on the overpriced (harder) one:
  cost = cheap_yes + (1 - exp_yes);  payoff ∈ {1, 2} (>=1 always).
  If the violation gap g = exp_yes - cheap_yes > 0, cost = 1 - g < 1 <= payoff → locked >= g.
Immune to the 3 killers: no view (logic only), no stop (hold to resolution), settles 0/1.

REALITY (verified 2026-06-17): the liquid BTC/ETH ladders are already monotonic; the only
live violations were ~1-2c < the 2% Polymarket fee = NOT capturable. So this leg is
DORMANT-RARE by design (like basket_arb / dataarb) — it ONLY books a lock whose gap clears
fee+slippage, which happens on thin/less-watched pairs. 0 locks is the EXPECTED steady state,
not a bug. It can never lose by construction; it just waits for a real free lunch.

GATE (same bar as every leg): n>=30 locks AND bootstrap CI>0 AND beats control AND control<=0.
Reads the freshest analyst_data_gate scan for live prices; settles via the proven CLOB path.

Run:  python3 monoarb.py            # scan + settle + report
      python3 monoarb.py once       # + Vault/Reports/monoarb.md (watchdog mode)
"""
import os, sys, json, time, collections, re

BASE = os.path.dirname(os.path.abspath(__file__))
GATE_LOG = os.path.join(BASE, "analyst_data_gate_log.jsonl")
STATE = os.path.join(BASE, "monoarb_state.json")
VAULT = os.path.expanduser("~/Documents/PolymarketVault/Reports/monoarb.md")

FEE = 0.02            # Polymarket resolution fee (2% of winnings)
SLIP = 0.01           # spread/slippage buffer per leg
MIN_NET = FEE + SLIP  # a violation gap must exceed this to be a capturable lock (~3c)
SIZE = 1.0            # paper notional per lock leg
CONTROL_TARGET = 30


def _load():
    try:
        return json.load(open(STATE))
    except Exception:
        return {"open": [], "resolved": [], "last_scan": None}


def _save(st):
    tmp = STATE + ".tmp"
    json.dump(st, open(tmp, "w"), indent=2)
    os.replace(tmp, STATE)


def _latest_rows():
    """Freshest crypto-threshold rows from the data-gate scan: q, yes, cid, asset/dir/thr, end."""
    last = None
    try:
        for line in open(GATE_LOG):
            if line.strip():
                last = line
    except FileNotFoundError:
        return []
    if not last:
        return []
    out = []
    for r in json.loads(last).get("rows", []):
        if r.get("source") != "crypto-threshold":
            continue
        p = r.get("params", {})
        if None in (p.get("asset"), p.get("direction"), p.get("threshold"), r.get("yes"), r.get("cid")):
            continue
        out.append({"cid": str(r["cid"]), "q": (r.get("q") or "")[:60], "yes": float(r["yes"]),
                    "asset": p["asset"], "dir": p["direction"], "thr": float(p["threshold"]),
                    "end": r.get("end", "?")})
    return out


def _cumulative(q):
    """Cross-DATE monotonicity holds only for cumulative 'touch by D' markets (reach/dip/hit/
    touch) — NOT point-in-time 'above $X ON D' closes, which can fall as well as rise across
    dates. Guard so we never book a FALSE date-lock (manufacturing an edge that isn't there)."""
    ql = (q or "").lower()
    return any(w in ql for w in ("reach", "dip", "hit", "touch"))


_MONTHS = {"january", "february", "march", "april", "may", "june", "july", "august",
           "september", "october", "november", "december", "jan", "feb", "mar", "apr",
           "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec"}
_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}")


def _norm_stem(q):
    """Structural stem of a question with ALL date/number/threshold tokens stripped, so two
    markets that differ ONLY in their deadline compare EQUAL. This is the resolution-source
    guard for the cross-date pass: it rejects pairs that share (asset,dir,thr) but resolve on
    different conditions — e.g. 'reach $1m by Dec 31 2026' vs 'hit $1m before GTA VI' (an
    event-relative deadline, not a calendar one). Stems: 'will bitcoin reach' vs 'will bitcoin
    hit gta vi' → unequal → not paired. The #1 prediction-market killer, defended in code."""
    ql = re.sub(r"[\$,\?\.\!]", " ", (q or "").lower())
    ql = re.sub(r"\b\d[\d,]*\.?\d*\s*[kmb]?\b", " ", ql)          # $1,000,000 / 120k / 1m / 2026 / 31
    toks = [t for t in ql.split()
            if t and t not in _MONTHS and not t.isdigit()
            and t not in {"by", "before", "on", "the", "of", "at", "st", "nd", "rd", "th"}]
    return " ".join(toks)


def find_locks(rows):
    """Return capturable monotonicity locks (gap > MIN_NET). Each: cheap leg (buy YES) +
    expensive leg (buy NO), with the locked gap. Two independent failure modes: cross-STRIKE
    (overround within one deadline) and cross-DATE (time-fragmentation across deadlines)."""
    locks, controls = [], []
    # ---- cross-STRIKE: same (asset, dir, deadline), monotone in threshold ----
    groups = collections.defaultdict(list)
    for r in rows:
        groups[(r["asset"], r["dir"], r["end"])].append(r)
    for key, items in groups.items():
        items.sort(key=lambda x: x["thr"])
        # adjacency in the ordered ladder
        for i in range(len(items) - 1):
            a, b = items[i], items[i + 1]   # a = lower thr, b = higher thr
            # easier outcome (should have >= yes): up→lower thr ; down→higher thr
            if a["dir"] == "up":
                easier, harder = a, b          # reach lower $ is easier
            else:
                easier, harder = b, a          # dip to higher $ is easier
            gap = harder["yes"] - easier["yes"]    # >0 means harder priced ABOVE easier = violation
            rec = {"easier": easier, "harder": harder, "gap": round(gap, 4),
                   "key": f"{key[0]}-{key[1]}-{key[2]}"}
            if gap > MIN_NET:
                locks.append(rec)
            elif gap <= 0:                         # consistent adjacent pair = control universe
                controls.append(rec)
    # ---- cross-DATE: same (asset, dir, threshold), monotone non-decreasing in deadline ----
    # A cumulative "touch by D" event with more time can only be MORE likely, so the later
    # deadline's YES must be >= the earlier's. Earlier priced ABOVE later = violation → buy the
    # cheap LATER yes + NO on the expensive EARLIER. Payoff in {1,2} (earlier-YES ⟹ later-YES,
    # so the 0-payoff state is impossible) → same lock structure as the strike pass.
    dgroups = collections.defaultdict(list)
    for r in rows:
        if not _cumulative(r["q"]) or not _ISO.match(str(r["end"])):
            continue                                # need touch semantics + a real CALENDAR deadline
        dgroups[(r["asset"], r["dir"], r["thr"])].append(r)
    for key, items in dgroups.items():
        items.sort(key=lambda x: x["end"])          # ISO deadlines sort lexically
        for i in range(len(items) - 1):
            earlier, later = items[i], items[i + 1]
            if earlier["end"] == later["end"]:
                continue                            # same deadline → duplicate, handled above
            if _norm_stem(earlier["q"]) != _norm_stem(later["q"]):
                continue                            # different resolution source → NOT nested, skip
            gap = earlier["yes"] - later["yes"]     # >0 = earlier richer than later = violation
            rec = {"easier": later, "harder": earlier, "gap": round(gap, 4),
                   "key": f"{key[0]}-{key[1]}-{key[2]}-DATE"}
            if gap > MIN_NET:
                locks.append(rec)
            elif gap <= 0:
                controls.append(rec)
    return locks, controls


def _pos(lock, kind):
    e, h = lock["easier"], lock["harder"]
    cost = round(e["yes"] + (1.0 - h["yes"]), 4)
    return {"id": f"{e['cid']}|{h['cid']}", "kind": kind, "key": lock["key"], "gap": lock["gap"],
            "cheap_cid": e["cid"], "cheap_yes": e["yes"], "cheap_q": e["q"],
            "exp_cid": h["cid"], "exp_yes": h["yes"], "exp_q": h["q"],
            "cost": cost, "opened": time.strftime("%Y-%m-%d %H:%M"), "status": "open"}


def scan(st):
    rows = _latest_rows()
    if not rows:
        print("scan: no crypto-threshold rows in gate log yet (run analyst_data_gate.py once)")
        return st
    locks, controls = find_locks(rows)
    held = {p["id"] for p in st["open"]} | {p["id"] for p in st["resolved"]}
    nr = nc = 0
    for lk in locks:
        p = _pos(lk, "real")
        if p["id"] in held:
            continue
        st["open"].append(p); held.add(p["id"]); nr += 1
    open_ctrl = sum(1 for p in st["open"] if p["kind"] == "control")
    for lk in controls:
        if open_ctrl >= CONTROL_TARGET:
            break
        p = _pos(lk, "control")          # a CONSISTENT pair "locked" the same way → must net ~0/neg after fee
        if p["id"] in held:
            continue
        st["open"].append(p); held.add(p["id"]); open_ctrl += 1; nc += 1
    st["last_scan"] = time.strftime("%Y-%m-%d %H:%M")
    print(f"scan: +{nr} real lock(s), +{nc} control · live violations>fee: {len(locks)}")
    return st


def _settle_one(cid):
    """Reuse dataarb's proven CLOB settlement path."""
    try:
        import dataarb
        return dataarb.clob_state(cid)["resolved"]   # "YES"/"NO"/None
    except Exception:
        return None


def settle(st):
    still, n = [], 0
    for p in st["open"]:
        cr = _settle_one(p["cheap_cid"])
        hr = _settle_one(p["exp_cid"])
        if cr in ("YES", "NO") and hr in ("YES", "NO"):
            cheap_pay = 1.0 if cr == "YES" else 0.0
            exp_no_pay = 1.0 if hr == "NO" else 0.0
            p["pnl"] = round((cheap_pay + exp_no_pay - p["cost"]) * (1 - FEE) * SIZE, 4)
            p["resolved"] = f"cheap={cr},exp={hr}"
            p["status"] = "resolved"; p["closed_at"] = time.strftime("%Y-%m-%d %H:%M")
            st["resolved"].append(p); n += 1
        else:
            still.append(p)
    st["open"] = still
    print(f"settle: {n} newly resolved (open {len(st['open'])}, resolved {len(st['resolved'])})")
    return st


def _ci(xs, iters=4000):
    if len(xs) < 2:
        return (None, None)
    import random
    rnd = random.Random(42); n = len(xs)
    m = sorted(sum(xs[rnd.randrange(n)] for _ in range(n)) / n for _ in range(iters))
    return (round(m[int(0.025 * iters)], 4), round(m[int(0.975 * iters)], 4))


def report(st):
    real = [p["pnl"] for p in st["resolved"] if p["kind"] == "real"]
    ctrl = [p["pnl"] for p in st["resolved"] if p["kind"] == "control"]
    L = ["=" * 64, "  monoarb — MONOTONICITY/CONSISTENCY ARB (paper, hold-to-resolution)", "=" * 64]
    for name, xs in (("REAL", real), ("control", ctrl)):
        if not xs:
            L.append(f"  {name:9} n=0  (accumulating — 0 locks is EXPECTED on a liquid venue)"); continue
        lo, hi = _ci(xs); ci = f"  CI[{lo:+.3f},{hi:+.3f}]" if lo is not None else ""
        L.append(f"  {name:9} n={len(xs):>3}  ${sum(xs)/len(xs):+.4f}/lock  total ${sum(xs):+.2f}{ci}")
    nr = sum(1 for p in st["open"] if p["kind"] == "real")
    L.append(f"  open: {len(st['open'])} (real {nr}) · last scan {st.get('last_scan')}")
    return "\n".join(L)


def main():
    st = _load()
    st = scan(st)
    st = settle(st)
    _save(st)
    out = report(st)
    print(out)
    if len(sys.argv) > 1 and sys.argv[1] == "once":
        try:
            os.makedirs(os.path.dirname(VAULT), exist_ok=True)
            open(VAULT, "w").write("# monoarb — monotonicity-arb leg\n\n```\n" + out + "\n```\n")
        except Exception:
            pass


if __name__ == "__main__":
    main()
