#!/usr/bin/env python3
"""
leg_gauntlet.py — recurring, ZERO-Claude-token port of the leg-design-gauntlet.

Four designers (structural-arb / mechanical / data-arb / wildcard) propose NEW
candidate legs the bot does not already run; each candidate is run through the
5-lens kill ledger (calibrated-mids / gap-through / fee-wall / already-covered /
resolution-mismatch), each lens defaulting to LANDS; a candidate SURVIVES only if
it clears ALL FIVE. Survivors (if any) are a rare lead for the human + the vet
pipeline — NEVER an auto-built leg.

It runs on the FREE LLM via llm_client.judge() (Python gathers the grounding, the
LLM judges — same zero-cost pattern as pm_agents_24x7.py / pm_chain.py), so it can
recur weekly with no Claude-Code-token cost. Grounded in BRAIN.md + the live leg
list so "already-covered" is real, not guessed.

Honest by construction: the leg-space is a confirmed null (95-leg graveyard;
Lessons 13/15/16/23). A 0-survivor run is the EXPECTED result and stays QUIET —
no iMessage. Only a genuine survivor pings. Always appends leg_gauntlet_log.jsonl
and refreshes the Obsidian digest regardless.

Modes:
  python3 leg_gauntlet.py once         # run now; alert iMessage ONLY on a survivor
  python3 leg_gauntlet.py --cron       # run only if >=7d since last (for the watchdog)
  python3 leg_gauntlet.py --selftest   # check LLM config + 1 live ping; no full run

Read-only. Never touches the bot, the book, processes, or funds. Fail-soft throughout.
"""
import os, sys, json, subprocess
from datetime import datetime, timezone

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
try:
    import llm_client
except Exception:
    llm_client = None
try:
    from gate_wrapper import gate_edge as _brain_gate
except ImportError:
    _brain_gate = None  # graceful fallback

LOG = os.path.join(BASE, "leg_gauntlet_log.jsonl")
VAULT = os.path.expanduser("~/Documents/PolymarketVault/Reports/leg_gauntlet.md")
STAMP = os.path.join(BASE, ".last_leg_gauntlet")
WEEK = 7 * 24 * 3600
IMESSAGE_TARGETS = ["krisharyan@icloud.com", "+918449447444"]

LENSES = ["calibrated-mids", "gap-through", "fee-wall", "already-covered", "resolution-mismatch"]

ANGLES = {
    "structural-arb": (
        "Complete-set/negRisk baskets summing != $1 at ask, monotonicity ladders "
        "(P(reach $X) >= P(reach $Y) for X<Y), Dutch books, redeem/mint discounts. "
        "Capturable, held-to-resolution, NON-predictive. The richest honest vein — push it."),
    "data-arb": (
        "Markets settling on an OBJECTIVE public series (crypto klines, IMF PortWatch "
        "chokepoints, BLS, USGS, lmarena) where the data has ALREADY decided the outcome "
        "but price lags. Name the EXACT keyless series + fetch path AND whether it is "
        "provably the same input the market's resolution uses (not a proxy). The Hormuz species."),
    "mechanical": (
        "Non-predictive microstructure/lifecycle: redemption-window theta, near-deadline "
        "impossible-YES decay, complete-set creation arb, settlement-timing, fee/rebate "
        "mechanics. NOT TA, NOT sentiment. Flag anything predictive as likely-dead."),
    "wildcard": (
        "Anything outside the other three that could plausibly be NON-predictive +EV. "
        "Be creative, but most wildcards are predictive in disguise and will die on the "
        "calibrated-mids lens — flag them honestly."),
}

KNOWN_LEGS = (
    "dip scalp fade truefade nearresfade allin coinflip coinup coindown diverg feargreed "
    "lateprox noevent newsno btc15no weatherno ytbuzz wikivol redditbuzz momentum favyes "
    "midfade thetadk thetano certsnipe windowshut csarb clusterarb strikeflip laddermid "
    "negladder basketlock basket_paper monoarb multiarb dataarb redeemdisc funding_basis "
    "deadline (controls that MUST lose: allin coinflip coindown *rand)")


def _ts():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ground():
    """Trimmed BRAIN slice + the live/dead leg registry — the killer's 'already-covered'
    and 'calibrated-mids' lenses need the real ledger, not a guess. Budget-capped."""
    brain = ""
    p = os.path.join(BASE, "BRAIN.md")
    try:
        with open(p, encoding="utf-8", errors="ignore") as f:
            brain = f.read()
    except Exception:
        brain = ""
    # Keep it cheap: head (lessons live near the top) + any graveyard section, capped.
    head = brain[:7000]
    grave = ""
    low = brain.lower()
    for marker in ("graveyard", "dead leg", "lesson 13", "lesson 15", "lesson 16", "lesson 23"):
        i = low.find(marker)
        if i >= 0:
            grave += brain[i:i + 1200] + "\n"
    grave = grave[:6000]
    return (f"LIVE/DEAD LEG REGISTRY: {KNOWN_LEGS}\n\n"
            f"BRAIN (lessons head):\n{head}\n\nBRAIN (graveyard/lessons excerpts):\n{grave}")


DESIGNER_SYS = (
    "You are a leg designer for a PAPER-ONLY Polymarket bot. The leg-space is a confirmed "
    "honest null: ~95 predictive taker legs are dead, ONE cause — calibrated mids + paying "
    "the spread (Lesson 15/16). The ONLY species that ever beat this market is NON-predictive: "
    "settled-but-mispriced data arb (Hormuz NO @0.185) and structural complete-set/monotonicity "
    "locks (basketlock). Weight candidates HARD toward non-predictive structural/data mechanisms. "
    "Do NOT re-propose anything in the live/dead registry. If you propose a predictive leg, label "
    "kind='predictive' and say it likely dies on calibrated-mids. Be honest, not promotional.")

KILLER_SYS = (
    "You exist to KILL a candidate leg for a PAPER-ONLY Polymarket bot. Default EVERY lens to "
    "LANDS (the leg dies on it); clear a lens only if the candidate demonstrably survives it. A "
    "candidate clears the gauntlet ONLY if all five lenses clear — they are independent necessary "
    "conditions, not a vote. Lenses: "
    "calibrated-mids (predictive taker on a calibrated mid = dead, Lesson 15/16); "
    "gap-through (exit assumes a clean fill where the market gaps through in one tick, Lesson 13); "
    "fee-wall (gross edge <= ~2% fee + spread = uncapturable); "
    "already-covered (duplicates a live/dead leg or a relabeled control); "
    "resolution-mismatch (misreads how/when it resolves, or for data-arb the fetched series is not "
    "provably the exact oracle input). Be specific; cite the lesson/endpoint. A clean KILL is the "
    "valuable result — do not manufacture survival.")


def design(angle, desc, ground):
    if not (llm_client and llm_client.configured()):
        return []
    user = (f"{ground}\n\nANGLE = {angle.upper()}: {desc}\n\n"
            "Propose 3-5 DISTINCT candidate legs for THIS angle only. Return JSON.")
    schema = ('{"candidates":[{"name":str,"thesis":str,"mechanism":str,"signal_source":str,'
              '"keyless":bool,"kind":"structural|data|mechanical|predictive","resolving_data":str,'
              '"why_not_dead":str,"est_edge":str,"capacity_hint":str}]}')
    try:
        r = llm_client.judge(system=DESIGNER_SYS, user=user, schema_hint=schema,
                             timeout=150, max_tokens=1500)
        cands = (r or {}).get("candidates") or []
        for c in cands:
            c["angle"] = angle
        return cands[:5]
    except Exception as e:
        print(f"  design[{angle}] failed: {e}")
        return []


def kill(cand, ground):
    if not (llm_client and llm_client.configured()):
        return None
    user = (f"{ground}\n\nCANDIDATE:\n{json.dumps(cand)}\n\n"
            "Run all five lenses. Default each to LANDS. Return JSON.")
    schema = ('{"lenses":[{"lens":"calibrated-mids|gap-through|fee-wall|already-covered|'
              'resolution-mismatch","lands":bool,"confidence":int,"why":str}],'
              '"survives":bool,"binding":str,"summary":str}')
    try:
        v = llm_client.judge(system=KILLER_SYS, user=user, schema_hint=schema,
                             timeout=150, max_tokens=1500)
        if not isinstance(v, dict):
            return None
        # Defensive: survives ONLY if the model says so AND no lens lands.
        lenses = v.get("lenses") or []
        any_lands = any(bool(l.get("lands")) for l in lenses)
        got = {l.get("lens") for l in lenses}
        all_five = set(LENSES).issubset(got)
        v["survives"] = bool(v.get("survives")) and not any_lands and all_five
        return v
    except Exception as e:
        print(f"  kill[{cand.get('name')}] failed: {e}")
        return None


def send_imessage(text):
    safe = text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    ok = False
    for tgt in IMESSAGE_TARGETS:
        script = ('tell application "Messages"\n'
                  '  set s to id of first service whose service type = iMessage\n'
                  f'  set b to participant "{tgt}" of service id s\n'
                  f'  send "{safe}" to b\n'
                  'end tell')
        try:
            r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=20)
            ok = ok or r.returncode == 0
        except Exception:
            pass
    return ok


def write_vault(summary, survivors, killed):
    try:
        os.makedirs(os.path.dirname(VAULT), exist_ok=True)
        lines = [
            "---", "type: report", "status: " + ("SURVIVOR" if survivors else "leg-saturated"),
            f"updated: {_ts()}", "---", "", "# Leg-Design Gauntlet", "",
            f"_{summary}_", "",
        ]
        if survivors:
            lines.append("## ⭐ Survivors (clear all 5 lenses — VET before building)")
            for s in survivors:
                c = s["cand"]
                lines.append(f"- **{c.get('name')}** ({c.get('angle')}/{c.get('kind')}) — {c.get('thesis')}")
                lines.append(f"  - mechanism: {c.get('mechanism')}")
                lines.append(f"  - source: {c.get('signal_source')} (keyless={c.get('keyless')})")
                lines.append(f"  - {s['verdict'].get('summary','')}")
        else:
            lines.append("## No survivors — honest leg-saturated result")
            lines.append("Every candidate died. The only +EV mechanisms that survive scrutiny are "
                         "the non-predictive arbs already running. Do not auto-build.")
        # kill ledger tally
        tally = {}
        for k in killed:
            b = (k["verdict"] or {}).get("binding", "?")
            tally[b] = tally.get(b, 0) + 1
        lines += ["", "## Killed (binding lens)"]
        for k in killed:
            v = k["verdict"] or {}
            lines.append(f"- {k['cand'].get('name')} — [{v.get('binding','?')}] {v.get('summary','')[:160]}")
        with open(VAULT, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except Exception as e:
        print(f"  vault write failed: {e}")


def run_once():
    if not (llm_client and llm_client.configured()):
        print("LLM not configured (set LLM_PROVIDER + key in .env) — no-op.")
        return 0
    ground = _ground()
    designed = []
    for angle, desc in ANGLES.items():
        cands = design(angle, desc, ground)
        print(f"design[{angle}]: {len(cands)} candidates")
        designed += cands
    judged, survivors, killed = [], [], []
    for c in designed:
        # Early filter: BRAIN gatekeeper (graveyard check) before expensive gauntlet
        brain_pre_gate = None
        if _brain_gate is not None:
            try:
                leg_type = c.get("name", "?").lower()
                leg_desc = c.get("thesis", "")
                allow, verdict = _brain_gate(leg_type, leg_desc, mode="soft")
                brain_pre_gate = {"allow": allow, "reason": verdict.get("status", "?"), "mode": "pre-gate"}
                # In "hard" mode, skip gauntlet if graveyard rejects (efficiency)
                if not allow:
                    print(f"  BRAIN-gate {leg_type:8s} — skipped (graveyard match)")
                    c["brain_pre_gate"] = brain_pre_gate
                    killed.append({"cand": c, "verdict": {"binding": "BRAIN-gate", "summary": brain_pre_gate["reason"]}})
                    continue
            except Exception as e:
                brain_pre_gate = {"allow": None, "reason": f"error: {str(e)[:40]}", "mode": "pre-gate"}

        # Run full 5-lens gauntlet
        v = kill(c, ground)
        if v is None:
            continue
        rec = {"cand": c, "verdict": v, "brain_pre_gate": brain_pre_gate}
        judged.append(rec)
        (survivors if v.get("survives") else killed).append(rec)
        flag = "SURVIVE" if v.get("survives") else "kill"
        print(f"  {flag:8s} {c.get('name')}  [{v.get('binding','?')}]")

    summary = (f"{len(designed)} candidates designed, {len(judged)} judged, "
               f"{len(survivors)} survived, {len(killed)} killed.")
    print(summary)

    # always log
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "ts": _ts(), "designed": len(designed), "judged": len(judged),
                "survived": len(survivors), "survivors": [s["cand"].get("name") for s in survivors],
                "killed": len(killed),
                **llm_client.provenance(),
            }) + "\n")
    except Exception as e:
        print(f"  log write failed: {e}")

    write_vault(summary, survivors, killed)

    # alert ONLY on a survivor (0-survivor is the expected null → stay quiet)
    if survivors:
        names = ", ".join(s["cand"].get("name", "?") for s in survivors)
        send_imessage(f"🧪 leg-gauntlet: {len(survivors)} candidate(s) CLEARED all 5 lenses — {names}. "
                      f"RARE lead, NOT a built leg — vet (resolution-checker → edge-refuter → dsr-gate) "
                      f"before any config. See Obsidian Reports/leg_gauntlet.md.")
        print("iMessage sent (survivor).")
    return len(survivors)


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else "once"
    if arg == "--selftest":
        ok = bool(llm_client and llm_client.configured())
        print(f"llm_client configured: {ok}")
        if ok:
            try:
                r = llm_client.judge(system="Reply with JSON.", user="Return {\"ok\":true}.",
                                     schema_hint='{"ok":bool}')
                print(f"live ping: {r}")
            except Exception as e:
                print(f"live ping failed: {e}")
        return
    if arg == "--cron":
        # weekly self-gate; stamp BEFORE running so a crash can't retry-storm (one attempt/week)
        try:
            last = float(open(STAMP).read().strip()) if os.path.exists(STAMP) else 0.0
        except Exception:
            last = 0.0
        now = datetime.now(timezone.utc).timestamp()
        if now - last < WEEK:
            print(f"not due ({int((WEEK-(now-last))/3600)}h to go) — skip.")
            return
        try:
            with open(STAMP, "w") as f:
                f.write(str(now))
        except Exception:
            pass
        run_once()
        return
    run_once()


if __name__ == "__main__":
    main()
