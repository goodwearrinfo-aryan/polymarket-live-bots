#!/usr/bin/env python3
"""signal_verifier.py — watch the paper bot's decided-open trades, VERIFY them
against historical edge, and emit only the survivors as actionable signals for
a HUMAN to place manually. PAPER project; this does NOT place any order.

Signal source : trade_log.json["open"]  (a new entry = the bot opened that trade)
Verification  : a trade passes ONLY if its feature bucket has a real, positive
                historical win rate. The whole system is ~33% net; this gate is
                what isolates the few combos that actually win.

Outputs:
  verified_signals.jsonl   — append-only log of everything that passed (audit)
  rejected_signals.jsonl   — append-only log of what was filtered + why
  latest_signal.txt        — the most recent PLACE block (phone-glanceable)
  .signal_seen.json        — dedupe state (token_id|opened_at already processed)
  stdout                   — clean "PLACE THIS" blocks for the watchdog log
  email (optional)         — if .smtp_creds.json has a real App Password, sends.

Run once per watchdog cycle:  python3 signal_verifier.py
"""
import os, sys, json, time, subprocess, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
TRADE_LOG = os.path.join(HERE, "trade_log.json")
BRAIN     = os.path.join(HERE, "brain.json")
SEEN      = os.path.join(HERE, ".signal_seen.json")
VERIFIED  = os.path.join(HERE, "verified_signals.jsonl")
REJECTED  = os.path.join(HERE, "rejected_signals.jsonl")
LATEST    = os.path.join(HERE, "latest_signal.txt")
RUN_LOG   = os.path.join(HERE, "run_output.txt")

# ── verification gate: DOLLARS, not win rate ─────────────────────────────────
# Hard-won lesson from this bot's own data (2026-06-07): win rate is a trap.
# consensus|nearCertain|deep wins 53% and still loses -$6.44/trade. So the gate
# is realized $/trade for the trade's feature bucket, computed from closed trades.
# A signal is forwarded ONLY if its bucket has made money over a real sample.
COMBO_MIN_N    = 10      # need this many CLOSED trades in the bucket to trust it
COMBO_MIN_PPT  = 0.05    # ... and >= +$0.05 realized per trade to forward
DEAD_STRATS    = {"midline"}    # 0/41 historically — never forward
DEAD_PRICE     = {"longshot"}   # 0/76 historically — never forward
MIN_CONF       = 0.0     # confidence is NOISE here (winners 0.381 vs losers 0.380); off
EMAIL_SIGNALS  = True    # call send_mail.py when a signal passes (safe no-op w/o creds)


def _vol_bucket(v):
    if v < 5_000:   return "micro"
    if v < 20_000:  return "thin"
    if v < 100_000: return "medium"
    return "deep"

def _price_bucket(p):
    if p < 0.15: return "longshot"
    if p < 0.40: return "low"
    if p < 0.60: return "midrange"
    if p < 0.80: return "high"
    return "nearCertain"


def combo_dollar_table(closed):
    """Realized $/trade per 'strategy|price|vol' bucket from CLOSED trades."""
    agg = {}
    for t in closed:
        k = (f"{t.get('strategy')}|{_price_bucket(float(t.get('entry_price',0) or 0))}"
             f"|{_vol_bucket(float(t.get('volume',0) or 0))}")
        n, w, p = agg.get(k, (0, 0, 0.0))
        agg[k] = (n + 1, w + (1 if t.get('pnl_usdc', 0) > 0 else 0),
                  p + float(t.get('pnl_usdc', 0) or 0))
    return agg  # k -> (n, wins, total_pnl)


def insider_paused():
    """Detect the insider circuit breaker from the tail of the run log."""
    try:
        with open(RUN_LOG, "rb") as f:
            f.seek(max(0, os.path.getsize(RUN_LOG) - 4000))
            tail = f.read().decode("utf-8", "ignore")
        return "Insider circuit breaker active" in tail or "Insider status: ⛔" in tail
    except Exception:
        return False


def load_json(path, default):
    try:
        return json.load(open(path))
    except Exception:
        return default


def verify(pos, dollar_tbl, insider_off):
    """Return (passed: bool, reason: str, detail: dict). Gate = realized $/trade."""
    strat = pos.get("strategy", "")
    price = float(pos.get("entry_price", 0) or 0)
    vol   = float(pos.get("volume", 0) or 0)
    pb, vb = _price_bucket(price), _vol_bucket(vol)
    combo  = f"{strat}|{pb}|{vb}"

    if strat in DEAD_STRATS:
        return False, f"dead strategy ({strat})", {"combo": combo}
    if pb in DEAD_PRICE:
        return False, f"dead price zone ({pb})", {"combo": combo}
    if strat == "insider" and insider_off:
        return False, "insider circuit breaker active", {"combo": combo}

    n, w, p = dollar_tbl.get(combo, (0, 0, 0.0))
    ppt = (p / n) if n else 0.0
    if n < COMBO_MIN_N:
        return False, f"{combo}: thin sample (n={n}) — not enough closed trades to trust", \
               {"combo": combo, "n": n, "ppt": ppt}
    if ppt >= COMBO_MIN_PPT:
        return True, f"{combo}: +${ppt:.2f}/trade over n={n} (wr {w/n:.0%})", \
               {"combo": combo, "n": n, "ppt": ppt, "wr": w / n}
    return False, f"{combo}: loses ${ppt:.2f}/trade over n={n} — not +EV", \
           {"combo": combo, "n": n, "ppt": ppt}


def place_block(pos, detail):
    side = pos.get("side", "?")
    return (
        "──────────── ✅ VERIFIED SIGNAL — PLACE MANUALLY ────────────\n"
        f"  {side}  @ {float(pos.get('entry_price',0)):.3f}\n"
        f"  Market : {pos.get('title','?')}\n"
        f"  Strategy: {pos.get('strategy','?')}   conf {float(pos.get('confidence',0)):.2f}\n"
        f"  Edge   : {detail.get('_reason','')}\n"
        f"  Vol ${float(pos.get('volume',0)):,.0f}   traders {pos.get('trader_count','?')}   "
        f"closes in {pos.get('days_to_close','?')}d\n"
        f"  condition_id: {pos.get('condition_id','?')}\n"
        "  PAPER PROJECT — verify the live book before risking real money.\n"
        "─────────────────────────────────────────────────────────────"
    )


def main():
    data  = load_json(TRADE_LOG, {"open": [], "closed": []})
    dollar_tbl = combo_dollar_table(data.get("closed", []))
    seen  = set(load_json(SEEN, []))
    insider_off = insider_paused()

    new_passed, new_rejected = [], []
    for pos in data.get("open", []):
        key = f"{pos.get('token_id')}|{pos.get('opened_at')}"
        if key in seen:
            continue
        seen.add(key)
        ok, reason, detail = verify(pos, dollar_tbl, insider_off)
        rec = {
            "ts": datetime.datetime.now().isoformat(timespec="seconds"),
            "title": pos.get("title"), "side": pos.get("side"),
            "entry_price": pos.get("entry_price"), "strategy": pos.get("strategy"),
            "confidence": pos.get("confidence"), "reason": reason,
            "combo": detail.get("combo"), "condition_id": pos.get("condition_id"),
        }
        if ok:
            detail["_reason"] = reason
            new_passed.append((pos, detail, rec))
        else:
            new_rejected.append(rec)

    # persist
    json.dump(sorted(seen), open(SEEN, "w"))
    with open(REJECTED, "a") as f:
        for r in new_rejected:
            f.write(json.dumps(r) + "\n")
    with open(VERIFIED, "a") as f:
        for _, _, r in new_passed:
            f.write(json.dumps(r) + "\n")

    if not new_passed:
        print(f"[signal_verifier] no new verified signals "
              f"({len(new_rejected)} new opens filtered, insider_off={insider_off})")
        return 0

    blocks = []
    for pos, detail, _ in new_passed:
        b = place_block(pos, detail)
        blocks.append(b)
        print(b)
    open(LATEST, "w").write("\n\n".join(blocks) +
                            f"\n\ngenerated {datetime.datetime.now():%Y-%m-%d %H:%M}\n")

    if EMAIL_SIGNALS:
        subj = f"🟢 {len(new_passed)} verified Polymarket signal(s) — {datetime.datetime.now():%H:%M}"
        try:
            subprocess.run(["python3", os.path.join(HERE, "send_mail.py"), LATEST, subj],
                           capture_output=True, text=True, timeout=60)
        except Exception as e:
            print(f"[signal_verifier] email step failed (non-fatal): {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
