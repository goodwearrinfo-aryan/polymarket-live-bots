#!/usr/bin/env python3
"""basketlock.py — PAPER leg: capture EXECUTABLE basket-arb locks, hold to resolution.

The one +EV thing in the system: structural arbitrage (a complete set of mutually-
exclusive outcomes priced < $1 at the ASK pays exactly $1 at resolution — risk-free,
no prediction). FEE (resolved 2026-06-20, supersedes the old "zero fees" claim): Polymarket
charges ZERO MAKER fees (+daily rebate), but since 2026-03-30 it charges category-based TAKER
fees = feeRate·p²·(1−p) per share (peak 1.8% at p=0.5, →0 near 0/1; GEOPOLITICS/world-events
FREE; politics/finance 0.04, econ/culture/weather 0.05, sports 0.03, crypto 0.07). For a held
complete-set lock the taker haircut is small (~0.5–1% summed; longshot legs ≈0) — verified
2026-06-20: real complete-field locks net-positive after taker fee (8/8 booked, +3.16% net on
the live Bitcoin-vs-Gold lock). The MIN_EDGE=0.02 floor below already clears the haircut; maker
entry clears it entirely. So NOT zero (old claim) and NOT a flat ~2% (the other stale figure).

STEP 1 (this file): the capture engine + state. The hard lesson from the depth probe is
that top-of-book is thin, so a lock is only real for the size the book actually holds:
for a complete LONG set you buy equal SHARES of every leg, so the binding size is
  shares = min over legs( size available at that leg's ask )
  usd_deployed = shares * Σask        locked_profit = shares * edge
Filters keep only fillable, small-field, near-dated locks (no multi-year capital lockup).
Read-only, NO orders, NO keys. Captures to basketlock_state.json (resumable).

Usage:
  python3 basketlock.py            # scan live, show the board, capture new locks to state
  python3 basketlock.py --scan     # scan + show board only (no capture)
  python3 basketlock.py --state    # show captured paper book
"""
import os, sys, json, time, argparse, urllib.request, random
import edge_common as ec

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "basketlock_state.json")
GAMMA = "https://gamma-api.polymarket.com/markets"
CLOB_BOOK = "https://clob.polymarket.com/book?token_id="

MIN_EDGE = 0.02            # 2% locked minimum to bother
MAX_OUTCOMES = 6          # >6 legs can't be filled at top-of-book simultaneously
MAX_DAYS = 210            # ~7 months; includes the ubiquitous year-end resolution date,
                          # while still excluding the multi-year locks that kill annualized return
MIN_DEPLOY_USD = 10.0     # if the book holds < $10 at the lock, it's a phantom quote — skip
EXHAUST_LO, EXHAUST_HI = 0.93, 1.08   # Σmid in this band ⇒ complete mutually-exclusive field
ASK_TOL = 0.005          # book must still be within 0.5¢ of the quoted ask

# basketrand control: random UNRELATED markets bought YES @ ask. No complete-set
# structure ⇒ E[P&L] = Σmid − Σask = −spread < 0. MUST lose; if it ever nets positive
# at resolution like the real baskets, the lock math / settlement has a bug.
CONTROL_TARGET = 30      # keep ~30 open control baskets accumulating toward the gate
CONTROL_DEPLOY = 50.0    # fixed paper notional per control basket
CONTROL_N = (2, 3, 4)    # match the small-N shape of the real locks


def _get(url, timeout=12):
    return json.loads(urllib.request.urlopen(
        urllib.request.Request(url, headers={"User-Agent": "basketlock/1.0"}), timeout=timeout).read())


def leg_top(token, ask):
    """(shares, px) at the best ask if the book is still ~the quoted ask, else (0, px)."""
    try:
        asks = _get(CLOB_BOOK + str(token)).get("asks", [])
        levels = sorted(((float(a["price"]), float(a["size"])) for a in asks), key=lambda x: x[0])
        if not levels:
            return 0.0, ask
        px, sz = levels[0]
        if px > ask + ASK_TOL:        # book walked away from the quote
            return 0.0, px
        return sz, px
    except Exception:
        return 0.0, ask


def resolve_ts(token):
    """Resolution epoch for the market owning this token (Gamma endDate); None if unknown."""
    try:
        d = _get(GAMMA + "?clob_token_ids=" + str(token))
        m = d[0] if isinstance(d, list) and d else None
        end = m.get("endDate") if m else None
        if end:
            return int(time.mktime(time.strptime(end[:19], "%Y-%m-%dT%H:%M:%S")))
    except Exception:
        pass
    return None


def scan_executable(pages=10):
    """Verified LONG locks enriched with REAL fillable size + resolution date.
    Cheap filters (free from poly_events) first; the Gamma + CLOB calls only run on
    candidates that already clear edge/field/tenor."""
    now = time.time()
    out = []
    for e in ec.poly_events(pages=pages):
        if not e["mutually_exclusive"]:
            continue
        legs = e["markets"]
        n = len(legs)
        if n < 2 or n > MAX_OUTCOMES:
            continue
        if any(m["ask"] is None or m["bid"] is None for m in legs):
            continue
        if any(not m["yes_token"] for m in legs):
            continue
        s_mid = sum(m["yes"] for m in legs)
        if not (EXHAUST_LO <= s_mid <= EXHAUST_HI):     # complete field?
            continue
        s_ask = sum(m["ask"] for m in legs)
        edge = 1.0 - s_ask
        if edge < MIN_EDGE:
            continue
        rd = resolve_ts(legs[0]["yes_token"])           # need a date to settle later
        if rd is None:
            continue
        days = (rd - now) / 86400
        if days < 0 or days > MAX_DAYS:
            continue
        shares = float("inf")
        legrows = []
        for m in legs:
            sz, px = leg_top(m["yes_token"], m["ask"])
            shares = min(shares, sz)
            legrows.append({"q": m["q"][:60], "token": m["yes_token"],
                            "ask": round(m["ask"], 3), "top_px": round(px, 3),
                            "top_shares": round(sz, 1)})
        usd = shares * s_ask
        if usd < MIN_DEPLOY_USD:
            continue
        out.append({
            "slug": e["slug"], "title": e["title"], "n": n,
            "edge": round(edge, 4), "sum_ask": round(s_ask, 4),
            "resolve_ts": rd, "resolve": time.strftime("%Y-%m-%d", time.gmtime(rd)),
            "days": round(days, 1), "shares": round(shares, 1),
            "usd_deployed": round(usd, 2), "locked_profit": round(shares * edge, 2),
            "legs": legrows,
        })
    out.sort(key=lambda d: d["locked_profit"], reverse=True)
    return out


def load_state():
    if os.path.exists(STATE):
        try:
            return json.load(open(STATE))
        except Exception:
            pass
    return {"captured": [], "resolved": [], "last_scan": None}


def save_state(st):
    tmp = STATE + ".tmp"
    json.dump(st, open(tmp, "w"), indent=2)
    os.replace(tmp, STATE)


def capture(locks, st):
    """Record NEW locks (dedupe by slug) as open paper positions at fillable size."""
    have = {c["slug"] for c in st["captured"]}
    new = 0
    for L in locks:
        if L["slug"] in have:
            continue
        st["captured"].append({
            "slug": L["slug"], "title": L["title"], "n": L["n"], "edge": L["edge"],
            "sum_ask": L["sum_ask"], "resolve_ts": L["resolve_ts"], "resolve": L["resolve"],
            "shares": L["shares"], "usd_deployed": L["usd_deployed"],
            "locked_profit": L["locked_profit"], "captured_ts": int(time.time()),
            "legs": L["legs"], "status": "open",
        })
        new += 1
    return new


def _parse_end(end):
    try:
        return int(time.mktime(time.strptime(end[:19], "%Y-%m-%dT%H:%M:%S"))) if end else None
    except Exception:
        return None


def capture_control(st, pool):
    """Top up the basketrand control with random UNRELATED markets bought YES @ ask.
    No complete-set structure → expected P&L = Σmid − Σask = −spread (must lose).
    Settled by the SAME logic as the real basket (payoff = shares × legs-that-win),
    so a positive control verdict at resolution would expose a settlement bug."""
    st.setdefault("control", [])
    open_ct = sum(1 for c in st["control"] if c["status"] == "open")
    if open_ct >= CONTROL_TARGET:
        return 0
    now = time.time()
    cand = []
    for m in pool:
        if m["bid"] is None or m["ask"] is None:
            continue
        if not (0.03 < m["yes"] < 0.97):          # skip degenerate ~0/~1 markets
            continue
        rd = _parse_end(m["end"])
        if rd is None or not (now < rd < now + MAX_DAYS * 86400):
            continue
        cand.append({**m, "resolve_ts": rd})
    used = {tuple(sorted(c["ids"])) for c in st["control"]}
    new, tries = 0, 0
    while open_ct + new < CONTROL_TARGET and tries < 300 and len(cand) >= 2:
        tries += 1
        n = random.choice(CONTROL_N)
        if len(cand) < n:
            continue
        pick = random.sample(cand, n)
        key = tuple(sorted(m["id"] for m in pick))
        if key in used:
            continue
        used.add(key)
        s_ask = sum(m["ask"] for m in pick)
        s_mid = sum(m["yes"] for m in pick)
        if s_ask <= 0:
            continue
        shares = round(CONTROL_DEPLOY / s_ask, 1)
        rts = max(m["resolve_ts"] for m in pick)
        st["control"].append({
            "kind": "control", "ids": [m["id"] for m in pick],
            "resolve_ts": rts, "resolve": time.strftime("%Y-%m-%d", time.gmtime(rts)),
            "shares": shares, "sum_ask": round(s_ask, 4), "sum_mid": round(s_mid, 4),
            "usd_deployed": round(shares * s_ask, 2),
            "exp_pnl": round(shares * (s_mid - s_ask), 2),   # ≈ −spread, must be ≤ ~0
            "captured_ts": int(time.time()),
            "legs": [{"q": m["q"][:50], "id": m["id"], "slug": m["slug"],
                      "ask": round(m["ask"], 3), "mid": round(m["yes"], 3)} for m in pick],
            "status": "open",
        })
        new += 1
    return new


def leg_resolved_yes(leg):
    """True if this leg's YES is the resolved winner, False if it lost, None if unresolved.
    Real legs carry 'token' (clob); control legs carry 'id' (conditionId) + 'slug'.
    Gamma first; on-chain CTF fallback (Gamma drops closed markets) when a conditionId is known."""
    m = None
    for q in (("clob_token_ids", leg.get("token")), ("condition_ids", leg.get("id")),
              ("slug", leg.get("slug"))):
        if not q[1]:
            continue
        try:
            d = _get(f"{GAMMA}?{q[0]}={q[1]}")
            m = d[0] if isinstance(d, list) and d else (d if isinstance(d, dict) and d.get("question") else None)
        except Exception:
            m = None
        if m:
            break
    if m and m.get("closed"):
        op = m.get("outcomePrices")
        if isinstance(op, str):
            op = json.loads(op)
        if op and len(op) == 2 and op[0] in ("0", "1"):
            return op[0] == "1"                  # YES = outcome index 0
    cid = (m.get("conditionId") if m else None) or leg.get("id")
    if cid:
        try:
            import ctf_resolution
            win = ctf_resolution.winning_outcome(cid)   # 0=YES, 1=NO, -1/None=unknown
            if win in (0, 1):
                return win == 0
        except Exception:
            pass
    return None


def settle(st):
    """Settle any basket (real or control) whose EVERY leg has resolved.
    payoff = shares × (legs resolving YES); realized P&L = payoff − usd_deployed.
    A complete set settles to exactly its locked profit; controls settle ≈ −spread."""
    sr = sc = 0
    for kind, key in (("real", "captured"), ("control", "control")):
        still = []
        for b in st.get(key, []):
            if b.get("status") != "open":
                continue
            if time.time() < b["resolve_ts"] - 86400:   # cheap guard: not near resolution yet
                still.append(b); continue
            outs = [leg_resolved_yes(leg) for leg in b["legs"]]
            if any(o is None for o in outs):             # not all legs resolved
                still.append(b); continue
            wins = sum(1 for o in outs if o)
            payoff = round(b["shares"] * wins, 2)
            b.update(kind=kind, status="resolved", wins=wins, payoff=payoff,
                     realized_pnl=round(payoff - b["usd_deployed"], 2), resolved_ts=int(time.time()))
            st.setdefault("resolved", []).append(b)
            sr, sc = (sr + 1, sc) if kind == "real" else (sr, sc + 1)
        st[key] = still
    return sr, sc


def boot_ci(xs, n=2000):
    """Deterministic bootstrap 95% CI of the mean (xorshift LCG → reproducible across
    runs, same as scorecard.py). None if < 5 samples."""
    if len(xs) < 5:
        return None
    seed, m = 2463534242, len(xs)
    means = []
    for _ in range(n):
        s = 0.0
        for _ in range(m):
            seed ^= (seed << 13) & 0xFFFFFFFF
            seed ^= seed >> 17
            seed ^= (seed << 5) & 0xFFFFFFFF
            s += xs[seed % m]
        means.append(s / m)
    means.sort()
    return means[int(0.025 * n)], means[int(0.975 * n)]


def scoreboard(st):
    res = st.get("resolved", [])
    real = [b for b in res if b.get("kind") == "real"]
    ctrl = [b for b in res if b.get("kind") == "control"]
    rp = sum(b["realized_pnl"] for b in real)
    cp = sum(b["realized_pnl"] for b in ctrl)
    pnls = [b["realized_pnl"] for b in real]
    ci = boot_ci(pnls)
    mean = (rp / len(real)) if real else 0.0
    ci_str = f"  CI[{ci[0]:+.3f},{ci[1]:+.3f}]" if ci else "  (CI needs ≥5 resolved)"
    print(f"\n  SCOREBOARD — realized (held to resolution)")
    print(f"   REAL    {len(real):>3} resolved   net ${rp:+.2f}   mean/lock ${mean:+.3f}{ci_str}")
    if real:
        print(f"            per-lock distribution: min ${min(pnls):+.2f}  max ${max(pnls):+.2f}")
    print(f"   CONTROL {len(ctrl):>3} resolved   net ${cp:+.2f}   (must stay ≤0)")
    gate_n = len(real) >= 30
    gate_real = ci is not None and ci[0] > 0       # bootstrap LOWER bound > 0, not just net+
    gate_ctrl = cp <= 0
    if gate_n and gate_real and gate_ctrl:
        print(f"   → GATE PASSED: ≥30 real locks, bootstrap CI>0, control loses. PROVEN risk-free edge.")
    else:
        miss = []
        if not gate_n:
            miss.append(f"{len(real)}/30 real locks")
        elif not gate_real:
            miss.append("bootstrap CI not yet > 0")
        if not gate_ctrl:
            miss.append("CONTROL POSITIVE → settlement bug!")
        print(f"   → gate: {', '.join(miss) or 'on track'} (need 30 + CI>0 + control loses)")


def show_board(locks):
    print(f"\n  EXECUTABLE BASKET LOCKS — fillable, ≤{MAX_OUTCOMES} legs, ≤{MAX_DAYS}d — PAPER")
    if not locks:
        print("  (none right now — books too thin or no field priced under $1)\n")
        return
    print(f"  {'edge':>6} {'profit$':>8} {'deploy$':>8} {'N':>3} {'resolve':>11} {'days':>5}  event")
    for L in locks:
        print(f"  {L['edge']*100:>5.1f}% {L['locked_profit']:>8.2f} {L['usd_deployed']:>8.0f} "
              f"{L['n']:>3} {L['resolve']:>11} {L['days']:>5.0f}  {L['title'][:40]}")
    tot = sum(L["locked_profit"] for L in locks)
    print(f"  → {len(locks)} locks, total locked profit ${tot:.2f} (risk-free, held to resolution)\n")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--scan", action="store_true", help="scan + board only, no capture")
    ap.add_argument("--state", action="store_true", help="show captured paper book")
    ap.add_argument("--score", action="store_true", help="settle resolved baskets + show scoreboard")
    a = ap.parse_args()
    if a.score:
        st = load_state()
        sr, sc = settle(st)
        save_state(st)
        print(f"  settled {sr} real + {sc} control this run")
        scoreboard(st)
        sys.exit(0)
    if a.state:
        st = load_state()
        ctrl = st.get("control", [])
        print(f"\n  BASKETLOCK paper book: {len(st['captured'])} real open, {len(st['resolved'])} resolved "
              f"| {len(ctrl)} control open")
        for c in st["captured"]:
            print(f"   REAL  {c['edge']*100:>4.1f}%  ${c['locked_profit']:>6.2f} on ${c['usd_deployed']:>6.0f}  "
                  f"res {c['resolve']}  {c['title'][:42]}")
        if ctrl:
            exp = sum(c["exp_pnl"] for c in ctrl)
            print(f"   CONTROL basketrand: {len(ctrl)} baskets, Σ expected P&L ${exp:+.2f} "
                  f"(must be ≤0 — random buying loses the spread)")
        sys.exit(0)
    locks = scan_executable()
    show_board(locks)
    if not a.scan:
        st = load_state()
        sr, sc = settle(st)                     # settle anything that resolved since last run
        n = capture(locks, st)
        pool = ec.poly_markets(pages=6, min_vol=5000)
        nc = capture_control(st, pool)
        st["last_scan"] = int(time.time())
        save_state(st)
        print(f"  settled {sr} real + {sc} control | captured {n} new real, {nc} new control → {STATE}")
        print(f"  ({len(st['captured'])} real open, {len(st.get('control', []))} control open)")
        scoreboard(st)
