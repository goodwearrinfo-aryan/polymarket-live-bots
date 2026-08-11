#!/usr/bin/env python3
"""
obsidian_enrich.py — autonomous subsystem-doc enricher for the Obsidian vault.

The 16 Bot/ subsystem notes are LIVE-STATE mirrors that obsidian_snapshot.py
rewrites every 6h — so any hand/agent enrichment is clobbered. This job re-enriches
them from the REAL source code, driven by a LOCAL, FREE LLM (Ollama llama3.1:8b via
llm_client.py). It runs as 6 "agents":

    enrich:A  opportunity_ranker, anchor_hunter, internal_consistency
    enrich:B  attention_decay, edge_hunter_agent, confluence_agent
    enrich:C  liquidity_scanner, news_velocity_agent, breaking_news_agent
    enrich:D  hypothesis_lab, position_monitor, position_defender
    enrich:E  xvenue, brain_snapshot, scout_candidates, ports_analysis_hub
    verify    deterministic broken-[[wikilink]] scan over all Bot/ notes

IDEMPOTENT: a note is enriched only if it lacks the enrichment marker AND lacks a
"## How it works" heading — so a freshly-snapshot-clobbered note gets re-enriched,
but an already-rich note is left alone (no wasted LLM calls). Run frequently (launchd
every 30min); steady-state it does almost nothing and only fires right after a 6h
snapshot wipes the docs.

Architecture: Judgment -> LLM (the prose), Math/IO -> deterministic Python (file
reads, source mapping, link scan). LLM is FORCED to local Ollama regardless of .env.

Run:
    python3 obsidian_enrich.py            # enrich all stale notes + verify (default)
    python3 obsidian_enrich.py --force    # re-enrich every note (ignore marker)
    python3 obsidian_enrich.py --agent A  # one enricher only
    python3 obsidian_enrich.py --verify   # link-verify only, no LLM
Paper-only infra. No funds, no orders, no API keys (local Ollama is keyless).
"""
import os, sys, re, glob, time, argparse
from datetime import datetime, timezone

# ── FORCE the free local model (do NOT inherit a .env NVIDIA/Groq provider) ──
os.environ["LLM_PROVIDER"] = "ollama"
os.environ["OLLAMA_MODEL"] = os.environ.get("OBSIDIAN_ENRICH_MODEL", "llama3.1:8b")

SRC   = os.path.dirname(os.path.abspath(__file__))
VAULT = os.path.join(os.path.expanduser("~"), "Documents/PolymarketVault/Bot")
LOG   = os.path.join(SRC, "obsidian_enrich.log")
MARKER = "<!-- enriched-by:obsidian_enrich -->"
MODEL  = os.environ["OLLAMA_MODEL"]

sys.path.insert(0, SRC)
import llm_client       # noqa: E402  (provider-agnostic; we forced ollama above)
import obsidian_snapshot as snap   # noqa: E402  (reuse the durable enrichment cache + apply)

# note basename (no .md) -> primary source file(s), relative to SRC unless absolute.
# Mapping resolved from the real codebase (incl. the non-obvious ones).
AGENTS = {
    "A": {
        "opportunity_ranker":   ["opportunity_ranker_agent.py"],
        "anchor_hunter":        ["anchor_hunter_agent.py"],
        "internal_consistency": ["internal_consistency_agent.py"],
    },
    "B": {
        "attention_decay":      ["attention_decay_agent.py"],
        "edge_hunter_agent":    ["edge_hunter_agent.py"],
        "confluence_agent":     ["confluence_agent.py"],
    },
    "C": {
        "liquidity_scanner":    ["liquidity_scanner_agent.py"],
        "news_velocity_agent":  ["news_velocity_agent.py"],
        "breaking_news_agent":  ["breaking_news_agent.py"],
    },
    "D": {
        "hypothesis_lab":       ["hypothesis_lab.py"],
        "position_monitor":     ["position_monitor.py"],
        "position_defender":    ["position_defender_agent.py"],
    },
    "E": {
        "xvenue":               ["edge2_xvenue.py", "xvenue_logger.py"],
        # brain_snapshot EXCLUDED: its "source" is BRAIN.md (prose, not a code module), so the
        # code-doc prompt made the 8B fabricate identifiers (caught by audit_grounding at 0.40).
        # Its native snapshot_brain_excerpt is the correct content — leave it un-enriched.
        "scout_candidates":     ["market_scout.py"],
        "ports_analysis_hub":   ["ports_analysis_common.py"],
    },
}

MAX_SRC_CHARS = 9000   # keep the 8B model's context tight & on-task


def log(msg):
    line = f"[{datetime.now(timezone.utc).isoformat()[:19]}Z] {msg}"
    print(line)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _read_source(files):
    """Concatenate the real source for the prompt (truncated, labeled). Returns (text, found, missing)."""
    chunks, found, missing = [], [], []
    for f in files:
        p = f if os.path.isabs(f) else os.path.join(SRC, f)
        if not os.path.exists(p):
            missing.append(f); continue
        try:
            txt = open(p, encoding="utf-8", errors="replace").read()
        except Exception:
            missing.append(f); continue
        if f.endswith("BRAIN.md"):
            # snapshot_brain_excerpt only uses the first "## " section — match that scope
            parts = re.split(r"^## ", txt, flags=re.MULTILINE)
            txt = ("## " + parts[1]) if len(parts) > 1 else txt
        chunks.append(f"### SOURCE: {os.path.basename(p)}\n```\n{txt[:MAX_SRC_CHARS]}\n```")
        found.append(os.path.basename(p))
    return "\n\n".join(chunks), found, missing


def _ensure_info_callout(body):
    """Guarantee the purpose line under the `# title` is a proper `> [!info]` callout.
    The 8B often drops the `[!info]` type, leaving a bare `> blockquote` or plain text —
    this normalizes it deterministically so the callout always renders."""
    lines = body.split("\n")
    ti = next((i for i, l in enumerate(lines) if l.startswith("# ")), None)
    if ti is None:
        return body
    si = next((i for i in range(ti + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    region = lines[ti + 1:si]
    if any(l.lstrip().startswith("> [!") for l in region):
        return body  # already a real callout
    for j, l in enumerate(region):
        s = l.strip()
        if not s:
            continue
        text = s[1:].strip() if s.startswith(">") else s   # drop a bare blockquote '>'
        if text.startswith("[!"):                          # half-written "[!info] x"
            text = text.split("]", 1)[-1].strip() or text
        region[j] = f"> [!info] {text}"
        break
    lines[ti + 1:si] = region
    return "\n".join(lines)


def _newest_src_mtime(files):
    """Newest mtime among the source files (for cache-staleness checks)."""
    m = 0.0
    for f in files:
        p = f if os.path.isabs(f) else os.path.join(SRC, f)
        if os.path.exists(p):
            m = max(m, os.path.getmtime(p))
    return m


def _frontmatter(note, found):
    """Deterministic Obsidian YAML frontmatter (built in Python, never the LLM, so the
    properties can't be malformed). Standard keys (title/tags/aliases) + provenance."""
    title = note.replace("_", " ").title()
    srclist = "\n".join(f"  - {s}" for s in found)
    return ("---\n"
            f"title: {title}\n"
            "tags:\n  - bot/subsystem\n"
            f"aliases:\n  - {note}\n"
            f"source:\n{srclist}\n"
            f"model: {MODEL}\n"
            f"updated: {datetime.now(timezone.utc).isoformat()[:10]}\n"
            "---")


def _is_enriched(note_text):
    return bool(note_text) and (MARKER in note_text or "## How it works" in note_text)


def _known_notes():
    """Resolvable [[wikilink]] targets: every vault FILE's basename — both with extension
    (e.g. `subsystems.base`, `graph.canvas`) and stem (e.g. `subsystems`) — lowercased.
    Covers .md notes AND embeddable files (.base, .canvas, images) so legit embeds aren't
    flagged broken / stripped."""
    root = os.path.dirname(VAULT)
    known = set()
    for p in glob.glob(os.path.join(root, "**", "*"), recursive=True):
        if os.path.isfile(p):
            b = os.path.basename(p).lower()
            known.add(b)
            known.add(os.path.splitext(b)[0])
    return known


def _sanitize_links(text, known):
    """Drop any [[link]] whose target note does NOT exist (keep its plain display text),
    so the local model can't leave broken wikilinks that pollute the knowledge graph.
    Valid links (target resolves to a real vault note) are preserved untouched."""
    def repl(m):
        inner = m.group(1)
        target = inner.split("|")[0].split("#")[0].strip().split("/")[-1].lower()
        if target in known:
            return m.group(0)
        return inner.split("|")[-1].split("#")[0].strip() or inner  # broken → plain text
    return re.sub(r"\[\[([^\]]+)\]\]", repl, text)


SYS_PROMPT = (
    "You are a precise technical writer documenting one module of a paper-trading research bot. "
    "You are given the module's REAL source code. Write an Obsidian markdown reference doc for it. "
    "ABSOLUTE RULES: every statement must be supported by the provided source — never invent behavior, "
    "numbers, win-rates, or capabilities. Quote real identifiers (function names, constants and their "
    "values, file paths, endpoints). If something is not in the source, omit it. Output ONLY the markdown "
    "document, no preamble, no code fences around the whole thing."
)


def _enrich_prompt(note, src_text):
    title = note.replace("_", " ").title()
    return (
        f"Module note: `{note}`\n\n{src_text}\n\n"
        "Write ONLY the markdown body (NO YAML frontmatter — it is added separately) with EXACTLY these sections:\n"
        f"# {title}\n"
        "> [!info] (write the one-sentence purpose RIGHT HERE on this same line, not below)\n"
        "## What it is\n(plain-language description from the module docstring + code)\n"
        "## How it works\n(the actual flow: data sources/inputs, core logic and decision rules, the real "
        "thresholds/constants with their values, and what it produces. For any critical gotcha or failure mode, "
        "add a callout and prefix EVERY line of it with `> `, e.g.\n> [!warning] Title\n> detail line.)\n"
        "## Key parameters\n(bullet list of real constants/config: `name = value` — meaning)\n"
        "## Outputs & wiring\n(exactly what it writes and where: log/JSONL/vault note/iMessage/return value)\n"
        "## Related\n(Obsidian [[wikilinks]] to related subsystem notes by basename)\n"
        "Ground every claim in the source above. Use Obsidian callout syntax `> [!info]` / `> [!warning]` exactly."
    )


def enrich_note(note, files, force=False):
    """Generate the source-grounded doc into the DURABLE cache (Bot/.enrich/<note>.md),
    then materialize it onto Bot/<note>.md via the shared apply. Because every snapshot
    re-applies the cache, the doc now survives the watchdog's fast per-agent clobbers.
    Returns ('written'|'skipped'|'nosrc'|'error', detail)."""
    snap.ENRICH_CACHE.mkdir(parents=True, exist_ok=True)
    cache = snap.ENRICH_CACHE / (note + ".md")
    src_text, found, missing = _read_source(files)
    if not found:
        return ("nosrc", f"no source found ({missing})")
    # idempotent: regenerate only when the cache is missing, stale vs source, or forced
    if not force and cache.exists() and os.path.getmtime(cache) >= _newest_src_mtime(files):
        snap.apply_enrichment(note)   # keep the live note carrying the current cache
        return ("skipped", "cache current")
    try:
        body = llm_client.chat(
            [{"role": "system", "content": SYS_PROMPT},
             {"role": "user", "content": _enrich_prompt(note, src_text)}],
            temperature=0.1, max_tokens=1800, timeout=180, model=MODEL,
        ).strip()
    except Exception as e:
        return ("error", f"LLM call failed: {e}")
    if len(body) < 120 or "## How it works" not in body:
        return ("error", f"LLM output too thin/malformed ({len(body)} chars)")
    body = re.sub(r"^```[a-z]*\n", "", body).rstrip("`\n ")          # strip a wrapping fence
    body = _sanitize_links(body, _known_notes())                    # drop invented [[links]]
    body = _ensure_info_callout(body)                               # guarantee the [!info] purpose callout
    block = f"{_frontmatter(note, found)}\n{snap.ENRICH_OPEN}\n\n{body}"
    tmp = str(cache) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(block)
    os.replace(tmp, cache)
    snap.apply_enrichment(note)   # materialize onto the live note now
    return ("written", f"{len(body)}c -> cache+note from {', '.join(found)}")


# Notes that must NOT be auto-enriched — prose-sourced, so the code-doc prompt makes the 8B
# fabricate identifiers (brain_snapshot: source is BRAIN.md, historically caught by grounding).
# Curated AGENTS notes are excluded from auto-discovery by name automatically.
ENRICH_EXCLUDE = {"brain_snapshot"}


def _discover_auto():
    """Auto-discover Bot/ notes that have a matching <note>.py source and aren't already curated in
    AGENTS (or excluded) — so a NEWLY-built subsystem (leg/logger/etc.) is enriched IMMEDIATELY on
    the next run, with NO manual AGENTS edit. Mapping: note 'X' -> ['X.py']. Safe by construction:
    enrich_note is idempotent (cache-freshness → each enriched once, re-done only on source change)
    and the thin-output + link-sanitize guards still apply. The hand-maintained list was the bug."""
    curated = {n for grp in AGENTS.values() for n in grp}
    auto = {}
    for p in sorted(glob.glob(os.path.join(VAULT, "*.md"))):
        note = os.path.splitext(os.path.basename(p))[0]
        if note in curated or note in ENRICH_EXCLUDE:
            continue
        if os.path.exists(os.path.join(SRC, note + ".py")):
            auto[note] = [note + ".py"]
    return auto


def run_enrichers(only=None, force=False):
    stats = {"written": 0, "skipped": 0, "nosrc": 0, "error": 0}
    for ag, notes in AGENTS.items():
        if only and ag.upper() != only.upper():
            continue
        for note, files in notes.items():
            status, detail = enrich_note(note, files, force=force)
            stats[status] += 1
            tag = {"written": "✏️", "skipped": "·", "nosrc": "⚠️", "error": "✗"}[status]
            log(f"  {tag} enrich:{ag} {note} — {status}: {detail}")
    # AUTO group: any Bot note with a matching <note>.py not curated above — enriched immediately
    # so a new subsystem never silently goes un-enriched again ("it should happen immediately").
    if only is None or only.upper() == "AUTO":
        for note, files in _discover_auto().items():
            status, detail = enrich_note(note, files, force=force)
            stats[status] += 1
            tag = {"written": "✏️", "skipped": "·", "nosrc": "⚠️", "error": "✗"}[status]
            log(f"  {tag} enrich:AUTO {note} — {status}: {detail}")
    log(f"enrich done: {stats['written']} written, {stats['skipped']} skipped, "
        f"{stats['nosrc']} no-source, {stats['error']} errors")
    return stats


def sanitize_existing():
    """Strip broken [[wikilinks]] from every existing Bot/ note (no LLM). Idempotent."""
    known, changed = _known_notes(), 0
    for p in glob.glob(os.path.join(VAULT, "*.md")):
        txt = open(p, encoding="utf-8", errors="replace").read()
        new = _sanitize_links(txt, known)
        if new != txt:
            tmp = p + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(new)
            os.replace(tmp, p)
            changed += 1
    log(f"sanitize: stripped broken wikilinks from {changed} note(s)")
    return changed


def verify_links():
    """Deterministic broken-[[wikilink]] scan over all vault notes (no LLM)."""
    known = _known_notes()
    broken, scanned = [], 0
    for p in glob.glob(os.path.join(VAULT, "*.md")):
        txt = open(p, encoding="utf-8", errors="replace").read()
        for m in re.findall(r"\[\[([^\]]+)\]\]", txt):
            scanned += 1
            # Obsidian resolves [[Folder/Note|alias#heading]] to the note basename
            target = m.split("|")[0].split("#")[0].strip().split("/")[-1].lower()
            if target and target not in known:
                broken.append((os.path.basename(p), m))
    log(f"verify: scanned {scanned} wikilinks across Bot/, {len(broken)} broken")
    for note, link in broken[:40]:
        log(f"  ✗ broken: {note} -> [[{link}]]")
    return broken


_TOKEN_CACHE = None
def _project_tokens():
    """Set of every identifier/filename across all project .py (for grounding checks). Cached."""
    global _TOKEN_CACHE
    if _TOKEN_CACHE is None:
        toks = set()
        for p in glob.glob(os.path.join(SRC, "**", "*.py"), recursive=True):
            try:
                toks.update(re.findall(r"[A-Za-z_][A-Za-z0-9_]+",
                                       open(p, encoding="utf-8", errors="replace").read()))
            except Exception:
                continue
            toks.add(os.path.basename(p))
        _TOKEN_CACHE = toks
    return _TOKEN_CACHE


def _codeish(idn):
    """True if a backticked token looks like a code identifier (not a plain English word)."""
    return ("_" in idn) or ("." in idn) or ("/" in idn) or bool(re.search(r"[a-z][A-Z]", idn))


def audit_grounding(threshold=0.6):
    """Deterministic anti-fabrication guard: for each enriched note, every code-like
    `backticked` identifier must appear somewhere in the project source. A note below
    `threshold` grounded is flagged — logged loudly AND iMessaged (failures must be loud)."""
    toks = _project_tokens()
    flagged = []
    for note in (n for ag in AGENTS.values() for n in ag):
        f = os.path.join(VAULT, note + ".md")
        if not os.path.exists(f):
            continue
        ids = {i for i in re.findall(r"`([A-Za-z_][A-Za-z0-9_./]+)`",
                                     open(f, encoding="utf-8", errors="replace").read())
               if len(i) >= 4 and _codeish(i)}
        if not ids:
            continue
        ung = sorted(i for i in ids
                     if i not in toks and re.split(r"[./]", i)[0] not in toks
                     and os.path.basename(i) not in toks)
        ratio = 1 - len(ung) / len(ids)
        if ratio < threshold:
            flagged.append((note, round(ratio, 2), ung[:6]))
    if flagged:
        log(f"⚠️ GROUNDING DRIFT: {len(flagged)} note(s) < {threshold} grounded")
        for note, r, ung in flagged:
            log(f"  ⚠️ {note}: {r} grounded — ungrounded: {ung}")
        try:
            from hourly_pnl_alert import send_imessage
            send_imessage("⚠️ obsidian_enrich grounding drift: " +
                          ", ".join(f"{n}({r})" for n, r, _ in flagged))
        except Exception as e:
            log(f"  (imsg failed, logged only: {e})")
    else:
        log(f"grounding audit: all enriched notes >= {threshold} grounded ✓")
    return flagged


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="re-enrich even already-enriched notes")
    ap.add_argument("--agent", default=None, help="run one enricher only (A-E)")
    ap.add_argument("--verify", action="store_true", help="link-verify only (no LLM)")
    ap.add_argument("--sanitize", action="store_true", help="strip broken [[links]] from existing notes (no LLM)")
    ap.add_argument("--audit", action="store_true", help="grounding audit only — flag fabricated identifiers (no LLM)")
    a = ap.parse_args()
    if a.audit:
        audit_grounding(); return
    if a.sanitize:
        sanitize_existing(); verify_links(); return
    if a.verify:
        verify_links(); return
    if not llm_client.configured():
        log("ABORT: llm_client not configured for ollama (is Ollama running on :11434?)"); sys.exit(1)
    log(f"obsidian_enrich start (model={MODEL}, force={a.force}, agent={a.agent or 'all'})")
    run_enrichers(only=a.agent, force=a.force)
    sanitize_existing()   # also clean skipped notes that predate the sanitizer
    verify_links()
    audit_grounding()     # anti-fabrication guard, loud on drift


if __name__ == "__main__":
    main()
