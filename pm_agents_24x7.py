#!/usr/bin/env python3
"""pm_agents_24x7.py — ALL 15 pm-agents, powered by YOUR free LLM API.

Each agent gathers its data in Python (deterministic, read-only), sends it to the
LLM (pm_agent_llm) under the agent's system role, writes the verdict to the Obsidian
vault Reports/agent_<name>.md. No tool-calling needed → any free OpenAI-compatible
API works (OpenRouter free / Groq / Together / local Ollama). PAPER only, read-only.

On-demand agents (resolution/vetter/capacity/counterparty/news/refuter/backtest) take
an OPTIONAL target arg; without one they AUTO-PICK a sensible current target so they
can also run unattended. Continuous agents (leg/settle/regime/anomaly/bias/correlation/
dsr) need no target.

Usage: python3 pm_agents_24x7.py <agent> [target]      |      all
"""
import os, sys, json, time, subprocess, urllib.request, urllib.parse, glob
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pm_agent_llm as L
UA = {"User-Agent": "Mozilla/5.0"}


def sh(cmd, t=90):
    try:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=t, cwd=HERE).stdout.strip()
    except Exception as e:
        return f"(cmd failed: {e})"

def tail(path, n=20):
    p = os.path.join(HERE, path)
    try:
        return "\n".join(open(p).read().splitlines()[-n:]) if os.path.exists(p) else f"({path} missing)"
    except Exception as e:
        return f"({path}: {e})"

def get(url, t=12):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=t) as r:
            return json.loads(r.read())
    except Exception as e:
        return {"_err": str(e)[:60]}

def dapi(path, q): return get("https://data-api.polymarket.com/" + path + "?" + urllib.parse.urlencode(q))
def gamma(q): return get("https://gamma-api.polymarket.com/markets?" + urllib.parse.urlencode(q))

def _active_whales(k=8):
    try:
        wl = json.load(open(os.path.join(HERE, "whale_candidates_active.json")))
        return ([w["addr"] for w in wl] if isinstance(wl, list) else list(wl))[:k]
    except Exception:
        return []

def _open_whalecopy(k=6):
    """current open whalecopy positions (cid, tok, title) for the per-market agents."""
    out = []
    try:
        st = json.load(open(os.path.join(HERE, "whalecopy_state.json")))
        for v in list(st.get("open", {}).values())[:k]:
            out.append(v)
    except Exception:
        pass
    return out


# ── data-gatherers (all accept optional arg) ──────────────────────────────────

def g_leg(arg=None):
    return "scalp_lab one-cycle (per-leg open/closed/WR/paper P&L + controls):\n" + sh("python3 scalp_lab.py once 2>&1 | tail -70", 130)

def g_settle(arg=None):
    out = ["controls + total (controls MUST be negative):", sh("python3 scalp_lab.py once 2>&1 | grep -iE 'total|allin|coinflip|coindown' | tail -12", 130),
           "\nwhalecopy gated leg:", sh("python3 whalecopy_probe.py report 2>&1 | head -8"), "\non-chain resolution spot-checks:"]
    for p in _open_whalecopy(4):
        cid = p.get("cid", "")
        out.append(f"  {cid[:14]} ({p.get('title','')[:30]}): " + sh(f"python3 ctf_resolution.py {cid} 2>&1 | grep -iE 'RESOLVED|UNRESOLVED|winning' | head -2", 40))
    return "\n".join(out)

def g_regime(arg=None):
    eps = {"data-api": "https://data-api.polymarket.com/trades?limit=1", "gamma": "https://gamma-api.polymarket.com/markets?limit=1",
           "clob": "https://clob.polymarket.com/ok", "hermes": "http://127.0.0.1:48580/feeds"}
    lines = ["endpoint liveness:"]
    for n, u in eps.items():
        r = get(u, 8); lines.append(f"  {n}: {'OK' if not (isinstance(r,dict) and r.get('_err')) else 'DOWN '+r.get('_err','')}")
    lines.append("\nwatched-whale recency (dormant = dead signal):")
    for a in _active_whales(6):
        t = dapi("activity", {"user": a, "limit": 1}); ts = (t[0].get("timestamp") if isinstance(t, list) and t else None)
        lines.append(f"  {a[:10]}: last {round((time.time()-int(ts))/86400,1) if ts else '?'}d ago")
    return "\n".join(lines)

def g_anomaly(arg=None):
    return ("logger feeds (anomaly substrate):\n[sharpdrift]\n" + tail("sharpdrift.log", 8) + "\n[tapeshock]\n" + tail("tapeshock.log", 8) +
            "\n[overround]\n" + tail("overround.log", 8) + "\n[whalexit]\n" + tail("whalexit.log", 8) + "\n[whalecopy]\n" + tail("whalecopy.log", 6))

def g_bias(arg=None):
    brain = "\n".join(open(os.path.expanduser("~/Documents/polymarket/BRAIN.md")).read().splitlines()[:60]) if os.path.exists(os.path.expanduser("~/Documents/polymarket/BRAIN.md")) else ""
    return "BRAIN.md head:\n" + brain + "\n\nlatest scorecard / live claims:\n" + sh("python3 scorecard.py 2>&1 | head -40 || python3 analyst_scorecard.py 2>&1 | head -40", 90)

def g_correlation(arg=None):
    return ("open positions across legs (cluster by latent driver — same underlying / event / wallet / structural bet):\n" +
            sh("python3 scalp_lab.py once 2>&1 | grep -iE 'open=' | head -40", 130) + "\n\nwhalecopy open markets:\n" +
            "\n".join(f"  {p.get('title','')[:60]}" for p in _open_whalecopy(15)))

def g_dsr(arg=None):
    leg = arg or "whalecopy"
    rep = sh("python3 whalecopy_probe.py report 2>&1")
    # PRE-COMPUTE the deterministic gate logic in Python so the LLM can't fumble the
    # arithmetic (an 8B mislabeled "CI includes 0" as "CI>0"). The LLM only narrates.
    pre = "(could not parse report JSON — judge from the raw report)"
    try:
        import re as _re
        j = json.loads(rep[rep.index("{"):rep.rindex("}") + 1])
        n = j.get("closed", 0); mean = j.get("mean"); lo = j.get("ci_lo"); hi = j.get("ci_hi"); ctrl = j.get("control")
        if mean is None:
            pre = f"PRE-COMPUTED FACTS (do NOT recompute): n={n} → ACCUMULATING (no closed-exit stats yet)."
        else:
            chk_n = n >= 30
            chk_ci = (lo is not None and lo > 0)                 # CI strictly excludes 0 on the low side
            ci_incl_0 = (lo is not None and hi is not None and lo <= 0 <= hi)
            chk_ctrl = (ctrl is None) or (mean > ctrl)           # `ctrl` = this leg's COMPARISON baseline
            # Marking-honesty halt uses the AUTHORITATIVE controls_logger (the allin/coinflip… honesty
            # controls, n>=30 gated) — NOT this leg's comparison baseline `ctrl`, which can legitimately
            # be positive (it's the beats-benchmark value used in chk_ctrl). The old `ctrl>0` test cried
            # MARKING-BUG-HALT every run (whalecopy's baseline sits at +0.04 while marking is honest).
            try:
                import controls_logger as _cl
                _mk = _cl.status()
                marking_bug = bool(_mk and _mk.get("any_positive"))
            except Exception:
                marking_bug = False
            if marking_bug:
                verdict = "MARKING-BUG-HALT (a graded honesty-control is POSITIVE — the P&L pipeline is suspect)"
            elif not chk_n:
                verdict = f"ACCUMULATING ({n}/30 exits — no claim allowed yet)"
            elif chk_ci and chk_ctrl:
                verdict = "GRADUATE (n≥30 AND CI excludes 0 AND beats control) — pending DSR confirm"
            else:
                verdict = "REJECT (n≥30 but CI includes 0 and/or loses to control)"
            pre = ("PRE-COMPUTED FACTS (computed in Python — do NOT recompute, just explain + add DSR/qualitative judgment):\n"
                   f"  n_closed = {n}   (≥30 ? {chk_n})\n"
                   f"  mean $/exit = {mean}\n"
                   f"  95% CI = [{lo}, {hi}]   (excludes 0 ? {chk_ci};  INCLUDES 0 ? {ci_incl_0})\n"
                   f"  comparison-control = {ctrl}   (leg beats its benchmark ? {chk_ctrl})\n"
                   f"  marking-honesty (allin/coinflip controls, authoritative) OK ? {not marking_bug}\n"
                   f"  DETERMINISTIC VERDICT = {verdict}\n"
                   f"  NOTE: DSR (deflated Sharpe, best-of-N) is NOT yet computed — flag it as the remaining gate if otherwise passing.")
    except Exception:
        pass
    # feed ONLY the pre-computed facts — the raw tape distracts a weak local model
    return f"leg '{leg}' graduation gate:\n{pre}"

def g_quant(arg=None):
    """PRE-COMPUTE quant stats in Python (LLMs botch arithmetic): power/exits-to-
    significance, Kelly, portfolio exposure. The model only interprets → decisions."""
    import math, statistics, collections
    pnls = []
    p = os.path.join(HERE, "whalecopy_log.jsonl")
    if os.path.exists(p):
        for ln in open(p).read().splitlines():
            try:
                r = json.loads(ln)
                if r.get("ev") == "close" and isinstance(r.get("pnl"), (int, float)):
                    pnls.append(float(r["pnl"]))
            except Exception:
                pass
    L_ = ["PRE-COMPUTED QUANT (Python — do NOT recompute; INTERPRET into decisions):"]
    if len(pnls) >= 2:
        n = len(pnls); mean = statistics.mean(pnls); sd = statistics.pstdev(pnls) if n < 2 else statistics.stdev(pnls)
        se = sd / math.sqrt(n) if n else float("nan")
        t = mean / se if se else float("nan")
        # exits to |t|=1.96 holding mean & sd (optimistic: assumes effect persists)
        if mean != 0 and sd > 0:
            n_need = (1.96 * sd / abs(mean)) ** 2
            more = max(0, math.ceil(n_need - n))
        else:
            more = "∞ (mean≈0)"
        kelly = (mean / (sd ** 2)) if sd > 0 else 0.0          # continuous Kelly approx (per-bet fraction)
        L_ += [f"  whalecopy closed exits: n={n}, mean=${mean:+.4f}/exit, sd=${sd:.4f}, SE=${se:.4f}, t={t:+.2f}",
               f"  exits-to-significance (|t|→1.96, IF mean/sd hold): ~{more} MORE exits",
               f"  Kelly fraction ≈ {kelly:+.3f} (half-Kelly {kelly/2:+.3f}); NOTE if mean<0 → no edge → size 0",
               f"  sign of mean is {'POSITIVE' if mean>0 else 'NEGATIVE'} → near-term realistic verdict {'maybe graduate' if mean>0 else 'REJECT/accumulate'}"]
    else:
        L_.append(f"  whalecopy closed exits: n={len(pnls)} (<2) — not enough to compute power/Kelly yet.")
    # portfolio exposure + concentration from open positions
    opens = _open_whalecopy(50)
    L_.append(f"  open positions: {len(opens)} · exposure ≈ ${len(opens)*1.0:.0f} (at $1/bet)")
    buckets = collections.Counter()
    for o in opens:
        ti = (o.get("title") or "").lower()
        key = ("hormuz" if "hormuz" in ti else "israel/iran" if ("israel" in ti or "iran" in ti) else
               "world cup" if "world cup" in ti or "fifa" in ti else "nba" if ("nba" in ti or "finals" in ti) else "other")
        buckets[key] += 1
    if opens:
        top = buckets.most_common(1)[0]
        L_.append(f"  concentration: top driver '{top[0]}' = {top[1]}/{len(opens)} open ({100*top[1]//len(opens)}%) → effective-n < nominal if correlated")
    return "\n".join(L_)

def g_refuter(arg=None):
    return ("CURRENT live edge claims to refute (Track-1 analyst book + open theses). Refute each via 3 lenses (resolution-misread / fresh-counter-evidence / market-is-right):\n" +
            sh("python3 scorecard.py 2>&1 | sed -n '/TRACK 1/,/=====/p' | head -30", 90) + "\nopen whalecopy theses:\n" + "\n".join(f"  {p.get('title','')[:60]} (entry {p.get('entry')})" for p in _open_whalecopy(6)))

def g_resolution(arg=None):
    cids = [arg] if arg else [p.get("cid") for p in _open_whalecopy(4)]
    out = ["resolution criteria of current open markets (find the trap: date cutoff, touch-vs-close, MOU≠treaty, immediate-resolution, second-round):"]
    for cid in [c for c in cids if c]:
        m = gamma({"condition_ids": cid}); m = m[0] if isinstance(m, list) and m else {}
        out.append(f"\n• {m.get('question','?')[:80]}\n  end={m.get('endDate','?')} | umaStatus={m.get('umaResolutionStatus','?')}\n  desc: {(m.get('description') or '')[:400]}")
    return "\n".join(out)

def g_vetter(arg=None):
    addr = arg or (_active_whales(1)[0] if _active_whales(1) else None)
    if not addr: return "no wallet to vet."
    return (f"vet wallet {addr} for COPYABILITY (dormant? 0.99-padding? coinflip-HFT? sharp-info?):\n" + sh(f"python3 account_flag.py {addr} 2>&1 | head -12", 40) +
            "\nrecent trades (market mix + recency + entry prices):\n" + json.dumps([{k: t.get(k) for k in ('side','price','title','timestamp')} for t in (dapi('trades', {'user': addr, 'limit': 20}) or [])][:20])[:1500])

def g_capacity(arg=None):
    # PRE-COMPUTE the $ capacity in Python (walk the ask ladder): how many $ you can
    # buy before the price you pay exceeds best_ask + 1c. The LLM only narrates.
    out = ["PRE-COMPUTED CAPACITY (computed in Python — do NOT recompute; just rate toy/sub-scale/real-money & name the binding constraint):"]
    caps = []
    for p in _open_whalecopy(5):
        tok = p.get("tok"); b = get(f"https://clob.polymarket.com/book?token_id={tok}") if tok else {}
        asks = b.get("asks") if isinstance(b, dict) else None
        if not asks:
            out.append(f"  {p.get('title','')[:40]}: no live book"); continue
        levels = sorted(({"p": float(a["price"]), "s": float(a["size"])} for a in asks), key=lambda x: x["p"])
        best = levels[0]["p"]
        cap = sum(l["p"] * l["s"] for l in levels if l["p"] <= best + 0.01)   # $ within 1c of best ask
        caps.append(cap)
        out.append(f"  {p.get('title','')[:40]} | best ask {best:.3f} | $ deployable within 1c slippage = ${cap:,.0f}")
    if caps:
        out.append(f"  → median per-entry capacity ≈ ${sorted(caps)[len(caps)//2]:,.0f}. (×fire-rate ≈ a few copies/week for $/week; self-impact halves the edge for copy legs at size.)")
    return "\n".join(out)

def g_counterparty(arg=None):
    p = _open_whalecopy(1); cid = arg or (p[0].get("cid") if p else None)
    if not cid: return "no market to profile."
    tape = dapi("trades", {"market": cid, "limit": 60}) or []
    rows = [{k: t.get(k) for k in ('side','price','size','proxyWallet','timestamp')} for t in (tape if isinstance(tape, list) else [])][:30]
    return f"recent trade tape for market {cid[:14]} — who's on each side (cross-ref the wallets vs the sharp set):\n" + json.dumps(rows)[:1800]

def g_news(arg=None):
    return ("latest news (hermes feed) vs a current event market — is the catalyst already PRICED or a fresh edge?\n[hermes recent]\n" +
            (tail("hermes_news.log", 12) if os.path.exists(os.path.join(HERE, "hermes_news.log")) else "(no hermes log)") +
            "\n[top current event markets]\n" + "\n".join(f"  {m.get('question','')[:60]} mid~{m.get('lastTradePrice','?')}" for m in (gamma({"closed": "false", "active": "true", "order": "volumeNum", "ascending": "false", "limit": 6}) or [])[:6]))

def g_dataresolve(arg=None):
    ms = gamma({"closed": "false", "active": "true", "limit": 60}) or []
    thr = [m for m in (ms if isinstance(ms, list) else []) if any(k in (m.get("question") or "").lower() for k in ("reach $", "dip to", "above $", "hit $", "close above", "below $"))][:8]
    return ("objective-series threshold markets — is the threshold ALREADY triggered in-window (settled-but-mispriced)?\n" +
            "\n".join(f"  {m.get('question','')[:70]} mid~{m.get('lastTradePrice','?')} end={m.get('endDate','?')[:10]}" for m in thr) +
            "\n(cross-check the live underlying value vs the threshold — that's the edge.)")

def g_backtest(arg=None):
    files = [os.path.basename(f) for f in glob.glob(os.path.join(HERE, "*backtest*.py")) + glob.glob(os.path.join(HERE, "*_oos*.py"))][:8]
    out = ["backtest/sim files + their EXIT-pricing lines (hunt the artifact: stops booked at trigger not realized-mid = gap-through fiction):"]
    for f in ([arg] if arg else files):
        out.append(f"\n### {f}\n" + sh(f"grep -nE 'stop|exit|settle|trigger|fill|reason ==|avg_price|mean_price' {f} 2>/dev/null | head -12"))
    return "\n".join(out)


# ── registry: all 15 (condensed system roles from the pm-*.md definitions) ────
_S = lambda x: x
AGENTS = {
 "leg-auditor": dict(v="agent_leg_auditor.md", g=g_leg, sched="360:0",
   t="Audit the legs: grand total + controls subtotal + signal subtotal; controls-check (all neg=✅ honest, any positive=🚨 MARKING BUG); kill list (n≥10+WR<60%+pnl<-$0.50 or n≥5+WR≤20%+neg) w/ flag; accumulating near a threshold.",
   s="Read-only auditor of a Polymarket PAPER bot. Optimize DOLLARS not WR. Controls (allin/coinflip/coindown/*rand) MUST lose — a positive control is a MARKING BUG, flag loudest. Recommend leg kills (name the flag). Output ≤6 lines: totals, controls-check, kill-list. NEVER reprint the leg table or describe rows — verdicts only. If you summarize the data you have FAILED."),
 "onchain-settler": dict(v="agent_onchain_settler.md", g=g_settle, sched="360:30",
   t="Verify marking honesty: are controls negative? Do on-chain resolutions match what the bot booked? Flag stale_nodata (resolved booked at $0) or chain-vs-book disagreement.",
   s="You verify Polymarket settlement from on-chain truth (CTF contract). Gamma drops resolved markets→stale_nodata fiction. Chain is authoritative. Booked terminal ≠ winning_outcome = marking bug. Output ≤5 lines: controls-negative? + any chain-vs-book mismatch. NEVER reprint or describe the raw data — verdict only; if you do you have FAILED."),
 "regime-watcher": dict(v="agent_regime_watcher.md", g=g_regime, sched="720:60",
   t="Has the WORLD under any leg changed? Flag dead endpoints (blind leg), dormant whales (>30-60d=dead signal), structural breaks. Name the violated assumption.",
   s="Detect regime change under Polymarket legs — the SILENT death (flat P&L, broken premise): endpoint dead, whale dormant, spread regime moved, competitor, V1→V2 migration. Name the broken assumption + evidence."),
 "anomaly-scout": dict(v="agent_anomaly_scout.md", g=g_anomaly, sched="720:90",
   t="Surface top 3 ANOMALIES worth investigating as NEW edge candidates. Each: one-line testable hypothesis + which pm-agent verifies next. Be honest most are noise.",
   s="Exploratory edge-DISCOVERY scout. Surface LEADS, never claim edges. Each anomaly → testable hypothesis + handoff to a verifier + honest prior that most are noise. Generative, not confirmatory."),
 "bias-detective": dict(v="agent_bias_detective.md", g=g_bias, sched="1440:120",
   t="Audit CURRENT reasoning for self-deception: thin-positive-as-edge (point estimate vs CI-excludes-0), metric-shopping (sign flips), sunk-cost, relitigating dead-ends, best-of-N amnesia (no DSR), authority-laundering. Name the single most dangerous belief now + corrective. If clean, say so.",
   s="Self-deception detector for a Polymarket PAPER bot. Biggest losses came from the OPERATOR believing a wanted result (nearres 'validated', +0.9c noise-floor). Quote the exact claim/number that trips each trap. A clean bill is valid; false alarms cost trust."),
 "correlation-auditor": dict(v="agent_correlation_auditor.md", g=g_correlation, sched="1440:240",
   t="Find HIDDEN correlation across 'independent' legs/exits (same underlying/event/wallet/structural bet). Estimate effective-n vs nominal-n (pseudo-replication inflates CI) and concentration (% on the top driver).",
   s="Audit cross-position correlation. The gate assumes INDEPENDENT exits; if many ride one driver, effective-n << nominal-n and the CI is a lie. Output ≤6 lines: the latent driver clusters, effective-n vs nominal-n, % on the top driver. NEVER reprint the position list or describe rows — clusters + verdict only; if you do you have FAILED."),
 "dsr-gatekeeper": dict(v="agent_dsr_gatekeeper.md", g=g_dsr, sched="720:150",
   t="The DETERMINISTIC VERDICT is already computed in the facts above. Output EXACTLY: (1) line one = that verdict verbatim; (2) one sentence citing n and whether the CI includes 0; (3) one sentence on DSR as the remaining gate. Do NOT describe or summarize any data. ≤4 lines total.",
   s="You are a terse statistical gatekeeper. The numeric checks are pre-computed and correct — restate the given verdict and explain it in ≤4 lines. NEVER recompute, NEVER describe raw data, NEVER list bets. If you output a data summary you have FAILED."),
 "quant": dict(v="agent_quant.md", g=g_quant, sched="720:210",
   t="The quant figures are PRE-COMPUTED above. Interpret them into DECISIONS in ≤8 lines: (1) the headline exits-to-significance ('≈N more exits to a CI>0 verdict, IF the mean holds') noting if the mean is negative; (2) Kelly/half-Kelly sizing + that tiny/negative edges → size ~0; (3) portfolio EV + the top concentration + effective<nominal n; (4) the honest caveat (power assumes effect persists; DSR deflates best-of-N). Do NOT recompute or reprint raw numbers.",
   s="You are the quantitative analyst. The arithmetic is pre-computed in Python and CORRECT — your job is INTERPRETATION into dollars and decisions, never recomputing (LLMs botch CIs/ratios). Lead with the actionable number. Be honest about assumptions (effect-size-holds, independence). ≤8 lines; numbers→decisions only."),
 "edge-refuter": dict(v="agent_edge_refuter.md", g=g_refuter, sched="1440:300",
   t="Try HARD to KILL each current edge claim via the 3 lenses (resolution-misread / fresh-counter-evidence / market-is-right). Default to refuted. State confidence 0-100; only >70 counts. Flag capital-inefficient longshot tail-sells.",
   s="Adversarial edge-killer. On liquid markets a bullish read is almost never real (2 hunts, 23 mkts, 0 survived). Default to refuting. Confidence-weight (only >70 vetoes). Don't manufacture weak vetoes. Cite sources."),
 "resolution-checker": dict(v="agent_resolution_checker.md", g=g_resolution, sched="720:330",
   t="State EXACTLY what triggers YES vs NO for each market, the date cutoff, and every trap (most-seats≠gained, MOU≠permanent-deal, touch≠close, announcement-counts, immediate-resolution-on-elimination, second-round).",
   s="Resolution-criteria specialist — the #1 prediction-market edge killer. Read the verbatim rules; state precisely the YES/NO trigger, cutoff, and traps. Misreading resolution is how most 'edges' die."),
 "whale-vetter": dict(v="agent_whale_vetter.md", g=g_vetter, sched="1440:420",
   t="Classify this wallet: SHARP-INFO (copyable) / FAVORITE-PADDER / COINFLIP-HFT / DORMANT / LUCKY-SMALL-N. Recency>P&L. Is it copyable? Suggest a conviction floor if yes.",
   s="Vet a Polymarket wallet for COPYABILITY. Big P&L isn't enough. Dormant (>30-60d)=useless. 0.99-padding=uncopyable. >50% coinflip=non-copyable at 60s lag. Want sharp INFO in the [0.15,0.85] uncertainty band."),
 "capacity-estimator": dict(v="agent_capacity_estimator.md", g=g_capacity, sched="1440:540",
   t="The $ capacities are PRE-COMPUTED above. Output ≤5 lines: the median per-entry capacity verbatim, a TOY/SUB-SCALE/REAL-MONEY rating, and the binding constraint (depth/frequency/self-impact). Do NOT recompute or describe the raw numbers.",
   s="Terse capacity rater. The dollar figures are pre-computed and correct — rate them, don't recompute. Edges here are tiny so capacity is the binding constraint. ≤5 lines. NEVER dump or re-describe the input data; if you do you have FAILED."),
 "counterparty-profiler": dict(v="agent_counterparty_profiler.md", g=g_counterparty, sched="1440:660",
   t="From the tape, profile who's on the OTHER side of the trade you'd take. If sharp wallets dominate → you're the dumb money (adverse selection, AVOID). Name wallets + flags + take/avoid.",
   s="Profile counterparty flow. A price 'mispriced' is often mispriced AGAINST you. If informed wallets are dumping the side you'd buy, adverse-selection risk is HIGH — avoid. Name the wallets on the other side."),
 "news-arb": dict(v="agent_news_arb.md", g=g_news, sched="720:480",
   t="For an event market + its catalyst: ALREADY-PRICED (headline/public) or FRESH-EDGE (unincorporated)? Check timing-vs-resolution and whether the stated risk already fired. Cite the deciding fact.",
   s="Assess news-vs-price. Liquid markets arbitrage public info fast; default to ALREADY-PRICED. An edge needs a real, material, NOT-widely-reported fact (or a resolution misread). Cite outlet+date or it's worthless."),
 "data-resolver": dict(v="agent_data_resolver.md", g=g_dataresolve, sched="720:600",
   t="For each objective-series market: is the threshold ALREADY triggered in-window vs the live underlying value, and how far? This settled-but-mispriced type is the ONLY one that beat the market. Flag any already-decided-but-still-priced.",
   s="Fetch the resolving DATA for objective-series Polymarket markets (crypto klines, BLS, ESPN, IMF). Report current value vs threshold, whether ALREADY triggered in-window, and distance. Settled-but-mispriced is the real edge."),
 "backtest-auditor": dict(v="agent_backtest_auditor.md", g=g_backtest, sched="1440:780",
   t="Audit these backtests for fill-realism fiction: do stops book at the realized post-gap mid (not the trigger)? targets at the limit? slippage modeled? Flag any clean-fill/gap-through artifact (the nearres killer, Lesson 13) with the file:line.",
   s="Adversarial backtest auditor. Assume a positive CI is fiction until the code proves it models reality. Stops MUST book at realized_mid−half_spread, never the trigger (favorites gap 0.93→0). Quote file:line for every artifact."),
}


# procedural memory: judgment agents see their OWN resolution-verified track record before
# judging (the missing memory tier — see procedural_memory.py). Dormant/no-op until bets resolve.
try:
    import procedural_memory as _PMEM
except Exception:
    _PMEM = None
# agents that make a JUDGMENT call whose outcome the scorecard eventually verifies (NOT the
# deterministic interpreters or marking-checkers — those don't have a 'track record' to learn from)
_JUDGMENT = {"edge-refuter", "anomaly-scout", "news-arb", "data-resolver",
             "resolution-checker", "bias-detective", "whale-vetter", "counterparty-profiler"}


def run(name, arg=None):
    a = AGENTS[name]
    data = a["g"](arg)
    sys_role = a["s"]
    if _PMEM and name in _JUDGMENT:
        try:
            refl = _PMEM.reflection()
            if refl:
                # PREPEND: calibration is background context, so the agent's role + its strict
                # output rules ("verdicts only / ≤N lines / if you summarize you FAILED") stay
                # LAST in the system prompt (recency = salience). Appending it below those rules
                # would dilute them — the /verify finding this fixes.
                sys_role = refl + "\n\n" + sys_role
        except Exception:
            pass   # procedural memory is best-effort; never block an agent run
    out, err = L.run_agent(f"pm-{name}", sys_role, data, a["t"], a["v"])
    print(f"pm-{name}: {'OK → '+a['v'] if not err else 'SKIPPED ('+err+')'}")


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    arg = sys.argv[2] if len(sys.argv) > 2 else None
    targets = list(AGENTS) if which == "all" else [which]
    for t in targets:
        if t in AGENTS:
            run(t, arg); time.sleep(1)
        else:
            print(f"unknown agent: {t}\nhave: {', '.join(AGENTS)}")
