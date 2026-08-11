#!/usr/bin/env python3
"""brain_to_global.py — propagate the ConceptGraph into the GLOBAL brain that the `brain`
wrapper reads (~/.graphify/global-graph.json). Namespaces ConceptGraph nodes/edges as
`vault-concepts::<id>`, REPLACES the vault-concepts portion of global, and PRESERVES any
other-namespace nodes (findings, etc.). Backs up the prior global. This is the step that makes
the rebuild actually reach `brain` — without it the wrapper keeps reading a stale graph.

Usage: brain_to_global.py <conceptgraph.json> [<global.json>]
"""
import json, sys, os, shutil

GLOBAL = os.path.expanduser("~/.graphify/global-graph.json")
TAG = "vault-concepts"


def main(concept_p, global_p=GLOBAL):
    concept = json.load(open(concept_p))
    g = json.load(open(global_p))

    keep_nodes = [n for n in g["nodes"] if not str(n.get("id", "")).startswith(TAG + "::")]
    keep_ids = {n["id"] for n in keep_nodes}
    glinks = g.get("links", g.get("edges", []))
    keep_links = [l for l in glinks if l.get("source") in keep_ids and l.get("target") in keep_ids]

    for n in concept["nodes"]:
        nn = dict(n); nn["id"] = f"{TAG}::{n['id']}"; nn["repo"] = TAG
        keep_nodes.append(nn)
    for l in concept.get("links", concept.get("edges", [])):
        ll = dict(l); ll["source"] = f"{TAG}::{l['source']}"; ll["target"] = f"{TAG}::{l['target']}"
        keep_links.append(ll)

    allids = {n["id"] for n in keep_nodes}
    keep_links = [l for l in keep_links if l.get("source") in allids and l.get("target") in allids]  # prune dangling
    g["nodes"] = keep_nodes
    g["links" if "links" in g else "edges"] = keep_links
    if global_p == GLOBAL:
        shutil.copy2(global_p, global_p + ".pre-nv.bak")
    json.dump(g, open(global_p, "w"))
    vc = sum(1 for n in keep_nodes if str(n["id"]).startswith(TAG + "::"))
    print(f"global -> {len(keep_nodes)} nodes ({TAG}={vc}, other={len(keep_nodes)-vc}), {len(keep_links)} links")


if __name__ == "__main__":
    main(*sys.argv[1:])
