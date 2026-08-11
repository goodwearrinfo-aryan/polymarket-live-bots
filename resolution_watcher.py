#!/usr/bin/env python3
"""
resolution_watcher.py — Resolution-source latency edge (SCAFFOLD).

NOTE: Polymarket Gamma API does NOT expose resolution_source metadata.
The actual resolution is handled by UMA oracles off-chain.

This watcher uses a HEURISTIC APPROACH:
  1. Maintain a curated MAP of market keywords -> known official data sources
  2. Poll those sources at high frequency
  3. When a source updates (new CPI print, Fed decision, BTC price feed, etc.),
     find open Polymarket markets that would be affected
  4. If Polymarket price hasn't moved to reflect the new fact, enter

This is a structural edge: act on public facts BEFORE the order book reprices.
Paper-only. No keys. Runs as detached daemon via watchdog.
"""

import os, sys, json, time, urllib.parse, urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, List, Any

HERE = Path(__file__).parent
STATE_FILE = HERE / "resolution_watcher_state.json"
LOG_FILE = HERE / "resolution_watcher.log"
KILL_FILE = Path("~/.resolution_watcher_KILL").expanduser()

GAMMA = "https://gamma-api.polymarket.com/markets"
UA = "resolution-watcher-paper/1.0"

CONFIG = {
    "poll_sec":           60,      # source check interval
    "max_resolve_days":   30,      # watch markets resolving within a month
    "min_volume":         10_000,  # liquidity floor
    "max_open":           3,       # tiny cap — surgical, not volume
    "bet_usdc":           2.0,     # conviction-sized when signal hits
    "edge_min":           0.10,    # price must be off by >= this after source updates
    "max_hold_hours":     48,      # safety cap
    "spread":             0.02,    # taker cost on entry
    "source_timeout":     10,      # http timeout for source fetch
}

# ============================================================================
# SOURCE REGISTRY — Curated mapping of data feeds to market keywords
# Each source has: url, parser function, keywords it affects
# ============================================================================

def log(msg: str):
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def http_get(url: str, params: Optional[Dict] = None, timeout: int = 10) -> Optional[Any]:
    try:
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception as e:
        log(f"[http] {url} -> {e}")
        return None


def load_state() -> Dict:
    if STATE_FILE.exists():
        try:
            st = json.loads(STATE_FILE.read_text())
        except Exception:
            st = {}
    else:
        st = {}
    # Ensure all expected keys
    st.setdefault("open", [])
    st.setdefault("closed", [])
    st.setdefault("last_check", {})
    st.setdefault("hits", 0)
    return st


def save_state(st: Dict):
    STATE_FILE.write_text(json.dumps(st, indent=2))


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def hours_since(iso: str) -> float:
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(iso.replace("Z", "+00:00"))).total_seconds() / 3600
    except Exception:
        return 0.0


def days_to(end: str) -> Optional[float]:
    if not end:
        return None
    try:
        t = datetime.fromisoformat(str(end).replace("Z", "+00:00"))
        return (t - datetime.now(timezone.utc)).total_seconds() / 86400
    except Exception:
        return None


# -----------------------------------------------------------------------------
# SOURCE CHECKERS — Each returns (triggered: bool, value: any, metadata: dict)
# -----------------------------------------------------------------------------

def check_fred_latest(series_id: str, last_known: Optional[str] = None) -> tuple:
    """Check FRED (Federal Reserve Economic Data) for latest observation."""
    url = f"https://api.stlouisfed.org/fred/series/observations"
    params = {
        "series_id": series_id,
        "api_key": os.environ.get("FRED_API_KEY", ""),
        "file_type": "json",
        "sort_order": "desc",
        "limit": 1
    }
    data = http_get(url, params, CONFIG["source_timeout"])
    if not data or "observations" not in data or not data["observations"]:
        return False, None, {}
    obs = data["observations"][0]
    date = obs.get("date")
    value = obs.get("value")
    if date != last_known:
        return True, {"date": date, "value": value}, {"series_id": series_id, "date": date}
    return False, None, {}


def check_coingecko_price(coin_id: str, last_known: Optional[float] = None) -> tuple:
    """Check crypto spot price via feeds.coingecko — keyless chain
    (CoinGecko -> coinpaprika -> coinlore) so the signal never goes blind
    when CoinGecko's free tier 429s (it 429s often)."""
    try:
        import feeds
        q = feeds.coingecko(coin_id)
    except Exception:
        return False, None, {}
    if not q or q.get("price") in (None, 0):
        return False, None, {}
    price = float(q["price"])
    if last_known is None or abs(price - last_known) / max(last_known, 1) > 0.02:  # 2% move
        return True, price, {"coin_id": coin_id, "src": q.get("src", "coingecko")}
    return False, None, {}


def check_binance_price(symbol: str, last_known: Optional[float] = None) -> tuple:
    """Check Binance for perp price (more real-time than CoinGecko)."""
    url = f"https://fapi.binance.com/fapi/v1/ticker/24hr"
    params = {"symbol": symbol}
    data = http_get(url, params, CONFIG["source_timeout"])
    if not data or "lastPrice" not in data:
        return False, None, {}
    price = float(data["lastPrice"])
    if last_known is None or abs(price - last_known) / max(last_known, 1) > 0.01:  # 1% move
        return True, price, {"symbol": symbol}
    return False, None, {}


# Source registry: each entry defines a source to poll
SOURCES = [
    # Crypto price feeds (for "up or down" markets) — keyless
    {"id": "cg_bitcoin", "name": "CoinGecko BTC", "check": check_coingecko_price,
     "args": {"coin_id": "bitcoin"}, "keywords": ["bitcoin", "btc", "crypto"]},
    {"id": "cg_ethereum", "name": "CoinGecko ETH", "check": check_coingecko_price,
     "args": {"coin_id": "ethereum"}, "keywords": ["ethereum", "eth"]},
    {"id": "bn_BTCUSDT", "name": "Binance BTCUSDT", "check": check_binance_price,
     "args": {"symbol": "BTCUSDT"}, "keywords": ["bitcoin", "btc", "crypto"]},
    {"id": "bn_ETHUSDT", "name": "Binance ETHUSDT", "check": check_binance_price,
     "args": {"symbol": "ETHUSDT"}, "keywords": ["ethereum", "eth"]},
]

# In-memory cache of last known values per source
SOURCE_CACHE = {}


def fetch_markets(cfg: Dict) -> List[Dict]:
    """Fetch liquid markets resolving soon."""
    out, seen = [], set()
    for page in range(4):
        raw = http_get(GAMMA, {"closed": "false", "active": "true",
                                "order": "volumeNum", "ascending": "false",
                                "limit": 100, "offset": page * 100}) or []
        if not raw: break
        for m in raw:
            try:
                cid = str(m.get("conditionId") or m.get("id"))
                if cid in seen: continue
                prices = m.get("outcomePrices")
                if isinstance(prices, str): prices = json.loads(prices)
                if not prices: continue
                yes = float(prices[0])
                vol = float(m.get("volumeNum") or m.get("volume") or 0)
                d = days_to(m.get("endDate"))
                if vol < cfg["min_volume"]: continue
                if d is None or d > cfg["max_resolve_days"] or d < 0.1: continue
                seen.add(cid)
                out.append({"id": cid, "q": (m.get("question") or "").lower(), "yes": yes, "vol": vol, "days_left": d})
            except Exception:
                continue
            if len(out) >= 50: break
        if len(out) >= 50: break
    return out


def market_matches_keywords(market_q: str, keywords: List[str]) -> bool:
    """Simple keyword matching between market question and source keywords."""
    return any(kw in market_q for kw in keywords)


def one_scan(cfg: Dict, verbose=True) -> Dict:
    if KILL_FILE.exists():
        if verbose: print("[resolution_watcher] KILL switch active")
        sys.exit(0)

    st = load_state()

    # Reap stale/expired positions
    keep_open = []
    for p in st["open"]:
        if hours_since(p["opened_at"]) > cfg["max_hold_hours"]:
            _close(st, p, p.get("entry_fill", 0), "time", cfg)
        else:
            keep_open.append(p)
    st["open"] = keep_open

    # Poll all registered sources
    for src in SOURCES:
        src_id = src["id"]
        last = SOURCE_CACHE.get(src_id)
        try:
            triggered, value, meta = src["check"](**src["args"], last_known=last)
        except Exception as e:
            log(f"[source:{src_id}] error: {e}")
            continue

        if not triggered:
            continue

        # Source updated! Update cache.
        SOURCE_CACHE[src_id] = value
        log(f"SOURCE HIT: {src['name']} = {value} (meta: {meta})")
        st["last_check"][src_id] = now_iso()
        st["hits"] = st.get("hits", 0) + 1

        # Find markets matching this source's keywords
        mkts = fetch_markets(cfg)
        for m in mkts:
            if len(st["open"]) >= cfg["max_open"]:
                break
            if any(p["id"] == m["id"] for p in st["open"]):
                continue
            if not market_matches_keywords(m["q"], src["keywords"]):
                continue

            # Source updated and market is relevant — check divergence.
            # Use the REAL resolution-source signal (ml/resfeed.py): parse the
            # market's threshold ("Will BTC be above $X?") and compute P(YES)
            # from the live spot margin — NOT the old placeholder 0.7/0.3 on a
            # tick direction, which was pure noise.
            yes = m["yes"]
            try:
                import sys as _sys
                _sys.path.insert(0, str(Path(__file__).parent / "ml"))
                from resfeed import res_signal
                sig = res_signal(m["q"])
            except Exception:
                sig = None
            if sig and sig.get("p_yes") is not None:
                model_p = sig["p_yes"]
                src_value = (f"{sig['coin']}@{sig['spot']} thr={sig['threshold']} "
                             f"dir={sig['direction']}")
            else:
                # Not a threshold market — no real signal, skip. (Trading on a
                # bare tick move was the old bug: model_p was 0.7/0.3 noise.)
                continue

            div = model_p - yes
            if abs(div) < cfg["edge_min"]:
                continue

            side = "YES" if div > 0 else "NO"
            outcome_price = yes if side == "YES" else 1 - yes
            entry = min(0.999, outcome_price + cfg["spread"] / 2)
            size = round(cfg["bet_usdc"] / entry, 4) if entry > 0 else 0
            if size <= 0:
                continue

            st["open"].append({
                "id": m["id"], "q": m["q"][:80], "side": side,
                "src_id": src_id, "src_value": str(src_value)[:50],
                "entry_fill": round(entry, 4), "size": size,
                "opened_at": now_iso(),
            })
            log(f"OPEN {side} {m['q'][:50]} @ {entry:.3f} (signal: {src_value})")

    save_state(st)
    if verbose:
        log(f"open={len(st['open'])} closed={len(st['closed'])} hits={st.get('hits',0)}")
    return st


def _close(st: Dict, p: Dict, exit_fill: float, reason: str, cfg: Dict):
    pnl = round((exit_fill - p["entry_fill"]) * p["size"], 4)
    rec = dict(p)
    rec.update({"exit_fill": round(exit_fill, 4), "reason": reason,
                "pnl_usdc": pnl, "closed_at": now_iso()})
    st["closed"].append(rec)
    log(f"CLOSE {p['side']} {p['q'][:40]} @ {exit_fill:.3f} pnl={pnl:+.4f} ({reason})")


def board(st: Dict):
    c = st["closed"]; n = len(c); w = sum(1 for r in c if r["pnl_usdc"] > 0)
    pnl = sum(r["pnl_usdc"] for r in c)
    print("=" * 54)
    print("  RESOLUTION WATCHER — source latency edge (paper)")
    print("=" * 54)
    print(f"  open={len(st['open'])}  closed={n}  win={ (w/n*100 if n else 0):.0f}%  "
          f"realized=${pnl:+.2f}  avg/trade=${pnl/n if n else 0:+.4f}")
    if n and n < 20:
        print(f"  ({n} closed — need ~20-30 before trusting)")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "once"
    if cmd == "board": board(load_state()); return
    if cmd == "once": one_scan(CONFIG); board(load_state()); return
    if cmd == "run":
        log("resolution_watcher running (paper). Ctrl-C to stop.")
        while True:
            try: one_scan(CONFIG)
            except Exception as e: log(f"[loop] {e}")
            time.sleep(CONFIG["poll_sec"])
    print(__doc__)


if __name__ == "__main__":
    main()