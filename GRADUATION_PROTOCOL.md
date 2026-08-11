# nearres Graduation Protocol — pre-registered 2026-06-13
PAPER bot never touches real money. Every real-money step below is ARYAN's,
manually, in his own account. This doc exists so the plan is fixed BEFORE the
gate closes (no moving goalposts after).

## Trigger
nearres reaches 30/30 config-consistent forward exits AND fade_checkpoint-style
verification holds: bootstrap CI > 0 on those 30, controls negative.
(OOS already done: n=150, CI [+0.011,+0.051], DSR 0.91 ✓)

## Step 1 — Switch live config to 2h (Claude, on Aryan's word)
nearres_max_hours 4 → 2 in scalp_lab.py CONFIG. Justified by graduated
experiment (n=65, CI>0, DSR 0.976, +4.6¢ vs +3.1¢). One change, then frozen.

## Step 2 — Real-money micro-test (ARYAN, manual)
- Bankroll: an amount you can lose entirely without caring ($100-200).
- Sizing: fixed $5/trade (¼-Kelly at this edge ≈ 2-3% of $200; $5 is below it
  — deliberately conservative). NEVER size up mid-test.
- Entry rule = exactly the bot's: esports favorite, side-mid 0.88-0.95,
  <2h to resolution, liquidity_check-equivalent (spread ≤3¢, real depth),
  skip if price moved >5¢ in last minute.
- Exit rule: ride to settlement with a 3¢ mental stop.
- Execute only trades the paper bot ALSO entered (it's the signal generator;
  check the dashboard/alerts). Manual fills only.
- Log every trade in real_test_log.md: ts, market, paper fill, YOUR fill,
  exit, P&L. The paper-vs-real fill gap is the point of the test.

## Step 3 — Verdict at 30 real trades
- Real edge ≥ 60% of paper edge (≥ ~2.7¢/trade) → PASS: size to ¼-Kelly
  properly (still capped by book_depth), continue logging.
- Real edge < 50% of paper → slippage eats it: stop, analyze fill gap.
- Drawdown > 50% of test bankroll at any point → stop immediately, review.

## Never
- No API-key trading from the bot. No automation of real orders. No sizing
  past ¼-Kelly. No adding legs to real money until they pass the same bar.
