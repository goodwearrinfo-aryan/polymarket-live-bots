# Resolved Winners Reconciliation

## Discrepancy
Your brief: **9 live + 3 resolved winners ready to close**  
State file shows: **12 live open, 0 resolved marked as "ready to close"**

## Action Required
Please clarify which 3 positions are ready to close:

### Option A: Markets that have resolved but position still open in state
Run this to find candidates:
```bash
cd ~/Documents/polymarket
python3 << 'EOF'
import json
from datetime import datetime, timedelta

with open('scalp_lab_state.json') as f:
    data = json.load(f)

# Markets that resolved more than 1 day ago but still open
now = datetime.now()
one_day_ago = now - timedelta(days=1)

print("=== CANDIDATES: Open positions in resolved markets ===\n")
for leg, state in data.items():
    for pos in state.get('open', []):
        market_slug = pos.get('market_slug', 'N/A')
        opened_at = datetime.fromisoformat(pos.get('opened_at', '').replace('Z', '+00:00'))
        # (You'd need to cross-check with gamma/resolution API to know if market resolved)
        # Placeholder: show all positions > 30 days old
        if (now - opened_at).days > 30:
            print(f"{leg:15} {pos.get('side'):4} @ {pos.get('entry_fill'):.3f}  |  {pos.get('q', 'N/A')[:50]}")
            print(f"                Opened {(now - opened_at).days} days ago\n")

EOF
```

### Option B: Closed trades marked as resolved + profitable
Run this to find closed winners:
```bash
cd ~/Documents/polymarket
python3 << 'EOF'
import json

with open('scalp_lab_state.json') as f:
    data = json.load(f)

print("=== CLOSED PROFITABLE TRADES (exit_reason=resolved) ===\n")
winners = []
for leg, state in data.items():
    for trade in state.get('closed', []):
        if trade.get('exit_reason') == 'resolved' and trade.get('pnl_usd', 0) > 0:
            winners.append({
                'leg': leg,
                'market': trade.get('q', 'N/A')[:50],
                'side': trade.get('side'),
                'pnl': trade.get('pnl_usd'),
                'closed_at': trade.get('closed_at'),
            })

winners.sort(key=lambda x: x['closed_at'], reverse=True)
for w in winners[:10]:
    print(f"{w['leg']:15} {w['side']:4} +${w['pnl']:6.2f}  |  {w['market']}")

print(f"\nTotal resolved winners found: {len(winners)}")

EOF
```

### Option C: User clarification (preferred)
Please respond with:
```
Resolved winner 1: [leg name] [market slug] [side] [pnl]
Resolved winner 2: ...
Resolved winner 3: ...
```

Example:
```
Resolved winner 1: geopolbomb Iran_escalation NO +$27.28
Resolved winner 2: nearres UFC_knockout YES +$12.50
Resolved winner 3: cryptovol BTC_surge NO +$8.75
```

---

## Once Clarified

Update SEVEN_DAY_TEST_MONITOR.md with:
1. Exact market slugs + current P&L
2. Closure procedure (timestamp when closed)
3. Impact on capital reallocation

Then capital allocation can proceed with the freed capital.

---

## Current Open Positions (for reference)

| Leg | Side | Entry | Market | Age |
|-----|------|-------|--------|-----|
| windowshutrand | NO | 0.853 | Bitcoin dip $60k | 10.6h |
| windowshutrand | NO | 0.810 | Bitcoin reach $70k | 16.0h |
| windowshutrand | NO | 0.923 | Bitcoin reach $72.5k | 34.5h |
| windowshutrand | NO | 0.964 | Bitcoin dip $57.5k | 42.0h |
| windowshutrand | NO | 0.948 | Bitcoin dip $55k | 165.9h |
| windowshutrand | NO | 0.963 | Bitcoin reach $75k | 204.7h |
| windowshutrand | NO | 0.963 | Bitcoin dip $55k (June) | 588.5h (STALE?) |
| newsmove | YES | 0.474 | LeBron → Miami | 13.9h |
| newsmove | NO | 0.740 | Lamine Yamal Ballon d'Or | 42.1h |
| newsmove | NO | 0.728 | LeBron → Cavaliers | 42.1h |
| nearterm | YES | 0.952 | Trump Iran sanctions (June) | 891.1h (STALE - LIKELY RESOLVED) |
| nearterm | YES | 0.880 | Claude 5 release (June) | 1080.3h (STALE - LIKELY RESOLVED) |

**Action:** Confirm if the two nearterm positions (Iran sanctions, Claude 5) are actually resolved and should be closed.
