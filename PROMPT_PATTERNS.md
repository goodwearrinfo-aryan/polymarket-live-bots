# Prompt Patterns — distilled from CL4R1T4S (elder-plinius)

Mined 2026-06-15 from extracted system prompts of Perplexity, Devin, Cursor, Factory
(DROID), Manus. Patterns are **described in my own words** — nothing copied verbatim
(source repo is AGPL-3.0; the prompts are proprietary case studies). Goal: sharpen
`analyst_gate.py` lenses + the bot's agent prompts. Each pattern → which vendor uses
it → how to apply it here.

---

## The 8 transferable patterns

### 1. Evidence-grounded verdicts  *(DROID, strongest)*
Every claim must cite the specific thing it rests on — file path + excerpt, a package
success line, an exit code. "Demonstrate you inspected the actual code." No assertion
without attached evidence.
→ **This is your `verify-before-done` rule, already.** The new lever is forcing it
*inside the LLM panel*, not just in my own work.

### 2. Verify-before-assert / ban assumptions  *(Devin, Cursor)*
"NEVER assume a library is available — check the codebase first." "Don't assume link
contents without visiting." The mechanism must be confirmed, never inferred.
→ **This is literally the BTC touch-vs-close failure.** The 2 analyst-gate FAILs both
hinged on an *unverified resolution mechanism*. Encode it: a thesis that depends on a
resolution rule the analyst hasn't verified should auto-fail the resolution lens.

### 3. Root cause, not symptoms  *(Devin, Cursor)*
"First consider the root cause might be in the code, not the test." Don't modify the
test to make it pass.
→ For `edge_is_real`: distinguish "the market price is the root truth and the analyst
is anchoring on a narrative" from "the analyst genuinely knows something." The lens
should name *which*.

### 4. Bounded retry + explicit escalation  *(Devin "ask after 3rd CI fail", Cursor "don't loop >3x on linter")*
A hard cap on self-correction loops, then escalate to the human.
→ For my working loops and any bot agent that retries (RPC, fills, API). Stop at N,
surface the blocker, don't spin.

### 5. Citation / sourcing discipline  *(Perplexity)*
Cite the source right after each sentence it supports. Combine multiple sources on the
same event and cite all. Prioritize **recent + diverse + trustworthy**; compare
timestamps. If sources are empty/unhelpful, say so and fall back to known facts.
→ For the analyst `sources` field + a possible new **sourcing lens**: a thesis whose
sources are stale, single-origin, or don't actually support the claim is weaker. Mirror
your existing `RECENCY>P&L` instinct from the wallet watcher.

### 6. Calibrated uncertainty / no fabrication  *(Cursor "NEVER lie or make things up", Perplexity graceful-degradation)*
Explicit "don't invent." Graceful degradation when data is thin instead of confident
bullshit.
→ The gate already has a skeptic prior (PASS needs ≥2/3 to FAIL to refute). Strengthen:
each lens should **default to REFUTED when it lacks the evidence to judge** — uncertainty
is a refutation, not a pass. (Matches `controls must lose` honesty.)

### 7. Structured sections + explicit output contract  *(Perplexity XML tags, Manus numbered-pseudocode plan w/ status+reflection)*
`<goal>/<planning_rules>/<output>` tags; a plan as numbered steps each carrying a
status + reflection. The output format is a hard contract, not a suggestion.
→ The lens prompts already do "reason then VERDICT:". Tighten to a 3-field contract:
**evidence cited → reasoning → VERDICT** so the panel can't skip the evidence step.

### 8. Plan-then-act phase separation  *(Devin planning|standard modes, Manus planner module)*
Explicit mode boundary: gather everything in "planning," only edit in "standard." One
tool call per iteration, iterate to completion.
→ Already how I work here (scout → act). Worth making explicit in any multi-step bot
agent so it doesn't act before it has the full picture.

---

## Highest-value concrete upgrade: `analyst_gate.py`

Combine patterns **1 + 2 + 6** into the three position lenses. Today each lens reasons
then votes; it can pass a thesis on vibes. Upgrade:

- **resolution_risk** → require it to QUOTE the resolution-criteria text it's judging,
  and **auto-REFUTE if the thesis depends on a resolution mechanism not present/verified
  in that text** (the touch-vs-close + "unverified" lesson, encoded). This alone would
  have caught both June FAILs structurally, not by luck.
- **edge_is_real** → require it to name the root truth (price-right vs analyst-knows),
  pattern 3.
- **all three** → default-REFUTED-under-uncertainty (pattern 6): if the lens can't cite
  evidence, that's a refutation.

Output contract per lens (pattern 7): `EVIDENCE: <quoted text/data> → REASONING: <2-3 sent> → VERDICT: REFUTED|NOT_REFUTED`.

Net effect: the gate stops rewarding confident theses with unverified mechanics — exactly
the hole the BTC-dip and US-Iran positions fell through.

---

## For the bot's agent prompts generally
- Bake **evidence + exit-code** reporting into any agent that runs commands (DROID).
- **Bounded retry** on RPC/fill/API loops, then log-and-escalate, not spin (Devin/Cursor)
  — fits the existing `heal-not-kill` watchdog ethos.
- **Never hardcode keys where exposed** (Cursor) — already a hard rule; good external confirmation.
- **Sourcing recency + diversity** for any news/whale-signal agent (Perplexity) — mirrors
  `RECENCY>P&L`.
