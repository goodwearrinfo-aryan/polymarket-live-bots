#!/usr/bin/env python3
"""brain_rebuild_nv.py — high-fidelity vault-concept brain rebuild on FREE NVIDIA Maverick.

The $0 replacement for the manual *opus* concept rebuild. Reuses concept_shadow_refresh's
corpus staging, runs `graphify extract` via NVIDIA Maverick (the 3-key rotation, $0 metered),
and — ONLY if the result is sane (>= FLOOR nodes) — swaps it into the real
ConceptGraph/graphify-out/graph.json (backing up the prior), then re-propagates via the
project's own tools (refresh_unified_brain.py + brain_ingest.py --graph). TCC-clean
(framework python, /tmp stage). Fail-soft: any failure leaves the existing brain untouched.

Schedule: daily 4:30am via com.aryan.brain-rebuild-nv.
Usage:  python3 brain_rebuild_nv.py [--dry-run] [--force]
  --dry-run : stage + validate key/paths/reachability, but do NOT extract or swap.
"""
import os, sys, json, shutil, subprocess, time
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import concept_shadow_refresh as css   # reuse its corpus staging

HOME = Path.home()
GFY = "/Users/aryanagarwal/.local/share/uv/tools/graphifyy/bin/graphify"
MODEL = "nvidia/nemotron-3-ultra-550b-a55b"   # maverick EOL 2026-07-27 (410); nvultra is the live replacement
NVBASE = "https://integrate.api.nvidia.com/v1"
REAL = HOME / "Documents/PolymarketVault/ConceptGraph/graphify-out/graph.json"
STAGE = Path("/tmp/brain_rebuild_nv_stage")   # OWN dir — never share /tmp/concept_shadow (race: both fire 04:30)
LOG = "/tmp/brain-rebuild-nv.log"
FLOOR = 200   # never replace a ~500-node brain with a tiny/broken extraction


def stage():
    """Stage the SAME concept corpus as the shadow, but into our OWN dir (no shared-dir race)."""
    shutil.rmtree(STAGE, ignore_errors=True)
    STAGE.mkdir(parents=True, exist_ok=True)
    n = 0
    for f in css.concept_notes():
        try:
            shutil.copy2(f, STAGE / f.name); n += 1
        except Exception:
            pass
    return n


def log(m):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {m}"
    print(line)
    try:
        open(LOG, "a").write(line + "\n")
    except Exception:
        pass


def nvidia_key():
    try:
        for l in open(HOME / "Documents/polymarket/.env"):
            if l.startswith("NVIDIA_API_KEY="):
                return l.rstrip("\n").split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return os.environ.get("NVIDIA_API_KEY", "")


def main():
    dry = "--dry-run" in sys.argv
    key = nvidia_key()
    if not key:
        log("ABORT: no NVIDIA_API_KEY"); return 1
    n = stage()  # -> our own /tmp/brain_rebuild_nv_stage (no race with concept-shadow)
    log(f"staged {n} concept notes" + (" [dry-run: stop before extract]" if dry else "; extracting on NVIDIA Maverick..."))
    if dry:
        log(f"dry-run OK: key present, graphify={'yes' if os.path.exists(GFY) else 'MISSING'}, "
            f"real-graph={'exists' if REAL.exists() else 'absent'}, floor={FLOOR}")
        return 0

    env = dict(os.environ, OPENAI_BASE_URL=NVBASE, OPENAI_API_KEY=key)
    try:
        r = subprocess.run(
            [GFY, "extract", str(STAGE), "--backend", "openai", "--model", MODEL,
             "--max-concurrency", "4", "--no-cluster"],
            capture_output=True, text=True, timeout=2400, env=env)
    except subprocess.TimeoutExpired:
        log("ABORT: extract TIMEOUT (40m) — kept old graph"); return 1
    if r.returncode != 0:
        log(f"ABORT: extract rc={r.returncode}: {(r.stderr or '')[-300:]} — kept old graph"); return 1

    new = STAGE / "graphify-out" / "graph.json"
    try:
        nn = len(json.load(open(new)).get("nodes", []))
    except Exception as e:
        log(f"ABORT: extraction unreadable ({e}) — kept old graph"); return 1
    if nn < FLOOR:
        log(f"ABORT: extraction only {nn} nodes (< floor {FLOOR}) — bad run, kept old graph"); return 1

    # MONOTONIC UNION — fold the fresh extraction INTO the rich base by normalized label.
    # The brain can only GROW (never downgrade): a thin Maverick run enriches, never shrinks.
    before = len(json.load(open(REAL)).get("nodes", [])) if REAL.exists() else 0
    if REAL.exists():
        shutil.copy2(REAL, str(REAL) + ".pre-nv.bak")
    REAL.parent.mkdir(parents=True, exist_ok=True)
    u = subprocess.run(["python3", str(HERE / "brain_union.py"), str(REAL), str(new), str(REAL)],
                       capture_output=True, text=True, timeout=180)
    log("union: " + (u.stdout or u.stderr).strip()[:200])
    after = len(json.load(open(REAL)).get("nodes", []))
    if after < before:   # safety: union must never shrink — if it did, restore
        log(f"ABORT: union shrank {before}->{after}; restoring backup")
        shutil.copy2(str(REAL) + ".pre-nv.bak", REAL); return 1
    log(f"concept graph: {before} -> {after} nodes (monotonic, +{after-before})")

    # PROPAGATE to the global brain that `brain`/Obsidian actually reads
    p = subprocess.run(["python3", str(HERE / "brain_to_global.py"), str(REAL)],
                       capture_output=True, text=True, timeout=180)
    log("global: " + (p.stdout or p.stderr).strip()[:200])

    # refresh the secondary views (fail-soft)
    for cmd, lbl in [(["python3", str(HERE / "refresh_unified_brain.py")], "unified-merge"),
                     (["python3", str(HERE / "brains.py")], "brains-canvas"),
                     (["python3", str(HERE / "brain_gradients.py")], "gradient-map")]:
        try:
            subprocess.run(cmd, timeout=600); log(f"{lbl} done")
        except Exception as e:
            log(f"{lbl} failed (non-fatal): {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
