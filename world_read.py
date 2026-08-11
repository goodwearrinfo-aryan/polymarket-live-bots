#!/usr/bin/env python3
"""world_read.py — 24/7 keyless WORLD BRIEF: geopolitics + sports + markets + earth/space/health.

Pulls live data from keyless public sources every cycle, writes the snapshot to the Obsidian vault
(World Read.md — a live dashboard), and iMessages ONLY on a big numeric move (crypto |24h|>=8% or a
fresh M6.0+ quake), deduped so it never spams. launchd, hourly, $0, fail-soft throughout.
"""
import subprocess, json, re, os, datetime

HERE   = os.path.expanduser("~/Documents/polymarket")
VAULT  = os.path.expanduser("~/Documents/PolymarketVault")
OUT    = os.path.join(VAULT, "World Read.md")
STATE  = os.path.join(HERE, ".world_read_state.json")
TARGETS = ["krisharyan@icloud.com", "+918449447444"]
OSA = ('on run argv\ntell application "Messages"\n  set s to 1st service whose service type = iMessage\n'
       '  set b to buddy (item 1 of argv) of s\n  send (item 2 of argv) to b\nend tell\nend run')


def imsg(m):
    for t in TARGETS:
        try: subprocess.run(["osascript", "-e", OSA, t, m], capture_output=True, timeout=8)
        except Exception: pass

def get(u, t=8):
    try: return subprocess.run(["curl", "-sL", "-m", str(t), "-A", "research-bot", u],
                               capture_output=True, text=True, timeout=t + 3).stdout
    except Exception: return ""

def gj(u, t=8):
    try: return json.loads(get(u, t))
    except Exception: return None

def _clean(s):
    s = re.sub(r"\{\{[^}]*\}\}", "", s); s = re.sub(r"<ref.*?</ref>", "", s, flags=re.S); s = re.sub(r"<[^>]+>", "", s)
    s = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]+)\]\]", r"\1", s); s = re.sub(r"\[https?://\S+ ([^\]]+)\]", r"\1", s)
    s = re.sub(r"'''?", "", s); return re.sub(r"\s+", " ", s).strip(" *:–-")


def geopolitics():
    today = datetime.date.today()
    for d in range(4):
        day = today - datetime.timedelta(days=d)
        page = ("Portal:Current_events/" + day.strftime("%Y %B ") + str(day.day)).replace(" ", "%20")
        j = gj("https://en.wikipedia.org/w/api.php?action=parse&page=" + page + "&format=json&prop=wikitext", 10)
        if j and "parse" in j:
            wt = j["parse"]["wikitext"]["*"]
            evs = [_clean(l) for l in wt.splitlines() if l.strip().startswith("**")]
            evs = [e for e in evs if len(e) > 28][:8]
            if evs:
                return day.strftime("%b %d"), evs
    return None, []


def sports():
    out = {}
    for lbl, code in [("Cricket", "cricket/8048"), ("MLB", "baseball/mlb"), ("NBA", "basketball/nba"),
                      ("Soccer EPL", "soccer/eng.1")]:
        d = gj(f"https://site.api.espn.com/apis/site/v2/sports/{code}/scoreboard", 7)
        rows = []
        for e in (d or {}).get("events", [])[:3]:
            try:
                a, b = e["competitions"][0]["competitors"][:2]
                rows.append(f"{a['team']['abbreviation']} {a.get('score','')}–{b.get('score','')} "
                            f"{b['team']['abbreviation']} ({e['status']['type']['shortDetail']})")
            except Exception:
                pass
        if rows: out[lbl] = rows
    return out


def main():
    try: prev = json.load(open(STATE))
    except Exception: prev = {}
    alerts = []

    geo_day, geo = geopolitics()
    spt = sports()

    crypto = {}
    for s in ("BTCUSDT", "ETHUSDT"):
        d = gj(f"https://api.binance.com/api/v3/ticker/24hr?symbol={s}", 6)
        if d:
            try: crypto[s[:3]] = (float(d["lastPrice"]), float(d["priceChangePercent"]))
            except Exception: pass
    fx = (gj("https://api.frankfurter.app/latest?from=USD&to=EUR,GBP,JPY,INR", 6) or {}).get("rates", {})
    q = gj("https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_day.geojson", 8)
    quakes = sorted((q or {}).get("features", []), key=lambda f: -(f["properties"].get("mag") or 0))[:3]
    w = (gj("https://api.open-meteo.com/v1/forecast?latitude=28.6&longitude=77.2&current_weather=true", 6) or {}).get("current_weather", {})
    iss = gj("http://api.open-notify.org/iss-now.json", 5); ast = gj("http://api.open-notify.org/astros.json", 5)

    # ---- alerts (deduped) ----
    now = datetime.datetime.now()
    for k, (px, ch) in crypto.items():
        last = prev.get("crypto_alert_ts", {}).get(k)
        recent = last and (now - datetime.datetime.fromisoformat(last)).total_seconds() < 8 * 3600
        if abs(ch) >= 8 and not recent:
            alerts.append(f"{k} {ch:+.1f}% 24h (${px:,.0f})")
            prev.setdefault("crypto_alert_ts", {})[k] = now.isoformat()
    seen_q = set(prev.get("quakes_alerted", []))
    for f in quakes:
        mag = f["properties"].get("mag") or 0
        qid = f.get("id")
        if mag >= 6.0 and qid not in seen_q:
            alerts.append(f"M{mag} quake {f['properties']['place'][:40]}")
            seen_q.add(qid)
    prev["quakes_alerted"] = list(seen_q)[-50:]
    if alerts:
        imsg("\U0001f30d world-read: " + " | ".join(alerts))

    # ---- write the vault snapshot ----
    L = ["---\ntype: dashboard\ncssclasses: [dashboard]\ntags: [world-read, live]\nupdated: %s\n---\n" % now.strftime("%Y-%m-%d %H:%M"),
         "# \U0001f30d World Read", f"*keyless live brief · updated {now.strftime('%Y-%m-%d %H:%M')} · $0*\n"]
    L.append("\n## \U0001f310 Geopolitics" + (f"  ({geo_day})" if geo_day else ""))
    L += [f"- {e}" for e in geo] or ["- (feed unreachable this cycle)"]
    L.append("\n## \U0001f3c6 Sports")
    for lbl, rows in spt.items():
        L.append(f"- **{lbl}:** " + " · ".join(rows))
    L.append("\n## \U0001f4b9 Markets & crypto")
    for k, (px, ch) in crypto.items(): L.append(f"- {k}: ${px:,.0f} ({ch:+.1f}% 24h)")
    if fx: L.append("- USD → " + " · ".join(f"{k} {v}" for k, v in fx.items()))
    L.append("\n## \U0001f30e Earth · space · health")
    if quakes: L.append("- Quakes (M4.5+/24h): " + " · ".join(f"M{f['properties']['mag']} {f['properties']['place'][:22]}" for f in quakes))
    if w: L.append(f"- Delhi: {w.get('temperature')}°C, wind {w.get('windspeed')} km/h")
    if iss: L.append(f"- ISS: {float(iss['iss_position']['latitude']):.0f}, {float(iss['iss_position']['longitude']):.0f}" + (f" · {ast['number']} in space" if ast else ""))
    L.append("\n*↑ [[World Data]] · [[Agent Fleet]]*\n")
    try:
        tmp = OUT + ".tmp"; open(tmp, "w", encoding="utf-8").write("\n".join(L)); os.replace(tmp, OUT)
    except Exception: pass

    try:
        tmp = STATE + ".tmp"; json.dump(prev, open(tmp, "w")); os.replace(tmp, STATE)
    except Exception: pass
    print(f"world_read: geo={len(geo)} sports={sum(len(v) for v in spt.values())} crypto={len(crypto)} quakes={len(quakes)} alerts={len(alerts)}")


if __name__ == "__main__":
    try: main()
    except Exception as e: print(f"world_read: error {str(e)[:80]}")
