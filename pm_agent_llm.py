#!/usr/bin/env python3
"""pm_agent_llm.py — shared LLM layer for the 24/7 pm-agents (PAPER research infra).

Provider-agnostic OpenAI-compatible chat call. YOU supply a FREE API in
`.agent_llm.json` (OpenRouter free models, Groq, Together, or a local Ollama at
http://localhost:11434/v1) — the agents then run on YOUR LLM at zero cost. Safe
no-op until the key is filled (so the watchdog can call it harmlessly meanwhile).

The agents do NOT need tool-calling: pm_agents_24x7.py gathers each agent's data
in Python (deterministic), then calls run_agent() to get the LLM's judgment, then
writes it to the Obsidian vault. Any plain chat endpoint works.

.agent_llm.json (create it):
  { "base_url": "https://openrouter.ai/api/v1",
    "api_key": "PASTE_FREE_KEY",
    "model": "meta-llama/llama-3.3-70b-instruct:free" }
"""
import os, json, time, urllib.request
HERE = os.path.dirname(os.path.abspath(__file__))
CFG = os.path.join(HERE, ".agent_llm.json")
VAULT = os.path.expanduser("~/Documents/PolymarketVault/Reports")


def _load_env():
    """Seed os.environ from .env (no override) so .agent_llm.json can reference a key
    that lives ONLY in gitignored .env (e.g. "env:NVIDIA_API_KEY"), keeping this
    tracked config file secret-free."""
    p = os.path.join(HERE, ".env")
    if os.path.exists(p):
        for line in open(p):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _resolve_key(key):
    """A key of "env:VAR" or "$VAR" is resolved from the environment, so the secret
    stays in .env (gitignored), never in this tracked JSON. Plain keys pass through."""
    key = (key or "").strip()
    if key.startswith("env:"):
        return os.environ.get(key[4:], "").strip()
    if key.startswith("$"):
        return os.environ.get(key[1:], "").strip()
    return key


def load_cfg():
    if not os.path.exists(CFG):
        return None, "no .agent_llm.json (create it with your free OpenAI-compatible API)"
    try:
        c = json.load(open(CFG))
    except Exception as e:
        return None, f"bad .agent_llm.json: {e}"
    _load_env()
    c["api_key"] = _resolve_key(c.get("api_key"))
    key = c["api_key"]
    if not key or "PASTE" in key.upper():
        return None, "api_key placeholder not replaced yet"
    c.setdefault("base_url", "https://openrouter.ai/api/v1")
    c.setdefault("model", "meta-llama/llama-3.3-70b-instruct:free")
    return c, None


def call_llm(system, user, max_tokens=800, temperature=0.2, tries=2, agent=None):
    """OpenAI-compatible POST {base_url}/chat/completions. Returns (text, err).
    If `agent` matches a key in cfg["overrides"], that agent uses a different
    endpoint/model (e.g. the reasoning-heavy agents → NVIDIA's 70B while the
    numeric ones stay on free local Ollama)."""
    c, err = load_cfg()
    if err:
        return None, err
    if agent:
        ov = (c.get("overrides") or {}).get(agent.replace("pm-", ""))
        ov_key = _resolve_key(ov.get("api_key")) if ov else ""
        if ov and ov_key and "PASTE" not in ov_key.upper():
            ov = {**ov, "api_key": ov_key}
            c = {**c, **{k: ov[k] for k in ("base_url", "api_key", "model") if ov.get(k)}}
    body = json.dumps({
        "model": c["model"],
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "max_tokens": max_tokens, "temperature": temperature,
    }).encode()
    url = c["base_url"].rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {c['api_key']}"}
    # OpenRouter likes these but they're harmless elsewhere
    headers.setdefault("HTTP-Referer", "https://localhost/polymarket-bot")
    headers.setdefault("X-Title", "polymarket-pm-agents")
    last = None
    for a in range(tries):
        try:
            req = urllib.request.Request(url, data=body, headers=headers)
            with urllib.request.urlopen(req, timeout=240) as r:   # 240s covers Ollama cold-load (4.7GB) of a staggered scheduled run
                j = json.load(r)
            return j["choices"][0]["message"]["content"].strip(), None
        except Exception as e:
            last = e
            time.sleep(2.0 * (a + 1))
    return None, f"llm call failed: {last}"


def model_for(agent):
    """The model name call_llm() will ACTUALLY use for this agent (for report bylines).
    Mirrors call_llm's resolution: base model, unless the agent has an override with a valid key."""
    c, err = load_cfg()
    if err:
        return "unknown"
    m = c.get("model", "unknown")
    if agent:
        ov = (c.get("overrides") or {}).get(agent.replace("pm-", ""))
        ov_key = _resolve_key(ov.get("api_key")) if ov else ""
        if ov and ov_key and "PASTE" not in ov_key.upper() and ov.get("model"):
            m = ov["model"]
    return m


def run_agent(name, system, data, task, vault_file=None):
    """Run one agent: feed gathered `data` + `task` to the LLM under the agent's
    `system` role, write the verdict to the Obsidian vault, return (text, err)."""
    user = f"{task}\n\n=== GATHERED DATA (read-only, current) ===\n{data}"
    out, err = call_llm(system, user, agent=name)
    stamp = time.strftime("%Y-%m-%d %H:%M")
    if err:
        body = f"# {name}\n\n_run {stamp} — SKIPPED: {err}_\n"
    else:
        body = (f"# {name}\n\n_auto-run {stamp} · paper research · {model_for(name)}_\n\n{out}\n")
    if vault_file:
        try:
            os.makedirs(VAULT, exist_ok=True)
            open(os.path.join(VAULT, vault_file), "w").write(body)
        except Exception:
            pass
    # append to the audit ledger (track record substrate for pm_grade.py)
    try:
        summary = " ".join((out or err or "").split())[:240]
        rec = {"ts": stamp, "agent": name, "ok": (not err), "summary": summary}
        open(os.path.join(HERE, "pm_agent_ledger.jsonl"), "a").write(json.dumps(rec) + "\n")
    except Exception:
        pass
    return (out if not err else None), err


if __name__ == "__main__":
    c, err = load_cfg()
    print("config:", "OK ("+c["model"]+" @ "+c["base_url"]+")" if not err else f"NOT READY — {err}")
    if not err:
        t, e = call_llm("You are a test.", "Reply with exactly: LLM_OK")
        print("live call:", t or e)
