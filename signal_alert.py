#!/usr/bin/env python3
"""
signal_alert.py  —  iMessage alerts for VALIDATED high-conviction Polymarket signals.

Validation pipeline (must pass ALL gates):
  1. ≥5 elite wallets agree (not just 4)
  2. Wallet quality: avg_rank_pct ≥ 0.55 (mid-tier wallets; top-tier ≤0.20 was WORST bucket)
  3. Real money: cost_usdc ≥ $50 per position (not a $1 probe bet)
  4. Price drift: market moved ≥5¢ AND ≤35¢ our way since entry
     — too little = unconfirmed; too much = manipulation pump (coordinated wallets
       push price hard to create a fake signal, retail follows, then they flip)
  5. Cheap entry: buy price ≤ $0.55 (max upside remaining)
  6. Payout ≥ 80% upside
  7. Short-horizon: resolves within 14 days
  8. Liquid: ≥$50k volume (thin markets are easy to manipulate)
  9. Not a thin geopolitical market: volume ≥ $200k if it's a news/geopolitical market
     (Iran, Israel, airspace, war, sanctions, Hormuz — huge drift on thin volume = manipulation)

Run by watchdog every 5 min. Seen-set prevents duplicates.
"""
import os, sys, json, time, subprocess, requests
from datetime import datetime

IMESSAGE_TARGET = "+918449447444"
PRIM_LOG = "/Users/aryanagarwal/Documents/polymarket/trade_log.json"

# ── Validation gates ─────────────────────────────────────────────────────────
MIN_TRADERS      = 5      # ≥5 elite wallets
MIN_WALLET_RANK  = 0.55   # avg_rank_pct ≥ 0.55 (mid-tier wallets; top-tier ≤0.20 was WORST bucket)
MIN_COST_USDC    = 50.0   # real money — not a probe bet
MIN_PRICE_DRIFT  = 0.05   # ≥5¢ confirmed move our way
MAX_PRICE_DRIFT  = 0.35   # ≤35¢ — drift >35¢ = coordinated pump/manipulation, not organic signal
MAX_BUY_AT       = 0.65   # matches bot's max_price — alert range aligned with entry range
MIN_PAYOUT       = 80     # ≥80% upside
MAX_DAYS_TO_CLOSE = 14    # only short-horizon markets — resolves within 2 weeks
MIN_VOLUME        = 50_000 # ≥$50k market volume — liquid markets only (illiquid = manipulable)
MIN_VOLUME_GEO    = 200_000 # geopolitical/news markets need ≥$200k — thin geo = easy to rig

# Keywords that flag a market as geopolitical/news (prone to thin-market manipulation)
GEO_KEYWORDS = ["iran", "israel", "airspace", "hormuz", "war", "sanction", "missile",
                 "nuclear", "ceasefire", "conflict", "attack", "military", "airstrike"]

SEEN_FILE = "/tmp/polymarket_alerted.json"
GAMMA_URL = "https://gamma-api.polymarket.com/markets?closed=false&limit=1&clob_token_ids="


def load_seen():
    try:
        return set(json.load(open(SEEN_FILE)))
    except Exception:
        return set()


def save_seen(seen):
    with open(SEEN_FILE, "w") as f:
        json.dump(list(seen), f)


def load_opens():
    try:
        return json.load(open(PRIM_LOG)).get("open", [])
    except Exception:
        return []


def fetch_current_mid(token_id: str) -> float | None:
    """Hit Gamma API to get the live market mid price."""
    try:
        r = requests.get(GAMMA_URL + token_id, timeout=5)
        markets = r.json()
        if markets:
            m = markets[0]
            yes = float(m.get("bestBid", 0) or m.get("lastTradePrice", 0) or 0)
            return yes if yes > 0 else None
    except Exception:
        return None


def enrich(pos):
    """Add derived fields needed for validation and display."""
    entry  = pos.get("entry_price", 0.5)
    side   = pos.get("side", "BUY")

    if side == "SELL":
        # Bot sold YES = we're betting NO. Buy NO at (1 - entry).
        buy_at     = round(1.0 - entry, 3)
        payout_pct = (entry / buy_at * 100) if buy_at > 0 else 0
        play       = "NO"
        # Price drift: we win if YES falls → drift = entry - current_yes
        pos["_drift_direction"] = "down"
    else:
        buy_at     = round(entry, 3)
        payout_pct = ((1.0 - entry) / entry * 100) if entry > 0 else 0
        play       = "YES"
        pos["_drift_direction"] = "up"

    traders = pos.get("trader_count", pos.get("signals", 1))
    conf    = pos.get("confidence", 0.5)

    pos["_play"]       = play
    pos["_buy_at"]     = buy_at
    pos["_payout_pct"] = payout_pct
    pos["_score"]      = payout_pct * traders * conf / 100
    pos["_entry_yes"]  = entry
    return pos


def validate(pos) -> tuple[bool, list[str]]:
    """
    Run all validation gates. Returns (passed, reasons_failed).
    A trade must pass every gate to alert.
    """
    fails = []

    # Gate 1: enough wallets
    traders = pos.get("trader_count", pos.get("signals", 1))
    if traders < MIN_TRADERS:
        fails.append(f"only {traders} wallets (need ≥{MIN_TRADERS})")

    # Gate 2: wallet quality — not just any traders, top-ranked ones
    rank = pos.get("avg_rank_pct", 0)
    if rank < MIN_WALLET_RANK:
        fails.append(f"wallet rank {rank:.2f} (need ≥{MIN_WALLET_RANK})")

    # Gate 3: real money — not a probe bet
    cost = pos.get("cost_usdc", 0)
    if cost < MIN_COST_USDC:
        fails.append(f"only ${cost:.0f} behind signal (need ≥${MIN_COST_USDC:.0f})")

    # Gate 4: price confirmation — market moving our way
    token_id = pos.get("token_id", "")
    current_yes = fetch_current_mid(token_id) if token_id else None
    entry_yes   = pos.get("_entry_yes", 0.5)

    if current_yes is not None:
        if pos["_drift_direction"] == "down":
            drift = entry_yes - current_yes   # positive = YES fell = good for NO
        else:
            drift = current_yes - entry_yes   # positive = YES rose = good for YES

        pos["_drift"] = round(drift, 3)
        pos["_current_yes"] = current_yes

        if drift < MIN_PRICE_DRIFT:
            fails.append(f"price only moved {drift:+.3f} (need ≥{MIN_PRICE_DRIFT} our way)")
        elif drift > MAX_PRICE_DRIFT:
            fails.append(f"drift {drift:+.3f} too large — looks like manipulation pump (max {MAX_PRICE_DRIFT})")
    else:
        # Can't fetch live price — skip price gate, note it
        pos["_drift"] = None
        pos["_current_yes"] = None

    # Gate 5: cheap entry
    if pos["_buy_at"] > MAX_BUY_AT:
        fails.append(f"entry ${pos['_buy_at']:.3f} too expensive (max ${MAX_BUY_AT})")

    # Gate 6: payout upside
    if pos["_payout_pct"] < MIN_PAYOUT:
        fails.append(f"only {pos['_payout_pct']:.0f}% upside (need ≥{MIN_PAYOUT}%)")

    # Gate 7: short-horizon — only markets resolving within 14 days
    days = pos.get("days_to_close")
    if days is not None and days > MAX_DAYS_TO_CLOSE:
        fails.append(f"resolves in {days:.0f}d (max {MAX_DAYS_TO_CLOSE}d — long-horizon = more uncertainty)")

    # Gate 8: liquid market — volume ≥ $50k (illiquid markets are manipulable)
    vol = pos.get("volume", 0)
    if vol < MIN_VOLUME:
        fails.append(f"volume ${vol:,.0f} too thin (need ≥${MIN_VOLUME:,})")

    # Gate 9: geopolitical markets need much higher volume — thin geo = coordinated wallet pump
    title_lower = pos.get("title", "").lower()
    is_geo = any(kw in title_lower for kw in GEO_KEYWORDS)
    if is_geo and vol < MIN_VOLUME_GEO:
        fails.append(f"geo/news market with only ${vol:,.0f} volume — easy to rig (need ≥${MIN_VOLUME_GEO:,})")

    return len(fails) == 0, fails


def send_imessage(text: str) -> tuple[bool, str]:
    safe = text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    script = (
        'tell application "Messages"\n'
        '  set targetService to id of first service whose service type = iMessage\n'
        f'  set targetBuddy to participant "{IMESSAGE_TARGET}" of service id targetService\n'
        f'  send "{safe}" to targetBuddy\n'
        'end tell'
    )
    r = subprocess.run(["osascript", "-e", script],
                       capture_output=True, text=True, timeout=15)
    return r.returncode == 0, r.stderr.strip()


def fmt_alert(pos, rank) -> str:
    play    = pos["_play"]
    buy_at  = pos["_buy_at"]
    payout  = pos["_payout_pct"]
    traders = pos.get("trader_count", pos.get("signals", "?"))
    title   = pos.get("title", "")
    cost    = pos.get("cost_usdc", 0)
    drift   = pos.get("_drift")
    cur_yes = pos.get("_current_yes")
    wallets = len(pos.get("wallets", []))

    drift_str = f"{drift:+.3f} ✅" if drift is not None else "unconfirmed"
    cur_str   = f"${cur_yes:.3f}" if cur_yes is not None else "?"

    return (
        f"🎯 #{rank} VALIDATED SIGNAL — BET {play}\n"
        f"{title}\n"
        f"\n"
        f"Buy {play} at: ${buy_at:.3f}/share\n"
        f"$100 → +${round(payout):.0f} profit if correct\n"
        f"\n"
        f"✅ {traders} elite wallets | ${cost:.0f} real money in\n"
        f"✅ Price moved {drift_str} our way (now {cur_str})\n"
        f"\n"
        f"polymarket.com → search → {play} → confirm"
    )


def main():
    seen   = load_seen()
    opens  = load_opens()

    validated = []
    rejected  = 0

    seen_titles = {}
    for pos in opens:
        tid   = pos.get("token_id", "")
        title = pos.get("title", "")
        if not tid or tid in seen:
            continue

        pos = enrich(pos)
        passed, fails = validate(pos)

        if passed:
            # Dedup by title, keep highest score
            if title not in seen_titles or pos["_score"] > seen_titles[title]["_score"]:
                seen_titles[title] = pos
        else:
            rejected += 1
            print(f"  SKIP: {title[:45]} — {'; '.join(fails)}")

    validated = sorted(seen_titles.values(), key=lambda x: x["_score"], reverse=True)[:3]

    if not validated:
        print(f"No validated signals (scanned {len(opens)} open, {rejected} failed gates)")
        return

    now = datetime.now().strftime("%H:%M")
    header = (
        f"🔥 POLYMARKET — {now}\n"
        f"{len(validated)} trade{'s' if len(validated)>1 else ''} passed ALL validation gates.\n"
        f"Elite wallets + real $ + price confirmed moving our way."
    )
    ok, err = send_imessage(header)
    print(f"Header: {'sent' if ok else f'FAILED {err}'}")
    time.sleep(1.5)

    for i, pos in enumerate(validated, 1):
        msg = fmt_alert(pos, i)
        ok, err = send_imessage(msg)
        if ok:
            seen.add(pos.get("token_id", ""))
        print(f"  #{i} {pos['_play']} {pos['_payout_pct']:.0f}% — "
              f"drift={pos.get('_drift','?')} — {'sent' if ok else f'FAILED: {err}'}")
        time.sleep(1.5)

    save_seen(seen)
    print(f"Done — {len(validated)} alerts sent, {rejected} rejected by validation.")


if __name__ == "__main__":
    main()
