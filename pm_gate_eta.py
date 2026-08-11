#!/usr/bin/env python3
"""
pm_gate_eta.py — time-to-DECIDABILITY for every accumulating paper leg.

The fleet already ALARMS on the two events that matter (control-bug via
controls_logger, gate-cross via fade_checkpoint/dsr_gate) and reports gate
*distance* ("29 more exits needed") via the night analyst. The gap this fills:
nothing autonomously answers **"when will we actually KNOW?"** — the power
analysis (how many MORE exits until a CI could even exclude zero at the current
effect size) and the projected CALENDAR DATE at the current exit rate.

This is pure math over the same honest data dsr_gate reads (scalp_lab_state.json
closed exits) — NO LLM, NO new fill model. It does not claim an edge; it tells
you when the experiment becomes decidable, so accumulation has a deadline, not
a vibe.

Honest framing baked in:
- Reaching n* is NECESSARY, not SUFFICIENT — the real gate is bootstrap-CI>0
  AND beats-control AND DSR>0. n* only answers "could the CI even exclude 0 yet".
- The power estimate assumes the current sample mean/std persist (they won't
  exactly) — it's an order-of-magnitude planning date, not a promise.
- A leg with mean<=0 can NEVER reach CI>0 at its current sign — reported as such,
  not given a fake ETA.
- Controls (allin/coinflip/coindown/basketrand) are labelled CONTROL — a positive
  control is a marking BUG (controls_logger's job), never a graduation here.

Paper-only, stdlib + project modules only, read-only. iMessage ONLY when a
non-control leg is already decidable or projected decidable within 7 days.
"""
import os, sys, json, math, subprocess
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
STATE = os.path.join(HERE, "scalp_lab_state.json")
VAULT = "/Users/aryanagarwal/Documents/PolymarketVault/Reports"

try:
    from edge_screen_logger import _bootstrap_ci
except Exception:
    _bootstrap_ci = None

GATE_N = 30
MIN_N_ETA = 5   # below this, mean/std/rate are noise — no ETA, no alarm (the thin-positive trap)
CONTROLS = {"allin", "coinflip", "coindown", "basketrand", "coinup"}
Z = 1.96  # 95%


def _imsg(msg):
    try:
        subprocess.run(["osascript", "-e",
            f'tell application "Messages" to send "{msg}" to buddy "krisharyan@icloud.com"'],
            timeout=15, capture_output=True)
    except Exception:
        pass


def _parse_ts(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def load():
    """leg -> list of (pnl, closed_at_dt) for closed exits, in file order."""
    s = json.load(open(STATE))
    out = {}
    for k, v in s.items():
        if not (isinstance(v, dict) and "closed" in v):
            continue
        recs = []
        for t in v.get("closed", []):
            p = t.get("pnl_usdc")
            if p is None:
                continue
            recs.append((float(p), _parse_ts(t.get("closed_at"))))
        if recs:
            out[k] = recs
    return out


def power_n_star(mean, std):
    """Smallest n at which the normal-approx 95% CI lower bound > 0 (mean>0 only)."""
    if mean <= 0 or std <= 0:
        return None
    return math.ceil((Z * std / mean) ** 2)


def exit_rate_per_day(times):
    ts = sorted([t for t in times if t is not None])
    if len(ts) < 2:
        return None
    span = (ts[-1] - ts[0]).total_seconds() / 86400.0
    if span <= 0:
        return None
    return (len(ts) - 1) / span


def analyze():
    legs = load()
    now = datetime.now(timezone.utc)
    rows = []
    for name, recs in legs.items():
        pnls = [p for p, _ in recs]
        times = [t for _, t in recs]
        n = len(pnls)
        mean = sum(pnls) / n
        std = (sum((x - mean) ** 2 for x in pnls) / (n - 1)) ** 0.5 if n > 1 else 0.0
        ci_lo = ci_hi = None
        if _bootstrap_ci and n >= 2:
            try:
                ci_lo, ci_hi = _bootstrap_ci(pnls)
            except Exception:
                pass
        is_control = name in CONTROLS
        rate = exit_rate_per_day(times)

        status, eta_days, eta_date, need = "", None, None, None
        if is_control:
            status = "CONTROL"  # positive = bug, not graduation (controls_logger owns the alarm)
        elif n < MIN_N_ETA:
            status = f"TOO FEW (n={n}) — accumulating, mean/rate are noise"
        elif mean <= 0:
            status = "NEGATIVE — can't reach CI>0 at current sign"
        else:
            n_star = power_n_star(mean, std)
            if ci_lo is not None and ci_lo > 0 and n >= GATE_N:
                status = "DECIDABLE NOW (CI>0, n>=30) — run full gate"
                need = 0
            elif n_star is None:
                status = "POSITIVE but undecidable (std≈0?)"
            else:
                target = max(GATE_N, n_star)
                need = max(0, target - n)
                if need == 0:
                    status = "POWER-READY — CI could exclude 0; run full gate"
                elif rate and rate > 0:
                    eta_days = need / rate
                    if eta_days > 3650:  # >10y → effect size too small to ever decide
                        status = (f"effectively never (~{eta_days/365:.0f}y, {need:,} more exits "
                                  f"@ {rate:.2f}/day) — effect size too small")
                        eta_days = None
                    else:
                        eta_date = now.timestamp() + eta_days * 86400
                        status = f"ETA ~{eta_days:.0f}d ({need} more exits @ {rate:.2f}/day)"
                else:
                    status = f"{need} more exits (rate unknown — no ETA)"
        rows.append(dict(name=name, n=n, mean=mean, ci_lo=ci_lo, ci_hi=ci_hi,
                         control=is_control, status=status, need=need,
                         eta_days=eta_days, eta_date=eta_date, rate=rate))
    # sort: actionable (positive, soonest ETA) first; controls + negatives last
    def key(r):
        if r["control"]:
            return (4, 0)
        if r["mean"] <= 0:
            return (3, 0)
        if r["n"] < MIN_N_ETA:
            return (2, -r["n"])  # too-few positives below the real ETAs
        if r["eta_days"] is None:
            return (0, -r["n"])  # decidable/power-ready first
        return (1, r["eta_days"])
    rows.sort(key=key)
    return rows, now


def fmt_date(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%d") if epoch else "—"


def main():
    rows, now = analyze()
    ts = now.strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"# Gate ETA — time-to-decidability  ({ts})", ""]
    lines.append("_n*=normal-approx exits for CI-lo>0; NECESSARY not sufficient "
                 "(real gate = bootstrap-CI>0 ∧ beats-control ∧ DSR>0). "
                 "ETA assumes current mean/std persist — a planning date, not a promise._")
    lines.append("")
    lines.append("| leg | n | mean$ | CI | status |")
    lines.append("|---|---|---|---|---|")
    for r in rows:
        ci = f"[{r['ci_lo']:+.3f},{r['ci_hi']:+.3f}]" if r["ci_lo"] is not None else "—"
        eta = f" → ~{fmt_date(r['eta_date'])}" if r["eta_date"] else ""
        lines.append(f"| {r['name']} | {r['n']} | {r['mean']:+.4f} | {ci} | {r['status']}{eta} |")
    out = "\n".join(lines) + "\n"

    try:
        os.makedirs(VAULT, exist_ok=True)
        open(os.path.join(VAULT, "gate_eta.md"), "w").write(out)
    except Exception:
        pass
    print(out)

    # actionable alarm ONLY: a non-control leg decidable now or within 7 days
    hot = [r for r in rows if not r["control"] and r["mean"] > 0
           and (r["need"] == 0 or (r["eta_days"] is not None and r["eta_days"] <= 7))]
    if hot:
        top = hot[0]
        when = "NOW" if top["need"] == 0 else f'~{top["eta_days"]:.0f}d'
        _imsg(f"GATE ETA: leg `{top['name']}` decidable {when} "
              f"(n={top['n']}, mean ${top['mean']:+.4f}) — run dsr_gate before any real money.")
        print(f"[pm_gate_eta] ALARM sent: {top['name']}")
    else:
        print("[pm_gate_eta] no leg decidable within 7d — quiet (no iMessage).")


if __name__ == "__main__":
    main()
