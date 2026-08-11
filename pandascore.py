#!/usr/bin/env python3
"""
pandascore.py — PandaScore esports data/odds client for the scalp lab.
Purpose: independent bookmaker-grade reference for esports matches, to be
logged as a covariate on nearres entries (model/line agreement vs market
price). NOT a trading trigger until the covariate proves predictive at gate.

Setup (one human step):
  1. Sign up free at https://app.pandascore.co/signup
  2. Dashboard → copy your access token
  3. Paste it:   echo "YOUR_TOKEN" > ~/Documents/polymarket/.pandascore_key
The bot picks it up automatically — no restart needed (once-mode).

No key → every call returns {} / [] quietly. Free tier ≈ 1000 req/h; we cache
5 min and poll only running+upcoming matches (2 calls/refresh).

CLI test:  python3 pandascore.py
"""
import json, os, sys, time, urllib.request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
KEY_FILE = os.path.join(BASE_DIR, ".pandascore_key")
API      = "https://api.pandascore.co"
_cache       = {"matches": [], "ts": 0.0}
_score_cache = {}  # match_id -> {"ts": float, "data": dict}
TTL       = 300
SCORE_TTL =  30   # live score cache: 30s (fast enough for 60s watchdog cycles)


def _key():
    try:
        return open(KEY_FILE).read().strip()
    except Exception:
        return None


def _get(path, params=""):
    k = _key()
    if not k:
        return None
    url = f"{API}{path}?token={k}&per_page=50{params}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except Exception as e:
        sys.stderr.write(f"[pandascore] {path}: {e}\n")
        return None


def matches(force=False):
    """Running + upcoming esports matches (all titles), cached 5 min.
    Each item: {name, begin_at, status, league, game, opponents[]}"""
    now = time.time()
    if not force and now - _cache["ts"] < TTL:
        return _cache["matches"]
    out = []
    for path in ("/matches/running", "/matches/upcoming"):
        rows = _get(path) or []
        for m in rows:
            try:
                # tournament_round: infer bracket depth from match/tournament name
                t_name = (m.get("tournament") or {}).get("name", "")
                m_name = m.get("name", "")
                combined = (t_name + " " + m_name).lower()
                if any(x in combined for x in ("grand final", "grand finale")):
                    t_round = "grand_final"
                elif any(x in combined for x in ("final",)):
                    t_round = "final"
                elif any(x in combined for x in ("semifinal", "semi-final", "semi final")):
                    t_round = "semifinal"
                elif any(x in combined for x in ("quarterfinal", "quarter-final", "quarter final")):
                    t_round = "quarterfinal"
                elif any(x in combined for x in ("playoff", "play-off", "knockou")):
                    t_round = "playoff"
                elif any(x in combined for x in ("group", "swiss", "regular season", "open qualifier", "qualifier")):
                    t_round = "group_stage"
                else:
                    t_round = "unknown"
                out.append({
                    "id":               m.get("id"),
                    "name":             m_name,
                    "begin":            m.get("begin_at"),
                    "status":           m.get("status"),
                    "league":           (m.get("league") or {}).get("name", ""),
                    "game":             (m.get("videogame") or {}).get("name", ""),
                    "teams":            [(o.get("opponent") or {}).get("name", "")
                                         for o in (m.get("opponents") or [])],
                    "tournament_round": t_round,
                })
            except Exception:
                continue
    if out or _key():
        _cache.update({"matches": out, "ts": now})
    return out


def live_score(match_id):
    """Return live map/game scores for a running match.
    Returns dict:
      games_done  — number of completed maps
      score       — (team1_maps_won, team2_maps_won) series score
      latest_map  — winner of most-recently-completed map ("team1"/"team2"/"none")
    Returns {} if no key, match not found, or error. Cached 30s."""
    if not match_id or not _key():
        return {}
    now = time.time()
    cached = _score_cache.get(match_id)
    if cached and now - cached["ts"] < SCORE_TTL:
        return cached["data"]
    raw = _get(f"/matches/{match_id}")
    if not raw:
        return {}
    games = raw.get("games") or []
    done  = [g for g in games if g.get("complete")]
    t1_w  = sum(1 for g in done if (g.get("winner") or {}).get("type") == "Team"
                and (g.get("winner") or {}).get("id") == _team_id(raw, 0))
    t2_w  = len(done) - t1_w
    last_winner = "none"
    if done:
        last = done[-1]
        wid  = (last.get("winner") or {}).get("id")
        last_winner = "team1" if wid == _team_id(raw, 0) else "team2"
    data = {"games_done": len(done), "score": (t1_w, t2_w), "latest_map": last_winner}
    _score_cache[match_id] = {"ts": now, "data": data}
    return data


def _team_id(match_raw, idx):
    """Extract team id at position idx from opponents list."""
    try:
        return match_raw["opponents"][idx]["opponent"]["id"]
    except (KeyError, IndexError, TypeError):
        return None


def _name_tokens(m):
    """Team identifiers from BOTH sources: opponents[] carries full names
    ('Natus Vincere'); the match title carries acronyms ('NAVI vs TS')."""
    toks = [t.lower() for t in m["teams"] if t]
    tail = m.get("name", "").split(":")[-1]
    for part in tail.lower().split(" vs "):
        p = part.strip()
        if p:
            toks.append(p)
    return toks


def match_for_question(q):
    """Map a Polymarket question to a PandaScore match. Exact = two distinct
    identifiers hit; partial = one distinctive (len>=4) hit."""
    ql = str(q).lower()
    partial = None
    for m in matches():
        toks = _name_tokens(m)
        hits = {t for t in toks if len(t) >= 2 and t in ql}
        if len(hits) >= 2:
            return m
        if partial is None and any(len(t) >= 4 for t in hits):
            partial = m
    return partial


if __name__ == "__main__":
    if not _key():
        print("No key yet. After signup:")
        print(f'  echo "YOUR_TOKEN" > {KEY_FILE}')
        sys.exit(0)
    ms = matches(force=True)
    print(f"{len(ms)} running/upcoming matches")
    for m in ms[:10]:
        print(f"  [{m['game']}] {m['name']}  status={m['status']} begin={m['begin']}")
