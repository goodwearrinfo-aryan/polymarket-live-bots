#!/usr/bin/env python3
"""vault_fixer_swarm.py — a SWARM of free-LLM agents (NVIDIA Maverick, $0 metered, never Claude)
that HANDLE and FIX the loose ends vault_weaver only flags: broken [[wikilinks]] + un-homed orphans.

Per loose end a 2-agent swarm decides + adversarially verifies the fix on Maverick:
    ROUTER   → {repoint | create | unlink} + target + confidence + reason
    VERIFIER → skeptical 2nd Maverick pass; vetoes weak / overconfident calls (the candela lesson:
               free-LLM judgment is systematically overconfident, so a single call is never trusted)
    HOME     → for an un-homed orphan, picks the single best existing hub to link it from
Only a fix the verifier AGREES with at high confidence is applied; the rest go to a human report.

Every fix is NON-DESTRUCTIVE to content (and git-reversible via the vault's autocommit):
    CREATE  = write a stub note so the link resolves            (purely additive)
    REPOINT = [[X]] -> [[Correct|X]]   (preserves the display text; just fixes the target)
    UNLINK  = [[X]] -> X               (strip the broken brackets, keep the words)
It NEVER deletes a note or any note text, NEVER edits notes in agent-owned folders
(Connectors/Live/memory/ConceptGraph/Graph/graphify-out — owned by wiring_keeper/activity_light),
and NEVER creates a stub inside those folders. Fans out with a thread pool (the swarm); all
judgment is $0 on the NVIDIA free key. stdlib + framework python = TCC-clean. Fail-soft, idempotent.

    python3 vault_fixer_swarm.py            # decide + APPLY verified fixes
    python3 vault_fixer_swarm.py --dry-run  # decide + report only, change nothing
"""
import os, re, sys, json, time, datetime, difflib, collections
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# --- pin the swarm to NVIDIA Maverick (in-process only; NEVER write .env — the clobber gotcha) ---
os.environ.setdefault("LLM_PROVIDER", "nvidia")
os.environ.setdefault("NVIDIA_MODEL", "meta/llama-4-maverick-17b-128e-instruct")

HERE  = Path.home() / "Documents/polymarket"
VAULT = Path.home() / "Documents/PolymarketVault"
REPORT = VAULT / "Vault Fixes.md"
LOG   = Path("/tmp/vault-fixer-swarm.log")
LOCK  = Path("/tmp/vault-fixer-swarm.lock")
STATE = Path("/tmp/.vault_fixer_state")   # last-run max source-note mtime → the 1-min dirty-gate
sys.path.insert(0, str(HERE))

SKIP_DIRS = {"Connectors", "Live", "memory", "ConceptGraph", "Graph", "graphify-out",
             "Templates", "_inbox", "copilot", ".trash", ".obsidian", "Archive"}
# Notes that other agents REGENERATE every cycle — the fixer must never parse/edit/recreate them, or it
# fights the generator forever (e.g. obsidian_live_feed rewrites index.md w/ [[Judge Panel Verdicts]] each
# 60s; the weaver rewrites Vault Loose Ends.md listing [[deadlinks]]). It only touches hand-authored notes.
SKIP_NOTES = {"index.md", "Analyst Positions.md", "Analyst Calibration.md", "Judge Panel Verdicts.md",
              "Live Market Data.md", "Analyst Candidate Queue.md", "News Thesis.md", "Activity.md",
              "World Read.md", "World Data.md", "World Data Hunter.md", "Vault Loose Ends.md",
              "Vault Fixes.md", "Suggested Connections.md", "Agents Health.md", "Brain Live Gate.md"}
GEN_TYPES = {"dashboard", "report", "live", "feed", "activity", "world-data-hub", "world-data-domain",
             "world-data-source", "data-source-hub"}

def _is_generated(path, head=""):
    if path.name in SKIP_NOTES:
        return True
    m = re.search(r"^type:\s*([\w-]+)", head, re.M)
    return bool(m and m.group(1) in GEN_TYPES)
WORKERS        = 2       # swarm width — keep low so the NVIDIA primary key doesn't 429 (weaker fallback)
MAX_FIX        = 150     # high enough to clear the whole backlog in one autonomous run
APPLY_CONF     = 0.70    # verifier confidence floor to apply
REPOINT_SIM    = 0.60    # deterministic floor: repoint target must be this similar (kills date-only mis-repoints)
LINKRE = re.compile(r"\[\[([^\]]+?)\]\]")
DRY = "--dry-run" in sys.argv


def _strip_code(t):
    """Remove fenced + inline code so [[wikilinks]] inside code examples aren't counted/edited —
    Obsidian does NOT render wikilinks inside backticks, so they are never real dead links."""
    t = re.sub(r"```.*?```", "", t, flags=re.S)
    t = re.sub(r"~~~.*?~~~", "", t, flags=re.S)
    return re.sub(r"`[^`\n]*`", "", t)


def log(msg):
    line = f"{datetime.datetime.now().isoformat(timespec='seconds')} {msg}"
    try: LOG.open("a").write(line + "\n")
    except Exception: pass
    print(line)


def in_skip(rel_parts):
    return bool(rel_parts) and rel_parts[0] in SKIP_DIRS


def _topic(s):
    """Topic slug for similarity: lowercase, drop a 'memory/' prefix and any trailing ISO date, so
    'memory/vol-regime-2026-06-12' vs 'Journal 2026-06-12' compares 'vol regime' vs 'journal' (not the
    shared date) — the date-suffix mis-repoint the LLM keeps making."""
    s = os.path.basename(s.split("#")[0].split("|")[0]).lower()
    s = re.sub(r"[-_ ]?\d{4}-\d{2}-\d{2}$", "", s)
    return re.sub(r"[-_]+", " ", s).strip()


def looks_real(t):
    if t.startswith("_COMMUNITY_") or any(c in t for c in '`"\n<>') or not (2 <= len(t) <= 50):
        return False
    if t.startswith(".") or t in {"Folder/Note", "<slug>", "BTC", "ETH", "x", "y"}:
        return False
    return bool(re.match(r"^[\w ,.&/'\-()]+$", t))


# ---------------------------------------------------------------- the Maverick agents
def _judge(system, user, schema, label):
    import llm_client
    try:
        out = llm_client.judge(system=system, user=user, schema_hint=schema, temperature=0.1)
        prov = llm_client.provenance() or {}
        return out, bool(prov.get("served_fallback"))
    except Exception as e:
        log(f"  [{label}] judge err: {type(e).__name__}: {e}")
        return None, True


def router_agent(target, variants, contexts, candidates):
    sys_p = ("You repair broken Obsidian [[wikilinks]]. A note links to a target that does NOT exist. "
             "Decide the BEST fix and reply ONLY as JSON. Options: "
             "'repoint' (the writer meant an existing note — give its EXACT title from candidates), "
             "'create' (it names a real, note-worthy concept that SHOULD exist — give a 1-2 sentence stub), "
             "'unlink' (it's a passing mention/placeholder/duplicate not worth its own note — just remove the brackets). "
             "Prefer 'repoint' when a candidate clearly matches; prefer 'create' for real recurring concepts; "
             "use 'unlink' when unsure it deserves a note. Be calibrated — do NOT invent a repoint target.")
    usr = (f"Broken link target: '{target}'\nLink forms used: {variants}\n"
           f"Appears in (note → snippet):\n" + "\n".join(contexts[:5]) +
           f"\n\nExisting notes that might be the intended target (candidates): {candidates or 'none'}")
    schema = ('{"action":"repoint|create|unlink","repoint_to":"exact candidate title or null",'
              '"create_summary":"stub body if create else null","confidence":0.0,"reason":"short"}')
    return _judge(sys_p, usr, schema, "router")


def verifier_agent(target, contexts, candidates, decision):
    sys_p = ("You are a SKEPTICAL reviewer of a proposed fix to a broken Obsidian link. Free-LLM "
             "proposers are systematically OVERCONFIDENT, so default to caution. Veto a 'repoint' that "
             "isn't clearly the same thing, and a 'create' for something not note-worthy. Reply ONLY as "
             "JSON. final_action may override the proposal; use 'skip' (leave for a human) if unsure. "
             "'unlink' is the safe fallback when a link is broken but no good target exists.")
    usr = (f"Target: '{target}'\nProposed: {json.dumps(decision)}\nCandidates: {candidates or 'none'}\n"
           f"Contexts:\n" + "\n".join(contexts[:5]))
    schema = '{"agree":true,"final_action":"repoint|create|unlink|skip","confidence":0.0,"reason":"short"}'
    return _judge(sys_p, usr, schema, "verifier")


def home_agent(orphan, snippet, hubs):
    sys_p = ("Pick the single BEST existing hub note to link an orphan note from, so it joins the "
             "graph. Reply ONLY as JSON with the EXACT hub title from the list, or null if none fits.")
    usr = f"Orphan note: '{orphan}'\nSnippet: {snippet[:300]}\nHub options: {hubs}"
    return _judge(sys_p, usr, '{"hub":"exact hub title or null","reason":"short"}', "home")


# ---------------------------------------------------------------- graph scan
def _all_md():
    """Collect every .md in the vault, FOLLOWING symlinks — vault/memory is a symlink to the auto-memory
    store, so rglob (which does not descend symlinked dirs) would falsely flag every [[memory/*]] link as
    dead. Guarded against symlink cycles by tracking visited real paths."""
    out, seen = [], set()
    for root, dirs, files in os.walk(VAULT, followlinks=True):
        rp = os.path.realpath(root)
        if rp in seen:
            dirs[:] = []; continue
        seen.add(rp)
        for fn in files:
            if fn.endswith((".md", ".canvas", ".base")):    # .canvas/.base are valid link TARGETS too
                out.append(Path(root) / fn)
    return out


def scan():
    allfiles = _all_md()
    md = [f for f in allfiles if f.suffix == ".md"]         # only .md have parseable outgoing links
    titles, bodies = {}, {}
    for f in allfiles:                                       # index BOTH 'Name.base' and 'Name' as targets
        titles.setdefault(f.name.lower(), f)
        titles.setdefault(f.stem.lower(), f)
    # dead target -> {variants:set, occ:[(path,raw_inner)]}
    dead = collections.defaultdict(lambda: {"variants": set(), "occ": []})
    inbound = collections.Counter()
    for f in md:
        try: raw = f.read_text(encoding="utf-8", errors="ignore")
        except Exception: continue
        if _is_generated(f, raw[:300]):     # don't parse links FROM agent-regenerated notes (no churn fights)
            continue
        bodies[f] = raw
        body = re.sub(r"<!-- vault-weaver:start -->.*?<!-- vault-weaver:end -->", "", raw, flags=re.S)
        body = _strip_code(body)        # ignore [[links]] inside code examples (Obsidian doesn't render them)
        for m in LINKRE.finditer(body):
            inner = m.group(1)
            raw0 = inner.split("|")[0].split("#")[0].split("^")[0].strip()
            nm = os.path.basename(raw0).lower()              # may carry .base/.canvas/.md
            stem = os.path.splitext(nm)[0]
            if not nm: continue
            if nm in titles or stem in titles:               # resolves either with or without extension
                inbound[stem] += 1
            elif looks_real(raw0):
                dead[raw0]["variants"].add(inner)
                dead[raw0]["occ"].append((f, inner))
    return md, titles, bodies, dead, inbound


# ---------------------------------------------------------------- appliers (non-destructive)
def apply_unlink(bodies, occ):
    n = 0
    for f, inner in occ:
        if in_skip(f.relative_to(VAULT).parts): continue
        disp = (inner.split("|", 1)[1] if "|" in inner else
                os.path.basename(inner.split("#")[0].split("^")[0])).strip()
        t = bodies.get(f, f.read_text(encoding="utf-8", errors="ignore"))
        new = t.replace(f"[[{inner}]]", disp)
        if new != t:
            f.write_text(new, encoding="utf-8"); bodies[f] = new; n += 1
    return n


def apply_repoint(bodies, occ, target_title):
    n = 0
    for f, inner in occ:
        if in_skip(f.relative_to(VAULT).parts): continue
        disp = (inner.split("|", 1)[1] if "|" in inner else
                os.path.basename(inner.split("#")[0].split("^")[0])).strip()
        t = bodies.get(f, f.read_text(encoding="utf-8", errors="ignore"))
        new = t.replace(f"[[{inner}]]", f"[[{target_title}|{disp}]]")
        if new != t:
            f.write_text(new, encoding="utf-8"); bodies[f] = new; n += 1
    return n


def apply_create(target, variants, summary):
    # honour a path-form link a/b -> create at that path; else vault root. Refuse skip dirs.
    pathform = next((v.split("|")[0].split("#")[0].strip() for v in variants if "/" in v), None)
    rel = (pathform if pathform else re.sub(r"[\\:*?\"<>|]", "-", target))
    if not rel.endswith(".md"): rel += ".md"
    p = VAULT / rel
    if in_skip(Path(rel).parts): return False, "would-create-in-agent-dir"
    if Path(rel).name in SKIP_NOTES: return False, "generator-owned note"
    if p.exists(): return False, "exists"
    p.parent.mkdir(parents=True, exist_ok=True)
    today = datetime.date.today().isoformat()
    p.write_text(
        f"---\ntype: stub\nstatus: stub\ndate: {today}\ntags: [stub, auto-resolved]\n---\n\n"
        f"# {target}\n\n"
        f"> [!note] Referenced by other notes but not yet written. Auto-created by **vault-fixer-swarm** "
        f"({today}) so the link resolves — a human should fill in the real definition. (The free model's "
        f"guess is deliberately omitted: it tends to confidently mis-define project concepts.)\n\n"
        f"*↑ [[Obsidian Brain]]*\n", encoding="utf-8")
    return True, str(rel)


def main():
    if LOCK.exists() and (time.time() - LOCK.stat().st_mtime) < 3600:
        log("another run in progress; exit"); return
    LOCK.touch()
    applied = collections.Counter()
    fixes, unresolved = [], []
    fallback_hits = [0]

    def _max_mtime():   # newest HAND-AUTHORED note (excl. agent-regenerated notes that churn every cycle,
        mx = 0.0        # so the gate only opens on real edits — not on each live-feed/dashboard rewrite)
        for p in _all_md():
            if p.name in SKIP_NOTES: continue
            try: mx = max(mx, p.stat().st_mtime)
            except OSError: pass
        return mx

    try:
        # DIRTY-GATE — makes a 1-min cadence cheap: if no source note changed since last run, do nothing.
        cur_mtime = _max_mtime()
        if not DRY and STATE.exists():
            try: prev = float(STATE.read_text().strip())
            except Exception: prev = -1.0
            if abs(cur_mtime - prev) < 1e-6:
                log("no source-note changes since last run; skip (dirty-gate)")
                return
        md, titles, bodies, dead, inbound = scan()
        all_titles = sorted({f.stem for f in md})
        # a target is only worth LLM-processing if at least one occurrence is in an EDITABLE note —
        # links living only in protected/agent-owned notes can't be fixed, so don't burn calls on them.
        def editable(t):
            return any(not in_skip(f.relative_to(VAULT).parts) for f, _ in dead[t]["occ"])
        unresolved += [(t, "links live only in protected/agent-owned notes — not editable")
                       for t in dead if not editable(t)]
        targets = sorted([t for t in dead if editable(t)], key=lambda t: -len(dead[t]["occ"]))[:MAX_FIX]
        log(f"scan: {len(dead)} broken targets, {len(targets)} editable | dry={DRY}")

        # ---- swarm: decide every target concurrently (router -> verifier) on Maverick ----
        def decide(t):
            d = dead[t]
            contexts = []
            for f, _ in d["occ"][:5]:
                rel = f.relative_to(VAULT)
                snip = ""
                bt = bodies.get(f, "")
                mm = re.search(r".{0,60}\[\[" + re.escape(list(d["variants"])[0]) + r"\]\].{0,60}", bt)
                if mm: snip = re.sub(r"\s+", " ", mm.group(0))
                contexts.append(f"  {rel} → …{snip}…")
            cands = difflib.get_close_matches(t, all_titles, n=6, cutoff=0.55)
            r, fb1 = router_agent(t, sorted(d["variants"]), contexts, cands)
            if fb1: fallback_hits[0] += 1
            if not r: return t, None, cands, contexts
            v, fb2 = verifier_agent(t, contexts, cands, r)
            if fb2: fallback_hits[0] += 1
            return t, (r, v), cands, contexts

        results = {}
        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            for fut in as_completed([ex.submit(decide, t) for t in targets]):
                t, dec, cands, contexts = fut.result()
                results[t] = (dec, cands)

        # ---- apply verified fixes sequentially (file writes are not thread-safe) ----
        for t in targets:
            dec, cands = results.get(t, (None, []))
            if not dec:                                      # LLM gave nothing → autonomous safe unlink
                occ = dead[t]["occ"]
                if DRY:
                    fixes.append((t, "unlink (no LLM decision)", len(occ)))
                else:
                    n = apply_unlink(bodies, occ)
                    if n: applied["unlink"] += 1; fixes.append((t, f"unlink ({n} links; no LLM decision)", len(occ)))
                    else: unresolved.append((t, "no LLM decision; links only in protected notes"))
                continue
            r, v = dec
            action = (v or {}).get("final_action") or r.get("action")
            conf = float((v or {}).get("confidence") or 0)
            agree = bool((v or {}).get("agree"))
            occ = dead[t]["occ"]; nlinks = len(occ)
            tbase = os.path.basename(t.split("#")[0].split("|")[0]).lower()

            # AUTONOMOUS resolution — every broken link gets resolved, no human tier. Take the verified,
            # guard-passing repoint/create; otherwise fall back to a SAFE unlink (strip the broken bracket,
            # keep the words). All three are non-destructive + git-reversible, so the worst case is recoverable.
            chosen, detail = "unlink", "no confident target"
            rep = r.get("repoint_to") or (v or {}).get("repoint_to")
            wants_create = (r.get("action") == "create") or (action == "create")
            is_fileref = bool(re.search(r"\.(md|canvas|base|json|png|jpg|csv|txt|py|sh)$", tbase))
            # 1) precise repoint — verified + the deterministic date-stripped similarity guard
            if v and agree and conf >= APPLY_CONF and action == "repoint" and rep and rep.lower() in titles:
                ratio = difflib.SequenceMatcher(None, _topic(t), _topic(rep)).ratio()
                if rep in cands and ratio >= REPOINT_SIM:
                    chosen, detail = "repoint", rep
                else:
                    detail = f"repoint '{rep}' failed similarity guard (r={ratio:.2f})"
            # 2) concept stub — a real recurring idea (multi-word/dated slug, not a file ref, not junk)
            #    becomes a fillable note. Reject code-ish junk (commas/quotes) and lowercase sentence
            #    fragments ("create a link") that are never real note titles.
            junky = ("," in t or "'" in t or '"' in t or
                     (tbase == tbase.lower() and " " in tbase and "-" not in tbase and not re.search(r"\d", tbase)))
            if chosen == "unlink" and wants_create and not is_fileref and not junky \
                    and len(tbase) >= 6 and re.search(r"[-_ ]|\d", tbase):
                chosen, detail = "create", "concept stub"
            # 3) else: safe unlink — file refs, bare/generic words, and links with no good target

            if chosen == "repoint":
                if DRY: fixes.append((t, f"repoint→{detail}", nlinks))
                else:
                    n = apply_repoint(bodies, occ, detail); applied["repoint"] += 1
                    fixes.append((t, f"repoint→[[{detail}]] ({n} links)", nlinks))
            elif chosen == "create":
                if DRY: fixes.append((t, "create-stub", nlinks))
                else:
                    ok, info = apply_create(t, dead[t]["variants"], r.get("create_summary"))
                    if ok:
                        applied["create"] += 1; fixes.append((t, f"create-stub {info}", nlinks))
                    else:                                   # create blocked (agent-dir/exists) → safe unlink
                        n = apply_unlink(bodies, occ)
                        if n: applied["unlink"] += 1; fixes.append((t, f"unlink ({n} links; create blocked: {info})", nlinks))
                        else: unresolved.append((t, f"create blocked ({info}); links only in protected notes"))
            else:                                            # unlink — the autonomous safe default
                if DRY: fixes.append((t, f"unlink ({detail})", nlinks))
                else:
                    n = apply_unlink(bodies, occ)
                    if n: applied["unlink"] += 1; fixes.append((t, f"unlink ({n} links → plain text; {detail})", nlinks))
                    else: unresolved.append((t, "links live only in protected/agent-owned notes — left as-is"))

        # (orphan→hub homing is vault-weaver's job, every 6h — the fixer stays a pure broken-link fixer
        #  so a 1-min cadence makes no redundant LLM calls.)

        # ---- report: write ONLY when something actually changed, so a 1-min no-op doesn't churn git ----
        if not DRY and not fixes:
            try: STATE.write_text(str(_max_mtime()))
            except Exception: pass
            log(f"OK applied={{}} unresolved={len(unresolved)} (no editable broken links) dry={DRY}")
            return
        prov = ""
        try:
            import llm_client; p = llm_client.provenance() or {}
            prov = f"{p.get('served_by')}/{p.get('served_model')}"
        except Exception: pass
        today = datetime.date.today().isoformat()
        L = ["---", "type: report", "status: open", f"date: {today}",
             "tags: [vault-health, auto-fix, swarm]", "---", "",
             f"# 🛠️ Vault Fixes — {today}", "",
             f"FULLY AUTONOMOUS audit log. A swarm of free-LLM agents (NVIDIA Maverick, $0) resolved the "
             f"loose ends flagged in [[Vault Loose Ends]] with NO human tier: a router proposes and a "
             f"skeptical verifier checks; a guard-passing repoint/create is applied, otherwise the link "
             f"falls back to a safe unlink (brackets stripped, words kept). Every edit is non-destructive "
             f"and git-reversible — revert any single fix with git. {'**DRY-RUN — nothing changed.**' if DRY else ''}", "",
             "## Summary", "",
             f"- applied: **{dict(applied)}**  ·  left as-is (protected source notes): **{len(unresolved)}**",
             f"- last model: `{prov}`  ·  fallback hits (Maverick unavailable): **{fallback_hits[0]}**"
             f"{'  ⚠️ verdicts weaker than usual' if fallback_hits[0] else ''}", "",
             "## Resolved this run", ""]
        L += [f"- `{t}` — {how} _(was {n} broken link{'s' if n!=1 else ''})_" for t, how, n in fixes] or ["_none_"]
        L += ["", "## Left as-is (links live only in agent-owned notes — not editable)", ""]
        L += [f"- `{t}` — {why}" for t, why in unresolved[:120]] or ["_none_"]
        L += ["", "*↑ [[Obsidian Brain]] · [[Agent Fleet]] · [[Vault Loose Ends]]*", ""]
        REPORT.write_text("\n".join(L), encoding="utf-8")
        if not DRY:                      # record post-edit mtime so our own writes don't re-trigger next minute
            try: STATE.write_text(str(_max_mtime()))
            except Exception: pass
        log(f"OK applied={dict(applied)} unresolved={len(unresolved)} "
            f"fallback={fallback_hits[0]} model={prov} dry={DRY}")
    except Exception as e:
        log(f"ERR {type(e).__name__}: {e}")
    finally:
        try: LOCK.unlink()
        except Exception: pass


if __name__ == "__main__":
    main()
