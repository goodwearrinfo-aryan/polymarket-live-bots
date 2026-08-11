#!/usr/bin/env python3
"""vault_weaver.py — a 24/7 agent that WIRES the vault's loose ends (orphan notes + dead links).

The complement to vault_connector.py (which only SUGGESTS links, read-only) and wiring_keeper.py
(which only manages the Connectors live-map graph). This one actively connects ORPHAN notes —
notes nothing links to — into their natural hub, so the Obsidian graph has no floating islands.

DISCIPLINE (heal-not-kill, additive-only):
  * It NEVER edits a note's body, NEVER deletes, NEVER rewrites a link. The ONLY write is appending
    a wikilink to a HUB note inside a clearly-delimited, regenerated managed block
    (<!-- vault-weaver --> markers), so every run is idempotent and self-correcting.
  * It refuses to touch folders OWNED by other agents (Connectors/, Live/, memory/, ConceptGraph/,
    Graph/, graphify-out/ — maintained by wiring_keeper / activity_light / the concept jobs).
  * Links that exist only inside a weaver-managed block do NOT count as "real inbound" — so an
    orphan wired by the weaver stays wired (no flip-flop), and when a real human link appears the
    note drops out of the managed block automatically.
  * Truly dead links + un-homeable orphans are REPORTED in `Vault Loose Ends.md` for a human — never
    auto-removed (the _COMMUNITY_* graphify artifacts are filtered out as expected noise, not loose ends).

stdlib-only + framework python = TCC-clean under launchd. Fail-soft, lockfile-guarded, /tmp logs.
"""
import os, re, json, time, datetime, collections
from pathlib import Path

VAULT = Path.home() / "Documents/PolymarketVault"
REPORT = VAULT / "Vault Loose Ends.md"
LOG  = Path("/tmp/vault-weaver.log")
LOCK = Path("/tmp/vault-weaver.lock")

# Folders OWNED by other autonomous agents or that are legitimately top-level — never wire/skip as orphans.
SKIP_DIRS = {"Connectors", "Live", "memory", "ConceptGraph", "Graph", "graphify-out",
             "Templates", "_inbox", "copilot", ".trash", ".obsidian", "Archive"}

# Orphan folder -> hub note basename (hub is created if missing). World Data is owned by world_data_hunter.
FOLDER_HUB = {
    "Reports":          "Reports",
    "Reports/Markets":  "Reports",
    "Agents":           "Agent Fleet",
    "Keyed":            "Keyed Sources",
    "Edges":            "Edge Map",
    "Analyst":          "Analyst Market Calls",
    "Stocks":           "Stocks",
    "Bot":              "Bot",
    "Dashboards":       "Dashboards",
    "Journal":          "Journal",
}
# Hub auto-create metadata: hub basename -> (emoji title, frontmatter type)
HUB_META = {
    "Reports":               ("📄 Reports", "report-hub"),
    "Keyed Sources":         ("🔑 Keyed Sources", "data-source-hub"),
    "Stocks":                ("📈 Stocks", "hub"),
    "Journal":               ("📓 Journal", "hub"),
}
START = "<!-- vault-weaver:start -->"
END   = "<!-- vault-weaver:end -->"
LINKRE = re.compile(r"\[\[([^\]]+?)\]\]")


def log(msg):
    line = f"{datetime.datetime.now().isoformat(timespec='seconds')} {msg}"
    try: LOG.open("a").write(line + "\n")
    except Exception: pass
    print(line)


def strip_managed(text):
    """Remove the vault-weaver managed block AND code spans (Obsidian doesn't render [[links]] inside
    backticks, so they're never real links/dead-links) before counting links."""
    text = re.sub(re.escape(START) + r".*?" + re.escape(END), "", text, flags=re.S)
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    text = re.sub(r"~~~.*?~~~", "", text, flags=re.S)
    return re.sub(r"`[^`\n]*`", "", text)


def norm_target(raw):
    t = raw.split("|")[0].split("#")[0].split("^")[0].strip()
    if not t:
        return None
    return os.path.basename(t).lower()


def looks_like_real_title(t):
    """Filter dead-link targets down to plausible intended note titles (drop junk/placeholders)."""
    if t.startswith("_COMMUNITY_"):
        return False
    if any(c in t for c in '`"\n<>'):
        return False
    if not (2 <= len(t) <= 50):
        return False
    if t.startswith("."):              # .env config etc.
        return False
    if t in {"Folder/Note", "<slug>", "BTC", "ETH", "x", "y"}:
        return False
    return bool(re.match(r"^[\w ,.&/'\-()]+$", t))


def main():
    if LOCK.exists() and (time.time() - LOCK.stat().st_mtime) < 1800:
        log("another run in progress; exit"); return
    LOCK.touch()
    try:
        # follow symlinks: vault/memory is a symlink to the auto-memory store, so a plain rglob would
        # falsely flag every [[memory/*]] link as dead. (cycle-guarded by visited real paths.)
        md, _seen = [], set()
        for _root, _dirs, _files in os.walk(VAULT, followlinks=True):
            _rp = os.path.realpath(_root)
            if _rp in _seen:
                _dirs[:] = []; continue
            _seen.add(_rp)
            md += [Path(_root) / _fn for _fn in _files if _fn.endswith(".md")]
        titles = {}                      # basename.lower() -> Path (first wins)
        for f in md:
            titles.setdefault(f.stem.lower(), f)

        inbound = collections.Counter()
        dead = collections.Counter()
        bodies = {}
        for f in md:
            try:
                raw = f.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            bodies[f] = raw
            for m in LINKRE.finditer(strip_managed(raw)):   # ignore our own managed links
                tgt = norm_target(m.group(1))
                if tgt is None:
                    continue
                if tgt in titles:
                    inbound[tgt] += 1
                else:
                    dead[m.group(1).split("|")[0].split("#")[0].strip()] += 1

        def in_skip(f):
            rel = f.relative_to(VAULT)
            return rel.parts and rel.parts[0] in SKIP_DIRS

        def is_daily(b):
            return bool(re.match(r"^\d{4}-\d{2}-\d{2}", b))

        # collect orphans (0 real inbound), grouped by their folder->hub mapping
        wired = collections.defaultdict(list)   # hub basename -> [(path, name)]
        unhomed = []
        for f in md:
            if in_skip(f) or is_daily(f.stem):
                continue
            if inbound[f.stem.lower()] > 0:
                continue
            rel = f.relative_to(VAULT)
            folder = str(rel.parent) if str(rel.parent) != "." else "(root)"
            # frontmatter type that is itself a hub/home/index -> legitimately top-level, skip
            fm = bodies.get(f, "")[:400]
            if re.search(r"type:\s*\S*(hub|home|index)", fm) or f.stem in ("Home", "Obsidian Brain", "God Node", "BRAIN"):
                continue
            if folder.startswith("World Data"):     # owned by world_data_hunter
                continue
            hub = FOLDER_HUB.get(folder)
            if hub:
                wired[hub].append((str(rel).replace(".md", ""), f.stem))
            else:
                unhomed.append(str(rel))

        # --- wire orphans into their hubs (additive, idempotent via managed block) ---
        wired_count = 0
        for hub, items in wired.items():
            hub_path = VAULT / f"{hub}.md"
            if not hub_path.exists():
                emoji, ftype = HUB_META.get(hub, (f"🔗 {hub}", "hub"))
                hub_path.write_text(
                    f"---\ntype: {ftype}\ntags: [hub]\n---\n\n# {emoji}\n\n"
                    f"Hub note (auto-created by vault-weaver to home loose ends).\n\n"
                    f"*↑ [[Obsidian Brain]] · [[Agent Fleet]]*\n", encoding="utf-8")
                log(f"created hub {hub}.md")
            text = hub_path.read_text(encoding="utf-8", errors="ignore")
            items = sorted(set(items), key=lambda x: x[1].lower())
            block_lines = [START,
                           f"## 🔗 Auto-wired loose ends ({len(items)}) · _vault-weaver, {datetime.date.today()}_",
                           ""]
            block_lines += [f"- [[{p}|{n}]]" for p, n in items]
            block_lines += ["", END]
            block = "\n".join(block_lines)
            if START in text and END in text:
                text = re.sub(re.escape(START) + r".*?" + re.escape(END), block, text, flags=re.S)
            else:
                text = text.rstrip() + "\n\n" + block + "\n"
            hub_path.write_text(text, encoding="utf-8")
            wired_count += len(items)

        # --- report: un-homeable orphans + real dead links (human review; never auto-removed) ---
        real_dead = sorted((t for t in dead if looks_like_real_title(t)),
                           key=lambda t: -dead[t])
        community = sum(v for k, v in dead.items() if k.startswith("_COMMUNITY_"))
        today = datetime.date.today().isoformat()
        lines = [
            "---", "type: report", "status: open", f"date: {today}",
            "tags: [vault-health, loose-ends]", "---", "",
            f"# 🧹 Vault Loose Ends — {today}", "",
            f"Maintained by **vault-weaver** (additive-only healer). Orphans with a known home are "
            f"auto-wired into their hub; the rest are listed here for a human call. Heal-not-kill: "
            f"nothing below is auto-deleted.", "",
            "## Summary", "",
            f"- **{wired_count}** orphans auto-wired into hubs this run "
            f"({', '.join(f'{h} {len(v)}' for h, v in sorted(wired.items()))})",
            f"- **{len(unhomed)}** orphans with no obvious home (below)",
            f"- **{len(real_dead)}** real dead links (below) · _{community} `_COMMUNITY_*` graphify "
            f"artifacts filtered out as expected noise_", "",
            "## Orphans with no home (need a human link or archive)", "",
        ]
        lines += [f"- [[{o.replace('.md','')}]]" for o in sorted(unhomed)] or ["_none_"]
        lines += ["", "## Real dead links (target note missing)", ""]
        # list targets as plain code, NOT live [[wikilinks]] — else this report itself becomes a source
        # of dead links in the graph (it's reporting them, not linking to them).
        lines += [f"- `{t}` ×{dead[t]}" for t in real_dead[:120]] or ["_none_"]
        lines += ["", "*↑ [[Obsidian Brain]] · [[Agent Fleet]]*", ""]
        REPORT.write_text("\n".join(lines), encoding="utf-8")

        log(f"OK wired={wired_count} hubs={len(wired)} unhomed={len(unhomed)} "
            f"real_dead={len(real_dead)} community_filtered={community}")
    except Exception as e:
        log(f"ERR {type(e).__name__}: {e}")
    finally:
        try: LOCK.unlink()
        except Exception: pass


if __name__ == "__main__":
    main()
