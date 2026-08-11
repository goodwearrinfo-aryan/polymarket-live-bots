#!/usr/bin/env python3
"""
edge_trader.py — THE house strategy (Claude's pick), live paper.

Trades the ML model's DIVERGENCE from the market price — the exact thing that
backtested at +$0.116/trade, 69% win, out-of-sample (see ml/backtest_edge.py).
Reuses ml/predict.py for inference (the model now predicts P(YES)).

Logic each cycle:
  • Scan liquid markets resolving soon (so we actually see outcomes).
  • For each, model_p = score_market(cid); market_p = current YES price.
  • If |model_p - market_p| >= EDGE_MIN and price in band:
        buy YES if model says YES underpriced, else buy NO. Pay the ASK (taker, honest).
  • Hold to resolution (price -> ~0/1) or a max-hold cap. One position per market.

This is also the OUT-OF-TIME confirmation: it trades markets resolving AFTER
training, so if it stays +EV live, the edge is real. Paper only, no keys.

Usage: python3 edge_trader.py once | run | board
"""
import os, sys, json, time, urllib.parse, urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "ml"))
from predict import score_market          # reuse proven live inference
try:
    from gate_wrapper import gate_edge as _brain_gate
except ImportError:
    _brain_gate = None  # graceful fallback

GAMMA = "https://gamma-api.polymarket.com/markets"
STATE = os.path.join(HERE, "edge_trader_state.json")
UA = "edge-trader-paper/1.0"

KILL_FILE = os.path.expanduser("~/.edge_trader_KILL")

def check_model_edge():
    """Verify model_meta.json has edge CI excluding 0. Return (ok, reason)."""
    meta_path = os.path.join(HERE, "ml", "model_meta.json")
    if not os.path.exists(meta_path):
        return False, "no model_meta.json"
    try:
        with open(meta_path) as f:
            meta = json.load(f)
        direction = meta.get("direction", {})
        ci = direction.get("edge_ci95", [None, None])
        lo = ci[0] if ci and len(ci) > 0 else None
        hi = ci[1] if ci and len(ci) > 1 else None
        if lo is None or hi is None or lo != lo or hi != hi:  # NaN check
            return False, "edge CI unavailable"
        if lo <= 0 <= hi:
            return False, f"edge CI includes 0 [{lo:.4f}, {hi:.4f}]"
        if lo <= 0:
            return False, f"edge CI not positive [{lo:.4f}, {hi:.4f}]"
        return True, f"edge CI [{lo:.4f}, {hi:.4f}] excludes 0"
    except Exception as e:
        return False, f"model check error: {e}"

# Verify model has edge at startup (DISABLED per user request — running despite CI including 0)
# _ok, _reason = check_model_edge()
# if not _ok:
#     print(f"[edge_trader] MODEL GATE FAILED: {_reason} - creating kill switch")
#     open(KILL_FILE, "w").close()
#     sys.exit(0)
# else:
#     print(f"[edge_trader] MODEL GATE PASSED: {_reason}")
print("[edge_trader] MODEL GATE DISABLED — running despite CI including 0")

CONFIG = {
    "poll_sec":        120,
    "edge_min":        0.10,    # model must disagree with price by >= this
    "spread":          0.02,    # pay half the spread on entry (taker)
    "band":            (0.05, 0.95),
    "min_volume":      50_000,
    "max_resolve_days": 30,     # markets resolving within a month (so outcomes land in-test)
    "max_open":        5,       # conservative position cap during test
    "bet_usdc":        0.5,     # smaller bets during test
    "max_hold_days":   30,
    "scan_markets":    40,      # cap candidates (each needs a price-history fetch)
    "scan_pages":      4,       # paginate Gamma to find near-dated liquid markets
}


def http_get(url, params=None):
    if params:
        url += "?" + urllib.parse.urlencode(params)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception as e:
        sys.stderr.write(f"[http] {e}\n"); return None


def now_iso(): return datetime.now(timezone.utc).isoformat()


def days_to(end):
    if not end: return None
    try:
        t = datetime.fromisoformat(str(end).replace("Z", "+00:00"))
        return (t - datetime.now(timezone.utc)).total_seconds() / 86400
    except Exception:
        return None


def days_since(iso):
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(iso)).total_seconds() / 86400
    except Exception:
        return 0.0


def fetch_candidates(cfg):
    out, seen = [], set()
    for page in range(cfg.get("scan_pages", 4)):
        raw = http_get(GAMMA, {"closed": "false", "active": "true",
                               "order": "volumeNum", "ascending": "false",
                               "limit": 100, "offset": page * 100}) or []
        if not raw:
            break
        for m in raw:
            try:
                cid = str(m.get("conditionId") or m.get("id"))
                if cid in seen:
                    continue
                prices = m.get("outcomePrices")
                if isinstance(prices, str): prices = json.loads(prices)
                if not prices: continue
                yes = float(prices[0])
                vol = float(m.get("volumeNum") or m.get("volume") or 0)
                d = days_to(m.get("endDate"))
                if vol < cfg["min_volume"]: continue
                if not (cfg["band"][0] <= yes <= cfg["band"][1]): continue
                if d is None or d > cfg["max_resolve_days"] or d < 0.25: continue
                seen.add(cid)
                out.append({"id": cid, "q": (m.get("question") or "")[:80], "yes": yes, "vol": vol})
            except Exception:
                continue
            if len(out) >= cfg["scan_markets"]:
                return out
    return out


def load_state():
    if os.path.exists(STATE):
        try: return json.load(open(STATE))
        except Exception: pass
    return {"open": [], "closed": []}


def save_state(s): json.dump(s, open(STATE, "w"), indent=2)


def one_scan(cfg, verbose=True):
    # Kill switch: check ~/.edge_trader_KILL
    import os
    if os.path.exists(os.path.expanduser("~/.edge_trader_KILL")):
        if verbose: print("[edge_trader] KILL switch active - exiting")
        sys.exit(0)
    # Auto-disable if total P&L < -$5
    st = load_state()
    total_pnl = sum(r.get("pnl_usdc", 0) for r in st["closed"])
    if total_pnl <= -5.0:
        if verbose: print(f"[edge_trader] P&L {total_pnl:.2f} <= -5.0 - auto-disabling")
        # Create kill switch to stop watchdog from relaunching
        open(os.path.expanduser("~/.edge_trader_KILL"), "w").close()
        sys.exit(0)

    mkts = fetch_candidates(cfg)
    px = {m["id"]: m["yes"] for m in mkts}
    held = {p["id"] for p in st["open"]}

    # ── exits: mark to current price; close on resolution(extreme) or hold cap ──
    keep = []
    for p in st["open"]:
        yes = px.get(p["id"])
        if yes is None:
            if days_since(p["opened_at"]) > cfg["max_hold_days"]:
                _close(st, p, p["entry_fill"], "stale", cfg)
            else:
                keep.append(p)
            continue
        cur = (yes if p["side"] == "YES" else 1 - yes)          # value of our outcome
        exit_px = max(0.001, cur - cfg["spread"] / 2)
        resolved = yes <= 0.02 or yes >= 0.98
        if resolved or days_since(p["opened_at"]) > cfg["max_hold_days"]:
            _close(st, p, exit_px, "resolved" if resolved else "time", cfg)
        else:
            keep.append(p)
    st["open"] = keep

    # ── entries: trade where model diverges from price ──
    opened = 0
    for m in mkts:
        if len(st["open"]) >= cfg["max_open"]: break
        if m["id"] in held: continue
        model_p = score_market(m["id"])           # P(YES) from the trained model
        if model_p is None: continue
        div = model_p - m["yes"]
        if abs(div) < cfg["edge_min"]: continue
        side = "YES" if div > 0 else "NO"
        outcome_price = m["yes"] if side == "YES" else 1 - m["yes"]
        entry = min(0.999, outcome_price + cfg["spread"] / 2)
        size = round(cfg["bet_usdc"] / entry, 4) if entry > 0 else 0
        if size <= 0: continue

        # BRAIN gatekeeper check before booking (ML divergence edge type)
        brain_gate_pass = None
        if _brain_gate is not None:
            try:
                allow, verdict = _brain_gate(
                    edge_type="diverg",  # ML divergence edge
                    description=m["q"][:100],
                    mode="soft"  # log but don't block for live testing
                )
                brain_gate_pass = allow
            except Exception:
                brain_gate_pass = None  # treat errors as "unjudged"

        st["open"].append({
            "id": m["id"], "q": m["q"], "side": side,
            "model_p": round(model_p, 4), "mkt_p": round(m["yes"], 4),
            "entry_fill": round(entry, 4), "size": size,
            "opened_at": now_iso(),
            "brain_gate_pass": brain_gate_pass,  # stamp verdict for audit
        })
        held.add(m["id"]); opened += 1
    save_state(st)
    if verbose:
        print(f"[{now_iso()[11:19]}] candidates={len(mkts)} opened={opened} "
              f"open={len(st['open'])} closed={len(st['closed'])}")
    return st


def _close(st, p, exit_fill, reason, cfg):
    pnl = round((exit_fill - p["entry_fill"]) * p["size"], 4)
    rec = dict(p); rec.update({"exit_fill": round(exit_fill, 4), "reason": reason,
                               "pnl_usdc": pnl, "closed_at": now_iso()})
    st["closed"].append(rec)


def board(st):
    c = st["closed"]; n = len(c); w = sum(1 for r in c if r["pnl_usdc"] > 0)
    pnl = sum(r["pnl_usdc"] for r in c)
    print("=" * 54)
    print("  EDGE TRADER — ML divergence (Claude's pick, paper)")
    print("=" * 54)
    print(f"  open={len(st['open'])}  closed={n}  win={ (w/n*100 if n else 0):.0f}%  "
          f"realized=${pnl:+.2f}  avg/trade=${pnl/n if n else 0:+.4f}")
    if n and n < 20:
        print(f"  ({n} closed — need ~20-30 before trusting. This IS the out-of-time test.)")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "once"
    if cmd == "board": board(load_state()); return
    if cmd == "once": one_scan(CONFIG); board(load_state()); return
    if cmd == "run":
        print("edge_trader running (paper). Ctrl-C to stop.")
        while True:
            try: one_scan(CONFIG)
            except Exception as e: sys.stderr.write(f"[loop] {e}\n")
            time.sleep(CONFIG["poll_sec"])
    print(__doc__)


if __name__ == "__main__":
    main()
