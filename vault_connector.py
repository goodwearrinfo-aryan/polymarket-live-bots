#!/usr/bin/env python3
"""vault_connector.py — a 24/7 agent that LIVES IN the vault and makes it denser.

Reads the Second Brain concept graph and surfaces pairs of notes the GRAPH says are
related (semantically_similar_to / conceptually_related_to / analogous_to / contradicts)
but that have NO wikilink between them in the vault yet — i.e. connections you haven't
made by hand. Writes the top suggestions to `Suggested Connections.md`.

READ-ONLY to your notes (it only writes its own suggestion note — never auto-links;
adding a link is a judgment call you make). Deterministic graph analysis, no LLM.
stdlib-only + framework python = TCC-clean under launchd. Fail-soft, lockfile-guarded.
"""
import os, re, json, time, collections
from pathlib import Path

VAULT = Path.home() / "Documents/PolymarketVault"
GRAPH = VAULT / "ConceptGraph/graphify-out/graph.json"
NOTE = VAULT / "Suggested Connections.md"
LOG = Path("/tmp/vault-connector.log")
LOCK = Path("/tmp/vault-connector.lock")
# associative relations = "these belong together" (NOT structural references/implements/etc.)
ASSOC = {"semantically_similar_to", "conceptually_related_to", "analogous_to", "contradicts", "shares_data_with"}
MIN_CONF = 0.7
TOP_N = 20


def _io_retry(fn, *a, tries=5, delay=0.6, **k):
    """Retry file I/O on the macOS iCloud-sync deadlock (OSError errno 11,
    'Resource deadlock avoided') that fileproviderd raises when it holds a
    brief exclusive lock on a ~/Documents CloudDocs file mid-sync. This job
    runs at a fixed daily instant (05:00) that consistently collided with a
    sync pass, so it failed 100% of runs until this backoff was added."""
    for i in range(tries):
        try:
            return fn(*a, **k)
        except OSError as e:
            if getattr(e, "errno", None) == 11 and i < tries - 1:
                time.sleep(delay * (i + 1))
                continue
            raise


def log(m):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {m}"
    print(line)
    try: LOG.open("a").write(line + "\n")
    except Exception: pass


# ── SAFETY INVARIANT ──────────────────────────────────────────────────────────
# This agent NEVER deletes or edits your notes. It only PROPOSES links in its own
# report note (NOTE) — you add the [[link]] by hand. All vault writes go through
# _write_report; there is intentionally NO delete/unlink of any vault path (the
# only unlink is the /tmp lockfile). Do not add auto-linking or note deletion here.
def _write_report(content):
    assert NOTE.parent.resolve() == VAULT.resolve() and NOTE.suffix == ".md", \
        "connector may only write its own vault report note"
    _io_retry(NOTE.write_text, content)


def vault_md_index():
    """basename(lower) -> relpath, for resolving source_file + link targets."""
    idx = {}
    for dp, dn, fn in os.walk(VAULT):
        dn[:] = [d for d in dn if d not in {".git", ".obsidian", "graphify-out", "Graph", "ConceptGraph", "UnifiedBrain", "Archive"}]
        for f in fn:
            if f.endswith(".md"):
                idx.setdefault(f[:-3].lower(), os.path.relpath(os.path.join(dp, f), VAULT))
    return idx


def note_link_set(relpath):
    """set of lowercased basenames this note wikilinks to."""
    out = set()
    try: txt = (VAULT / relpath).read_text(encoding="utf-8", errors="ignore")
    except Exception: return out
    for m in re.findall(r"\[\[([^\]]+)\]\]", txt):
        x = m.split("\\|")[0].split("|")[0].split("#")[0].strip().rstrip("\\").strip()
        if x: out.add(x.split("/")[-1].lower())
    return out


def main():
    if LOCK.exists() and (time.time() - LOCK.stat().st_mtime) < 1800:
        log("another run in flight — skip"); return 0
    LOCK.write_text(str(os.getpid()))
    try:
        if not GRAPH.exists():
            log("concept graph missing — skip (fail-soft)"); return 0
        d = json.loads(_io_retry(GRAPH.read_text))
        nid2sf = {n["id"]: (n.get("source_file") or "") for n in d["nodes"]}
        nid2label = {n["id"]: n.get("label", n["id"]) for n in d["nodes"]}
        nid2ft = {n["id"]: (n.get("file_type") or "") for n in d["nodes"]}
        idx = vault_md_index()

        def note_of(nid):
            sf = os.path.basename(nid2sf.get(nid, "")).lower()
            if sf.endswith(".md"): sf = sf[:-3]
            return idx.get(sf)

        # NOTE-HUB filter: a mega-note (BRAIN.md hosts 78 concepts, Agents 21) makes every
        # concept inside it collapse to one note, so it "relates" to everything — not a
        # missing link. Suppress notes that host >= MEGA_HOST concepts.
        MEGA_HOST = 10
        notecnt = collections.Counter(p for p in (note_of(nid) for nid in nid2sf) if p)
        MEGA = {p for p, c in notecnt.items() if c >= MEGA_HOST}

        edges = d.get("edges", d.get("links", []))
        # HUB filter: a few super-connected nodes (BRAIN, buyflow) touch half the graph,
        # so their "related" edges are structural noise, not missing links. Suppress any
        # node whose degree is in the top ~3% (>=95th pct, floor 12).
        deg = collections.Counter()
        for e in edges:
            deg[e.get("source")] += 1; deg[e.get("target")] += 1
        degs = sorted(deg.values())
        pctl = degs[int(len(degs) * 0.95)] if degs else 0
        HUB = max(12, pctl)
        hubs = {nid for nid, dd in deg.items() if dd >= HUB}
        # DEAD-LEG filter: two graveyard/archived legs clustering together (buyflow~catmom)
        # is the dead-leg pile, not a real connection. Drop pairs where BOTH are dead legs.
        def dead_leg(relpath): return relpath.startswith("Legs Archive/") or os.path.basename(relpath) == "Strategy Graveyard.md"

        # collect best associative edge per cross-note CONCEPT pair (hub + document-hub
        # edges suppressed — connections between real ideas, not index/hub notes like
        # BRAIN/Agents/Home which relate to everything and aren't missing links).
        pair_best = {}
        sup_hub = sup_doc = 0
        for e in edges:
            if e.get("relation") not in ASSOC: continue
            if float(e.get("confidence_score", 0)) < MIN_CONF: continue
            if e.get("source") in hubs or e.get("target") in hubs:
                sup_hub += 1; continue
            if nid2ft.get(e.get("source")) == "document" or nid2ft.get(e.get("target")) == "document":
                sup_doc += 1; continue
            na, nb = note_of(e.get("source")), note_of(e.get("target"))
            if not na or not nb or na == nb: continue
            key = tuple(sorted((na, nb)))
            cand = (float(e["confidence_score"]), e["relation"],
                    nid2label.get(e["source"], ""), nid2label.get(e["target"], ""))
            if key not in pair_best or cand[0] > pair_best[key][0]:
                pair_best[key] = cand

        # keep only pairs that (a) aren't dead-leg×dead-leg and (b) don't already link
        link_cache = {}
        def links(rel):
            if rel not in link_cache: link_cache[rel] = note_link_set(rel)
            return link_cache[rel]
        suggestions = []
        sup_dead = sup_mega = 0
        for (a, b), (conf, rel, la, lb) in pair_best.items():
            if a in MEGA or b in MEGA:
                sup_mega += 1; continue
            if dead_leg(a) and dead_leg(b):
                sup_dead += 1; continue
            ba, bb = os.path.basename(a)[:-3].lower(), os.path.basename(b)[:-3].lower()
            if bb in links(a) or ba in links(b): continue  # already linked
            suggestions.append((conf, rel, a, b, la, lb))
        suggestions.sort(key=lambda x: -x[0])
        top = suggestions[:TOP_N]

        L = ["---", "type: freshness", "status: " + ("suggestions" if top else "none"),
             f"date: {time.strftime('%Y-%m-%d')}", "---", "",
             "# Suggested Connections", "",
             f"_The Vault Connector (`com.aryan.vault-connector`, daily) · {time.strftime('%Y-%m-%d %H:%M')}_", "",
             "> Pairs the [[Second Brain]] graph says are related (semantically/conceptually) but that",
             "> your notes don't wikilink yet. Read-only — add a `[[link]]` if the connection is real;",
             "> ignore if it's a coincidence. The graph proposes, you decide.", "",
             f"**{len(suggestions)} concept-to-concept signal pairs · top {len(top)} below**  "
             f"_(suppressed {sup_hub} hub-node + {sup_doc} index-note + {sup_mega} mega-note + {sup_dead} dead-leg noise)_", ""]
        if top:
            L.append("| relatedness | note A | note B | why |")
            L.append("|---|---|---|---|")
            for conf, rel, a, b, la, lb in top:
                na, nb = os.path.basename(a)[:-3], os.path.basename(b)[:-3]
                L.append(f"| {conf:.2f} | [[{na}]] | [[{nb}]] | {rel.replace('_',' ')} |")
        else:
            L.append("_No unlinked related pairs above threshold — the vault's well-connected._")
        L += ["", "## Related", "[[Second Brain]] · [[Vault Health]] · [[Home]]"]
        _write_report("\n".join(L))
        log(f"OK signal={len(suggestions)} shown={len(top)} | HUB>={HUB} sup_hub={sup_hub} sup_doc={sup_doc} sup_mega={sup_mega} sup_dead={sup_dead}")
        return 0
    finally:
        try: LOCK.unlink()
        except Exception: pass


if __name__ == "__main__":
    import sys
    try: sys.exit(main())
    except Exception as e:
        log(f"FATAL {type(e).__name__}: {e}"); sys.exit(1)
