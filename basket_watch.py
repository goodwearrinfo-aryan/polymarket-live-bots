#!/usr/bin/env python3
"""
basket_watch.py — real-time basket-arb lock watcher + alerter (2026-06-15).

Polls for VERIFIED locked baskets (basket_arb.get_locked_baskets) and the instant
a NEW lock appears (or a known one's edge jumps materially) it:
  - ALERTS you (WhatsApp + iMessage via wa_alert.notify) — "tell us"
  - records it to the brain (Obsidian) and an event log

PAPER / DETECTION ONLY. It never places an order or moves money (hard rule +
the paper-guard hook). It tells you where the locked edge is the moment it
shows; deciding to put real money in — and eating the execution risk — is your
manual call.

HONEST REALITY CHECK (built into the design, not hidden):
  - REST polling has latency. True same-second / first-mover capture needs the
    Polymarket CLOB websocket feed; a 60s poller catches a new lock within ~a
    minute, not "the same second."
  - Genuinely-free locks are taken by faster (HFT) arb bots. What survives to a
    retail poller usually carries friction: thin depth, many legs to fill at
    once, or capital locked until a far-off resolution. Watch it; don't expect
    a money printer.

  python3 basket_watch.py     # one scan (launchd/cron); first run seeds silently
"""
import json, os, time
from datetime import datetime, timezone
from pathlib import Path

import basket_arb
try:
    import wa_alert
except Exception:
    wa_alert = None

BASE = Path(__file__).resolve().parent
STATE = BASE / "basket_watch_state.json"
LOG = BASE / "basket_locks.jsonl"
BRAIN = Path(os.path.expanduser("~/Documents/PolymarketVault")) / "brain" / "Resources"

ALERT_EDGE = 0.02      # only alert on locks >= 2%
REALERT_JUMP = 0.02    # re-alert a known lock if its edge climbs this much
EXPIRE_SEC = 7200      # a lock unseen for 2h is treated as fresh if it returns


def load_state():
    if STATE.exists():
        try:
            return json.loads(STATE.read_text())
        except Exception:
            pass
    return {}


def alert(text):
    if wa_alert:
        try:
            wa_alert.notify(text)
            return True
        except Exception as e:
            print(f"  alert failed: {e}")
    print("  [alert unavailable] " + text)
    return False


def write_brain(locks, ts):
    BRAIN.mkdir(parents=True, exist_ok=True)
    md = ["---", "type: basket-locks", "tags: [basket-arb, live]",
          f"updated: {ts}", "---", "",
          "# Basket-arb locks (live)", "",
          "Buy ALL outcomes when Σask < $1 → resolution pays $1 → lock the gap. "
          "Paper / detection only; depth + completeness + capital-lock are your risk.", "",
          "| edge | kind | #out | Σask | event |", "|---:|---|---:|---:|---|"]
    for b in locks:
        md.append(f"| {b['edge']:+.3f} | {b['kind']} | {b['n_outcomes']} | "
                  f"{b['sum_ask']:.3f} | {b['title'][:50]} |")
    if not locks:
        md.append("| — | — | — | — | none right now |")
    (BRAIN / "basket-locks.md").write_text("\n".join(md))


def main():
    ts_iso = datetime.now(timezone.utc).isoformat()
    now = time.time()
    locks = basket_arb.get_locked_baskets(0.01)
    state = load_state()
    first_run = not state

    new, jumped = [], []
    for b in locks:
        slug, e = b["slug"], b["edge"]
        prev = state.get(slug)
        fresh = prev is None or (now - prev.get("seen", 0)) > EXPIRE_SEC
        if not first_run:
            if fresh and e >= ALERT_EDGE:
                new.append(b)
            elif prev and (e - prev.get("edge", 0)) >= REALERT_JUMP:
                jumped.append(b)
        state[slug] = {"edge": e, "seen": now}

    # age out stale slugs so a genuine reappearance re-alerts
    state = {k: v for k, v in state.items() if (now - v.get("seen", 0)) <= EXPIRE_SEC}
    STATE.write_text(json.dumps(state, indent=2))
    write_brain(locks, ts_iso)

    # event log (only events, not every lock every tick)
    for b in new + jumped:
        with open(LOG, "a") as f:
            f.write(json.dumps({"ts": ts_iso, "event": "new" if b in new else "jump",
                                "slug": b["slug"], "edge": b["edge"], "kind": b["kind"],
                                "sum_ask": b["sum_ask"], "n": b["n_outcomes"]}) + "\n")

    for b in new + jumped:
        tag = "NEW" if b in new else "EDGE↑"
        alert(f"🔒 Basket lock {tag}: {b['edge']*100:.1f}% — {b['title'][:60]} "
              f"(buy {b['n_outcomes']} legs @ Σask {b['sum_ask']:.3f}). "
              f"Paper/detection only — depth + execution your call.")

    if first_run:
        print(f"[basket_watch] {ts_iso} | seeded {len(locks)} existing locks (no alert on first run)")
    else:
        best = locks[0]["edge"] if locks else 0.0
        print(f"[basket_watch] {ts_iso} | {len(locks)} locks | {len(new)} new, "
              f"{len(jumped)} jumped | best {best:+.3f}")


if __name__ == "__main__":
    main()
