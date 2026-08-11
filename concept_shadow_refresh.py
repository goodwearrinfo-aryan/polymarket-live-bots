#!/usr/bin/env python3
"""Weekly SHADOW refresh of the vault concept graph + staleness signal.

NON-DESTRUCTIVE: writes ConceptGraph/graphify-out/graph.shadow.json — NEVER
overwrites the high-fidelity opus graph.json. Two jobs:

  1. Staleness signal (free, always): how many concept notes changed since the
     last real (opus) build → tells you WHEN a high-fidelity rebuild is worth it.
  2. ollama shadow graph (free, if ollama up): a low-fidelity re-extraction you
     can diff against the opus graph. Fail-soft: if ollama is down, log + skip,
     still emit the staleness signal, exit 0.

stdlib-only + framework python = TCC-clean under launchd. The ollama extraction
is run by the graphify CLI on a /tmp staging copy (no ~/Documents access from
the uv-python CLI), then results are read back by this framework-python process.

Run with --quick to skip the ollama extraction (plumbing + staleness only).
"""
import json, os, re, shutil, subprocess, sys, time, urllib.request
from pathlib import Path

HOME = Path.home()
VAULT = HOME / "Documents/PolymarketVault"
CONCEPT_OUT = VAULT / "ConceptGraph/graphify-out"
OPUS = CONCEPT_OUT / "graph.json"            # high-fidelity, never touched
SHADOW = CONCEPT_OUT / "graph.shadow.json"   # this job's output
STAGE = Path("/tmp/concept_shadow")
LOG = Path("/tmp/concept-shadow-refresh.log")
NOTE = VAULT / "Concept Brain Freshness.md"
GRAPHIFY = "/Users/aryanagarwal/.local/share/uv/tools/graphifyy/bin/graphify"
OLLAMA_MODEL = "llama3.1:8b"   # graphify's default qwen2.5-coder:7b is NOT installed
OLLAMA = "http://localhost:11434/api/tags"
FOLDERS = ["Lessons", "Edges", "Bot", "Legs Archive", "Journal"]   # + root .md
CHANGED_ALERT = 5   # >= this many changed notes => "rebuild worth it"


def log(msg):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line)
    try:
        LOG.open("a").write(line + "\n")
    except Exception:
        pass


def concept_notes():
    """Full corpus that gets extracted (root hubs + concept folders)."""
    files = list(VAULT.glob("*.md"))
    for d in FOLDERS:
        p = VAULT / d
        if p.is_dir():
            files += list(p.rglob("*.md"))
    return [f for f in files if "graphify-out" not in str(f) and "/Graph/" not in str(f)]


# Hand-authored knowledge — an mtime change here is a REAL concept edit, not the
# machine-churn of the live-feed hub notes (Status Hub, Markets, Dashboard, ...).
# Bot/ is excluded too: obsidian_enrich re-writes those docs on every snapshot.
CORE_FOLDERS = ["Lessons", "Edges", "Legs Archive", "Journal"]


def core_notes():
    files = []
    for d in CORE_FOLDERS:
        p = VAULT / d
        if p.is_dir():
            files += list(p.rglob("*.md"))
    return [f for f in files if "graphify-out" not in str(f)]


def ollama_up():
    try:
        with urllib.request.urlopen(OLLAMA, timeout=4) as r:
            json.loads(r.read())
        return True
    except Exception:
        return False


def stage():
    if STAGE.exists():
        shutil.rmtree(STAGE)
    STAGE.mkdir(parents=True)
    for f in concept_notes():
        rel = f.relative_to(VAULT)
        dst = STAGE / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, dst)
    return sum(1 for _ in STAGE.rglob("*.md"))


def counts(path):
    try:
        d = json.loads(Path(path).read_text())
        return len(d.get("nodes", [])), len(d.get("edges", d.get("links", [])))
    except Exception:
        return None, None


def main(quick=False):
    notes = concept_notes()
    core = core_notes()
    baseline = OPUS.stat().st_mtime if OPUS.exists() else 0
    changed = [f for f in core if f.stat().st_mtime > baseline]
    base_str = time.strftime('%Y-%m-%d %H:%M', time.localtime(baseline)) if baseline else "never"
    log(f"staleness: {len(changed)}/{len(core)} hand-authored concept notes changed since last opus build ({base_str})")

    on, oe = counts(OPUS)
    sn = se = None
    extract_status = "skipped (--quick)" if quick else None
    if not quick:
        if not ollama_up():
            extract_status = "SKIPPED — ollama down"
            log("ollama not responding on :11434 — skipping shadow extract (fail-soft)")
        else:
            n = stage()
            log(f"staged {n} notes to {STAGE}; running ollama extract (this is slow)...")
            try:
                env = dict(os.environ, OLLAMA_API_KEY="local")
                r = subprocess.run(
                    [GRAPHIFY, "extract", str(STAGE), "--backend", "ollama",
                     "--model", OLLAMA_MODEL, "--max-concurrency", "1", "--no-cluster"],
                    capture_output=True, text=True, timeout=7200, env=env)
                shadow_src = STAGE / "graphify-out/graph.json"
                if r.returncode == 0 and shadow_src.exists():
                    shutil.copy2(shadow_src, SHADOW)
                    sn, se = counts(SHADOW)
                    extract_status = "ok"
                    log(f"shadow extract OK -> {SHADOW} ({sn} nodes, {se} edges)")
                else:
                    extract_status = f"FAILED rc={r.returncode}"
                    log(f"shadow extract failed rc={r.returncode}: {(r.stderr or '')[-300:]}")
            except subprocess.TimeoutExpired:
                extract_status = "TIMEOUT (90m)"
                log("shadow extract timed out")
            except Exception as e:
                extract_status = f"ERROR {type(e).__name__}"
                log(f"shadow extract error: {e}")

    verdict = ("REBUILD WORTH IT — run the opus concept rebuild"
               if len(changed) >= CHANGED_ALERT else "fresh enough — no rebuild needed")
    lines = [
        "---", "type: freshness", "status: " + ("stale" if len(changed) >= CHANGED_ALERT else "fresh"),
        f"date: {time.strftime('%Y-%m-%d')}", "---", "",
        "# Concept Brain Freshness", "",
        f"_Last checked: {time.strftime('%Y-%m-%d %H:%M')}_  ·  weekly shadow job (`com.aryan.concept-shadow`)", "",
        f"- **Changed hand-authored concept notes since last opus build:** {len(changed)} / {len(core)}  (baseline {base_str})",
        f"- _(full extract corpus: {len(notes)} notes incl. auto-churn hubs)_",
        f"- **Verdict:** {verdict}",
        f"- **Opus graph (live):** {on} nodes / {oe} edges" if on else "- Opus graph: missing",
        f"- **ollama shadow:** {extract_status}" + (f" — {sn} nodes / {se} edges" if sn else ""),
        "",
    ]
    if changed:
        lines.append("Recently changed notes:")
        for f in sorted(changed, key=lambda x: -x.stat().st_mtime)[:15]:
            lines.append(f"- `{f.relative_to(VAULT)}`")
        lines.append("")
    lines.append("> To rebuild high-fidelity: ask Claude **\"rebuild the concept brain\"** "
                 "(opus extraction; the shadow here is low-fidelity ollama, for signal only).")
    try:
        NOTE.write_text("\n".join(lines))
    except Exception as e:
        log(f"note write failed: {e}")
    log(f"verdict: {verdict}")
    return 0


LOCK = Path("/tmp/concept-shadow-refresh.lock")


def acquire_lock():
    """Skip (exit 0) if another run is in flight — prevents concurrent runs from
    corrupting the shared /tmp stage (a launchd fire overlapping a manual run)."""
    if LOCK.exists():
        try:
            age = time.time() - LOCK.stat().st_mtime
        except Exception:
            age = 0
        if age < 5400:   # < 90m: assume live
            log(f"another run in flight (lock {int(age)}s old) — skipping")
            return False
        log("stale lock (>90m) — taking over")
    LOCK.write_text(str(os.getpid()))
    return True


if __name__ == "__main__":
    quick = "--quick" in sys.argv
    if quick:
        sys.exit(main(quick=True))
    if not acquire_lock():
        sys.exit(0)
    try:
        sys.exit(main(quick=False))
    except Exception as e:
        log(f"FATAL {type(e).__name__}: {e}")
        sys.exit(1)
    finally:
        try:
            LOCK.unlink()
        except Exception:
            pass
