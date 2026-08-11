#!/usr/bin/env python3
"""xvenue_logger.py — READ-ONLY logger (NOT a trading leg, places NO trades).

Persists Polymarket↔Kalshi divergences (cross-venue arb candidates) to an append-only tape +
an Obsidian report — REUSING kalshi_feed.py's proven semantic matcher (entity-token overlap −
generic words, qtype agreement, market_match category+similarity, political office/stage gates)
rather than a crude re-implementation. kalshi_feed already FINDS and prints divergences and runs
on a schedule; the only gap was persistence, so this adds exactly that — divergences accumulate
so you can see whether any are REAL and PERSISTENT (a one-shot gap is usually a mismatch or a
fleeting quote, not an edge).

HONEST: a divergence is a CANDIDATE for human review, not locked profit — the two venues can
resolve on different sources/timing, so a "diverging pair" may not be the same event. Degrades
gracefully if Polymarket gamma is geo-blocked (home ISP) — logs that and skips.
Read-only, stdlib + kalshi_feed. Usage: python3 xvenue_logger.py once | report"""
import os, sys, json, time
import kalshi_feed as kf   # reuse the working fetchers + semantic matcher

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "xvenue_log.jsonl")
VAULT = os.path.expanduser("~/Documents/PolymarketVault/Reports/xvenue.md")


def find_divergences():
    """Return (n_poly_or_None, n_kalshi, hits[]). n_poly is None when gamma is unreachable."""
    kms = kf.kalshi_markets()
    pms = kf.poly_markets()
    if not pms:
        return None, len(kms), []          # gamma empty/blocked -> no cross-venue this run
    pm_tok = [(m, kf.toks(m["q"]) - kf.GENERIC) for m in pms]
    hits = []
    for k in kms:
        kt = kf.toks(k["title"]) - kf.GENERIC
        if len(kt) < kf.MIN_OVERLAP:
            continue
        best, bestm, shared = 0, None, set()
        for m, mt in pm_tok:
            ov = kt & mt
            if len(ov) > best:
                best, bestm, shared = len(ov), m, ov
        if best < kf.MIN_OVERLAP or not bestm:
            continue
        # SAME question type (win vs vote% vs margin vs run vs threshold), no "other"
        if kf.qtype(k["title"]) != kf.qtype(bestm["q"]) or kf.qtype(k["title"]) == "other":
            continue
        # fuzzy category + similarity + political office/stage gates (kalshi_feed's matcher)
        sim = 1.0
        if kf.mm_sim is not None:
            if kf.mm_cat(k["title"]) != kf.mm_cat(bestm["q"]):
                continue
            sim = kf.mm_sim(k["title"], bestm["q"])
            if sim < kf.MM_MIN_SIM:
                continue
            if kf.mm_cat(k["title"]) == "politics":
                ko, ks = kf.political_scope(k["title"])
                po, ps = kf.political_scope(bestm["q"])
                if (ko != "any" and po != "any" and ko != po) or \
                   (ks != "any" and ps != "any" and ks != ps):
                    continue
        div = abs(k["yes"] - bestm["yes"])
        if div >= kf.MIN_DIVERGENCE:
            hits.append({"div": round(div, 3), "k_yes": k["yes"], "p_yes": bestm["yes"],
                         "sim": round(sim, 2), "type": kf.qtype(k["title"]),
                         "side": "buy Poly YES" if k["yes"] > bestm["yes"] else "buy Poly NO",
                         "k": k["title"][:72], "p": bestm["q"][:72], "ticker": k.get("ticker")})
    hits.sort(key=lambda h: -h["div"])
    return len(pms), len(kms), hits


def once():
    n_poly, n_kalshi, hits = find_divergences()
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "status": "gamma_unreachable" if n_poly is None else "ok",
           "n_poly": n_poly, "n_kalshi": n_kalshi, "n_div": len(hits), "hits": hits[:30]}
    with open(LOG, "a") as f:
        f.write(json.dumps(rec) + "\n")
    report()
    if n_poly is None:
        print(f"xvenue: gamma unreachable — kalshi={n_kalshi}, no cross-venue this run")
    else:
        print(f"xvenue: poly={n_poly} kalshi={n_kalshi} divergences(>={kf.MIN_DIVERGENCE})={len(hits)}")


def _read_log():
    if not os.path.exists(LOG):
        return []
    out = []
    for line in open(LOG):
        line = line.strip()
        if line:
            try: out.append(json.loads(line))
            except Exception: pass
    return out


def report():
    rows = _read_log()
    L = ["# Cross-venue divergence — Polymarket ↔ Kalshi (read-only)",
         "",
         "> READ-ONLY logger (no trades). Reuses kalshi_feed.py's semantic matcher and PERSISTS the "
         "divergences so you can see which (if any) are real & persistent. A gap is a CANDIDATE for "
         "review, NOT locked profit — venues can resolve differently.",
         f"> Updated {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())} · {len(rows)} snapshots", ""]
    if not rows:
        L += ["_No snapshots yet._"]
    else:
        last = rows[-1]
        if last.get("status") == "gamma_unreachable":
            L += ["## ⚠️ Polymarket gamma unreachable this run",
                  "", "_Geo-blocked (home ISP?). Cross-venue needs both books — runs only on a "
                  "non-blocking network or the VPS. Kalshi markets seen: " + str(last.get("n_kalshi", 0)) + "._"]
        else:
            hits = last.get("hits") or []
            L += ["Polymarket **" + str(last.get("n_poly")) + "** · Kalshi **" + str(last.get("n_kalshi"))
                  + "** · divergences ≥" + str(kf.MIN_DIVERGENCE) + ": **" + str(last.get("n_div")) + "**", ""]
            if hits:
                L += ["## Divergence candidates (hand-verify they're the same event!)", "",
                      "| Δ | K yes | P yes | sim | type | action | Kalshi | Polymarket |",
                      "|--:|------:|------:|----:|------|--------|--------|------------|"]
                for h in hits[:20]:
                    L.append("| " + str(h["div"]) + " | " + str(h["k_yes"]) + " | " + str(h["p_yes"])
                             + " | " + str(h["sim"]) + " | " + h["type"] + " | " + h["side"]
                             + " | " + h["k"] + " | " + h["p"] + " |")
                L += ["", "_Persistence is the tell: a pair that diverges across MANY snapshots and is "
                      "genuinely the same event is the real candidate; one-shot gaps are usually mismatches "
                      "or stale quotes. Settlement risk still applies — verify before believing._"]
            else:
                L += ["_No divergences cleared the matcher + threshold this run — consistent with "
                      "the two venues having little genuinely-overlapping, mispriced inventory._"]
    os.makedirs(os.path.dirname(VAULT), exist_ok=True)
    open(VAULT, "w").write("\n".join(L) + "\n")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "once"
    if cmd == "once":
        once()
    elif cmd == "report":
        report()
    else:
        print("usage: xvenue_logger.py once|report")


if __name__ == "__main__":
    main()
