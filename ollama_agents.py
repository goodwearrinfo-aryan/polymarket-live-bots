#!/usr/bin/env python3
"""
10 Ollama Agents for 7-Day Test Autonomy
Each agent runs via local ollama (llama3.1:8b), zero API cost.

Usage:
  python3 ollama_agents.py --alert-monitor      (agent 1)
  python3 ollama_agents.py --stress-test        (agent 2)
  python3 ollama_agents.py --strategy-mutate    (agent 3)
  ... etc for all 10

Or run all: python3 ollama_agents.py --all
"""

import subprocess
import json
from pathlib import Path
from datetime import datetime
import sys

def call_ollama(prompt, model="llama3.1:8b"):
    """Call local ollama and return structured result."""
    try:
        result = subprocess.run(
            ["ollama", "run", model],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=120
        )
        return result.stdout.strip() if result.returncode == 0 else None
    except:
        return None

# ============================================================================
# AGENT 1: Alert Monitor
# ============================================================================

def agent_alert_monitor():
    """Ollama: Decide what warrants an alert (Slack/email)."""
    print("\n[AGENT 1] Alert Monitor — What should we alert on?")

    with open("SEVEN_DAY_TEST_LOG.json") as f:
        logs = json.load(f)

    latest = logs[-1] if logs else {}

    prompt = f"""
Analyze this test day's metrics and decide: should we send an ALERT?

DAY: {latest.get('day', '?')}/7
Edge per-trade: ${latest.get('backtest', {}).get('edge_per_trade', 0):.4f}
Open positions: {latest.get('open_positions', 0)}
Ollama verdict: {latest.get('ollama_verdict', 'N/A')[:100]}...

Respond with EXACTLY this JSON (no other text):
{{
  "should_alert": true/false,
  "severity": "critical/warning/info",
  "message": "reason for alert or empty",
  "channel": "slack/email/none"
}}
"""

    result = call_ollama(prompt)
    if result:
        try:
            data = json.loads(result)
            print(f"  Alert: {data.get('should_alert')} ({data.get('severity')})")
            if data.get('should_alert'):
                print(f"  Message: {data.get('message')}")
            return data
        except:
            print(f"  Parse error: {result[:100]}")
    return {"should_alert": False}

# ============================================================================
# AGENT 2: Stress Test
# ============================================================================

def agent_stress_test():
    """Ollama: Simulate crash scenario (what if BTC drops 20%?)."""
    print("\n[AGENT 2] Stress Test — What if BTC crashes 20%?")

    with open("scalp_lab_state.json") as f:
        state = json.load(f)

    # Get windowshutrand positions (BTC bets)
    btc_positions = []
    if 'windowshutrand' in state:
        btc_positions = state['windowshutrand'].get('open', [])[:3]

    position_summary = json.dumps([
        {"side": p.get('side'), "entry": p.get('entry_fill')}
        for p in btc_positions
    ])

    prompt = f"""
Stress test: BTC crashes 20% (e.g., $65k → $52k).

Your positions (NO-side bets on range):
{position_summary}

Estimate impact:
1. P&L on each position (assume linear price impact)
2. Total unrealized loss
3. Which positions get liquidated?
4. Recommendation: hold or close?

Respond JSON:
{{
  "btc_crash_scenario": "-20%",
  "positions_affected": N,
  "estimated_loss": "$X.XX",
  "liquidation_risk": "high/medium/low",
  "action": "hold/close_risky/close_all"
}}
"""

    result = call_ollama(prompt)
    if result:
        try:
            data = json.loads(result)
            print(f"  Loss estimate: {data.get('estimated_loss')}")
            print(f"  Liquidation risk: {data.get('liquidation_risk')}")
            print(f"  Action: {data.get('action')}")
            return data
        except:
            print(f"  Parse error: {result[:100]}")
    return {}

# ============================================================================
# AGENT 3: Strategy Mutate
# ============================================================================

def agent_strategy_mutate():
    """Ollama: Generate strategy variants to test."""
    print("\n[AGENT 3] Strategy Mutate — Test variant strategies")

    prompt = """
Your best leg: windowshutrand (BTC range NO-side bets)
Current edge: $0.1900/trade

Generate 3 VARIANT strategies to test in parallel:

1. Tighter ranges (trade $68k-$72k instead of $55k-$75k)
   - Rationale?
   - Expected edge change?

2. Longer hold times (remove time-stop, only use price stops)
   - Rationale?
   - Expected edge change?

3. Hybrid: Mix with volatility fade (short vol on range breaks)
   - Rationale?
   - Expected edge change?

Respond JSON:
{{
  "variants": [
    {{"name": "variant_1", "description": "...", "expected_edge": "+0.XX"}},
    {{"name": "variant_2", "description": "...", "expected_edge": "+0.XX"}},
    {{"name": "variant_3", "description": "...", "expected_edge": "+0.XX"}}
  ]
}}
"""

    result = call_ollama(prompt)
    if result:
        try:
            data = json.loads(result)
            for v in data.get('variants', []):
                print(f"  {v.get('name')}: {v.get('expected_edge')}")
            return data
        except:
            print(f"  Parse error: {result[:100]}")
    return {}

# ============================================================================
# AGENT 4: Auto Prune
# ============================================================================

def agent_auto_prune():
    """Ollama: Decide which legs to kill (underperformers)."""
    print("\n[AGENT 4] Auto Prune — Kill underperformers")

    with open("scalp_lab_state.json") as f:
        state = json.load(f)

    # Summarize leg performance
    legs_summary = []
    for leg, leg_state in list(state.items())[:10]:  # First 10 legs
        closed = leg_state.get('closed', [])
        if len(closed) >= 5:
            pnl_sum = sum(t.get('pnl_usdc') or 0 for t in closed)
            wr = sum(1 for t in closed if (t.get('pnl_usdc') or 0) > 0) / len(closed) * 100
            legs_summary.append({"leg": leg, "trades": len(closed), "win_rate": wr, "total_pnl": pnl_sum})

    prompt = f"""
Kill underperformers? Review these legs:

{json.dumps(legs_summary, indent=2)}

Rules:
- Kill if: n>=10 AND win_rate<15% AND pnl<-$1
- Kill if: no trades in 48h (stale)
- Keep if: win_rate>=30% OR recent positive trend

Respond JSON:
{{
  "kill_list": ["leg1", "leg2"],
  "keep_list": ["leg3", "leg4"],
  "rationale": "..."
}}
"""

    result = call_ollama(prompt)
    if result:
        try:
            data = json.loads(result)
            if data.get('kill_list'):
                print(f"  Kill: {', '.join(data.get('kill_list'))}")
            print(f"  Keep: {len(data.get('keep_list', []))} legs")
            return data
        except:
            print(f"  Parse error: {result[:100]}")
    return {}

# ============================================================================
# AGENT 5: Hot Reload
# ============================================================================

def agent_hot_reload():
    """Ollama: Decide if we should reload/swap a strategy mid-test."""
    print("\n[AGENT 5] Hot Reload — Swap strategies without restart?")

    prompt = """
Mid-test strategy reload decision:

Current: scalp (31% WR, -$369 total)
New candidate: arb detector (IMDEA-style complete-set arb)

Should we hot-reload mid-test? Why/why not?

Consider:
- Test contamination risk?
- Continuity of hypothesis?
- Upside if new strategy works?
- Cost of restart?

Respond JSON:
{{
  "reload_now": true/false,
  "timing": "immediately/on_next_stop_restore/after_test",
  "rationale": "...",
  "risk_level": "high/medium/low"
}}
"""

    result = call_ollama(prompt)
    if result:
        try:
            data = json.loads(result)
            print(f"  Reload: {data.get('reload_now')} ({data.get('timing')})")
            print(f"  Risk: {data.get('risk_level')}")
            return data
        except:
            print(f"  Parse error: {result[:100]}")
    return {}

# ============================================================================
# AGENT 6-10: Simplified placeholders
# ============================================================================

def agent_github_issues():
    """Create GitHub issues for red verdicts."""
    print("\n[AGENT 6] GitHub Issues — Auto-create on failures")
    print("  Placeholder: Would create issue on RED verdict")

def agent_dashboard_gen():
    """Generate HTML dashboard from daily logs."""
    print("\n[AGENT 7] Dashboard Gen — Create shareable HTML report")
    print("  Placeholder: Would generate daily HTML summary")

def agent_sharpe_tracker():
    """Track Sharpe improvement over 7 days."""
    print("\n[AGENT 8] Sharpe Tracker — Day-over-day improvement")
    print("  Placeholder: Would compute Sharpe trends")

def agent_telegram_bot():
    """Send verdicts via Telegram."""
    print("\n[AGENT 9] Telegram Bot — Send alerts to Telegram")
    print("  Placeholder: Would push daily verdict to Telegram")

def agent_a_b_test():
    """Run A/B test: stops vs no-stops on subset of positions."""
    print("\n[AGENT 10] A/B Test — Split positions, test both regimes")
    print("  Placeholder: Would randomize 50% of positions into stops-ON group")

# ============================================================================
# MAIN
# ============================================================================

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1

    cmd = sys.argv[1]

    agents = {
        "--alert-monitor": agent_alert_monitor,
        "--stress-test": agent_stress_test,
        "--strategy-mutate": agent_strategy_mutate,
        "--auto-prune": agent_auto_prune,
        "--hot-reload": agent_hot_reload,
        "--github-issues": agent_github_issues,
        "--dashboard-gen": agent_dashboard_gen,
        "--sharpe-tracker": agent_sharpe_tracker,
        "--telegram-bot": agent_telegram_bot,
        "--a-b-test": agent_a_b_test,
    }

    if cmd == "--all":
        print("\n" + "="*70)
        print("RUNNING ALL 10 OLLAMA AGENTS")
        print("="*70)
        for agent_func in agents.values():
            agent_func()
        print("\n" + "="*70)
        print("✓ All agents completed")
        print("="*70)
        return 0

    if cmd in agents:
        agents[cmd]()
        return 0

    print(f"Unknown command: {cmd}")
    return 1

if __name__ == "__main__":
    sys.exit(main())
