#!/usr/bin/env python3
"""
tradingagents_feed.py — runs the TradingAgents LLM agent graph on a few
forward-looking Polymarket markets and caches a directional verdict per market for
the scalp_lab `tasignal` leg. Same feed-service pattern as openalice/hermes.

Market SELECTION is keyless (Polymarket Gamma). The agent graph needs an LLM key.
Without ANTHROPIC_API_KEY it writes an idle cache and exits 0 ($0, graceful) — so
the launchd job is harmless until you drop a key in .env.

Bridge (TA is asset-centric; we map its decision to a market view):
  - crypto markets (BTC/ETH/SOL detected) -> asset_type="crypto", real ticker;
    BUY => coin up => lean YES on "above/up" markets.
  - everything else -> asset_type="stock" + news/social analysts only (topic-
    agnostic; the polymarket + news vendors carry the signal). subject = question.
  BUY  => event MORE likely than the crowd prices => lean YES
  SELL => event LESS likely                        => lean NO
  HOLD => no view                                  => no signal
The leg trades the DIVERGENCE between TA's lean and the market's implied YES.

Output: ~/Documents/polymarket/tradingagents_signals.json
Run:    python tradingagents_feed.py    (cadence via com.aryan.polymarket-tradingagents)
Env (.env in this dir): ANTHROPIC_API_KEY, TA_MODEL_DEEP, TA_MODEL_QUICK, TA_N_MARKETS.
"""
import os, sys, json, datetime, urllib.request

DIR    = os.path.dirname(os.path.abspath(__file__))
TA_DIR = os.path.expanduser("~/Documents/TradingAgents")
OUT    = os.path.join(DIR, "tradingagents_signals.json")
GAMMA  = "https://gamma-api.polymarket.com"
UA     = {"User-Agent": "Mozilla/5.0"}

COINS = {"bitcoin": "BTC", "btc": "BTC", "ethereum": "ETH", "eth": "ETH",
         "solana": "SOL", "sol": "SOL"}

# provider -> (env key, deep model, quick model). Order = preference: Groq first
# (free tier, so the leg can paper-trade at $0), then paid providers if set.
PROVIDER_DEFAULTS = {
    "groq":      ("GROQ_API_KEY",      "llama-3.3-70b-versatile", "llama-3.1-8b-instant"),
    "anthropic": ("ANTHROPIC_API_KEY", "claude-sonnet-4-6",       "claude-haiku-4-5-20251001"),
    "openai":    ("OPENAI_API_KEY",    "gpt-5.4-mini",            "gpt-5.4-mini"),
}
PROVIDER_ORDER = ["groq", "anthropic", "openai"]


def pick_provider():
    """First provider whose key is in env -> (provider, deep_model, quick_model).
    TA_PROVIDER forces one; TA_MODEL_DEEP/QUICK override the model names."""
    forced = os.environ.get("TA_PROVIDER", "").strip().lower()
    order = [forced] if forced in PROVIDER_DEFAULTS else PROVIDER_ORDER
    for prov in order:
        env_key, deep, quick = PROVIDER_DEFAULTS[prov]
        if os.environ.get(env_key):
            return prov, os.environ.get("TA_MODEL_DEEP", deep), os.environ.get("TA_MODEL_QUICK", quick)
    return None, None, None


def load_env():
    """Load the LLM key. Primary: .ta_env (gitignored, KEY=VALUE). Easy fallback:
    ANY file on the Desktop with 'groq' in its name — we pull a gsk_... token out
    of it regardless of format (so a plain OR TextEdit/RTF save both work). This
    spares the user from editing the hidden .ta_env dotfile."""
    import re
    p = os.path.join(DIR, ".ta_env")
    if os.path.exists(p):
        for line in open(p):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                v = v.strip().strip('"').strip("'")
                if v:
                    os.environ.setdefault(k.strip(), v)
    # Visible-file fallback: scan the Desktop for a 'groq' file, extract the token.
    if not os.environ.get("GROQ_API_KEY"):
        dsk = os.path.expanduser("~/Desktop")
        try:
            for fn in sorted(os.listdir(dsk)):
                if "groq" in fn.lower():
                    txt = open(os.path.join(dsk, fn), errors="ignore").read()
                    m = re.search(r'gsk_[A-Za-z0-9]{20,}', txt)
                    if m:
                        os.environ["GROQ_API_KEY"] = m.group(0)
                        break
        except Exception:
            pass


def get(url):
    try:
        req = urllib.request.Request(url, headers=UA)
        return json.loads(urllib.request.urlopen(req, timeout=20).read())
    except Exception:
        return None


def detect_coin(q):
    ql = q.lower()
    for kw, tic in COINS.items():
        if kw in ql:
            return tic
    return None


def pick_markets(n):
    """Top forward-looking binary markets by 24h volume, keyless Gamma."""
    rows = get(f"{GAMMA}/markets?closed=false&order=volume24hr&ascending=false&limit=150") or []
    out, now = [], datetime.datetime.now(datetime.timezone.utc)
    for m in rows:
        q = (m.get("question") or "").strip()
        if not q:
            continue
        try:
            prices = json.loads(m.get("outcomePrices") or "[]")
            outs = json.loads(m.get("outcomes") or "[]")
        except Exception:
            continue
        if len(prices) != 2 or len(outs) != 2:
            continue
        ed = m.get("endDate")
        if ed:
            try:
                if datetime.datetime.fromisoformat(ed.replace("Z", "+00:00")) < now:
                    continue
            except ValueError:
                pass
        yes = float(prices[0])
        if not (0.05 < yes < 0.95):      # skip near-certain — no divergence room
            continue
        out.append({"id": m.get("id"), "question": q, "implied_yes": round(yes, 3),
                    "end": ed, "vol24": float(m.get("volume24hr") or 0),
                    "coin": detect_coin(q)})
        if len(out) >= n:
            break
    return out


def write_cache(status, signals, **extra):
    payload = {"ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
               "status": status, "signals": signals, **extra}
    tmp = OUT + ".tmp"
    json.dump(payload, open(tmp, "w"), indent=1)
    os.replace(tmp, OUT)                  # atomic — leg never reads a half-written cache


def main():
    load_env()
    prov, deep, quick = pick_provider()
    if not prov:
        write_cache("no-key-idle", [])
        print("[ta-feed] no LLM key in .ta_env — set GROQ_API_KEY (free) or "
              "ANTHROPIC_API_KEY — wrote idle cache, $0"); return

    markets = pick_markets(int(os.environ.get("TA_N_MARKETS", "4")))
    if not markets:
        write_cache("no-markets", [])
        print("[ta-feed] no eligible markets"); return

    # Heavy import only once we know we'll actually run the graph.
    sys.path.insert(0, TA_DIR)
    os.environ.setdefault("TRADINGAGENTS_LLM_PROVIDER", prov)
    os.environ.setdefault("TRADINGAGENTS_DEEP_THINK_LLM", deep)
    os.environ.setdefault("TRADINGAGENTS_QUICK_THINK_LLM", quick)
    os.environ.setdefault("TRADINGAGENTS_MAX_DEBATE_ROUNDS", "1")   # cost control
    os.environ.setdefault("TRADINGAGENTS_MAX_RISK_ROUNDS", "1")
    print(f"[ta-feed] provider={prov}  deep={deep}  quick={quick}")
    from tradingagents.graph.trading_graph import TradingAgentsGraph
    from tradingagents.default_config import DEFAULT_CONFIG

    cfg = DEFAULT_CONFIG.copy()
    # news+social are topic-agnostic; market/fundamentals need a real ticker.
    ta = TradingAgentsGraph(selected_analysts=["social", "news"], debug=False, config=cfg)
    today = datetime.date.today().isoformat()

    signals = []
    for mk in markets:
        coin = mk["coin"]
        company = coin if coin else mk["question"]
        asset = "crypto" if coin else "stock"
        try:
            _, decision = ta.propagate(company, today, asset_type=asset)
        except Exception as e:
            print(f"[ta-feed] propagate FAILED [{company[:30]}]: {e}")
            continue
        dec = str(decision).upper()
        lean = "YES" if "BUY" in dec else "NO" if "SELL" in dec else None
        if not lean:
            print(f"[ta-feed] {mk['question'][:45]} -> HOLD (no signal)")
            continue
        ta_implied = 0.75 if lean == "YES" else 0.25     # coarse directional prior
        div = round(ta_implied - mk["implied_yes"], 3)
        signals.append({"id": mk["id"], "question": mk["question"],
                        "implied_yes": mk["implied_yes"], "asset": asset,
                        "ta_decision": dec.split()[0] if dec.split() else dec,
                        "lean": lean, "ta_implied": ta_implied, "divergence": div,
                        "end": mk["end"]})
        print(f"[ta-feed] {mk['question'][:45]} | mkt YES {mk['implied_yes']:.2f} "
              f"| TA {lean} | div {div:+.2f}")

    write_cache("ok", signals, provider=prov, model_deep=deep, n_scanned=len(markets))
    print(f"[ta-feed] wrote {len(signals)} signal(s) from {len(markets)} markets")


if __name__ == "__main__":
    main()
