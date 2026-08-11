#!/usr/bin/env python3
"""bug_heal.py — multi-stage bug finder+fixer that runs on the FREE NVIDIA chain ($0 metered).

The standalone, Claude-free port of the bug-heal fleet: Python does the orchestration, file
edits, reproduction and verification; NVIDIA Maverick (via llm_client) does the reasoning
(scout for bugs, propose the minimal fix). All LLM calls are $0 metered.

Pipeline per target:  scout -> (per bug) reproduce -> fix -> verify -> safety -> verdict
- reproduce/verify are OBJECTIVE: the scout returns a `test` snippet that must FAIL on the
  buggy code and PASS after the fix. No "it looks fixed" — it has to actually pass.
- edits are in place with an auto-backup and REVERT-ON-FAIL; the file is only kept changed
  when the fix verifies AND --land is passed (default = dry-run: report the diff, revert).
- a safety regex VETOES any fix touching orders/keys/funds/destructive ops (paper-only).

Usage:  cd ~/Documents/polymarket && LLM_PROVIDER=nvidia python3 bug_heal.py <target.py> [--land]
Python targets (v1). Honest default verdict: nothing found / nothing landed.
"""
import sys, os, re, json, subprocess, shutil, argparse
# Pin Maverick BEFORE llm_client loads .env (setdefault won't override) — immune to .env churn.
os.environ.setdefault("NVIDIA_MODEL", "meta/llama-4-maverick-17b-128e-instruct")
import llm_client as L

DANGER = re.compile(
    r'POLYMARKET_PRIVATE_KEY|/clob|clob.*post|web3|send_transaction|eth_send|'
    r'rm\s+-rf|\bpkill\b|\bkillall\b|launchctl\s+(disable|unload|bootout)|'
    r'\.env\b|[A-Z]+_API_KEY|secret|private_key', re.I)

MODEL = os.environ.get("NVIDIA_MODEL")  # inherits .env (maverick); llm_client handles it

def _run_test(target_dir, snippet, timeout=20):
    """Run a python test snippet with the target dir importable. Return (passed, output)."""
    try:
        r = subprocess.run([sys.executable, "-c", snippet], cwd=target_dir,
                           capture_output=True, text=True, timeout=timeout)
        return (r.returncode == 0, (r.stdout + r.stderr).strip()[-400:])
    except subprocess.TimeoutExpired:
        return (False, "timeout")
    except Exception as e:
        return (False, f"{type(e).__name__}: {e}")

def scout(path, code):
    schema = ('{"bugs":[{"title":"...","line":0,"severity":"crash|wrong-result|leak",'
              '"why":"...","test":"<python snippet that imports from the module and asserts '
              'CORRECT behavior; raises/exits-nonzero on the buggy code, exits 0 once fixed>"}]}')
    mod = os.path.splitext(os.path.basename(path))[0]
    out = L.judge(
        "You are a precise bug finder. Output ONLY JSON. Report only concrete defects (crashes, "
        "wrong results, leaks) — no style nits. For each, write a `test`: a self-contained python "
        f"snippet that does `from {mod} import ...`, exercises the function, and `assert`s the "
        "CORRECT result (so it FAILS now and PASSES once fixed). Empty list if no real bugs.",
        f"Module name: {mod}\nFile {path}:\n\n{code[:12000]}",
        schema_hint=schema)
    return out.get("bugs", []) if isinstance(out, dict) else []

def propose_fix(path, code, bug):
    schema = ('{"old_string":"<EXACT unique substring of the file to replace>",'
              '"new_string":"<replacement>","explanation":"...","escalate":false}')
    out = L.judge(
        "You fix bugs MINIMALLY via one find/replace. Output ONLY JSON. `old_string` MUST be an "
        "EXACT, UNIQUE substring copied verbatim from the file (enough context to be unique). "
        "Change as little as possible. Set escalate=true (and leave strings empty) if a correct "
        "fix would touch live orders, keys, funds, or destructive shell.",
        f"File {path}:\n\n{code[:12000]}\n\nBUG: {bug.get('title')} (line {bug.get('line')})\n"
        f"why: {bug.get('why')}\nThe fix must make this test pass:\n{bug.get('test','')}",
        schema_hint=schema)
    return out if isinstance(out, dict) else {}

def heal_one(path, bug, land):
    d = os.path.dirname(os.path.abspath(path))
    test = bug.get("test", "")
    res = {"bug": bug.get("title"), "line": bug.get("line")}
    if not test:
        return {**res, "verdict": "DROP", "reason": "no test snippet"}
    # 1. reproduce: test must FAIL on current code
    passed, _ = _run_test(d, test)
    if passed:
        return {**res, "verdict": "DROP", "reason": "not reproduced (test already passes)"}
    # 2. propose fix
    code = open(path).read()
    fix = propose_fix(path, code, bug)
    if fix.get("escalate") or not fix.get("old_string"):
        return {**res, "verdict": "ESCALATE", "reason": fix.get("explanation", "fixer escalated / no fix")}
    old, new = fix["old_string"], fix.get("new_string", "")
    # 3. safety veto (independent regex on the change)
    if DANGER.search(old + "\n" + new):
        return {**res, "verdict": "ESCALATE", "reason": "SAFETY VETO: fix touches danger surface"}
    if code.count(old) != 1:
        return {**res, "verdict": "ESCALATE", "reason": f"old_string not unique ({code.count(old)} matches)"}
    # 4. apply (with backup), verify, revert-on-fail
    bak = path + ".bugheal.bak"
    shutil.copy(path, bak)
    open(path, "w").write(code.replace(old, new, 1))
    syntax_ok, _ = _run_test(d, f"import py_compile,sys; py_compile.compile({json.dumps(os.path.basename(path))}, doraise=True)")
    passed_after, out_after = _run_test(d, test)
    diff = f"- {old.strip()[:200]}\n+ {new.strip()[:200]}"
    if syntax_ok and passed_after:
        if land:
            os.remove(bak)
            return {**res, "verdict": "LAND", "applied": True, "diff": diff, "explanation": fix.get("explanation")}
        else:  # dry-run: revert, report ready-to-land
            shutil.move(bak, path)
            return {**res, "verdict": "LAND", "applied": False, "diff": diff,
                    "explanation": fix.get("explanation"), "note": "verified; reverted (pass --land to keep)"}
    else:  # fix failed verification -> revert
        shutil.move(bak, path)
        return {**res, "verdict": "ESCALATE", "reason": f"fix failed verify (syntax={syntax_ok} test={passed_after}) {out_after[:120]}"}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("target")
    ap.add_argument("--land", action="store_true", help="keep verified fixes (default: dry-run + revert)")
    ap.add_argument("--max", type=int, default=8)
    a = ap.parse_args()
    if not os.path.isfile(a.target):
        print(json.dumps({"error": f"not a file: {a.target}"})); return
    code = open(a.target).read()
    bugs = scout(a.target, code)[:a.max]
    prov = L.provenance()
    results = [heal_one(a.target, b, a.land) for b in bugs]
    summary = {
        "target": a.target, "served_by": prov.get("served_by"), "model": prov.get("served_model"),
        "fallback": prov.get("served_fallback"), "land_mode": a.land,
        "found": len(bugs),
        "LAND": sum(r["verdict"] == "LAND" for r in results),
        "ESCALATE": sum(r["verdict"] == "ESCALATE" for r in results),
        "DROP": sum(r["verdict"] == "DROP" for r in results),
    }
    print(json.dumps({"summary": summary, "results": results}, indent=2))

if __name__ == "__main__":
    main()
