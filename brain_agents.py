#!/usr/bin/env python3
"""brain_agents.py — runs the LLM judgment agents (now FREE on local Ollama via
llm_client) over the live state and deposits their verdicts into the brain.

Wires the agent layer to the brain: each run re-vets the COPIED whale watchlist
(vet_whale → archetype/copyable via Ollama) and writes a maintained brain note +
append-only jsonl. The decision-relevant output: flags any whale we're copying
that the agent now judges NOT copyable (went dormant / favorite-padder / lucky),
so the whalecopy edge probe isn't following stale signal.

SAFETY: writes ONLY under <brain>/Areas (hard-guarded). Read-only on data-api.
Free inference (Ollama) → safe to run on a schedule. Stdlib + llm_client only.

Usage: python3 brain_agents.py once   |   report
"""
import os, sys, json, time, datetime
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import llm_client
import vet_whale

BRAIN = os.path.expanduser("~/Documents/PolymarketVault/brain")
AREAS = os.path.join(BRAIN, "Areas")
NOTE = os.path.join(AREAS, "Agent whale vets.md")
LOG = os.path.join(HERE, "brain_agents_log.jsonl")
MAX_VET = int(os.environ.get("BRAIN_AGENTS_MAX","25"))  # cap per run


def active_whales():
    wl = {}
    p = os.path.join(HERE, "insider_wallets.json")
    if os.path.exists(p):
        for addr, label in json.load(open(p)).items():
            if "WHALE" in (label or "").upper():
                wl[addr.lower()] = label
    p = os.path.join(HERE, "whale_candidates_active.json")
    if os.path.exists(p):
        try:
            for w in json.load(open(p)):
                wl.setdefault(w["addr"].lower(), f"whale-{w.get('win_rate','?')}pct")
        except Exception:
            pass
    return wl


def _safe(path):
    rp = os.path.realpath(path)
    if not rp.startswith(os.path.realpath(BRAIN) + os.sep):
        raise RuntimeError(f"REFUSED unsafe write outside brain/: {rp}")
    return path


def run_once():
    if not llm_client.configured():
        print("brain_agents: no LLM endpoint (set LLM_* in .env) — skipping"); return
    wl = active_whales()
    results = []
    for addr in list(wl)[:MAX_VET]:
        try:
            r = vet_whale.vet(addr)
            v = r.get("verdict", {}); s = r.get("stats", {})
            rec = {"addr": addr, "label": wl[addr],
                   "archetype": v.get("archetype", "?"), "copyable": v.get("copyable"),
                   "conf": v.get("confidence"), "pnl": s.get("realized_pnl"),
                   "wr": s.get("win_rate"), "recency_d": s.get("last_trade_days"),
                   "why": str(v.get("why", ""))[:140], "ts": int(time.time())}
            results.append(rec)
            with open(LOG, "a") as f:
                f.write(json.dumps(rec) + "\n")
            time.sleep(0.5)
        except Exception as e:
            results.append({"addr": addr, "error": str(e)[:80]})

    # A parse-failed verdict (empty archetype AND empty why) is UNKNOWN, not a real
    # "not copyable" — never let a model formatting miss false-flag a good whale.
    def parsed(r):
        return bool(str(r.get("archetype", "")).strip(" ?")) or bool(str(r.get("why", "")).strip())
    for r in results:
        if "error" not in r and not parsed(r):
            r["copyable"] = None; r["archetype"] = "unknown(parse-fail)"
    bad = [r for r in results if r.get("copyable") is False and parsed(r)]
    unknown = sum(1 for r in results if r.get("copyable") is None)
    write_brain_note(results, bad)
    print(f"brain_agents: vetted {len(results)} whales via Ollama → brain. "
          f"{len(bad)} confidently NOT copyable, {unknown} unknown(parse-fail). note={NOTE}")


def write_brain_note(results, bad):
    os.makedirs(AREAS, exist_ok=True)
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    L = [f"# Agent whale vets (auto, via local Ollama)", "",
         f"_Last run: {now} · {len(results)} whales re-vetted by the LLM judgment agent (free, local)._", "",
         "The whalecopy edge probe copies these wallets. If the agent flags one NOT copyable",
         "(dormant / favorite-padder / lucky), it's stale signal — consider dropping it.", ""]
    if bad:
        L += ["## ⚠️ Now judged NOT copyable", "",
              "| wallet | archetype | why |", "|---|---|---|"]
        for r in bad:
            L.append(f"| `{r['addr'][:12]}…` | {r['archetype']} | {r['why']} |")
        L.append("")
    L += ["## Full panel", "", "| wallet | copyable | archetype | conf | P&L | WR | recency |",
          "|---|---|---|---|---|---|---|"]
    for r in sorted(results, key=lambda x: (x.get("copyable") is not False)):
        if "error" in r:
            L.append(f"| `{r['addr'][:12]}…` | ERR | {r['error']} | | | | |"); continue
        L.append(f"| `{r['addr'][:12]}…` | {'✅' if r['copyable'] else '❌'} | "
                 f"{r['archetype']} | {r['conf']} | ${r['pnl']:,} | {r['wr']}% | {r['recency_d']}d |")
    open(_safe(NOTE), "w").write("\n".join(L) + "\n")


def report():
    if not os.path.exists(LOG):
        print("{}"); return
    rows = [json.loads(l) for l in open(LOG) if l.strip()]
    latest = {}
    for r in rows:
        if "addr" in r:
            latest[r["addr"]] = r
    bad = [r for r in latest.values() if r.get("copyable") is False]
    print(json.dumps({"total_vetted": len(latest), "not_copyable": len(bad),
                      "flagged": [r["addr"][:12] for r in bad]}, indent=2))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "once"
    report() if cmd == "report" else run_once()
