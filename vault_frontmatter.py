#!/usr/bin/env python3
"""vault_frontmatter.py — stamp Dataview-queryable YAML frontmatter onto vault notes.

Why: ~20+ scripts auto-write notes into PolymarketVault/Reports & Bot/ WITHOUT frontmatter,
so Dataview can't query them ("all loggers", "stale notes", "lessons"). Editing every
writer is fragile; this central, IDEMPOTENT stamper runs after them (watchdog) and adds
type/updated/tags inferred from folder + content. Run each cycle — auto-notes that got
rewritten without frontmatter are re-stamped; notes that ALREADY have `---` are left alone
(so the hand-curated MOCs with rich status/verdict frontmatter are never clobbered).

PAPER project infra; read/writes only the vault. Stdlib only.
Usage: python3 vault_frontmatter.py [--dry]
"""
import os, sys, re, time, glob

VAULT = os.path.expanduser("~/Documents/PolymarketVault")
SKIP_DIRS = ("Graph/", "/.obsidian/", "memory/", "Templates/", "_merged_from_ObsidianVault/")  # Graph=auto, memory=own frontmatter

# folder -> note type
def infer_type(rel):
    if rel.startswith("Lessons/"): return "lesson"
    if rel.startswith("Reports/Markets/"): return "market"
    if rel.startswith("Reports/"): return "logger"
    if rel.startswith("Bot/"): return "subsystem"
    if rel.startswith("Runbooks/"): return "runbook"
    if rel.startswith("Journal/"): return "journal"
    return "hub"   # root-level MOCs/hubs

def infer_tags(rel, text):
    tags = []
    t = (rel + " " + text[:400]).lower()
    for kw in ("whale","basket","analyst","hormuz","nearres","fade","crypto","spread","onchain","news"):
        if kw in t: tags.append(kw)
    return tags[:4]

def stamp(path, dry):
    rel = os.path.relpath(path, VAULT)
    if any(s in (rel if s.endswith("/") and rel.startswith(s) else "/"+rel) for s in SKIP_DIRS): return None
    if any(rel.startswith(s) for s in ("Graph/","Templates/","memory/")) or "/.obsidian/" in path: return None
    txt = open(path, encoding="utf-8", errors="ignore").read()
    if txt.lstrip().startswith("---"):
        # REPAIR: a prior buggy run auto-added an unreliable `status:` line under a
        # type:+updated: stamp. Strip it (status is hand-set on curated notes, not inferred).
        m = re.match(r"^(---\n.*?\n---\n)", txt, re.S)
        if m and "type: hub" in m.group(1) and "updated:" in m.group(1) and re.search(r"^status:.*$", m.group(1), re.M):
            fixed = re.sub(r"^status:.*\n", "", m.group(1), flags=re.M)
            if not dry:
                open(path, "w", encoding="utf-8").write(fixed + txt[m.end():])
            return f"repaired (stripped bad status) {rel}"
        return None
    typ = infer_type(rel); tags = infer_tags(rel, txt)
    mtime = time.strftime("%Y-%m-%d", time.localtime(os.path.getmtime(path)))
    fm = ["---", f"type: {typ}", f"updated: {mtime}"]
    if tags: fm.append("tags: [" + ", ".join(tags) + "]")
    fm.append("---\n")
    if dry:
        return f"WOULD stamp {rel}: type={typ} tags={tags}"
    open(path, "w", encoding="utf-8").write("\n".join(fm) + "\n" + txt)
    return f"stamped {rel}"

def main():
    dry = "--dry" in sys.argv
    n = 0
    for path in glob.glob(os.path.join(VAULT, "**", "*.md"), recursive=True):
        r = stamp(path, dry)
        if r:
            n += 1
            if dry or n <= 5: print("  " + r)
    print(f"{'(dry) ' if dry else ''}stamped {n} notes lacking frontmatter")

if __name__ == "__main__":
    main()
