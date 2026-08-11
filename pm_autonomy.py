#!/usr/bin/env python3
"""
pm_autonomy.py — the verify→solve→heal self-supervision loop as ONE Python runner
on the FREE LLM (zero Claude-Code-token cost), so the fleet self-recovers while
Aryan is offline. Ports the pm-autonomy-verifier / -solver / -healer subagents
(~/.claude/agents/pm-autonomy-*.md) into a scheduled script the watchdog can call —
a bash loop can't dispatch a Claude subagent, but it can run this.

Three tiers (run all in order with `all`, or one at a time):
  VERIFY  (deterministic, read-only) — did each autonomous worker actually DO its job?
          Checks REAL evidence: output FRESH within cadence, non-empty, provenance sane
          (not silently all-fallback = strong model down), + the Ollama keyless FLOOR.
          "Silence ≠ success" — a job can exit 0 while its output is stale/empty.
  SOLVE   (deterministic catalog + optional LLM narration) — match each non-PASS item to
          the project's known-failure catalog (TCC / exit-78 / FLOOR-DOWN / ALL-FALLBACK /
          stale-cache) → a MINIMAL fix classed SAFE-AUTO or NEEDS-HUMAN. The LLM only
          NARRATES; it can NEVER unlock a SAFE-AUTO beyond the hard allowlist (safety = the
          allowlist, not the model's judgment), so solve still works fully offline.
  HEAL    (the only state-changing tier) — auto-applies ONLY the hard allowlist:
            (1) Ollama floor down → restart the local keyless `ollama serve`
            (2) clear ONE solver-named stale cache file (move aside .bak, never delete)
          EVERYTHING else (watchdog relaunch, exit-78 plist repoint, keys, code, kills) is
          REFUSED and left for the human. heal-not-kill is absolute: NO pkill/killall, NO
          plist load, explicit actions only, Postgres-safe (waits out scalp_lab `once`).

  NOTE on watchdog-relaunch: when this runs FROM the watchdog the watchdog is by definition
  alive, so that allowlist action is moot here — copybot_watchdog.sh owns watchdog self-heal
  (Lesson 17: nohup-orphan, never the com.aryan.scalp-watchdog plist). We deliberately do NOT
  relaunch the watchdog from inside it.

Usage:
  python3 pm_autonomy.py verify       # ledger only (read-only)
  python3 pm_autonomy.py solve        # verify + prescribe (read-only)
  python3 pm_autonomy.py heal         # verify + solve + apply SAFE-AUTO allowlist
  python3 pm_autonomy.py all          # alias for heal (full loop)
Artifacts: pm_autonomy_ledger.json (verify), pm_autonomy_state.json (dedup of heal/alerts),
           Vault/Reports/autonomy.md (human view). iMessage ONLY on a NEW real problem.
"""
import os, sys, json, time, subprocess, urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
NOW = int(time.time())
VAULT = os.path.expanduser("~/Documents/PolymarketVault/Reports")
LEDGER = os.path.join(BASE, "pm_autonomy_ledger.json")
STATE = os.path.join(BASE, "pm_autonomy_state.json")
OLLAMA = "http://localhost:11434"
HEAL_THROTTLE = 600          # min seconds between the SAME heal action (no retry-storm)
ALERT_RECIPIENTS = ["krisharyan@icloud.com", "+918449447444"]

# ── Autonomous-worker registry: (name, freshness_file, cadence_seconds, kind) ────────────
# freshness_file is a *.log the worker appends EVERY run (even a no-op), so its mtime is an
# honest "did it fire" proxy — unlike a signal-only JSONL that only grows on a hit. cadence
# is the worker's real schedule from watchdog_loop.sh; we cry STALE only past TOL× cadence
# (cadence-aware discipline — a 6h job at 3h is NOT stale). Missing file ⇒ SKIP, never FAIL
# (don't fail a worker for verifier rot / a not-yet-restarted watchdog).
TOL = 2.5
WORKERS = [
    ("analyst_pipeline",  "pm_pipeline.log",          21600),
    ("analyst_data_gate", "analyst_data_gate.log",     1800),
    ("basket_paper",      "basket_paper.log",          1800),
    ("dataarb",           "dataarb.log",               1800),
    ("multiarb",          "multiarb.log",              1800),
    ("arb_track",         "arb_track.log",             1800),
    ("monoarb",           "monoarb.log",               1800),
    ("funding_basis",     "funding_basis.log",         1800),
    ("sentinel_sweep",    "sentinel_sweep.log",        1800),
    ("health_sentinel",   "health_sentinel.log",       1800),
    ("news_arb",          "news_arb.log",              7200),
    ("pm_agents",         "pm_agents.log",             3600),
]
# JSONL judgment logs carrying provider provenance — for ALL-FALLBACK (strong model down)
PROVENANCE_LOGS = ["news_arb_log.jsonl", "analyst_agent_log.jsonl"]


# ── helpers ──────────────────────────────────────────────────────────────────────────────
def _age(path):
    try:
        return NOW - int(os.path.getmtime(path))
    except OSError:
        return None


def _tail_json(path, n=8):
    """Last n parseable JSON objects from a .jsonl (best-effort)."""
    try:
        with open(path, "rb") as f:
            lines = f.read().splitlines()[-n:]
    except OSError:
        return []
    out = []
    for ln in lines:
        try:
            out.append(json.loads(ln))
        except (ValueError, UnicodeDecodeError):
            pass
    return out


def _ollama_up():
    try:
        urllib.request.urlopen(OLLAMA + "/api/tags", timeout=3).read()
        return True
    except Exception:
        return False


def _scalp_running():
    """True if a scalp_lab.py once cycle is in flight (Postgres-write window)."""
    try:
        r = subprocess.run(["pgrep", "-f", "scalp_lab.py once"],
                           capture_output=True, text=True, timeout=5)
        return bool(r.stdout.strip())
    except Exception:
        return False


def _wait_postgres_window(max_wait=24):
    """Wait out an in-flight scalp_lab once-cycle before any state change (Postgres safety)."""
    waited = 0
    while _scalp_running() and waited < max_wait:
        time.sleep(2)
        waited += 2
    return waited


def _load_state():
    try:
        return json.load(open(STATE))
    except (OSError, ValueError):
        return {}


def _save_state(s):
    try:
        json.dump(s, open(STATE, "w"), indent=2)
    except OSError:
        pass


def _imsg(msg):
    """Fail-soft iMessage to both endpoints (same path as watchdog_loop.sh)."""
    safe = msg.replace('"', "'")[:480]
    for t in ALERT_RECIPIENTS:
        os.system('osascript -e \'tell application "Messages" to send "%s" to buddy "%s" '
                  'of (service 1 whose service type is iMessage)\' >/dev/null 2>&1' % (safe, t))


# ── VERIFY ─────────────────────────────────────────────────────────────────────────────
def verify():
    """Read-only evidence ledger over the autonomous fleet. Returns list of item dicts."""
    items = []

    # 1) the keyless OFFLINE FLOOR — Ollama. Highest-priority infra flag if down.
    floor = _ollama_up()
    items.append({"worker": "ollama (floor)", "state": "PASS" if floor else "FLOOR-DOWN",
                  "age": None, "cadence": None,
                  "note": "keyless net up" if floor else "keyless offline net GONE — next cloud outage = judgment blackout"})

    # 2) per-worker freshness
    for name, fname, cad in WORKERS:
        path = os.path.join(BASE, fname)
        age = _age(path)
        if age is None:
            items.append({"worker": name, "state": "SKIP", "age": None, "cadence": cad,
                          "note": f"{fname} absent — not verified (watchdog may not have restarted to add it)"})
        elif age > cad * TOL:
            items.append({"worker": name, "state": "STALE", "age": age, "cadence": cad,
                          "note": f"last write {age//60}m ago > {int(cad*TOL)//60}m budget — worker not firing or erroring"})
        else:
            items.append({"worker": name, "state": "PASS", "age": age, "cadence": cad,
                          "note": f"{age//60}m / {cad//60}m"})

    # 3) provenance — are recent judgments ALL served by the weak local fallback?
    for plog in PROVENANCE_LOGS:
        recs = _tail_json(os.path.join(BASE, plog), n=6)
        served = [r for r in recs if "served_fallback" in r]
        if served and all(r.get("served_fallback") for r in served):
            items.append({"worker": plog, "state": "ALL-FALLBACK", "age": None, "cadence": None,
                          "note": f"last {len(served)} judgments all fallback-served → strong cloud model down"})
    return items


# ── SOLVE ──────────────────────────────────────────────────────────────────────────────
# Deterministic known-failure catalog. ONLY these can produce class=SAFE-AUTO; the LLM (if
# reachable) adds a narrative but can never escalate an item to SAFE-AUTO that isn't here.
def solve(items):
    rx = []
    for it in items:
        st = it["state"]
        if st in ("PASS", "SKIP"):
            continue
        if st == "FLOOR-DOWN":
            rx.append({**it, "root_cause": "local keyless ollama daemon not serving on :11434",
                       "fix": "restart `ollama serve` (local, keyless)", "action": "restart_ollama",
                       "klass": "SAFE-AUTO"})
        elif st == "ALL-FALLBACK":
            rx.append({**it, "root_cause": "strong cloud model down (expired/rate-limited key or no network)",
                       "fix": "verify ollama floor intact + log; rotating a KEY is human-only",
                       "action": "note_only", "klass": "NEEDS-HUMAN"})
        elif st == "STALE":
            # From inside a live watchdog, a stale worker = that script erroring or its endpoint
            # down, NOT a dead watchdog. Read its log to localize → human (code/endpoint).
            rx.append({**it, "root_cause": "worker not producing output though the watchdog is alive "
                       "(script error / dependency endpoint down / stale shared cache)",
                       "fix": f"tail {it['worker']}'s .log to localize; clear a named stale cache only if that's the cause",
                       "action": "note_only", "klass": "NEEDS-HUMAN"})
        else:  # EMPTY/INVALID/novel
            rx.append({**it, "root_cause": "worker runs but errors (schema drift / stale cache / endpoint)",
                       "fix": "read the worker's stderr/log", "action": "note_only", "klass": "NEEDS-HUMAN"})

    # Optional LLM narration over the evidence (enrichment only; wrapped so offline is fine).
    narrative = ""
    if rx:
        try:
            import llm_client
            if llm_client.configured():
                ev = "\n".join(f"- {r['worker']}: {r['state']} — {r['note']}" for r in rx)
                narrative = llm_client.chat([
                    {"role": "system", "content":
                     "You are the diagnostician for a paper-only Polymarket bot's autonomous fleet. "
                     "Given the verifier's flagged items, give ONE terse paragraph (<=4 sentences) "
                     "on the most likely root cause and the single highest-priority fix. Do NOT "
                     "invent fixes beyond restarting the local ollama daemon or reading a log."},
                    {"role": "user", "content": ev}], max_tokens=220, timeout=40).strip()
        except Exception as e:
            narrative = f"(LLM narration unavailable: {type(e).__name__})"
    return rx, narrative


# ── HEAL ───────────────────────────────────────────────────────────────────────────────
# Hard allowlist of auto-actions. Anything not here is refused and handed back to the human.
def heal(rx):
    state = _load_state()
    healed, refused = [], []
    for r in rx:
        action, klass = r.get("action"), r.get("klass")
        if klass != "SAFE-AUTO":
            refused.append(r)
            continue
        key = f"heal_{action}"
        last = state.get(key, 0)
        if NOW - last < HEAL_THROTTLE:
            r["result"] = f"throttled (last {NOW-last}s ago, min {HEAL_THROTTLE}s)"
            healed.append(r)
            continue

        if action == "restart_ollama":
            # re-verify the symptom ourselves (the ledger may be stale)
            if _ollama_up():
                r["result"] = "no-op: ollama already up on re-check"
                healed.append(r); continue
            _wait_postgres_window()  # belt-and-suspenders (ollama doesn't touch PG)
            subprocess.Popen("pgrep -x ollama >/dev/null || (nohup ollama serve "
                             ">/tmp/ollama.out 2>&1 & disown)", shell=True)
            time.sleep(4)
            ok = _ollama_up()
            r["result"] = "RESTARTED ollama → :11434 UP ✅" if ok else "restart attempted, :11434 still DOWN ❌"
            state[key] = NOW
            healed.append(r)
            _imsg(f"🔧 autonomy heal: ollama floor was DOWN → {'restored ✅' if ok else 'restart FAILED ❌ (needs you)'}")

        elif action == "clear_cache" and r.get("cache_file"):
            # only a solver-named exact file UNDER the bot dir, moved aside (never deleted)
            cf = os.path.join(BASE, os.path.basename(r["cache_file"]))
            if os.path.exists(cf):
                _wait_postgres_window()
                bak = cf + f".bak.{NOW}"
                try:
                    os.rename(cf, bak)
                    r["result"] = f"moved {os.path.basename(cf)} → {os.path.basename(bak)}"
                    state[key] = NOW
                except OSError as e:
                    r["result"] = f"could not move cache: {e}"
            else:
                r["result"] = "cache file absent — no-op"
            healed.append(r)
        else:
            refused.append(r)
    _save_state(state)
    return healed, refused


# ── report ───────────────────────────────────────────────────────────────────────────────
def _write_report(items, rx, narrative, healed, refused, mode):
    os.makedirs(VAULT, exist_ok=True)
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(NOW))
    L = [f"# Autonomy self-supervision (verify→solve→heal)", "",
         f"_Updated {ts} · mode `{mode}` · free-LLM runner (zero Claude cost)_", "",
         "## Verify ledger", "", "| worker | state | age/cadence | note |", "|---|---|---|---|"]
    for it in items:
        ac = (f"{it['age']//60}m / {it['cadence']//60}m"
              if it["age"] is not None and it["cadence"] else "—")
        L.append(f"| {it['worker']} | {it['state']} | {ac} | {it['note']} |")
    bad = [i for i in items if i["state"] not in ("PASS", "SKIP")]
    L += ["", f"**Verdict:** {'fleet healthy, nothing to heal ✅' if not bad else str(len(bad))+' item(s) need attention'}"]
    if rx:
        L += ["", "## Solve (prescriptions)", ""]
        for r in rx:
            L.append(f"- **{r['worker']}** ({r['state']}) → {r['fix']} _[{r['klass']}]_")
        if narrative:
            L += ["", "> " + narrative.replace("\n", " ")]
    if healed:
        L += ["", "## Heal (applied)", ""] + [f"- {h['worker']}: {h.get('result','')}" for h in healed]
    if refused:
        L += ["", "## Left for human (NEEDS-HUMAN / not on allowlist)", ""] + \
             [f"- {r['worker']}: {r['fix']}" for r in refused]
    try:
        open(os.path.join(VAULT, "autonomy.md"), "w").write("\n".join(L) + "\n")
    except OSError:
        pass


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    items = verify()
    json.dump({"ts": NOW, "items": items}, open(LEDGER, "w"), indent=2)

    # print the ledger (human-readable for manual runs / log)
    print(f"[pm_autonomy] {mode} @ {time.strftime('%F %T', time.localtime(NOW))}")
    for it in items:
        ac = (f"{it['age']//60}m/{it['cadence']//60}m"
              if it["age"] is not None and it["cadence"] else "—")
        print(f"  {it['worker']:<22} {it['state']:<12} {ac:<12} {it['note']}")
    bad = [i for i in items if i["state"] not in ("PASS", "SKIP")]

    rx, narrative, healed, refused = [], "", [], []
    if mode in ("solve", "heal", "all"):
        rx, narrative = solve(items)
        if narrative:
            print("  diagnosis:", narrative.replace("\n", " ")[:300])
        for r in rx:
            print(f"  FIX {r['worker']:<22} [{r['klass']}] {r['fix']}")
    if mode in ("heal", "all"):
        healed, refused = heal(rx)
        for h in healed:
            print(f"  HEAL {h['worker']:<22} {h.get('result','')}")
        for r in refused:
            print(f"  HUMAN {r['worker']:<22} {r['fix']}")

    _write_report(items, rx, narrative, healed, refused, mode)
    print(f"  verdict: {'healthy ✅' if not bad else str(len(bad))+' flagged'}")


if __name__ == "__main__":
    main()
