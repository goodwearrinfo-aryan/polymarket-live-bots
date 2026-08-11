#!/usr/bin/env python3
"""
info_digest.py — information-edge alert service (2026-06-13).

Pulls fresh headlines from the local hermes_news service (:48580), matches
them against live Polymarket gamma markets by keyword overlap, and flags
markets that have NOT yet repriced (|24h chg| small) despite matching news.
Sends the shortlist to the user via iMessage. READ-ONLY: never trades,
never touches scalp_lab state.

Run:  python3 info_digest.py          # one digest (launchd runs this)
      python3 info_digest.py --dry    # print only, no iMessage
"""
import json, re, sys, subprocess, urllib.request, urllib.parse
from datetime import datetime, timezone, timedelta

HERMES  = "http://127.0.0.1:48580/api/news"
GAMMA   = "https://gamma-api.polymarket.com/markets"
TARGET  = "krisharyan@icloud.com"
MAX_AGE_H   = 6      # headlines newer than this
MIN_OVERLAP = 3      # min meaningful word matches headline<->question (2 was noise: "new york", "world cup")
MAX_CHG     = 0.03   # "hasn't repriced": |24h chg| <= 3c
MIN_VOL     = 50_000
MAX_ALERTS  = 6

STOP = set("""a an the of to in on for and or is are was were will be at by with
from as it its this that has have had not no yes up down new says said after
before over under his her their they she he who what when where why how
could would should may might can vs his""".split())


def words(s):
    return {w for w in re.findall(r"[a-z']+", s.lower()) if len(w) > 3 and w not in STOP}


def get_json(url, timeout=12):
    req = urllib.request.Request(url, headers={"User-Agent": "info-digest/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def fresh_headlines():
    items = get_json(HERMES).get("items", [])
    cutoff = datetime.now(timezone.utc) - timedelta(hours=MAX_AGE_H)
    out = []
    for it in items:
        try:
            ts = datetime.fromisoformat(it["first_seen_utc"].replace("Z", "+00:00"))
        except Exception:
            continue
        if ts >= cutoff:
            out.append(it)
    return out


def live_markets():
    out, offset = [], 0
    while offset <= 600:
        batch = get_json(f"{GAMMA}?active=true&closed=false&limit=200&offset={offset}")
        if not batch: break
        for m in batch:
            try:
                yes = float(json.loads(m.get("outcomePrices", "[]"))[0])
                vol = float(m.get("volumeNum") or m.get("volume") or 0)
                chg = float(m.get("oneDayPriceChange") or 0)
            except Exception:
                continue
            if vol < MIN_VOL: continue
            if not (0.05 <= yes <= 0.95): continue
            out.append({"q": m.get("question", ""), "yes": yes, "vol": vol,
                        "chg": chg, "slug": m.get("slug", "")})
        if len(batch) < 200: break
        offset += 200
    return out


def main():
    dry = "--dry" in sys.argv
    heads = fresh_headlines()
    mkts  = live_markets()
    hits = []
    for m in mkts:
        if abs(m["chg"]) > MAX_CHG: continue       # already repriced — no edge
        qw = words(m["q"])
        if not qw: continue
        best, besth = 0, None
        for h in heads:
            ov = len(qw & words(h["title"]))
            if ov > best:
                best, besth = ov, h
        if best >= MIN_OVERLAP:
            hits.append((best, m, besth))
    hits.sort(key=lambda x: -x[0])
    hits = hits[:MAX_ALERTS]

    stamp = datetime.now().strftime("%H:%M")
    if not hits:
        print(f"[{stamp}] no unrepriced news-market matches")
        return
    lines = [f"📰 INFO EDGE {stamp} — news matched, market not yet moved:"]
    for ov, m, h in hits:
        lines.append(f"• {m['q'][:70]}")
        lines.append(f"  YES {m['yes']:.2f} chg {m['chg']:+.2f} vol ${m['vol']/1000:.0f}k (match {ov})")
        lines.append(f"  ↳ {h['title'][:80]} [{h['source']}]")
    msg = "\n".join(lines)
    print(msg)
    if not dry:
        script = f'''
tell application "Messages"
    set s to 1st service whose service type = iMessage
    send "{msg.replace('"', "'")}" to buddy "{TARGET}" of s
end tell
'''
        subprocess.run(["osascript", "-e", script], capture_output=True, timeout=30)


if __name__ == "__main__":
    main()
