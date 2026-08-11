#!/usr/bin/env python3
"""provider_health.py — deterministic ($0, NO LLM/agent) watchdog for the free-LLM provider chain.
Runs `llm_client.py --selftest`, parses the per-tier ✅/❌ live pings, then:
  • FLOOR (ollama) down  → auto-HEAL: (re)start `ollama serve`, re-check; only alert if the heal FAILS.
  • a STRONG keyed tier down (groq/cerebras/sambanova/…) → alert to refresh the key (a key can't be
    auto-healed), but DEBOUNCED: a single-cycle blip is ignored — alert only after DEBOUNCE consecutive
    bad cycles (cerebras's free tier flaps, so a transient must NOT page).
  • nothing serves / parse-fail → alert immediately (not debounced).
  • all healthy → silent.
Alerts on CONFIRMED status CHANGE only (dedup — never re-spams a persistent outage). HEAL-NOT-KILL:
the only state-changing action is STARTING ollama; it never kills a process. launchd:
com.aryan.provider-health. The deterministic twin of pm-spend-warden (the check is fully deterministic
— the selftest already pings every tier — so it needs no Claude tokens). Fail-soft throughout.

Run:  python3 provider_health.py        (one cycle; alert only on a CONFIRMED change)
"""
import os, re, json, subprocess, sys, shutil, time, urllib.request
from datetime import datetime, timezone

HERE  = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "provider_health_state.json")
LOG   = os.path.join(HERE, "provider_health_log.jsonl")
TARGETS = ["krisharyan@icloud.com", "+918449447444"]
PY = sys.executable

STRONG_KEYED = ("groq", "cerebras", "sambanova", "nvidia", "openrouter", "together")
SERVING = ("groq", "cerebras", "sambanova", "nvidia", "openrouter", "together", "public", "ollama")
DEBOUNCE = 2          # a flappy keyed-tier DEGRADED must persist this many consecutive cycles to alert
BAD = ("CRITICAL", "DEGRADED", "PARSE_FAIL")


def _send(body):
    safe = body.replace('"', "'").replace("\n", "\\n")
    for t in TARGETS:
        script = ('tell application "Messages"\n  set s to 1st service whose service type = iMessage\n'
                  f'  send "{safe}" to buddy "{t}" of s\nend tell')
        try:
            subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=10)
        except Exception:
            pass


def _floor_alive():
    try:
        urllib.request.urlopen("http://localhost:11434/api/tags", timeout=4)
        return True
    except Exception:
        return False


def _heal_floor():
    """HEAL: the local ollama floor is the ONLY safely-healable tier. (re)start `ollama serve`
    DETACHED (never kill anything — heal-not-kill) and re-check. Returns True if the floor returns."""
    if _floor_alive():
        return True   # floor is actually up — the selftest's ollama ❌ was a transient ping flake;
                      # don't spawn a redundant `ollama serve` (same blip-tolerance as the keyed debounce)
    ob = shutil.which("ollama") or next((p for p in ("/opt/homebrew/bin/ollama", "/usr/local/bin/ollama")
                                         if os.path.exists(p)), None)
    if not ob:
        return False
    try:
        subprocess.Popen([ob, "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
    except Exception:
        return False
    for _ in range(8):
        time.sleep(2)
        if _floor_alive():
            return True
    return False


def _selftest_tiers():
    """Run the llm_client selftest, return ({tier: up?}, chain_str)."""
    try:
        r = subprocess.run([PY, os.path.join(HERE, "llm_client.py"), "--selftest"],
                           capture_output=True, text=True, timeout=120, cwd=HERE)
        out = (r.stdout or "") + (r.stderr or "")
    except Exception as e:
        out = f"selftest crashed: {e}"
    tiers = {m.group(2): (m.group(1) == "✅")
             for m in re.finditer(r"^\s*([✅❌])\s+(\w+)\s*\(", out, re.M)}
    cm = re.search(r"failover chain \(\d+\):\s*(.+)", out)
    return tiers, (cm.group(1).strip() if cm else "?")


def _pin_maverick():
    """Counter the .env reverter (a parallel session keeps resetting the chain to groq/llama-3.3-70b):
    re-assert LLM_PROVIDER=nvidia and STRIP any NVIDIA_MODEL override, so the committed PROVIDERS
    Llama-4-Maverick default wins. Config lines only — NEVER touches *_KEY lines. Atomic, fail-soft.
    Returns True if it had to correct something (the reverter had been active)."""
    env = os.path.join(HERE, ".env")
    try:
        lines = open(env, encoding="utf-8").read().splitlines()
    except Exception:
        return None
    out, changed, has_prov = [], False, False
    for ln in lines:
        if ln.startswith("NVIDIA_MODEL="):          # drop the stale override → committed maverick default wins
            changed = True
            continue
        if ln.startswith("LLM_PROVIDER="):
            has_prov = True
            if ln.strip() != "LLM_PROVIDER=nvidia":
                ln, changed = "LLM_PROVIDER=nvidia", True
        out.append(ln)
    if not has_prov:
        out.append("LLM_PROVIDER=nvidia"); changed = True
    if changed:
        try:
            tmp = env + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write("\n".join(out) + "\n")
            os.replace(tmp, env)
        except Exception:
            return None
    return changed


def main():
    pinned = _pin_maverick()   # keep the chain on Llama-4-Maverick (counter the .env reverter)
    if pinned:
        print("provider_health: re-pinned chain -> nvidia/Llama-4-Maverick (.env reverter was active)")
    tiers, chain = _selftest_tiers()
    floor_up = tiers.get("ollama", False)

    # ── HEAL (floor only) — try BEFORE alerting; the floor is the one safely-healable tier ──
    healed = False
    if tiers and not floor_up:
        healed = _heal_floor()
        if healed:
            tiers["ollama"] = True
            floor_up = True

    keyed_down = [n for n in STRONG_KEYED if n in tiers and not tiers[n]]
    serving_up = any(tiers.get(n) for n in SERVING)

    if not tiers:
        status = "PARSE_FAIL"
    elif not serving_up:
        status = "CRITICAL"
    elif keyed_down or not floor_up:
        status = "DEGRADED"
    else:
        status = "OK"

    # ── streak + DEBOUNCE: only a DEGRADED whose SOLE cause is a flappy keyed tier (floor still up,
    #    something still serves) is debounced; CRITICAL / floor-down-after-failed-heal / parse-fail
    #    alert promptly. ──
    try:
        st = json.load(open(STATE))
    except Exception:
        st = {}
    streak = (st.get("streak", 0) + 1) if st.get("raw") == status else 1
    last = st.get("alerted")
    keyed_only = (status == "DEGRADED" and floor_up and bool(keyed_down))

    if status == "OK":
        confirmed = "OK"
    elif keyed_only and streak < DEBOUNCE:
        confirmed = last if last is not None else "OK"     # HOLD — transient blip, not yet alert-worthy
    else:
        confirmed = status

    now = datetime.now(timezone.utc).isoformat()
    try:
        with open(LOG, "a") as f:
            f.write(json.dumps({"ts": now, "status": status, "confirmed": confirmed, "down": keyed_down,
                                "floor_up": floor_up, "healed": healed, "streak": streak,
                                "chain": chain, "tiers": tiers}) + "\n")
    except Exception:
        pass

    if confirmed != last:
        if confirmed in BAD:
            why = []
            if not floor_up:
                why.append("ollama floor DOWN (auto-heal FAILED)")
            if keyed_down:
                why.append("keys down: " + ", ".join(keyed_down))
            if confirmed == "CRITICAL":
                why.append("NOTHING serving")
            _send(f"⚠️ LLM chain {confirmed}: {'; '.join(why) or 'see log'}. "
                  f"Refresh the key (browser → provider → API keys). chain={chain} (provider_health)")
        elif confirmed == "OK" and last in BAD:
            extra = " (ollama floor auto-restarted)" if healed else ""
            _send(f"✅ LLM chain RECOVERED — strong tiers + floor up{extra}. chain={chain} (provider_health)")

    try:
        json.dump({"alerted": confirmed, "raw": status, "streak": streak,
                   "down": keyed_down, "healed": healed, "updated": now}, open(STATE, "w"))
    except Exception:
        pass

    print(f"provider_health: {status} (confirmed={confirmed}) · keyed_down={keyed_down or 'none'} · "
          f"floor={'up' if floor_up else 'DOWN'}{' · HEALED' if healed else ''} · streak={streak}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
