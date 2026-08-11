# Real-Money Micro-Test Log (GRADUATION_PROTOCOL Step 2)

Rule: **execute ONLY trades the paper bot ALSO entered** (it is the signal generator).
Real fills are Aryan's manual ones in his own account. The paper-vs-real fill gap is the point.

- Bankroll: $100-200 (amount you can lose entirely)
- Stake: fixed $5/trade, capped at 1/4-Kelly (never size up mid-test)
- Entry: exactly the paper bot's (side + price), <2h to resolution where applicable, spread ≤3¢, real depth
- Exit: ride to settlement, 3¢ mental stop
- Stop rules: real edge < 50% of paper edge → slippage eats it, stop + analyze fill gap;
  drawdown > 50% of test bankroll → stop immediately
- Never: API-key trading from the bot, automated real orders, sizing past 1/4-Kelly

`live_harness.py` prints today's candidate trades and appends rows here on `--applog`.

| logged_ts (UTC) | source | slug | paper_fill_ts | paper_net_edge | paper_cost | real_fill_ts | real_fill_price | real_pnl |
|---|---|---|---|---|---|---|---|---|
| 2026-08-11 19:34Z | basket | where-will-2026-rank-among-the-hottest-years-on-record | 2026-08-06T23:33:45.286427+00:00 | 0.011 | 0.966 | — | — | — |
| 2026-08-11 19:36Z | basket | where-will-2026-rank-among-the-hottest-years-on-record | 2026-08-06T23:33:45.286427+00:00 | 0.011 | 0.966 | — | — | — |
