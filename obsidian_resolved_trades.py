#!/usr/bin/env python3
"""
obsidian_resolved_trades.py — mirror the bot's resolved/closed paper trades into Obsidian.

Reads scalp_lab_state.json (the Postgres mirror) and writes a LIVE note
~/Documents/PolymarketVault/Reports/Resolved Trades.md so the resolved-trade
ledger is browsable in the vault instead of frozen in June-12 leg snapshots.

- "Resolved" trades = closed exits with reason == "resolved" (the market actually
  settled while we held). Other closed exits (time/target/stop) are scalp exits
  before resolution; summarised separately for completeness.
- Atomic write (tmp + os.replace), keyless, $0. PAPER ONLY — read-only on bot state.

Wire to launchd to keep it live (see obsidian_resolved_trades install notes).
"""

import json, os, sys, html
from datetime import datetime, timezone

STATE = os.path.expanduser("~/Documents/polymarket/scalp_lab_state.json")
OUT = os.path.expanduser("~/Documents/PolymarketVault/Reports/Resolved Trades.md")
RECENT_N = 120  # most-recent resolved trades to table out


def _clean(s, n=52):
    """Markdown-table-safe, truncated market text."""
    s = (s or "").replace("|", "/").replace("\n", " ").strip()
    s = html.unescape(s)
    return (s[: n - 1] + "…") if len(s) > n else s


def _dt(s):
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def collect(state):
    rows = []
    for leg, cfg in state.items():
        if not isinstance(cfg, dict):
            continue
        for ex in cfg.get("closed", []) or []:
            if not isinstance(ex, dict):
                continue
            rows.append(
                {
                    "leg": leg,
                    "q": ex.get("q"),
                    "side": ex.get("side"),
                    "reason": ex.get("reason"),
                    "result": ex.get("result"),
                    "pnl": float(ex.get("pnl_usdc") or 0.0),
                    "closed_at": ex.get("closed_at"),
                    "entry": ex.get("entry_fill"),
                    "exit": ex.get("exit_fill"),
                }
            )
    return rows


def main():
    if not os.path.exists(STATE):
        sys.stderr.write(f"no state file at {STATE}\n")
        return 1
    state = json.load(open(STATE))
    rows = collect(state)
    rows.sort(key=lambda r: r["closed_at"] or "", reverse=True)

    resolved = [r for r in rows if (r["reason"] or "").lower() == "resolved"]
    total_pnl = sum(r["pnl"] for r in rows)
    res_pnl = sum(r["pnl"] for r in resolved)
    res_wins = sum(1 for r in resolved if r["pnl"] > 0)
    wr = (res_wins / len(resolved) * 100) if resolved else 0.0

    # date range
    dts = [d for d in (_dt(r["closed_at"]) for r in rows) if d]
    span = f"{min(dts):%Y-%m-%d} → {max(dts):%Y-%m-%d}" if dts else "—"
    now = datetime.now(timezone.utc)

    # per-leg resolved summary
    legs = {}
    for r in resolved:
        a = legs.setdefault(r["leg"], {"n": 0, "pnl": 0.0, "w": 0})
        a["n"] += 1
        a["pnl"] += r["pnl"]
        a["w"] += 1 if r["pnl"] > 0 else 0

    L = []
    L.append("---")
    # type: logger so the Dashboard's `FROM "Reports" WHERE type="logger"` freshness
    # Dataview auto-surfaces it (and flags it if the 15min job ever goes stale).
    L.append("type: logger")
    L.append("status: live")
    L.append(f"updated: {now:%Y-%m-%dT%H:%M:%SZ}")
    L.append("tags: [polymarket, resolved, trades, paper, ledger]")
    L.append("---")
    L.append("")
    L.append("# 🧾 Resolved Trades (live)")
    L.append("")
    L.append(
        "> [!warning] PAPER ONLY. Auto-mirrored from `scalp_lab_state.json` — "
        "the live source of truth, not a frozen snapshot. "
        f"_updated {now:%Y-%m-%d %H:%M UTC}._"
    )
    L.append("")
    L.append("## Summary")
    L.append("")
    L.append(f"- **Market-resolved trades:** {len(resolved)}  (held to settlement)")
    L.append(f"- **Resolved P&L:** ${res_pnl:+.2f}  ·  **WR:** {wr:.0f}%  ({res_wins}/{len(resolved)})")
    L.append(f"- **All closed exits (incl. scalp time/target/stop):** {len(rows)}  ·  total realized ${total_pnl:+.2f}")
    L.append(f"- **Span:** {span}")
    L.append("")
    L.append("## Resolved by leg")
    L.append("")
    L.append("| Leg | Resolved n | WR | P&L |")
    L.append("|---|--:|--:|--:|")
    for leg, a in sorted(legs.items(), key=lambda kv: kv[1]["pnl"], reverse=True):
        legwr = a["w"] / a["n"] * 100 if a["n"] else 0
        L.append(f"| {leg} | {a['n']} | {legwr:.0f}% | ${a['pnl']:+.2f} |")
    if not legs:
        L.append("| _none yet_ | 0 | — | $0.00 |")
    L.append("")
    L.append(f"## Recent resolved trades (last {min(RECENT_N, len(resolved))})")
    L.append("")
    L.append("| Closed (UTC) | Leg | Market | Side | Entry→Exit | P&L |")
    L.append("|---|---|---|---|---|--:|")
    for r in resolved[:RECENT_N]:
        d = _dt(r["closed_at"])
        ds = f"{d:%Y-%m-%d %H:%M}" if d else (r["closed_at"] or "—")
        ee = ""
        if r["entry"] is not None and r["exit"] is not None:
            ee = f"{r['entry']:.3f}→{r['exit']:.3f}"
        L.append(f"| {ds} | {r['leg']} | {_clean(r['q'])} | {r['side']} | {ee} | ${r['pnl']:+.3f} |")
    if not resolved:
        L.append("| _no market-resolved trades yet_ | | | | | |")
    L.append("")
    L.append("_Source: `scalp_lab_state.json` · [[Dashboard]] · [[legpnl|Per-Leg P&L]] · [[Markets Paper]] · [[Edge Ledger]]_")

    body = "\n".join(L) + "\n"
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    tmp = OUT + ".tmp"
    with open(tmp, "w") as f:
        f.write(body)
    os.replace(tmp, OUT)
    print(f"wrote {OUT}: {len(resolved)} resolved / {len(rows)} closed, resolved P&L ${res_pnl:+.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
