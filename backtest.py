#!/usr/bin/env python3
"""
Polymarket Bot — Paper Trading Simulator  (v2)
===============================================
Simulates 90 days of trading.

Phase 1 — Bootstrap (silent): feed the brain 200 resolved historical
           markets so it starts with learned win-rates instead of cold
           Laplace defaults.  Mirrors --bootstrap in production.

Phase 2 — Live simulation: run all three strategies across 120 active
           markets every day for 90 days.

Runs 3 independent seeds and prints each + an average summary.
"""

import random
from collections import defaultdict
from dataclasses import dataclass
from typing import List, Dict, Optional

TOPICS = [
    "Will Bitcoin hit $150k by end of 2026?",
    "Will the Fed cut rates in June 2026?",
    "Will SpaceX land on Mars before 2030?",
    "Will Trump win the 2028 election?",
    "Will Ethereum hit $10k in 2026?",
    "Will Apple release AR glasses in 2026?",
    "Will US unemployment exceed 5% in Q3 2026?",
    "Will GPT-5 pass the bar exam?",
    "Will Nvidia stock double by end of 2026?",
    "Will there be a US recession in 2026?",
    "Will Taylor Swift tour in Asia in 2026?",
    "Will the UK rejoin the EU single market?",
    "Will gold hit $4000/oz in 2026?",
    "Will Solana flip Ethereum in market cap?",
    "Will TikTok be banned in the US?",
    "Will Amazon acquire a major bank?",
    "Will there be a major earthquake in California?",
    "Will Anthropic reach $100B valuation?",
    "Will OpenAI go public in 2026?",
    "Will oil drop below $50/barrel?",
    "Will China invade Taiwan before 2027?",
    "Will the S&P 500 hit 7000?",
    "Will a major airline go bankrupt in 2026?",
    "Will the next Pope be non-European?",
    "Will Elon Musk leave Tesla?",
    "Will Bitcoin ETF inflows exceed $50B?",
    "Will Dogecoin hit $1?",
    "Will Meta release a competitor to Claude?",
    "Will AGI be declared by a major lab in 2026?",
    "Will a country adopt Bitcoin as legal tender?",
]

CFG_BASE = {
    "bet_size_usdc":           3.0,
    "insider_bet_size_usdc":   1.5,  # insider bets half — limits damage
    "max_open_positions":      5,
    "max_daily_loss_usdc":     20.0,
    "take_profit_pct":         0.28,
    "stop_loss_pct":           0.13,
    # Consensus — the ONLY reliably profitable strategy (56% WR)
    # Lower share to 0.65 and min_traders to 3 so it fires more
    "consensus_min_traders":   3,
    "consensus_min_share":     0.65,
    "consensus_min_volume":    7_000,
    "consensus_max_positions": 5,   # consensus can fill ALL slots
    # Insider — capped at 1 position, only the sharpest signals
    "insider_max_volume":      12_000,
    "insider_max_price":       0.60,
    "insider_min_bet_usdc":    400,
    "insider_max_positions":   1,
    # Midline — DISABLED: 3.7% WR is worse than a coin flip
    "midline_buy_max":         0.0,   # 0.0 = never fires
    "midline_sell_target":     0.50,
    "midline_min_volume":      999_999,
    "midline_max_positions":   0,
    "min_price":               0.08,
    "max_price":               0.88,
    # Learning
    "min_confidence":          0.52,
    "min_confidence_floor":    0.52,
    "autotune_every_n":        20,
}


# ── Market ────────────────────────────────────────────────────────────────────

@dataclass
class Market:
    id:           str
    title:        str
    true_prob:    float
    start_price:  float
    volume:       float
    duration:     int
    resolved_yes: bool
    current_price: float = 0.0
    day_created:  int = 0

    def __post_init__(self):
        self.current_price = self.start_price

    def step(self, day: int):
        days_live = day - self.day_created
        progress  = min(days_live / self.duration, 1.0)
        target    = 1.0 if self.resolved_yes else 0.0
        drift     = (target - self.current_price) * progress * 0.35
        noise     = random.gauss(0, 0.022)
        self.current_price = max(0.02, min(0.98,
            self.current_price + drift + noise))

    def is_expired(self, day: int) -> bool:
        return day >= self.day_created + self.duration


@dataclass
class Position:
    market_id:    str
    title:        str
    strategy:     str
    entry_price:  float
    size_tokens:  float
    sell_target:  Optional[float]
    entry_day:    int
    volume:       float
    trader_count: int


@dataclass
class Trade:
    strategy:    str
    entry_price: float
    exit_price:  float
    pnl:         float
    won:         bool
    reason:      str


# ── Brain ─────────────────────────────────────────────────────────────────────

class Brain:
    def __init__(self, cfg: dict):
        self.cfg    = cfg
        self.stats  = defaultdict(lambda: defaultdict(lambda: [0, 0]))
        self.total       = [0, 0]   # all (bootstrap + live)
        self._live_total = [0, 0]   # live only — used for autotune
        self.log    = []
        self._closed_since_tune = 0

    def _pb(self, p):
        if p < 0.15: return "longshot"
        if p < 0.40: return "low"
        if p < 0.60: return "mid"
        if p < 0.80: return "high"
        return "near"

    def _vb(self, v):
        if v < 5_000:   return "micro"
        if v < 20_000:  return "thin"
        if v < 80_000:  return "medium"
        return "deep"

    def score(self, strategy: str, price: float, volume: float) -> float:
        dims = [
            ("strategy",  strategy),
            ("price",     self._pb(price)),
            ("volume",    self._vb(volume)),
        ]
        weighted_sum, total_w = 0.0, 0.0
        for dim, key in dims:
            w, l = self.stats[dim][key]
            n    = w + l
            rate = (w + 1) / (n + 2)
            wt   = 1.0 if n >= 5 else 0.5
            weighted_sum += rate * wt
            total_w      += wt
        return weighted_sum / total_w if total_w else 0.5

    def record(self, strategy: str, price: float, volume: float,
               won: bool, from_bootstrap: bool = False):
        dims = [("strategy", strategy), ("price", self._pb(price)),
                ("volume",   self._vb(volume))]
        for dim, key in dims:
            self.stats[dim][key][0 if won else 1] += 1
        self.total[0 if won else 1] += 1
        if not from_bootstrap:
            self._live_total[0 if won else 1] += 1
            self._closed_since_tune += 1
            if self._closed_since_tune >= self.cfg["autotune_every_n"]:
                self._closed_since_tune = 0
                self._autotune()

    def _autotune(self):
        t = self._live_total[0] + self._live_total[1]  # live trades only
        if t < 10:
            return
        wr  = self._live_total[0] / t
        old = self.cfg["min_confidence"]
        floor = self.cfg.get("min_confidence_floor", 0.52)
        # Only raise when losing — never lower below floor
        if wr < 0.44:
            self.cfg["min_confidence"] = min(old + 0.03, 0.72)
        elif wr > 0.62 and old > floor:
            self.cfg["min_confidence"] = max(old - 0.01, floor)
        if self.cfg["min_confidence"] != old:
            self.log.append(
                f"  Auto-tune: min_confidence {old:.2f}→"
                f"{self.cfg['min_confidence']:.2f}  (live WR={wr:.1%}, n={t})"
            )

    # Bootstrap: teach brain from 200 synthetic historical resolved markets
    def bootstrap(self, rng: random.Random):
        for _ in range(200):
            true_p   = rng.uniform(0.10, 0.90)
            start    = max(0.05, min(0.93, true_p + rng.gauss(0, 0.10)))
            volume   = rng.choice([
                rng.uniform(3_000, 10_000),
                rng.uniform(10_000, 80_000),
                rng.uniform(80_000, 300_000),
            ])
            resolved = rng.random() < true_p

            # Consensus: top traders right 58% of the time
            side_yes = rng.random() < (0.58 if resolved else 0.42)
            won = (side_yes == resolved)
            self.record("consensus", start, volume, won, from_bootstrap=True)

            # Insider: right 64% on big bets on thin markets
            if volume < 15_000:
                side_yes2 = rng.random() < (0.58 if resolved else 0.42)
                won2 = (side_yes2 == resolved)
                self.record("insider", start, volume, won2, from_bootstrap=True)

            # Midline: price reverts to 0.50 about 52% of the time
            if start < 0.47:
                won3 = rng.random() < 0.52
                self.record("midline", start, volume, won3, from_bootstrap=True)

    def overall_wr(self):
        t = sum(self.total)
        return self.total[0] / t * 100 if t else 0


# ── Signal generators ─────────────────────────────────────────────────────────

def sig_consensus(mkt: Market, cfg: dict, rng: random.Random):
    if mkt.volume < cfg["consensus_min_volume"]: return None
    p = mkt.current_price
    if not (cfg["min_price"] <= p <= cfg["max_price"]): return None

    n = rng.randint(2, 14)
    if n < cfg["consensus_min_traders"]: return None
    yes_n = sum(1 for _ in range(n)
                if rng.random() < (0.58 if mkt.resolved_yes else 0.42))
    no_n  = n - yes_n

    for side, count, entry in [("YES", yes_n, p), ("NO", no_n, 1-p)]:
        share = count / n
        if count >= cfg["consensus_min_traders"] and share >= cfg["consensus_min_share"]:
            if cfg["min_price"] <= entry <= cfg["max_price"]:
                return {"strategy":"consensus","side":side,"price":entry,
                        "trader_count":count,"volume":mkt.volume,
                        "title":mkt.title,"market_id":mkt.id}
    return None


def sig_insider(mkt: Market, cfg: dict, rng: random.Random):
    if mkt.volume > cfg["insider_max_volume"]: return None
    p = mkt.current_price
    if p > cfg["insider_max_price"] or p < cfg["min_price"]: return None
    if rng.random() > 0.18: return None  # 18% of thin markets have a signal

    side_yes = rng.random() < (0.64 if mkt.resolved_yes else 0.36)
    side  = "YES" if side_yes else "NO"
    entry = p if side == "YES" else (1 - p)
    if not (cfg["min_price"] <= entry <= cfg["max_price"]): return None

    return {"strategy":"insider","side":side,"price":entry,
            "bet_usd": rng.uniform(200, 900),
            "trader_count":1,"volume":mkt.volume,
            "title":mkt.title,"market_id":mkt.id}


def sig_midline(mkt: Market, cfg: dict, rng: random.Random):
    p = mkt.current_price
    if p > cfg["midline_buy_max"] or p < cfg["min_price"]: return None
    if mkt.volume < cfg["midline_min_volume"]: return None
    if rng.random() > 0.55: return None   # top traders holding YES

    return {"strategy":"midline","side":"YES","price":p,
            "sell_target": cfg["midline_sell_target"],
            "trader_count": rng.randint(1, 5),
            "volume": mkt.volume,
            "title": mkt.title,"market_id": mkt.id}


# ── Simulator ─────────────────────────────────────────────────────────────────

class Sim:

    def __init__(self, seed: int, days: int = 90):
        self.seed  = seed
        self.days  = days
        self.rng   = random.Random(seed)
        self.cfg   = dict(CFG_BASE)
        self.brain = Brain(self.cfg)
        self.open:   Dict[str, Position] = {}
        self.trades: List[Trade]         = []
        self.balance      = 0.0
        self.daily_pnl    = 0.0
        self.bal_curve    = []
        self.stats = defaultdict(int)

        # Insider circuit breaker (mirrors polymarket_bot.py)
        self._insider_consec_losses = 0
        self._insider_paused_until  = -1   # day number when pause ends

    def _gen_markets(self, n: int = 120) -> List[Market]:
        mkts = []
        titles = TOPICS * 4
        for i in range(n):
            true_p  = self.rng.uniform(0.10, 0.90)
            start   = max(0.06, min(0.92, true_p + self.rng.gauss(0, 0.10)))
            vol     = self.rng.choice([
                self.rng.uniform(2_000, 8_000),
                self.rng.uniform(8_000, 30_000),
                self.rng.uniform(30_000, 200_000),
            ])
            resolved = self.rng.random() < true_p
            dur = self.rng.randint(10, 50)
            day_created = self.rng.randint(0, 20)  # stagger start dates

            mkts.append(Market(
                id=f"m{i:03d}",
                title=titles[i % len(titles)],
                true_prob=true_p,
                start_price=start,
                volume=vol,
                duration=dur,
                resolved_yes=resolved,
                day_created=day_created,
            ))

        # Extra midline-eligible markets
        for j in range(25):
            true_p = self.rng.uniform(0.35, 0.65)
            start  = self.rng.uniform(0.38, 0.47)
            mkts.append(Market(
                id=f"ml{j:02d}",
                title=f"Close-call market #{j+1}",
                true_prob=true_p,
                start_price=start,
                volume=self.rng.uniform(8_000, 22_000),
                duration=self.rng.randint(7, 25),
                resolved_yes=self.rng.random() < true_p,
                day_created=self.rng.randint(0, 15),
            ))

        return mkts

    def _place(self, sig: dict, day: int) -> bool:
        mid      = sig["market_id"]
        strategy = sig["strategy"]
        if mid in self.open:                              return False
        if len(self.open) >= self.cfg["max_open_positions"]: return False
        if self.daily_pnl <= -self.cfg["max_daily_loss_usdc"]: return False

        # Insider circuit breaker
        if strategy == "insider" and day < self._insider_paused_until:
            self.stats["skip_insider_pause"] += 1
            return False

        # Per-strategy position caps (prevent insider/midline from dominating)
        strat_count = sum(1 for p in self.open.values() if p.strategy == strategy)
        cap_key = f"{strategy}_max_positions"
        if cap_key in self.cfg and strat_count >= self.cfg[cap_key]:
            return False

        conf = self.brain.score(strategy, sig["price"], sig["volume"])
        if conf < self.cfg["min_confidence"]:
            self.stats["skip_conf"] += 1
            return False

        # Use reduced bet size for insider (half the standard)
        if strategy == "insider":
            usd = self.cfg.get("insider_bet_size_usdc", self.cfg["bet_size_usdc"] / 2)
        else:
            usd = self.cfg["bet_size_usdc"]
        size = usd / sig["price"]
        self.open[mid] = Position(
            market_id    = mid,
            title        = sig["title"][:50],
            strategy     = sig["strategy"],
            entry_price  = sig["price"],
            size_tokens  = size,
            sell_target  = sig.get("sell_target"),
            entry_day    = day,
            volume       = sig["volume"],
            trader_count = sig.get("trader_count", 1),
        )
        self.stats["placed"]             += 1
        self.stats[sig["strategy"]]      += 1
        return True

    def _close(self, mid: str, pos: Position, price: float,
               reason: str, day: int):
        pnl = (price - pos.entry_price) * pos.size_tokens
        won = pnl > 0
        self.balance    += pnl
        self.daily_pnl  += pnl
        self.brain.record(pos.strategy, pos.entry_price, pos.volume, won, from_bootstrap=False)
        self.stats[reason]   += 1
        self.trades.append(Trade(
            strategy    = pos.strategy,
            entry_price = pos.entry_price,
            exit_price  = price,
            pnl         = round(pnl, 4),
            won         = won,
            reason      = reason,
        ))

        # Insider circuit breaker: pause 3 days after 3 consecutive losses
        if pos.strategy == "insider":
            if won:
                self._insider_consec_losses = 0
            else:
                self._insider_consec_losses += 1
                if self._insider_consec_losses >= 3:
                    self._insider_paused_until  = day + 3
                    self._insider_consec_losses = 0
                    self.stats["insider_cb_fires"] += 1

        del self.open[mid]

    def run(self) -> dict:
        # Phase 1 — bootstrap brain from history
        self.brain.bootstrap(self.rng)

        # Phase 2 — live simulation
        markets = self._gen_markets()

        for day in range(self.days):
            self.daily_pnl = 0.0
            active = [m for m in markets
                      if m.day_created <= day and not m.is_expired(day)]
            for m in active:
                m.step(day)

            # Generate signals (scan all active markets)
            sigs = []
            for mkt in active:
                c = sig_consensus(mkt, self.cfg, self.rng)
                if c: sigs.append(c)
                i = sig_insider(mkt, self.cfg, self.rng)
                if i: sigs.append(i)
                ml = sig_midline(mkt, self.cfg, self.rng)
                if ml: sigs.append(ml)

            self.stats["signals"] += len(sigs)

            # Prioritise: insider → midline → consensus
            order = ["insider","midline","consensus"]
            sigs.sort(key=lambda s: order.index(s["strategy"]))
            for sig in sigs:
                if len(self.open) >= self.cfg["max_open_positions"]:
                    self.stats["skip_cap"] += len(sigs) - sigs.index(sig)
                    break
                self._place(sig, day)

            # Monitor positions
            for mid, pos in list(self.open.items()):
                mkt = next((m for m in markets if m.id == mid), None)
                if not mkt:
                    continue
                cur     = mkt.current_price
                pnl_pct = (cur - pos.entry_price) / pos.entry_price

                if pos.strategy == "midline" and pos.sell_target:
                    if cur >= pos.sell_target:
                        self._close(mid, pos, cur, "midline_target", day)
                    elif pnl_pct <= -self.cfg["stop_loss_pct"]:
                        self._close(mid, pos, cur, "sl", day)
                else:
                    if pnl_pct >= self.cfg["take_profit_pct"]:
                        self._close(mid, pos, cur, "tp", day)
                    elif pnl_pct <= -self.cfg["stop_loss_pct"]:
                        self._close(mid, pos, cur, "sl", day)

                if mkt.is_expired(day) and mid in self.open:
                    final = (1.0 if mkt.resolved_yes else 0.0)
                    self._close(mid, pos, final, "expired", day)

            self.bal_curve.append(round(self.balance, 2))

        # Close any remaining
        for mid, pos in list(self.open.items()):
            mkt = next((m for m in markets if m.id == mid), None)
            p   = mkt.current_price if mkt else pos.entry_price
            self._close(mid, pos, p, "expired", self.days)

        n      = len(self.trades)
        wins   = sum(1 for t in self.trades if t.won)
        total  = sum(t.pnl for t in self.trades)
        placed = self.stats["placed"]
        roi    = total / (placed * self.cfg["bet_size_usdc"]) * 100 if placed else 0

        return {
            "seed": self.seed, "n": n, "wins": wins,
            "wr": wins/n*100 if n else 0,
            "pnl": round(total, 2), "roi": round(roi, 1),
            "placed": placed,
            "by_strat": {
                s: {
                    "n":   sum(1 for t in self.trades if t.strategy==s),
                    "wins":sum(1 for t in self.trades if t.strategy==s and t.won),
                    "pnl": round(sum(t.pnl for t in self.trades if t.strategy==s),2),
                }
                for s in ("consensus","insider","midline")
            },
            "exits": {
                "tp":             self.stats["tp"],
                "sl":             self.stats["sl"],
                "midline_target": self.stats["midline_target"],
                "expired":        self.stats["expired"],
            },
            "signals":            self.stats["signals"],
            "skip_conf":          self.stats["skip_conf"],
            "skip_cap":           self.stats["skip_cap"],
            "skip_insider_pause": self.stats["skip_insider_pause"],
            "insider_cb_fires":   self.stats["insider_cb_fires"],
            "bal_curve": self.bal_curve,
            "tune_log":  self.brain.log,
            "trades":    self.trades,
        }


# ── Print results ─────────────────────────────────────────────────────────────

def print_run(r: dict):
    trades = r["trades"]
    best   = sorted(trades, key=lambda t: t.pnl, reverse=True)[:3]
    worst  = sorted(trades, key=lambda t: t.pnl)[:3]

    print(f"\n  Seed {r['seed']} | {r['n']} trades | "
          f"WR {r['wr']:.1f}% | Net ${r['pnl']:+.2f} | ROI {r['roi']:+.1f}%")

    for s, d in r["by_strat"].items():
        if d["n"] == 0: continue
        swr = d["wins"]/d["n"]*100
        print(f"    {s:<10}: {d['n']:>3} trades | {swr:>5.1f}% WR | ${d['pnl']:>+7.2f}")

    exits = r["exits"]
    print(f"    Exits — TP:{exits['tp']}  SL:{exits['sl']}  "
          f"Midline:{exits['midline_target']}  Expired:{exits['expired']}")
    print(f"    Insider CB — fires:{r['insider_cb_fires']}  "
          f"signals skipped:{r['skip_insider_pause']}")

    # 30-day rolling balance
    curve = r["bal_curve"]
    print(f"    Balance:  "
          f"30d=${curve[29]:>+6.2f}  "
          f"60d=${curve[59]:>+6.2f}  "
          f"90d=${curve[89]:>+6.2f}")

    if r["tune_log"]:
        for line in r["tune_log"]:
            print(f"    {line}")

    print(f"    Best  : ", end="")
    print("  |  ".join(f"+${t.pnl:.2f} [{t.strategy}]" for t in best))
    print(f"    Worst : ", end="")
    print("  |  ".join(f"-${abs(t.pnl):.2f} [{t.strategy}]" for t in worst))


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    SEEDS  = [42, 7, 99]
    DAYS   = 90
    results = []

    print("=" * 65)
    print("  Polymarket Bot — Paper Trading Simulation (v2)")
    print(f"  90 days  |  $3/trade  |  3 independent runs")
    print(f"  Bootstrapped brain: 200 historical markets pre-loaded")
    print("=" * 65)

    for seed in SEEDS:
        sim = Sim(seed=seed, days=DAYS)
        r   = sim.run()
        results.append(r)
        print_run(r)

    # Summary across all seeds
    avg_pnl = sum(r["pnl"] for r in results) / len(results)
    avg_wr  = sum(r["wr"]  for r in results) / len(results)
    avg_roi = sum(r["roi"] for r in results) / len(results)
    avg_n   = sum(r["n"]   for r in results) / len(results)

    print("\n" + "=" * 65)
    print("  AVERAGE ACROSS 3 RUNS")
    print("=" * 65)
    print(f"  Avg trades / run   : {avg_n:.0f}")
    print(f"  Avg win rate       : {avg_wr:.1f}%")
    print(f"  Avg net PnL        : ${avg_pnl:+.2f}")
    print(f"  Avg ROI per trade  : {avg_roi:+.1f}%")

    profitable = sum(1 for r in results if r["pnl"] > 0)
    print(f"  Profitable runs    : {profitable}/{len(results)}")

    print("\n  Strategy averages:")
    for s in ("consensus", "insider", "midline"):
        ns   = [r["by_strat"][s]["n"]   for r in results]
        pnls = [r["by_strat"][s]["pnl"] for r in results]
        wrsl = [r["by_strat"][s]["wins"]/r["by_strat"][s]["n"]*100
                if r["by_strat"][s]["n"] else 0 for r in results]
        print(f"    {s:<10}: avg {sum(ns)/3:.0f} trades | "
              f"avg WR {sum(wrsl)/3:.1f}% | avg PnL ${sum(pnls)/3:+.2f}")

    verdict = "PROFITABLE" if avg_pnl > 0 else "UNPROFITABLE"
    print(f"\n  VERDICT: {verdict}")
    print("=" * 65)
