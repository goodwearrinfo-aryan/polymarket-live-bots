#!/usr/bin/env python3
"""vault_librarian.py — a read-only 24/7 sentinel that LIVES IN the Obsidian vault.

Runs the corrected vault-health sweep (the 2026-06-21 full-pass logic) on a schedule
and writes `Vault Health.md` with a score + findings. READ-ONLY: it reports, never
deletes or rewrites notes (sentinel discipline — heal-not-kill). Deterministic, no LLM.

Correctness baked in (these were the false-positive traps in the raw audit):
  - the `memory/` dir is a symlink Obsidian follows but os.walk does not -> index it explicitly
  - `.base` / `.canvas` link targets are real notes in Obsidian -> count them as valid
  - escaped-pipe `\\|` table cells split wrong -> strip before resolving
  - Graph/ConceptGraph/UnifiedBrain are machine-generated -> excluded from hygiene

stdlib-only + framework python = TCC-clean under launchd. Fail-soft, lockfile-guarded.
"""
import os, re, json, time, collections
from pathlib import Path

VAULT = Path.home() / "Documents/PolymarketVault"
MEM = Path.home() / ".claude/projects/-Users-aryanagarwal-Documents-Codex-2026-05-28-is-git-installed/memory"
NOTE = VAULT / "Vault Health.md"
LOG = Path("/tmp/vault-librarian.log")
LOCK = Path("/tmp/vault-librarian.lock")
SKIP_DIRS = {".git", ".obsidian", "graphify-out"}
EXCL_PREFIX = ("Graph/", "ConceptGraph/", "UnifiedBrain/",
               "Connectors/", "Live/")   # machine-generated live-map graph nodes
                                          # (wiring_keeper.py) — linked via graph tags,
                                          # not wikilinks; live-map integrity is the
                                          # wiring-warden's job, not hand-authored hygiene
AUTO_PREFIX = ("Reports/", "Analyst/", "Stocks/", "Templates/", "Archive/")
LINKABLE_EXT = (".md", ".base", ".canvas", ".png")
DAILY = re.compile(r"\d{4}-\d{2}-\d{2}")


def log(m):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {m}"
    print(line)
    try: LOG.open("a").write(line + "\n")
    except Exception: pass


# ── SAFETY INVARIANT ──────────────────────────────────────────────────────────
# This agent NEVER deletes or rewrites your notes. The ONLY vault path it may
# write is its own report note (NOTE). All vault writes go through _write_report;
# there is intentionally NO delete/unlink of any vault path anywhere in this file
# (the only unlink is the /tmp lockfile). Do not add note deletion here — if a
# future "auto-fix" mode is ever wanted, it must be a separate, explicitly-gated
# tool, not this read-only sentinel.
def _write_report(content):
    assert NOTE.parent.resolve() == VAULT.resolve() and NOTE.suffix == ".md", \
        "librarian may only write its own vault report note"
    NOTE.write_text(content)


def is_excl(rel): return any(rel.startswith(p) for p in EXCL_PREFIX)


def is_freshness(full):
    """machine-generated status notes (Vault Health, Suggested Connections, Concept Brain
    Freshness, brain-gate, etc.) carry `type: freshness` — exclude from hygiene so the
    librarian's own [[X]] report text doesn't self-pollute the dead-link count."""
    try:
        head = full.read_text(encoding="utf-8", errors="ignore")[:300]
    except Exception:
        return False
    return head.startswith("---") and re.search(r"^type:\s*freshness\s*$", head, re.M) is not None


def is_auto(rel): return any(rel.startswith(p) for p in AUTO_PREFIX) or bool(DAILY.fullmatch(os.path.basename(rel)[:-3] or ""))


def link_targets(text):
    for m in re.findall(r"\[\[([^\]]+)\]\]", text):
        x = m.split("\\|")[0].split("|")[0].split("#")[0].strip().rstrip("\\").strip()
        if x: yield x


def sweep():
    names, paths, notes = set(), set(), []
    for dp, dn, fn in os.walk(VAULT):
        dn[:] = [d for d in dn if d not in SKIP_DIRS]
        for f in fn:
            ext = os.path.splitext(f)[1]
            if ext not in LINKABLE_EXT: continue
            rel = os.path.relpath(os.path.join(dp, f), VAULT)
            if ext == ".md": notes.append(rel)
            names.add(os.path.splitext(f)[0].lower())
            paths.add(rel[:-len(ext)].lower()); paths.add(rel.lower())
    if MEM.is_dir():
        for f in os.listdir(MEM):
            if f.endswith(".md"):
                names.add(f[:-3].lower()); paths.add(("memory/" + f[:-3]).lower())

    dead = collections.defaultdict(list)
    inbound = collections.Counter()
    scratch = []
    freshness = set()
    for rel in notes:
        full = VAULT / rel
        if os.path.basename(rel).lower().startswith("untitled") and full.stat().st_size < 60:
            scratch.append(rel)
        if is_excl(rel): continue
        if is_freshness(full):                 # machine status note — don't scan its [[X]] report text
            freshness.add(rel); continue
        try: txt = full.read_text(encoding="utf-8", errors="ignore")
        except Exception: continue
        for t in link_targets(txt):
            tl = t.lower(); base = tl.split("/")[-1]
            ext = os.path.splitext(base)[1]
            key = base[:-len(ext)] if ext in LINKABLE_EXT else base
            if base in names or key in names or tl in paths or tl + ".md" in paths:
                inbound[base] += 1
            else:
                dead[rel].append(t)
    # scratch .base/.canvas too
    for ext in (".base", ".canvas"):
        for p in VAULT.glob(f"Untitled*{ext}"):
            if p.stat().st_size < 60: scratch.append(os.path.relpath(p, VAULT))

    orphans = [n for n in notes if not is_excl(n) and not is_auto(n) and n not in freshness
               and os.path.basename(n)[:-3].lower() not in inbound
               and os.path.basename(n) != "Home.md"]
    # stale: root-level hand notes (non-auto) untouched > 30 days
    now = time.time()
    stale = []
    for n in notes:
        if "/" in n or is_excl(n) or is_auto(n): continue
        age_d = (now - (VAULT / n).stat().st_mtime) / 86400
        if age_d > 30: stale.append((n, int(age_d)))

    n_dead = sum(len(v) for v in dead.values())
    # health score: start 100, subtract for issues (capped)
    score = 100 - min(20, n_dead * 2) - min(15, len(orphans)) - min(10, len(scratch) * 3) - min(10, len(stale))
    score = max(0, score)
    return dict(notes=len(notes), dead=dict(dead), n_dead=n_dead, orphans=sorted(orphans),
                scratch=sorted(set(scratch)), stale=sorted(stale, key=lambda x: -x[1]), score=score)


def write_note(r):
    status = "healthy" if r["score"] >= 85 else ("ok" if r["score"] >= 70 else "needs-attention")
    L = ["---", "type: freshness", f"status: {status}", f"date: {time.strftime('%Y-%m-%d')}", "---", "",
         "# Vault Health", "",
         f"_Auto-swept by the Vault Librarian (`com.aryan.vault-librarian`, every 6h) · {time.strftime('%Y-%m-%d %H:%M')}_", "",
         f"## Score: {r['score']}/100  ({status})", "",
         f"- Notes audited: **{r['notes']}** (excl. machine-generated Graph/ConceptGraph/Unified)",
         f"- Dead links: **{r['n_dead']}**  ·  Orphans: **{len(r['orphans'])}**  ·  Scratch files: **{len(r['scratch'])}**  ·  Stale (>30d) hubs: **{len(r['stale'])}**",
         "",
         "> Read-only sentinel — it flags, it does not edit. Fix the real ones; many dead links in `Reports/`/`Analyst/` auto-notes re-break on regen (expected).", ""]
    if r["dead"]:
        L += ["## Dead links", ""]
        for src, ts in sorted(r["dead"].items(), key=lambda kv: -len(kv[1]))[:20]:
            L.append(f"- `{src}` ({len(ts)}): " + ", ".join(f"`{t}`" for t in ts[:6]) + (" …" if len(ts) > 6 else ""))
        L.append("")
    if r["scratch"]:
        L += ["## Scratch files (safe to delete)", ""] + [f"- `{s}`" for s in r["scratch"]] + [""]
    if r["orphans"]:
        L += ["## Orphans (no inbound wikilink — non-auto)", ""] + [f"- `{o}`" for o in r["orphans"][:25]] + [""]
    if r["stale"]:
        L += ["## Stale hub notes (>30d untouched)", ""] + [f"- `{n}` — {d}d" for n, d in r["stale"][:15]] + [""]
    L += ["## Related", "[[Second Brain]] · [[Home]] · [[Brain Architecture]]"]
    _write_report("\n".join(L))


def main():
    if LOCK.exists() and (time.time() - LOCK.stat().st_mtime) < 1800:
        log("another run in flight — skip"); return 0
    LOCK.write_text(str(os.getpid()))
    try:
        if not VAULT.is_dir():
            log("vault not found — skip (fail-soft)"); return 0
        r = sweep()
        write_note(r)
        log(f"OK score={r['score']} notes={r['notes']} dead={r['n_dead']} orphans={len(r['orphans'])} scratch={len(r['scratch'])} stale={len(r['stale'])}")
        return 0
    finally:
        try: LOCK.unlink()
        except Exception: pass


if __name__ == "__main__":
    import sys
    try: sys.exit(main())
    except Exception as e:
        log(f"FATAL {type(e).__name__}: {e}"); sys.exit(1)
