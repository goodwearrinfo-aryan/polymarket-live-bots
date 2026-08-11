#!/usr/bin/env python3
"""
hourly_pnl_alert.py — Send P&L summary via iMessage every hour.
Called by launchd / cron. Reads scalp_lab_state.json directly.
"""
import json, subprocess, os, sys, time
from datetime import datetime, timezone

STATE_FILE   = os.path.join(os.path.dirname(__file__), "scalp_lab_state.json")
TARGETS      = ["krisharyan@icloud.com", "+918449447444"]

# Legs to highlight in the summary (signal legs only — controls excluded)
SIGNAL_LEGS  = [
    "nearres", "nearresfade",
    "nohappen", "longshortbias", "clobimbal", "polyflup",
    "noevent", "newsno", "btc15no", "weatherno", "ytbuzz",
    "sportres", "psconfirm", "quietfade", "panel", "tasignal", "nearrestitle",
    "fogbuy", "crashbuy", "polvol", "latefade",
    "truefade",   # killed, but 20 open draining — keep visible until empty
]
CONTROL_LEGS = []

def load_state():
    with open(STATE_FILE) as f:
        return json.load(f)

# P&L baseline (paper-balance reset 2026-06-10): reports show P&L SINCE reset.
# History/gate counts stay cumulative — only dollars are re-zeroed.
BASELINE_FILE = os.path.join(os.path.dirname(__file__), "pnl_baseline.json")
def load_baseline():
    try:
        with open(BASELINE_FILE) as f:
            return json.load(f).get("baseline", {})
    except Exception:
        return {}

def arb_resolved_count():
    """Combined resolved exits across the structural-arb track (basketlock + dataarb-REAL +
    monoarb) — the live +EV candidates accumulating toward the n>=30 gate. nearres is
    retracted/dead, so the gate line now tracks THIS, not nearres. Fail-soft → 0 on any error."""
    here = os.path.dirname(__file__)
    n = 0
    for fn, real_only in (("basketlock_state.json", False), ("dataarb_state.json", True),
                          ("monoarb_state.json", False)):
        try:
            res = json.load(open(os.path.join(here, fn))).get("resolved", [])
            n += sum(1 for p in res if (not real_only or p.get("kind") == "real"))
        except Exception:
            pass
    return n
_BASELINE = load_baseline()

def leg_stats(book, leg=None):
    closed  = book.get("closed", [])
    open_   = book.get("open",   [])
    priced  = [r for r in closed if r.get("pnl_usdc") is not None]
    wins    = [r for r in priced if r["pnl_usdc"] > 0]
    pnl     = sum(r["pnl_usdc"] for r in priced)
    wr      = len(wins) / len(priced) * 100 if priced else 0
    avg_w   = sum(r["pnl_usdc"] for r in wins) / len(wins) if wins else 0
    losses  = [r for r in priced if r["pnl_usdc"] < 0]
    avg_l   = abs(sum(r["pnl_usdc"] for r in losses) / len(losses)) if losses else 0
    rr      = avg_w / avg_l if avg_l else 0
    pnl    -= _BASELINE.get(leg, 0.0)   # paper-balance reset: dollars since reset
    return dict(n=len(priced), open=len(open_), wr=wr, pnl=pnl, avg_w=avg_w, avg_l=avg_l, rr=rr)

def send_imessage(body: str):
    """Send via Messages.app. Returns True if at least one target's osascript succeeded.
    NB: under launchd the osascript→Messages AppleEvent can be denied (no Automation grant /
    no GUI session) — we now CAPTURE that rc instead of swallowing it, so the log tells the
    truth instead of always printing 'Sent.'."""
    esc = body.replace('"', "'").replace("\n", "\\n")
    sent_any = False
    for target in TARGETS:
        script = (
            f'tell application "Messages"\n'
            f'  set s to 1st service whose service type = iMessage\n'
            f'  set b to buddy "{target}" of s\n'
            f'  send "{esc}" to b\n'
            f'end tell'
        )
        try:
            r = subprocess.run(["osascript", "-e", script],
                               capture_output=True, text=True, timeout=10)
            if r.returncode == 0:
                sent_any = True
            else:
                print(f"iMessage NOT sent ({target}): osascript rc={r.returncode} "
                      f"{(r.stderr or '').strip()[:140]}", file=sys.stderr)
        except Exception as e:
            print(f"iMessage error ({target}): {e}", file=sys.stderr)
    return sent_any
    # Also push to WhatsApp via the OpenWA gateway (fail-soft: a down/unlinked
    # gateway never blocks the iMessage path above or crashes the alert).
    try:
        import wa_alert
        ok, _ = wa_alert.send_whatsapp(body)
        if not ok:
            print("WhatsApp send skipped/failed (gateway down or unlinked)", file=sys.stderr)
    except Exception as e:
        print(f"WhatsApp error: {e}", file=sys.stderr)

BOOK_FILE = os.path.join(os.path.dirname(__file__), "analyst_positions.json")
GAMMA = "https://gamma-api.polymarket.com/markets"

def _live_yes(market_id):
    """Current YES price for a market id; None on any failure (fail-soft)."""
    import urllib.request
    for q in (f"?condition_ids={market_id}", f"?id={market_id}"):
        try:
            req = urllib.request.Request(GAMMA + q, headers={"User-Agent": "pnl-alert/1.0"})
            d = json.loads(urllib.request.urlopen(req, timeout=8).read())
            m = d[0] if isinstance(d, list) and d else (d if isinstance(d, dict) else None)
            if m and m.get("outcomePrices"):
                return float(json.loads(m["outcomePrices"])[0])
        except Exception:
            continue
    return None

def analyst_book_lines():
    """Mark the research-analyst book to live Polymarket prices. Returns (lines, total).
    Entirely fail-soft: any error → ([], 0.0) so the algo P&L alert still sends."""
    try:
        if not os.path.exists(BOOK_FILE):
            return [], 0.0
        positions = json.loads(open(BOOK_FILE).read()).get("positions", [])
    except Exception:
        return [], 0.0
    lines, tot = [], 0.0
    for p in positions:
        if p.get("settled"):
            continue
        try:
            ly = _live_yes(p["market_id"])
            if p["side"] == "YES":
                cur = ly if ly is not None else p["entry_price"]
            else:
                cur = (1 - ly) if ly is not None else p["entry_price"]
            mtm = cur - p["entry_price"]
            tot += mtm
            mark = "?" if ly is None else f"{ly:.2f}"
            lines.append(f"  {p['side']} {p['q'][:30]} {p['edge_pct']:.0f}pt "
                         f"{mtm:+.3f} (YES={mark}) res {p['resolves'][5:]}")
        except Exception:
            continue
    return lines, tot

FUTURES_STATE = os.path.expanduser("~/Documents/futures-bot/paper_state.json")

def futures_bot_lines():
    """Read the standalone paper futures bot's main + runner books. Returns (lines, None).
    Fully fail-soft: missing file or any error → ([], ) so the Polymarket alert still sends.
    The runner leg holds entries to +15%/−5%/72h; the main ATR-exit book is its control."""
    try:
        if not os.path.exists(FUTURES_STATE):
            return []
        s = json.loads(open(FUTURES_STATE).read())
    except Exception:
        return []
    try:
        m_eq    = s.get("equity", 1000.0)
        m_n     = len(s.get("closed", []))
        m_open  = len(s.get("positions", {}))
        r_eq    = s.get("runner_equity", 1000.0)
        r_cl    = s.get("runner_closed", [])
        r_n     = len(r_cl)
        r_open  = len(s.get("runner_positions", {}))
        r_pnl   = sum(t.get("pnl", 0) for t in r_cl)
        l_cl    = s.get("lev_closed", [])
        l_n     = len(l_cl)
        l_open  = len(s.get("lev_positions", {}))
        l_pnl   = sum(t.get("pnl", 0) for t in l_cl)
        return [
            "─────────────────────",
            "🤖 FUTURES BOT (paper, Binance perp)",
            f"  main: n={m_n} op={m_open} eq=${m_eq:.2f} ({m_eq-1000:+.2f})",
            f"  runner(+15%/-5%): n={r_n} op={r_open} pnl=${r_pnl:+.2f} "
            + ("🔄 <30" if r_n < 30 else "✅ n≥30 → gate-check"),
            f"  lev(conv-sized ≤3x): n={l_n} op={l_open} pnl=${l_pnl:+.2f} "
            + ("🔄 <30" if l_n < 30 else "✅ n≥30 vs runner"),
        ]
    except Exception:
        return []

def build_message(state):
    now = datetime.now(timezone.utc).strftime("%H:%M UTC")
    lines = [f"📊 Polymarket P&L · {now}" + ("  (since reset 06-10)" if _BASELINE else "")]
    lines.append("─────────────────────")

    # ── Signal legs ──
    sig_pnl   = 0
    sig_lines = []
    for leg in SIGNAL_LEGS:
        if leg not in state:
            continue
        s = leg_stats(state[leg], leg)
        if s["n"] == 0 and s["open"] == 0:
            continue
        sig_pnl += s["pnl"]
        wr_str = f"{s['wr']:.0f}%" if s["n"] else "—"
        rr_str = f"{s['rr']:.1f}" if s["avg_l"] else "∞"
        # Status emoji vs 80% win / 1:3 R:R target
        if s["n"] >= 5:
            ok = "✅" if (s["wr"] >= 80 and s["rr"] >= 3.0) else ("⚠️" if s["pnl"] > 0 else "❌")
        else:
            ok = "🔄"  # still accumulating
        sig_lines.append(
            f"{ok} {leg}: n={s['n']} op={s['open']} "
            f"wr={wr_str} rr={rr_str} pnl={s['pnl']:+.2f}"
        )

    lines.append("🎯 SIGNAL LEGS")
    lines.extend(sig_lines if sig_lines else ["  (no data yet)"])
    lines.append(f"  → Total: ${sig_pnl:+.2f}")

    # ── Controls (must be negative) ──
    lines.append("─────────────────────")
    lines.append("🔒 CONTROLS (must be ❌)")
    ctrl_pnl = 0
    for leg in CONTROL_LEGS:
        if leg not in state:
            continue
        s = leg_stats(state[leg], leg)
        ctrl_pnl += s["pnl"]
        flag = "✅ MARKING BUG!" if s["pnl"] > 0 else "❌"
        lines.append(f"  {flag} {leg}: n={s['n']} pnl={s['pnl']:+.2f}")

    # ── Grand total ──
    lines.append("─────────────────────")
    grand = sig_pnl + ctrl_pnl
    lines.append(f"💰 Grand: ${grand:+.2f}")

    # ── arb-track gate progress (the live +EV path; nearres is retracted/dead) ──
    arb_n = arb_resolved_count()
    lines.append(f"🏁 arb-track gate: {arb_n}/30 combined exits ({max(0, 30 - arb_n)} to first verdict)")

    # ── Analyst book (the only sourced-edge track; settles at resolution) ──
    a_lines, a_tot = analyst_book_lines()
    if a_lines:
        lines.append("─────────────────────")
        lines.append("🔬 ANALYST BOOK (mark-to-live, paper)")
        lines.extend(a_lines)
        lines.append(f"  → MTM: ${a_tot:+.3f} · real verdict at resolution")

    # ── Futures bot (separate paper book; main + runner A/B) ──
    lines.extend(futures_bot_lines())

    return "\n".join(lines)

if __name__ == "__main__":
    try:
        state = load_state()
        msg   = build_message(state)
        print(msg)   # also log to stdout for launchd capture
        sent = send_imessage(msg)
        print("Sent." if sent else "iMessage NOT delivered (osascript denied under launchd?)",
              file=sys.stderr)
        sys.exit(0)   # always exit 0 on a completed run — delivery success is in the log, not the rc
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
