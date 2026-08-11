#!/usr/bin/env python3
"""logger_digest.py — one-shot 7-day verdict on the 5 candidate-leg research probes.

Reads each read-only logger's accumulated state + leg_health, and emits a
GRADUATE / KILL / KEEP-ACCUMULATING call per probe, then iMessages the summary.
These 5 probes came out of the 2026-06-14 "10 novel legs" adversarial teardown —
only the survivors were worth instrumenting; this digest decides which (if any)
earned a real gated leg. PAPER research, read-only.

Heuristics are deliberately conservative — the digest surfaces the numbers and a
recommendation; Aryan makes the final call. Run by launchd on 2026-06-22; the
plist self-removes after firing (true one-shot).
"""
import os, re, subprocess, time

BASE = os.path.expanduser("~/Documents/polymarket")
TARGETS = ["krisharyan@icloud.com", "+918449447444"]

# probe -> (logfile, what it tests, the make-or-break metric)
PROBES = {
    "sharpdrift": ("sharpdrift.log", "whalecons: >=2 sharp wallets agree on a slow market",
                   "consensus events finalized"),
    "latticesnap": ("latticesnap.log", "ladder-break re-cohere", "breaks judged"),
    "tapeshock":  ("tapeshock.log", "liquidity-shock reversion (overreact)", "shocks judged"),
    "deadline":   ("deadline.log", "near-deadline impossible-YES theta", "NO-side exits priced"),
    "redeemdisc": ("redeemdisc.log", "post-resolution redemption discount", "buyable discounts found"),
    "whalexit":   ("whalexit.log", "do sharp EXITS predict decline", "exits matured"),
    "favwatch":   ("favwatch.log", "favorite drift + resolution gap", "favorites resolved"),
    "overround":  ("overround.log", "multi-outcome basket / Dutch-book watch", "dutch-books found"),
}


def tail_text(fn, n=400):
    p = os.path.join(BASE, fn)
    if not os.path.exists(p):
        return ""
    with open(p, errors="ignore") as f:
        return "".join(f.readlines()[-n:])


def nums(text, key):
    """last value of `key=NN` in the text, else None."""
    m = re.findall(rf"{key}=(\d+)", text)
    return int(m[-1]) if m else None


def verdict_for(name, text):
    """Conservative GRADUATE / KILL / ACCUMULATE call + the evidence line."""
    if not text.strip():
        return "⏳ NO DATA", "logger never ran — check watchdog cadence"
    if name == "sharpdrift":
        fin = nums(text, "finalized"); cons = nums(text, "new_consensus")
        pend = nums(text, "pending")
        # frequency is the killer: if essentially no consensus ever fired, it's dead
        total_seen = (fin or 0) + (pend or 0)
        if (fin or 0) >= 15:
            return "✅ GRADUATE-CANDIDATE", f"{fin} consensus events finalized — enough to gate; check WR vs entry-mid"
        if total_seen <= 1:
            return "❌ KILL (frequency-dead)", f"~0 consensus events in 7d (finalized={fin}, pending={pend}) — can't reach n=30; the kill-panel frequency flaw held"
        return "⏳ ACCUMULATE", f"finalized={fin} pending={pend} — real but slow; keep logging"
    if name == "latticesnap":
        jud = nums(text, "judged"); brk = len(re.findall(r"\bBREAK\b", text))
        if (jud or 0) >= 15:
            return "✅ GRADUATE-CANDIDATE", f"{jud} breaks judged — gateable; check re-cohere edge"
        if brk == 0:
            return "❌ KILL (no events)", "0 ladder-breaks observed in 7d"
        return "⏳ ACCUMULATE", f"{brk} breaks seen, judged={jud} — waiting on resolutions"
    if name == "deadline":
        res = nums(text, "resolved"); pend = nums(text, "pending")
        if (res or 0) >= 15: return "✅ GRADUATE-CANDIDATE", f"{res} NO-side exits priced — measure theta capture"
        if (res or 0) == 0 and (pend or 0) == 0: return "❌ KILL (empty habitat)", "0 near-deadline longshots tracked in 7d"
        return "⏳ ACCUMULATE", f"resolved={res} pending={pend} — waiting on deadline passes"
    if name == "whalexit":
        mat = nums(text, "matured")
        if (mat or 0) >= 15: return "✅ GRADUATE-CANDIDATE", f"{mat} sharp-exits matured — gateable; check post-exit decline"
        if (mat or 0) == 0 and (nums(text, "pending") or 0) == 0: return "❌ KILL (no events)", "0 sharp exits tracked in 7d"
        return "⏳ ACCUMULATE", f"matured={mat} pending={nums(text,'pending')}"
    if name == "favwatch":
        res = nums(text, "resolved")
        if (res or 0) >= 20: return "✅ GRADUATE-CANDIDATE", f"{res} favorites resolved — measure the drift/gap edge"
        return "⏳ ACCUMULATE", f"resolved={res} pending={nums(text,'pending')}"
    if name == "overround":
        db = nums(text, "dutch_books"); tot = nums(text, "total")
        if (db or 0) >= 5: return "✅ GRADUATE-CANDIDATE", f"{db} Dutch-books in {tot} samples — rare arb may exist"
        if (tot or 0) >= 200 and (db or 0) == 0: return "❌ KILL (empty habitat)", f"0 Dutch-books in {tot} samples — teardown's negRisk-converter kill confirmed"
        return "⏳ ACCUMULATE", f"dutch_books={db} of total={tot} samples"
    # generic event-counting probes
    events = len(re.findall(r"(BREAK|SHOCK|ENTER|EXIT|DISCOUNT|NO @|priced)", text, re.I))
    if events >= 15:
        return "✅ GRADUATE-CANDIDATE", f"~{events} qualifying events logged — enough to evaluate"
    if events == 0:
        return "❌ KILL (empty habitat)", "0 qualifying events in 7d — matches the teardown's empty-habitat kill"
    return "⏳ ACCUMULATE", f"~{events} events so far — too few to judge"


def leg_health_tail():
    try:
        out = subprocess.run(["python3", os.path.join(BASE, "leg_health.py")],
                             capture_output=True, text=True, timeout=120).stdout
        # surface any leg that is REAL winning (CI excludes 0, positive)
        wins = [l for l in out.splitlines() if "REAL" in l and "losing" not in l and "control" not in l]
        return wins or ["(no leg shows REAL winning yet)"]
    except Exception as e:
        return [f"(leg_health failed: {e})"]


def send_imessage(body):
    osa = ('on run argv\ntell application "Messages"\nset s to 1st service whose service type = iMessage\n'
           'send (item 2 of argv) to buddy (item 1 of argv) of s\nend tell\nend run')
    ok = True
    for t in TARGETS:
        r = subprocess.run(["osascript", "-e", osa, t, body], capture_output=True, text=True, timeout=30)
        ok = ok and r.returncode == 0
    return ok


def main():
    lines = ["📊 LEG-PROBE 7-DAY DIGEST (graduate/kill calls)", ""]
    grads = []
    for name, (fn, what, metric) in PROBES.items():
        text = tail_text(fn)
        v, why = verdict_for(name, text)
        if v.startswith("✅"):
            grads.append(name)
        lines.append(f"{v}  {name} — {what}")
        lines.append(f"     {why}")
    lines += ["", "Leg health (any REAL winner?):"]
    lines += ["  " + l.strip() for l in leg_health_tail()[:6]]
    lines += ["", ("➡️ ACTION: graduate " + ", ".join(grads) + " to a gated leg.") if grads
              else "➡️ ACTION: none graduate yet — keep accumulating or kill the dead ones."]
    body = "\n".join(lines)
    print(body)
    sent = send_imessage(body)
    print(f"\n[iMessage] {'sent' if sent else 'FAILED'}")

    # self-remove the launchd plist so this fires exactly once — but ONLY on/after the
    # scheduled fire date, so a test/dry run before then can't nuke the schedule.
    if time.strftime("%Y-%m-%d") >= "2026-06-22":
        plist = os.path.expanduser("~/Library/LaunchAgents/com.aryan.logger-digest.plist")
        if os.path.exists(plist):
            subprocess.run(["launchctl", "unload", plist], capture_output=True)
            try: os.remove(plist)
            except Exception: pass


if __name__ == "__main__":
    main()
