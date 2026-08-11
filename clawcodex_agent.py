#!/usr/bin/env python3
"""
clawcodex_agent.py — structured, SAFE launcher for the ClawCodex coding agent.

ClawCodex (oss-bots/clawcodex) is a Python rebuild of Claude Code. This wrapper pins the
safe invocation so the dangerous flags can never be typed by habit:
  - backend = FREE NVIDIA NIM (key read from polymarket/.env, never printed)
  - dangerous tools HARD-BLOCKED every run (Bash, Write, Edit, NotebookEdit, ...)
  - public-API reads via WebFetch only when an explicit --allow-domain is passed
    (scoped settings allowlist, NOT --dangerously-skip-permissions)
  - read-only; never wired to launchd; you DRIVE it, it is not autonomous.

⚠️ HALLUCINATION GUARD: if a tool is denied, the model may fabricate data instead of
failing. Only trust a "data" answer when --allow-domain matched the URL (we log which).

Usage:
  python3 clawcodex_agent.py "explain what scalp_lab.py does"          # reasoning only
  python3 clawcodex_agent.py --allow-domain api.coingecko.com \
      "WebFetch https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd; report BTC USD"
"""
import argparse, json, os, re, subprocess, sys, pathlib

CLAW   = os.path.expanduser("~/Documents/polymarket/oss-bots/clawcodex")
PY     = os.path.join(CLAW, ".venv/bin/python")
ENVF   = os.path.expanduser("~/Documents/polymarket/.env")
CFG    = pathlib.Path.home() / ".clawcodex"
DANGER = ["Bash", "Write", "Edit", "NotebookEdit", "CronCreate", "CronDelete",
          "TeamCreate", "TeamDelete", "Workflow"]

def ensure_config(allow_domain: str | None):
    """Write ~/.clawcodex/{config.json,settings.json}: NVIDIA backend + scoped WebFetch."""
    env = {}
    for ln in open(ENVF):
        m = re.match(r"\s*([A-Z_]+)=(.*)", ln)
        if m:
            env[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    key = env.get("NVIDIA_API_KEY", "")
    if not key.startswith("nvapi-"):
        sys.exit("no NVIDIA_API_KEY in polymarket/.env")
    CFG.mkdir(exist_ok=True)
    cfg = {"default_provider": "openai",
           "providers": {"openai": {"api_key": key,
               "base_url": "https://integrate.api.nvidia.com/v1",
               "default_model": env.get("NVIDIA_MODEL", "meta/llama-3.3-70b-instruct")}}}
    (CFG / "config.json").write_text(json.dumps(cfg, indent=2)); os.chmod(CFG / "config.json", 0o600)
    # scoped WebFetch allow ONLY for an explicit domain (else no tool fetch at all)
    allow = ["WebFetch", f"WebFetch(domain:{allow_domain})"] if allow_domain else []
    (CFG / "settings.json").write_text(json.dumps({"permissions": {"allow": allow}}, indent=2))
    return allow

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("prompt")
    ap.add_argument("--allow-domain", default=None,
                    help="public host to permit WebFetch on (e.g. api.coingecko.com). Omit = no fetch.")
    ap.add_argument("--max-turns", type=int, default=6)
    a = ap.parse_args()
    allow = ensure_config(a.allow_domain)
    if a.allow_domain:
        print(f"[clawcodex] WebFetch allowed ONLY for: {a.allow_domain} (trust data only if it matched)",
              file=sys.stderr)
    else:
        print("[clawcodex] no --allow-domain → tools denied; reasoning only (do NOT trust 'data' answers)",
              file=sys.stderr)
    cmd = [PY, "-m", "src.cli", "-p", "--max-turns", str(a.max_turns),
           "--disallowed-tools", ",".join(DANGER), a.prompt]
    sys.exit(subprocess.run(cmd, cwd=CLAW).returncode)

if __name__ == "__main__":
    main()
