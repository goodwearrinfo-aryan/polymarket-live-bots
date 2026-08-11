#!/usr/bin/env python3
"""
vet_whale.py — pm-whale-vetter as a standalone tool on the free LLM.
Vets a Polymarket wallet for COPYABILITY: genuinely sharp INFORMATION, or favorite-padding /
coinflip-HFT / dormant / just lucky? Python computes the real stats deterministically (P&L, WR,
recency, coinflip-share, 0.99-padding) from the data-api; the LLM classifies the archetype and
rules copyable yes/no. Encodes the project's lessons: RECENCY > P&L; coinflip-HFT is non-copyable
at lag; 0.99-padding inflates WR with ~no EV; no >88%-WR human exists (stat-sharp != copyable).
Zero Claude-Code cost. Run: python3 vet_whale.py --addr 0x... [--mock]
"""
import os, sys, json, time, datetime, argparse, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import whale_screen as ws  # reuse its data-api _get
import llm_client

BASE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(BASE, "vet_whale_log.jsonl")
VAULT = os.path.expanduser("~/Documents/PolymarketVault/Reports/whale_vet_latest.md")

SHAPE = ('{"archetype": "sharp-info|favorite-padder|coinflip-hft|dormant|lucky|mixed", '
         '"copyable": true|false, "confidence": 0-100, '
         '"why": "the specific reason, citing the stats", "caveat": "what would change the verdict"}')

SYSTEM = (
    "You vet a Polymarket wallet for COPYABILITY by a 60s-lagged paper copy-watcher. Hard lessons: "
    "(1) RECENCY > P&L — a $5M whale dormant 500 days is useless; (2) coinflip-HFT (Up/Down 10-min "
    "makers, ~100% WR uniform) are both-sides arb bots, NON-copyable at lag; (3) 0.99-entry favorite-"
    "padding inflates win-rate with ~zero EV (risk $1 to win $0.01) — high WR != edge; (4) no genuine "
    ">88%-WR human exists in the pool, so a sky-high WR screams padding or HFT, not skill; (5) the "
    "copyable archetype is an ACTIVE event-longshot whale with real edge and recent fills. Given the "
    "computed stats, classify the archetype and rule copyable true/false. Be skeptical; default to "
    "non-copyable unless it's clearly an active, real-edge info trader."
)


def gather(addr):
    pos = ws._get("positions", {"user": addr, "limit": 500, "sizeThreshold": 0}) or []
    closed = [p for p in pos if p.get("redeemable") or p.get("curPrice", 0.5) in (0.0, 1.0)]
    pnl = sum(p.get("realizedPnl", 0) for p in pos)
    wagered = sum(p.get("totalBought", 0) for p in pos)
    wins = sum(1 for p in closed if p.get("realizedPnl", 0) > 0)
    # favorite-padding: positions entered at a high price (avgPrice >= 0.95)
    priced = [p for p in pos if isinstance(p.get("avgPrice"), (int, float))]
    padding = sum(1 for p in priced if p.get("avgPrice", 0) >= 0.95)
    trades = ws._get("trades", {"user": addr, "limit": 100}) or []
    coinflip = sum(1 for t in trades if "up or down" in (t.get("title") or "").lower())
    last_days = round((time.time() - max((int(t["timestamp"]) for t in trades), default=0)) / 86400, 1) if trades else None
    titles = collections.Counter((t.get("title") or "")[:48] for t in trades)
    return {
        "addr": addr, "n_positions": len(pos), "closed": len(closed),
        "realized_pnl": round(pnl, 2), "wagered": round(wagered, 2),
        "avg_bet": round(wagered / len(pos), 2) if pos else 0,
        "win_rate": round(100 * wins / len(closed), 1) if closed else None,
        "favorite_padding_share": round(padding / len(priced), 2) if priced else None,
        "coinflip_share": round(coinflip / len(trades), 2) if trades else None,
        "last_trade_days": last_days, "recent_trades": len(trades),
        "top_markets": [t for t, _ in titles.most_common(4)],
    }


def vet(addr, mock=False):
    s = gather(addr)
    user = "WALLET STATS (computed from data-api):\n" + json.dumps(s, indent=1)
    if mock:
        verdict = {"archetype": "mixed", "copyable": False, "confidence": 70,
                   "why": "[mock] pipeline test", "caveat": "[mock]"}
    else:
        try:
            verdict = llm_client.judge(system=SYSTEM, user=user, schema_hint=SHAPE)
        except Exception as e:
            verdict = {"error": str(e), "copyable": False}
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    r = {"ts": ts, "addr": addr, "stats": s, "verdict": verdict, "mock": mock}
    _persist(r)
    return r


def _persist(r):
    try:
        open(LOG, "a").write(json.dumps({**r, **llm_client.provenance()}) + "\n")
    except Exception as e:
        print(f"[log FAIL] {e}", file=sys.stderr)
    s, v = r["stats"], r["verdict"]
    try:
        lines = [f"# Whale vet — {r['addr'][:10]}…", "",
                 f"_{r['ts']}_" + ("  _(mock)_" if r["mock"] else ""), "",
                 f"**{v.get('archetype','?')} — copyable: {v.get('copyable')} @{v.get('confidence','?')}**", "",
                 f"- P&L ${s['realized_pnl']:,} | WR {s['win_rate']}% | avg bet ${s['avg_bet']} | "
                 f"closed {s['closed']}",
                 f"- recency {s['last_trade_days']}d | coinflip {s['coinflip_share']} | "
                 f"padding {s['favorite_padding_share']}",
                 f"- top: {', '.join(s['top_markets'][:3])}", "",
                 f"**Why:** {v.get('why','')}", "", f"**Caveat:** {v.get('caveat','')}"]
        os.makedirs(os.path.dirname(VAULT), exist_ok=True)
        open(VAULT, "w").write("\n".join(lines) + "\n")
    except Exception as e:
        print(f"[vault FAIL] {e}", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--addr", required=True); ap.add_argument("--mock", action="store_true")
    a = ap.parse_args()
    if not a.mock and not llm_client.configured():
        print("⚠️ No LLM endpoint (set LLM_* in .env) — or use --mock."); sys.exit(2)
    r = vet(a.addr, mock=a.mock)
    s, v = r["stats"], r["verdict"]
    print(f"\nWHALE VET {a.addr[:12]}…  [{'mock' if r['mock'] else 'live'}]")
    print(f"  stats: P&L ${s['realized_pnl']:,} | WR {s['win_rate']}% | recency {s['last_trade_days']}d | "
          f"coinflip {s['coinflip_share']} | padding {s['favorite_padding_share']}")
    print(f"  → {v.get('archetype','?')} | copyable={v.get('copyable')} @{v.get('confidence','?')}")
    print(f"  why: {str(v.get('why',''))[:110]}")


if __name__ == "__main__":
    main()
