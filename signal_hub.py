#!/usr/bin/env python3
"""signal_hub.py — Obsidian Signal Hub: cross-probe convergence mapper.

Reads all 4 probe caches, normalises market names into consistent [[wikilinks]],
finds markets appearing in 2+ probes (stronger signal), and writes:

  PolymarketVault/Reports/Signal Hub.md   — hub note with cross-probe links
  PolymarketVault/Reports/Markets/<slug>.md  — one note per market mentioned
     (so Obsidian graph view shows the probe files linking to shared market nodes)

Run every 3h via watchdog (after probes have had a chance to update their caches).
Usage: python3 signal_hub.py
"""

import json, os, re, sys, time

HERE  = os.path.dirname(os.path.abspath(__file__))
VAULT = os.path.expanduser("~/Documents/PolymarketVault/Reports")
sys.path.insert(0, HERE)

CACHES = {
    "consensus":  os.path.join(HERE, "consensus_cache.json"),
    "sentiment":  os.path.join(HERE, "sentiment_cache.json"),
    "trending":   os.path.join(HERE, "trending_cache.json"),
    "orderflow":  os.path.join(HERE, "orderflow_cache.json"),
    "macro":      os.path.join(HERE, "macro_cache.json"),
    "tech":       os.path.join(HERE, "tech_cache.json"),
}

PROBE_NOTES = {
    "consensus":  "consensus_probe",
    "sentiment":  "sentiment_probe",
    "trending":   "trending_probe",
    "orderflow":  "orderflow_probe",
    "macro":      "macro_probe",
    "tech":       "tech_probe",
}


def _kb_context(question, k=3):
    """Query the graphify knowledge graph for context on this market question."""
    try:
        import kb_query
        qt = kb_query.toks(question)
        scored = []
        for src, title, text in kb_query.load_passages():
            s = kb_query.score(qt, text)
            if s > 0:
                scored.append((s, src, title, text))
        scored.sort(key=lambda x: -x[0])
        lines = []
        for s, src, title, text in scored[:k]:
            snippet = re.sub(r"\s+", " ", text)[:120]
            lines.append(f"- **{title[:50]}** ({src}, score={s}): {snippet}")
        return lines
    except Exception:
        return []

STALE_SEC = 8 * 3600   # cache older than 8h = stale


def _slug(question):
    """Short, filename-safe slug for Obsidian note title."""
    q = question.strip()
    q = re.sub(r"[^\w\s'-]", "", q)
    q = re.sub(r"\s+", " ", q)
    return q[:55].rstrip()


def _load(name, path):
    if not os.path.exists(path):
        return None, "missing"
    age = time.time() - os.path.getmtime(path)
    if age > STALE_SEC:
        return None, f"stale ({int(age/3600)}h old)"
    try:
        return json.load(open(path)), None
    except Exception as e:
        return None, str(e)


def _ts(data):
    return time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(data.get("ts", 0)))


# ── Extract market records from each cache ───────────────────────────────────

def markets_from_consensus(data):
    """Returns list of {question, yes, signal_type, detail, probe}."""
    out = []
    for d in data.get("divergences", []):
        out.append({"question": d["question"], "yes": d.get("poly_yes", 0),
                    "signal_type": "consensus_divergence",
                    "detail": f"Poly {d['poly_yes']:.0%} vs Manifold {d['manifold_yes']:.0%} (gap {d['gap']:+.0%})",
                    "probe": "consensus"})
    for a in data.get("attentions", []):
        out.append({"question": a["question"], "yes": a.get("poly_yes", 0),
                    "signal_type": "wiki_spike",
                    "detail": f"Wikipedia entity **{a['entity']}** spiked {a['ratio']}×",
                    "probe": "consensus"})
    return out


def markets_from_sentiment(data):
    out = []
    for m in data.get("markets", []):
        ns = m.get("news", {})
        if abs(ns.get("score", 0)) < 0.1 and ns.get("fresh_count", 0) < 3:
            continue
        direction = "bullish" if ns.get("score", 0) > 0.1 else "bearish"
        out.append({"question": m["question"], "yes": m.get("yes", 0),
                    "signal_type": f"sentiment_{direction}",
                    "detail": f"score {ns.get('score',0):+.2f}, {ns.get('fresh_count',0)} headlines, category={ns.get('category','?')}",
                    "probe": "sentiment"})
    return out


def markets_from_trending(data):
    out = []
    for m in data.get("coin_hits", []):
        ct = m.get("trending", {})
        out.append({"question": m["question"], "yes": m.get("yes", 0),
                    "signal_type": "coingecko_trending",
                    "detail": f"coin **{ct.get('coin')}** trending rank #{ct.get('rank','?')} on CoinGecko",
                    "probe": "trending"})
    for m in data.get("hn_hits", []):
        for hm in m.get("hn_matches", [])[:1]:
            out.append({"question": m["question"], "yes": m.get("yes", 0),
                        "signal_type": "hn_frontpage",
                        "detail": f"HN: \"{hm['title'][:60]}\"",
                        "probe": "trending"})
    return out


def markets_from_orderflow(data):
    out = []
    for m in data.get("spikes", []):
        direction = "buyer" if m.get("delta", 0) > 0.1 else ("seller" if m.get("delta", 0) < -0.1 else "mixed")
        out.append({"question": m["question"], "yes": m.get("yes", 0),
                    "signal_type": "velocity_spike",
                    "detail": f"{m['velocity']}× velocity, {direction}-driven (delta {m['delta']:+.2f}, {m['buy_pct']:.0f}% buyers)",
                    "probe": "orderflow"})
    return out


def markets_from_macro(data):
    out = []
    fg = data.get("fg", {})
    fg_val = fg.get("value", 50)
    for s in data.get("signals", []):
        out.append({"question": s["question"], "yes": s.get("yes", 0),
                    "signal_type": s["signal_type"],
                    "detail": s["detail"] + f" (F&G={fg_val} {fg.get('classification','')})",
                    "probe": "macro"})
    return out


def markets_from_tech(data):
    out = []
    for m in data.get("github_hits", []):
        out.append({"question": m["question"], "yes": m.get("yes", 0),
                    "signal_type": "github_trending",
                    "detail": f"GitHub trending: **{m.get('repo_name','?')}** — {m.get('desc','')[:60]}",
                    "probe": "tech"})
    for m in data.get("guardian_hits", []):
        out.append({"question": m["question"], "yes": m.get("yes", 0),
                    "signal_type": "guardian_news",
                    "detail": f"Guardian: \"{m.get('title','')[:65]}\"",
                    "probe": "tech"})
    return out


EXTRACTORS = {
    "consensus": markets_from_consensus,
    "sentiment": markets_from_sentiment,
    "trending":  markets_from_trending,
    "orderflow": markets_from_orderflow,
    "macro":     markets_from_macro,
    "tech":      markets_from_tech,
}


# ── Write per-market Obsidian notes ──────────────────────────────────────────

def write_market_note(slug, records):
    """One note per market — all probe signals for that market in one place."""
    mkt_dir = os.path.join(VAULT, "Markets")
    os.makedirs(mkt_dir, exist_ok=True)
    q    = records[0]["question"]
    yes  = records[0].get("yes", 0)
    tags = " ".join(f"#{r['signal_type']}" for r in records)
    probes = sorted({r["probe"] for r in records})

    L = [
        f"---",
        f"tags: [{', '.join(r['signal_type'] for r in records)}]",
        f"probes: [{', '.join(probes)}]",
        f"yes_price: {yes:.2f}",
        f"---",
        f"",
        f"# {slug}",
        f"",
        f"> Polymarket YES: **{yes:.0%}**  ·  signals from: {', '.join(f'[[{PROBE_NOTES[p]}]]' for p in probes)}",
        f"",
        f"## Signals",
        f"",
    ]
    for r in records:
        L.append(f"- **{r['signal_type']}** ([[{PROBE_NOTES[r['probe']]}]]): {r['detail']}")

    # Knowledge graph context from graphify brain
    kb_hits = _kb_context(q)
    if kb_hits:
        L += ["", "## Knowledge Graph Context", "",
              "_From graphify brain — what the bot already knows about this market:_", ""]
        L.extend(kb_hits)

    path = os.path.join(mkt_dir, slug[:60] + ".md")
    open(path, "w").write("\n".join(L) + "\n")
    return path


# ── Main hub note ─────────────────────────────────────────────────────────────

def build_hub():
    now_str = time.strftime("%Y-%m-%d %H:%M UTC")

    # Load all caches
    loaded = {}
    status = {}
    for name, path in CACHES.items():
        data, err = _load(name, path)
        loaded[name] = data
        status[name] = f"✅ {_ts(data)}" if data else f"⚠️ {err}"

    # Extract all market signals
    all_records = []
    for name, data in loaded.items():
        if data:
            all_records.extend(EXTRACTORS[name](data))

    # Group by normalised question
    by_q = {}
    for r in all_records:
        key = r["question"].lower().strip()[:80]
        by_q.setdefault(key, []).append(r)

    # Convergences = market in 2+ probes
    converged = {k: v for k, v in by_q.items()
                 if len({r["probe"] for r in v}) >= 2}
    single    = {k: v for k, v in by_q.items()
                 if len({r["probe"] for r in v}) == 1}

    # Write per-market notes for convergences
    mkt_links = {}
    for key, records in converged.items():
        slug = _slug(records[0]["question"])
        write_market_note(slug, records)
        mkt_links[key] = slug

    # ── Hub note ──────────────────────────────────────────────────────────────
    L = [
        "---",
        "tags: [signal-hub, polymarket, daily-digest]",
        "---",
        "",
        "# 🎯 Signal Hub",
        "",
        f"> Updated {now_str}",
        f"> Probes: [[consensus_probe]] · [[sentiment_probe]] · [[trending_probe]] · [[orderflow_probe]]",
        "",
        "## Probe Status",
        "",
    ]
    for name, st in status.items():
        L.append(f"- [[{PROBE_NOTES[name]}]]: {st}")

    # Convergences section
    L += ["", "---", "", "## 🔴 Cross-Probe Convergences", "",
          "_Markets appearing in 2+ probes — highest confidence signals._", ""]

    if converged:
        for key, records in sorted(converged.items(),
                                   key=lambda x: -len({r["probe"] for r in x[1]})):
            slug   = mkt_links[key]
            probes = sorted({r["probe"] for r in records})
            yes    = records[0].get("yes", 0)
            signal_icons = {"consensus_divergence": "⚖️", "wiki_spike": "📚",
                            "sentiment_bullish": "📈", "sentiment_bearish": "📉",
                            "coingecko_trending": "🔥", "hn_frontpage": "💻",
                            "velocity_spike": "🚀"}
            icons = " ".join(signal_icons.get(r["signal_type"], "•") for r in records)
            probe_links = " + ".join(f"[[{PROBE_NOTES[p]}]]" for p in probes)
            L.append(f"### [[Reports/Markets/{slug}|{slug[:50]}]]")
            L.append(f"> {icons}  ·  Poly YES {yes:.0%}  ·  {probe_links}")
            L.append("")
            for r in records:
                L.append(f"- {r['detail']}")
            L.append("")
    else:
        L += ["_No cross-probe convergences this cycle._", ""]

    # Single-probe signals (compact)
    L += ["---", "", "## Single-Probe Signals", ""]
    by_probe = {}
    for key, records in single.items():
        p = records[0]["probe"]
        by_probe.setdefault(p, []).extend(records)

    for probe in ["orderflow", "consensus", "trending", "sentiment"]:
        records = by_probe.get(probe, [])
        if not records:
            continue
        L.append(f"### [[{PROBE_NOTES[probe]}]] ({len(records)} signals)")
        L.append("")
        for r in records[:6]:
            slug = _slug(r["question"])
            L.append(f"- **{r['signal_type']}** · Poly {r.get('yes',0):.0%} · {r['detail'][:70]}")
        if len(records) > 6:
            L.append(f"  _…and {len(records)-6} more in [[{PROBE_NOTES[probe]}]]_")
        L.append("")

    # Footer links
    L += ["---", "",
          "## All Probe Reports", "",
          "| Probe | Report | Findings (graphify) |",
          "|---|---|---|",
          "| Consensus | [[consensus_probe]] | [[consensus_findings]] |",
          "| Sentiment | [[sentiment_probe]] | [[sentiment_findings]] |",
          "| Trending  | [[trending_probe]]  | [[trending_findings]]  |",
          "| Orderflow | [[orderflow_probe]] | [[orderflow_findings]] |",
          ""]

    hub_path = os.path.join(VAULT, "Signal Hub.md")
    open(hub_path, "w").write("\n".join(L) + "\n")
    print(f"[hub] {len(converged)} convergences, {len(single)} single-probe → {hub_path}")
    return len(converged)


def main():
    os.makedirs(os.path.join(VAULT, "Markets"), exist_ok=True)
    n = build_hub()
    # (2026-06-21) Removed the rsync into the legacy "Obsidian Vault" — it was merged
    # into the canonical PolymarketVault and retired. VAULT already points there.


if __name__ == "__main__":
    main()
