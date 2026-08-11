#!/usr/bin/env python3
"""
maker_fill_honest.py — an HONEST maker/taker fill model for paper backtests.

Why this exists
---------------
Hummingbot's paper engine (connector/exchange/paper_trade/paper_trade_exchange.pyx)
fills a resting LIMIT order the moment a real trade prints *through* its price
(c_match_trade_to_limit_orders) and fills a MARKET order at the size-weighted
average price across consumed book depth (simulate_buy -> VWAP). That maker-cross
discipline is exactly what the project's spread-capture / MM sim lacks ("fake
fills"). BUT Hummingbot has one OPTIMISTIC bias: it fills a maker on a mere
price *touch*, with NO queue-position model — in reality you sit behind the
resting size already at your level and may never fill.

This module ports the honest part and FIXES the optimism:
  1) trade-cross discipline  : a resting quote fills only when price trades
                               *through* it (strict clear, not just touch);
  2) queue-position haircut  : even on a cross you fill only the volume that
                               trades through your level BEYOND the queue ahead
                               of you — the thing Hummingbot omits;
  3) VWAP taker slippage     : market orders walk real depth (worse fill for
                               bigger size), not the top-of-book mid;
  4) round-trip fees         : maker and taker fee rates applied per side.

Two fidelity tiers
------------------
  * TICK tier  (maker_fill_from_trades): the truly honest engine — feed it real
    trade prints (ts, price, size, aggressor). Use this once tick/trade data is
    collected. Mirrors Hummingbot's logic + the queue haircut.
  * CANDLE tier (maker_fill_on_bar): an APPROXIMATION for when you only have
    OHLCV (the data candle_short.py / candle_lab already fetch). It cannot see
    volume-at-price, so it proxies through-volume from how deeply the bar pierces
    your level. It is deliberately CONSERVATIVE (strict clear + haircut), i.e.
    biased the opposite way to Hummingbot. Treat its fills as a floor, not truth.

Keyless: the optional real-data demo reuses the public Binance klines endpoint
(same source as candle_short.fetch_klines). No keys, paper only, no orders placed.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Optional, Tuple
import sys

# ---- fee schedule (override per venue). Polymarket basket locks = 0; CEX MM ~ maker rebate/fee.
DEFAULT_MAKER_FEE = 0.0000   # fraction of notional, per side
DEFAULT_TAKER_FEE = 0.0005   # 5 bps/side example; set to your venue's real taker fee


@dataclass
class RestingOrder:
    side: str          # 'buy' (resting bid) or 'sell' (resting ask)
    price: float       # limit price
    size: float        # base units desired
    queue_ahead: float # base units resting AT your price level AHEAD of you when you joined
    filled: float = 0.0
    fills: List[Tuple[float, float]] = field(default_factory=list)  # (price, size)

    @property
    def remaining(self) -> float:
        return max(0.0, self.size - self.filled)

    @property
    def avg_fill_price(self) -> Optional[float]:
        if self.filled <= 0:
            return None
        return sum(p * s for p, s in self.fills) / self.filled


# ----------------------------------------------------------------------------
# TICK TIER — the honest engine. Feed real trade prints.
# ----------------------------------------------------------------------------
def maker_fill_from_trades(order: RestingOrder,
                           trades: List[Tuple[float, float, float, str]],
                           tick: float = 0.0) -> RestingOrder:
    """
    Fill a resting limit order against a stream of REAL trades.
    trades: list of (timestamp, price, size, aggressor) where aggressor is
            'buy' (a taker BUY lifting asks) or 'sell' (a taker SELL hitting bids).

    Discipline (mirrors Hummingbot c_match_trade_to_limit_orders + a queue model):
      - A resting BID at price P is hit by taker SELLs that trade at price <= P.
      - A resting ASK at price P is lifted by taker BUYs that trade at price >= P.
      - 'tick' (optional) requires a STRICT clear (price < P-tick / > P+tick) so a
        mere touch at exactly P does not auto-fill — the conservative choice.
      - Queue: incoming through-volume first consumes 'queue_ahead'; only the
        remainder fills YOU. This is the piece Hummingbot leaves out.
    """
    queue = order.queue_ahead
    for _ts, price, size, aggressor in trades:
        if order.remaining <= 0:
            break
        if order.side == 'buy':
            crosses = (aggressor == 'sell') and (price <= order.price - tick)
        else:
            crosses = (aggressor == 'buy') and (price >= order.price + tick)
        if not crosses:
            continue
        vol = size
        # consume the queue ahead of us first
        if queue > 0:
            eaten = min(queue, vol)
            queue -= eaten
            vol -= eaten
        if vol <= 0:
            continue
        take = min(order.remaining, vol)
        # maker fills AT your limit price (you set the price; you don't pay spread)
        order.filled += take
        order.fills.append((order.price, take))
    return order


# ----------------------------------------------------------------------------
# CANDLE TIER — approximation when you only have OHLCV.
# ----------------------------------------------------------------------------
def maker_fill_on_bar(order: RestingOrder,
                      bar: Tuple[float, float, float, float, float],
                      tick: float = 0.0,
                      max_through_frac: float = 1.0) -> RestingOrder:
    """
    Approximate a maker fill within ONE OHLCV bar (o,h,l,c,v).

    Cannot see volume-at-price, so it proxies the volume that traded THROUGH your
    level by how deeply the bar pierced it:
        bid at P: pierce = max(0, P - low);  through_frac = pierce / (high-low)
        ask at P: pierce = max(0, high - P); through_frac = pierce / (high-low)
    through_vol = bar_volume * min(max_through_frac, through_frac).
    Then the queue-ahead haircut is applied, exactly like the tick engine.

    Conservative by construction:
      - requires a STRICT clear (low < P-tick for a bid) — a touch does NOT fill;
      - linear pierce proxy under-credits shallow pierces;
      - queue_ahead still has to be eaten first.
    This is a FLOOR on fills, the opposite of Hummingbot's optimistic touch-fill.
    """
    o, h, l, c, v = bar
    rng = max(h - l, 1e-12)
    if order.side == 'buy':
        if not (l < order.price - tick):      # strict clear-through required
            return order
        pierce = max(0.0, order.price - l)
    else:
        if not (h > order.price + tick):
            return order
        pierce = max(0.0, h - order.price)
    through_frac = min(max_through_frac, pierce / rng)
    through_vol = v * through_frac
    # queue haircut
    avail = through_vol - order.queue_ahead
    if avail <= 0:
        return order
    take = min(order.remaining, avail)
    if take <= 0:
        return order
    order.filled += take
    order.fills.append((order.price, take))
    return order


# ----------------------------------------------------------------------------
# TAKER — VWAP across real book depth (Hummingbot simulate_buy equivalent).
# ----------------------------------------------------------------------------
def taker_vwap(levels: List[Tuple[float, float]], size: float) -> Tuple[float, float, float]:
    """
    Walk an order book and return (filled_size, vwap, worst_price).
    levels: [(price, size), ...] in the order they'd be consumed
            (asks ascending for a BUY, bids descending for a SELL).
    Bigger 'size' eats deeper => worse vwap => honest slippage.
    """
    remaining, cost, worst = size, 0.0, None
    for price, avail in levels:
        if remaining <= 0:
            break
        take = min(remaining, avail)
        cost += take * price
        worst = price
        remaining -= take
    filled = size - remaining
    vwap = cost / filled if filled > 0 else float('nan')
    return filled, vwap, (worst if worst is not None else float('nan'))


def roundtrip_cost(notional: float, maker: bool,
                   maker_fee: float = DEFAULT_MAKER_FEE,
                   taker_fee: float = DEFAULT_TAKER_FEE) -> float:
    """Fee for ONE side. Call twice (entry+exit) for a round trip."""
    return notional * (maker_fee if maker else taker_fee)


# ----------------------------------------------------------------------------
# Self-test + optional real-data demo
# ----------------------------------------------------------------------------
def _selftest() -> None:
    print("== TICK tier: queue-haircut discipline ==")
    # Resting bid 100.0 size 10, queue 4 ahead. Taker SELLs print through.
    o = RestingOrder('buy', 100.0, 10.0, queue_ahead=4.0)
    trades = [
        (1, 100.5, 5.0, 'sell'),   # price 100.5 > 100 -> does NOT cross a bid
        (2, 100.0, 3.0, 'sell'),   # touch at exactly 100 with tick=0.01 -> no fill
        (3,  99.9, 6.0, 'sell'),   # clears through: 6 vol; eats 4 queue -> 2 fills you
        (4,  99.8, 9.0, 'sell'),   # clears: 9 vol, queue gone -> fills remaining 8
    ]
    maker_fill_from_trades(o, trades, tick=0.01)
    assert abs(o.filled - 10.0) < 1e-9, o.filled
    assert o.avg_fill_price == 100.0
    print(f"   filled {o.filled} @ {o.avg_fill_price} (touch ignored, queue eaten first) OK")

    print("== TICK tier: a pure touch never fills ==")
    o2 = RestingOrder('sell', 200.0, 5.0, queue_ahead=0.0)
    maker_fill_from_trades(o2, [(1, 200.0, 100.0, 'buy')], tick=0.01)  # exact touch
    assert o2.filled == 0.0
    print("   touch-only -> 0 fill (vs Hummingbot would fill) OK")

    print("== CANDLE tier: strict clear + pierce proxy + haircut ==")
    # bid 100, size 10, queue 3. Bar pierces to low 99 (deep), vol 100.
    oc = RestingOrder('buy', 100.0, 10.0, queue_ahead=3.0)
    maker_fill_on_bar(oc, (100.5, 101.0, 99.0, 100.2, 100.0), tick=0.01)
    # range=2, pierce=1 -> through_frac .5 -> through_vol 50 -> minus 3 queue -> fill 10
    assert abs(oc.filled - 10.0) < 1e-9, oc.filled
    print(f"   deep pierce -> filled {oc.filled} OK")
    oc2 = RestingOrder('buy', 100.0, 10.0, queue_ahead=3.0)
    maker_fill_on_bar(oc2, (100.5, 101.0, 100.0, 100.4, 100.0), tick=0.01)  # low==price: touch
    assert oc2.filled == 0.0
    print("   bar only touches (low==price) -> 0 fill OK")

    print("== TAKER: VWAP slippage scales with size ==")
    book = [(100.0, 5), (100.1, 5), (100.2, 5), (100.5, 50)]  # asks
    _, vwap_small, _ = taker_vwap(book, 5)
    _, vwap_big, _ = taker_vwap(book, 20)
    assert vwap_small == 100.0 and vwap_big > vwap_small
    print(f"   buy 5 -> vwap {vwap_small}; buy 20 -> vwap {vwap_big:.4f} (deeper=worse) OK")
    print("\nAll self-tests passed.")


def _demo_real(symbol: str, interval: str, bars: int) -> None:
    """Run the candle-tier maker model over real keyless Binance klines."""
    try:
        from candle_short import fetch_klines
    except Exception as e:
        print(f"(skip real demo: cannot import candle_short.fetch_klines: {e})")
        return
    O, H, L, C, V = fetch_klines(symbol, interval, bars)
    if not C:
        print("(skip real demo: no klines returned)")
        return
    n = len(C)
    # Naive passive-MM demo: each bar, rest a bid 5bps below prior close, size 1,
    # assume queue_ahead = 0.5*bar_volume (you're mid-queue). Count honest fills.
    fills, attempts = 0, 0
    for i in range(1, n):
        ref = C[i - 1]
        bid = ref * (1 - 0.0005)
        bar = (O[i], H[i], L[i], C[i], V[i])
        q = 0.5 * V[i]
        ordr = RestingOrder('buy', bid, 1.0, queue_ahead=q)
        maker_fill_on_bar(ordr, bar, tick=ref * 1e-5)
        attempts += 1
        if ordr.filled > 0:
            fills += 1
    rate = fills / attempts if attempts else 0.0
    print(f"\n== REAL candle demo {symbol} {interval} ({n} bars) ==")
    print(f"   passive bid 5bps below close, queue=50% bar vol:")
    print(f"   honest fill rate = {fills}/{attempts} = {rate:.1%}")
    print("   (NOTE: candle-tier is a conservative APPROXIMATION — collect tick")
    print("    data and use maker_fill_from_trades for the truly honest number.)")


if __name__ == '__main__':
    _selftest()
    if '--real' in sys.argv:
        # usage: python maker_fill_honest.py --real BTCUSDT 1h 1000
        a = sys.argv[sys.argv.index('--real') + 1:]
        sym = a[0] if len(a) > 0 else 'BTCUSDT'
        itv = a[1] if len(a) > 1 else '1h'
        bars = int(a[2]) if len(a) > 2 else 1000
        _demo_real(sym, itv, bars)
