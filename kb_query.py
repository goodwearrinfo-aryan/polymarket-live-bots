#!/usr/bin/env python3
"""
kb_query.py — unified knowledge base over the Polymarket research stack (2026-06-13).

Token-free retrieval across every knowledge source:
  • graphify-out/graph.json  — code + leg + decision knowledge graph (1170 nodes)
  • BRAIN.md, memory/glossary.md — canonical lessons, kill reasons, terms
  • info_impact.db           — 24k+ GDELT news→market matches (the impact memory)
  • hermes live news (:48580) — last-6h headlines (optional, if server up)

Scores passages by query-term overlap (TF weighted, stdlib only) and prints the
top cited hits. Read the citations yourself, or pipe to Claude when you want
reasoning — retrieval costs zero tokens.

  python3 kb_query.py "why was truefade killed"
  python3 kb_query.py "what news moved bitcoin markets"      # hits impact memory
  python3 kb_query.py "what feeds nearres" --k 8
  python3 kb_query.py --impact "spacex"   # top market moves after entity news
"""
import json, re, sqlite3, sys, urllib.request
from pathlib import Path
from collections import Counter

BASE = Path(__file__).resolve().parent
STOP = set("the a an of to in on for and or is are was were will be at by with from "
           "as it its this that has have had not no new says said what when where why "
           "how did do does can could would should which who".split())


def toks(s):
    return [w for w in re.findall(r"[a-z0-9']+", str(s).lower()) if len(w) > 2 and w not in STOP]


def score(qt, text):
    if not text:
        return 0.0
    tc = Counter(toks(text))
    return sum(tc[w] for w in qt)


def load_passages():
    """Yield (source, title, text) from every static knowledge source."""
    P = []
    # graph nodes (labels + rationale attrs)
    gj = BASE / "graphify-out" / "graph.json"
    if gj.exists():
        g = json.loads(gj.read_text())
        for n in g.get("nodes", []):
            txt = " ".join(str(n.get(k, "")) for k in ("label", "rationale", "id"))
            sf = n.get("source_file", "graph")
            P.append(("graph", n.get("label", n.get("id", "")), txt + " [" + str(sf) + "]"))
    # markdown docs split into paragraphs
    for md in ("BRAIN.md", "memory/glossary.md"):
        f = BASE / md
        if f.exists():
            for para in re.split(r"\n\s*\n", f.read_text()):
                para = para.strip()
                if len(para) > 40:
                    head = para.splitlines()[0][:70]
                    P.append((md, head, para))
    return P


def search(query, k=6):
    qt = toks(query)
    scored = []
    for src, title, text in load_passages():
        s = score(qt, text)
        if s > 0:
            scored.append((s, src, title, text))
    scored.sort(key=lambda x: -x[0])
    print(f"\n🔎 KB: top {k} for '{query}'\n" + "=" * 60)
    for s, src, title, text in scored[:k]:
        snippet = re.sub(r"\s+", " ", text)[:280]
        print(f"\n[{src}] {title}  (score {s})")
        print(f"  {snippet}")
    # impact-memory angle: any entity in the query that appears in matches
    impact_hits(qt, query)


def impact_hits(qt, query):
    db = BASE / "info_impact.db"
    if not db.exists():
        return
    c = sqlite3.connect(db)
    like = f"%{query.split()[0]}%" if query.split() else "%"
    rows = c.execute("""SELECT entity, question, yes_at_match,
                               COALESCE(yes_24h,yes_1h) yf, tone, COUNT(*) n
                        FROM matches WHERE (entity LIKE ? OR question LIKE ?)
                          AND COALESCE(yes_24h,yes_1h) IS NOT NULL
                        GROUP BY market_id
                        ORDER BY ABS(COALESCE(yes_24h,yes_1h)-yes_at_match) DESC
                        LIMIT 5""", (like, like)).fetchall()
    if rows:
        print(f"\n📰 impact memory — biggest 24h moves matching '{query.split()[0] if query.split() else ''}':")
        for e, q, y0, y24, tone, n in rows:
            mv = (y24 - y0) if (y24 is not None and y0 is not None) else 0
            print(f"  {mv:+.2f} 24h  tone {tone:+.1f}  [{e}] {str(q)[:55]} (n={n})")


def impact_only(entity):
    db = BASE / "info_impact.db"
    c = sqlite3.connect(db)
    # prefer 24h follow-up; fall back to 1h (24h fills ~24h after GDELT job start)
    rows = c.execute("""SELECT question, yes_at_match,
                               COALESCE(yes_24h, yes_1h) yf,
                               (yes_24h IS NOT NULL) has24, tone FROM matches
                        WHERE entity LIKE ? AND COALESCE(yes_24h, yes_1h) IS NOT NULL
                        ORDER BY ABS(COALESCE(yes_24h,yes_1h)-yes_at_match) DESC LIMIT 12""",
                     (f"%{entity}%",)).fetchall()
    if not rows:
        print(f"\n📰 impact memory for '{entity}': no filled follow-ups yet")
        return
    win = "24h" if rows[0][3] else "1h"
    print(f"\n📰 impact memory for '{entity}' — biggest {win} moves after news:")
    for q, y0, yf, has24, tone in rows:
        w = "24h" if has24 else "1h "
        print(f"  {yf-y0:+.2f} {w}  tone {tone:+.1f}  {str(q)[:58]}")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:]]
    if "--impact" in args:
        args.remove("--impact")
        impact_only(" ".join(args))
    elif not args:
        print(__doc__)
    else:
        k = 6
        if "--k" in args:
            i = args.index("--k"); k = int(args[i + 1]); del args[i:i + 2]
        search(" ".join(args), k)
