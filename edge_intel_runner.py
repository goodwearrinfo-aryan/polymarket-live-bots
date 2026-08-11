#!/usr/bin/env python3
"""
edge_intel_runner.py — hourly free/$0 autonomous layer for the Edge-Intelligence suite.

The 7 Claude agents (graveyard-oracle, settlement-race-sentinel, book-portfolio-optimizer,
edge-decay-tracker, loss-autopsy, resolution-source-monitor, adverse-selection-sentinel)
are METERED Claude subagents — too costly to cron. This runner is their cheap autonomous
shadow: the DETERMINISTIC layer of each, computed in pure Python ($0), with the free-LLM
chain used ONLY for an optional one-paragraph synthesis (worker_lib.free_chat, also $0).

It does NOT replace the deep agents — it surfaces the leads hourly so a human/agent only
spends the metered key when there's something real. Honest by construction: every monitor
is fail-soft (degrades to a clear status line, never a traceback), the determinacy/adverse
reads are labelled as the cheap layer, and nothing here trades or edits config.

Run:  python3 edge_intel_runner.py once     # one cycle (launchd calls this)
      python3 edge_intel_runner.py loop     # foreground debug loop
"""
import os, sys, json, time, urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import worker_lib as W

STATE = os.path.expanduser("~/Documents/polymarket/scalp_lab_state.json")
GATE_LOG = os.path.expanduser("~/Documents/polymarket/analyst_data_gate_log.jsonl")
LAG_THRESHOLD = 0.05          # min net price-lag to flag a determinacy gap
STALE_GATE_SECS = 3 * 3600    # gate scan older than this = stale


def _now():
    return datetime.now(timezone.utc)


def _get(url, timeout=12):
    req = urllib.request.Request(url, headers={"User-Agent": "edge-intel/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


# ---------------------------------------------------------------- monitor 1
def settlement_race():
    """Cheap settlement-race-sentinel: read the latest data-gate scan, rank the
    determinacy gaps (data already determines outcome but price lags)."""
    try:
        if not os.path.exists(GATE_LOG):
            return "settlement-race: no gate log yet — keep logging."
        last = None
        with open(GATE_LOG) as f:
            for line in f:
                line = line.strip()
                if line:
                    last = line
        if not last:
            return "settlement-race: gate log empty."
        rec = json.loads(last)
        age = None
        try:
            age = (_now() - datetime.fromisoformat(rec["ts"].replace("Z", "+00:00"))).total_seconds()
        except Exception:
            pass
        stale = f" [STALE {int(age//3600)}h]" if (age and age > STALE_GATE_SECS) else ""
        now = _now()
        races, suspects = [], []
        for row in rec.get("rows", []):
            doss = row.get("dossier") or {}
            if not isinstance(doss, dict):
                continue
            trig = doss.get("already_triggered")
            yes = row.get("yes")
            if yes is None:
                continue
            try:
                yes = float(yes)
            except Exception:
                continue
            # window-closed test: NO is only DETERMINED if the resolution window has ended.
            window_closed = False
            try:
                end = row.get("end")
                if end:
                    edt = datetime.fromisoformat(str(end).replace("Z", "+00:00"))
                    window_closed = edt < now
            except Exception:
                pass
            q = row.get("q", "?")[:70]
            src = row.get("source", "?")
            if trig is True:
                # YES is data-determined. A genuine lag has yes meaningfully BELOW 1 but
                # NOT near 0 — yes≈0 on a 'triggered' market is a data/price CONTRADICTION
                # (likely a resolution-reading issue), NOT a tradeable gap. Don't surface it as edge.
                lag = 1.0 - yes
                if yes < 0.10:
                    suspects.append((q, src, yes, "triggered but priced ~0 — resolution/gate contradiction"))
                elif lag >= LAG_THRESHOLD:
                    races.append((lag, "YES", yes, q, src))
            elif trig is False and window_closed:
                # NO is data-determined (window closed without trigger).
                lag = yes - 0.0
                if yes > 0.90:
                    suspects.append((q, src, yes, "window closed & not triggered but priced ~1 — contradiction"))
                elif lag >= LAG_THRESHOLD:
                    races.append((lag, "NO", yes, q, src))
            # trig False + window OPEN  => market is LIVE (undetermined) => NOT a gap, skip.
        races.sort(reverse=True)
        out = []
        if races:
            out.append(f"settlement-race: {len(races)} GAP(S){stale} (cheap layer — verify executable fill before trusting):")
            for lag, side, yes, q, src in races[:5]:
                out.append(f"  • {side} determined, lag {lag:+.2f} @ yes={yes:.2f} [{src}] — {q}")
            out.append("  → hand the top one to settlement-race-sentinel / edge-verifier for the real fill.")
        else:
            out.append(f"settlement-race: NO race live{stale} — {len(rec.get('rows',[]))} series scanned, none determined-but-mispriced (calibrated steady state).")
        if suspects:
            out.append(f"  ⚠️ {len(suspects)} data/price contradiction(s) (NOT edge — resolution-reading flags): " + "; ".join(f"{q} ({why})" for q, src, yes, why in suspects[:3]))
        return "\n".join(out)
    except Exception as e:
        return f"settlement-race: [fail-soft] {e}"


# ---------------------------------------------------------------- monitor 2
def source_monitor():
    """Cheap resolution-source-monitor: keyless liveness probe of the resolution oracles.
    Frozen/unreachable != stable. Methodology drift needs the full agent (WebSearch)."""
    probes = {
        "klines(Binance)": "https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1h&limit=2",
        "ESPN": "https://site.api.espn.com/apis/site/v2/sports/soccer/eng.1/scoreboard",
        "Open-Meteo": "https://api.open-meteo.com/v1/forecast?latitude=0&longitude=0&current=temperature_2m",
    }
    out = []
    for name, url in probes.items():
        try:
            body = _get(url)
            ok = bool(body) and len(body) > 20
            out.append(f"  • {name}: {'STABLE' if ok else 'EMPTY?'}")
        except Exception as e:
            out.append(f"  • {name}: UNREACHABLE ({type(e).__name__})")
    note = "  (liveness only — methodology/schedule drift needs resolution-source-monitor agent + WebSearch)"
    return "resolution-sources:\n" + "\n".join(out) + "\n" + note


# ---------------------------------------------------------------- monitor 3 + 4 + 5
def _load_state():
    with open(STATE) as f:
        return json.load(f)


def adverse_and_book():
    """Cheap adverse-selection + book-portfolio layer from the paper book.
    Proxy only (paper fills): realized mean$/exit & win-rate per leg as the
    aggregate adverse-selection signature; raw open-position count for the book."""
    try:
        st = _load_state()
    except Exception as e:
        return f"book/adverse: [fail-soft] {e}", 0
    leg_rows, open_n, loss_flags = [], 0, []
    for leg, d in st.items():
        if not isinstance(d, dict):
            continue
        opens = d.get("open") or []
        closed = d.get("closed") or []
        open_n += len(opens) if isinstance(opens, list) else 0
        if isinstance(closed, list) and closed:
            pnls = []
            for ex in closed:
                if isinstance(ex, dict):
                    p = ex.get("pnl_usdc", ex.get("pnl", ex.get("realized", ex.get("profit"))))
                    try:
                        pnls.append(float(p))
                    except Exception:
                        pass
            if pnls:
                n = len(pnls)
                mean = sum(pnls) / n
                wr = sum(1 for p in pnls if p > 0) / n
                leg_rows.append((mean, leg, n, wr))
                # adverse signature: systematic negative over a non-trivial sample
                if n >= 10 and mean < 0 and wr < 0.5:
                    loss_flags.append((mean, leg, n, wr))
    leg_rows.sort()
    lines = [f"book/adverse (cheap proxy — paper fills): {open_n} open positions across {len(leg_rows)} legs w/ exits."]
    if loss_flags:
        loss_flags.sort()
        lines.append("  adverse-selection flags (n≥10, mean<0, WR<50% — bleeding aggregate):")
        for mean, leg, n, wr in loss_flags[:5]:
            lines.append(f"  • {leg}: mean ${mean:+.3f}/exit, WR {wr:.0%}, n={n}")
        lines.append("  → most are calibrated-mid noise, not a fixable adversary; hand a flagged leg to adverse-selection-sentinel to separate signal from variance.")
    else:
        lines.append("  no leg with a systematic-loss signature at n≥10 (or too few exits).")
    lines.append("  (effective-N / driver-concentration needs book-portfolio-optimizer agent — not computable deterministically here.)")
    return "\n".join(lines), len(loss_flags)


def _resolved_losers(st):
    """The ONLY honest autopsy target: positions that RODE TO RESOLUTION and lost
    (reason='resolved', pnl<0) — NOT scalp stop/time/target exits (calibrated-mid noise)."""
    out = []
    for leg, d in st.items():
        if not isinstance(d, dict):
            continue
        for ex in (d.get("closed") or []):
            if not isinstance(ex, dict):
                continue
            if str(ex.get("reason", "")).lower() != "resolved":
                continue
            try:
                pnl = float(ex.get("pnl_usdc"))
            except Exception:
                continue
            if pnl < 0:
                out.append((leg, ex, pnl))
    return out


def loss_scan():
    """Cheap loss-autopsy trigger: count RESOLVED losers (not scalp stop-outs)."""
    try:
        st = _load_state()
    except Exception as e:
        return f"loss-scan: [fail-soft] {e}"
    rl = _resolved_losers(st)
    return (f"loss-scan: {len(rl)} RESOLVED losing positions (reason='resolved', pnl<0) — "
            "these are the real loss-autopsy targets (scalp stop/time/target exits are calibrated-mid noise, excluded). "
            "Autonomous autopsy processes NEW ones each cycle; see Graveyard Auto.")


# ---------------------------------------------------------------- autopsy ($0)
AUTOPSY_STATE = os.path.expanduser("~/Documents/polymarket/edge_intel_autopsy_state.json")
GRAVEYARD_AUTO = os.path.expanduser("~/Documents/PolymarketVault/Graveyard Auto")
AUTOPSY_CAP = 3   # max NEW resolved-losers autopsied per cycle (quality cap, not cost — it's $0)
# only these causes earn a written lesson; noise / bad-luck do NOT (don't pollute the graveyard)
WRITEWORTHY = {"resolution-misread", "regime-break", "executed-badly"}


def _load_autopsy_state():
    try:
        return set(json.load(open(AUTOPSY_STATE)))
    except Exception:
        return set()


def _save_autopsy_state(done):
    try:
        tmp = AUTOPSY_STATE + ".tmp"
        json.dump(sorted(done), open(tmp, "w"))
        os.replace(tmp, AUTOPSY_STATE)   # atomic
    except Exception as e:
        W.log("edge_intel", f"[autopsy state FAIL] {e}")


def autopsy_resolved_losers():
    """Autonomous loss-autopsy ($0, free chain). Processes NEW resolved losers only,
    classifies the cause, and WRITES a provisional graveyard lesson to Vault/Graveyard Auto/
    ONLY for fixable causes (never noise/bad-luck). Each exit autopsied once (state-tracked).
    Free-LLM is overconfident, so notes are marked PROVISIONAL for human promotion."""
    try:
        st = _load_state()
    except Exception as e:
        return f"autopsy: [fail-soft] {e}"
    done = _load_autopsy_state()
    fresh = [(leg, ex, pnl) for leg, ex, pnl in _resolved_losers(st)
             if ex.get("id") and ex["id"] not in done]
    if not fresh:
        return f"autopsy: no NEW resolved losers ({len(done)} already autopsied). Loop idle — the honest steady state."
    if not W.llm_ready():
        return f"autopsy: {len(fresh)} new resolved loser(s) but free LLM chain down — deferring (will retry next cycle)."
    os.makedirs(GRAVEYARD_AUTO, exist_ok=True)
    written, processed = [], 0
    for leg, ex, pnl in fresh[:AUTOPSY_CAP]:
        q = (ex.get("q") or "")[:140]
        side = ex.get("side")
        sysmsg = ("You are loss-autopsy: root-cause ONE resolved trading loss into exactly one "
                  "cause. Causes: resolution-misread (rules read wrong), regime-break (world changed "
                  "after entry), was-always-noise (calibrated-mid coin flip — NO fixable lesson), "
                  "executed-badly (fill/slippage ate it), bad-luck-on-real-edge (correct process). "
                  "Most scalp/prediction losses are was-always-noise — default there unless evidence "
                  "names a specific misread/break. Return JSON.")
        user = (f"Market: {q}\nSide held: {side}\nResult: resolved AGAINST (pnl ${pnl:+.3f}).\n"
                f"Leg/strategy: {leg}.\nClassify the cause and, if fixable, state the one-line lesson.")
        schema = '{"cause": "<one of the 5>", "confidence": 0.0, "lesson": "<one line or empty if noise/bad-luck>"}'
        v = W.free_judge(sysmsg, user, schema_hint=schema)
        processed += 1
        done.add(ex["id"])
        if not isinstance(v, dict):
            continue
        cause = str(v.get("cause", "")).strip().lower()
        conf = v.get("confidence", 0)
        try:
            conf = float(conf)
        except Exception:
            conf = 0.0
        lesson = (v.get("lesson") or "").strip()
        if cause in WRITEWORTHY and conf >= 0.6 and lesson:
            # dedupe by lesson signature
            key = "".join(c for c in lesson.lower() if c.isalnum())[:40]
            stem = f"auto-{cause}-{key[:24]}"
            path = os.path.join(GRAVEYARD_AUTO, f"{stem}.md")
            if not os.path.exists(path):
                fm = (f"---\ntitle: {stem}\ntype: graveyard-auto\nstatus: provisional\n"
                      f"cause: {cause}\nconfidence: {conf}\nsource: free-LLM ({W.prov()})\n"
                      f"created: {W.ts()}\ntags: [graveyard, auto, provisional]\n---\n\n")
                body = (f"# Auto-autopsy: {cause}\n\n**Lesson:** {lesson}\n\n"
                        f"- Market: {q}\n- Side: {side}\n- Loss: ${pnl:+.3f}\n- Leg: {leg}\n\n"
                        f"> ⚠️ PROVISIONAL — written autonomously by edge_intel_runner on the FREE LLM chain "
                        f"(overconfident; the candela lesson). A human/metered loss-autopsy agent should "
                        f"confirm before this is promoted to the curated memory graveyard. graveyard-oracle "
                        f"reads this folder as provisional corpse-DNA.\n")
                try:
                    open(path, "w").write(fm + body)
                    written.append((cause, lesson[:60]))
                except Exception as e:
                    W.log("edge_intel", f"[autopsy write FAIL] {e}")
    _save_autopsy_state(done)
    if written:
        lines = [f"autopsy: processed {processed} new resolved loser(s), WROTE {len(written)} provisional lesson(s):"]
        for cause, lesson in written:
            lines.append(f"  • [{cause}] {lesson}")
        lines.append("  → in Vault/Graveyard Auto/ (provisional, free-LLM); promote confirmed ones to curated memory.")
        return "\n".join(lines)
    return f"autopsy: processed {processed} new resolved loser(s) — all classified noise/bad-luck (no lesson written, correct default)."


# ---------------------------------------------------------------- live race (15-min)
def settlement_race_live():
    """Tighter-cadence settlement race: re-derive CRYPTO-threshold determinacy LIVE from
    klines (bypasses the 6h gate for the fastest-moving markets). Alerts only on NEWLY
    data-determined markets (dedup), which is the time-sensitive event. Price-lag still
    needs the full agent (gate yes may be stale)."""
    try:
        import analyst_data_gate as G
    except Exception as e:
        return f"race-live: [fail-soft] cannot import gate ({e})."
    if not os.path.exists(GATE_LOG):
        return "race-live: no gate log to source the crypto market list."
    last = None
    with open(GATE_LOG) as f:
        for line in f:
            if line.strip():
                last = line
    try:
        rec = json.loads(last)
    except Exception as e:
        return f"race-live: [fail-soft] {e}"
    determined, n = [], 0
    for row in rec.get("rows", []):
        if row.get("source") != "crypto-threshold":
            continue
        d = row.get("dossier") or {}
        sym = d.get("symbol") or ""
        asset = sym.replace("USDT", "").replace("USD", "")[:5] or None
        thr, direction, ws = d.get("threshold"), d.get("direction"), d.get("window_start")
        if not (asset and thr and direction):
            continue
        n += 1
        try:
            r = G.fetch_crypto_threshold(asset, thr, direction, ws)
        except Exception:
            continue
        if r.get("already_triggered") is True:
            yes = row.get("yes")
            determined.append((row.get("q", "?")[:70], asset, thr, direction, yes, r.get("realized_max"), r.get("realized_min")))
    # dedup: alert only markets not already flagged
    new = [t for t in determined if W.is_new_alert("edge_race", t[0])]
    body = [f"# Edge-Race live (crypto determinacy) — {W.ts()}",
            f"\nRe-derived {n} crypto-threshold markets LIVE from klines (bypassing the 6h gate)."]
    if determined:
        body.append(f"\n**{len(determined)} data-DETERMINED (YES) right now** — verify price caught up:")
        for q, asset, thr, dirn, yes, rmax, rmin in determined[:8]:
            body.append(f"- {asset} {dirn} {thr} TRIGGERED (realized_max={rmax}) · gate yes={yes} — {q}")
        body.append("\n> Determined ≠ tradeable: yes may be stale (6h gate) and yes≈0 on a triggered market is a "
                    "resolution/data CONTRADICTION, not edge. Hand a fresh one to settlement-race-sentinel + pm-resolution-checker.")
    else:
        body.append("\nNO crypto market newly data-determined — calibrated steady state.")
    W.vault_write("edge_race", "\n".join(body))
    W.log("edge_race", f"live race ok — {n} crypto re-derived, {len(determined)} determined, {len(new)} new")
    print(f"[{W.ts()}] edge-race live ok — {n} crypto re-derived, {len(determined)} determined, {len(new)} new-alert")
    return f"race-live: {n} crypto re-derived, {len(determined)} determined ({len(new)} new)."


# ---------------------------------------------------------------- cycle
def run_once():
    parts = []
    parts.append(settlement_race())
    parts.append(source_monitor())
    book, n_flags = adverse_and_book()
    parts.append(book)
    parts.append(loss_scan())
    parts.append(autopsy_resolved_losers())   # autonomous $0 loop-closer
    body_core = "\n\n".join(parts)

    # optional free-LLM synthesis ($0, fail-soft); deterministic body stands alone if down
    synth = None
    if W.llm_ready():
        synth = W.free_chat(
            prompt="Synthesize these autonomous edge-intelligence monitor outputs into ONE "
                   "dollars-first sentence naming the single most actionable thing, or say "
                   "'nothing actionable — honest steady state'. Be skeptical; cheap layer, "
                   "leads not verdicts.\n\n" + body_core,
            system="You are a terse, honest trading-ops synthesizer. One sentence. No hype.",
            max_tokens=200)

    header = f"# Edge-Intel hourly brief — {W.ts()}\n"
    prov_line = f"\n_synthesis: {W.prov() if synth else 'deterministic-only (LLM chain down or no synth)'}_\n"
    synth_block = f"\n**Synthesis:** {synth.strip()}\n" if synth else ""
    body = header + synth_block + "\n```\n" + body_core + "\n```\n" + prov_line

    W.vault_write("edge_intel", body)
    W.log("edge_intel", f"cycle ok — {n_flags} adverse flag(s); synth={'yes' if synth else 'no'}")
    # stdout for the launchd /tmp log (proves a real cycle ran)
    print(f"[{W.ts()}] edge-intel cycle ok — vault: Reports/edge_intel.md — {n_flags} adverse flag(s)")
    return body_core


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "once"
    if mode == "race":           # 15-min live-crypto determinacy race (its own launchd job)
        settlement_race_live()
    elif mode == "loop":
        while True:
            try:
                run_once()
            except Exception as e:
                print("loop error:", e)
            time.sleep(3600)
    else:
        run_once()


if __name__ == "__main__":
    main()
