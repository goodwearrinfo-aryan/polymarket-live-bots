#!/usr/bin/env python3
"""
brain_ingest.py — self-organizing ingest for the Obsidian brain (2026-06-15).

ONE tool fusing the best patterns from 5 vetted second-brain repos:
  - drop folder        (AgriciDaniel/claude-obsidian, NicholasSpisak/second-brain): drop sources in _inbox/
  - librarian filing   (NicholasSpisak/second-brain): each source -> structured linked note + maintained index
  - PARA buckets       (jamesmcroft/obsidian-ai-second-brain): Projects / Areas / Resources / Archive
  - graph connections  (BEKO2210/Firstbrain): optional `--graph` hook into graphify (already installed)
  - always-on capture  (smixs/agent-second-brain): a watched inbox — WITHOUT the Telegram bot / VPS surface

SAFETY (the auto-organizer risk): writes ONLY under <vault>/brain/ and <vault>/_inbox/.
NEVER touches BRAIN.md, analyst.json, or any canonical trading file, and never
writes into ~/Documents/polymarket. Originals are MOVED to _inbox/_processed,
never deleted (honors "never clone-then-delete").

  python3 brain_ingest.py            # organize everything in _inbox/
  python3 brain_ingest.py --graph    # also refresh the graphify graph after
"""
import os, re, shutil, sys, subprocess
from datetime import datetime, timezone
from pathlib import Path

VAULT = Path(os.path.expanduser("~/Documents/PolymarketVault"))
BRAIN = VAULT / "brain"
INBOX = VAULT / "_inbox"
PROCESSED = INBOX / "_processed"
PARA = ["Projects", "Areas", "Resources", "Archive"]
SAFE_ROOTS = [BRAIN, INBOX]                      # the ONLY places we may write
TEXT_EXT = (".md", ".markdown", ".txt")

# domain glossary so ingested notes auto-link to the trading brain's concepts
GLOSSARY = [
    "nearres", "truefade", "nearresfade", "fastfade", "analyst", "judge_panel",
    "scorecard", "analyst_hunt", "edge", "spread", "gap-through", "Wang transform",
    "DSR", "Brier", "coinflip", "coinup", "coindown", "diverg", "feargreed",
    "lateprox", "whale", "maker", "control", "bootstrap", "calibration",
    "Polymarket", "Kalshi", "bitcoin", "silver", "iran", "resolution",
]


def _guard(path: Path):
    rp = path.resolve()
    if not any(str(rp).startswith(str(r.resolve())) for r in SAFE_ROOTS):
        raise RuntimeError(f"REFUSED unsafe write outside brain/_inbox: {rp}")


def classify(text, name):
    # Classify on the TITLE/filename (strong signal), not buried body prose —
    # words like "killed"/"dead" appear constantly in trading notes' bodies and
    # would wrongly Archive a useful lesson. Body is only a weak fallback.
    title = next((l.strip("# ").strip() for l in text.splitlines()
                  if l.strip().startswith("#") and l.strip("# ").strip()), "")
    head = (title + " " + name).lower()
    body = text.lower()
    if any(k in head for k in ("archive", "deprecated", "retired", "obsolete", "dead end")):
        return "Archive"
    if any(k in head for k in ("todo", "plan", "roadmap", "sprint", "deploy", "build")):
        return "Projects"
    if any(k in head for k in ("bot", "ops", "monitor", "health", "watchdog", "routine", "alert", "daily")):
        return "Areas"
    if any(k in body for k in ("- [ ]", "todo:", "next steps")):  # weak fallback
        return "Projects"
    return "Resources"


def slugify(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:60] or "note"


def existing_note_names():
    return {p.stem for p in VAULT.rglob("*.md")}


def find_links(text, known):
    low = text.lower()
    out = []
    for term in list(known) + GLOSSARY:
        if term and len(term) > 3 and term.lower() in low:
            if term not in out:
                out.append(term)
    return out[:12]


def ingest_file(fp: Path, known):
    text = fp.read_text(errors="replace")
    title = next((l.lstrip("# ").strip() for l in text.splitlines()
                  if l.strip().startswith("#") and l.strip("# ").strip()), fp.stem)
    bucket = classify(text, fp.name)
    links = find_links(text, known)
    dest = BRAIN / bucket / f"{slugify(title)}.md"
    _guard(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    fm = ["---", f"title: {title}", f"para: {bucket}", f"source: {fp.name}",
          f"ingested_at: {datetime.now(timezone.utc).isoformat()}", "tags: [ingested]", "---", ""]
    body = [f"# {title}", "", text.strip(), ""]
    if links:
        body += ["", "## Related", ""] + [f"- [[{l}]]" for l in links]
    dest.write_text("\n".join(fm + body))
    return bucket, dest, links


def rebuild_index():
    lines = ["# Brain Index", "",
             f"_Self-organizing ingest. Drop files in `_inbox/`; run brain_ingest.py._",
             f"_Updated {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}_", ""]
    total = 0
    for bucket in PARA:
        d = BRAIN / bucket
        notes = sorted(d.glob("*.md")) if d.exists() else []
        if not notes:
            continue
        total += len(notes)
        lines += [f"## {bucket} ({len(notes)})", ""] + [f"- [[{n.stem}]]" for n in notes] + [""]
    if total == 0:
        lines += ["_No notes yet — drop a `.md`/`.txt` into `_inbox/` and run the tool._"]
    _guard(BRAIN / "index.md")
    (BRAIN / "index.md").write_text("\n".join(lines))
    return total


def main():
    for d in [BRAIN, INBOX, PROCESSED] + [BRAIN / b for b in PARA]:
        d.mkdir(parents=True, exist_ok=True)
    known = existing_note_names()
    files = [f for f in INBOX.glob("*") if f.is_file() and f.suffix.lower() in TEXT_EXT]
    if not files:
        n = rebuild_index()
        print(f"[brain_ingest] inbox empty; index has {n} notes")
        return
    print(f"[brain_ingest] organizing {len(files)} file(s) from _inbox/")
    for fp in files:
        try:
            bucket, dest, links = ingest_file(fp, known)
            shutil.move(str(fp), str(PROCESSED / fp.name))
            print(f"  + {fp.name} -> brain/{bucket}/{dest.name}  ({len(links)} links)")
        except Exception as e:
            print(f"  ! {fp.name} skipped: {e}")
    total = rebuild_index()
    if "--graph" in sys.argv:
        pyf = Path(__file__).parent / "graphify-out" / ".graphify_python"
        if pyf.exists():
            print("[brain_ingest] refreshing graphify graph (--update)...")
            try:
                subprocess.run([pyf.read_text().strip(), "-m", "graphify", "update",
                                str(VAULT)], timeout=600, check=False)
            except Exception as e:
                print(f"  graph refresh skipped: {e}")
    print(f"[brain_ingest] done — {total} notes in brain/")


if __name__ == "__main__":
    main()
