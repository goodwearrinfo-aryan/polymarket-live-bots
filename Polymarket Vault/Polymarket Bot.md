---
title: Polymarket Bot
type: project-log
status: paper-trading (dry-run)
created: 2026-05-30
updated: 2026-05-30
tags: [polymarket, trading-bot, paper-trading]
---

# Polymarket Bot

> [!warning] Paper money only
> The bot runs in `--dry-run` mode — no real orders, no real funds. Do **not** go live until a clean sample clears the go-live bar (below).

## What it is
A self-learning bot that copies top Polymarket traders. It tracks the top-500 leaderboard, mirrors high-conviction moves (consensus + insider signals), and now also runs a "midline" scalp and a news/sentiment layer. Lives in `~/Documents/polymarket`, launched via `restart_with_tracker.command`.

## Current status — 2026-05-30
- **Mode:** dry-run (paper)
- **Open positions:** ~60
- **Closed:** 82 — *but all pre-fix and invalid* (see caveat)
- **Clean (post-fix) closed:** 0 so far — evaluation hasn't started yet
- **Worker threads:** 48 (positions) / 24 (activity)

> [!important] The 82 closed trades are NOT a real result
> Every one was *entered* at a stubbed $0.50 (price-feed bug) and closed at the real resolution price → effectively coin-flips (42W/40L, +$3, ~51%). This is noise, not edge. The real test only counts trades opened **after** the price fix (2026-05-30 ~14:20 local).

## Changes made (2026-05-30)
- [x] **Price feed fixed** — `best_price` now pulls live prices from the **CLOB** (`clob.polymarket.com/midpoint`), with Gamma `outcomePrices` fallback, then 0.5 only as last resort. *This was the critical bug — before it, every trade booked at 0.50 so P&L was permanently $0.*
- [x] **Midline hang fixed** — pre-filter to markets with ≥2 YES holders, cap 60 candidates, concurrent prefetch (was fetching thousands of markets serially → froze scans).
- [x] **24-hour trades enabled** — `resolution_min_days` 4 → 0.04 (~1h floor).
- [x] **48¢ → 50¢ midline strategy ON** — buy YES ≤0.48, sell at 0.50, up to 25 positions.
- [x] **News/data feed** (`news_data.py`, no API key) — GDELT headlines + CoinGecko prices; adjusts confidence by sentiment/momentum, skips breaking-news spikes, logs a headline on each trade. News-driven *signals* built but **OFF** by default.
- [x] **Faster loop** — full scan every 20s; between scans, re-price held markets every 7s so the 50¢ target / stops fire near-real-time.
- [x] **Launcher hardened** — `nohup` + detached stdin + `disown` so closing the Terminal window no longer kills it.
- [x] **Worker threads** raised to 48/24.

## Key config (`polymarket_bot.py` → CONFIG)
| Setting | Value |
|---|---|
| `scan_interval_sec` | 20 |
| `fast_monitor_interval_sec` | 7 |
| `resolution_min_days` | 0.04 |
| `midline_enabled` | True (buy ≤0.48, sell 0.50) |
| `midline_min_traders` / `max_candidates` | 2 / 60 |
| `news_enabled` | True |
| `news_signals_enabled` | False |
| `fetch_workers` | 48 |
| `take_profit_pct` / `stop_loss_pct` | 0.30 / 0.15 |

## Open risks / watch-list
- [ ] **Midline is unproven & suspect** — backtested ~3.7% win rate; 0 closed so far. Watch its live numbers; be ready to disable.
- [ ] **No watchdog running** — a silent crash won't auto-recover (it died twice today). Consider an auto-restart watchdog.
- [ ] **Rate-limiting** — 48 workers is near the safe ceiling; if 429s appear in `run_output.txt`, dial back `fetch_workers`.
- [ ] **Profitability not guaranteed** — markets are efficient/adversarial. These changes improve *expected edge*, not certainty.

## Go-live bar (do not skip)
> [!note] Don't risk real money until ALL of these hold on the **clean** sample
> - ≥ 30–50 clean closed trades (opened after the price fix)
> - win rate > 52%
> - positive realized P&L
> - no single strategy (esp. midline) dragging the rest down

## Automation
- **Daily profitability check** — scheduled task `polymarket-profitability-check`, runs 09:06 local. Reports win rate + P&L per strategy on the clean sample; declares a verdict only once ≥30 clean trades exist.
- **Morning status** — existing `polymarket-update-8am`.

## Next steps
- [ ] Let the clean sample accumulate; read the 9 AM check.
- [ ] Once ≥30 clean trades: cut losing strategies/markets, amplify winners.
- [ ] Decide on a watchdog for unattended reliability.
