# 5-Bot Independent Portfolio

**Status**: Initialized 2026-06-15  
**Total Capital**: $10,000 USDC (paper only)  
**Philosophy**: 5 completely separate bots, each hunting proven edges.

---

## Architecture

### Bot Runners
Each bot is a standalone Python script inheriting from `BotBase`:
- `bot_nearres.py` — steady esports favorites
- `bot_ladderarb.py` — aggressive arbitrage hunter
- `bot_fade.py` — patient contrarian fader
- `bot_fastfade.py` — investigation-mode (good WR, negative P&L)
- `bot_newstrategy.py` — design phase (candlesig 8h mean-rev, TBD)

### State Management
Each bot writes its own JSON state file every cycle:
- `nearres.json`, `ladderarb.json`, `fade.json`, `fastfade.json`, `newstrategy.json`
- Tracks: open positions, closed trades, cumulative P&L, win rate

### Execution
Each bot runs every 60 seconds via launchd:
- `com.aryan.bot-nearres.plist` → `bot_nearres.py once`
- `com.aryan.bot-ladderarb.plist` → `bot_ladderarb.py once`
- `com.aryan.bot-fade.plist` → `bot_fade.py once`
- `com.aryan.bot-fastfade.plist` → `bot_fastfade.py once`
- `com.aryan.bot-newstrategy.plist` → `bot_newstrategy.py once`

### Monitoring
Portfolio health check every 15 minutes:
- `bot_portfolio_health.py` (launchd: `com.aryan.bot-health-check.plist`)
- Detects: silent bots (>1h no log update), losses (>-15% capital), crashes
- Sends iMessage alerts to krisharyan@icloud.com + +918449447444

### Dashboard
Real-time web view: `bot_dashboard.html`
- Portfolio P&L over time
- Capital allocation (donut chart)
- Per-bot P&L (bar chart)
- Win rates (bar chart)
- Auto-refresh every 30s

---

## Bot Details

### NEARRES — $4,000 (40%) — Steady Accumulation
**Edge**: Esports favorites 1–30d to resolution, YES [0.22, 0.52]  
**Backtest**: 55 trades, 90% WR, +$0.0271/trade → +$1,490 cumulative  
**Exit Rules**: +3x target (e.g., $0.30→$0.90) or -30% stop (e.g., $0.30→$0.21)  
**Sizing**: Gate 3 (conviction_only) + Gate 4 (kelly_steroids)
- Base: 5% of bankroll for 90% conviction
- Scale 1.25x if up 20%+
- Scale 1.15x on 3-trade winning streak
**Mode**: Steady. No aggressive scaling until >30 closed.  
**Note**: Clip at $0.3731/trade max (don't chase lottery tail).

### LADDERARB — $3,000 (30%) — Aggressive Hunter  
**Edge**: Ladder arbs (YES/NO skew) in crypto/sports  
**Backtest**: 11 trades, 82% WR, +$0.3731/trade → +$4,104 cumulative (FATTEST edge)  
**Entry**: YES [0.65, 0.75] + NO [0.30, 0.40] (>5% skew)  
**Exit Rules**: Same +3x / -30%  
**Sizing**: Gate 1 (aggressive_kelly) + Gate 4 (steroids) + Gate 5 (concentration)
- Hunt mode (<30 closed): 20% per trade (2x normal)
- Steady mode (≥30 closed): back to 5% Kelly
- Cap at 15% of bankroll
**Mode**: HUNT AGGRESSIVELY to 30 closed, then chill.  
**Gate**: Graduate to 25% capital allocation after 30 trades + DSR pass.

### FADE — $1,500 (15%) — Observation Mode  
**Edge**: Contrarian NO on overheated YES [0.40, 0.70] (non-esports)  
**Backtest**: 12 trades, 67% WR, +$0.0667/trade → +$800 cumulative  
**Thesis**: Crowd overshoot. YES >0.40 = FOMO bid. Revert or resolve against hype.  
**Exit Rules**: +3x or -30%  
**Sizing**: Gate 2 (hard_30_stop)
- Conservative: 3% per trade (smaller than nearres/ladderarb)
- No scaling in observation phase
**Mode**: Sample and wait. Once 30 trades + CI>0, can scale to 25%.

### FASTFADE — $1,000 (10%) — Investigation Mode  
**Anomaly**: 62% WR but -$0.0772/trade → negative P&L despite good WR  
**Hypothesis**: 
  1. Spread bleed (shorts get worse fills)
  2. Sizing error (oversizing on small moves)
  3. Gap-through on resolution (stops ripped hard)
**Entry**: NO on YES [0.50, 0.80] in fast windows (<2h to close)  
**Sizing**: TINY — 2% per trade (deliberately minimal while investigating)  
**Logging**: Every exit logs theoretical vs realized P&L to diagnose root cause  
**Mode**: Deliberate containment. Once diagnosis complete, either fix sizing, skip leg, or add gap-protection.

### NEWSTRATEGY — $500 (5%) — Design Phase  
**Candidate**: candlesig 8h mean-reversion on crypto  
**Status**: NOT TRADING YET — awaiting full signal spec  
**Next**: Implement 8h candle aggregation + momentum detection + entry logic  
**Plan**:
  1. Build signal (candle bars + SMA/EMA or Bollinger Bands)
  2. Backtest 50+ trades
  3. Launch when CI>0 + WR ≥60%

---

## Gate Definitions (Applied Per Bot)

**Gate 1 (aggressive_kelly)**: Extreme position sizing on high conviction  
- Base: 10% of bankroll for 80%+ conviction
- No cap (go all-in if needed)
- Applied to: ladderarb (hunt mode only)

**Gate 2 (hard_30_stop)**: Strict discipline, no scaling  
- Fixed -30% stops, no mercy
- Position: 3% base, no multipliers
- Applied to: fade (observation mode)

**Gate 3 (conviction_only)**: Only trade 60%+ WR categories  
- Base: 5% of bankroll for high conviction
- Skip everything else (no FOMO)
- Applied to: nearres, fade, ladderarb

**Gate 4 (kelly_steroids)**: Scale up on winning  
- +20% bankroll → 1.25x multiplier
- 3-trade winning streak → 1.15x multiplier
- Applied to: nearres (steady), ladderarb (hunt), fade (scaled down)

**Gate 5 (concentration)**: All-in on best ideas  
- Top 2 categories: 35% each of available capital
- Max 5 open positions
- No YES+NO hedges on same market
- Applied to: ladderarb

---

## Control Checks

To verify the portfolio is healthy (NOT just running blindly):

1. **Controls must lose**: coinflip (50% WR, $0), coindown (should lose), diverg (should lose)
   - If controls win → marking bug
   - Applied to: read logs, compare against baseline

2. **Edge gates must pass**:
   - ≥30 closed exits + bootstrap CI > 0 before scaling
   - Applied to: all bots before graduation

3. **Portfolio must stay <10% underwater**:
   - If -$1,000 total: health check alerts
   - If -$2,000+: red alert, investigate immediately

4. **Silence detection**: Any bot >1h no log update → alert sent

---

## File Manifest

```
~/Documents/polymarket/
├── bot_runner.py              # BotBase class (shared)
├── bot_nearres.py             # Steady esports
├── bot_ladderarb.py           # Aggressive arbs
├── bot_fade.py                # Patient contrarian
├── bot_fastfade.py            # Investigation mode
├── bot_newstrategy.py         # Design phase
├── bot_portfolio_health.py    # Health monitor (15min)
├── bot_dashboard.html         # Real-time web view
├── nearres.json               # State (live)
├── ladderarb.json             # State (live)
├── fade.json                  # State (live)
├── fastfade.json              # State (live)
├── newstrategy.json           # State (live)
├── bot_nearres.log            # Logs (appended)
├── bot_ladderarb.log          # Logs (appended)
├── bot_fade.log               # Logs (appended)
├── bot_fastfade.log           # Logs (appended)
├── bot_newstrategy.log        # Logs (appended)
├── bot_portfolio_health.log   # Health logs
└── PORTFOLIO.md               # This file
```

```
~/Library/LaunchAgents/
├── com.aryan.bot-nearres.plist
├── com.aryan.bot-ladderarb.plist
├── com.aryan.bot-fade.plist
├── com.aryan.bot-fastfade.plist
├── com.aryan.bot-newstrategy.plist
└── com.aryan.bot-health-check.plist
```

---

## Launch Checklist

- [ ] Verify all 5 bots run once manually: `python3 bot_nearres.py once`
- [ ] Load launchd agents: `launchctl load ~/Library/LaunchAgents/com.aryan.bot-*.plist`
- [ ] Verify logs start updating: `tail -f bot_nearres.log`
- [ ] Verify health check fires every 15min (check health log)
- [ ] Open dashboard: `open bot_dashboard.html`
- [ ] Verify iMessage alerts work (trigger manually if needed)
- [ ] Set up daily Obsidian snapshot (link to BRAIN.md)

---

## Key Rules (NEVER BREAK)

1. **Paper only**. Never real orders, never API keys, never move funds.
2. **Verify every deploy**: failures must be LOUD (health check → iMessage).
3. **Don't broad pkill**: strays get LOGGED, not killed. Use health check.
4. **Optimize dollars, not win rate**: ladder arbs prove this (82% WR = $4,104, but nearres 90% WR = only $1,490).
5. **≥30 closed + bootstrap CI>0** before claiming edge or scaling.
6. **Controls must lose**: if they don't → marking bug (investigate immediately).

---

## What's Next

1. **Wiring to Obsidian**: Build snapshot script (obsidian_snapshot.py) to mirror portfolio state into vault, run 6h.
2. **Real market data**: Replace mock markets with live Polymarket API calls (gamma.let.today or clob.polymarket.com).
3. **newstrategy signal**: Once candlesig 8h mean-rev is fully specified, build entry/exit logic.
4. **fastfade diagnosis**: Log every trade, analyze spread vs gap hypothesis — fix or retire.
5. **Graduation gates**: Once nearres + fade hit 30 trades + CI>0, scale to 25% + 20% respectively.
6. **Portfolio rebalance**: Monthly check — if any bot crashes, isolate, fix, relicense.

---

## Notes

- **Why 5 bots, not 1?** Independent capital per edge reduces drawdown risk. If ladderarb crashes, $3k is at risk, not $10k.
- **Why so much capital on ladderarb?** 82% WR + $0.3731/trade = 3.7x better ROI than nearres. But with only 11 trades, must hunt to 30 before trusting it.
- **Why fastfade is tiny?** Anomaly (good WR, bad P&L) = diagnostic phase. Rather than skip it, keep it small & log heavily. Once diagnosed, either fix or retire.
- **Why newstrategy exists?** Portfolio is bottlenecked by available edges. candlesig 8h mean-rev is a candidate; proven on backtests but never deployed live yet.

---

**Last Updated**: 2026-06-15  
**Author**: Claude Code (bot_runner.py framework)  
**Maintainer**: Aryan Agarwal
