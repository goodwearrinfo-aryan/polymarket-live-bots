#!/usr/bin/env python3
"""
gate_watcher.py — monitors the live-trading gate for the LEAD candidate (nearres).

The project ABANDONED the old moonshot gate (90% win / 5x / 10x on conviction+moonshot,
both now dead/disabled) — that gated on win-rate, the exact trap the project rejects.
GO-LIVE on nearres now requires the DOLLARS-based rule:
  - >= 30 priced exits (enough sample)
  - bootstrap 95% CI on mean $/exit EXCLUDES 0 (lower bound > 0)  — a real $ edge
  - controls (allin, coinflip) NEGATIVE — marking-honesty sanity (a positive control = bug)
(DSR is checked in the deeper analysis; this is the hourly iMessage alerter.)

Run hourly. State file prevents duplicate alerts. Pass --dry (or NO_IMESSAGE=1) to print
without sending any iMessage.
"""
import os, json, sys, random, subprocess
from datetime import datetime

DIR      = os.path.dirname(os.path.abspath(__file__))
LOG      = os.path.join(DIR, "scalp_lab_state.json")
STATE    = os.path.join(DIR, ".gate_watcher_state.json")
IMESSAGE = "+918449447444"
DRY      = "--dry" in sys.argv or os.environ.get("NO_IMESSAGE") == "1"

GATE_LEG       = "nearres"           # the lead candidate that gates live trading
CONTROLS       = ("allin", "coinflip")
GATE_MIN_EXITS = 30                  # project hard rule: >=30 closed exits before claiming edge
random.seed(42)


def boot_ci(pnls, n=5000):
    """95% bootstrap CI on the mean $/exit; None if too few exits."""
    if len(pnls) < 5:
        return None
    means = sorted(sum(random.choices(pnls, k=len(pnls))) / len(pnls) for _ in range(n))
    return means[int(0.025 * n)], means[int(0.975 * n)]


def send_imessage(text: str) -> bool:
    if DRY:
        print("[dry-run] would iMessage:\n" + text)
        return False
    safe = text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    script = (
        'tell application "Messages"\n'
        '  set targetService to id of first service whose service type = iMessage\n'
        f'  set targetBuddy to participant "{IMESSAGE}" of service id targetService\n'
        f'  send "{safe}" to targetBuddy\n'
        'end tell'
    )
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=15)
    return r.returncode == 0


def load_state() -> dict:
    try:
        return json.load(open(STATE))
    except Exception:
        return {"alerted": {}}


def save_state(s: dict):
    with open(STATE, "w") as f:
        json.dump(s, f)


def exits(scalp: dict, leg: str) -> list:
    return [p for p in scalp.get(leg, {}).get("closed", [])
            if p.get("reason") not in ("no-data", "stale_nodata")
            and p.get("pnl_usdc") is not None]


def main():
    try:
        scalp = json.load(open(LOG))
    except Exception:
        return

    wstate  = load_state()
    alerted = wstate.get("alerted", {})
    now     = datetime.now().strftime("%d %b %H:%M")

    # ── Gate on the lead candidate (nearres), dollars-based ──────────────────
    ex   = exits(scalp, GATE_LEG)
    pnls = [p["pnl_usdc"] for p in ex]
    n    = len(pnls)
    tot  = sum(pnls)
    wr   = sum(1 for p in pnls if p > 0) / n * 100 if n else 0
    ci   = boot_ci(pnls)
    ci_lo = ci[0] if ci else None

    # controls must be negative (marking sanity — a positive control means a marking bug)
    ctrl_pnl = {c: sum(p["pnl_usdc"] for p in exits(scalp, c)) for c in CONTROLS}
    ctrls_ok = all(v < 0 for v in ctrl_pnl.values())

    n_ok        = n >= GATE_MIN_EXITS
    ci_ok       = ci_lo is not None and ci_lo > 0
    gate_passes = n_ok and ci_ok and ctrls_ok

    if gate_passes and "LIVE_GATE_nearres" not in alerted:
        msg = (
            f"🚀 nearres GATE PASSED — {now}\n\n"
            f"✅ {n} priced exits (≥{GATE_MIN_EXITS})\n"
            f"✅ bootstrap 95% CI [{ci[0]:+.3f},{ci[1]:+.3f}] excludes 0 (real $ edge)\n"
            f"✅ controls negative ("
            + ", ".join(f"{c} {v:+.1f}" for c, v in ctrl_pnl.items()) + ")\n"
            f"WR {wr:.0f}% · total {tot:+.2f}$\n\n"
            f"Dollars-based gate met. Confirm DSR, then it's the real-money micro-test "
            f"call (GRADUATION_PROTOCOL.md). I will NOT flip dry_run."
        )
        if send_imessage(msg):
            alerted["LIVE_GATE_nearres"] = {"n": n, "ci_lo": ci_lo, "at": now}
            print("🚀 nearres GATE ALERTED")
    else:
        ci_str = f"[{ci[0]:+.3f},{ci[1]:+.3f}]" if ci else "n<5"
        print(f"nearres GATE: "
              f"{'✅' if n_ok else '⏳'} exits {n}/{GATE_MIN_EXITS}  "
              f"{'✅' if ci_ok else '⏳'} CI>0 {ci_str}  "
              f"{'✅' if ctrls_ok else '❌'} controls<0 "
              f"({', '.join(f'{c}{v:+.0f}' for c, v in ctrl_pnl.items())})  "
              f"· WR {wr:.0f}% tot {tot:+.2f}$")

    # ── Live milestone display: top active NON-control legs by priced exits ──
    rows = []
    for leg, book in scalp.items():
        if not isinstance(book, dict) or "closed" not in book or leg in CONTROLS:
            continue
        e = exits(scalp, leg)
        if e:
            rows.append((leg, len(e), sum(p["pnl_usdc"] for p in e)))
    rows.sort(key=lambda r: -r[1])
    print("  active legs by exits:")
    for leg, ne, pnl in rows[:12]:
        print(f"    {leg:14} {ne:>4} exits  P&L {pnl:+.2f}$")

    wstate["alerted"] = alerted
    save_state(wstate)


if __name__ == "__main__":
    main()
