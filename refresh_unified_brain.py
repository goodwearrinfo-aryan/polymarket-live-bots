#!/usr/bin/env python3
"""Rebuild the unified second-brain graph from the two source graphs.

DETERMINISTIC, keyless, stdlib-only (no graphify import) so it runs TCC-clean
under launchd via the framework python. Re-runs are idempotent: it rebuilds
UnifiedBrain/graph.json from scratch from the current bot + concept graphs each
time, so it always reflects the latest auto-rebuilt bot code graph.

  bot graph (auto-fresh on commit)  ┐
                                    ├─ namespace ─ union ─ bridge ─> UnifiedBrain/graph.json
  concept graph (re-extracted wkly) ┘

Bridges: concept node <-- implements -- code node, matched by name
(concept stem/label token == code symbol/file stem), conservative, INFERRED 0.8.
"""
import json, re, sys, time
from pathlib import Path

HOME = Path.home()
BOT      = HOME / "Documents/polymarket/graphify-out/graph.json"
CONCEPT  = HOME / "Documents/PolymarketVault/ConceptGraph/graphify-out/graph.json"
OUT      = HOME / "Documents/PolymarketVault/UnifiedBrain/graphify-out/graph.json"
STAMP    = HOME / "Documents/PolymarketVault/UnifiedBrain/graphify-out/.last_refresh"
LOG      = Path("/tmp/unified-brain-merge.log")

# (tag, path) — tag becomes the id namespace, matching `graphify merge-graphs`
SOURCES = [("polymarket", BOT), ("ConceptGraph", CONCEPT)]

STOP = {"the","gate","edge","map","leg","legs","bot","arb","data","news","agent",
        "agents","track","model","test","paper","live","home","hot","core","base",
        "index","hub","lab","flow","gap","book","fade","mid","run","job","jobs",
        "venue","venues","market","markets"}


def _io_retry(fn, *a, tries=5, delay=0.6, **k):
    """Retry file I/O on the macOS iCloud-sync deadlock (OSError errno 11,
    'Resource deadlock avoided') that fileproviderd raises when it holds a
    brief exclusive lock on a ~/Documents CloudDocs file mid-sync. This job
    runs at a fixed daily instant (04:10) that consistently collided with a
    sync pass, so it failed 100% of runs until this backoff was added."""
    for i in range(tries):
        try:
            return fn(*a, **k)
        except OSError as e:
            if getattr(e, "errno", None) == 11 and i < tries - 1:
                time.sleep(delay * (i + 1))
                continue
            raise


def log(msg):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line)
    try:
        with LOG.open("a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def norm(s):
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def stem(nid):
    last = str(nid).split("::")[-1]
    return re.sub(r"\(\)$", "", last)


def load_graph(path):
    d = json.loads(_io_retry(Path(path).read_text))
    nodes = d.get("nodes", [])
    edges = d.get("edges", d.get("links", []))
    return nodes, edges


def main():
    all_nodes, all_edges = [], []
    seen = set()
    per = {}
    for tag, path in SOURCES:
        if not Path(path).exists():
            log(f"WARN source missing: {path}")
            continue
        nodes, edges = load_graph(path)
        per[tag] = (len(nodes), len(edges))
        for n in nodes:
            nid = f"{tag}::{n['id']}"
            if nid in seen:
                continue
            seen.add(nid)
            m = dict(n)
            m["id"] = nid
            m["namespace"] = tag
            all_nodes.append(m)
        for e in edges:
            if "source" not in e or "target" not in e:
                continue
            me = dict(e)
            me["source"] = f"{tag}::{e['source']}"
            me["target"] = f"{tag}::{e['target']}"
            all_edges.append(me)

    # ---- bridge pass: concept node <- implements - code node, by name ----
    code = [n for n in all_nodes if n["id"].startswith("polymarket::")]
    concept = [n for n in all_nodes if n["id"].startswith("ConceptGraph::")]
    code_by_norm = {}
    for n in code:
        s = norm(stem(n["id"]))
        code_by_norm.setdefault(s, []).append(n["id"])
        lab = norm(n.get("label", ""))
        if lab and lab != s:
            code_by_norm.setdefault(lab, []).append(n["id"])

    existing = {(e["source"], e["target"]) for e in all_edges}
    bridges = 0
    matched = 0
    for c in concept:
        keys = set()
        cs = norm(stem(c["id"]))
        if len(cs) >= 5 and cs not in STOP:
            keys.add(cs)
        for w in re.split(r"[^a-zA-Z0-9]+", c.get("label", "")):
            wn = norm(w)
            if len(wn) >= 5 and wn not in STOP:
                keys.add(wn)
        hits = []
        for k in keys:
            hits.extend(code_by_norm.get(k, []))
        hits = list(dict.fromkeys(hits))[:3]
        if hits:
            matched += 1
        for codeid in hits:
            pair = (codeid, c["id"])
            if pair in existing:
                continue
            existing.add(pair)
            all_edges.append({
                "source": codeid, "target": c["id"], "relation": "implements",
                "confidence": "INFERRED", "confidence_score": 0.8,
                "source_file": "cross-link", "weight": 1.0,
            })
            bridges += 1

    OUT.parent.mkdir(parents=True, exist_ok=True)
    _io_retry(OUT.write_text, json.dumps({"nodes": all_nodes, "edges": all_edges},
                                         ensure_ascii=False))
    _io_retry(STAMP.write_text, str(int(time.time())))
    srcdesc = " ".join(f"{t}={per.get(t,('?','?'))[0]}n" for t, _ in SOURCES)
    log(f"OK unified: {len(all_nodes)} nodes, {len(all_edges)} edges "
        f"({bridges} bridges, {matched}/{len(concept)} concepts linked) | src {srcdesc} -> {OUT}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        log(f"ERROR {type(e).__name__}: {e}")
        sys.exit(1)
