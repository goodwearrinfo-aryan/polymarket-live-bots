#!/usr/bin/env python3
"""Read-only honest re-mark of scalp_lab closed trades.

WHY: historical closed trades in scalp_lab_state.json were booked BEFORE the
target-overshoot fix (scalp_lab.py lines ~400-411). They captured the full
gap past the take-profit as profit, inflating winners while stops booked the
full adverse move. This script re-applies the limit-fill cap that current
source enforces, WITHOUT mutating the live state file (the daemon owns it).

Run: python3 honest_report.py
"""
import json, glob, os

HALF = 0.01  # half of assumed_spread (0.02)

# target-mid rule per strategy — mirrors scalp_lab.py exit cap logic
def target_mid(strat, entry_mid):
    rules = {
        "dip":      0.49,                 # absolute YES level (dip_sell_target)
        "scalp":    entry_mid + 0.06,     # scalp_gain (FIX 2026-06-07: was 0.02)
        "fade":     entry_mid + 0.10,     # fade_gain
        "fastfade": entry_mid + 0.06,     # fastfade_target (FIX 2026-06-07: was 0.03)
        "allin":    entry_mid + 0.05,     # allin_target
        "momentum": entry_mid + 0.05,     # momentum_gain
        "favyes":   entry_mid + 0.08,     # favyes_gain
        "coinflip": entry_mid + 0.05,     # coinflip_gain
        "midfade":  entry_mid + 0.05,     # midfade_gain
        "truefade": entry_mid + 0.10, "deepfade": entry_mid + 0.10,
        "nearresfade": entry_mid + 0.10,  # nearresfade_gain 0.10 (truefade successor)
        "nsfade":   entry_mid + 0.10, "wangfade": entry_mid + 0.10,  # all _gain 0.10
        "manifold": entry_mid + 0.10, "metaculus": entry_mid + 0.10,
        "endgame":    entry_mid + 0.08,  # endgame_gain
        "lowliq":     entry_mid + 0.10,  # lowliq_gain
        "contrarian": entry_mid + 0.08,  # contrarian_gain
        "catpol":     entry_mid + 0.10,  # catpol_gain
        "conviction": 0.80,              # absolute target (conviction_target)
        "moonshot":   entry_mid + 0.35,  # varies by tier but ~+35¢ upside
    }
    if strat not in rules:
        gain = _config_gain(strat)
        if gain is None:
            # moonshot-style legs (abs_target/stop_delta) and retired legs have no
            # <leg>_gain; no cap is the same treatment scalp_lab gives them.
            return entry_mid
        return entry_mid + gain
    return rules[strat]


_CFG_GAINS = None

def _config_gain(strat):
    """Look up <strat>_gain from scalp_lab.py's CONFIG without importing the
    (heavy, daemon-shaped) module. Parsed once, cached."""
    global _CFG_GAINS
    if _CFG_GAINS is None:
        import re
        src = open(os.path.join(os.path.dirname(__file__), "scalp_lab.py"), errors="replace").read()
        _CFG_GAINS = {m.group(1): float(m.group(2))
                      for m in re.finditer(r'"(\w+)_gain":\s*([0-9.]+)', src)}
    return _CFG_GAINS.get(strat)

def gamma_status():
    """Read Mac<->Polymarket reachability from scalp_lab.log's last fetch line.
    This is the right source: scalp_lab runs ON THE MAC, so its 'fetched N
    markets' lines reflect the Mac's real connectivity (a live ping from the
    sandbox would always falsely fail)."""
    import re
    for p in (os.path.join(os.path.dirname(__file__), "scalp_lab.log"),
              *glob.glob("/sessions/*/mnt/polymarket/scalp_lab.log")):
        if os.path.exists(p):
            try:
                txt = open(p, errors="replace").read()
            except Exception:
                continue
            fetches = [int(x) for x in re.findall(r"fetched (\d+) markets", txt)]
            if not fetches:
                return "unknown (no fetch logged yet)"
            n = fetches[-1]
            # Only call it BLOCKED if a *sustained* run of recent fetches is 0.
            # A single 0 is almost always a transient blip (WiFi switch, captive
            # portal, one timed-out cycle) and must NOT trip a false outage alarm.
            window = fetches[-3:]
            sustained_zero = len(window) >= 3 and all(v == 0 for v in window)
            if n > 0:
                return f"OK — last fetch {n} markets (Mac can reach Polymarket)"
            if sustained_zero:
                return ("BLOCKED — last 3 fetches all 0 markets "
                        "(gamma-api unreachable from the Mac)")
            # Most recent is 0 but not a sustained run: degraded, not down.
            last_ok = next((v for v in reversed(fetches) if v > 0), 0)
            return (f"DEGRADED — last fetch 0 but recent cycles OK "
                    f"(most recent non-zero: {last_ok} markets) — likely a transient blip")
    return "unknown (scalp_lab.log not found)"

def find_state():
    for p in ("scalp_lab_state.json",
              os.path.join(os.path.dirname(__file__), "scalp_lab_state.json")):
        if os.path.exists(p):
            return p
    hits = glob.glob("/sessions/*/mnt/polymarket/scalp_lab_state.json")
    if hits:
        return hits[0]
    raise FileNotFoundError("scalp_lab_state.json not found")

STALE_REASONS = ("stale_nodata", "no-data")

def legs_roster(state):
    """Every leg with a book in state — the canonical roster. Any report that
    totals realized P&L must use this (a hardcoded leg list is how the status
    PDF silently under-reported by $232 until 2026-07-03)."""
    return sorted(k for k, v in state.items()
                  if isinstance(v, dict) and ("closed" in v or "open" in v))

def priced_exits(closed):
    """Closed trades with a real outcome — stale no-data exits book $0 and are
    excluded from counts/win-rate (dollar-neutral by construction)."""
    return [t for t in closed if t.get("reason") not in STALE_REASONS]

def honest_pnl(strat, t):
    """Re-mark one closed trade with the limit-fill cap. Read-only."""
    xf, em, sz, ef, r = (t["exit_fill"], t["entry_mid"], t["size"],
                         t["entry_fill"], t["reason"])
    if r == "target":
        xf = min(xf, target_mid(strat, em) - t.get("spread", 0.02) / 2)   # cap at bid(target), per-pos spread
    # stops / time / stale already book the real adverse fill — leave as-is
    return (xf - ef) * sz

def main():
    state = json.load(open(find_state()))

    # ===== HEADLINE: REALIZED P&L from EXIT (closed) trades only =====
    print("=" * 64)
    print("  REALIZED P&L  —  EXIT (closed) trades only.  This is the number.")
    print("  Open positions are UNREALIZED and excluded; shown separately below.")
    print("=" * 64)
    # total$ = summed realized P&L (what TOTAL/grand-total use); $/exit = mean per
    # exit (sum/n) — the gate metric, so a low-n leg can't look n× more profitable.
    print(f"{'leg':14} {'exits':>5} {'total$':>9} {'$/exit':>9} {'win%':>6}  flag")
    g_real = 0.0
    open_ctx, leaders, zero = {}, [], []
    CONTROLS = {"allin", "coinflip", "coindown"}
    # full roster: every leg that has a book in state (not a hardcoded legacy list)
    legs = legs_roster(state)
    for strat in legs:
        c = state.get(strat, {}).get("closed", [])
        o = state.get(strat, {}).get("open", [])
        open_ctx[strat] = len(o)
        # Exclude stale_nodata: closed at entry_fill (pnl=0) — not a real trade outcome
        priced = priced_exits(c)
        stale_n = len(c) - len(priced)
        if not priced:
            zero.append(strat)          # no priced exits yet — summarized below, not a row
            continue
        new = sum(honest_pnl(strat, t) for t in priced)
        wins = sum(1 for t in priced if honest_pnl(strat, t) > 1e-9)
        wr = wins / len(priced) * 100
        g_real += new
        leaders.append((strat, new, len(priced)))
        flag = ""
        stale_tag = f"  [{stale_n} stale excl]" if stale_n else ""
        if strat in CONTROLS and new < 0:
            flag = "control negative -> accounting OK"
        elif len(priced) < 10:
            flag = "too few exits (<10) — noise"
        elif new <= 0 and wr >= 60:
            flag = "WIN-RATE TRAP: high win%, negative $"
        print(f"{strat:14} {len(priced):>5} {new:>+9.3f} {new/len(priced):>+9.4f} "
              f"{wr:>5.1f}%  {flag}{stale_tag}")
    print("-" * 72)
    print(f"{'TOTAL':14} {'':>5} {g_real:>+9.3f} {'':>9}  <-- total realized $ across all legs "
          f"(per-leg $/exit is the gate metric)")

    # ===== SECONDARY: open positions (unrealized, NOT in the number above) =====
    print("\n  open positions still running (unrealized — not counted):")
    opens = sorted(((k, v) for k, v in open_ctx.items() if v), key=lambda x: -x[1])
    print("   ", "  ".join(f"{k}={v}" for k, v in opens) or "none")
    if zero:
        print(f"\n  {len(zero)} leg(s) with no priced exits yet (omitted above): "
              + ", ".join(zero))
    # crown the best NON-CONTROL leg with >=10 priced exits
    qualified = [x for x in leaders if x[2] >= 10 and x[0] not in CONTROLS]
    if qualified:
        best = max(qualified, key=lambda x: x[1])
        print(f"\n  Ahead on realized $ (>=10 exits): {best[0]} ({best[1]:+.3f} over {best[2]} exits).")
    else:
        print("\n  No (non-control) leg has >=10 exits yet — too early to crown a winner.")
    print(f"\n  Network (Mac->Polymarket): {gamma_status()}")
    print("  Honest = target exits capped at the limit fill (current source rule).")

if __name__ == "__main__":
    main()
