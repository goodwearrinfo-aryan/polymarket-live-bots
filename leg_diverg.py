"""
leg_diverg.py — standalone spot-price vs Polymarket divergence leg.

Paper-only crypto trading. Spot price vs market divergence = entry signal.
If BTC/ETH/etc is up >N% in 24h but the Up/Down market is still priced
near 50/50, the market hasn't repriced → buy YES.

Runs independently or via leg_runner.py. Records forecasts/settlements to
shared belief_ledger.jsonl. No state mutation outside local scope.

Usage:
  python3 leg_diverg.py scan      # entry scan (run every 60s)
  python3 leg_diverg.py exit      # exit scan (run every 60s)
  python3 leg_diverg.py board     # print leg stats
"""
import json, urllib.request, time
from datetime import datetime, timezone
from collections import defaultdict

# ── Config (leg-local, passed in or hardcoded) ────────────────────────────
CONFIG = {
    "enabled": True,
    "chg_threshold": 0.03,          # 3% 24h move to qualify
    "yes_max_bull": 0.58,           # buy YES only when market hasn't caught up
    "yes_min_bear": 0.42,           # buy NO only when market hasn't caught up
    "min_hours_left": 1.0,
    "max_hours_left": 8.0,
    "min_volume": 50_000,
    "max_open": 8,
    "bet_usdc": 1.0,
    "gain": 0.10,
    "stop": 0.08,
    "max_hold_hours": 8,
}

STATE_FILE = "leg_diverg_state.json"


def _get(url, tries=3):
    """Fetch URL with retry."""
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as r:
                return json.loads(r.read())
        except Exception:
            if i == tries - 1:
                return None
            time.sleep(1)


def _get_coingecko_24h_change():
    """Fetch 24h price change for BTC, ETH, SOL from CoinGecko (keyless)."""
    url = "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum,solana&vs_currencies=usd&include_24hr_change=true"
    data = _get(url)
    if not data:
        return {}
    return {
        "BTC": data.get("bitcoin", {}).get("usd_24h_change", 0) / 100,
        "ETH": data.get("ethereum", {}).get("usd_24h_change", 0) / 100,
        "SOL": data.get("solana", {}).get("usd_24h_change", 0) / 100,
    }


def scan_entries(markets, state):
    """Entry scan for diverg leg. Yields entry positions."""
    if not CONFIG["enabled"]:
        return

    chg = _get_coingecko_24h_change()
    if not chg:
        return

    book = state.setdefault("diverg", {"open": [], "closed": []})
    own = {p["id"] for p in book["open"]}

    for m in markets:
        if m["id"] in own or len(book["open"]) >= CONFIG["max_open"]:
            continue

        # Match coin to Up/Down market (e.g., "Bitcoin - Up or Down?" ~ BTC)
        q_lower = m.get("q", "").lower()
        coin_abbr = None
        for abbr, name in [("BTC", "bitcoin"), ("ETH", "ethereum"), ("SOL", "solana")]:
            if name in q_lower:
                coin_abbr = abbr
                break

        if not coin_abbr or coin_abbr not in chg:
            continue

        # Entry logic: divergence
        change_pct = chg[coin_abbr]
        entry_side, entry_px = None, None

        if change_pct > CONFIG["chg_threshold"] and m.get("yes") < CONFIG["yes_max_bull"]:
            # Coin up, market not caught up → buy YES
            entry_side, entry_px = "YES", m.get("yes")
        elif change_pct < -CONFIG["chg_threshold"] and m.get("yes") > CONFIG["yes_min_bear"]:
            # Coin down, market not caught up → buy NO
            entry_side, entry_px = "NO", m.get("no")

        if entry_side:
            pos = {
                "id": m["id"],
                "q": m["q"],
                "strategy": "diverg",
                "side": entry_side,
                "entry_fill": round(entry_px, 4),
                "entry_mid": round((m.get("yes") + m.get("no")) / 2, 4),
                "size": CONFIG["bet_usdc"],
                "opened_at": datetime.now(timezone.utc).isoformat(),
                "cat": "crypto",
            }
            book["open"].append(pos)
            yield pos


def scan_exits(book, prices):
    """Exit scan for diverg leg. Yields (position, exit_price, reason)."""
    for pos in book.get("open", []):
        exit_px = prices.get(pos["id"])
        if not exit_px:
            continue

        # Target or stop
        entry = pos["entry_fill"]
        if pos["side"] == "YES":
            if exit_px >= entry * (1 + CONFIG["gain"]):
                yield (pos, exit_px, "target")
            elif exit_px <= entry * (1 - CONFIG["stop"]):
                yield (pos, exit_px, "stop")
        else:  # NO
            if exit_px <= entry * (1 - CONFIG["gain"]):
                yield (pos, exit_px, "target")
            elif exit_px >= entry * (1 + CONFIG["stop"]):
                yield (pos, exit_px, "stop")


def load_state():
    """Load leg state from disk."""
    try:
        return json.load(open(STATE_FILE))
    except FileNotFoundError:
        return {"diverg": {"open": [], "closed": []}}


def save_state(state):
    """Save leg state to disk."""
    json.dump(state, open(STATE_FILE, "w"), indent=2)


def board(state):
    """Print leg stats."""
    book = state.get("diverg", {})
    open_c = len(book.get("open", []))
    closed = book.get("closed", [])
    priced = [r for r in closed if r.get("pnl_usdc") is not None]
    n = len(priced)
    w = sum(1 for r in priced if r["pnl_usdc"] > 0) if n > 0 else 0
    pnl = sum(r["pnl_usdc"] for r in priced) if n > 0 else 0.0
    wr = (w / n * 100) if n > 0 else 0

    print(f"\n  [DIVERG]  open={open_c}  closed={n}  win={wr:5.1f}%  paper P&L=${pnl:+.2f}")
    if n:
        print(f"     exits: {dict((r['reason'], sum(1 for x in priced if x['reason']==r['reason'])) for r in priced)}")


if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "board"

    if cmd == "board":
        board(load_state())
    elif cmd == "scan":
        print("diverg: entry/exit scan (stub — requires markets + prices)")
    else:
        print(f"unknown command: {cmd}")
