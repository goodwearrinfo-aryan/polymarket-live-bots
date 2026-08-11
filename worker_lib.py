"""
worker_lib.py — shared helpers for the 24/7 background analyst workers
(night_brief.py, catalyst_watch.py, survivor_watch.py).

Design rules (match the project):
- FREE inference only: judgment runs on llm_client (nvidia/minimax-m3 -> ollama floor).
  Deterministic gathering uses NO LLM. Zero Claude tokens.
- FAIL-SOFT: a 24/7 worker must never crash-loop. Every external call is guarded.
- HONEST: always stamp provenance (which model served). Silence != success.
- NO SPAM: iMessage is gated + deduped by the caller; vault+log always written.
- TCC-clean: logs go to /tmp; vault notes go to ~/Documents/PolymarketVault/Reports.
"""
import os, sys, json, time, hashlib, subprocess
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import llm_client
except Exception:
    llm_client = None

IMESSAGE_TARGETS = ["krisharyan@icloud.com", "+918449447444"]
VAULT_DIR = os.path.expanduser("~/Documents/PolymarketVault/Reports")


def ts():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(stem, text):
    """Append a heartbeat line to /tmp/<stem>.log and echo to stdout (launchd captures)."""
    line = f"[{ts()}] {text}"
    print(line, flush=True)
    try:
        with open(f"/tmp/{stem}.log", "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def vault_write(stem, body):
    """Overwrite ~/Documents/PolymarketVault/Reports/<stem>.md with Bases-friendly frontmatter.
    Atomic (temp + os.replace) with a short retry so a transient lock from Obsidian/cloud-sync
    or a concurrent worker (EDEADLK) doesn't silently drop the write."""
    path = os.path.join(VAULT_DIR, f"{stem}.md")
    fm = (f"---\ntitle: {stem}\ntype: report\nstatus: info\n"
          f"updated: {ts()}\ntags: [polymarket, autonomous]\n---\n\n")
    content = fm + body + "\n"
    for attempt in range(3):
        tmp = None
        try:
            os.makedirs(VAULT_DIR, exist_ok=True)
            tmp = f"{path}.tmp.{os.getpid()}"
            with open(tmp, "w") as f:
                f.write(content)
            os.replace(tmp, path)  # atomic rename — sidesteps open(w) lock contention
            return True
        except Exception as e:
            if tmp:
                try:
                    os.remove(tmp)
                except Exception:
                    pass
            if attempt == 2:
                log(stem, f"[vault FAIL] {e}")
                return False
            time.sleep(0.25 * (attempt + 1))


def llm_ready():
    return bool(llm_client and llm_client.configured())


def prov():
    """'served_by/served_model (fallback=…)' for the last free-LLM call, or 'n/a'."""
    try:
        p = llm_client.provenance() or {}
        return f"{p.get('served_by','?')}/{p.get('served_model','?')} (fallback={p.get('served_fallback')})"
    except Exception:
        return "n/a"


def free_chat(prompt, system=None, max_tokens=700, temperature=0.2):
    """Free-model prose. Returns text or None (fail-soft). Uses minimax-m3 -> ollama floor."""
    if not llm_ready():
        return None
    msgs = []
    if system:
        msgs.append({"role": "system", "content": system})
    msgs.append({"role": "user", "content": prompt})
    try:
        return llm_client.chat(msgs, temperature=temperature, max_tokens=max_tokens)
    except Exception:
        return None


def free_judge(system, user, schema_hint=None):
    """Free-model structured judgment. Returns dict/list or None (fail-soft)."""
    if not llm_ready():
        return None
    try:
        return llm_client.judge(system=system, user=user, schema_hint=schema_hint)
    except Exception:
        return None


def _in_quiet_hours(h=None):
    """True if the current local hour (machine tz = IST here) is inside the no-alert
    window. Default 23:00–07:00; override via WORKER_QUIET_START / WORKER_QUIET_END.
    Set them equal to disable quiet hours entirely."""
    try:
        if h is None:
            h = datetime.now().hour                 # naive = machine local time (IST)
        start = int(os.environ.get("WORKER_QUIET_START", "23"))
        end = int(os.environ.get("WORKER_QUIET_END", "7"))
        if start == end:
            return False
        return (start <= h < end) if start < end else (h >= start or h < end)
    except Exception:
        return False


def send_imessage(text):
    """Dual iMessage send (fail-soft). Callers gate + dedup before calling this.
    Suppressed (but logged) during quiet hours; WORKER_NO_IMESSAGE=1 also suppresses
    (testing). Both still return True so callers' dedup state advances normally."""
    if os.environ.get("WORKER_NO_IMESSAGE"):
        return True
    if _in_quiet_hours():
        try:
            with open("/tmp/worker_quiet_suppressed.log", "a") as f:
                f.write(f"[{ts()}] (quiet hours) {text[:240]}\n")
        except Exception:
            pass
        return True
    safe = text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    ok = False
    for tgt in IMESSAGE_TARGETS:
        script = ('tell application "Messages"\n'
                  '  set s to id of first service whose service type = iMessage\n'
                  f'  set b to participant "{tgt}" of service id s\n'
                  f'  send "{safe}" to b\n'
                  'end tell')
        try:
            r = subprocess.run(["osascript", "-e", script],
                               capture_output=True, text=True, timeout=20)
            ok = ok or r.returncode == 0
        except Exception:
            pass
    return ok


def is_new_alert(stem, key, cap=400):
    """Dedup: True the FIRST time a key is seen, then False. Persists in /tmp/<stem>.seen."""
    p = f"/tmp/{stem}.seen"
    h = hashlib.sha1(key.encode("utf-8", "ignore")).hexdigest()[:16]
    seen = []
    try:
        seen = open(p).read().split()
    except Exception:
        pass
    if h in seen:
        return False
    seen.append(h)
    try:
        open(p, "w").write("\n".join(seen[-cap:]))
    except Exception:
        pass
    return True


def run_capture(args, timeout=150):
    """Run a subprocess and return stdout text (fail-soft, '' on error/timeout)."""
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                           cwd=os.path.dirname(os.path.abspath(__file__)))
        return r.stdout or ""
    except Exception as e:
        return f"[run_capture FAIL {args}: {e}]"
