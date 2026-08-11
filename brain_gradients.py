#!/usr/bin/env python3
"""brain_gradients.py — BrainSpace macroscale-gradient analysis of the concept BRAIN.

Borrows the neuroimaging trick (diffusion-map embedding of a connectivity matrix) and applies
it to the graphify concept graph: it finds the PRINCIPAL AXES along which the brain's concepts
are organized — the same way BrainSpace finds cortical gradients. Writes to Obsidian:
  • Brain Gradients.md       — each gradient axis + the concepts anchoring each extreme (insight)
  • Brain Gradient Map.canvas — concepts laid out in 2D gradient space, coloured by community
$0, NO LLM — pure linear algebra (BrainSpace / sklearn) over the existing graph.

Usage: python3 brain_gradients.py
"""
import json, os, numpy as np
from pathlib import Path
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import connected_components
from brainspace.gradient import GradientMaps

VAULT = Path.home() / "Documents/PolymarketVault"
GRAPH = VAULT / "ConceptGraph/graphify-out/graph.json"
NOTE = VAULT / "Brain Gradients.md"
CANVAS = VAULT / "Brain Gradient Map.canvas"
NCOMP, TOPK = 3, 8
PAL = ["1", "2", "3", "4", "5", "6"]


def main():
    g = json.load(open(GRAPH))
    nodes, links = g["nodes"], g.get("links", g.get("edges", []))
    idx = {n["id"]: i for i, n in enumerate(nodes)}
    labels = [n.get("label", n["id"]) for n in nodes]
    comm = [n.get("community", -1) for n in nodes]
    N = len(nodes)
    A = np.zeros((N, N))
    for l in links:
        s, t = idx.get(l.get("source")), idx.get(l.get("target"))
        if s is not None and t is not None and s != t:
            A[s, t] += 1.0; A[t, s] += 1.0

    # diffusion maps need ONE connected component — take the largest
    ncc, lab = connected_components(csr_matrix(A > 0), directed=False)
    biggest = np.bincount(lab).argmax()
    keep = np.where(lab == biggest)[0]
    A2 = A[np.ix_(keep, keep)]
    klabels = [labels[i] for i in keep]
    kcomm = [comm[i] for i in keep]

    gm = GradientMaps(n_components=NCOMP, approach="dm", kernel="normalized_angle", random_state=0)
    gm.fit(A2, sparsity=0)
    grad, lam = gm.gradients_, gm.lambdas_

    # --- insight note: each gradient axis + the concepts at each pole ---
    out = ["---", "tags: [brain, analysis, running]", "---", "",
           "# 🧭 Brain Gradients",
           f"Principal organizational axes of the concept brain ({len(keep)} connected concepts), "
           f"via BrainSpace diffusion-map embedding (the neuroimaging-gradient method applied to the "
           f"knowledge graph). Concepts close on an axis are organized similarly; the poles are what "
           f"that axis *separates*.", "",
           "Read by → [[🧠 Shared Brain]] · → [[Brain Gradient Map]]", ""]
    for c in range(NCOMP):
        order = np.argsort(grad[:, c])
        lo = " · ".join(klabels[i] for i in order[:TOPK])
        hi = " · ".join(klabels[i] for i in order[-TOPK:][::-1])
        out += [f"## Gradient {c+1} — variance {lam[c]:.3f}",
                f"**Pole A:** {lo}", "", f"**Pole B:** {hi}", ""]
    NOTE.write_text("\n".join(out))

    # --- canvas: 2D scatter by (G1, G2), coloured by community ---
    def scale(v, span=5200):
        v = (v - v.min()) / (v.max() - v.min() + 1e-9)
        return (v - 0.5) * span
    X, Y = scale(grad[:, 0]), scale(grad[:, 1])
    cnodes = [{"id": f"n{i}", "type": "text", "text": f"{klabels[i]}",
               "x": int(X[i]), "y": int(Y[i]), "width": 190, "height": 46,
               "color": PAL[kcomm[i] % len(PAL)] if kcomm[i] >= 0 else "0"}
              for i in range(len(keep))]
    CANVAS.write_text(json.dumps({"nodes": cnodes, "edges": []}))
    print(f"gradients: {len(keep)} connected concepts, {NCOMP} axes (variance {np.round(lam, 3)})")
    print(f"wrote: {NOTE.name} + {CANVAS.name}")


if __name__ == "__main__":
    main()
