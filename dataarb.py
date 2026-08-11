#!/usr/bin/env python3
"""dataarb.py — PAPER leg: the ONE edge type that ever beat this market (settled-but-mispriced).

The brain's verdict (95 dead legs, Lessons 13-22): predictive TAKER legs die to calibrated mids.
The only edge type that EVER beat this market is DATA-ARB — a market whose objective resolving
series ALREADY determines the outcome, but the price hasn't caught up (Hormuz). `analyst_data_gate
.arb_signals` finds them (BUY YES; data confirms YES and YES is still cheap). The WATCH/buy-NO
cases are excluded upstream = the FLB trap (cheap NO on a long tail).

Why this leg can be +EV when every predictive leg isn't:
  • NON-predictive — it acts on data that ALREADY decided the outcome, not a forecast.
  • HOLD TO RESOLUTION — no stop, no exit view → immune to gap-through (Lesson 13) and
    spread-on-exit. Settles at $1/$0 on CLOB on-chain truth (stale_nodata-proof).
  • Resolution-mechanics-aware — the data-gate's classify (snapshot/race/cuts fixes) gates
    which markets are true touch-arbs, so the data read is honest. The gate also suppresses
    resolution-IDENTITY mismatches before they reach this leg: BLS markets that name only a
    year ("unemployment in 2026") are an annual/span basis a single monthly print can't
    resolve → fetch_bls returns released=False and no signal is written (commit 1efe670).

HONEST BY CONSTRUCTION:
  • CONTROL = buy YES on comparable source-matched cheap-YES markets that are NOT data-confirmed.
    A calibrated market prices cheap-YES to ~0 EV → the control MUST be ~0/negative. The leg is
    real ONLY if it beats the control.
  • GATE: n>=30 real exits AND bootstrap CI>0 AND mean>control AND control<=0. Same bar as every
    leg — on the SAME honest scoreboard, not a separate 'book'.

Rare by nature (real settled-mispricings are scarce → slow to n=30). Rare-but-real is the Hormuz
profile — the opposite of the fast-firing predictive legs that all died. PAPER only. Stdlib only.

Run: dataarb.py scan | settle | once | report
"""
import os, json, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "dataarb_state.json")
SIGNALS = os.path.join(HERE, "datagate_arbs.json")
CLOB = "https://clob.polymarket.com/markets/"
SIZE = 10.0           # paper shares per position (fixed notional shape)
CONTROL_TARGET = 40   # keep ~this many control positions open, matching the real-leg shape


def clob_state(market_id):
    """{yes, resolved 'YES'/'NO'/None, closed} from CLOB on-chain truth (stale_nodata-proof).
    Copied from analyst_book.clob_state so settlement matches the proven path."""
    try:
        req = urllib.request.Request(CLOB + str(market_id), headers={"User-Agent": "dataarb/1.0"})
        m = json.loads(urllib.request.urlopen(req, timeout=12).read())
    except Exception:
        return {"yes": None, "resolved": None, "closed": False}
    yes, resolved = None, None
    for t in m.get("tokens", []):
        oc = (t.get("outcome") or "").lower()
        if oc == "yes":
            try:
                yes = float(t.get("price"))
            except (TypeError, ValueError):
                pass
            if t.get("winner") is True:
                resolved = "YES"
        elif oc == "no" and t.get("winner") is True:
            resolved = "NO"
    return {"yes": yes, "resolved": resolved, "closed": bool(m.get("closed"))}


def load_state():
    if os.path.exists(STATE):
        try:
            return json.load(open(STATE))
        except Exception:
            pass
    return {"open": [], "resolved": [], "last_scan": None}


def save_state(st):
    tmp = STATE + ".tmp"
    json.dump(st, open(tmp, "w"), indent=2)
    os.replace(tmp, STATE)


def _pos(cid, q, side, price, kind, reason=""):
    return {"cid": str(cid), "q": (q or "")[:90], "side": side, "entry": round(float(price), 4),
            "size": SIZE, "kind": kind, "reason": reason,
            "opened": time.strftime("%Y-%m-%d %H:%M"), "status": "open"}


def scan(st):
    """Open paper YES positions: real (data-gate signals) + control (random cheap-YES, not
    data-confirmed). One position per market; skips anything already open or resolved."""
    try:
        sig = json.load(open(SIGNALS))
    except Exception:
        print("scan: no datagate_arbs.json yet — run `analyst_data_gate.py once` first")
        return st
    held = {p["cid"] for p in st["open"]} | {p["cid"] for p in st["resolved"]}
    n_real = n_ctrl = 0
    for s in sig.get("signals", []):
        if str(s["cid"]) in held:
            continue
        st["open"].append(_pos(s["cid"], s["q"], s["side"], s["price"], "real", s.get("reason", "")))
        held.add(str(s["cid"]))
        n_real += 1
    open_ctrl = sum(1 for p in st["open"] if p["kind"] == "control")
    for c in sig.get("control_pool", []):
        if open_ctrl >= CONTROL_TARGET:
            break
        if str(c["cid"]) in held:
            continue
        st["open"].append(_pos(c["cid"], c["q"], "YES", c["yes"], "control",
                               "random cheap-YES (not data-confirmed)"))
        held.add(str(c["cid"]))
        open_ctrl += 1
        n_ctrl += 1
    st["last_scan"] = time.strftime("%Y-%m-%d %H:%M")
    print(f"scan: +{n_real} real, +{n_ctrl} control (open now: {len(st['open'])})")
    return st


def _pnl(pos, resolved):
    """Per-position realized paper P&L. YES buy: win=(1-entry)*size, lose=-entry*size."""
    won = (resolved == pos["side"])
    return round(((1.0 - pos["entry"]) if won else (-pos["entry"])) * pos["size"], 4)


def settle(st):
    """Resolve open positions via CLOB on-chain truth; book P&L; move to resolved."""
    still_open, n = [], 0
    for p in st["open"]:
        cs = clob_state(p["cid"])
        if cs["resolved"] in ("YES", "NO"):
            p["resolved"] = cs["resolved"]
            p["pnl"] = _pnl(p, cs["resolved"])
            p["closed_at"] = time.strftime("%Y-%m-%d %H:%M")
            p["status"] = "resolved"
            st["resolved"].append(p)
            n += 1
        else:
            still_open.append(p)
    st["open"] = still_open
    print(f"settle: {n} newly resolved (open: {len(st['open'])}, total resolved: {len(st['resolved'])})")
    return st


def _boot_ci(xs, iters=2000):
    if len(xs) < 2:
        return (None, None)
    import random
    random.seed(42)
    n = len(xs)
    means = sorted(sum(xs[random.randrange(n)] for _ in range(n)) / n for _ in range(iters))
    return (round(means[int(0.025 * iters)], 4), round(means[int(0.975 * iters)], 4))


def report(st):
    real = [p["pnl"] for p in st["resolved"] if p["kind"] == "real"]
    ctrl = [p["pnl"] for p in st["resolved"] if p["kind"] == "control"]
    print("=" * 64)
    print("  dataarb — settled-but-mispriced DATA-ARB (paper, hold-to-resolution)")
    print("=" * 64)
    for name, xs in (("REAL", real), ("control", ctrl)):
        if not xs:
            print(f"  {name:9} n=0")
            continue
        m = sum(xs) / len(xs)
        lo, hi = _boot_ci(xs)
        ci = f"  CI[{lo:+.3f},{hi:+.3f}]" if lo is not None else ""
        print(f"  {name:9} n={len(xs):>3}  ${m:+.4f}/exit  total ${sum(xs):+.2f}{ci}")
    nr = sum(1 for p in st["open"] if p["kind"] == "real")
    print(f"  open: {len(st['open'])} (real {nr}, control {len(st['open']) - nr}) · last scan {st.get('last_scan')}")
    if len(real) >= 30:
        lo, _ = _boot_ci(real)
        rm, cm = sum(real) / len(real), (sum(ctrl) / len(ctrl) if ctrl else 0.0)
        ok = lo is not None and lo > 0 and rm > cm and cm <= 0
        print(f"  GATE: {'✅ PASS — real edge (CI>0, beats control, control≤0)' if ok else '❌ no edge (gate not cleared)'}")
    else:
        print(f"  GATE: ACCUMULATING {len(real)}/30 real exits — rare-but-real, expected slow (Hormuz profile)")


def main():
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "report"
    st = load_state()
    if cmd == "scan":
        st = scan(st); save_state(st); report(st)
    elif cmd == "settle":
        st = settle(st); save_state(st); report(st)
    elif cmd == "once":
        st = settle(st); st = scan(st); save_state(st); report(st)
    elif cmd == "report":
        report(st)
    else:
        print("usage: dataarb.py scan|settle|once|report")


if __name__ == "__main__":
    main()
