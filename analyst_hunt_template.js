// analyst_hunt_template.js — canonical analyst hunt workflow (v4, 2026-06-16)
//
// LESSONS ENCODED:
// 1. Data before argument: run analyst_data_gate.py first; only analyze DATA-OK markets
// 2. Soft-refute + specificity fix: refutation counts if conf>70 OR names a specific
//    falsifiable mechanism. 50-70% refutations are SURFACED for human review, not auto-passed.
// 3. Resolution mechanics > headline: model the EXACT trigger shape, not the narrative
// 4. Regime-change check: if data has staleness warning, do a news check first
//
// HOW TO INVOKE:
//   Run analyst_data_gate.py --json first to get candidates, then paste DATA-OK markets here.
//
// ONE WORKFLOW AT A TIME — never launch 2 concurrent analyst workflows (~20 agents each
// trips the Anthropic server rate limit). Re-run only failed markets as a mini-workflow.

export const meta = {
  name: 'analyst-hunt-v4',
  description: 'Analyst edge hunt with data-gate, soft-refute+specificity, resolution check',
  phases: [
    { title: 'Resolution check', detail: 'Exact trigger criteria for each market' },
    { title: 'Data check',       detail: 'Fetch resolving series vs threshold' },
    { title: 'Refute panel',     detail: '3-lens adversarial refutation (soft+specificity)' },
    { title: 'Synthesis',        detail: 'Survivors → book recommendations' },
  ],
}

// ── PASTE MARKETS HERE ──────────────────────────────────────────────────────
// Replace with DATA-OK markets from analyst_data_gate.py --json output
const MARKETS = [
  // { q: "Will Bitcoin dip to $55,000 by December 31, 2026?", yes: 0.655, cid: "0x...", source: "crypto-threshold" },
]

// ── SCHEMAS ─────────────────────────────────────────────────────────────────

const RESOLUTION_SCHEMA = {
  type: 'object',
  properties: {
    exact_trigger: { type: 'string' },
    exact_no_condition: { type: 'string' },
    cutoff_date: { type: 'string' },
    traps: { type: 'array', items: { type: 'string' } },
    resolution_source: { type: 'string' },
    confidence_in_criteria: { type: 'number', minimum: 0, maximum: 100 },
  },
  required: ['exact_trigger', 'exact_no_condition', 'cutoff_date', 'traps', 'resolution_source'],
}

const REFUTE_SCHEMA = {
  type: 'object',
  properties: {
    lens: { type: 'string', enum: ['resolution-misread', 'counter-evidence', 'market-is-right'] },
    // SOFT-REFUTE + SPECIFICITY FIX (lesson 2026-06-16):
    // - refuted=true only if: conf>70 OR names a specific falsifiable mechanism (even at 62%)
    // - refuted=false if: weak (conf<50) generic efficient-market objection
    // - surface=true if: conf 50-70, but specific mechanism named → surface for human review
    refuted: { type: 'boolean' },
    surface_for_human_review: { type: 'boolean' },
    confidence: { type: 'number', minimum: 0, maximum: 100 },
    specific_mechanism: { type: 'string' },  // the falsifiable claim, if any
    reasoning: { type: 'string' },
  },
  required: ['lens', 'refuted', 'surface_for_human_review', 'confidence', 'reasoning'],
}

const VERDICT_SCHEMA = {
  type: 'object',
  properties: {
    market: { type: 'string' },
    yes_price: { type: 'number' },
    claimed_side: { type: 'string', enum: ['YES', 'NO'] },
    claimed_true_prob: { type: 'number' },
    claimed_edge_pts: { type: 'number' },
    data_confirmation: { type: 'string' },
    refute_summary: { type: 'string' },
    human_review_items: { type: 'array', items: { type: 'string' } },
    final_verdict: { type: 'string', enum: ['BOOK', 'SKIP', 'HUMAN_REVIEW'] },
    book_reason: { type: 'string' },
    skip_reason: { type: 'string' },
  },
  required: ['market', 'final_verdict'],
}

// ── MAIN ────────────────────────────────────────────────────────────────────

phase('Resolution check')
const resolved = await parallel(MARKETS.map(m => () =>
  agent(
    `You are a Polymarket resolution-criteria specialist. Market: "${m.q}" (YES=${m.yes}).

    Fetch the EXACT resolution criteria from Polymarket (search gamma or polymarket.com).
    Report: exact YES trigger, exact NO condition, cutoff date, resolution source, and traps.

    TRAP CHECKLIST (do not skip):
    - Is the trigger a single event vs sustained condition vs moving average vs one-touch?
    - Does the question say "by June 30" (close) vs "before June 30" (any day) vs "in June" (calendar month)?
    - Is there a 7-day MA vs single-day requirement? (Hormuz lesson: 60-MA ≠ single-day-40)
    - Does "normal" mean restored-to-prior-level or some specified threshold?
    - Resolution-source: does the market specify a single authoritative source, or is it ambiguous?`,
    { label: `resolve:${m.q.slice(0, 40)}`, schema: RESOLUTION_SCHEMA }
  ).then(r => ({ ...m, resolution: r }))
))

phase('Data check')
const data_checked = await parallel(resolved.filter(Boolean).map(item => () =>
  agent(
    `You are a Polymarket data-resolver. Market: "${item.q}".
    Resolution: ${JSON.stringify(item.resolution)}.
    Data source: ${item.source}.

    Fetch the CURRENT VALUE of the resolving series and compare to the exact trigger threshold.

    For portwatch: fetch IMF PortWatch ArcGIS (services9.arcgis.com/weJ1QsnbMYJlCHdG,
      service Daily_Chokepoints_Data/FeatureServer/0, field n_total, 7d-MA).
    For crypto-threshold: fetch Binance klines for current price vs threshold.
    For fed: fetch NY Fed TGCR (markets.newyorkfed.org/read?productCode=50&eventCodes=520).

    STALENESS CHECK: if PortWatch data >10 days old, do a quick news search for the market
    topic (ceasefire, deal, sanctions, etc.) and flag if a regime-change event occurred.

    Report: current value, threshold, gap, already_triggered (yes/no), data_date,
    and staleness_warning if data is stale + regime change found.`,
    { label: `data:${item.q.slice(0, 40)}` }
  ).then(text => ({ ...item, data_summary: text }))
))

phase('Refute panel')
const LENSES = ['resolution-misread', 'counter-evidence', 'market-is-right']

const refuted = await parallel(data_checked.filter(Boolean).map(item => () =>
  parallel(LENSES.map(lens => () =>
    agent(
      `You are an adversarial refuter. Your job is to KILL the claimed edge. Default to refuted=true.

      Market: "${item.q}" | YES=${item.yes}
      Resolution: ${JSON.stringify(item.resolution)}
      Data: ${item.data_summary}

      Lens: ${lens}

      SOFT-REFUTE + SPECIFICITY RULES (apply these exactly):
      - Set refuted=true if: your confidence is >70%
      - Set refuted=true if: you can name a SPECIFIC FALSIFIABLE MECHANISM even at 62% confidence
        (e.g. "Kpler reports 40 ships/day → 850-ship backlog could clear in one day")
      - Set refuted=false if: your objection is generic efficient-market reasoning at <70% confidence
        (e.g. "the market has already priced this in" at 60% = auto-pass, not a real refutation)
      - Set surface_for_human_review=true if: confidence 50-70% BUT you name a specific mechanism
        → this surfaces for human review rather than auto-passing OR auto-killing

      Resolution-misread lens: does the EXACT trigger text resolve differently than the claimed edge assumes?
      Counter-evidence lens: is there fresh data or a recent event that undermines the edge?
      Market-is-right lens: is the market price actually correct and the claimed edge an error?

      Report your lens, refuted (true/false), surface_for_human_review (true/false),
      confidence (0-100), specific_mechanism (the falsifiable claim if any), and reasoning.`,
      { label: `refute:${lens}:${item.q.slice(0, 30)}`, schema: REFUTE_SCHEMA }
    )
  )).then(votes => ({ ...item, votes: votes.filter(Boolean) }))
))

phase('Synthesis')
const verdicts = await parallel(refuted.filter(Boolean).map(item => () => {
  const votes = item.votes || []
  // Count refutations: refuted=true if conf>70 OR specific_mechanism named at 50-70%
  const hard_refutes = votes.filter(v =>
    v.refuted && (v.confidence > 70 || v.specific_mechanism)
  )
  const surface_items = votes.filter(v => v.surface_for_human_review)
  const survives = hard_refutes.length < 2

  return agent(
    `Synthesize the analyst review for this market.

    Market: "${item.q}" | YES=${item.yes}
    Data confirmation: ${item.data_summary}
    Refutation votes (${votes.length} lenses):
    ${votes.map(v => `- ${v.lens}: refuted=${v.refuted} (conf=${v.confidence}%) ${v.specific_mechanism ? '| mechanism: ' + v.specific_mechanism : ''}`).join('\n')}
    Hard refutations (conf>70 or mechanism named): ${hard_refutes.length}/3
    Items surfaced for human review: ${surface_items.length}
    Survives panel: ${survives}

    Apply these rules for the final verdict:
    - BOOK: survives (<2 hard refutations) AND data confirms the edge AND no critical human-review item
    - HUMAN_REVIEW: survives but has surface_for_human_review items that could flip the verdict
    - SKIP: ≥2 hard refutations, or data does not confirm, or resolution-misread kills it

    If BOOK: state the exact entry (side, price band, position sizing note, what to watch).
    If HUMAN_REVIEW: list the specific items a human needs to check before booking.
    If SKIP: state which refutation killed it (be specific — not "the market is efficient").`,
    { label: `verdict:${item.q.slice(0, 40)}`, schema: VERDICT_SCHEMA }
  )
}))

// Summary
const booked = verdicts.filter(Boolean).filter(v => v.final_verdict === 'BOOK')
const review = verdicts.filter(Boolean).filter(v => v.final_verdict === 'HUMAN_REVIEW')
const skipped = verdicts.filter(Boolean).filter(v => v.final_verdict === 'SKIP')

log(`=== ANALYST HUNT RESULTS ===`)
log(`BOOKED (${booked.length}): ${booked.map(v => v.market).join(', ') || 'none'}`)
log(`HUMAN REVIEW (${review.length}): ${review.map(v => v.market).join(', ') || 'none'}`)
log(`SKIPPED: ${skipped.length}`)

return { booked, review, skipped, all: verdicts.filter(Boolean) }
