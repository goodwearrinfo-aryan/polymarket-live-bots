#!/usr/bin/env python3
"""
profit_engine.py — Unified +EV paper engine for the Polymarket bot.

WHY THIS EXISTS
---------------
The paper history proves (95 dead legs, -$380 across controls+scalp) that no
*predictive* edge survives on this calibrated venue. The ONLY dollars that have
ever come in were from positions that were +EV BEFORE the outcome was known:

  1. STRUCTURAL locks  — complete-set / negRisk baskets bought at Sum(ask) < 1,
     held to resolution. Profit is locked by structure, not by a forecast.
  2. DATA arb          — a market resolving on an objective public series where
     the series already determines the outcome in-window but the price lags
     (the Hormuz / PortWatch win).

This engine refuses to take anything else. It is "profitable by construction":
its expectation is positive before resolution for every position it selects,
subject to honest execution gates. It is NOT a forecaster and never sizes a
directional/predictive bet.

It also enforces the project's hardest-won lesson as a HARD CONSTRAINT:
anti-concentration. +$28 from one geopolitical theme is one bet wearing three
hats (the green-book-concentration scar). So no more than K positions per theme.

PAPER ONLY. Read-only. Places no orders. Reads local state files; no keys.

USAGE
-----
  python3 profit_engine.py                 # build the sized BUY plan
  python3 profit_engine.py --bankroll 500  # plan against a $500 paper bankroll
  python3 profit_engine.py --accounting    # honest track decomposition of the book
  python3 profit_engine.py --json          # machine-readable plan
"""
import json, os, sys, argparse, math
from collections import defaultdict

BOT = os.path.dirname(os.path.abspath(__file__))

# ---- Hard constants (from BRAIN canon; change deliberately) -----------------
NET_EDGE_FLOOR = 0.005      # require >= 0.5% net-of-fee edge to even consider
MAX_PER_THEME  = 2          # anti-concentration: <= K positions per theme
CAP_PER_POS    = 200.0      # depth ceiling: locks die past ~$200 notional
DEFAULT_BANKROLL = 500.0

# Taker fee RATE by category (per-share fee = rate * p^2 * (1-p)); geo is FREE.
FEE_RATE = {
    "geopolitics": 0.00, "politics": 0.04, "macro": 0.04,
    "crypto": 0.07, "weather": 0.05, "sports": 0.03, "other": 0.05,
}
# Conservative summed fee haircut cap on a complete-set lock (BRAIN: ~0.5-1%,
# peak ~1.8% at p=0.5, geo=0). Scale the category rate to a lock-level haircut.
def fee_haircut(theme):
    f = FEE_RATE.get(theme, 0.05)
    return min(f * 0.25, 0.018)   # geo -> 0.0; crypto -> 0.0175; macro -> 0.010

# ---- Theme classification (drives anti-concentration + fees) ----------------
THEME_KEYS = [
    ("geopolitics", ("iran","hormuz","hezbollah","israel","gaza","russia",
                     "ukraine","war","strike","nuclear","missile","strait")),
    ("politics",    ("senate","midterm","primary","election","democrat",
                     "republican","president","governor","congress","house")),
    ("macro",       ("fed","rate cut","interest rate","cpi","inflation",
                     "payroll","unemployment","jobs","recession","gdp")),
    ("crypto",      ("bitcoin","btc","ethereum","eth","solana","crypto","xrp")),
    ("weather",     ("volcano","temperature","hottest","climate","hurricane",
                     "eruption","record","el nino","la nina")),
    ("commodity",   ("gold","oil","crude","gas","silver","copper")),
    ("sports",      ("nba","nfl","mlb","nhl","cup","champion","playoff","match")),
]
def theme_of(text):
    t = (text or "").lower()
    for name, keys in THEME_KEYS:
        if any(k in t for k in keys):
            # fold commodity into macro for fee/concentration purposes
            return "macro" if name == "commodity" else name
    return "other"

# ---- Candidate loading ------------------------------------------------------
def _load(path):
    try:
        return json.load(open(os.path.join(BOT, path)))
    except Exception:
        return None

def load_candidates():
    """Real (non-control) +EV positions from the survivor scanners."""
    cands = []

    # 1) Structural locks (basket_paper_book.json -> open). All real; controls
    #    were purged. gross edge = `edge`; net = gross - fee haircut(theme).
    bk = _load("basket_paper_book.json") or {}
    for slug, p in (bk.get("open") or {}).items():
        title = p.get("title", slug)
        th = theme_of(title)
        gross = float(p.get("edge", 0.0))
        net = gross - fee_haircut(th)
        cands.append({
            "src": "basket", "id": slug, "label": title, "theme": th,
            "gross_edge": gross, "net_edge": net,
            "min_notional": 50.0, "kind": p.get("kind", "LOCK"),
        })

    # 2) Data arb (dataarb_state.json -> open, kind != control).
    da = _load("dataarb_state.json") or {}
    for p in (da.get("open") or []):
        if p.get("kind") == "control":
            continue
        q = p.get("q", p.get("cid", "data-arb"))
        th = theme_of(q)
        # data-arb edge = distance of price from the data-implied outcome.
        # entry is the YES price; if data says the YES side is determined,
        # net edge ~ (1 - entry) for YES or entry for NO, minus fee. We store
        # a conservative placeholder; real magnitude comes from the gate.
        edge = float(p.get("edge", p.get("net_edge", 0.0)) or 0.0)
        cands.append({
            "src": "dataarb", "id": p.get("cid", q), "label": q, "theme": th,
            "gross_edge": edge, "net_edge": edge - fee_haircut(th),
            "min_notional": float(p.get("size", 10.0)), "kind": "DATA",
        })

    # 3) Monotonicity captures (monoarb_state.json -> open, kind != control).
    mo = _load("monoarb_state.json") or {}
    for p in (mo.get("open") or []):
        if p.get("kind") == "control":
            continue
        key = p.get("key", p.get("id", "mono"))
        th = theme_of(p.get("cheap_q", key))
        gap = float(p.get("gap", 0.0))   # gap < 0 means NO capture available
        edge = max(0.0, -gap) if gap < 0 else 0.0
        cands.append({
            "src": "monoarb", "id": p.get("id", key), "label": key, "theme": th,
            "gross_edge": edge, "net_edge": edge - fee_haircut(th),
            "min_notional": 10.0, "kind": "MONO",
        })

    # 4) Logical-consistency Dutch books (logic_arb_state.json -> open).
    #    Written by the edge-logic hunter: IMPLICATION (P(A) > P(B) when A=>B,
    #    so buy B / sell A) and MECE (a mutually-exclusive-and-exhaustive set
    #    priced != 1.0). Structural, non-predictive — same risk class as a
    #    basket lock. Contract: store gross_edge (pre-fee); engine nets it.
    lo = _load("logic_arb_state.json") or {}
    for p in (lo.get("open") or []):
        if p.get("kind") == "control":
            continue
        lbl = p.get("label", p.get("q", "logic-arb"))
        th = theme_of(lbl)
        gross = float(p.get("gross_edge", p.get("edge", 0.0)) or 0.0)
        cands.append({
            "src": "logic", "id": p.get("id", lbl), "label": lbl, "theme": th,
            "gross_edge": gross, "net_edge": gross - fee_haircut(th),
            "min_notional": float(p.get("size", 10.0)), "kind": "LOGIC",
        })

    # 5) Resolution-misread edges (resolution_arb_state.json -> open).
    #    Written by the edge-resolution hunter ONLY after the adversarial vet
    #    (edge-verifier, default KILL). Informational, not a forecast — but the
    #    easiest family to fool yourself on, so HARD-GUARD: require vetted=true.
    rs = _load("resolution_arb_state.json") or {}
    for p in (rs.get("open") or []):
        if p.get("kind") == "control" or not p.get("vetted"):
            continue
        lbl = p.get("label", p.get("q", "resolution-arb"))
        th = theme_of(lbl)
        gross = float(p.get("gross_edge", p.get("edge", 0.0)) or 0.0)
        cands.append({
            "src": "resolution", "id": p.get("id", lbl), "label": lbl, "theme": th,
            "gross_edge": gross, "net_edge": gross - fee_haircut(th),
            "min_notional": float(p.get("size", 10.0)), "kind": "RESOLUTION",
        })

    return cands

# ---- Allocation: anti-concentrated greedy by net-edge-per-dollar ------------
def allocate(cands, bankroll):
    # Gate first: must clear the net-of-fee floor.
    pool = [c for c in cands if c["net_edge"] >= NET_EDGE_FLOOR]
    pool.sort(key=lambda c: c["net_edge"], reverse=True)

    chosen, theme_count, spent = [], defaultdict(int), 0.0
    for c in pool:
        th = c["theme"]
        if theme_count[th] >= MAX_PER_THEME:
            c["skip"] = f"theme cap ({th} already has {MAX_PER_THEME})"
            continue
        size = min(CAP_PER_POS, bankroll - spent)
        if size < c["min_notional"]:
            c["skip"] = "bankroll exhausted"
            continue
        c["notional"] = round(size, 2)
        c["exp_profit"] = round(size * c["net_edge"], 2)
        chosen.append(c)
        theme_count[th] += 1
        spent += size

    return chosen, dict(theme_count), round(spent, 2), pool

# ---- Honest accounting: where did realized P&L actually come from? ----------
CONTROL = {"allin", "coinflip"}
ARB_KEYS = ("basket", "ladderarb", "clusterarb", "dataarb", "monoarb",
            "multiarb", "basketlock")
ANALYST_KEYS = ("geopol", "conviction", "alicenews", "analyst", "newsgate")
def track_of(leg):
    n = leg.lower()
    if n in CONTROL: return "control"
    if any(k in n for k in ARB_KEYS): return "arb"
    if any(k in n for k in ANALYST_KEYS): return "analyst"
    return "predictive"

def accounting():
    path = os.path.join(BOT, "legpnl_log.jsonl")
    if not os.path.exists(path):
        print("legpnl_log.jsonl not found; cannot decompose."); return
    # Each line is a full snapshot {..., total_realized, legs:{name:{realized..}}}.
    # Take the LAST parseable snapshot (latest state).
    snap = None
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line: continue
            try: r = json.loads(line)
            except Exception: continue
            if isinstance(r, dict) and isinstance(r.get("legs"), dict):
                snap = r
    if not snap:
        print("no parseable snapshot in legpnl_log.jsonl."); return
    agg = defaultdict(float); n = defaultdict(int)
    for leg, rec in snap["legs"].items():
        pnl = float((rec or {}).get("realized", 0.0) or 0.0)
        t = track_of(leg); agg[t] += pnl; n[t] += 1
    total = sum(agg.values())
    print("\n=== HONEST TRACK DECOMPOSITION (legpnl_log.jsonl) ===")
    order = ["control", "predictive", "arb", "analyst"]
    note = {
        "control":   "MUST be negative (honesty probe). Negative = healthy.",
        "predictive":"The confirmed null. Calibrated mids + gap-through.",
        "arb":       "Structural locks — the +EV-by-construction family.",
        "analyst":   "Data-arb judgments — watch for theme concentration.",
    }
    for t in order:
        print(f"  {t:11s} ${agg[t]:+8.2f}  ({n[t]:3d} legs)  {note[t]}")
    print(f"  {'TOTAL':11s} ${total:+8.2f}")
    print("  Read: only arb + analyst can carry real edge. A positive")
    print("  predictive number is lucky tails, not skill.\n")

# ---- Live re-price: what is actually ENTERABLE now --------------------------
def live_candidates():
    """Enterable locks RIGHT NOW via basket_arb's live CLOB re-price.
    net_edge here is already LIVE net-of-fee (do NOT subtract fee again).
    Fail-soft: returns (None, err) if the scan can't run; ([], None) if it
    runs but finds nothing (an honest stand-down)."""
    try:
        import basket_arb
        locks = basket_arb.get_locked_baskets(NET_EDGE_FLOOR)
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"
    cands = []
    for b in (locks or []):
        lbl = b.get("title") or b.get("event") or b.get("slug") or "live-lock"
        th = theme_of(lbl)
        net = float(b.get("net_edge") or 0.0)
        cands.append({
            "src": "live", "id": b.get("slug", lbl), "label": lbl, "theme": th,
            "gross_edge": float(b.get("gross_edge", net) or net),
            "net_edge": net, "min_notional": 50.0, "kind": "LIVE",
        })
    return cands, None

# ---- Main -------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Unified +EV paper engine")
    ap.add_argument("--bankroll", type=float, default=DEFAULT_BANKROLL)
    ap.add_argument("--accounting", action="store_true",
                    help="decompose realized P&L by track and exit")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--live", action="store_true",
                    help="re-price what's ENTERABLE now via basket_arb (network)")
    a = ap.parse_args()

    if a.accounting:
        accounting(); return

    if a.live:
        cands, err = live_candidates()
        if err is not None:
            print("LIVE SCAN UNAVAILABLE (fail-soft):", err)
            print("Nothing entered — an honest stand-down, not a guess.")
            return
        held = False
    else:
        cands = load_candidates()
        held = True
    chosen, themes, spent, pool = allocate(cands, a.bankroll)
    total_exp = round(sum(c["exp_profit"] for c in chosen), 2)

    if a.json:
        print(json.dumps({
            "bankroll": a.bankroll, "deployed": spent,
            "expected_profit": total_exp, "themes": themes,
            "positions": [{k: c.get(k) for k in
                ("src","label","theme","net_edge","notional","exp_profit")}
                for c in chosen]}, indent=2))
        return

    print("=" * 68)
    print(" UNIFIED +EV PAPER ENGINE  —  profitable by construction, paper only")
    print("=" * 68)
    if held:
        print(" MODE: HELD INVENTORY — edge is BOOKED-AT-ENTRY (stale), not live.")
        print("       These are positions already held; the edge is banked ONLY if")
        print("       the paper fills were real. For what's ENTERABLE now: --live.")
    else:
        print(" MODE: LIVE — locks re-priced on the CLOB now, net-of-fee.")
    print("-" * 68)
    print(f" Candidates scanned : {len(cands)}  (real, non-control survivors)")
    print(f" Cleared net-fee floor (>= {NET_EDGE_FLOOR:.1%}): {len(pool)}")
    print(f" Bankroll           : ${a.bankroll:,.0f}")
    print(f" Anti-concentration : <= {MAX_PER_THEME} positions / theme")
    print(f" Per-position cap   : ${CAP_PER_POS:,.0f} (depth ceiling)")
    print("-" * 68)
    if not chosen:
        print(" PLAN: stand down — no position clears the net-of-fee floor right")
        print(" now. On a calibrated venue this is the CORRECT steady state, not")
        print(" a failure. The engine only fires when structure or data hands it")
        print(" a real edge. Let the open locks ride to resolution.")
    else:
        print(f" {'SRC':8s} {'THEME':11s} {'NET':>6s} {'SIZE':>8s} {'E[$]':>7s}  LABEL")
        for c in chosen:
            print(f" {c['src']:8s} {c['theme']:11s} {c['net_edge']:5.2%} "
                  f"${c['notional']:7.0f} {c['exp_profit']:+6.2f}  {c['label'][:30]}")
        print("-" * 68)
        print(f" Deployed ${spent:,.0f} across {len(chosen)} positions / "
              f"{len(themes)} themes  ->  E[profit] ${total_exp:+,.2f}")
        print(f" Theme spread: {themes}")
    print("=" * 68)
    if held:
        print(" CAVEAT: edges above are entry-time (stale). The live re-price")
        print(" (--live) is what tells you what's enterable NOW. Predictive legs")
        print(" are a proven null; do not add one. Run --accounting for the proof.")
    else:
        print(" Predictive legs are a proven null; do not add one. --accounting for proof.")

if __name__ == "__main__":
    main()
