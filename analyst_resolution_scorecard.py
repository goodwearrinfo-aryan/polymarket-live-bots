#!/usr/bin/env python3
"""analyst_resolution_scorecard.py — resolution-verified scoreboard for the GATED analyst book.

NOTE: distinct from analyst_scorecard.py (that one is the weekly/monthly edge-tracker
dashboard → scorecard.plist/md). THIS file is the June-30 milestone: it marks the 5
sourced positions in analyst_positions.json to RESOLUTION and scores realized P&L + Brier.

Canonical book = analyst_positions.json (sourced, adversarially-gated). Each daily run:
  1. For every still-open position, check resolution via the CLOB settled-price path
     (resolved markets age out of gamma, but clob.polymarket.com keeps 0/1 token prices —
     same fallback the algo legs use as fetch_price_clob).
  2. On a NEWLY-resolved position: book realized P&L + Brier, persist, and alert it LOUDLY
     via iMessage + WhatsApp (Aryan: failures must be loud, never miss a resolution).
  3. Milestone scoreboard once on/after 2026-06-30, FINAL once all 5 resolve. Headline test:
     ANALYST Brier vs MARKET Brier — did the sourced true_prob beat the market's entry price?
     That, not win rate, is the analyst edge signal.

PAPER research — read-only on markets. No orders, no keys, no funds moved.

  python3 analyst_resolution_scorecard.py            # daily run (launchd entrypoint)
  python3 analyst_resolution_scorecard.py --dry      # compute + print, NO alerts (safe live test)
  python3 analyst_resolution_scorecard.py --force    # send the scoreboard now regardless of flags
"""
import json, os, sys, subprocess, urllib.request
from datetime import datetime, timezone, date

BASE  = os.path.dirname(os.path.abspath(__file__))
BOOK  = os.path.join(BASE, "analyst_positions.json")
STATE = os.path.join(BASE, "analyst_resolution_scorecard_state.json")
TARGETS = ["krisharyan@icloud.com", "+918449447444"]
MILESTONE = date(2026, 6, 30)

DRY   = "--dry" in sys.argv
FORCE = "--force" in sys.argv


# ── CLOB resolution (settled markets pin the YES token to ~0 or ~1) ──────────
def clob_market(cid):
    try:
        req = urllib.request.Request(f"https://clob.polymarket.com/markets/{cid}",
                                     headers={"User-Agent": "analyst-scorecard/1.0"})
        return json.loads(urllib.request.urlopen(req, timeout=12).read())
    except Exception:
        return None


def resolution(cid):
    """(resolved_bool, outcome_yes 1/0/None, yes_price)."""
    d = clob_market(cid)
    if not d:
        return (False, None, None)
    toks = d.get("tokens") or []
    yes = next((t for t in toks if str(t.get("outcome", "")).lower() == "yes"),
               toks[0] if toks else None)
    yp = None
    if yes and yes.get("price") is not None:
        try: yp = float(yes["price"])
        except Exception: yp = None
    closed = bool(d.get("closed"))
    if closed or (yp is not None and (yp <= 0.02 or yp >= 0.98)):
        if yp is None:
            return (True, None, yp)
        return (True, 1 if yp >= 0.5 else 0, yp)
    return (False, None, yp)


# ── state ────────────────────────────────────────────────────────────────────
def load_state():
    if os.path.exists(STATE):
        try:
            s = json.load(open(STATE))
        except Exception:
            s = {}
    else:
        s = {}
    s.setdefault("resolved", {})        # market_id -> booked record
    s.setdefault("milestone_sent", False)
    s.setdefault("final_sent", False)
    return s


def save_state(s):
    if DRY:                 # --dry is strictly read-only: never consume a resolution event
        return
    tmp = STATE + ".tmp"
    json.dump(s, open(tmp, "w"), indent=2)
    os.replace(tmp, STATE)


# ── alerts (iMessage + WhatsApp, both fail-soft) ─────────────────────────────
def imsg(body):
    if DRY:
        print("[dry] would alert:\n" + body + "\n")
        return True
    osa = ('on run argv\ntell application "Messages"\n'
           'set s to 1st service whose service type = iMessage\n'
           'send (item 2 of argv) to buddy (item 1 of argv) of s\nend tell\nend run')
    ok = True
    for t in TARGETS:
        r = subprocess.run(["osascript", "-e", osa, t, body],
                           capture_output=True, text=True, timeout=30)
        ok = ok and r.returncode == 0
    # Also push to WhatsApp (fail-soft — never blocks iMessage or crashes).
    try:
        import wa_alert
        wa_alert.send_whatsapp(body)
    except Exception:
        pass
    return ok


def book_position(p, oy, yp):
    """Realized P&L + Brier for one resolved position."""
    side = p["side"]
    entry_cost = float(p["entry_price"])          # cost of OUR side at entry
    win = (oy == 1 and side == "YES") or (oy == 0 and side == "NO")
    payoff = 1.0 if win else 0.0
    roi = (payoff - entry_cost) / entry_cost if entry_cost > 0 else 0.0
    # Brier on the YES outcome: analyst's true_prob vs the market's entry YES price.
    brier_analyst = (float(p["true_prob"]) - oy) ** 2
    brier_market  = (float(p["entry_yes"]) - oy) ** 2
    # gate status (vet_book.py writes p["gate"]). True = survived the panel = part of the
    # real gated analyst track; False/None = booked un-vetted (don't let it pad the edge).
    gated = (p.get("gate") or {}).get("survived")
    return {
        "q": p["q"], "side": side, "entry_cost": round(entry_cost, 4),
        "true_prob": p["true_prob"], "entry_yes": p["entry_yes"],
        "confidence": p.get("confidence"), "resolves": p.get("resolves"),
        "outcome_yes": oy, "yes_price": yp, "win": win,
        "roi": round(roi, 4), "pnl_per_dollar": round(roi if win else -1.0, 4),
        "brier_analyst": round(brier_analyst, 4), "brier_market": round(brier_market, 4),
        "gated": gated,
        "booked_at": datetime.now(timezone.utc).isoformat(),
    }


def scoreboard(book, state, label):
    recs = list(state["resolved"].values())
    n = len(recs)
    total = len(book)
    if n == 0:
        return f"📊 ANALYST SCOREBOARD ({label})\n  0/{total} resolved yet — nothing to score."
    wins = sum(1 for r in recs if r["win"])
    roi_equal = sum(r["pnl_per_dollar"] for r in recs) / n      # equal $1 per bet
    ba = sum(r["brier_analyst"] for r in recs) / n
    bm = sum(r["brier_market"] for r in recs) / n
    lines = [f"📊 ANALYST SCOREBOARD ({label}) — {n}/{total} resolved", ""]
    for r in recs:
        lines.append(f"{'✅' if r['win'] else '❌'} {r['side']} {r['q'][:46]}")
        lines.append(f"    ROI {r['roi']*100:+.0f}%  conf {r['confidence']}/10  "
                     f"(YES→{r['yes_price']:.2f})")
    edge = "ANALYST BEAT MARKET ✅" if ba < bm else ("market beat analyst ❌" if ba > bm else "tie")
    lines += [
        "", f"hit rate: {wins}/{n} ({wins/n*100:.0f}%)",
        f"equal-stake ROI: {roi_equal*100:+.1f}% / bet",
        f"Brier  analyst {ba:.3f}  vs  market {bm:.3f}  (baseline 0.25) → {edge}",
        "", ("EDGE SIGNAL: analyst true_prob carried information the market price did not."
             if ba < bm else
             "NO EDGE YET: analyst forecasts did not beat the market's own price."),
    ]

    # GATED COHORT: the milestone verdict must rest only on positions that PASSED the
    # panel (gate.survived True). Un-vetted bookings (gated False/None) are reported for
    # transparency but excluded from the edge claim — else an ungated lucky win pads it.
    gated_recs = [r for r in recs if r.get("gated") is True]
    ungated = [r for r in recs if r.get("gated") is not True]
    if gated_recs:
        gn = len(gated_recs)
        gw = sum(1 for r in gated_recs if r["win"])
        groi = sum(r["pnl_per_dollar"] for r in gated_recs) / gn
        gba = sum(r["brier_analyst"] for r in gated_recs) / gn
        gbm = sum(r["brier_market"] for r in gated_recs) / gn
        gedge = "BEAT MARKET ✅" if gba < gbm else ("market won ❌" if gba > gbm else "tie")
        lines += ["", f"── GATED TRACK ONLY ({gn}/{n} resolved were panel-survivors) ──",
                  f"hit {gw}/{gn} ({gw/gn*100:.0f}%) · ROI {groi*100:+.1f}%/bet · "
                  f"Brier a {gba:.3f} vs m {gbm:.3f} → {gedge}",
                  "⟵ THIS is the analyst-track verdict; the all-bookings number above includes un-vetted bets."]
    if ungated:
        lines.append(f"  ({len(ungated)} resolved booking(s) were UN-VETTED — excluded from the edge claim)")

    # Effective-n correction — correlated positions are NOT independent draws, so the
    # Brier-beat verdict above can be one geopolitical outcome wearing N hats. Reuse the
    # cluster-block bootstrap (analyst_correlation) over realized $/bet to qualify it.
    try:
        import analyst_correlation
        synth = [{"status": "settled", "realized_pnl": r["pnl_per_dollar"], "q": r["q"]}
                 for r in recs]
        bs = analyst_correlation.cluster_block_bootstrap_ci(synth)
        if bs.get("n_settled", 0) >= 1:
            lines += ["", f"effective draws: ~{bs.get('effective_n', '?')} independent of {bs['n_settled']} "
                          f"resolved ({bs.get('n_clusters', '?')} driver cluster(s))"]
            if bs.get("degenerate"):
                lines.append(f"  ⚠️ {bs['note']} — '{edge}' rests on ~1 independent bet; NOT a generalizable edge yet")
            else:
                lines.append(f"  honest CI on $/bet [{bs['cluster_ci'][0]:+.3f}, {bs['cluster_ci'][1]:+.3f}] "
                             f"(naive [{bs['naive_ci'][0]:+.3f}, {bs['naive_ci'][1]:+.3f}], "
                             f"{bs['inflation']}× wider for correlation)")
                if bs.get("effective_n", 99) < 5:
                    lines.append("  ⚠️ low effective-n: treat the Brier verdict as PROVISIONAL until "
                                 "draws span more independent drivers")
    except Exception as _e:
        lines.append(f"\n(effective-n correction unavailable: {_e})")

    if n < total:
        lines.append(f"\n({total-n} still open — final scoreboard fires when all resolve.)")
    return "\n".join(lines)


def main():
    if not os.path.exists(BOOK):
        print("no analyst_positions.json"); return
    book = json.load(open(BOOK)).get("positions", [])
    state = load_state()

    newly = []
    for p in book:
        mid = p["market_id"]
        if mid in state["resolved"]:
            continue
        resolved, oy, yp = resolution(mid)
        if not resolved or oy is None:
            continue
        rec = book_position(p, oy, yp)
        state["resolved"][mid] = rec
        newly.append(rec)
    save_state(state)

    # loud per-resolution alerts
    for r in newly:
        imsg(f"📊 ANALYST POSITION RESOLVED\n{r['side']} {r['q'][:55]}\n"
             f"{'WIN ✅' if r['win'] else 'LOSS ❌'}  ROI {r['roi']*100:+.0f}%  "
             f"(YES settled {r['yes_price']:.2f})\n"
             f"Brier a/m {r['brier_analyst']:.3f}/{r['brier_market']:.3f}")

    today = datetime.now(timezone.utc).date()
    all_resolved = len(state["resolved"]) >= len(book) and len(book) > 0

    sent_label = None
    if FORCE:
        sent_label = "manual"
        print(scoreboard(book, state, "manual"))
        imsg(scoreboard(book, state, "manual"))
    elif all_resolved and not state["final_sent"]:
        sent_label = "FINAL"
        body = scoreboard(book, state, "FINAL")
        print(body); imsg(body)
        state["final_sent"] = True; state["milestone_sent"] = True
        save_state(state)
    elif today >= MILESTONE and not state["milestone_sent"]:
        sent_label = "June-30 milestone"
        body = scoreboard(book, state, "June-30 milestone")
        print(body); imsg(body)
        state["milestone_sent"] = True
        save_state(state)

    print(json.dumps({
        "resolved": len(state["resolved"]), "total": len(book),
        "newly_resolved": len(newly), "scoreboard_sent": sent_label,
        "milestone_sent": state["milestone_sent"], "final_sent": state["final_sent"],
    }, indent=2))


if __name__ == "__main__":
    main()
