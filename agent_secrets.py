#!/usr/bin/env python3
"""agent_secrets.py — single canonical local secrets loader for ALL bot agents.

launchd agents run with cwd=/ and DO NOT inherit the interactive shell env, so a
plain os.getenv("GROQ_API_KEY") returns nothing under launchd. This module seeds
os.environ from ~/Documents/polymarket/.env using an ABSOLUTE path (cwd-independent),
mirroring the convention already in llm_client.py / keyed_sources.py — so any agent
that calls agent_secrets.load() at startup gets every local key, even under launchd.

.env stays the local secrets store (chmod 600). Values are NEVER printed; never
hardcoded; never committed. `--check` reports only which key NAMES are present.

Usage:
    import agent_secrets; agent_secrets.load()      # seed env at agent startup
    python3 agent_secrets.py --check                # audit presence (names only)
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(HERE, ".env")

# Keys agents may rely on (audit list only — presence reported, values never shown).
KNOWN = [
    "GROQ_API_KEY", "CEREBRAS_API_KEY", "SAMBANOVA_API_KEY", "NVIDIA_API_KEY",
    "OPENROUTER_API_KEY", "LLM_API_KEY",
    "TAVILY_API_KEY", "FINNHUB_API_KEY", "MARKETAUX_API_KEY", "COINGECKO_API_KEY",
    "BIRDEYE_API_KEY", "ALCHEMY_KEY", "ODDPOOL_KEY",
    "POLYMARKET_API_KEY", "POLYMARKET_API_SECRET", "POLYMARKET_API_PASSPHRASE",
]


def load(path=ENV_PATH):
    """Seed os.environ from .env WITHOUT overriding real env (setdefault). Returns count."""
    if not os.path.exists(path):
        return 0
    n = 0
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k and k not in os.environ:
                    os.environ[k] = v
                    n += 1
    except Exception:
        pass
    return n


def present(names=None):
    """Map of key-name -> bool(present & non-empty). Values never returned."""
    names = names or KNOWN
    return {k: bool(os.environ.get(k)) for k in names}


if __name__ == "__main__":
    loaded = load()
    p = present()
    have = sum(1 for v in p.values() if v)
    print(f"secrets store: {ENV_PATH} ({'exists' if os.path.exists(ENV_PATH) else 'MISSING'}); seeded {loaded} vars")
    print(f"keys present: {have}/{len(p)}")
    for k in KNOWN:
        print(f"  {'✅' if p[k] else '❌'} {k}")
