#!/usr/bin/env python3
"""brain_union.py — MONOTONIC union-merge: fold a fresh extraction INTO a base graph by
normalized concept label, so the brain NEVER loses concepts — only adds new ones and keeps
the rich base. Reconciles formats (Maverick `edges` -> opus node-link `links`) and preserves
the base graph's structure/flags. This is what makes the nightly free rebuild "max": a thin
Maverick run can only ENRICH the opus base, never downgrade it.

Usage: brain_union.py <base.json> <fresh.json> <out.json>
"""
import json, sys, re


def norm(label):
    return re.sub(r"[^a-z0-9]+", "", (label or "").lower())


def main(base_p, fresh_p, out_p):
    base = json.load(open(base_p))
    fresh = json.load(open(fresh_p))
    base_nodes = base.get("nodes", [])
    base_links = base.get("links", base.get("edges", []))

    seen = {}  # norm_label -> base id
    for n in base_nodes:
        seen[n.get("norm_label") or norm(n.get("label"))] = n.get("id")

    id_map, added_n = {}, 0
    for n in fresh.get("nodes", []):
        key = norm(n.get("label"))
        if key in seen:
            id_map[n["id"]] = seen[key]            # already in base -> map to base id
        else:
            base_nodes.append({                    # NEW concept -> add in base shape
                "label": n.get("label"), "file_type": n.get("file_type", "document"),
                "source_file": n.get("source_file"), "id": n["id"],
                "norm_label": key, "community": -1, "via": "nv-rebuild"})
            seen[key] = n["id"]; id_map[n["id"]] = n["id"]; added_n += 1

    have = {(l.get("source"), l.get("target"), l.get("relation")) for l in base_links}
    added_e = 0
    for e in fresh.get("edges", fresh.get("links", [])):
        s = id_map.get(e.get("source"), e.get("source"))
        t = id_map.get(e.get("target"), e.get("target"))
        k = (s, t, e.get("relation"))
        if s and t and k not in have:
            base_links.append({**e, "source": s, "target": t})
            have.add(k); added_e += 1

    base["nodes"] = base_nodes
    base["links" if "links" in base else "edges"] = base_links
    json.dump(base, open(out_p, "w"))
    print(f"union -> {len(base_nodes)} nodes (+{added_n} new), {len(base_links)} edges (+{added_e} new)")


if __name__ == "__main__":
    main(*sys.argv[1:4])
