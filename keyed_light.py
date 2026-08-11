#!/usr/bin/env python3
"""keyed_light.py — live "keyed connectors" layer for the Obsidian vault.

The companion to connector_light.py (which covers the 166 KEYLESS feeds). This one
surfaces the bot's KEYED services — the LLM provider chain and the keyed data/trading
APIs — as graph nodes so the live-map shows what the agents can reach WITH their local
keys. Reads keys via agent_secrets.load() (abs-path .env seed), so it works under
launchd with no shell env.

Per node (Keyed/<slug>.md), tagged for the existing graph color groups:
  • #online (green)  = key is configured in .env  → the agent CAN use this service
  • #offline (red)   = key MISSING               → wire the key (signup URL shown)
Reachability of each host is probed (keylessly, any HTTP response = up) and shown as
a detail, but the colour is gated on KEY PRESENCE (the honest meaning of "has its key"),
so an imperfect probe URL never falsely reds-out a configured connector.

Writes Keyed/<slug>.md nodes + a "Keyed Services" hub + a "Keyed Services.md" board.
Separate folder from Connectors/ so wiring_keeper's orphan-pruner never removes these.
Self-throttled (~15 min), low-churn, atomic, fail-soft. Usage: keyed_light.py [force]
"""
import os, re, sys, json, time, tempfile, urllib.request, socket
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

sys.path.insert(0, os.path.expanduser("~/Documents/polymarket"))
import agent_secrets

HERE = os.path.expanduser("~/Documents/polymarket")
VAULT = os.path.expanduser("~/Documents/PolymarketVault")
KEYED_DIR = os.path.join(VAULT, "Keyed")
BOARD = os.path.join(VAULT, "Keyed Services.md")
STATE = os.path.join(HERE, ".keyed_state.json")
LAST = os.path.join(HERE, ".keyed_last")
THROTTLE = 900
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 keyed-light"
HUB = "Keyed Services"

# (display, env_var, category, probe_url, signup_url, provides)
KEYED = [
    ("Groq LLM",        "GROQ_API_KEY",     "llm",     "https://api.groq.com/openai/v1/models",        "https://console.groq.com",      "PRIMARY free strong-LLM (gpt-oss/llama) — judge panel + agents"),
    ("Cerebras LLM",    "CEREBRAS_API_KEY", "llm",     "https://api.cerebras.ai/v1/models",            "https://cloud.cerebras.ai",     "gpt-oss-120b, no-card free tier"),
    ("SambaNova LLM",   "SAMBANOVA_API_KEY","llm",     "https://api.sambanova.ai/v1/models",           "https://cloud.sambanova.ai",    "llama-3.3-70b forever-free"),
    ("NVIDIA LLM",      "NVIDIA_API_KEY",   "llm",     "https://integrate.api.nvidia.com/v1/models",   "https://build.nvidia.com",      "70B fallback tier"),
    ("OpenRouter LLM",  "OPENROUTER_API_KEY","llm",    "https://openrouter.ai/api/v1/models",          "https://openrouter.ai",         "multi-model router"),
    ("Tavily search",   "TAVILY_API_KEY",   "data",    "https://api.tavily.com",                       "https://app.tavily.com",        "keyed web research (LIVE)"),
    ("Finnhub",         "FINNHUB_API_KEY",  "data",    "https://finnhub.io/api/v1/",                   "https://finnhub.io/register",   "equities quotes/news"),
    ("Marketaux",       "MARKETAUX_API_KEY","data",    "https://api.marketaux.com/v1/",                "https://www.marketaux.com",     "market news"),
    ("Birdeye",         "BIRDEYE_API_KEY",  "data",    "https://public-api.birdeye.so",                "https://birdeye.so",            "Solana token data"),
    ("Alchemy RPC",     "ALCHEMY_KEY",      "onchain", "https://dashboard.alchemy.com",                "https://alchemy.com",           "Polygon RPC (on-chain reads)"),
    ("Oddpool",         "ODDPOOL_KEY",      "venue",   "https://api.oddpool.xyz",                      "https://oddpool.xyz",           "Kalshi<->Polymarket cross-venue (1k/mo)"),
    ("Polymarket CLOB", "POLYMARKET_API_KEY","trading","https://clob.polymarket.com/",                 "https://polymarket.com",        "order/book API (paper)"),
]


def slug(s):
    return "keyed-" + re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def reachable(url, timeout=7):
    req = urllib.request.Request(url, headers={"User-Agent": UA}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            r.read(1)
        return True
    except urllib.error.HTTPError:
        return True            # 401/403/404 still means the host answered
    except (urllib.error.URLError, socket.timeout, ConnectionError, Exception):
        return False


def _atomic(path, text):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def main(force=False):
    try:
        if not force and (time.time() - os.path.getmtime(LAST)) < THROTTLE:
            return
    except OSError:
        pass

    agent_secrets.load()                       # seed keys from .env (launchd-safe)
    os.makedirs(KEYED_DIR, exist_ok=True)

    have = {d[0]: bool(os.environ.get(d[1])) for d in KEYED}
    # probe reachability only for connectors that have a key (informational)
    reach = {}
    with ThreadPoolExecutor(max_workers=12) as ex:
        futs = {d[0]: ex.submit(reachable, d[3]) for d in KEYED}
        for name, fut in futs.items():
            try:
                reach[name] = fut.result()
            except Exception:
                reach[name] = False

    try:
        prev = json.load(open(STATE))
    except Exception:
        prev = {}

    cur = {}
    for display, env_var, cat, probe, signup, provides in KEYED:
        keyed = have[display]
        up = reach.get(display, False)
        state = "online" if keyed else "offline"      # colour gated on KEY PRESENCE
        cur[display] = {"keyed": keyed, "reach": up}
        path = os.path.join(KEYED_DIR, f"{slug(display)}.md")
        if prev.get(display) == cur[display] and os.path.exists(path):
            continue
        if keyed:
            badge = "\U0001f7e2 keyed & ready" + ("" if up else " · ⚠️ host probe failed")
            detail = f"Key **{env_var}** configured. Host reachable: {'✅' if up else '❌ (transient?)'}"
        else:
            badge = "\U0001f534 no key"
            detail = f"Missing **{env_var}** in `.env`. Add it to enable. Signup: {signup}"
        body = (f"---\ntags: [connector, keyed, {cat}, {state}]\n---\n\n"
                f"# \U0001f511 {display}\n\n**Category:** keyed/{cat}\n\n**Status:** {badge}\n\n"
                f"{provides}\n\n{detail}\n\n→ [[{HUB}]] · [[Connectors]] · [[Home]]\n")
        _atomic(path, body)

    # hub
    hub_body = (f"---\ntags: [connector-hub]\n---\n\n# \U0001f511 {HUB}\n\n"
                f"The bot's KEYED services (LLM chain + keyed data/venue/trading APIs), read with "
                f"local keys from `.env` via `agent_secrets`. Green = key configured.\n\n"
                f"These power the judgment agents, keyed data legs and cross-venue scans.\n\n"
                f"→ [[Connectors]] · [[Data Sources]] · [[Home]]\n")
    _atomic(os.path.join(KEYED_DIR, HUB + ".md"), hub_body)

    json.dump(cur, open(STATE, "w"))

    total = len(KEYED)
    keyed_n = sum(1 for v in cur.values() if v["keyed"])
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    cls = "live" if keyed_n == total else "idle"
    dot = '<span class="live-dot"></span>'
    L = ["---", "type: dashboard", f"cssclasses: [dashboard, activity, {cls}]", "---", "",
         f"> [!banner] {dot} Keyed Services — {keyed_n}/{total} keyed",
         f"> Keyed connectors readable with local keys · refreshed {now}", "",
         "## \U0001f511 By service", ""]
    for display, env_var, cat, *_ in KEYED:
        v = cur[display]
        mark = "\U0001f7e2" if v["keyed"] else "\U0001f534"
        rr = "" if v["keyed"] and v["reach"] else (" · host ✗" if v["keyed"] else " · add key")
        L.append(f"- {mark} [[{slug(display)}\\|{display}]] — {cat}{rr}")
    miss = [d[0] for d in KEYED if not cur[d[0]]["keyed"]]
    L += ["", "## \U0001f534 Missing keys", ""]
    L += [f"- {m}" for m in miss] if miss else ["_All keyed services have keys ✅_"]
    L += ["", "> [!note]- How this works",
          "> `keyed_light.py` seeds keys from `.env` via `agent_secrets` (launchd-safe) and renders "
          "each keyed service as a graph node — green = key configured, red = key missing. Host "
          "reachability is probed keylessly and shown as a detail.",
          "", "→ [[Connectors]] · [[Activity]] · [[Home]]", ""]
    _atomic(BOARD, "\n".join(L))
    with open(LAST, "w") as f:
        f.write(str(int(time.time())))


if __name__ == "__main__":
    try:
        main(force=("force" in sys.argv))
    except Exception as e:
        try:
            with open(BOARD, "a", encoding="utf-8") as f:
                f.write(f"\n<!-- keyed_light error: {e} -->\n")
        except Exception:
            pass
