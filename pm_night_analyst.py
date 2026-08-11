#!/usr/bin/env python3
"""
pm_night_analyst.py — the autonomous "analyst on the night shift" as a Python runner on the
FREE LLM (NVIDIA 70B primary → Ollama keyless failover, zero Claude cost). Ports the
pm-night-analyst subagent (~/.claude/agents/pm-night-analyst.md): while Aryan is offline the
fleet keeps logging but nobody reads ACROSS it. This synthesizes ~50 loggers + the arb books
into ONE prioritized, dollars-first brief, written to the vault and printed.

Discipline (inherited, non-negotiable):
- PAPER only; optimize DOLLARS not win-rate; never propose a real order.
- A positive point estimate on small n is NOISE, not edge. Only n≥30 AND CI>0 AND beats-control
  AND DSR>0 is an edge. Say "accumulating", never "winning".
- Controls (allin/coinflip/coindown, basketrand) MUST be negative. A positive control at n≥30
  is a MARKING BUG → the #1 flag.
- Read-only. Synthesis, never action.

Numbers are computed in Python (never eyeball arithmetic); the LLM only SYNTHESIZES/RANKS.
If NO LLM is reachable (cloud key dead AND ollama down), a DETERMINISTIC fallback brief is
written and STAMPED as such — the night shift never goes dark.

iMessage fires ONLY on a material flag (control bug / arb lock that didn't pay / gate cross /
offline floor down), deduped in pm_night_state.json. A quiet night = a correct brief, no ping.

Usage: python3 pm_night_analyst.py        # gather → synthesize → write Reports/night_brief.md
"""
import os, sys, json, time

BASE = os.path.dirname(os.path.abspath(__file__))
NOW = int(time.time())
VAULT = os.path.expanduser("~/Documents/PolymarketVault/Reports")
STATE = os.path.join(BASE, "pm_night_state.json")
RECIPIENTS = ["krisharyan@icloud.com", "+918449447444"]
MIDEAST = ("hormuz", "iran", "israel", "hezbollah", "gaza", "mideast", "strait")


def _read_json(name, default=None):
    try:
        return json.load(open(os.path.join(BASE, name)))
    except (OSError, ValueError):
        return default


def _tail(name, n=6):
    try:
        with open(os.path.join(BASE, name)) as f:
            return [l.rstrip() for l in f.readlines()[-n:]]
    except OSError:
        return []


def _imsg(msg):
    safe = msg.replace('"', "'")[:480]
    for t in RECIPIENTS:
        os.system('osascript -e \'tell application "Messages" to send "%s" to buddy "%s" '
                  'of (service 1 whose service type is iMessage)\' >/dev/null 2>&1' % (safe, t))


# ── gather (deterministic) ───────────────────────────────────────────────────────────────
def gather():
    f = {}

    # fleet health from the autonomy ledger (written by pm_autonomy.py)
    led = _read_json("pm_autonomy_ledger.json", {})
    items = led.get("items", []) if isinstance(led, dict) else []
    f["floor_down"] = any(i.get("state") == "FLOOR-DOWN" for i in items)
    f["all_fallback"] = any(i.get("state") == "ALL-FALLBACK" for i in items)
    f["stale_workers"] = [i["worker"] for i in items if i.get("state") == "STALE"]
    f["ledger_age_min"] = (NOW - led.get("ts", NOW)) // 60 if isinstance(led, dict) and led.get("ts") else None

    # basket_paper book — the structural-lock track record
    bp = _read_json("basket_paper_book.json", {"open": [], "closed": []})
    closed = bp.get("closed", []) if isinstance(bp, dict) else []
    f["basket_open"] = len(bp.get("open", [])) if isinstance(bp, dict) else 0
    f["basket_closed"] = len(closed)
    f["basket_realized"] = round(sum(c.get("realized_pnl", 0) or 0 for c in closed), 4)
    f["basket_locks_failed"] = sum(1 for c in closed if c.get("lock_held") is False)

    # dataarb settled-but-mispriced track
    da = _read_json("dataarb_state.json", {})
    f["dataarb_open"] = len(da.get("open", [])) if isinstance(da, dict) else 0
    f["dataarb_resolved"] = len(da.get("resolved", [])) if isinstance(da, dict) else 0

    # arb_memory — realization (does the lock pay what entry promised?) + controls
    am = _read_json("arb_memory.json", {})
    f["arb_realization"] = (am.get("realization", {}).get("ALL", {}) if isinstance(am, dict) else {})
    f["arb_controls"] = am.get("controls", {}) if isinstance(am, dict) else {}
    f["arb_n_real"] = am.get("n_real", 0) if isinstance(am, dict) else 0

    # combined-CI gate distance + verdict straight from the track digests
    f["arb_track_tail"] = [l.strip() for l in _tail("arb_track.log", 5) if l.strip()]
    f["multiarb_tail"] = [l.strip() for l in _tail("multiarb.log", 4) if l.strip()]

    # analyst book — structure + gate survivors + driver concentration (correlation lesson)
    ap = _read_json("analyst_positions.json", {})
    pos = ap.get("positions", []) if isinstance(ap, dict) else []
    f["analyst_n"] = len(pos)
    f["analyst_gated"] = sum(1 for p in pos if (p.get("gate") or {}).get("survived"))
    mid = sum(1 for p in pos if any(k in (p.get("slug", "") + p.get("thesis", "")).lower() for k in MIDEAST))
    f["analyst_mideast"] = mid
    f["analyst_mideast_share"] = round(mid / len(pos), 2) if pos else 0

    # control-bug check (the #1 flag): any resolved control positive at n≥30
    f["control_bug"] = False
    for cname, c in (f["arb_controls"] or {}).items():
        if isinstance(c, dict) and c.get("n", 0) >= 30 and (c.get("realized", 0) or 0) > 0:
            f["control_bug"] = True
    return f


# ── material-flag detection (for iMessage; deterministic, deduped) ──────────────────────────
def material_flags(f):
    flags = []
    if f["control_bug"]:
        flags.append("control_bug")          # marking bug — highest priority
    if f["floor_down"]:
        flags.append("floor_down")           # offline keyless net gone
    if f["basket_locks_failed"]:
        flags.append("basket_lock_failed")   # a lock that DIDN'T pay = the thing basket_paper exists to catch
    # gate cross: any track digest line saying a gate is met (not "not yet")
    if any("GATE:" in l and "not yet" not in l.lower() for l in f["arb_track_tail"]):
        flags.append("gate_cross")
    return flags


# ── synthesize ──────────────────────────────────────────────────────────────────────────
def llm_brief(f):
    """Return (brief_text, provenance_label) or (None, None) if no LLM reachable."""
    try:
        import llm_client
        if not llm_client.configured():
            return None, None
        sys_p = (
            "You are the night-shift analyst for a PAPER-ONLY Polymarket bot. Turn the JSON facts "
            "into ONE brief a tired human reads in 30 seconds. RULES: dollars-first; a positive "
            "point estimate on small n is NOISE not edge (say 'accumulating', never 'winning'); "
            "only n>=30 AND CI>0 AND beats-control AND DSR>0 is an edge; controls MUST be negative "
            "(a positive control at n>=30 is a MARKING BUG = the #1 item); collapse correlated "
            "positions (mideast cluster = ~1 bet). Output <=12 lines, EXACTLY these sections:\n"
            "1. Headline: the one thing that matters, or 'Quiet - steady-state null, nothing actionable.'\n"
            "2. Money track: arb (basket/data/mono) - new lock? realization ratio? gate distance? control losing?\n"
            "3. Gate watch: anything approaching n>=30? how many exits out?\n"
            "4. Health flags: stale logger / dead floor / all-fallback judgments / control positive.\n"
            "5. Caveat: what you could NOT read. No preamble, no markdown headers.")
        txt = llm_client.chat([{"role": "system", "content": sys_p},
                               {"role": "user", "content": json.dumps(f, indent=2)}],
                              max_tokens=600, timeout=60).strip()
        prov = llm_client.provenance()
        label = ("fallback-served:%s" % prov.get("served_model") if prov.get("served_fallback")
                 else "strong:%s" % prov.get("served_model"))
        return txt, label
    except Exception as e:
        print(f"[pm_night_analyst] LLM unavailable: {type(e).__name__}: {str(e)[:80]}")
        return None, None


def fallback_brief(f):
    """Deterministic brief when NO LLM is reachable — the night shift never goes dark."""
    real = f["arb_realization"] or {}
    ratio = real.get("ratio")
    to_gate = max(0, 30 - f["arb_n_real"])
    head = ("⚠️ CONTROL POSITIVE at n>=30 — MARKING BUG" if f["control_bug"]
            else "⚠️ OFFLINE FLOOR DOWN (ollama)" if f["floor_down"]
            else "⚠️ a basket lock did NOT pay" if f["basket_locks_failed"]
            else "Quiet — steady-state null, nothing actionable.")
    lines = [
        f"1. Headline: {head}",
        f"2. Money track: basket {f['basket_open']} open / {f['basket_closed']} resolved "
        f"(realized ${f['basket_realized']}, {f['basket_locks_failed']} failed); dataarb "
        f"{f['dataarb_open']} open / {f['dataarb_resolved']} resolved; realization ratio "
        f"{ratio if ratio is not None else 'n/a'} (n={f['arb_n_real']}, want ≈1.0).",
        f"3. Gate watch: combined arb n={f['arb_n_real']}/30 → ~{to_gate} resolved exits to a gate; "
        f"analyst book {f['analyst_n']} pos ({f['analyst_gated']} gate-survivors, "
        f"{int(f['analyst_mideast_share']*100)}% mideast = ~1 effective bet).",
        f"4. Health: {'FLOOR DOWN; ' if f['floor_down'] else ''}"
        f"{'ALL-FALLBACK judgments; ' if f['all_fallback'] else ''}"
        f"{('stale: '+', '.join(f['stale_workers'])) if f['stale_workers'] else 'no stale workers'}; "
        f"controls {'POSITIVE (BUG)' if f['control_bug'] else 'not yet resolved / negative ✓'}.",
        "5. Caveat: deterministic fallback brief — NO LLM reachable, numbers are exact but unsynthesized; "
        "scalp-leg controls not re-read this run (see scorecard).",
    ]
    return "\n".join(lines)


def main():
    f = gather()
    txt, label = llm_brief(f)
    if txt is None:
        txt = fallback_brief(f)
        label = "fallback (no LLM reachable — deterministic)"

    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(NOW))
    body = (f"# Night brief\n\n_Updated {ts} · provenance: {label}_\n\n{txt}\n\n"
            f"---\n_Read-only synthesis. Paper only. Provenance: {label}. "
            f"A positive point estimate on small n is noise, not edge._\n")
    os.makedirs(VAULT, exist_ok=True)
    try:
        open(os.path.join(VAULT, "night_brief.md"), "w").write(body)
    except OSError:
        pass
    print(f"[pm_night_analyst] {ts}  provenance={label}")
    print(txt)

    # iMessage ONLY on a NEW material flag (deduped); quiet night = no ping
    flags = material_flags(f)
    if flags:
        try:
            st = json.load(open(STATE))
        except (OSError, ValueError):
            st = {}
        new = [fl for fl in flags if NOW - st.get("flag_" + fl, 0) > 21600]  # re-alert at most /6h
        if new:
            _imsg("🌙 night brief — " + ", ".join(new) + " | " + txt.split("\n")[0][:160])
            for fl in flags:
                st["flag_" + fl] = NOW
            try:
                json.dump(st, open(STATE, "w"), indent=2)
            except OSError:
                pass
        print(f"  material flags: {flags} (alerted: {new})")
    else:
        print("  no material flags — quiet night, no iMessage")


if __name__ == "__main__":
    main()
