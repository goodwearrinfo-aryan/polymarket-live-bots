#!/usr/bin/env python3
"""
llm_client.py — provider-agnostic, OpenAI-compatible LLM client. The "free unlock":
the analyst judgment agents (refuter, resolution-checker, news-arb, etc.) gather their
data deterministically in Python, then call THIS for the judgment — so they run on YOUR
free LLM at zero Claude-Code-token cost. No tool-calling, no external deps (urllib only),
no credential of the assistant's involved — you point it at any OpenAI-compatible endpoint.

Two ways to activate (in ~/Documents/polymarket/.env or the shell):
  A) Pick a named provider — one line + its key:
       LLM_PROVIDER=nvidia          NVIDIA_API_KEY=nvapi-...     (build.nvidia.com, free)
       LLM_PROVIDER=groq            GROQ_API_KEY=gsk_...
     (base_url + model come from the preset; override the model with <NAME>_MODEL,
      e.g. NVIDIA_MODEL=nvidia/llama-3.1-nemotron-70b-instruct)
  B) Explicit endpoint — the 3 generic vars (any OpenAI-compatible server):
       LLM_BASE_URL   e.g. https://api.groq.com/openai/v1
       LLM_API_KEY    your free key   (Ollama: leave unset / "ollama")
       LLM_MODEL      e.g. llama-3.3-70b-versatile

Free-provider presets (LLM_PROVIDER name → base / default model, all OpenAI-compatible):
  groq        https://api.groq.com/openai/v1            llama-3.3-70b-versatile                (fast, generous free tier)
  nvidia      https://integrate.api.nvidia.com/v1       meta/llama-3.1-70b-instruct            (build.nvidia.com, free credits)
  openrouter  https://openrouter.ai/api/v1              meta-llama/llama-3.3-70b-instruct:free
  together    https://api.together.xyz/v1               meta-llama/Llama-3.3-70B-Instruct-Turbo-Free
  ollama      http://localhost:11434/v1                 llama3.1                               (fully local, no key)

Autonomous failover (works when you're offline / unattended):
  The judgment agents call chat()/judge(), which tries a CHAIN of providers in order:
    primary (above)  →  LLM_FALLBACKS (comma-sep provider names)  →  local ollama (auto)
  Local ollama is auto-appended as the last resort — keyless, no network — so an unattended
  cycle keeps judging even if the cloud key dies or rate-limits with nobody online to fix it.
    LLM_FALLBACKS=groq,openrouter   # try these (only if their key is set) before ollama
    LLM_FALLBACKS=                  # empty → opt out of all fallbacks
    NO_OLLAMA_FALLBACK=1            # keep cloud fallbacks but drop the local net
    OLLAMA_MODEL=llama3.1:8b        # pick which local model the net uses

Usage:
  from llm_client import judge, chat, configured
  verdict = judge(system="You are a skeptic...", user="Refute: ...", schema_hint='{"refuted":bool,"confidence":int,"why":str}')
  text    = chat([{"role":"user","content":"hi"}])
Run `python3 llm_client.py --selftest` to check config + (if set) do a live ping.
"""
import os, json, sys, urllib.request, urllib.error

BASE = os.path.dirname(os.path.abspath(__file__))


def _load_env():
    """Mirror the repo convention: seed os.environ from .env without overriding real env."""
    p = os.path.join(BASE, ".env")
    if os.path.exists(p):
        for line in open(p):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                if " #" in v:  # strip inline comments (e.g. KEY=value  # note)
                    v = v[:v.index(" #")]
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()


# OpenAI-compatible provider presets:  name -> (base_url, default_model, key_env_var)
PROVIDERS = {
    "groq":       ("https://api.groq.com/openai/v1",      "llama-3.3-70b-versatile",                      "GROQ_API_KEY"),
    # no-CC, open-weight, forever-free rate-limited tiers (added 2026-06-21) — peers of groq,
    # both OpenAI-compatible. Cerebras ~1M tok/day; SambaNova forever-free RPM tier (Llama up to 405B).
    "cerebras":   ("https://api.cerebras.ai/v1",          "gpt-oss-120b",                                 "CEREBRAS_API_KEY"),  # free tier serves gpt-oss-120b (Apache-2.0) + zai-glm-4.7; override via CEREBRAS_MODEL
    # FAST LANE (2026-07-30, benchmarked): sub-second, 200+ tok/s — use for high-frequency/latency-sensitive tasks.
    "groqfast":   ("https://api.groq.com/openai/v1",      "llama-3.1-8b-instant",                         "GROQ_API_KEY"),      # 229 tok/s, 8B — quickest for classify/extract/triage
    "cerebrasfast":("https://api.cerebras.ai/v1",         "gpt-oss-120b",                                 "CEREBRAS_API_KEY"),  # 253 tok/s, 120B — fastest big model (quality + speed)
    "sambanova":  ("https://api.sambanova.ai/v1",         "Meta-Llama-3.3-70B-Instruct",                  "SAMBANOVA_API_KEY"),
    # NVIDIA build.nvidia.com cloud models — maverick was RETIRED (HTTP 410 Gone, 2026-07-28);
    # llama-3.3-70b is the live default. The nvidia-* presets below expose the other strong
    # cloud models on the SAME key, so LLM_FALLBACKS can walk the whole NVIDIA catalog
    # before dropping to local ollama. Override any via NVIDIA_MODEL / <NAME>_MODEL.
    "nvidia":     ("https://integrate.api.nvidia.com/v1", "meta/llama-3.1-70b-instruct",                  "NVIDIA_API_KEY"),
    "nvidia2":    ("https://integrate.api.nvidia.com/v1", "meta/llama-3.1-70b-instruct",                  "NVIDIA_API_KEY_2"),  # 2nd free NVIDIA key — extra capacity, rotates in when nvidia #1 throttles (429)
    "nvidia3":    ("https://integrate.api.nvidia.com/v1", "meta/llama-3.1-70b-instruct",                  "NVIDIA_API_KEY_3"),  # 3rd free NVIDIA key — more capacity
    "nvdeepseek": ("https://integrate.api.nvidia.com/v1", "deepseek-ai/deepseek-v4-pro",                  "NVIDIA_API_KEY"),  # DeepSeek V4 Pro — strongest reasoner on the catalog
    "nvkimi":     ("https://integrate.api.nvidia.com/v1", "moonshotai/kimi-k2.6",                         "NVIDIA_API_KEY"),  # Kimi K2.6 MoE
    "nvglm":      ("https://integrate.api.nvidia.com/v1", "z-ai/glm-5.2",                                 "NVIDIA_API_KEY"),  # GLM 5.2
    "nvnemotron": ("https://integrate.api.nvidia.com/v1", "nvidia/nemotron-3-super-120b-a12b",            "NVIDIA_API_KEY"),  # Nemotron 3 Super 120B MoE
    "nvultra":    ("https://integrate.api.nvidia.com/v1", "nvidia/nemotron-3-ultra-550b-a55b",            "NVIDIA_API_KEY"),  # Nemotron 3 Ultra 550B — strongest live model, maverick replacement (2026-07-30)
    "nvsuper49":  ("https://integrate.api.nvidia.com/v1", "nvidia/llama-3.3-nemotron-super-49b-v1.5",     "NVIDIA_API_KEY"),  # Llama-3.3 Nemotron Super 49B v1.5 — live (2026-07-30)
    "nvgptoss":   ("https://integrate.api.nvidia.com/v1", "openai/gpt-oss-120b",                          "NVIDIA_API_KEY"),  # gpt-oss-120b (Apache-2.0)
    "openrouter": ("https://openrouter.ai/api/v1",        "meta-llama/llama-3.3-70b-instruct:free",       "OPENROUTER_API_KEY"),
    "together":   ("https://api.together.xyz/v1",         "meta-llama/Llama-3.3-70B-Instruct-Turbo-Free", "TOGETHER_API_KEY"),
    # keyless resilience tier (no API key): OpenAI-compatible gpt-oss-20b (Apache-2.0) on the
    # anonymous pollinations.ai tier. key_env=None → reuses the ollama no-auth path (no Authorization
    # header). ~1 req/15s anonymous rate limit + variable latency → a fallback ABOVE the local floor,
    # never a primary. model alias "openai" maps to gpt-oss-20b; override via PUBLIC_MODEL.
    "public":     ("https://text.pollinations.ai/openai", "openai",                                       None),
    "ollama":     ("http://localhost:11434/v1",           "llama3.1",                                     None),
}


def _cfg():
    """Resolve (base_url, api_key, model). Precedence:
      1. LLM_PROVIDER=<name> → that preset wins: base+model from the preset, key from
         <NAME>_API_KEY (e.g. NVIDIA_API_KEY), model overridable via <NAME>_MODEL.
         Lets you paste NVIDIA_API_KEY + set LLM_PROVIDER=nvidia and flip the agents to
         NVIDIA without touching the LLM_* lines (avoids a stale model name leaking across).
      2. Explicit LLM_BASE_URL / LLM_MODEL / LLM_API_KEY (back-compat default).
      3. Auto: an NVIDIA_API_KEY is present and nothing else is configured → nvidia.
    """
    prov = os.environ.get("LLM_PROVIDER", "").strip().lower()
    base = os.environ.get("LLM_BASE_URL", "").rstrip("/")
    model = os.environ.get("LLM_MODEL", "")
    key = os.environ.get("LLM_API_KEY", "")
    if not prov and not base and os.environ.get("NVIDIA_API_KEY"):
        prov = "nvidia"
    if prov in PROVIDERS:
        p_base, p_model, p_keyenv = PROVIDERS[prov]
        base = p_base
        model = os.environ.get(f"{prov.upper()}_MODEL", "") or p_model
        if p_keyenv:
            key = os.environ.get(p_keyenv, "") or key
        else:
            key = key or "ollama"
    return base, key, model


def configured():
    return bool(_provider_chain())


def _resolve(name):
    """Resolve a preset provider name → (label, base, key, model), or None if its key is
    missing (a keyed provider we can't reach is skipped rather than failing the chain).
    Local ollama needs no key, so it always resolves when named."""
    if name not in PROVIDERS:
        return None
    p_base, p_model, p_keyenv = PROVIDERS[name]
    if p_keyenv:
        key = os.environ.get(p_keyenv, "")
        if not key:
            return None
    else:
        key = "ollama"
    model = os.environ.get(f"{name.upper()}_MODEL", "") or p_model
    return (name, p_base, key, model)


def _provider_chain():
    """Ordered list of (label, base, key, model) the judgment agents try in turn.
    Primary first (LLM_PROVIDER / explicit LLM_* / auto-nvidia), then each name in
    LLM_FALLBACKS, with local OLLAMA auto-appended as the always-on, keyless, OFFLINE
    safety net — so an unattended cycle keeps judging even if the cloud key dies or
    rate-limits while nobody's online. Set LLM_FALLBACKS= (empty) to opt out of the chain;
    set NO_OLLAMA_FALLBACK=1 to drop only the auto local net."""
    chain, seen = [], set()
    base, key, model = _cfg()
    prov0 = (os.environ.get("LLM_PROVIDER", "").strip().lower()
             or ("nvidia" if (not os.environ.get("LLM_BASE_URL") and os.environ.get("NVIDIA_API_KEY")) else "explicit"))
    if base and model:
        chain.append((prov0, base, key, model)); seen.add((base, model, key))

    raw = os.environ.get("LLM_FALLBACKS")
    fbs = [f.strip().lower() for f in (raw if raw is not None else "").split(",") if f.strip()]
    # auto-append the local model as the last resort (unless opted out or already primary)
    if not os.environ.get("NO_OLLAMA_FALLBACK") and prov0 != "ollama" and "ollama" not in fbs:
        fbs.append("ollama")
    for name in fbs:
        r = _resolve(name)
        if r and (r[1], r[3], r[2]) not in seen:  # dedup by (base, model, KEY) so multiple keys to one endpoint all rotate
            chain.append(r); seen.add((r[1], r[3], r[2]))
    return chain


def _one_chat(base, key, model, messages, temperature, max_tokens, timeout):
    """Single OpenAI-format chat completion against one resolved endpoint."""
    # max_tokens=None → OMIT the cap entirely, so the provider serves up to the model's own
    # maximum output length ("no limit" mode, 2026-07-28). LLM_MAX_TOKENS env can force a cap.
    if max_tokens is None:
        env_cap = os.environ.get("LLM_MAX_TOKENS", "").strip()
        max_tokens = int(env_cap) if env_cap.isdigit() else None
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    body = json.dumps(payload).encode()
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        # browser User-Agent: some providers sit behind Cloudflare, which 403s (error 1010)
        # the default "Python-urllib" signature as a bot. A real UA passes.
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    }
    if key and key.lower() != "ollama":
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(base + "/chat/completions", data=body, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.load(r)
    msg = data["choices"][0]["message"]
    # NVIDIA NIM reasoning models (step-3.x-flash etc.) put the answer in
    # reasoning_content and leave content empty if max_tokens cut off mid-think;
    # with adequate max_tokens content is the clean answer. Fall back so a
    # reasoning model never returns blank to the judgment agents.
    return msg.get("content") or msg.get("reasoning_content") or ""


# Provenance of the most recent successful chat(): (label, model, was_fallback).
# Agents read this via last_served() to record WHICH provider judged each call — so a
# weaker local-ollama fallback judgment made during a cloud outage is visible and re-checkable.
_LAST_SERVED = None


def last_served():
    """(label, model, was_fallback) of the most recent successful chat(), or None.
    was_fallback=True means the primary failed and a backup answered — a flag to re-check
    that judgment once the primary is healthy (the local net is a weaker model)."""
    return _LAST_SERVED


def provenance():
    """{'served_by','served_model','served_fallback'} for the last judge()/chat() — spread
    into an agent's log record (`{**rec, **llm_client.provenance()}`) so every judgment
    carries which provider produced it. served_fallback=True ⇒ weaker local net, re-checkable."""
    s = _LAST_SERVED
    return {"served_by": s[0] if s else None,
            "served_model": s[1] if s else None,
            "served_fallback": s[2] if s else None}


def chat(messages, temperature=0.2, max_tokens=None, timeout=300, model=None, system=None):
    """One OpenAI-format chat completion, with automatic failover down the provider chain
    (primary → fallbacks → local ollama). Returns the assistant text. Raises only if EVERY
    provider in the chain fails — and then names each failure so a dead key is visible in logs.
    system: optional system-prompt string, prepended as a {"role":"system",...} message."""
    global _LAST_SERVED
    if system:
        messages = [{"role": "system", "content": system}] + list(messages)
    chain = _provider_chain()
    if not chain:
        raise RuntimeError("LLM not configured — see llm_client.py header for free presets")
    errors = []
    for i, (label, base, key, default_model) in enumerate(chain):
        try:
            used_model = model or default_model
            out = _one_chat(base, key, used_model, messages, temperature, max_tokens, timeout)
            if not (out and out.strip()):
                raise ValueError("empty/blank response")  # 200-but-empty (e.g. cerebras gpt-oss) = failure → fall through, don't serve a blank judgment
            _LAST_SERVED = (label, used_model, i > 0)
            if i > 0:  # we fell over to a backup — make it visible in the agent's log
                print(f"[llm_client] primary failed; served by fallback '{label}' "
                      f"({' | '.join(errors)})", file=sys.stderr)
            return out
        except Exception as e:
            errors.append(f"{label}: {type(e).__name__} {str(e)[:80]}")
    raise RuntimeError("all LLM providers failed → " + " | ".join(errors))


def _extract_json(text):
    """Pull the first JSON object out of an LLM reply (handles ```json fences / prose)."""
    s = text.find("{")
    e = text.rfind("}")
    if s == -1 or e == -1 or e < s:
        raise ValueError(f"no JSON object in reply: {text[:120]}")
    return json.loads(text[s:e + 1])


def judge(system, user, schema_hint=None, temperature=0.1, retries=2):
    """For verdict-style agents: returns a parsed dict. schema_hint = a JSON shape string
    appended to the prompt so the model returns parseable JSON (no tool-calling needed)."""
    sys_full = system
    if schema_hint:
        sys_full += (f"\n\nReturn ONLY a JSON object of this shape, nothing else:\n{schema_hint}")
    last = None
    for _ in range(retries + 1):
        try:
            return _extract_json(chat([{"role": "system", "content": sys_full},
                                       {"role": "user", "content": user}], temperature=temperature))
        except (ValueError, json.JSONDecodeError, KeyError) as e:
            last = e
            temperature = 0.0  # tighten on retry
    raise RuntimeError(f"judge() could not get parseable JSON after retries: {last}")


def _selftest():
    base, key, model = _cfg()
    prov = os.environ.get("LLM_PROVIDER", "").strip().lower()
    if not prov and not os.environ.get("LLM_BASE_URL") and os.environ.get("NVIDIA_API_KEY"):
        prov = "nvidia (auto: NVIDIA_API_KEY present)"
    print("llm_client config:")
    print(f"  provider     = {prov or '(explicit LLM_BASE_URL)'}")
    print(f"  base_url     = {base or '(unset)'}")
    print(f"  api_key      = {'<set>' if key else '(unset)'}")
    print(f"  model        = {model or '(unset)'}")
    # structural check: request builds correctly even without a live endpoint
    try:
        body = json.dumps({"model": model or "x", "messages": [{"role": "user", "content": "ping"}],
                           "temperature": 0.2, "max_tokens": 5})
        json.loads(body)
        print("  request construction: ✅ valid OpenAI chat-completions body")
    except Exception as e:
        print(f"  request construction: ❌ {e}")
    print("  JSON extractor: ", end="")
    try:
        assert _extract_json('prefix {"refuted": true, "confidence": 80} suffix')["confidence"] == 80
        print("✅ parses fenced/prose JSON")
    except Exception as e:
        print(f"❌ {e}")
    if not configured():
        print("\n⚠️ No endpoint configured — set LLM_BASE_URL + LLM_MODEL (+ LLM_API_KEY) to activate.")
        print("   Presets are in this file's header (Groq / OpenRouter / Together / Ollama).")
        return
    chain = _provider_chain()
    print(f"\n  failover chain ({len(chain)}): " + " → ".join(f"{c[0]}[{c[3]}]" for c in chain))
    print("  per-provider live ping:")
    for label, p_base, p_key, p_model in chain:
        try:
            out = _one_chat(p_base, p_key, p_model,
                            [{"role": "user", "content": "Reply with exactly: OK"}], 0.0, 5, 30)
            print(f"    ✅ {label} ({p_model}) — reply: {out.strip()[:30]}")
        except Exception as e:
            print(f"    ❌ {label} ({p_model}) — {type(e).__name__}: {str(e)[:60]}")
    print("\n  end-to-end chat() (uses the chain w/ failover):")
    try:
        out = chat([{"role": "user", "content": "Reply with exactly: OK"}], max_tokens=5)
        print(f"    ✅ served — reply: {out.strip()[:40]}")
    except Exception as e:
        print(f"    ❌ all providers failed: {e}")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        # CLI: `python3 llm_client.py "your prompt"` → prints the free-chain answer to
        # stdout, provenance to stderr. Lets agents shell out (not just import). No prompt
        # → print the module docstring (help).
        _args = [a for a in sys.argv[1:] if not a.startswith("-")]
        if _args:
            try:
                _out = chat([{"role": "user", "content": " ".join(_args)}], max_tokens=512)
                print(_out or "")
                _p = provenance() or {}
                sys.stderr.write(f"[llm_client] served_by={_p.get('served_by')} "
                                 f"model={_p.get('served_model')} fallback={_p.get('served_fallback')}\n")
            except Exception as _e:
                sys.stderr.write(f"[llm_client] FAILED: {_e}\n")
                sys.exit(1)
        else:
            print(__doc__)
