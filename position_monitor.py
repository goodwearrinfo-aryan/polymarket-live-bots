#!/usr/bin/env python3
"""position_monitor.py — AI agent that watches every open analyst position for
price drift and breaking news, then routes an action recommendation.

Every run (30min via watchdog):
  1. Fetch current YES price from Gamma for each position in analyst_positions.json
  2. Flag price moves ≥ ALERT_PTS vs entry
  3. Scan hermes (127.0.0.1:48580) for headlines matching position keywords
  4. LLM (haiku) interprets drift + news → HOLD / INVESTIGATE / EXIT
  5. iMessage alert on significant events; always appends to position_monitor.log

Usage:
  python3 position_monitor.py           # verbose run
  python3 position_monitor.py --once    # quiet watchdog mode
  python3 position_monitor.py --status  # show latest status table
"""
import os, sys, json, re, time, argparse, urllib.request, urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOOK = HERE / "analyst_positions.json"
LOG  = HERE / "position_monitor.log"
UA   = {"User-Agent": "position-monitor/1.0"}

ALERT_PTS   = 0.05   # flag if YES moved ≥5 pts vs entry
HERMES_URL  = "http://127.0.0.1:48580/api/news"
_MONTHS = {"january","february","march","april","may","june","july","august",
           "september","october","november","december","will","the","by","for"}

# ── helpers ────────────────────────────────────────────────────────────────────

def _load_env():
    for envp in (HERE / ".env", Path.home() / "Documents/futures-bot/.env"):
        if not envp.exists():
            continue
        for line in envp.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

_load_env()
KEY = os.environ.get("ANTHROPIC_API_KEY")


def _get(url, params=None, timeout=15):
    if params:
        url += "?" + urllib.parse.urlencode(params)
    try:
        return json.load(urllib.request.urlopen(
            urllib.request.Request(url, headers=UA), timeout=timeout))
    except Exception:
        return None


def _call(prompt, max_tokens=80):
    if not KEY:
        return None
    body = json.dumps({
        "model": "claude-haiku-4-5-20251001", "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}]
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=body,
        headers={"x-api-key": KEY, "content-type": "application/json",
                 "anthropic-version": "2023-06-01"})
    try:
        r = json.load(urllib.request.urlopen(req, timeout=30))
        return r["content"][0]["text"].strip() if "content" in r else None
    except Exception:
        return None


def _imessage(msg, recipient="+918449447444"):
    safe = msg.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    # strip emoji and non-ASCII for AppleScript compatibility
    safe = "".join(c if ord(c) < 128 else " " for c in safe)
    script = f'tell application "Messages" to send "{safe}" to buddy "{recipient}" of (service 1 whose service type is iMessage)'
    os.system(f"osascript -e '{script}' 2>/dev/null")


# ── Gamma price fetch ──────────────────────────────────────────────────────────

def current_price(pos):
    """Current YES price from Gamma. Returns None on failure."""
    mid = pos.get("market_id"); slug = pos.get("slug")
    for q in ([f"condition_ids={mid}"] if mid else []) + ([f"slug={slug}"] if slug else []):
        d = _get(f"https://gamma-api.polymarket.com/markets?{q}")
        if isinstance(d, list) and d:
            prices = d[0].get("outcomePrices")
            if isinstance(prices, str):
                try:
                    prices = json.loads(prices)
                except Exception:
                    continue
            if prices and len(prices) >= 1:
                try:
                    return float(prices[0])
                except Exception:
                    continue
    return None


# ── Hermes news scan ──────────────────────────────────────────────────────────

def news_for(pos, max_show=5):
    """Keywords from the question → match hermes headlines. Returns (n_matched, headline_str)."""
    caps = [w for w in re.findall(r"[A-Z][a-zA-Z]{2,}", pos.get("q",""))
            if w.lower() not in _MONTHS]
    kw = list(dict.fromkeys(w.lower() for w in caps)) or \
        [w for w in re.findall(r"[a-z]{5,}", pos.get("q","").lower())
         if w not in _MONTHS][:4]
    if not kw:
        return 0, ""
    try:
        items = (_get(HERMES_URL, timeout=6) or {}).get("items", [])
    except Exception:
        return 0, "(hermes down)"
    matched = [it for it in items
               if any(k in (it.get("title") or "").lower() for k in kw)]
    if not matched:
        return 0, ""
    matched.sort(key=lambda it: it.get("first_seen_utc", ""), reverse=True)
    heads = " | ".join((it.get("title") or "")[:60] for it in matched[:max_show])
    return len(matched), heads


# ── LLM action recommendation ─────────────────────────────────────────────────

ACTION_PROMPT = """You are a prediction-market risk manager monitoring an open position.

Position: {side} on "{q}"
Entry YES price: {entry_yes:.2f}  Current YES price: {curr:.2f}  (drift: {drift:+.2f})
Resolves: {resolves}
Thesis: {thesis}

Live news ({n_news} headlines matching topic):
{news_str}

Give a one-word ACTION then ≤12-word reason:
  HOLD       — no material change, thesis intact
  INVESTIGATE — significant drift or news warrants checking resolution criteria / market
  EXIT       — thesis invalidated or risk/reward flipped badly

Format: ACTION — reason (≤12 words)"""


def recommend_action(pos, curr, drift, n_news, news_str):
    prompt = ACTION_PROMPT.format(
        side=pos.get("side","?"), q=pos.get("q","?"),
        entry_yes=float(pos.get("entry_yes",0.5)), curr=curr, drift=drift,
        resolves=pos.get("resolves","?"), thesis=pos.get("thesis","")[:200],
        n_news=n_news, news_str=news_str[:300] if news_str else "(none)")
    r = _call(prompt, max_tokens=60)
    if not r:
        return "HOLD", "API unavailable"
    parts = r.split("—", 1)
    action = parts[0].strip().upper()
    reason = parts[1].strip() if len(parts) > 1 else r[7:].strip()
    action = action if action in ("HOLD","INVESTIGATE","EXIT") else "HOLD"
    return action, reason[:80]


# ── Main monitoring loop ──────────────────────────────────────────────────────

def monitor(verbose=True):
    if not BOOK.exists():
        print("no analyst_positions.json"); return []
    positions = json.loads(BOOK.read_text()).get("positions", [])
    if not positions:
        print("no open positions"); return []

    ts = time.strftime("%Y-%m-%d %H:%MZ", time.gmtime())
    alerts, log_rows = [], []

    for pos in positions:
        q  = pos.get("q","?")[:55]
        side = pos.get("side","?")
        entry_yes = float(pos.get("entry_yes", 0.5))

        curr = current_price(pos)
        if curr is None:
            row = {"ts": ts, "q": q, "side": side, "entry_yes": entry_yes,
                   "curr": None, "drift": None, "action": "HOLD",
                   "reason": "price unavailable", "n_news": 0, "news": ""}
            log_rows.append(row)
            if verbose:
                print(f"  ⚠️  {side:3} {q}  — price unavailable")
            continue

        drift = curr - entry_yes
        n_news, news_str = news_for(pos)
        significant = abs(drift) >= ALERT_PTS or n_news >= 3

        if significant:
            action, reason = recommend_action(pos, curr, drift, n_news, news_str)
        else:
            action, reason = "HOLD", "within normal range"

        row = {"ts": ts, "q": q, "side": side, "entry_yes": entry_yes,
               "curr": round(curr, 3), "drift": round(drift, 3),
               "action": action, "reason": reason,
               "n_news": n_news, "news": news_str[:120]}
        log_rows.append(row)

        mark = "🟢" if action == "HOLD" else ("🟡" if action == "INVESTIGATE" else "🔴")
        if verbose:
            print(f"  {mark} {side:3} yes={curr:.2f} (entry {entry_yes:.2f} Δ{drift:+.2f}) "
                  f"news={n_news}  {action:12} {q}")
            if action != "HOLD":
                print(f"       → {reason}")

        if action in ("INVESTIGATE","EXIT"):
            alerts.append(f"{mark}{action} {side} '{q[:40]}' Δ{drift:+.2f} news={n_news} | {reason}")

    # append to log
    with open(LOG, "a") as f:
        for row in log_rows:
            f.write(json.dumps(row) + "\n")

    if alerts:
        msg = f"[{ts}] Position monitor — {len(alerts)} alert(s):\n" + "\n".join(alerts)
        _imessage(msg)
        if verbose:
            print(f"\n  → iMessage sent: {len(alerts)} alert(s)")

    print(f"[{ts}] monitor: {len(positions)} positions checked, "
          f"{len(alerts)} alert(s) → {LOG}")
    return log_rows


def show_status():
    """Print latest status for each position from the log."""
    if not LOG.exists():
        print("no log yet"); return
    rows = {}
    for line in open(LOG):
        try:
            r = json.loads(line)
            rows[r["q"]] = r
        except Exception:
            continue
    print(f"\n  Latest position monitor status ({len(rows)} positions):\n")
    for q, r in rows.items():
        mark = "🟢" if r["action"] == "HOLD" else ("🟡" if r["action"] == "INVESTIGATE" else "🔴")
        drift = r.get("drift")
        curr  = r.get("curr")
        print(f"  {mark} {r['side']:3} yes={curr or '?'} Δ{drift:+.3f} "
              f"news={r['n_news']}  {r['action']:12}  {q[:50]}")
        if r.get("reason") and r["action"] != "HOLD":
            print(f"       {r['reason']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once",   action="store_true", help="quiet watchdog mode")
    ap.add_argument("--status", action="store_true", help="show latest status table")
    a = ap.parse_args()
    if a.status:
        show_status(); return
    monitor(verbose=not a.once)


if __name__ == "__main__":
    main()
