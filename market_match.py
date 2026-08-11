#!/usr/bin/env python3
"""market_match.py — fuzzy cross-venue market/outcome matching.

Upgraded 2026-06-15: matching core now derived from the MarketMatcher in
oss-bots/polymarket-arbitrage (ImMike, MIT) — adds sports-team extraction,
date matching, person/event matching, entity overlap, and category boosts on
top of fuzzy text similarity. The old Jaccard+Levenshtein scorer scored an
obvious match (BTC $150k pair) at only 0.33; the upgraded scorer handles it.

Backward-compatible: `similarity(a, b)` and `match(left, right, threshold)`
keep their old signatures. Added: `same_market()`, `categorize()`.

USE for CROSS-VENUE matching (Poly<->Kalshi) where wording genuinely differs.
DO NOT use to group markets WITHIN one venue (ladderarb/clusterarb need EXACT
grouping — fuzzy there re-commits Lesson 7 overmatch). No deps. Paper util.
"""
import re
from difflib import SequenceMatcher
from typing import Optional

NOISE_WORDS = {"will", "the", "a", "an", "be", "to", "in", "on", "by", "at", "what", "who",
               "which", "when", "is", "are", "was", "were", "market", "prediction", "bet",
               "odds", "win", "winner"}

NFL_TEAMS = {
    "arizona cardinals": ["cardinals", "arizona", "ari"], "atlanta falcons": ["falcons", "atlanta", "atl"],
    "baltimore ravens": ["ravens", "baltimore", "bal"], "buffalo bills": ["bills", "buffalo", "buf"],
    "carolina panthers": ["panthers", "carolina", "car"], "chicago bears": ["bears", "chicago", "chi"],
    "cincinnati bengals": ["bengals", "cincinnati", "cin"], "cleveland browns": ["browns", "cleveland", "cle"],
    "dallas cowboys": ["cowboys", "dallas", "dal"], "denver broncos": ["broncos", "denver", "den"],
    "detroit lions": ["lions", "detroit", "det"], "green bay packers": ["packers", "green bay", "gb"],
    "houston texans": ["texans", "houston", "hou"], "indianapolis colts": ["colts", "indianapolis", "ind"],
    "jacksonville jaguars": ["jaguars", "jacksonville", "jax"], "kansas city chiefs": ["chiefs", "kansas city", "kc"],
    "las vegas raiders": ["raiders", "las vegas", "lv"], "los angeles chargers": ["chargers", "la chargers", "lac"],
    "los angeles rams": ["rams", "la rams", "lar"], "miami dolphins": ["dolphins", "miami", "mia"],
    "minnesota vikings": ["vikings", "minnesota", "min"], "new england patriots": ["patriots", "new england", "ne"],
    "new orleans saints": ["saints", "new orleans"], "new york giants": ["giants", "ny giants", "nyg"],
    "new york jets": ["jets", "ny jets", "nyj"], "philadelphia eagles": ["eagles", "philadelphia", "phi"],
    "pittsburgh steelers": ["steelers", "pittsburgh", "pit"], "san francisco 49ers": ["49ers", "san francisco", "sf"],
    "seattle seahawks": ["seahawks", "seattle", "sea"], "tampa bay buccaneers": ["buccaneers", "tampa bay", "tb"],
    "tennessee titans": ["titans", "tennessee", "ten"], "washington commanders": ["commanders", "washington", "was"],
}
NBA_TEAMS = {
    "boston celtics": ["celtics", "boston"], "brooklyn nets": ["nets", "brooklyn"],
    "new york knicks": ["knicks"], "philadelphia 76ers": ["76ers", "sixers"],
    "toronto raptors": ["raptors", "toronto"], "chicago bulls": ["bulls"],
    "cleveland cavaliers": ["cavaliers", "cavs"], "detroit pistons": ["pistons"],
    "indiana pacers": ["pacers", "indiana"], "milwaukee bucks": ["bucks", "milwaukee"],
    "atlanta hawks": ["hawks"], "charlotte hornets": ["hornets", "charlotte"],
    "miami heat": ["heat", "miami"], "orlando magic": ["magic", "orlando"],
    "washington wizards": ["wizards"], "denver nuggets": ["nuggets", "denver"],
    "minnesota timberwolves": ["timberwolves", "wolves"], "oklahoma city thunder": ["thunder", "okc"],
    "portland trail blazers": ["blazers", "portland"], "utah jazz": ["jazz", "utah"],
    "golden state warriors": ["warriors", "golden state"], "los angeles clippers": ["clippers", "la clippers"],
    "los angeles lakers": ["lakers", "la lakers"], "phoenix suns": ["suns", "phoenix"],
    "sacramento kings": ["kings", "sacramento"], "dallas mavericks": ["mavericks", "mavs"],
    "houston rockets": ["rockets", "houston"], "memphis grizzlies": ["grizzlies", "memphis"],
    "new orleans pelicans": ["pelicans"], "san antonio spurs": ["spurs", "san antonio"],
}
_TEAM_LOOKUP = {}
for _full, _vars in {**NFL_TEAMS, **NBA_TEAMS}.items():
    _TEAM_LOOKUP[_full] = _full
    for _v in _vars:
        _TEAM_LOOKUP[_v.lower()] = _full

_MONTHS = {'jan': '01', 'january': '01', 'feb': '02', 'february': '02', 'mar': '03', 'march': '03',
           'apr': '04', 'april': '04', 'may': '05', 'jun': '06', 'june': '06', 'jul': '07', 'july': '07',
           'aug': '08', 'august': '08', 'sep': '09', 'september': '09', 'oct': '10', 'october': '10',
           'nov': '11', 'november': '11', 'dec': '12', 'december': '12'}


# ── legacy helpers kept for any old caller ───────────────────────────────────
def jaccard(a, b):
    s1, s2 = set(str(a).lower().split()), set(str(b).lower().split())
    return 1.0 if not s1 and not s2 else len(s1 & s2) / len(s1 | s2)


def levenshtein(a, b):
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


# ── matching core ────────────────────────────────────────────────────────────
def normalize_text(text: str) -> str:
    text = re.sub(r'[^\w\s]', ' ', str(text).lower())
    return ' '.join(w for w in text.split() if w not in NOISE_WORDS)


def extract_teams(text: str) -> list:
    tl, found = str(text).lower(), []
    for key in sorted(_TEAM_LOOKUP.keys(), key=len, reverse=True):
        if key in tl:
            canon = _TEAM_LOOKUP[key]
            if canon not in found:
                found.append(canon)
                tl = tl.replace(key, "")
    return found


def extract_key_entities(text: str) -> set:
    ent = set(re.findall(r'\d+(?:\.\d+)?%?', str(text)))
    ent.update(re.findall(r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b', str(text)))
    tl = str(text).lower()
    for term in ("trump", "biden", "republican", "democrat", "gop", "dnc", "harris", "desantis",
                 "election", "president", "bitcoin", "btc", "ethereum", "eth", "crypto", "solana", "sol"):
        if term in tl:
            ent.add(term)
    return ent


def extract_date(text: str) -> Optional[str]:
    tl = str(text).lower()
    for name, num in _MONTHS.items():
        m = re.search(rf'{name}\.?\s+(\d{{1,2}})(?:,?\s+(\d{{4}}))?', tl)
        if m:
            return f"{m.group(2) or '2026'}-{num}-{m.group(1).zfill(2)}"
    m = re.search(r'(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})', tl)
    if m:
        y = m.group(3)
        return f"{('20' + y) if len(y) == 2 else y}-{m.group(1).zfill(2)}-{m.group(2).zfill(2)}"
    return None


def _dates_match(d1, d2) -> bool:
    return True if (not d1 or not d2) else d1 == d2


def is_sports_match(t1: str, t2: str):
    a, b = extract_teams(t1), extract_teams(t2)
    if len(a) >= 2 and len(b) >= 2:
        sa, sb = set(a[:2]), set(b[:2])
        if sa == sb:
            return (True, 0.95) if _dates_match(extract_date(t1), extract_date(t2)) else (False, 0.3)
        ov = sa & sb
        if ov and _dates_match(extract_date(t1), extract_date(t2)):
            return True, 0.7 + 0.2 * len(ov) / 2
    return False, 0.0


def is_same_person_event(t1: str, t2: str):
    pats = [r'\b(trump|biden|harris|desantis|obama|pence)\b',
            r'\b(musk|zuckerberg|bezos|gates)\b', r'\b(powell|yellen)\b']
    p1, p2 = set(), set()
    for p in pats:
        p1.update(re.findall(p, str(t1).lower()))
        p2.update(re.findall(p, str(t2).lower()))
    if p1 and p2 and p1 & p2:
        act = r'\b(win|lose|approve|poll|elect|resign|indicted?|convicted?)\w*\b'
        a1, a2 = set(re.findall(act, str(t1).lower())), set(re.findall(act, str(t2).lower()))
        return (True, 0.85) if a1 & a2 else (True, 0.6)
    return False, 0.0


def similarity(a: str, b: str) -> float:
    """Score 0..1 that two market questions are the same underlying prediction.
    Sports team+date and person+event matches short-circuit to high confidence;
    otherwise fuzzy text + entity overlap + sport/crypto boosts."""
    ok, s = is_sports_match(a, b)
    if ok and s > 0.7:
        return s
    ok, s = is_same_person_event(a, b)
    if ok and s > 0.7:
        return s
    text_sim = SequenceMatcher(None, normalize_text(a), normalize_text(b)).ratio()
    e1, e2 = extract_key_entities(a), extract_key_entities(b)
    sim = (0.5 * text_sim + 0.5 * (len(e1 & e2) / max(len(e1), len(e2)))) if (e1 and e2) else text_sim
    for kws, boost in ((("nfl", "nba", "mlb", "nhl", "football", "basketball", "baseball", "hockey"), 0.15),
                       (("bitcoin", "btc", "ethereum", "eth", "solana", "sol"), 0.2)):
        x = [k for k in kws if k in str(a).lower()]
        y = [k for k in kws if k in str(b).lower()]
        if x and y and set(x) & set(y):
            sim = min(1.0, sim + boost)
    return sim


def same_market(a: str, b: str, threshold: float = 0.6) -> bool:
    return similarity(a, b) >= threshold


def categorize(text: str) -> str:
    tl = str(text).lower()
    if any(x in tl for x in ('trump', 'biden', 'harris', 'president', 'election', 'democrat',
            'republican', 'congress', 'senate', 'governor', 'mayor', 'vote', 'nominee', 'primary',
            'presidential', 'prime minister', 'parliament')):
        return 'politics'
    if any(x in tl for x in ('bitcoin', 'btc', 'ethereum', 'eth', 'crypto', 'token', 'solana', 'sol',
            'blockchain', 'defi', 'nft', 'fdv', 'market cap')):
        return 'crypto'
    if any(x in tl for x in ('fed', 'interest rate', 'inflation', 'gdp', 'recession', 'stock',
            'nasdaq', 'dow', 's&p', 'treasury', 'tariff', 'federal reserve')):
        return 'finance'
    if any(x in tl for x in ('nfl', 'nba', 'mlb', 'nhl', 'premier league', 'champions league',
            'super bowl', 'playoff', 'la liga', 'soccer', ' fc', 'world cup', 'stanley cup')):
        return 'sports'
    if any(x in tl for x in _TEAM_LOOKUP.keys()):
        return 'sports'
    if any(x in tl for x in ('oscar', 'grammy', 'emmy', 'movie', 'film', 'album', 'artist',
            'actor', 'actress', 'netflix', 'spotify', 'best picture')):
        return 'entertainment'
    if any(x in tl for x in ('ai ', 'openai', 'gpt', 'google', 'apple', 'microsoft', 'tesla',
            'spacex', 'nvidia')):
        return 'tech'
    return 'other'


def match(left_titles, right_titles, threshold: float = 0.7):
    """Greedy one-to-one matching. Returns list of (left_idx, right_idx, score)."""
    used, out = set(), []
    for li, lt in enumerate(left_titles):
        best, best_ri = 0.0, -1
        for ri, rt in enumerate(right_titles):
            if ri in used:
                continue
            sc = similarity(lt, rt)
            if sc > best and sc >= threshold:
                best, best_ri = sc, ri
        if best_ri >= 0:
            used.add(best_ri)
            out.append((li, best_ri, round(best, 4)))
    return out


if __name__ == "__main__":
    tests = [
        ("Will Bitcoin reach $150k by July?", "Bitcoin above $150,000 by July 1", True),
        ("Will Trump win the 2026 election?", "Trump wins 2026 presidential election", True),
        ("Kevin Warsh nominated Fed chair", "Kevin Warsh next Fed chair", True),
        ("Chiefs vs Bills - who wins?", "Will the Kansas City Chiefs beat the Buffalo Bills?", True),
        ("Lakers win NBA finals", "Yankees win World Series", False),
        ("Will it rain in London tomorrow?", "Will the Lakers beat the Celtics?", False),
    ]
    print("market_match self-test (upgraded):")
    for a, b, expect in tests:
        s = similarity(a, b)
        ok = (s >= 0.6) == expect
        print(f"  [{'ok' if ok else 'XX'}] {s:.2f} want={'match' if expect else 'no':5s} "
              f"cat={categorize(a):13s} {a[:32]!r} ~ {b[:32]!r}")
