"""
obsidian_snapshot.py — auto-mirror bot state + beliefs + sports data to Obsidian vault.

Runs every 6h (launchd) + on-demand. Writes to ~/Documents/PolymarketVault/:
  - Bot/belief_calibration.md — Brier + reliability per leg (from belief_ledger)
  - Bot/sports_summary.md — last 30d pull + league breakdown
  - Bot/brain_snapshot.md — live excerpt from BRAIN.md
  - Bot/calibration_goldsky.md — Wilson-CI calibration screen (ml/dataset_goldsky.csv)
  - Bot/analyst_gate.md — latest 3-lens adversarial verdicts (analyst_verdicts.csv)
  - Bot/crypto_touch.md — latest model-vs-market one-touch tape (crypto_touch_daytrade.log)
  - Bot/bloomberg_terminal.md — live ports terminal: KPIs, leg P&L, MTM movers, analyst desk (ports_data.json)

Paper-only infrastructure. No API calls (file-based), no funds moved.
"""
import json, os, csv, math
from datetime import datetime, timezone
from pathlib import Path

VAULT_PATH = Path.home() / "Documents/PolymarketVault/Bot"
VAULT_PATH.mkdir(parents=True, exist_ok=True)
PM = Path(__file__).resolve().parent

# ── Durable enrichment cache ────────────────────────────────────────────────
# obsidian_enrich.py writes each note's source-grounded "How it works" doc to
# ENRICH_CACHE/<note>.md. The watchdog re-snapshots most notes every 30min-3h
# (per-agent --only refreshes), not just the 6h full run — so in-place enrichment
# is wiped fast. Fix: after ANY snapshot (re)writes a note, re-prepend its cached
# block, so the enrichment survives every clobber at every cadence.
import re as _re
ENRICH_CACHE = VAULT_PATH / ".enrich"
ENRICH_OPEN  = "<!-- enriched-by:obsidian_enrich -->"
ENRICH_CLOSE = "<!-- /enriched -->"

def apply_enrichment(note_name):
    """Idempotently prepend the cached enrichment block to Bot/<note_name>.md."""
    cache = ENRICH_CACHE / (note_name + ".md")
    note  = VAULT_PATH / (note_name + ".md")
    if not cache.exists() or not note.exists():
        return False
    block = cache.read_text(encoding="utf-8").strip()
    if not block:
        return False
    live = note.read_text(encoding="utf-8")
    # strip any previously-applied region so we never double-prepend. The region is
    # [optional YAML frontmatter] + OPEN ... CLOSE, anchored at file start — match it whole
    # (incl. the leading frontmatter) so re-applying never stacks two frontmatter blocks.
    live = _re.sub(r"\A(?:---\n.*?\n---\n)?" + _re.escape(ENRICH_OPEN) + r".*?" +
                   _re.escape(ENRICH_CLOSE) + r"\n*", "", live, flags=_re.DOTALL).lstrip()
    note.write_text(block + "\n" + ENRICH_CLOSE + "\n\n" + live, encoding="utf-8")
    return True

def apply_all_enrichment():
    """Re-apply every cached enrichment block. Cheap + idempotent; runs after each snapshot."""
    if not ENRICH_CACHE.exists():
        return 0
    return sum(1 for c in ENRICH_CACHE.glob("*.md") if apply_enrichment(c.stem))

def _ts():
    return datetime.now(timezone.utc).isoformat()[:16]


def _wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (c - h, c + h)


def snapshot_beliefs():
    """Calibration snapshot from belief_ledger."""
    try:
        import belief_ledger
        cal = belief_ledger.from_state()
    except Exception as e:
        cal = {"error": str(e)}

    lines = [
        "# Belief Calibration",
        f"_Last update: {_ts()}_",
        "",
        "## Per-Leg Calibration (Brier + Reliability)",
        "",
    ]

    if "error" in cal:
        lines.append(f"⚠️ {cal['error']}")
    else:
        for leg in sorted(cal.keys(), key=lambda l: -cal[l].get("n", 0)):
            c = cal[leg]
            n = c.get("n", 0)
            if n == 0:
                continue
            brier = c.get("brier", 0)
            gap = c.get("calibration_gap", 0)
            flag = "✅ WELL-CALIBRATED" if abs(gap) <= 0.05 else "⚠️ MISCALIBRATED"
            lines.append(f"### `{leg.upper()}`")
            lines.append(f"- **n={n}** | Brier={brier} | gap={gap:+.3f} {flag}")
            lines.append(f"- Implied={c.get('mean_implied', 0):.3f} | Realized={c.get('realized_winrate', 0):.3f}")
            if c.get("reliability"):
                lines.append("- **Reliability (deciles):**")
                for k, b in c["reliability"].items():
                    lines.append(f"  - {k:.1f} → {b['realized']:.2f} (n={b['n']})")
            lines.append("")

    (VAULT_PATH / "belief_calibration.md").write_text("\n".join(lines), encoding="utf-8")
    return len(cal)


def snapshot_sports():
    """Sports data summary from last 30d pull."""
    try:
        import sports_data
        pay = sports_data.load()
        idx, form = sports_data.result_index()
    except Exception as e:
        pay = {"error": str(e), "events": []}
        idx, form = {}, {}

    lines = [
        "# Sports Data (ESPN Keyless)",
        f"_Last update: {_ts()}_",
        "",
        "## Summary",
        f"- **Events:** {pay.get('total_events', 0)} total, {pay.get('completed_events', 0)} completed",
        f"- **Leagues:** {len(pay.get('by_league', {}))}",
        f"- **Teams with form:** {len(form)}",
        f"- **Resolution index entries:** {len(idx)}",
        "",
        "## By League",
        "",
    ]

    for league, count in sorted(pay.get("by_league", {}).items(), key=lambda kv: -kv[1]):
        lines.append(f"- **{league.upper()}:** {count} events")

    lines.extend([
        "",
        "## Top Teams by Games Played",
        "",
    ])

    top_teams = sorted(form.items(), key=lambda kv: -(kv[1][0] + kv[1][1]))[:15]
    for team, (w, l) in top_teams:
        pct = round(100 * w / (w + l), 1) if (w + l) > 0 else 0
        lines.append(f"- **{team}:** {w}W-{l}L ({pct}%)")

    lines.append("")
    lines.append("## How to Query")
    lines.append("```python")
    lines.append("import sports_data")
    lines.append("form = sports_data.sports_team_form('TOR')  # [wins, losses]")
    lines.append("result = sports_data.sports_match_result('nba', 'NY', 'WSH', '2026-06-14')")
    lines.append("```")

    (VAULT_PATH / "sports_summary.md").write_text("\n".join(lines), encoding="utf-8")
    return len(pay.get("events", []))


def snapshot_brain_excerpt():
    """Latest excerpt from BRAIN.md (key sections)."""
    brain_path = Path.home() / "Documents/polymarket/BRAIN.md"
    if not brain_path.exists():
        return 0

    brain_text = brain_path.read_text(encoding="utf-8")
    # Extract key sections: Mission, Leg Registry, Decision Rules
    lines = [
        "# Bot Brain (Live Excerpt)",
        f"_Last update: {_ts()}_",
        "",
        "**Full source:** ~/Documents/polymarket/BRAIN.md",
        "",
    ]

    # Copy the first major section (Mission & Hard Rules)
    import re
    sections = re.split(r'^## ', brain_text, flags=re.MULTILINE)[1:]  # skip preamble
    if sections:
        lines.append(f"## {sections[0][:500]}")  # first 500 chars of first section

    (VAULT_PATH / "brain_snapshot.md").write_text("\n".join(lines), encoding="utf-8")
    return 1


def snapshot_calibration():
    """Goldsky V1 calibration screen: price bands vs actual YES rate + Wilson CI."""
    path = PM / "ml" / "dataset_goldsky.csv"
    if not path.exists():
        return 0
    rows = list(csv.DictReader(open(path)))
    buckets = {}
    for r in rows:
        try:
            p = float(r["prob"]); y = int(r["final_outcome"])
        except (ValueError, KeyError):
            continue
        buckets.setdefault(round(p * 10) / 10, []).append((p, y))
    lines = [
        "# Goldsky Calibration Screen",
        f"_Last update: {_ts()}_  ·  {len(rows)} resolved markets, fully on-chain (no Gamma)",
        "",
        "Is any price band mispriced? A band is a **real gap** only if the market price",
        "sits OUTSIDE the Wilson 95% CI of the actual YES-resolution rate.",
        "",
        "| band | n | pred | actual | actual 95% CI | flag |",
        "|------|---|------|--------|---------------|------|",
    ]
    n_real = 0
    for b in sorted(buckets):
        v = buckets[b]; n = len(v); k = sum(y for _, y in v)
        pred = sum(p for p, _ in v) / n
        lo, hi = _wilson(k, n)
        real = pred < lo or pred > hi
        n_real += int(real)
        lines.append(f"| {b:.1f} | {n} | {pred:.2f} | {k/n:.2f} | [{lo:.2f}, {hi:.2f}] | "
                     f"{'⚠️ REAL GAP' if real else 'calibrated'} |")
    lines += ["", (f"**{n_real} band(s) with a real gap.**" if n_real else
                   "**All bands calibrated → no naive fade edge.** Don't gate band hypotheses "
                   "(burns LLM calls on noise). 4th independent confirm that nearres died on "
                   "execution, not entry. See [[goldsky-calibration-null-2026-06-15]]."), ""]
    (VAULT_PATH / "calibration_goldsky.md").write_text("\n".join(lines), encoding="utf-8")
    return len(buckets)


def snapshot_analyst_gate():
    """Latest 3-lens adversarial verdicts on sourced analyst positions."""
    path = PM / "analyst_verdicts.csv"
    if not path.exists():
        return 0
    rows = list(csv.DictReader(open(path)))
    if not rows:
        return 0
    passed = sum(1 for r in rows if r.get("verdict") == "PASS")
    lines = [
        "# Analyst Gate — 3-Lens Adversarial Panel",
        f"_Last update: {_ts()}_  ·  {passed}/{len(rows)} survived (≥2/3 lenses failed to refute)",
        "",
        "Each sourced thesis is attacked by 3 independent lenses; PASS = the edge held.",
        "",
    ]
    for r in rows[-12:]:
        try:
            conf = float(r.get("confidence", 0))
        except ValueError:
            conf = 0.0
        mark = "✅ PASS" if r.get("verdict") == "PASS" else ("❌ FAIL" if r.get("verdict") == "FAIL" else "⚠️ ERR")
        lines.append(f"## {mark} ({conf:.2f}) — {r.get('claim','?')[:70]}")
        if r.get("notes"):
            for chunk in r["notes"].split(" || "):
                lines.append(f"- {chunk}")
        lines.append("")
    lines.append("See [[goldsky-calibration-null-2026-06-15]] — gate's real unit is a sourced thesis, not a single row.")
    (VAULT_PATH / "analyst_gate.md").write_text("\n".join(lines), encoding="utf-8")
    return len(rows)


def snapshot_crypto_touch():
    """Latest model-vs-market one-touch tape across all live crypto touch markets."""
    path = PM / "crypto_touch_daytrade.log"
    if not path.exists():
        return 0
    recs = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            recs.append(json.loads(line))
        except Exception:
            continue
    if not recs:
        return 0
    latest_ts = max(r["ts"] for r in recs)
    batch = [r for r in recs if r["ts"] == latest_ts and r.get("gap") is not None]
    batch.sort(key=lambda r: abs(r["gap"]), reverse=True)
    when = datetime.fromtimestamp(latest_ts, timezone.utc).isoformat()[:16]
    lines = [
        "# Crypto One-Touch — Model vs Market",
        f"_Last update: {_ts()}_  ·  tape marked {when}Z  ·  {len(batch)} live markets",
        "",
        "Each Polymarket dip/reach market is a one-touch contract. `model` = fair touch-prob",
        "at daily realized vol; `gap` = model − market YES. Persistent market-rich on far-OTM",
        "longshots = vol-risk premium. 15m scalping is spread-dominated (proven).",
        "",
        "| coin | dir | barrier | spot | vol | days | model | market | gap |",
        "|------|-----|---------|------|-----|------|-------|--------|-----|",
    ]
    for r in batch:
        lines.append(f"| {r['coin']} | {r['dir']} | {r['barrier']:g} | {r['spot']:g} | "
                     f"{r['vol']*100:.0f}% | {r['T_days']:.0f} | {r['model']:.3f} | "
                     f"{r['real']:.3f} | {r['gap']:+.3f} |")
    rich = [r for r in batch if r["gap"] < -0.10]
    lines += ["", f"**{len(rich)} markets market-rich vs realized-vol model (>10¢).** "
              "Signal = how these gaps move over time (this tape). See [[goldsky-calibration-null-2026-06-15]].", ""]
    (VAULT_PATH / "crypto_touch.md").write_text("\n".join(lines), encoding="utf-8")
    return len(batch)


def snapshot_decompose():
    """Mirror the gap decomposition (spot vs vol/time vs market-repricing) to the vault.
    Runs the crypto_touch_daytrade --decompose CLI; its own insufficient-data guard keeps
    it honest until the tape clears 200 marks / 48h."""
    import subprocess, sys
    script = PM / "crypto_touch_daytrade.py"
    if not script.exists():
        return 0
    try:
        out = subprocess.run([sys.executable, str(script), "--decompose"],
                             capture_output=True, text=True, timeout=90).stdout.strip()
    except Exception as e:
        out = f"(decompose failed: {e})"
    if not out:
        return 0
    lines = [
        "# Crypto Touch — Gap Decomposition",
        f"_Last update: {_ts()}_",
        "",
        "Attributes each market's gap movement to **spot** (BTC moved) vs **vol/time** vs",
        "**market-repricing** (`−Δreal`). Only the market-repricing term can be an edge —",
        "the gap must CLOSE via the market re-rating, not via spot drifting. Stays in",
        "insufficient-data mode until the tape clears 200 marks / 48h.",
        "",
        "```",
        out,
        "```",
        "",
        "See [[goldsky-calibration-null-2026-06-15]].",
    ]
    (VAULT_PATH / "crypto_touch_decompose.md").write_text("\n".join(lines), encoding="utf-8")
    return 1



def snapshot_basket_arb():
    """Verified mutually-exclusive basket locks (reusable engine, basket_arb.py)."""
    try:
        import basket_arb
        return basket_arb.snapshot_basket()
    except Exception:
        return 0

def snapshot_terminal():
    """Bloomberg-style ports terminal — live KPIs, leg P&L, mark-to-market movers, analyst desk.
    Reads ports_data.json emitted by gen_ports.py (the terminal generator) in the Codex project;
    the board itself serves at http://localhost:8077 via serve.sh (60s regen). Paper-only."""
    path = Path.home() / "Documents/Codex/2026-05-28/claud/ports_data.json"
    if not path.exists():
        return 0
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        (VAULT_PATH / "bloomberg_terminal.md").write_text(f"# Bloomberg Terminal — Ports\n\n⚠️ {e}\n", encoding="utf-8")
        return 0

    def usd(x):
        x = x or 0
        return ("+" if x >= 0 else "-") + "$" + f"{abs(x):.2f}"
    def px(p):
        return f"{p:.3f}" if isinstance(p, (int, float)) else "-"

    h = d.get("headline", {})
    legs = d.get("legs", []) or []
    pos = d.get("positions", []) or []
    analyst = d.get("analyst", []) or []
    wire = d.get("wire", []) or []
    marked = [p for p in pos if p.get("unreal") is not None]
    winners = sorted(marked, key=lambda p: -(p["unreal"]))[:6]
    losers = sorted(marked, key=lambda p: (p["unreal"]))[:6]

    lines = [
        "# Bloomberg Terminal — Ports",
        f"_Last update: {_ts()}_  ·  snapshot generated {d.get('generated_at', '?')}",
        "",
        "Read-only Bloomberg-style board over live `scalp_lab` state. Tool: "
        "`~/Documents/Codex/2026-05-28/claud/gen_ports.py` → `bloomberg_terminal.html`; "
        "live at `http://localhost:8077` via `serve.sh` (60s regen). Paper-only, no funds moved.",
        "",
        "## Headline",
        f"- **Realized:** {usd(h.get('realized_pnl'))}  ·  **Unrealized (MTM):** {usd(h.get('unreal_pnl'))}  ·  **Total:** {usd(h.get('total_pnl'))}",
        f"- **Open:** {h.get('total_open', 0)} ({h.get('marked', 0)} marked-to-market)  ·  **Gross exposure:** ${h.get('gross_exp', 0):.2f}",
        f"- **Closed exits:** {h.get('total_closed', 0)}  ·  **Blended WR:** {h.get('blended_wr', 0)}%  ·  **Legs tracked:** {h.get('n_legs', 0)}",
        f"- **Fear & Greed:** {h.get('fg_index', '?')} {h.get('fg_label', '')}  ·  **Analyst P&L:** {usd(h.get('analyst_pnl'))}",
        "",
        "## Leg P&L (controls MUST lose)",
        "",
        "| leg | open | closed | realized | WR |",
        "|-----|------|--------|----------|----|",
    ]
    for l in legs[:18]:
        lines.append(f"| `{l['name']}` | {l['open']} | {l['closed']} | {usd(l['realized'])} | {l['wr']}% |")

    lines += ["", "## Live movers (mark-to-market)", "", "**Top unrealized winners:**"]
    for p in winners:
        lines.append(f"- `{p['sym']}` {p['side']} `{p['strat']}` — {usd(p['unreal'])} ({p.get('unreal_pct', '?')}%) · px {px(p.get('cur'))} vs entry {px(p.get('entry'))}")
    lines += ["", "**Top unrealized losers:**"]
    for p in losers:
        lines.append(f"- `{p['sym']}` {p['side']} `{p['strat']}` — {usd(p['unreal'])} ({p.get('unreal_pct', '?')}%) · px {px(p.get('cur'))} vs entry {px(p.get('entry'))}")

    if analyst:
        lines += ["", "## Analyst desk (sourced bets)", ""]
        for a in analyst:
            lines.append(f"- **{a['sym']} {a['side']}** @ {px(a.get('entry'))} · edge +{a.get('edge')}pt · conv {a.get('conviction')} · ${a.get('size', 0):.0f}")
            lines.append(f"  - {a.get('q', '')}")

    if wire:
        lines += ["", "## Wire", ""]
        for w in wire[:6]:
            lines.append(f"- `{w['tag']}` {w['text']}")

    lines += ["", "See [[brain_snapshot]] · [[analyst_gate]] · [[belief_calibration]]."]
    (VAULT_PATH / "bloomberg_terminal.md").write_text("\n".join(lines), encoding="utf-8")
    return len(pos)


def snapshot_analysis_loggers():
    """Run the READ-ONLY ports analysis loggers (refresh snapshot + 7 loggers), which self-export to
    Reports/. Wired onto THIS working 6h job because the watchdog %30 path is launchd-blocked, so the
    loggers still auto-run + mirror. Also writes a vault hub note linking every logger report."""
    import subprocess
    script = PM / "run_analysis_loggers.sh"
    if not script.exists():
        return 0
    try:
        subprocess.run(["bash", str(script)], capture_output=True, text=True, timeout=240)
    except Exception:
        pass
    names = ["edge_screen", "bandcal", "legpnl", "exposure", "flow", "mover", "analyst_mtm", "controls"]
    present = [n for n in names if (PM / (n + "_log.jsonl")).exists()]
    lines = [
        "# Ports Analysis — Logger Hub",
        f"_Last update: {_ts()}_",
        "",
        "Read-only analysis loggers over the Bloomberg ports terminal (paper-only). Auto-run on this 6h "
        "job + on-demand via `run_analysis_loggers.sh`. `edge_screen` is the goal screen (bootstrap CI on $/exit).",
        "",
    ]
    for n in names:
        lines.append(f"- [[{n}]]" + ("" if n in present else "  _(no data yet)_"))
    lines += ["", "See [[bloomberg_terminal]]."]
    (VAULT_PATH / "ports_analysis_hub.md").write_text("\n".join(lines), encoding="utf-8")
    return len(present)


def snapshot_basketlock():
    """Basketlock paper LEG: captured locks + basketrand control + realized scoreboard
    (distinct from snapshot_basket_arb, which is the live scanner board)."""
    path = PM / "basketlock_state.json"
    if not path.exists():
        return 0
    st = json.loads(path.read_text())
    cap, ctrl, res = st.get("captured", []), st.get("control", []), st.get("resolved", [])
    real_res = [b for b in res if b.get("kind") == "real"]
    ctrl_res = [b for b in res if b.get("kind") == "control"]
    rp = sum(b.get("realized_pnl", 0) for b in real_res)
    cp = sum(b.get("realized_pnl", 0) for b in ctrl_res)
    exp = sum(c.get("exp_pnl", 0) for c in ctrl)
    lines = [
        "# Basketlock — risk-free arb leg",
        f"_Last update: {_ts()}_  ·  the one +EV thing: structural complete-set arbitrage (PAPER)",
        "",
        "## Scoreboard (held to resolution)",
        f"- **Real**: {len(real_res)} resolved, net ${rp:+.2f}  ·  {len(cap)} open lock(s)",
        f"- **Control (basketrand)**: {len(ctrl_res)} resolved net ${cp:+.2f}  ·  {len(ctrl)} open, "
        f"Sum expected ${exp:+.2f} (must stay <=0)",
        f"- **Gate**: {len(real_res)}/30 resolved real locks; passes at 30 + bootstrap CI>0 + control loses",
        "",
        "## Open locks (paper book)",
        "| edge | profit$ | deploy$ | N | resolve | event |",
        "|------|---------|---------|---|---------|-------|",
    ]
    for c in cap:
        lines.append(f"| {c['edge']*100:.1f}% | {c['locked_profit']:+.2f} | {c['usd_deployed']:.0f} | "
                     f"{c['n']} | {c['resolve']} | {c['title'][:40]} |")
    if not cap:
        lines.append("| - | - | - | - | - | (no executable locks clearing the bar now) |")
    lines += ["", "Honest expectation: positive but small (depth-capped). See [[deribit-options-2026-06-15]]."]
    (VAULT_PATH / "basketlock.md").write_text("\n".join(lines), encoding="utf-8")
    return len(cap) + len(ctrl)


def snapshot_xvenue():
    """Cross-venue arb scanner: captured Polymarket↔Kalshi locks + LLM-matcher cache stats."""
    sp = PM / "xvenue_arb_state.json"
    if not sp.exists():
        return 0
    st = json.loads(sp.read_text())
    cp = PM / "xvenue_match_cache.json"
    cache = json.loads(cp.read_text()) if cp.exists() else {}
    same = sum(1 for v in cache.values() if v == "SAME")
    diff = sum(1 for v in cache.values() if v == "DIFF")
    locks = sorted(st.get("locks", []), key=lambda l: -l["gap"])
    lines = [
        "# Cross-venue arb — Polymarket ↔ Kalshi",
        f"_Last update: {_ts()}_  ·  standing scanner, LLM semantic matcher (PAPER)",
        "",
        f"- **Captured locks**: {len(locks)}",
        f"- **Matcher cache**: {len(cache)} pairs verdicted ({same} same-event, {diff} different)",
        "",
        "## Captured locks",
        "| gap | buy YES @ | P | K | event |",
        "|-----|-----------|---|---|-------|",
    ]
    for l in locks[:20]:
        lines.append(f"| {l['gap']:.2f} | {l['buy_yes_on']} | {l['poly_yes']:.2f} | {l['kalshi_yes']:.2f} | {l['poly_q'][:42]} |")
    if not locks:
        lines.append("| - | - | - | - | (none yet — big gaps are 'win vs run' artifacts; real matches price efficiently) |")
    lines += ["", "Finding 2026-06-15: no live xvenue arb — structural question-mismatch, not inefficiency. "
              "Scanner waits for a rare real divergence. See [[basketlock-arb-leg-2026-06-15]]."]
    (VAULT_PATH / "xvenue.md").write_text("\n".join(lines), encoding="utf-8")
    return len(cache)


def snapshot_venue_watch():
    """Venue-watch sentinel — the 'change the venue' branch. Watches thinner venues (Limitless/Base)
    keylessly for the day a real non-coinflip / negRisk market appears (→ point basketlock at it).
    Today: all coinflips → quiet. Mirrors venue_watch.py's latest scan + the standing-agent re-scan path."""
    lp = PM / "venue_watch_log.jsonl"
    sp = PM / "venue_watch_state.json"
    if not lp.exists() and not sp.exists():
        return 0
    last = {}
    if lp.exists():
        rows = [l for l in lp.read_text().splitlines() if l.strip()]
        if rows:
            try: last = json.loads(rows[-1])
            except Exception: last = {}
    seen = 0
    if sp.exists():
        try: seen = len(json.loads(sp.read_text()).get("seen", []))
        except Exception: pass
    fresh = last.get("fresh", 0)
    status = ("🆕 EMERGENCE — " + ", ".join(last.get("fresh_titles", []))) if fresh else \
             "quiet (all coinflips, nothing to capture)"
    lines = [
        "# Venue watch — the \"change the venue\" branch",
        f"_Last update: {_ts()}_  ·  read-only keyless sentinel (PAPER), watchdog 6h",
        "",
        "> Polymarket is too calibrated for the bot's structural arb. This watches THINNER venues for "
        "the day a real non-coinflip / negRisk market appears — then point [[basketlock]] at it. "
        "**Watch, don't build.**",
        "",
        f"- **Limitless (Base) last scan**: {last.get('total','?')} active · "
        f"{last.get('qualifying',0)} non-coinflip · {fresh} fresh",
        f"- **Baselined markets tracked**: {seen}",
        f"- **Status**: {status}",
        "",
        "## Verdict (2026-06-18) — no buildable venue edge today",
        "- **Kalshi↔Polymarket** = dead ([[23 Cross-venue Poly-Kalshi arb is unrealizable|Lesson 23]]).",
        "- **Limitless** = keyless read works, but live book = 100% crypto coinflips, 0 negRisk groups.",
        "- **Hyperliquid HIP-4** = best keyless CLOB, multi-outcome not shipped → the one to watch.",
        "",
        "Re-scan with the standing agents `pm-venue-scout` ×4 → `pm-venue-ranker` "
        "(workflow `venue-edge-sweep.js`, by scriptPath). See [[Agents]].",
    ]
    (VAULT_PATH / "venue_watch.md").write_text("\n".join(lines), encoding="utf-8")
    return fresh + 1


def snapshot_funding_basis():
    """funding_basis carry probe — delta-neutral perp-funding harvest (the non-predictive 'trade
    crypto' lane). Mirrors open/resolved holds + the n>=30 REAL + CI>0 + control gate. Dormant by
    design (fee-gated → fires only in high-funding regimes)."""
    sp = PM / "funding_basis_state.json"
    if not sp.exists():
        return 0
    st = json.loads(sp.read_text())
    res = st.get("resolved", [])
    real = [r for r in res if r.get("kind") == "real"]
    ctrl = [r for r in res if r.get("kind") == "control"]
    rp = sum(r.get("pnl", 0) for r in real)
    cp = sum(r.get("pnl", 0) for r in ctrl)
    lines = [
        "# funding_basis — delta-neutral carry probe",
        f"_Last update: {_ts()}_  ·  PAPER, read-only ccxt, fee-gated (NOT a directional bet)",
        "",
        "> Harvests perp funding on the side that RECEIVES it (delta-neutral; direction set from the "
        "OBSERVED funding sign, never a forecast). The honest 'trade crypto' lane — directional crypto "
        "is the dead taker pattern. Held MIN_HOLD_DAYS to amortize fees.",
        "",
        f"- **Open carries**: {len(st.get('open', []))}",
        f"- **Resolved REAL**: {len(real)}/30 toward gate · ${rp:+.3f}",
        f"- **Control (pay-carry, MUST lose)**: {len(ctrl)} · ${cp:+.3f}",
        f"- **Last scan**: {st.get('last_scan') or '—'}",
        "",
        "GATE: n≥30 REAL holds AND bootstrap CI>0 AND mean>control AND control≤0. Dormant-rare is "
        "EXPECTED, not broken ([[25 A failed fetch is not an empty result (null is not zero, both directions)|Lesson 25]]). "
        "Regime data: [[funding_landscape]]. See [[Loggers and Probes]].",
    ]
    (VAULT_PATH / "funding_basis.md").write_text("\n".join(lines), encoding="utf-8")
    return len(real) + 1


def snapshot_scout():
    """Market scout candidates — pre-screened, drafted, and quick-gated."""
    sp = PM / "scout_candidates.json"
    if not sp.exists():
        return 0
    st = json.loads(sp.read_text())
    cands = st.get("candidates", [])
    gate_passes = [c for c in cands if c.get("gate_pass") and not c.get("promoted")]
    promoted = [c for c in cands if c.get("promoted")]
    pending = [c for c in cands if not c.get("promoted") and not c.get("gate_pass") and c.get("draft")]
    last = st.get("last_scan")
    last_str = datetime.fromtimestamp(last, tz=timezone.utc).strftime("%Y-%m-%d %H:%MZ") if last else "never"
    lines = [
        "# Market Scout — AI Research Agent",
        f"_Last scan: {last_str}_  ·  pipeline: Gamma → haiku prescreen → opus draft → haiku quick-gate",
        "",
        f"- **Gate-passing candidates**: {len(gate_passes)} (ready to promote)",
        f"- **Promoted to book**: {len(promoted)}",
        f"- **Pending / draft-failed**: {len(pending)}",
        "",
        "## Gate-Passing Candidates (promote with `--promote N`)",
        "| # | side | yes | edge | conf | days | thesis |",
        "|---|------|-----|------|------|------|--------|",
    ]
    for i, c in enumerate(gate_passes[:10]):
        d = c.get("draft") or {}
        lines.append(f"| {i} | {d.get('side','?')} | {c['yes']:.2f} | {d.get('edge_pct','?')}pts "
                     f"| {d.get('confidence','?')}/10 | {c['days']}d | {(d.get('thesis',''))[:55]} |")
    if not gate_passes:
        lines.append("| - | - | - | - | - | - | (no gate-passing candidates yet) |")
    lines += ["", "## Recently Promoted", "| side | yes | q |", "|------|-----|---|"]
    for c in promoted[-5:]:
        d = c.get("draft") or {}
        lines.append(f"| {d.get('side','?')} | {c['yes']:.2f} | {c['q'][:55]} |")
    if not promoted:
        lines.append("| - | - | (none yet) |")
    (VAULT_PATH / "scout_candidates.md").write_text("\n".join(lines), encoding="utf-8")
    return len(cands)


def snapshot_position_monitor():
    """Latest status for each open analyst position from position_monitor.log."""
    log = PM / "position_monitor.log"
    if not log.exists():
        return 0
    rows = {}
    for line in open(log):
        try:
            r = json.loads(line)
            rows[r["q"]] = r
        except Exception:
            continue
    lines = [
        "# Position Monitor",
        f"_Last update: {_ts()}_  ·  30min watchdog, haiku action recommendation",
        "",
        f"- **Positions watched**: {len(rows)}",
        f"- **INVESTIGATE/EXIT alerts**: {sum(1 for r in rows.values() if r.get('action') in ('INVESTIGATE','EXIT'))}",
        "",
        "## Current Status",
        "| action | side | entry | curr | drift | news | question |",
        "|--------|------|-------|------|-------|------|----------|",
    ]
    for q, r in sorted(rows.items(), key=lambda x: x[1].get("action","HOLD")):
        mark = "🟢" if r["action"]=="HOLD" else ("🟡" if r["action"]=="INVESTIGATE" else "🔴")
        drift = r.get("drift")
        drift_str = f"{drift:+.3f}" if drift is not None else "?"
        lines.append(f"| {mark}{r['action']} | {r.get('side','?')} | {r.get('entry_yes','?')} "
                     f"| {r.get('curr','?')} | {drift_str} | {r.get('n_news',0)} | {q[:42]} |")
    if not rows:
        lines.append("| - | - | - | - | - | - | (no data yet) |")
    (VAULT_PATH / "position_monitor.md").write_text("\n".join(lines), encoding="utf-8")
    return len(rows)


def snapshot_hypothesis_lab():
    """Active hypothesis seeds from hypothesis_lab.py."""
    sf = PM / "hypothesis_seeds.json"
    if not sf.exists():
        return 0
    st = json.loads(sf.read_text())
    seeds = st.get("seeds", [])
    active = [s for s in seeds if not s.get("used")]
    lines = [
        "# Hypothesis Lab",
        f"_Last run: {st.get('generated_at','never')}_  ·  weekly opus synthesis → scout search queries",
        "",
        f"- **Total seeds generated**: {len(seeds)} across {st.get('runs',0)} runs",
        f"- **Active (unused)**: {len(active)}",
        "",
        "## Active Seeds",
        "| id | direction | category | conf | signal | scout query |",
        "|----|-----------|----------|------|--------|-------------|",
    ]
    for s in active[-10:]:
        lines.append(f"| {s['id']} | {s.get('direction','?')} | {s.get('category','?')} "
                     f"| {s.get('confidence','?')}/10 | {s.get('signal','')[:40]} "
                     f"| {s.get('market_search_query','')[:35]} |")
    if not active:
        lines.append("| - | - | - | - | - | (run hypothesis_lab.py to generate) |")
    (VAULT_PATH / "hypothesis_lab.md").write_text("\n".join(lines), encoding="utf-8")
    return len(active)


def snapshot_internal_consistency():
    """Logical price constraint violations across related markets."""
    path = PM / "consistency_violations.json"
    if not path.exists():
        return 0
    st = json.loads(path.read_text())
    vs = st.get("violations", [])
    strong = [v for v in vs if v.get("severity") == "STRONG" and v.get("executable") == "YES"]
    lines = [
        "# Internal Consistency Agent",
        f"_Last scan: {st.get('last_scan','never')}_  ·  logical price constraint violations (pure arb)",
        "",
        f"- **Violations**: {len(vs)}  ·  **STRONG + executable**: {len(strong)}",
        "",
        "## Latest Violations",
        "| severity | type | gap | exec | market A |",
        "|----------|------|-----|------|----------|",
    ]
    for v in vs[-10:]:
        sev = v.get("severity","?")
        mark = "🔴" if sev == "STRONG" else ("🟡" if sev == "MODERATE" else "⚪")
        lines.append(f"| {mark}{sev:8} | {v.get('type','?'):12} | {v.get('gap','?'):>5} "
                     f"| {v.get('executable','?'):3} | {v.get('market_a','?')[:45]} |")
        if v.get("constraint"):
            lines.append(f"|  | | | | _{v['constraint'][:75]}_ |")
        if v.get("action"):
            lines.append(f"|  | | | | **trade**: {v['action'][:65]} |")
    if not vs:
        lines.append("| - | - | - | - | (no violations yet — runs every 6h) |")
    lines += ["", "Pure arb: no directional bet — buy/sell the constraint gap. "
              "See [[basketlock-arb-leg-2026-06-15]]."]
    (VAULT_PATH / "internal_consistency.md").write_text("\n".join(lines), encoding="utf-8")
    return len(vs)


def snapshot_attention_decay():
    """Attention-decay fade signals — stale news premium not yet unwound."""
    path = PM / "attention_decay_signals.json"
    if not path.exists():
        return 0
    st = json.loads(path.read_text())
    sigs = st.get("signals", [])
    high = [s for s in sigs if s.get("confidence") == "HIGH"]
    lines = [
        "# Attention Decay Agent",
        f"_Last scan: {st.get('last_scan','never')}_  ·  dying news + stale price = fade (NO) opportunity",
        "",
        f"- **Signals**: {len(sigs)}  ·  **HIGH confidence**: {len(high)}",
        "",
        "## Latest Decay Signals",
        "| conf | action | velocity | overpricing | market |",
        "|------|--------|----------|-------------|--------|",
    ]
    for s in sigs[-10:]:
        c = s.get("confidence","?")
        mark = "🔴" if c == "HIGH" else ("🟡" if c == "MEDIUM" else "⚪")
        lines.append(f"| {mark}{c:6} | {s.get('action','?'):12} | {s.get('velocity_now','?'):10} "
                     f"| {s.get('overpricing','?'):>8} | {s.get('market','?')[:42]} |")
        if s.get("last_catalyst"):
            lines.append(f"|  | | | | _(was)_ {s['last_catalyst'][:65]} |")
    if not sigs:
        lines.append("| - | - | - | - | (no signals yet — runs every 5h) |")
    lines += ["", "Inverse of [[news_velocity_agent]]. See [[confluence_agent]]."]
    (VAULT_PATH / "attention_decay.md").write_text("\n".join(lines), encoding="utf-8")
    return len(sigs)


def snapshot_anchor_hunter():
    """Behavioral anchor signals — round-number price stickiness about to snap."""
    path = PM / "anchor_signals.json"
    if not path.exists():
        return 0
    st = json.loads(path.read_text())
    sigs = st.get("signals", [])
    strong = [s for s in sigs if s.get("anchor_quality") == "STRONG"]
    lines = [
        "# Anchor Hunter Agent",
        f"_Last scan: {st.get('last_scan','never')}_  ·  round-number psychological anchors about to snap",
        "",
        f"- **Anchors found**: {len(sigs)}  ·  **STRONG**: {len(strong)}",
        "",
        "## Latest Anchor Signals",
        "| quality | anchor | curr | snap | mag | time | market |",
        "|---------|--------|------|------|-----|------|--------|",
    ]
    for s in sigs[-10:]:
        aq = s.get("anchor_quality","?")
        mark = "🔴" if aq == "STRONG" else ("🟡" if aq == "MODERATE" else "⚪")
        lines.append(f"| {mark}{aq:8} | {s.get('anchor_price','?'):>5} "
                     f"| {s.get('current_yes','?'):>5} | {s.get('snap_direction','?'):5} "
                     f"| {s.get('snap_magnitude','?'):>5} | {s.get('time_pressure','?'):6} "
                     f"| {s.get('market','?')[:38]} |")
        if s.get("catalyst") and s["catalyst"] != "none — time pressure only":
            lines.append(f"|  | | | | | | _{s['catalyst'][:68]}_ |")
    if not sigs:
        lines.append("| - | - | - | - | - | - | (no anchors yet — runs every 4h) |")
    lines += ["", "Behavioral edge: anchors snap fast when pressure builds. "
              "See [[confluence_agent]] · [[news_velocity_agent]]."]
    (VAULT_PATH / "anchor_hunter.md").write_text("\n".join(lines), encoding="utf-8")
    return len(sigs)


def snapshot_liquidity_scanner():
    """CLOB sweep signals — informed positioning via spread/imbalance detection."""
    path = PM / "liquidity_signals.json"
    if not path.exists():
        return 0
    st = json.loads(path.read_text())
    sigs = st.get("signals", [])
    strong = [s for s in sigs if s.get("strength") == "STRONG"]
    lines = [
        "# Liquidity Scanner Agent",
        f"_Last scan: {st.get('last_scan','never')}_  ·  CLOB sweep: spread + bid/ask depth imbalance",
        "",
        f"- **Signals**: {len(sigs)}  ·  **STRONG**: {len(strong)}",
        "",
        "## Latest Signals",
        "| strength | type | spread | imbal | action | market |",
        "|----------|------|--------|-------|--------|--------|",
    ]
    for s in sigs[-10:]:
        st_ = s.get("strength","?")
        mark = "🔴" if st_ == "STRONG" else ("🟡" if st_ == "MODERATE" else "⚪")
        lines.append(f"| {mark}{st_:8} | {s.get('type','?'):12} | {s.get('spread','?'):>6} "
                     f"| {s.get('imbalance','?'):>5} | {s.get('action','?'):14} "
                     f"| {s.get('market','?')[:40]} |")
        if s.get("interpretation"):
            lines.append(f"|  | | | | | _{s['interpretation'][:75]}_ |")
    if not sigs:
        lines.append("| - | - | - | - | - | (no signals yet — runs every 4h) |")
    (VAULT_PATH / "liquidity_scanner.md").write_text("\n".join(lines), encoding="utf-8")
    return len(sigs)


def snapshot_news_velocity():
    """News velocity signals — acceleration vs market price lag."""
    path = PM / "news_velocity_signals.json"
    if not path.exists():
        return 0
    st = json.loads(path.read_text())
    sigs = st.get("signals", [])
    enters = [s for s in sigs if s.get("action") == "ENTER"]
    lines = [
        "# News Velocity Agent",
        f"_Last scan: {st.get('last_scan','never')}_  ·  hermes velocity → price lag detection",
        "",
        f"- **Signals**: {len(sigs)}  ·  **ENTER**: {len(enters)}",
        "",
        "## Latest Signals",
        "| trend | accel | book | gap | action | market |",
        "|-------|-------|------|-----|--------|--------|",
    ]
    for s in sigs[-10:]:
        tr = s.get("trend","?")
        mark = "🔴" if tr == "ACCELERATING" else ("🟡" if tr == "ELEVATED" else "⚪")
        lines.append(f"| {mark}{str(tr or '?'):13} | {str(s.get('accel') or '?'):>5} | {str(s.get('book_status') or '?'):10} "
                     f"| {str(s.get('gap') or '?'):>6} | {str(s.get('action') or '?'):6} "
                     f"| {s.get('market','?')[:40]} |")
        if s.get("news_lead"):
            lines.append(f"|  | | | | | _{s['news_lead'][:75]}_ |")
    if not sigs:
        lines.append("| - | - | - | - | - | (no signals yet — runs every 2h) |")
    (VAULT_PATH / "news_velocity_agent.md").write_text("\n".join(lines), encoding="utf-8")
    return len(sigs)


def snapshot_confluence():
    """Confluence signals — news-accel + CLOB-imbalance both pointing same direction."""
    path = PM / "confluence_signals.json"
    if not path.exists():
        return 0
    st = json.loads(path.read_text())
    sigs = st.get("signals", [])
    tier1 = [s for s in sigs if s.get("quality") == "TIER1"]
    lines = [
        "# Confluence Agent",
        f"_Last scan: {st.get('last_scan','never')}_  ·  news-accel + CLOB-imbalance = highest confidence",
        "",
        f"- **Signals**: {len(sigs)}  ·  **TIER1**: {len(tier1)}",
        "",
        "## Latest Signals (TIER1 first)",
        "| quality | align | news | book | edge | side | market |",
        "|---------|-------|------|------|------|------|--------|",
    ]
    sorted_sigs = sorted(sigs[-15:], key=lambda s: ("TIER1","TIER2","TIER3").index(s.get("quality","TIER3")) if s.get("quality") in ("TIER1","TIER2","TIER3") else 3)
    for s in sorted_sigs:
        q = s.get("quality","?")
        mark = "🔴" if q == "TIER1" else ("🟡" if q == "TIER2" else "⚪")
        lines.append(f"| {mark}{q} | {s.get('alignment','?'):10} | {s.get('news_accel','?'):>5} "
                     f"| {s.get('book_signal','?'):12} | {s.get('edge_pts','?'):>6} "
                     f"| {s.get('side','?')} | {s.get('market','?')[:38]} |")
        if s.get("catalyst"):
            lines.append(f"|  | | | | | | _{s['catalyst'][:70]}_ |")
    if not sigs:
        lines.append("| - | - | - | - | - | - | (no signals yet — runs every 3h) |")
    lines += ["", "See [[liquidity_scanner]] · [[news_velocity_agent]] · [[breaking_news_agent]]."]
    (VAULT_PATH / "confluence_agent.md").write_text("\n".join(lines), encoding="utf-8")
    return len(sigs)


def snapshot_breaking_news():
    """Breaking news agent signals — real-time news-to-market gaps."""
    path = PM / "breaking_news_signals.json"
    if not path.exists():
        return 0
    st = json.loads(path.read_text())
    sigs = st.get("signals", [])
    hot = [s for s in sigs if s.get("urgency") in ("CRITICAL","HIGH")]
    lines = [
        "# Breaking News Agent",
        f"_Last scan: {st.get('last_scan','never')}_  ·  hermes 52-source feed → market impact gaps",
        "",
        f"- **Total signals**: {len(sigs)}  ·  **CRITICAL/HIGH**: {len(hot)}",
        "",
        "## Latest Signals",
        "| urgency | action | side | move | market |",
        "|---------|--------|------|------|--------|",
    ]
    for s in sigs[-10:]:
        urg = s.get("urgency","?")
        mark = "🔴" if urg == "CRITICAL" else ("🟡" if urg == "HIGH" else "⚪")
        lines.append(f"| {mark}{urg} | {s.get('action','?')} | {s.get('side','?')} "
                     f"| {s.get('implied_move','?')} | {s.get('market','?')[:42]} |")
        if s.get("news_headline"):
            lines.append(f"|  | | | | _{s['news_headline'][:75]}_ |")
    if not sigs:
        lines.append("| - | - | - | - | (no signals yet — runs hourly) |")
    (VAULT_PATH / "breaking_news_agent.md").write_text("\n".join(lines), encoding="utf-8")
    return len(sigs)


def snapshot_thesis_critic():
    """Thesis critic verdicts — 3-lens pre-entry quality gate."""
    path = PM / "thesis_critic_verdicts.json"
    if not path.exists():
        return 0
    st = json.loads(path.read_text())
    vs = st.get("verdicts", [])
    promotes = sum(1 for v in vs if v.get("overall") == "PROMOTE")
    kills = sum(1 for v in vs if v.get("overall") == "KILL")
    lines = [
        "# Thesis Critic Agent",
        f"_Last update: {_ts()}_  ·  3-lens attack: statistician + contrarian + resolution",
        "",
        f"- **Verdicts**: {len(vs)}  ·  **PROMOTE**: {promotes}  ·  **KILL**: {kills}",
        "",
        "## Latest Verdicts",
        "| overall | 1:stat | 2:contra | 3:res | side | question |",
        "|---------|--------|----------|-------|------|----------|",
    ]
    for v in vs[-10:]:
        ov = v.get("overall","?")
        mark = "✅" if ov == "PROMOTE" else ("❌" if ov == "KILL" else "⚠️")
        s1 = "✓" if v.get("lens1") == "SURVIVES" else "✗"
        s2 = "✓" if v.get("lens2") == "SURVIVES" else "✗"
        s3 = "✓" if v.get("lens3") == "SURVIVES" else "✗"
        lines.append(f"| {mark}{ov:8} | {s1} | {s2} | {s3} "
                     f"| {v.get('side','?')} | {v.get('q','?')[:42]} |")
        if v.get("fatal_flaw") and v["fatal_flaw"].lower() not in ("none","none found"):
            lines.append(f"|  | | | | | ⚠️ _{v['fatal_flaw'][:75]}_ |")
    if not vs:
        lines.append("| - | - | - | - | - | (no verdicts yet) |")
    (VAULT_PATH / "thesis_critic_agent.md").write_text("\n".join(lines), encoding="utf-8")
    return len(vs)


def snapshot_opportunity_ranker():
    """Latest cross-agent ranked priority list from opportunity_ranker_agent."""
    md = PM / "opportunity_ranking_latest.md"
    if not md.exists():
        return 0
    content = md.read_text(encoding="utf-8")
    (VAULT_PATH / "opportunity_ranker.md").write_text(content, encoding="utf-8")
    return 1


def snapshot_deep_research():
    """Deep research agent reports (tool-use agentic deep-diver on scout candidates)."""
    path = PM / "deep_research_reports.json"
    if not path.exists():
        return 0
    st = json.loads(path.read_text())
    reps = st.get("reports", [])
    pursue = [r for r in reps if r.get("action") == "PURSUE"]
    lines = [
        "# Deep Research Agent",
        f"_Last update: {_ts()}_  ·  Claude tool-use: fetch_market + search_news + order_book",
        "",
        f"- **Reports**: {len(reps)} total  ·  **PURSUE**: {len(pursue)}",
        "",
        "## Latest Reports",
        "| action | conf | edge | query |",
        "|--------|------|------|-------|",
    ]
    for r in reps[-10:]:
        mark = "✅" if r.get("action") == "PURSUE" else ("❌" if r.get("action") == "PASS" else "⚠️")
        lines.append(f"| {mark}{r.get('action','?')} | {r.get('confidence','?')} "
                     f"| {r.get('edge','?')} | {r.get('query','?')[:50]} |")
        if r.get("thesis"):
            lines.append(f"|  | | | _{r['thesis'][:80]}_ |")
    if not reps:
        lines.append("| - | - | - | (no reports yet — run deep_research_agent.py --auto) |")
    (VAULT_PATH / "deep_research_agent.md").write_text("\n".join(lines), encoding="utf-8")
    return len(reps)


def snapshot_defender_verdicts():
    """Position defender agent verdicts (adversarial HOLD/REDUCE/EXIT analysis)."""
    path = PM / "defender_verdicts.json"
    if not path.exists():
        return 0
    st = json.loads(path.read_text())
    vs = st.get("verdicts", [])
    exits = [v for v in vs if v.get("action") == "EXIT"]
    reduces = [v for v in vs if v.get("action") == "REDUCE"]
    lines = [
        "# Position Defender Agent",
        f"_Last update: {_ts()}_  ·  Claude tool-use adversarial: defaults to EXIT unless evidence is contrary",
        "",
        f"- **Verdicts**: {len(vs)} total  ·  **EXIT**: {len(exits)}  ·  **REDUCE**: {len(reduces)}",
        "",
        "## Latest Verdicts",
        "| action | status | conf | side | question |",
        "|--------|--------|------|------|----------|",
    ]
    for v in vs[-10:]:
        act = v.get("action","?")
        mark = "🔴" if act == "EXIT" else ("🟡" if act == "REDUCE" else "🟢")
        lines.append(f"| {mark}{act} | {v.get('thesis_status','?')} | {v.get('confidence','?')} "
                     f"| {v.get('side','?')} | {v.get('q','?')[:45]} |")
        if v.get("key_finding"):
            lines.append(f"|  | | | | _{v['key_finding'][:80]}_ |")
    if not vs:
        lines.append("| - | - | - | - | (no verdicts yet — triggers after position_monitor INVESTIGATE) |")
    (VAULT_PATH / "position_defender.md").write_text("\n".join(lines), encoding="utf-8")
    return len(vs)


def snapshot_edge_hunter():
    """Edge hunter agent dossiers (hypothesis-seed-driven opportunity finder)."""
    path = PM / "edge_hunter_dossiers.json"
    if not path.exists():
        return 0
    st = json.loads(path.read_text())
    ds = st.get("dossiers", [])
    confirmed = [d for d in ds if d.get("signal_confirmed") == "YES"]
    lines = [
        "# Edge Hunter Agent",
        f"_Last hunt: {st.get('last_hunt','never')}_  ·  hypothesis seeds → list_markets → fetch → news → dossier",
        "",
        f"- **Dossiers**: {len(ds)} total  ·  **Signal confirmed**: {len(confirmed)}",
        "",
        "## Latest Dossiers",
        "| side | edge | signal | risk | seed | market |",
        "|------|------|--------|------|------|--------|",
    ]
    for d in ds[-10:]:
        sig = d.get("signal_confirmed", "?")
        mark = "✅" if sig == "YES" else ("⚠️" if sig == "PARTIAL" else "❌")
        lines.append(f"| {d.get('side','?')} | {d.get('edge','?')} | {mark}{sig} "
                     f"| {d.get('risk','?')} | {d.get('seed_id','?')} | {d.get('market','?')[:45]} |")
        if d.get("thesis"):
            lines.append(f"|  | | | | | _{d['thesis'][:80]}_ |")
    if not ds:
        lines.append("| - | - | - | - | - | (no dossiers yet — run edge_hunter_agent.py) |")
    lines += ["", "Feeds from [[hypothesis_lab]] seeds → promotes to [[scout_candidates]] on signal=YES."]
    (VAULT_PATH / "edge_hunter_agent.md").write_text("\n".join(lines), encoding="utf-8")
    return len(ds)


def snapshot_structural_arb():
    """ONE combined page for the 3 NON-PREDICTIVE +EV sources (basket + data + mono) and the
    multiarb combined-CI gate. The only road to profit on a calibrated venue, in one vault note.
    Every sub-pull is fail-soft so a slow/blank source never breaks the page."""
    import json as _json
    L = [f"# Structural Arb — 3 sources + combined", f"_updated {_ts()}_", "",
         "The only +EV-by-construction track: locked/structural edges, never predicting calibrated",
         "mids. basket=combinatorial lock · data=settled-mispricing · mono=monotonicity violation.", ""]

    # 1. basket — live locked baskets (slow CLOB call; fail-soft)
    L += ["## basket arb — live locked baskets", ""]
    try:
        import basket_arb
        locks = sorted(basket_arb.get_locked_baskets(min_edge=0.0), key=lambda b: -b.get("edge", 0))[:10]
        if locks:
            L += ["| edge | kind | Σ | basket |", "|------|------|---|--------|"]
            for b in locks:
                s = b.get("sum_ask") if b.get("kind") == "LONG" else b.get("sum_bid")
                L.append(f"| {b.get('edge',0):+.1%} | {b.get('kind','?')} | {s} | {str(b.get('slug',''))[:46]} |")
            L.append("\n_Real outcome-independent edges, but thin — most gone by ~$200 size (see [[basket_depth]])._")
        else:
            L.append("_no live locks this pull._")
    except Exception as e:
        L.append(f"_basket live fetch failed: {str(e)[:80]}_")
    L.append("")

    # 2. data — settled-but-mispriced
    L += ["## data arb — settled-but-mispriced", ""]
    try:
        st = _json.load(open(str(Path.home() / "Documents/polymarket/dataarb_state.json")))
        nr = sum(1 for p in st.get("resolved", []) if p.get("kind") == "real")
        L.append(f"- open **{len(st.get('open',[]))}** · resolved {len(st.get('resolved',[]))} (real {nr}) · last scan {st.get('last_scan')}")
        try:
            sig = _json.load(open(str(Path.home() / "Documents/polymarket/datagate_arbs.json")))
            L.append(f"- live data-gate ARB signals: **{len(sig.get('signals',[]))}** (0 = no settled-mispricing right now)")
        except Exception:
            L.append("- live data-gate ARB signals: (none cached)")
    except Exception as e:
        L.append(f"_dataarb state read failed: {str(e)[:80]}_")
    L.append("")

    # 3. mono — monotonicity/consistency arb
    L += ["## mono arb — monotonicity/consistency", ""]
    try:
        st = _json.load(open(str(Path.home() / "Documents/polymarket/monoarb_state.json")))
        reals = sum(1 for p in st.get("open", []) if p.get("kind") == "real")
        ctrls = sum(1 for p in st.get("open", []) if p.get("kind") == "control")
        L.append(f"- capturable locks (>fee): **{reals}** · control pairs {ctrls} · resolved {len(st.get('resolved',[]))} · last scan {st.get('last_scan')}")
        L.append("- 0 capturable is EXPECTED — liquid ladders are monotonic; live violations are sub-2%-fee.")
    except Exception as e:
        L.append(f"_monoarb state read failed: {str(e)[:80]}_")
    L.append("")

    # 4. combined — multiarb gate
    L += ["## combined — multiarb gate", ""]
    try:
        import multiarb
        s = multiarb.build()
        r, c = s["combined_real"], s["combined_control"]
        L.append(f"- combined REAL n=**{r['n']}** mean ${r['mean'] if r['mean'] is not None else 0:+.4f} total ${r['total']:+.2f}")
        if r["ci"][0] is not None:
            L.append(f"  - bootstrap CI [{r['ci'][0]:+.3f}, {r['ci'][1]:+.3f}]")
        L.append(f"- control n={c['n']} mean ${c['mean'] if c['mean'] is not None else 0:+.4f}")
        g = s["gate"]
        verdict = ("✅ GRADUATE" if g.get("GRADUATE") else
                   "accumulating" if r["n"] == 0 else "not yet")
        L.append(f"- **gate: {verdict}** (need n≥30 + CI>0 + beats losing control)")
        L.append(f"- sub-legs: basket {s['sub_legs']['basket_real']['n']} · data {s['sub_legs']['data_real']['n']} · mono {s['sub_legs']['mono_real']['n']} exits")
    except Exception as e:
        L.append(f"_multiarb build failed: {str(e)[:80]}_")
    L += ["", "Links: [[basket_arb]] · [[basket_paper]] · [[analyst_data_gate]] · [[multiarb]] · [[monoarb]]"]

    (VAULT_PATH / "structural_arb.md").write_text("\n".join(L), encoding="utf-8")
    return 3


def snapshot_crypto_trader():
    """Mirror the crypto-trader bot's live state into Bot/crypto_trader_live.md
    and a gate-view note at Edges/crypto-trader.md.

    Reads from ~/trader/data/journal.db (SQLite) — pure file-based, no API.
    Two outputs:
      - Bot/crypto_trader_live.md  — last fills/signals + current equity snapshot
      - Edges/crypto-trader.md     — gate bar (n≥30 + CI>0 + control-loses)
    Idempotent, fail-soft.
    """
    JOURNAL_DB = Path.home() / "trader" / "data" / "journal.db"
    if not JOURNAL_DB.exists():
        # Bot not running or no journal yet — write a placeholder so Obsidian
        # doesn't render a broken backlink.
        lines = [
            "---",
            "type: subsystem",
            "status: idle",
            "tags: [crypto, side-system]",
            "updated: " + _ts(),
            "---",
            "",
            "# Crypto Trader — Live State",
            "",
            "_Bot is not running or journal does not exist yet._",
            "",
            f"_Last checked: {_ts()}_",
            "",
            "## How to start it",
            "```bash",
            "cd ~/trader",
            "uv run python -m src.main",
            "```",
            "",
            "→ [[Bot/crypto_trader]] · [[memory/crypto-trader-2026-06-22]]",
        ]
        (VAULT_PATH / "crypto_trader_live.md").write_text("\n".join(lines), encoding="utf-8")
        return 0

    import sqlite3
    conn = None
    try:
        conn = sqlite3.connect(str(JOURNAL_DB))
        conn.row_factory = sqlite3.Row

        # Latest equity snapshot
        eq_row = conn.execute(
            "SELECT ts, equity, cash, positions_value, realized_pnl, "
            "unrealized_pnl, drawdown FROM equity_snapshots ORDER BY id DESC LIMIT 1"
        ).fetchone()

        # Recent fills (last 20)
        fills = conn.execute(
            "SELECT ts, pair, side, qty, price, fee, mode FROM fills "
            "ORDER BY id DESC LIMIT 20"
        ).fetchall()

        # Recent signals (last 20)
        signals = conn.execute(
            "SELECT ts, pair, side, strength, reason FROM signals "
            "ORDER BY id DESC LIMIT 20"
        ).fetchall()

        # Stats: n trades, win rate, total P&L (synthesized from fills via round-trip pairing)
        n_fills = conn.execute("SELECT COUNT(*) FROM fills").fetchone()[0]
        # Approx realized P&L from fills (the journal records per-fill; we sum
        # realized on sells which our router doesn't track directly — fall back
        # to the latest equity snapshot's realized_pnl if available).
        realized = eq_row["realized_pnl"] if eq_row else 0.0
        n_signals = conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]

    except Exception as e:
        # Fail soft — log to stderr, write a degraded note
        import sys as _sys
        _sys.stderr.write(f"snapshot_crypto_trader: db read error: {e}\n")
        (VAULT_PATH / "crypto_trader_live.md").write_text(
            f"# Crypto Trader — Live State\n\n_DB read error: {e}_\n\n_{_ts()}_\n",
            encoding="utf-8",
        )
        return 0
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    # --- Build Bot/crypto_trader_live.md --------------------------------
    lines = [
        "---",
        "type: subsystem",
        "status: live",
        "tags: [crypto, side-system, paper-default]",
        "source: ~/trader/data/journal.db",
        "updated: " + _ts(),
        "---",
        "",
        "# Crypto Trader — Live State",
        "",
        f"_Auto-mirrored from `~/trader/data/journal.db` · {_ts()}_",
        "",
        "## Current State",
        "",
    ]

    if eq_row:
        eq = eq_row["equity"]
        cash = eq_row["cash"]
        pos = eq_row["positions_value"]
        rl = eq_row["realized_pnl"]
        url = eq_row["unrealized_pnl"]
        dd = eq_row["drawdown"]
        pnl_color = "🟢" if rl >= 0 else "🔴"
        lines.extend([
            "| Metric | Value |",
            "|---|---|",
            f"| **Equity** | ${eq:,.2f} |",
            f"| Cash | ${cash:,.2f} |",
            f"| Positions (mark-to-market) | ${pos:,.2f} |",
            f"| Realized P&L | {pnl_color} ${rl:+,.2f} |",
            f"| Unrealized P&L | ${url:+,.2f} |",
            f"| Drawdown | {dd:.2%} |",
            f"| Last snapshot | `{eq_row['ts']}` |",
            "",
        ])
    else:
        lines.append("_No equity snapshots yet — bot warming up._\n")

    lines.extend([
        f"## Recent Fills (last {len(fills)}, total {n_fills} since boot)",
        "",
        "| Time | Pair | Side | Qty | Price | Fee | Mode |",
        "|---|---|---|---|---|---|---|",
    ])
    for f in fills:
        lines.append(
            f"| `{f['ts']}` | {f['pair']} | {f['side']} | "
            f"{f['qty']:.6f} | ${f['price']:,.2f} | ${f['fee']:.4f} | {f['mode']} |"
        )

    lines.extend([
        "",
        f"## Recent Signals (last {len(signals)}, total {n_signals})",
        "",
        "| Time | Pair | Side | Strength | Reason |",
        "|---|---|---|---|---|",
    ])
    for s in signals:
        reason_short = (s["reason"][:80] + "…") if len(s["reason"]) > 80 else s["reason"]
        lines.append(
            f"| `{s['ts']}` | {s['pair']} | {s['side']} | "
            f"{s['strength']:.2f} | {reason_short} |"
        )

    lines.extend([
        "",
        "## Links",
        "",
        "- [[Bot/crypto_trader]] — engine inventory",
        "- [[memory/crypto-trader-2026-06-22]] — design rationale",
        "- [[Subsystems]] — listed under Side systems",
        "- [[hot]] — flagged as new side-system",
        "- [[small-edges-die-on-the-spread]] — design constraint",
        "- [[Decision Log]] — 2026-06-22 entries",
        "",
        "## Related wiring",
        "",
        "- Mirror script: `obsidian_snapshot.py::snapshot_crypto_trader()` (every 6h via launchd)",
        "- Optional per-write mirror: `~/trader/src/dashboard/obsidian_mirror.py` (every 30s)",
        "",
    ])

    (VAULT_PATH / "crypto_trader_live.md").write_text("\n".join(lines), encoding="utf-8")

    # --- Build Edges/crypto-trader.md (gate view) -----------------------
    EDGES_PATH = VAULT_PATH.parent / "Edges"
    EDGES_PATH.mkdir(parents=True, exist_ok=True)

    # Gate bar (per project standard): n≥30 + bootstrap CI>0 + beats control
    if n_fills < 30:
        status = "accumulating"
        verdict = f"n={n_fills} exits — gate bar (n≥30) NOT cleared"
    else:
        status = "live"
        verdict = "n≥30 reached — needs CI>0 + control-loses to graduate"

    gate_lines = [
        "---",
        "type: edge",
        "status: " + status,
        "verdict: \"" + verdict + "\"",
        "tags: [crypto, side-system, accumulating]",
        "updated: " + _ts(),
        "---",
        "",
        "# crypto-trader",
        "",
        "> **Status: paper default** — bot does not yet place live orders on Binance. ",
        "> Opt-in live mode via `LIVE_TRADING=1` env var.",
        "",
        "## The thesis",
        "",
        "Long-only crypto-spot on BTC/USDT and ETH/USDT via Binance REST polling. ",
        "Signal: 15m RSI(14) oversold/overbought + MACD(12,26,9) histogram cross, ",
        "filtered by 1h EMA(200) trend direction. Position sized to risk 1% of equity ",
        "per trade with a 25% max-position cap.",
        "",
        "## Gate (per project standard)",
        "",
        f"- **n_fills**: {n_fills} (gate bar: **n≥30**) — **{('✅' if n_fills >= 30 else '⏳ accumulating')}**",
        f"- **n_signals**: {n_signals}",
        f"- **Realized P&L (last snapshot)**: ${realized:+,.2f}",
        f"- **Status**: {status}",
        "",
        "## Why this is a SIDE system (out of polymarket paper track)",
        "",
        "Built 2026-06-22. NOT subject to the BRAIN.md paper-only rule (Polymarket-specific). ",
        "Opt-in live mode via `LIVE_TRADING=1`. Same edge bar applies before sizing up.",
        "",
        "## Lessons applied",
        "",
        "- [[small-edges-die-on-the-spread]] — order router fills at live bid/ask + slippage, NOT mid",
        "- [[memory/ai-trader-repo-vet-2026-06-17]] — pre-existing audit (mark-to-mid, fake CI)",
        "- [[memory/nautilus-trader-vetted-2026-06-17]] — reference 2nd-opinion validator",
        "",
        "## Reverse if",
        "",
        "- live-mode fills show systematic negative slippage → kill LIVE_TRADING=1",
        "- ≥30 closed exits + CI>0 + DSR + control-loses → keep + size",
        "",
        "## Related",
        "",
        "- [[Bot/crypto_trader]] · [[Bot/crypto_trader_live]] · [[memory/crypto-trader-2026-06-22]]",
        "- [[Subsystems]] · [[hot]] · [[Decision Log]]",
        "- [[Edges/arb-track]] (polymarket sister track — same gate bar)",
        "",
    ]
    (EDGES_PATH / "crypto-trader.md").write_text("\n".join(gate_lines), encoding="utf-8")

    return 2  # wrote 2 notes


SNAPSHOTS = {
    "structarb": snapshot_structural_arb,
    "beliefs": snapshot_beliefs, "sports": snapshot_sports, "brain": snapshot_brain_excerpt,
    "calib": snapshot_calibration, "gate": snapshot_analyst_gate, "touch": snapshot_crypto_touch,
    "basket": snapshot_basket_arb,
    "basketlock": snapshot_basketlock,
    "xvenue": snapshot_xvenue,
    "venue": snapshot_venue_watch,
    "fundbasis": snapshot_funding_basis,
    "decompose": snapshot_decompose,
    "analysis": snapshot_analysis_loggers,
    "terminal": snapshot_terminal,
    "scout": snapshot_scout,
    "monitor": snapshot_position_monitor,
    "hypothesis": snapshot_hypothesis_lab,
    "deepresearch": snapshot_deep_research,
    "defender": snapshot_defender_verdicts,
    "hunter": snapshot_edge_hunter,
    "news": snapshot_breaking_news,
    "critic": snapshot_thesis_critic,
    "ranker": snapshot_opportunity_ranker,
    "liquidity": snapshot_liquidity_scanner,
    "velocity": snapshot_news_velocity,
    "confluence": snapshot_confluence,
    "consistency": snapshot_internal_consistency,
    "decay": snapshot_attention_decay,
    "anchor": snapshot_anchor_hunter,
    "cryptotrader": snapshot_crypto_trader,
}

if __name__ == "__main__":
    import sys, argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=list(SNAPSHOTS), default=None,
                    help="run a single snapshot (e.g. 'touch' for the 15min crypto tape); default = all")
    a = ap.parse_args()
    try:
        if a.only:
            n = SNAPSHOTS[a.only]()
            print(f"✓ snapshot {a.only}: {n}")
        else:
            res = {k: fn() for k, fn in SNAPSHOTS.items()}
            print("✓ snapshots: " + ", ".join(f"{v} {k}" for k, v in res.items()))
        applied = apply_all_enrichment()   # durable enrichment survives every clobber
        if applied:
            print(f"✓ re-applied enrichment to {applied} note(s)")
    except Exception as e:
        sys.stderr.write(f"snapshot error: {e}\n")
