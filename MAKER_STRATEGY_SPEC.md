# Maker / Spread-Capture Strategy — Spec (Option #1)

Status: design. Paper-only until Phase B explicitly gated. No real orders without the user.

## 1. Thesis

Every current leg is a **taker**: it buys at the ask and sells at the bid, paying the full
round-trip spread (~2¢) into markets that are already efficient. That tax is why the allin
and coinflip controls are negative — it's structural, not strategy.

A **maker** inverts it: post a resting limit order *inside* the spread, and when someone
crosses to you, you **earn** the spread instead of paying it. On a tight, liquid, mean-reverting
book, capturing 1–2¢ per round-trip many times can be net positive precisely because it
collects what the takers lose. This is the only direction with a *structural* (not predictive)
edge, and `scalp_engine.py` already polls the real CLOB book — so the foundation exists.

## 2. The honesty problem (must solve before any number is believable)

`scalp_engine.py` already runs a `maker` book, but its fill model is a fantasy:
- entry: `fill = bk["bid"]` (line 238) — assumes a buy limit at the bid fills instantly.
- exit:  `fill = bk["ask"]` (line 278) — assumes a sell limit at the ask fills instantly.

In reality a resting order fills **only when the market trades through your price**, and the
cruel part is *adverse selection*: your buy-at-bid tends to fill exactly when the market is
about to drop further (you get hit by informed flow), and your sell-at-ask fills when it's
about to keep rising (you miss the move). So the naive maker book overstates P&L two ways:
it assumes 100% fill, and it ignores that the fills you DO get are disproportionately the bad ones.

**Rule for this whole project (learned from the marking bug): a fill you didn't model honestly
is a fill that lies.** The maker book is the single most dangerous place for that.

## 3. Phase A — Honest paper-sim maker (build first, on scalp_engine.py)

Goal: a maker book whose fills are conditional on real book movement, so the paper P&L is
defensible enough to decide whether Phase B is worth the risk. NOT to be trusted as live truth.

### Fill model (the core change)
Replace instant fills with **resting orders that fill only on a through-trade**:
1. On entry signal, place a virtual buy limit at `bid` (or `bid + 1 tick` to improve queue).
   Record `placed_at`, `limit_price`. Do NOT mark it filled yet.
2. On each subsequent ~10s poll, the order fills **only if** the book's best ask drops to
   ≤ `limit_price` (someone crossed down to you) OR a trade prints at/through `limit_price`.
   Until then it rests, unfilled.
3. If unfilled after `maker_quote_timeout` (e.g. 60–120s), **cancel** it (no trade booked).
   Cancels are the honest cost of being a maker — most quotes won't fill.
4. Same logic on exit: post a sell limit at `ask`, fill only when bid rises to ≥ your price,
   else cancel/re-quote or fall back to a taker exit after a hold cap (and book THAT cost).

### Adverse-selection accounting
- Track **fill rate** (filled quotes / quotes posted) — the number that kills naive maker P&L.
- Track **fill-conditional drift**: average mid move in the N seconds *after* a fill. If it's
  systematically against you, that's adverse selection quantified.
- A maker leg only "works" if `spread captured × fill rate − adverse drift − fees > 0`.

### What changes in code
- New fill-state on open positions: `status: quoting | filled`, `limit_price`, `placed_at`.
- `_entries`: post quote (status=quoting), don't append a filled position.
- `poll_once`/`_exits`: promote quoting→filled on through-trade; expire stale quotes.
- New config: `maker_quote_timeout`, `maker_improve_ticks`, `maker_taker_exit_fallback` (bool).
- Reporting: add fill-rate and adverse-drift columns to the engine board + status PDF.
- Keep the old optimistic maker book running in PARALLEL, relabeled "maker-naive (ceiling)",
  so you can see how much the honest model claws back. The gap = the fantasy you were trading.

### What Phase A can and cannot prove
- CAN: whether the strategy is even plausibly positive after realistic fill rates; plumbing,
  config, metrics, kill criteria.
- CANNOT: real fill rates or real adverse selection. A sim guesses who crosses to you. Treat
  Phase A P&L as an UPPER bound that's less wrong than naive — never as evidence to size up.

### Phase A kill criteria
If honest-maker net P&L is ≤ 0 across ≥30 filled round-trips with the controls correctly
negative, the spread on these markets isn't capturable in sim — stop; do not go to Phase B.

## 4. Phase B — Real CLOB maker (gated, real money eventually)

Only after: Phase A is net-positive on ≥30 fills, the Mac↔Polymarket network is stable, and
you accept this moves from paper to real orders.

### What it does
- Post/cancel **real limit orders** on Polymarket's CLOB for one tight, liquid market at a time.
- Measure the things sim can't: true fill rate, true adverse selection, queue position, maker
  rebates/fees, latency between decide→post→ack.
- Start at the smallest allowed size; one market; hard daily loss cap that halts on breach.

### Hard boundaries (these are mine, not negotiable)
- **I will not enter API keys, place, cancel, or sign any real order, or move funds.** Phase B
  order placement and key handling are done by you. I can write the client code, the quoting
  logic, the risk controls, and the dry-run harness — but the live "go" is yours to execute.
- Phase B starts in **dry-run** (posts to a testnet or logs intended orders) until you've read
  the fill logs and explicitly approve live.

### Phase B build order
1. CLOB client: auth, `post_order`, `cancel_order`, `get_book`, `get_fills` (dry-run first).
2. Quoting loop reusing Phase A's signal + the HONEST fill expectations as priors.
3. Risk layer: max open, max daily loss (auto-halt), max position per market, cancel-on-disconnect.
4. Reconciliation: every intended order vs actual fill, logged, fed back into the adverse-selection
   model so live data corrects the sim.
5. Tiny-size live test → compare real fill rate to Phase A's assumed fill rate. If reality is
   much worse (likely), the edge was a sim artifact — stop.

### Phase B kill criteria
Real fill rate or adverse selection materially worse than Phase A assumed, OR net-of-fees ≤ 0
over a meaningful sample → shut it down. Don't average-in hoping it turns.

## 5. Honest expectation

[Likely] Phase A will look positive and Phase B will be worse — adverse selection and real fill
rates are where paper maker strategies die. That's not a reason to skip it; it's the reason to
build Phase A's fill realism *first* and treat every number with suspicion. If the edge is real,
it survives honest fills; if it only exists under instant-fill fantasy, you've learned that cheaply.

## 6. First concrete step
Implement the Phase A honest fill model in `scalp_engine.py` (the quoting/through-trade/cancel
state machine), run it alongside the naive maker book, and watch fill-rate + adverse-drift in the
status PDF. Everything else gates on that.
