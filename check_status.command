#!/bin/bash
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"
PY=$(command -v python3 || command -v python)

$PY - << 'PYEOF'
import json, os
from datetime import datetime

DIR = os.getcwd()

# Check if bot is running
pid_file = os.path.join(DIR, "bot.pid")
running = False
if os.path.exists(pid_file):
    pid = int(open(pid_file).read().strip())
    running = os.path.exists(f"/proc/{pid}") or os.system(f"ps -p {pid} > /dev/null 2>&1") == 0

print("=" * 50)
print("  POLYMARKET BOT — 10-DAY TRAINING STATUS")
print("=" * 50)
print(f"  Bot running : {'🟢 YES (PID ' + str(pid) + ')' if running else '🔴 NO'}")
print(f"  Time        : {datetime.now().strftime('%Y-%m-%d %H:%M')}")
print()

# Load brain
brain_path = os.path.join(DIR, "brain.json")
if os.path.exists(brain_path):
    b = json.load(open(brain_path))
    wins   = b.get("live_wins", 0)
    losses = b.get("live_losses", 0)
    total  = wins + losses
    wr     = wins/total*100 if total else 0
    pnl    = b.get("total_pnl_usdc", 0.0)
    roi    = pnl / max(total * 3.0, 1) * 100
    conf   = b.get("config_overrides", {}).get("min_confidence", 0.52)

    print(f"  Trades      : {total}  (W:{wins} / L:{losses})")
    print(f"  Win rate    : {wr:.1f}%")
    print(f"  P&L         : ${pnl:+.2f} USDC")
    print(f"  ROI         : {roi:+.1f}%")
    print(f"  Min conf    : {conf:.2f}  (auto-tuned)")
    print()

    # Per-strategy breakdown from trade log
    tlog = os.path.join(DIR, "trade_log.json")
    if os.path.exists(tlog):
        trades = json.load(open(tlog))
        stats = {}
        for t in trades:
            s = t.get("strategy","?")
            pnl_t = t.get("pnl_usdc", 0)
            stats.setdefault(s, {"n":0,"w":0,"pnl":0})
            stats[s]["n"] += 1
            stats[s]["pnl"] += pnl_t
            if pnl_t > 0: stats[s]["w"] += 1
        print("  Strategy breakdown:")
        for s, v in stats.items():
            wr_s = v["w"]/v["n"]*100 if v["n"] else 0
            print(f"    {s:10s}: {v['n']} trades, {wr_s:.0f}% WR, ${v['pnl']:+.2f}")
        print()

    # Profitable?
    if total >= 20:
        verdict = "✅ PROFITABLE — ready to go live!" if pnl > 0 and wr >= 52 else "⏳ Not yet profitable — keep training"
        print(f"  Verdict     : {verdict}")
    else:
        print(f"  Verdict     : ⏳ Need {20-total} more trades to evaluate")
else:
    print("  No brain.json yet — bot hasn't made trades yet.")

print("=" * 50)
PYEOF
sleep 10
