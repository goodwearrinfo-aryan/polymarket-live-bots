# Polymarket Auto Trading Bot

An automated trading bot for [Polymarket](https://polymarket.com) that scans prediction markets, finds value bets, and manages positions with take-profit and stop-loss logic.

---

## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Set up your environment

```bash
cp .env.example .env
```

Open `.env` and add your **Polygon wallet private key** (the wallet you use on Polymarket).

### 3. Generate API credentials

```bash
python polymarket_bot.py --setup
```

This derives your Polymarket CLOB API key from your wallet and saves the credentials to `.env`.

### 4. Run in dry-run mode (safe — no real orders)

```bash
python polymarket_bot.py --dry-run
```

Watch the logs. The bot will scan markets, identify value bets, and simulate trades without spending any real money.

### 5. Go live

When you're happy with the strategy, open `polymarket_bot.py` and set:

```python
"dry_run": False,
```

Then run:

```bash
python polymarket_bot.py
```

---

## Configuration

All settings are in the `CONFIG` dict at the top of `polymarket_bot.py`:

| Setting | Default | Description |
|---|---|---|
| `bet_size_usdc` | `5.0` | USDC to stake per trade |
| `min_probability` | `0.05` | Skip markets below 5% |
| `max_probability` | `0.95` | Skip markets above 95% |
| `edge_threshold` | `0.06` | Minimum edge (6%) to place a bet |
| `take_profit_pct` | `0.25` | Close position at +25% |
| `stop_loss_pct` | `0.12` | Close position at -12% |
| `max_open_positions` | `5` | Maximum simultaneous positions |
| `scan_interval_sec` | `60` | Seconds between market scans |
| `max_markets_scan` | `50` | Markets to evaluate per scan |
| `dry_run` | `True` | Simulation mode (no real orders) |

---

## Adding Your Strategy

The bot ships with a placeholder strategy. Open `polymarket_bot.py` and find:

```python
def estimate_probability(market: dict) -> Optional[float]:
    # TODO: implement your edge here
    return None  # returning None skips this market
```

Return a float between 0 and 1 representing your probability estimate for YES. If it differs from the market price by more than `edge_threshold`, the bot places a bet.

**Example strategies:**
- News sentiment analysis
- Statistical base rates
- External model / API predictions
- Manual watchlist overrides

---

## Files

| File | Description |
|---|---|
| `polymarket_bot.py` | Main bot script |
| `requirements.txt` | Python dependencies |
| `.env.example` | Environment variable template |
| `.env` | Your secrets (never commit this) |
| `polymarket_bot.log` | Runtime log (created on first run) |
| `trade_log.json` | Trade history (created on first run) |

---

## Safety Notes

- Always test with `dry_run: True` first
- Start with small `bet_size_usdc` values
- Never commit your `.env` file to version control
- Keep your private key secure — it controls your Polygon wallet
