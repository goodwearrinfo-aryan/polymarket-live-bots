#!/usr/bin/env python3
"""orderflow_probe.py — Internal Polymarket trade velocity signal (read-only).

For each high-volume live market, compare last-1h trade volume to the 24h
average hourly rate. A velocity spike (>3x) means informed money is moving NOW
— this is the earliest internal signal before it shows up in price.

Also tracks buy/sell imbalance (delta): net positive = buyers pressing,
net negative = sellers pressing. Combined with velocity = directional momentum.

Metrics per market:
  velocity_ratio = vol_last_1h / (vol_24h / 24)   (1.0 = normal, >3 = spike)
  delta_ratio    = (buy_vol - sell_vol) / total_vol (+1 = all buyers, -1 = all sellers)
  signal         = velocity_ratio * |delta_ratio|

Read-only (no trades). Run every 1h via watchdog. Obsidian export each run.

Usage:
    python3 orderflow_probe.py          # scan + export
    python3 orderflow_probe.py report   # reprint cached
"""

import json, os, re, sys, time, urllib.request
from concurrent.futures import ThreadPoolExecutor

HERE  = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "orderflow_cache.json")
VAULT = os.path.expanduser("~/Documents/PolymarketVault/Reports/orderflow_probe.md")
UA    = {"User-Agent": "Mozilla/5.0"}

GAMMA    = "https://gamma-api.polymarket.com/markets"
DATA_API = "https://data-api.polymarket.com/trades"

MIN_VOL_24H = 10_000    # minimum 24h volume to bother scanning
POLY_PAGES  = 4         # ~400 candidate markets
WORKERS     = 6         # concurrent data-api calls (be polite)
VEL_SPIKE   = 3.0       # velocity ratio threshold to flag
ALERT_VEL   = 5.0       # iMessage threshold (very unusual activity)
TRADES_FETCH = 200       # trades to fetch per market


def _get(url, timeout=12):
    try:
        req = urllib.request.Request(url, headers=UA)
        return json.loads(urllib.request.urlopen(req, timeout=timeout).read())
    except Exception:
        return None


# ── Polymarket markets ───────────────────────────────────────────────────────

def poly_markets():
    out = []
    for pg in range(POLY_PAGES):
        batch = _get(f"{GAMMA}?active=true&closed=false&order=volume&ascending=false"
                     f"&limit=100&offset={pg*100}")
        if not batch:
            break
        for m in batch:
            vol24 = float(m.get("volume24hr") or 0)
            vol   = float(m.get("volume") or m.get("volumeNum") or 0)
            if vol24 < MIN_VOL_24H:
                continue
            op = m.get("outcomePrices")
            if isinstance(op, str):
                try: op = json.loads(op)
                except: op = None
            if not op:
                continue
            yes = float(op[0])
            if not (0.001 <= yes <= 0.999):   # exclude fully resolved only
                continue
            out.append({"id": m.get("id"), "cid": m.get("conditionId"),
                        "question": m.get("question", ""),
                        "yes": yes, "vol24": vol24, "vol_total": vol})
    return out


# ── trade velocity ───────────────────────────────────────────────────────────

def trade_velocity(mkt):
    cid = mkt.get("cid")
    if not cid:
        return None
    trades = _get(f"{DATA_API}?market={cid}&limit={TRADES_FETCH}")
    if not trades:
        return None

    now = time.time()
    hour_ago = now - 3600

    buy_1h = sell_1h = vol_1h = 0.0
    buy_all = sell_all = 0.0

    for t in trades:
        try:
            size  = float(t.get("size", 0))
            price = float(t.get("price", 0))
            usd   = size * price
            side  = t.get("side", "").upper()
            ts    = int(t.get("timestamp", 0))
        except Exception:
            continue

        if side == "BUY":
            buy_all += usd
            if ts >= hour_ago:
                buy_1h += usd; vol_1h += usd
        elif side == "SELL":
            sell_all += usd
            if ts < hour_ago:
                pass
            else:
                sell_1h += usd; vol_1h += usd

    vol24_per_hr = mkt["vol24"] / 24.0
    velocity     = vol_1h / vol24_per_hr if vol24_per_hr > 0 else 1.0
    total_all    = buy_all + sell_all
    delta        = (buy_all - sell_all) / total_all if total_all > 0 else 0.0

    return {
        "velocity":    round(velocity, 2),
        "vol_1h":      round(vol_1h, 0),
        "vol24_avg_h": round(vol24_per_hr, 0),
        "delta":       round(delta, 3),        # + = buyers, - = sellers
        "signal":      round(velocity * abs(delta), 3),
        "buy_pct":     round(buy_all / total_all * 100 if total_all else 50, 1),
    }


# ── scan ─────────────────────────────────────────────────────────────────────

def scan():
    print("[orderflow] fetching markets...", flush=True)
    mkts = poly_markets()
    print(f"[orderflow] {len(mkts)} markets to probe", flush=True)

    results = []

    def probe(m):
        v = trade_velocity(m)
        if v is None:
            return None
        return {**m, **v}

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for r in ex.map(probe, mkts):
            if r:
                results.append(r)

    results.sort(key=lambda x: -x.get("velocity", 0))
    spikes = [r for r in results if r.get("velocity", 0) >= VEL_SPIKE]

    out = {"ts": int(time.time()), "markets": results,
           "spikes": spikes, "scanned": len(results)}
    tmp = CACHE + ".tmp"
    json.dump(out, open(tmp, "w"), indent=1)
    os.replace(tmp, CACHE)
    print(f"[orderflow] {len(spikes)} velocity spikes (>{VEL_SPIKE}x) out of {len(results)}")
    return out


# ── Obsidian export ───────────────────────────────────────────────────────────

def export_obsidian(data):
    ts     = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(data.get("ts", 0)))
    mkts   = data.get("markets", [])
    spikes = data.get("spikes", [])

    L = [
        "# Order Flow Probe — Trade Velocity + Buy/Sell Imbalance",
        "",
        f"> Updated {ts} · {data.get('scanned',0)} markets",
        "> **velocity_ratio** = last-1h volume ÷ (24h vol / 24).",
        "> >3x = unusual. >5x = someone knows something.",
        "> **delta** = (buys − sells) / total. +1 = all buyers pressing.",
        "",
    ]

    if spikes:
        L += [f"## Velocity Spikes (>{VEL_SPIKE}x)", "",
              "| Velocity | Delta | Buy% | 1h vol | Market | Poly YES |",
              "|---|---|---|---|---|---|"]
        for m in spikes[:15]:
            direction = "🟢 buyers" if m["delta"] > 0.15 else ("🔴 sellers" if m["delta"] < -0.15 else "⚪ mixed")
            L.append(f"| {m['velocity']}× | {m['delta']:+.2f} ({direction}) | "
                     f"{m['buy_pct']:.0f}% | ${m['vol_1h']:,.0f} | "
                     f"{m['question'][:48]} | {m['yes']:.0%} |")
        L.append("")
    else:
        L += [f"## Velocity Spikes", "_No spikes above threshold this hour_", ""]

    if mkts:
        L += ["## Top 10 by Signal (velocity × |delta|)", "",
              "| Signal | Vel | Delta | Market | Poly YES |",
              "|---|---|---|---|---|"]
        by_sig = sorted(mkts, key=lambda x: -x.get("signal", 0))
        for m in by_sig[:10]:
            L.append(f"| {m['signal']:.2f} | {m['velocity']}× | {m['delta']:+.2f} | "
                     f"{m['question'][:55]} | {m['yes']:.0%} |")
        L.append("")

    L += ["## How to use",
          "- **High velocity + strong delta** → informed one-sided flow. "
            "Buy in same direction IF analyst thesis exists.",
          "- **High velocity + neutral delta** → two-sided churn, not directional. "
            "Market may be near a news catalyst.",
          "- **Low velocity** → quiet market; news would move it more than usual."]

    os.makedirs(os.path.dirname(VAULT), exist_ok=True)
    open(VAULT, "w").write("\n".join(L) + "\n")
    print(f"[orderflow] Obsidian → {VAULT}")


_OSA = ('on run argv\ntell application "Messages"\n'
        '  set s to 1st service whose service type = iMessage\n'
        '  set b to buddy (item 1 of argv) of s\n'
        '  send (item 2 of argv) to b\nend tell\nend run')

def _imsg(body):
    import subprocess
    try:
        subprocess.run(["osascript", "-e", _OSA, "+918449447444", body],
                       capture_output=True, text=True, timeout=30)
    except Exception:
        pass


def _write_graphify_node(data):
    ts     = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(data.get("ts", 0)))
    spikes = data.get("spikes", [])
    mkts   = data.get("markets", [])
    L = [f"# Order Flow Probe Findings — {ts}", "",
         "Signal source: Polymarket CLOB trade API — compares last-1h trade volume "
         "to 24h average hourly rate per market to detect informed flow before price moves.", "",
         "## Velocity Spikes (last 1h vs 24h average)", ""]
    for m in spikes[:12]:
        direction = "buyer-driven" if m["delta"] > 0.15 else (
                    "seller-driven" if m["delta"] < -0.15 else "two-sided")
        L.append(f"- **{m['question']}** (Poly {m['yes']:.0%}): "
                 f"{m['velocity']}× velocity spike, {direction} flow "
                 f"(delta {m['delta']:+.2f}, {m['buy_pct']:.0f}% buyers, "
                 f"${m['vol_1h']:,.0f} in last hour)")
    if not spikes:
        L.append("No velocity spikes this hour.")
    L += ["", "## Top Markets by Signal Score", ""]
    by_sig = sorted(mkts, key=lambda x: -x.get("signal", 0))
    for m in by_sig[:8]:
        L.append(f"- **{m['question']}**: signal={m['signal']:.2f}, "
                 f"vel={m['velocity']}×, delta={m['delta']:+.2f}")
    content = "\n".join(L) + "\n"
    open(os.path.join(HERE, "orderflow_findings.md"), "w").write(content)
    vault = os.path.expanduser("~/Documents/PolymarketVault/Reports/orderflow_findings.md")
    os.makedirs(os.path.dirname(vault), exist_ok=True)
    open(vault, "w").write(content)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "once"
    if cmd == "report":
        if os.path.exists(CACHE):
            data = json.load(open(CACHE))
            export_obsidian(data)
            for m in data.get("markets", [])[:5]:
                print(f"  {m.get('velocity','?')}x vel | {m.get('delta',0):+.2f} delta | "
                      f"{m['question'][:55]}")
        else:
            print("No cache — run without args first")
        return
    data = scan()
    export_obsidian(data)
    _write_graphify_node(data)
    big = [m for m in data.get("spikes", []) if m.get("velocity", 0) >= ALERT_VEL]
    if big:
        lines = [f"{m['velocity']}× {m['delta']:+.2f}δ | {m['question'][:45]} "
                 f"(Poly {m['yes']:.0%})" for m in big[:3]]
        _imsg("🚀 ORDER VELOCITY SPIKE\n" + "\n".join(lines))


if __name__ == "__main__":
    main()
