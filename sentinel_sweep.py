#!/usr/bin/env python3
"""sentinel_sweep.py — AUTONOMOUS deterministic twin of the 3 read-only logger sentinels
(pm-xvenue-sentinel / pm-dataedge-sentinel / pm-knowledge-sentinel).

The Claude agents interpret on-demand; this is the scheduled, zero-cost, no-LLM version:
pure-Python data-reduction over the SAME logger families, applying the SAME discipline —
tx-dedup recurrence, effective-n (distinct timestamps), in-window-vs-lifetime, freshness.
Quiet by default; alerts LOUD (iMessage+WhatsApp, fail-soft) ONLY on a real escalatable
signal. Deterministic on purpose: no model to hallucinate, nothing to be down, can't fail.

Run:  python3 sentinel_sweep.py [all|xvenue|dataedge|knowledge] [--alert]
Wired into watchdog_loop.sh (KeepAlive'd) on staggered %N offsets → self-heals with it.
Outputs: sentinel_sweep_log.jsonl  +  ~/Documents/PolymarketVault/Reports/sentinel_sweep.md
"""
import os, sys, json, glob
import datetime as dt

PM = os.path.dirname(os.path.abspath(__file__))
CODEX = os.path.expanduser("~/Documents/Codex/2026-05-28/is-git-installed")
VAULT = os.path.expanduser("~/Documents/PolymarketVault/Reports/sentinel_sweep.md")
LOG = os.path.join(PM, "sentinel_sweep_log.jsonl")

ODDPOOL = os.path.join(CODEX, "oddpool_multibot_log.jsonl")
XVENUE = os.path.join(PM, "xvenue_log.jsonl")
BASKET = os.path.join(PM, "basket_locks.jsonl")
FUNDING = os.path.join(PM, "funding_log.jsonl")
GATE = os.path.join(PM, "analyst_data_gate_log.jsonl")
NEWSARB = os.path.join(PM, "news_arb_log.jsonl")
CANDLE = os.path.join(PM, "candle_knowledge_log.jsonl")


def _now():
    return dt.datetime.now(dt.timezone.utc)


def _read(path, n=None):
    try:
        with open(path, encoding="utf-8") as f:
            lines = [json.loads(x) for x in f if x.strip()]
        return lines[-n:] if n else lines
    except Exception:
        return []


def _age_h(path):
    try:
        return (_now().timestamp() - os.path.getmtime(path)) / 3600
    except Exception:
        return None


def _parse_ts(s):
    try:
        return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None


# ── xvenue: structural / cross-venue recurrence ──────────────────────────────
def sweep_xvenue():
    sig, health = [], []

    # oddpool: real accumulation = ≥3 DISTINCT tx on one market across ≥2 cycles
    cyc = _read(ODDPOOL, 12)
    if cyc:
        by_title = {}
        for line in cyc:
            ts = line.get("ts")
            for w in line.get("whales", []):
                t = w.get("title", "")
                if not t:
                    continue
                by_title.setdefault(t, {"tx": set(), "cycles": set()})
                by_title[t]["tx"].add(w.get("tx"))
                by_title[t]["cycles"].add(ts)
        for t, d in by_title.items():
            if len(d["tx"]) >= 3 and len(d["cycles"]) >= 2:
                sig.append(f"🐋 xvenue: '{t[:48]}' drew {len(d['tx'])} distinct whale tx "
                           f"across {len(d['cycles'])} cycles → real accumulation (→ pm-whale-vetter)")
        # any compound signal logged this window
        for line in cyc:
            for c in line.get("compound", []):
                sig.append(f"🔀 xvenue: compound '{c.get('title','')[:42]}' "
                           f"score={c.get('score')} {','.join(c.get('types',[]))}")
        age = _age_h(ODDPOOL)
        if age is not None and age > 13:
            health.append(f"oddpool history stale {age:.0f}h (12h sched) — check com.aryan.oddpool-bot")

    # xvenue: any non-zero divergence (rare; honest null is n_div=0)
    xv = _read(XVENUE, 5)
    for line in xv:
        if line.get("n_div", 0) > 0 and line.get("hits"):
            sig.append(f"⚡ xvenue: {line['n_div']} live Poly↔Kalshi divergence(s) "
                       f"(→ pm-resolution-checker same-event check)")
            break

    # basket: a fresh lock that clears the 2% fee (edge ≥ 0.03)
    bl = _read(BASKET, 1)
    if bl:
        b = bl[-1]
        if (b.get("edge") or 0) >= 0.03 and (b.get("n") or 0) >= 2:
            sig.append(f"🔒 xvenue: fresh basket lock {b.get('slug','')} {b.get('kind','')} "
                       f"edge {b['edge']:+.0%} n={b['n']} (→ pm-capacity-estimator)")

    # funding: extreme basis
    fz = _read(FUNDING, 1)
    if fz:
        for c, v in (fz[-1].get("annual_pct") or {}).items():
            if abs(v) >= 30:
                sig.append(f"📈 xvenue: {c} perp funding {v:+.0f}%/yr (extreme basis)")
    return sig, health


# ── dataedge: lagging-market arb / catalyst ──────────────────────────────────
def sweep_dataedge():
    sig, health = [], []
    g = _read(GATE, 1)
    if g:
        last = g[-1]
        for a in (last.get("alerts") or []):
            q = (a.get("q") or "")[:48]
            sig.append(f"🎯 dataedge: GATE ARB '{q}' (→ pm-resolution-checker → pm-data-resolver)")
        age = _age_h(GATE)
        if age is not None and age > 2:
            health.append(f"data-gate stale {age:.0f}h — check watchdog %30 block")
    for n in _read(NEWSARB, 6):
        v = n.get("verdict", {})
        if v.get("edge") and v.get("confidence", 0) > 70 and v.get("timing_ok"):
            sig.append(f"📰 dataedge: unincorporated catalyst '{(n.get('q') or '')[:42]}' "
                       f"side={v.get('side')} conf={v.get('confidence')} (→ pm-news-arb)")
    return sig, health


# ── knowledge: effective-n + multi-regime gate (no fake candle edges) ─────────
def sweep_knowledge():
    sig, health = [], []
    rows = _read(CANDLE)
    if not rows:
        return sig, health
    setups = {}
    for r in rows:
        det = r.get("det_t")
        for s in (r.get("setups") or []):
            d = setups.setdefault(s, {"dts": set(), "r1": [], "r4": []})
            d["dts"].add(det)
            if r.get("r1h") is not None:
                d["r1"].append(r["r1h"])
            if r.get("r4h") is not None:
                d["r4"].append(r["r4h"])
    for s, d in setups.items():
        eff = len(d["dts"])                     # distinct timestamps = effective n
        if eff < 30 or not d["r1"] or not d["r4"]:
            continue
        # multi-regime proxy: distinct det_t span ≥ 14 days
        ts = [t for t in d["dts"] if t]
        span_d = (max(ts) - min(ts)) / 86400_000 if len(ts) >= 2 else 0  # det_t is ms
        m1 = sum(d["r1"]) / len(d["r1"])
        m4 = sum(d["r4"]) / len(d["r4"])
        one_signed = (m1 > 0 and m4 > 0) or (m1 < 0 and m4 < 0)
        if one_signed and span_d >= 14 and abs(m4) >= 0.005:
            sig.append(f"🕯 knowledge: setup '{s}' eff-n={eff} span={span_d:.0f}d "
                       f"r1h{m1:+.2%}/r4h{m4:+.2%} one-signed → DSR-test (→ pm-dsr-gatekeeper)")
    age = _age_h(CANDLE)
    if age is not None and age > 1:
        health.append(f"candle logger stale {age:.0f}h")
    return sig, health


SWEEPS = {"xvenue": sweep_xvenue, "dataedge": sweep_dataedge, "knowledge": sweep_knowledge}


def main():
    which = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else "all"
    do_alert = "--alert" in sys.argv
    names = list(SWEEPS) if which == "all" else [which]

    all_sig, all_health, report = [], [], {}
    for n in names:
        try:
            s, h = SWEEPS[n]()
        except Exception as e:
            s, h = [], [f"{n} sweep error: {e}"]
        report[n] = {"signals": s, "health": h}
        all_sig += s
        all_health += h

    # vault md (always) + jsonl (always) — quiet steady-state is a valid result
    ts = _now().strftime("%Y-%m-%d %H:%M UTC")
    try:
        os.makedirs(os.path.dirname(VAULT), exist_ok=True)
        lines = [f"# Sentinel Sweep", f"_={ts}_", ""]
        for n in names:
            r = report[n]
            lines.append(f"## {n}  ({len(r['signals'])} signal, {len(r['health'])} health)")
            lines += [f"- {x}" for x in r["signals"]] or ["- _quiet — no escalatable signal_"]
            lines += [f"- ⚠️ {x}" for x in r["health"]]
            lines.append("")
        with open(VAULT, "w") as f:
            f.write("\n".join(lines))
    except Exception:
        pass
    try:
        with open(LOG, "a") as f:
            f.write(json.dumps({"ts": _now().strftime("%Y-%m-%dT%H:%M:%SZ"),
                                "n_signals": len(all_sig), "signals": all_sig,
                                "health": all_health}) + "\n")
    except Exception:
        pass

    print(f"[sentinel_sweep {which}] {len(all_sig)} signal(s), {len(all_health)} health note(s)")
    for x in all_sig:
        print("  " + x)
    for x in all_health:
        print("  ⚠️ " + x)

    # alert LOUD only on a NEW signal — dedup so a standing signal (oh-09, a whale)
    # alerts once, not every cycle. Health notes never alert (they go to the vault).
    if do_alert and all_sig:
        import hashlib, time
        SEEN = os.path.join(PM, ".sentinel_seen.json")
        TTL = 86400                                  # re-alert the same signal at most daily
        now = time.time()
        try:
            seen = {k: t for k, t in json.load(open(SEEN)).items() if now - t < TTL}
        except Exception:
            seen = {}
        fresh = []
        for s in all_sig:
            # key on the stable prefix (drop counts that wiggle cycle-to-cycle)
            key = hashlib.md5(s.split("→")[0].strip().encode()).hexdigest()[:12]
            if key not in seen:
                fresh.append(s)
                seen[key] = now
        if fresh:
            body = f"🛰 SENTINEL SWEEP — {len(fresh)} new signal(s)\n" + "\n".join(fresh[:12])
            if all_health:
                body += "\n— health: " + "; ".join(all_health[:3])
            try:
                import wa_alert
                wa_alert.notify(body)                # dual iMessage+WhatsApp, fail-soft
            except Exception as e:
                print(f"  (alert failed: {e})", file=sys.stderr)
            try:
                json.dump(seen, open(SEEN, "w"))
            except Exception:
                pass
            print(f"  → alerted {len(fresh)} new")
        else:
            print("  → all signals already alerted (deduped, quiet)")


if __name__ == "__main__":
    main()
