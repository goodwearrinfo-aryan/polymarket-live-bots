#!/usr/bin/env python3
"""test_calib_shrink.py — verifies the free-LLM tail-shrinkage calibration fix in candela_args.

Covers the design verify_plan: helper math, exclusions (non-tail / small-gap / boundary),
provenance guard, polarity guard, tail-overlay coexistence, and the no-edge-inflation property.
Self-contained (plain asserts, no pytest needed): `python3 test_calib_shrink.py` -> exits 0 if all pass.
"""
import sys, math
import candela_args as ca
import judge_panel

PASS = 0
def ok(cond, name):
    global PASS
    assert cond, f"FAIL: {name}"
    PASS += 1
    print(f"  ok  {name}")

def close(a, b, tol=1e-9): return abs(a - b) <= tol

# ── UNIT: helper math (tail + big gap -> shrink toward 0.5 = 0.2 + 0.6*p) ──
e, s, _ = ca._shrink_llm_tail(0.15, 0.605); ok(close(e, 0.29) and s, "Hormuz 0.15/0.605 -> 0.29 shrunk")
e, s, _ = ca._shrink_llm_tail(0.17, 0.50);  ok(close(e, 0.302) and s, "Iran 0.17/0.50 -> 0.302 shrunk")
e, s, _ = ca._shrink_llm_tail(0.85, 0.30);  ok(close(e, 0.71) and s, "high tail 0.85/0.30 -> 0.71 shrunk")

# ── UNIT: non-tail untouched (p in (0.20,0.80) never shrinks, regardless of gap) ──
e, s, _ = ca._shrink_llm_tail(0.40, 0.136); ok(close(e, 0.40) and not s, "non-tail 0.40 untouched (BTC dip)")
e, s, _ = ca._shrink_llm_tail(0.79, 0.10);  ok(close(e, 0.79) and not s, "0.79 just inside hi-tail untouched")

# ── UNIT: small-gap untouched (free LLM AGREEING with a longshot market is not overconfidence) ──
e, s, _ = ca._shrink_llm_tail(0.04, 0.155); ok(close(e, 0.04) and not s, "small-gap 0.04/0.155 (Hezbollah) untouched")
e, s, _ = ca._shrink_llm_tail(0.035, 0.153); ok(close(e, 0.035) and not s, "small-gap 0.035/0.153 (Renan) untouched")
e, s, _ = ca._shrink_llm_tail(0.17, 0.348); ok(close(e, 0.17) and not s, "Iran 0.17/0.348 gap=0.178<0.20 untouched")

# ── UNIT: boundary (gap strict > ; p inclusive <= / >=) ──
e, s, _ = ca._shrink_llm_tail(0.10, 0.30);  ok(close(e, 0.10) and not s, "gap exactly 0.20 does NOT fire (strict >)")
e, s, _ = ca._shrink_llm_tail(0.20, 0.50);  ok(close(e, 0.32) and s, "p exactly 0.20 DOES fire (inclusive <=)")
e, s, _ = ca._shrink_llm_tail(0.80, 0.50);  ok(close(e, 0.68) and s, "p exactly 0.80 DOES fire (inclusive >=)")

# ── UNIT: None / bad input is safe ──
e, s, _ = ca._shrink_llm_tail(None, 0.5); ok(e is None and not s, "None p safe")
e, s, _ = ca._shrink_llm_tail(0.1, None); ok(close(e, 0.1) and not s, "None entry safe (no market -> no shrink)")

# ── PROPERTY: no-edge-inflation. Over a grid, the shrink can only move p toward 0.5 and can
#    NEVER increase the YES edge (conviction - entry) when p>entry, nor flip a non-edge to edge. ──
grid = [i / 100 for i in range(0, 101)]
for p in grid:
    for entry in grid:
        eff, shrank, _ = ca._shrink_llm_tail(p, entry)
        if shrank:
            ok_closer = abs(eff - 0.5) <= abs(p - 0.5) + 1e-12          # never farther from 0.5
            between = min(p, 0.5) - 1e-12 <= eff <= max(p, 0.5) + 1e-12  # stays between p and 0.5
            # gate is YES-framed: edge = conviction - entry. shrink must never INCREASE a positive edge.
            no_inflate = (eff - entry) <= (p - entry) + 1e-12 if p > entry else True
            assert ok_closer and between and no_inflate, f"PROPERTY FAIL p={p} entry={entry} eff={eff}"
PASS += 1; print("  ok  property: shrink always moves toward 0.5, never inflates YES edge (grid 101x101)")

# ── INTEGRATION via _maybe_gate: patch provenance + spy on the gate's conviction ──
_captured = {}
_real_eval = judge_panel.JudgePanel.evaluate_bet
def _spy(self, thesis, conviction, **kw):
    _captured["conviction"] = conviction
    return True, {}  # canned survive; we only care which conviction was priced
judge_panel.JudgePanel.evaluate_bet = _spy

def gate(p, entry, served=("groq", "m", False), side="YES"):
    ca.last_served = (lambda: served)            # monkeypatch provenance in candela_args namespace
    _captured.clear()
    arg = {"p_estimate": p, "synthesis": "x has 3 reasons", "evidence": [], "side": side}
    out = ca._maybe_gate(arg, "crypto", entry, market_id="0xabc")
    return out, _captured.get("conviction")

# free-LLM tail conviction shrinks, and the SHRUNK value is what the gate prices
out, conv = gate(0.15, 0.605)
cs = out["verdicts"]["calibration_shrink"]
ok(cs["applied"] and close(cs["effective_p"], 0.29) and close(conv, 0.29), "INT free-LLM tail shrinks, gate prices 0.29")

# provenance guard: not free-LLM-produced (last_served None) -> NOT shrunk, gate prices RAW p
out, conv = gate(0.15, 0.605, served=None)
cs = out["verdicts"]["calibration_shrink"]
ok((not cs["applied"]) and close(conv, 0.15) and "not free-LLM" in cs["reasoning"], "INT provenance guard: market-implied untouched")

# agreeing conviction (small gap) not shrunk
out, conv = gate(0.04, 0.155)
ok((not out["verdicts"]["calibration_shrink"]["applied"]) and close(conv, 0.04), "INT agreeing longshot not shrunk")

# polarity-flip guard: side=NO, p>=0.80, mkt<0.20 -> skipped, raw priced (Trump signature)
out, conv = gate(0.95, 0.095, side="NO")
cs = out["verdicts"]["calibration_shrink"]
ok((not cs["applied"]) and close(conv, 0.95) and "polarity" in cs["reasoning"], "INT polarity-flip skipped, raw priced")

# ── REGRESSION: tail-sanity overlay still independently REJECTS on a near-impossible market,
#    and judges the RAW p (not the shrunk eff_p). p=0.50 on entry=0.02 -> overlay refutes. ──
judge_panel.JudgePanel.evaluate_bet = _real_eval  # restore: need real verdicts dict to merge tail_sanity
def _spy2(self, thesis, conviction, **kw):
    _captured["conviction"] = conviction
    return True, {"edge": {"survived_refutation": True, "reasoning": "stub"}}
judge_panel.JudgePanel.evaluate_bet = _spy2
out, conv = gate(0.50, 0.02)
ts = out["verdicts"]["tail_sanity"]
ok((not ts["survived_refutation"]) and (out["survived"] is False), "REGRESSION tail overlay still refutes near-impossible market")
ok("p=0.50" in ts["reasoning"], "REGRESSION tail overlay judged the RAW p (0.50), not a shrunk value")
judge_panel.JudgePanel.evaluate_bet = _real_eval

# ── REGRESSION: the real _print() must render the heterogeneous calibration_shrink verdicts entry
#    WITHOUT KeyError. The adversarial verify caught this: the mocked tests above never call _print,
#    so the missing-survived_refutation crash on every gated CLI run slipped past 20/20 green. ──
import io, contextlib
judge_panel.JudgePanel.evaluate_bet = lambda self, thesis, conviction, **kw: (True, {"edge": {"survived_refutation": True, "reasoning": "stub"}})
ca.last_served = (lambda: ("groq", "m", False))
gate_out = ca._maybe_gate({"p_estimate": 0.15, "synthesis": "x 3", "evidence": [], "side": "YES"}, "crypto", 0.605, "0xabc")
full_arg = {"question": "Q?", "data_backed_fraction": 0.5, "evidence": [], "bull_case": "b",
            "bear_case": "r", "synthesis": "s", "p_estimate": 0.15, "confidence": 50,
            "what_would_change_my_mind": "w", "honesty_flag": "ok", "gate": gate_out}
_buf = io.StringIO()
with contextlib.redirect_stdout(_buf):
    ca._print(full_arg)   # must NOT raise KeyError 'survived_refutation'
ok("calibration_shrink" in _buf.getvalue(), "REGRESSION _print renders calibration_shrink entry, no KeyError")
judge_panel.JudgePanel.evaluate_bet = _real_eval

print(f"\nALL {PASS} CHECKS PASSED")
sys.exit(0)
