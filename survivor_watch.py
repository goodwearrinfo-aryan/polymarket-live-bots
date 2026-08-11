#!/usr/bin/env python3
"""
survivor_watch.py — standing monitor for the legs LEFT ENABLED after the kill-sweeps.

Deterministic (NO LLM), read-only. The Python twin of the pm-survivor-watch agent. It tracks
every surviving leg and answers one question honestly: is any accumulating into a REAL edge, or
is it lucky-tail noise / a leg that has turned confirmed-dead?

Prior it enforces (the whole project's lesson): the bot is a confirmed null (realized ≈ -$340 /
~4k trades; gap-honest backtest DSR fails all bands). A positive point estimate on small n is
NOISE, not an edge — so a survivor only GRADUATES at n>=30 AND bootstrap CI>0 AND it beats its
control. Optimize DOLLARS, not win-rate.

Silent by design: it iMessages ONLY when a survivor first becomes a GRADUATE-candidate (→ run the
full DSR gate) or KILL-NOW (CI entirely <0 → disable it). Steady-state = quiet. Always appends
survivor_watch_log.jsonl. Dedupes alerts via survivor_watch_state.json so it pings once per event.

Excludes: controls (allin/coinflip/coindown/coinup/*rand — they MUST lose) and feature flags.
Treats the non-predictive arb track (clusterarb/csarb/basket/dataarb/monoarb/multiarb) as
DORMANT-by-design — never KILL-NOW, only eligible to GRADUATE.

Read-only: never edits config, never touches the bot or processes. Fail-soft throughout.

Modes:  python3 survivor_watch.py [once]     (run; alert only on a NEW graduate/kill)
"""
import os, re, json, random, subprocess
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "survivor_watch_state.json")
LOG   = os.path.join(HERE, "survivor_watch_log.jsonl")
TARGETS = ["krisharyan@icloud.com", "+918449447444"]

CTRL = re.compile(r"(allin|coinflip|coindown|coinup|rand)", re.I)
ARB  = re.compile(r"(clusterarb|csarb|basket|dataarb|monoarb|multiarb)", re.I)
SKIP = {"entry_gate", "watchdog"}
GRAD_N = 30


def _send(body):
    safe = body.replace('"', "'").replace("\n", "\\n")
    for t in TARGETS:
        script = ('tell application "Messages"\n  set s to 1st service whose service type = iMessage\n'
                  f'  send "{safe}" to buddy "{t}" of s\nend tell')
        try:
            subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=10)
        except Exception:
            pass


def _boot(xs, B=2000, seed=42):
    n = len(xs)
    if n < 2:
        return None, None
    rng = random.Random(seed)
    ms = sorted(sum(xs[rng.randrange(n)] for _ in range(n)) / n for _ in range(B))
    return ms[int(0.025 * B)], ms[int(0.975 * B)]


def main():
    try:
        import hourly_pnl_alert as h
        st = h.load_state()
        leg_stats = h.leg_stats
    except Exception as e:
        print(f"survivor_watch: cannot load state (fail-soft): {e}")
        return 0
    try:
        src = open(os.path.join(HERE, "scalp_lab.py")).read()
        enabled = {m.group(1) for m in re.finditer(r'"(\w+)_enabled"\s*:\s*True', src)}
    except Exception as e:
        print(f"survivor_watch: cannot read scalp_lab.py (fail-soft): {e}")
        return 0

    # control mean (legs must beat it) — use the worst-case (highest) control mean
    ctrl_means = []
    for leg, b in st.items():
        if CTRL.search(leg) and isinstance(b, dict):
            xs = [r.get("pnl_usdc") for r in b.get("closed", []) if r.get("pnl_usdc") is not None]
            if xs:
                ctrl_means.append(sum(xs) / len(xs))
    ctrl_bar = max(ctrl_means) if ctrl_means else 0.0

    rows, graduates, kills = [], [], []
    for leg in sorted(enabled):
        if CTRL.search(leg) or leg in SKIP:
            continue
        b = st.get(leg, {})
        if not isinstance(b, dict):
            continue
        xs = [r.get("pnl_usdc") for r in b.get("closed", []) if r.get("pnl_usdc") is not None]
        op = len(b.get("open", []))
        n = len(xs)
        if n == 0 and op == 0:
            continue
        mean = sum(xs) / n if n else 0.0
        lo, hi = _boot(xs)
        is_arb = bool(ARB.search(leg))
        if n >= GRAD_N and lo is not None and lo > 0 and mean > ctrl_bar:
            status = "GRADUATE"
            graduates.append((leg, n, mean, lo, hi))
        elif (not is_arb) and n >= 10 and hi is not None and hi < 0:
            status = "KILL-NOW"
            kills.append((leg, n, mean, lo, hi))
        elif is_arb:
            status = "DORMANT-arb"
        elif n < GRAD_N:
            status = "ACCUMULATING"
        else:
            status = "NOISE"
        rows.append({"leg": leg, "n": n, "open": op, "mean": round(mean, 4),
                     "ci": [round(lo, 4) if lo is not None else None,
                            round(hi, 4) if hi is not None else None],
                     "status": status})

    now = datetime.now(timezone.utc).isoformat()
    try:
        with open(LOG, "a") as f:
            f.write(json.dumps({"ts": now, "ctrl_bar": round(ctrl_bar, 4),
                                "graduates": len(graduates), "kills": len(kills),
                                "rows": rows}) + "\n")
    except Exception:
        pass

    # dedupe alerts: ping once per (leg,status) event
    try:
        alerted = set(json.load(open(STATE)).get("alerted", []))
    except Exception:
        alerted = set()
    fresh_msgs = []
    for leg, n, mean, lo, hi in graduates:
        key = f"{leg}:GRADUATE"
        if key not in alerted:
            alerted.add(key)
            fresh_msgs.append(f"GRADUATE? {leg} n={n} mean=${mean:+.3f} CI[{lo:+.3f},{hi:+.3f}] beats ctrl ${ctrl_bar:+.3f} — RUN DSR to confirm the first real edge.")
    for leg, n, mean, lo, hi in kills:
        key = f"{leg}:KILL-NOW"
        if key not in alerted:
            alerted.add(key)
            fresh_msgs.append(f"KILL-NOW {leg} n={n} mean=${mean:+.3f} CI[{lo:+.3f},{hi:+.3f}] entirely <0 — disable it (flip _enabled False).")
    try:
        json.dump({"alerted": sorted(alerted), "updated": now}, open(STATE, "w"))
    except Exception:
        pass

    if fresh_msgs:
        body = "🔬 survivor-watch:\n" + "\n".join(fresh_msgs)
        print(body)
        _send(body)
    else:
        ng = sum(1 for r in rows if r["status"] == "GRADUATE")
        print(f"survivor_watch: {len(rows)} survivors, {ng} graduate / {len(kills)} kill-now, "
              f"0 new events — null holds, quiet.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
