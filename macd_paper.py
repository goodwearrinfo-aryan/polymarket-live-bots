#!/usr/bin/env python3
"""macd_paper.py — Standalone MACD (12,26,9) paper-trading bot for BTC/ETH/SOL.

Extracted from algo_lab.py. Runs every hour via launchd com.aryan.macd-paper.
State written to macd_paper_state.json. Trades logged to macd_paper_trades.log.
PAPER ONLY — no real orders. Keyless Binance OHLCV feed.
"""
import json, os, time, urllib.request, logging

PM     = os.path.expanduser("~/Documents/polymarket")
STATE  = os.path.join(PM, "macd_paper_state.json")
LOG    = os.path.join(PM, "macd_paper_trades.log")
ASSETS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
NOTIONAL = 100.0          # USD per trade
INTERVAL  = "1h"
LIMIT     = 200           # candles; need >=26+9=35 for warmup

logging.basicConfig(
    filename=LOG,
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)

# ---------------------------------------------------------------------------
# indicators — pure Python, no pandas
# ---------------------------------------------------------------------------
def ema(xs: list[float], n: int) -> list[float]:
    k = 2 / (n + 1)
    out: list[float] = []
    e = None
    for x in xs:
        e = x if e is None else x * k + e * (1 - k)
        out.append(e)
    return out

def macd_lines(closes: list[float]):
    """Return (macd_line, signal_line, histogram) lists."""
    ema12   = ema(closes, 12)
    ema26   = ema(closes, 26)
    macd_l  = [a - b for a, b in zip(ema12, ema26)]
    signal  = ema(macd_l, 9)
    hist    = [m - s for m, s in zip(macd_l, signal)]
    return macd_l, signal, hist

def rsi(closes: list[float], period: int = 14) -> list[float]:
    """Wilder RSI. Returns a list aligned to closes[period:] (length = len(closes)-period)."""
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0.0))
        losses.append(max(-diff, 0.0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    result = []
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        rs = avg_gain / avg_loss if avg_loss else 100.0
        result.append(100.0 - 100.0 / (1 + rs))
    return result

# ---------------------------------------------------------------------------
# data fetch
# ---------------------------------------------------------------------------
CG_IDS = {"BTCUSDT": "bitcoin", "ETHUSDT": "ethereum", "SOLUSDT": "solana"}

def fetch_closes(asset: str) -> tuple[list[float], float]:
    """Returns (closes_list, latest_price). Falls back to CoinGecko."""
    url = (f"https://api.binance.com/api/v3/klines"
           f"?symbol={asset}&interval={INTERVAL}&limit={LIMIT}")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "macd-paper/1.0"})
        data = json.loads(urllib.request.urlopen(req, timeout=12).read())
        closes = [float(c[4]) for c in data]
        return closes, closes[-1]
    except Exception as e:
        logging.warning("Binance fetch failed for %s: %s — trying CoinGecko", asset, e)

    cg_url = (f"https://api.coingecko.com/api/v3/coins/{CG_IDS[asset]}"
              f"/ohlc?vs_currency=usd&days=14")
    req = urllib.request.Request(cg_url, headers={"User-Agent": "macd-paper/1.0"})
    data = json.loads(urllib.request.urlopen(req, timeout=12).read())
    closes = [float(c[4]) for c in data]
    return closes, closes[-1]

# ---------------------------------------------------------------------------
# state helpers
# ---------------------------------------------------------------------------
def load_state() -> dict:
    if os.path.exists(STATE):
        try:
            return json.load(open(STATE))
        except Exception:
            pass
    return {
        "positions": {},
        "signals":   {},
        "realized_pnl": 0.0,
        "ts": 0,
    }

def save_state(s: dict):
    tmp = STATE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(s, f, indent=2)
    os.replace(tmp, STATE)

# ---------------------------------------------------------------------------
# signal logic
# ---------------------------------------------------------------------------
def compute_signal(macd_l: list[float], signal: list[float], hist: list[float],
                   rsi_val: float | None = None) -> str:
    """BUY  = MACD crossed above signal AND RSI < 70 (not overbought).
       SELL = MACD crossed below signal AND RSI > 30 (not oversold).
       HOLD otherwise (includes RSI filter blocking a raw crossover)."""
    if len(macd_l) < 2:
        return "HOLD"
    prev_above = macd_l[-2] > signal[-2]
    now_above  = macd_l[-1] > signal[-1]
    if not prev_above and now_above:
        # RSI filter: skip BUY if overbought
        if rsi_val is not None and rsi_val >= 70:
            return "HOLD"
        return "BUY"
    if prev_above and not now_above:
        # RSI filter: skip SELL if oversold
        if rsi_val is not None and rsi_val <= 30:
            return "HOLD"
        return "SELL"
    return "HOLD"

# ---------------------------------------------------------------------------
# paper trade execution
# ---------------------------------------------------------------------------
def execute(asset: str, sig: str, price: float, state: dict):
    pos  = state["positions"].get(asset, {"side": "flat", "entry": 0.0,
                                          "qty": 0.0, "unrealized_pnl": 0.0})
    side = pos["side"]

    if sig == "BUY" and side == "flat":
        qty = round(NOTIONAL / price, 8)
        pos = {"side": "long", "entry": price, "qty": qty, "unrealized_pnl": 0.0}
        state["positions"][asset] = pos
        logging.info("OPEN  LONG  %s  qty=%.8f  entry=%.4f", asset, qty, price)

    elif sig == "SELL" and side == "long":
        qty  = pos["qty"]
        pnl  = (price - pos["entry"]) * qty
        state["realized_pnl"] = round(state["realized_pnl"] + pnl, 6)
        logging.info("CLOSE LONG  %s  qty=%.8f  entry=%.4f  exit=%.4f  pnl=%.4f",
                     asset, qty, pos["entry"], price, pnl)
        state["positions"][asset] = {"side": "flat", "entry": 0.0,
                                     "qty": 0.0, "unrealized_pnl": 0.0}

    else:
        # update unrealized for open longs
        if side == "long":
            pos["unrealized_pnl"] = round((price - pos["entry"]) * pos["qty"], 6)
        state["positions"][asset] = pos

# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main():
    state = load_state()
    state["ts"] = int(time.time())

    for asset in ASSETS:
        try:
            closes, price = fetch_closes(asset)
        except Exception as e:
            logging.error("Cannot fetch %s: %s", asset, e)
            continue

        if len(closes) < 35:
            logging.warning("%s: not enough candles (%d)", asset, len(closes))
            continue

        macd_l, signal, hist = macd_lines(closes)

        rsi_series = rsi(closes, 14)
        rsi_val    = round(rsi_series[-1], 2) if rsi_series else None

        sig = compute_signal(macd_l, signal, hist, rsi_val)
        state["signals"][asset] = {
            "signal":      sig,
            "macd":        round(macd_l[-1], 6),
            "signal_line": round(signal[-1], 6),
            "hist":        round(hist[-1], 6),
            "price":       round(price, 4),
            "rsi":         rsi_val,
        }

        execute(asset, sig, price, state)
        rsi_str = f"{rsi_val:.1f}" if rsi_val is not None else "?"
        print(f"{asset:10s}  price={price:>12.4f}  macd={macd_l[-1]:+.4f}"
              f"  sig={signal[-1]:+.4f}  hist={hist[-1]:+.4f}  rsi={rsi_str}  -> {sig}")

    save_state(state)
    print(f"\nRealized P&L: ${state['realized_pnl']:+.4f}")
    print(f"State written to {STATE}")

if __name__ == "__main__":
    main()
