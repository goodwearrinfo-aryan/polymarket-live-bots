#!/usr/bin/env python3
"""
win_tracker.py  –  Live Win-Ratio Dashboard for the Polymarket Copy-Trading Bot
Reads trade_log.json and shows a detailed P&L + win-rate breakdown.
Run once for a snapshot, or with --watch for a live updating view.
"""

import json, time, sys, os, math
from datetime import datetime, timezone
from collections import defaultdict

TRADE_LOG = os.path.join(os.path.dirname(__file__), "trade_log.json")
REFRESH   = 30  # seconds between refreshes in --watch mode

# ── ANSI colours ─────────────────────────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
RESET  = "\033[0m"

def clr(text, colour): return f"{colour}{text}{RESET}"

# ── helpers ───────────────────────────────────────────────────────────────────
def load_trades():
    """Load trades from trade_log.json.
    Supports both formats:
      - {"open": [...], "closed": [...]}   (bot format)
      - [...]                              (flat list)
    Returns a flat list with 'status' injected: 'open' or 'closed'.
    """
    if not os.path.exists(TRADE_LOG):
        return []
    with open(TRADE_LOG) as f:
        raw = json.load(f)

    if isinstance(raw, dict):
        open_trades   = raw.get("open", [])
        closed_trades = raw.get("closed", [])
        for t in open_trades:
            t.setdefault("_status", "open")
        for t in closed_trades:
            t.setdefault("_status", "closed")
        return open_trades + closed_trades
    elif isinstance(raw, list):
        return raw
    return []

def pct(n, d):
    return (n / d * 100) if d else 0.0

def bar(ratio, width=20):
    filled = round(ratio * width)
    return clr("█" * filled, GREEN) + clr("░" * (width - filled), DIM)

def trade_pnl(t):
    """
    Return (is_closed, pnl_pct, pnl_dollars) for a trade dict.
    Supports both field naming styles from trade_log.json:
      entry_price, size / cost_usdc, exit_price, current_price
    """
    entry   = t.get("entry_price", 0.5)
    exit_p  = t.get("exit_price")
    # cost_usdc is the actual dollars spent; size is token count
    cost    = t.get("cost_usdc", t.get("size_usd", t.get("size", 0.0)))
    side    = t.get("side", "BUY").upper()

    # If no exit yet, use current_price or entry as estimate
    current = t.get("current_price", entry)

    if exit_p is not None or t.get("_status") == "closed":
        close     = exit_p if exit_p is not None else current
        is_closed = True
    else:
        close     = current
        is_closed = False

    if side == "BUY":
        pnl_pct = (close - entry) / entry * 100 if entry else 0
    else:  # SELL = we profit when price goes down
        pnl_pct = (entry - close) / entry * 100 if entry else 0

    # P&L in dollars = cost × % move (cost_usdc is what we risked)
    pnl_dollars = cost * (pnl_pct / 100)
    return is_closed, pnl_pct, pnl_dollars

def win_loss(pnl_pct):
    if pnl_pct > 0:  return "WIN"
    if pnl_pct < 0:  return "LOSS"
    return "EVEN"

# ── main report ───────────────────────────────────────────────────────────────
def render(trades):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = []

    def h(s): lines.append(clr(s, BOLD + CYAN))
    def ln(s=""): lines.append(s)

    # ── Header ────────────────────────────────────────────────────────────────
    h("╔══════════════════════════════════════════════════════════════════╗")
    h(f"║  📊  POLYMARKET BOT  –  WIN-RATIO DASHBOARD        {now}  ║")
    h("╚══════════════════════════════════════════════════════════════════╝")
    ln()

    if not trades:
        ln(clr("  No trades found in trade_log.json yet.", YELLOW))
        return "\n".join(lines)

    # Separate paper vs live
    paper_trades = [t for t in trades if t.get("dry")]
    live_trades  = [t for t in trades if not t.get("dry")]

    for label, tlist in [("📄 PAPER TRADING", paper_trades), ("💰 LIVE TRADING", live_trades)]:
        if not tlist:
            continue

        h(f"  {label}")
        ln(clr("  " + "─" * 64, DIM))

        closed_trades, open_trades = [], []
        for t in tlist:
            is_closed, pnl_pct, pnl_dollars = trade_pnl(t)
            if is_closed:
                closed_trades.append((t, pnl_pct, pnl_dollars))
            else:
                open_trades.append((t, pnl_pct, pnl_dollars))

        all_with_pnl = closed_trades + open_trades
        total_count  = len(all_with_pnl)
        total_dollars= sum(p[2] for p in all_with_pnl)

        # ── Overall win rate (closed trades only) ─────────────────────────
        if closed_trades:
            wins   = sum(1 for _, p, _ in closed_trades if p > 0)
            losses = sum(1 for _, p, _ in closed_trades if p < 0)
            evens  = len(closed_trades) - wins - losses
            wr     = pct(wins, len(closed_trades))

            ln(f"  Overall Win Rate  │ {bar(wins/len(closed_trades))}  "
               f"{clr(f'{wr:.1f}%', GREEN if wr >= 50 else RED)}  "
               f"({wins}W / {losses}L / {evens}E  of {len(closed_trades)} closed)")
        else:
            ln(f"  Overall Win Rate  │ {clr('No closed trades yet', YELLOW)}")

        # ── Unrealised on open positions ──────────────────────────────────
        if open_trades:
            unreal = sum(p for _, p, _ in open_trades)
            unreal_d = sum(d for _, _, d in open_trades)
            colour = GREEN if unreal_d >= 0 else RED
            ln(f"  Open Positions    │ {len(open_trades)} trades  │  "
               f"Unrealised {clr(f'{unreal_d:+.2f}$', colour)}  "
               f"({clr(f'{unreal/len(open_trades):+.1f}%', colour)} avg)")

        # ── Total P&L ─────────────────────────────────────────────────────
        colour = GREEN if total_dollars >= 0 else RED
        ln(f"  Total P&L (est.)  │ {clr(f'{total_dollars:+.2f}$', colour)}"
           f"  across {total_count} position(s)")

        # ── By Category ──────────────────────────────────────────────────
        ln()
        ln(clr("  By Category:", BOLD))
        cat_data = defaultdict(list)
        for t, pnl_p, pnl_d in all_with_pnl:
            cat_data[t.get("category", "other")].append((pnl_p, pnl_d))

        for cat in sorted(cat_data):
            rows = cat_data[cat]
            n    = len(rows)
            w    = sum(1 for p, _ in rows if p > 0)
            tot  = sum(d for _, d in rows)
            wr_c = pct(w, n)
            colour = GREEN if tot >= 0 else RED
            ln(f"    {cat:<12}  {w}W/{n-w}L  win={clr(f'{wr_c:.0f}%',GREEN if wr_c>=50 else RED)}"
               f"  pnl={clr(f'{tot:+.2f}$', colour)}")

        # ── By Signal Type ────────────────────────────────────────────────
        ln()
        ln(clr("  By Signal Type:", BOLD))
        sig_data = defaultdict(list)
        for t, pnl_p, pnl_d in all_with_pnl:
            sig_data[t.get("strategy", t.get("signal", "consensus"))].append((pnl_p, pnl_d))

        for sig in sorted(sig_data):
            rows = sig_data[sig]
            n    = len(rows)
            w    = sum(1 for p, _ in rows if p > 0)
            tot  = sum(d for _, d in rows)
            wr_s = pct(w, n)
            colour = GREEN if tot >= 0 else RED
            ln(f"    {sig:<14}  {w}W/{n-w}L  win={clr(f'{wr_s:.0f}%',GREEN if wr_s>=50 else RED)}"
               f"  pnl={clr(f'{tot:+.2f}$', colour)}")

        # ── Individual Trade List ─────────────────────────────────────────
        ln()
        ln(clr("  Individual Trades:", BOLD))
        ln(clr(f"  {'#':<3} {'Market':<42} {'Side':<5} {'Entry':>6} {'Now':>6} {'P&L':>7}  {'Status'}", DIM))
        ln(clr("  " + "─" * 80, DIM))

        for i, (t, pnl_p, pnl_d) in enumerate(all_with_pnl, 1):
            title   = t.get("title", t.get("market_title", t.get("condition_id","?")))[:42]
            side    = t.get("side", "BUY")
            entry   = t.get("entry_price", 0)
            is_cls  = t.get("exit_price") is not None or t.get("_status") == "closed"
            cur     = t.get("exit_price", t.get("current_price", entry))
            conf    = t.get("confidence", 0)
            days    = t.get("days_to_close", 0)
            status  = clr("CLOSED", DIM) if is_cls else clr("OPEN  ", YELLOW)
            wl      = win_loss(pnl_p)
            pnl_col = GREEN if pnl_p > 0 else (RED if pnl_p < 0 else RESET)
            wl_tag  = clr(f"[{wl}]", pnl_col)
            ln(f"  {i:<3} {title:<44} {side:<5} {entry:.2f}→{cur:.2f}  "
               f"{clr(f'{pnl_p:+5.1f}%', pnl_col)}  {status} {wl_tag}"
               f"  {clr(f'conf={conf:.2f} {days:.0f}d', DIM)}")

        ln()

    # ── Kelly Performance Insight ─────────────────────────────────────────────
    all_closed = [(t, p, d) for t in trades for (ic, p, d) in [trade_pnl(t)] if ic]
    if len(all_closed) >= 3:
        h("  📐  Kelly Performance Insight")
        ln(clr("  " + "─" * 64, DIM))
        wins_all = [p for _, p, _ in all_closed if p > 0]
        loss_all = [abs(p) for _, p, _ in all_closed if p < 0]
        wr_all   = pct(len(wins_all), len(all_closed))
        avg_win  = sum(wins_all) / len(wins_all) if wins_all else 0
        avg_loss = sum(loss_all) / loss_all.__len__() if loss_all else 0
        b        = avg_win / avg_loss if avg_loss else 0
        q        = 1 - wr_all / 100
        kelly    = (wr_all/100 - q / b) if b else 0
        kelly    = max(0, min(kelly, 0.25))   # cap at 25%
        ln(f"  Win Rate: {wr_all:.1f}%   Avg Win: {avg_win:.1f}%   Avg Loss: {avg_loss:.1f}%")
        ln(f"  Kelly fraction: {kelly*100:.1f}%  "
           f"{'(bet more — edge is real)' if kelly > 0.10 else '(conservative — build track record)'}")
        ln()

    return "\n".join(lines)


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    watch = "--watch" in sys.argv

    if watch:
        print(clr("Win-Ratio Tracker — refreshing every 30s (Ctrl+C to stop)", CYAN))
        try:
            while True:
                os.system("clear")
                trades = load_trades()
                print(render(trades))
                print(clr(f"\n  ↻  Next refresh in {REFRESH}s…", DIM))
                time.sleep(REFRESH)
        except KeyboardInterrupt:
            print("\nStopped.")
    else:
        trades = load_trades()
        print(render(trades))
