"""Known-answer benchmark task for vetting any LLM framework.

The point: a framework only earns adoption if it makes the SAME model do the task
at least as well as a plain call. So we use a task with KNOWN-CORRECT answers and
score framework-vs-baseline. Swap CASES/PROMPT for whatever judgment you care about;
keep the `expect` field so scoring is objective.

Current task = Polymarket wallet copyability (the real bot decision), 2 cases:
  0xc76f -> DROP (100% 5-min coinflip markets, uncopyable at copy lag)
  0xbd04 -> KEEP (multi-day geopolitical event markets, sharp info)
"""

CASES = [
    {"id": "0xc76f", "expect": "DROP",
     "facts": "75.4% win rate, $31,175 realized pnl, 496 closed trades, 372 on-chain fills. "
              "100% of its trades are 5-minute 'Bitcoin Up or Down' / 'XRP Up or Down' markets "
              "that resolve within 5-15 minutes."},
    {"id": "0xbd04", "expect": "KEEP",
     "facts": "25.6% win rate, $109,504 realized pnl, only 86 closed trades, avg bet $20,435. "
              "Trades multi-day geopolitical event markets (US-Iran diplomacy, Israel-Iran "
              "ceasefire) with multi-day to multi-week resolution windows."},
]

PROMPT = (
    "You vet Polymarket wallets for COPY-TRADING. A copy bot mirrors a wallet's BUY ~30-60s "
    "after it trades. Verdict KEEP only if the wallet is a genuinely sharp INFORMATION trader "
    "whose edge survives a 30-60s copy lag; verdict DROP if it is coinflip-HFT (sub-15-min "
    "markets that resolve before any copy lands) or otherwise uncopyable.\n\n"
    "Wallet facts: {facts}\n\n"
    "Answer with EXACTLY one word first — KEEP or DROP — then one sentence why."
)

import re
def score(text, expect):
    """Objective verdict extraction from the first ~40 chars."""
    head = (text or "")[:40]
    keep = bool(re.search(r"\bKEEP\b", head, re.I))
    drop = bool(re.search(r"\bDROP\b", head, re.I))
    v = "KEEP" if keep and not drop else ("DROP" if drop and not keep else "?")
    return v, (v == expect)
