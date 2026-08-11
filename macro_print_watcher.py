#!/usr/bin/env python3
"""macro_print_watcher.py — watches for macro DATA PRINTS landing and pages the ones
that touch the paper book.

NOT a trading bot. A keyless watcher: it polls news feeds for scheduled + surprise
macro releases (Fed/FOMC decision, US CPI, US jobs/NFP, US GDP, RBI policy, ECB), and
when a PRINT LANDS it decides whether it matters to us and, if so, iMessage-alerts:
  (A) TOP-TIER prints (Fed/CPI/jobs/RBI/ECB) always page — the crypto scalp book is
      broadly macro-exposed, so a rate/inflation surprise moves BTC/ETH we hold; and
  (B) a print that keyword-matches an OPEN Polymarket position title (harvested from the
      paper-book state files) pages as a position-specific hit.
Everything detected is logged + mirrored to Obsidian; only (A)/(B) page.

Discipline: "a print landed" is detected from a HEADLINE, so it is a heads-up to LOOK,
never an instruction to trade — the market has usually already moved by the time the wire
prints. Dedup per link so a print never re-pages. Read-only, paper, keyless. stdlib only.

Usage: python3 macro_print_watcher.py once | report
"""
import os, sys, json, time, re, html, glob, subprocess, urllib.request, urllib.error
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "macro_print_state.json")
LOG = os.path.join(HERE, "macro_print_log.jsonl")
VAULT = os.path.expanduser("~/Documents/PolymarketVault/Reports/macro_prints.md")
UA = {"User-Agent": "Mozilla/5.0 (macro-print-watcher; keyless research)"}

ALERT_TARGETS = ("krisharyan@icloud.com", "+918449447444")

# book state files to harvest OPEN-position market titles from (for position-specific hits)
BOOK_STATE_GLOBS = ["basketlock_state.json", "newsmove_state.json",
                    "deadline_state.json", "analyst_*state.json"]

# top-tier macro prints — always page (broad crypto/rates exposure). (label, detector)
TIER1 = [
    ("FOMC / Fed rate", re.compile(r"\b(fomc|federal reserve|fed)\b.*\b(rate|hike|cut|hold|"
                                   r"decision|minutes|powell|basis points|bps)\b", re.I)),
    ("US CPI / inflation", re.compile(r"\b(cpi|inflation|pce)\b.*\b(rose|fell|report|data|"
                                      r"%|percent|cooler|hotter|eases|accelerat)\b", re.I)),
    ("US jobs / NFP", re.compile(r"\b(nonfarm|payrolls|nfp|jobs report|unemployment)\b", re.I)),
    ("US GDP", re.compile(r"\bgdp\b.*\b(grew|shrank|annualized|quarter|%|percent|data)\b", re.I)),
    ("RBI policy", re.compile(r"\b(rbi|reserve bank of india)\b.*\b(rate|repo|policy|mpc)\b", re.I)),
    ("ECB policy", re.compile(r"\b(ecb|european central bank|lagarde)\b.*\b(rate|policy|cut|hike)\b", re.I)),
]
# a "landed" print usually carries a number/verb; require this to cut pre-event chatter
LANDED = re.compile(r"\b(\d+(\.\d+)?%|\d+\s?bps|rose|fell|held|cut|hiked|raised|kept|"
                    r"unchanged|beats?|misses?|higher|lower|data shows?|reported)\b", re.I)

QUERIES = [
    "Federal Reserve interest rate decision",
    "US CPI inflation report",
    "US jobs report nonfarm payrolls",
    "US GDP growth data",
    "RBI monetary policy repo rate",
    "ECB interest rate decision",
]
# stopwords: filler + the macro-generic terms Tier-1 ALREADY captures. A `touches`
# hit should surface a DISTINCTIVE position entity (e.g. bitcoin, powell, recession),
# not a word like "rate" that appears in half the book — otherwise the tag misleads.
STOP = set((
    "the a an of for and or to in on is are will vs winner primary market who what when "
    "2026 2025 be this that with from into over under next "
    # macro-generic (Tier-1 already pages these) — exclude so they don't fake a position hit
    "interest rate rates decision report policy federal reserve fed data growth jobs "
    "inflation cut cuts hike hikes hiked unchanged steady higher lower year years month "
    "months june july august expectations live updates update powell meeting forecast "
    "quarter annualized percent economy economic bank central repo mpc gdp cpi pce nfp "
    "payrolls unemployment ecb rbi lagarde week weekly monthly u.s us"
).split())


def _get(url, timeout=20):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _gnews(q):
    from urllib.parse import quote
    return "https://news.google.com/rss/search?q=" + quote(q) + "&hl=en-US&gl=US&ceid=US:en"


def _parse_feed(url):
    try:
        root = ET.fromstring(_get(url))
    except Exception:
        return []
    out = []
    for item in root.iter():
        if item.tag.split("}")[-1] not in ("item", "entry"):
            continue
        title = link = pub = None
        for ch in item:
            ctag = ch.tag.split("}")[-1]
            if ctag == "title":
                title = (ch.text or "").strip()
            elif ctag == "link":
                link = (ch.text or "").strip() or ch.attrib.get("href", "")
            elif ctag in ("pubDate", "published", "updated"):
                pub = (ch.text or "").strip()
        if not title or not link:
            continue
        epoch = None
        if pub:
            try:
                epoch = int(parsedate_to_datetime(pub).timestamp())
            except Exception:
                epoch = None
        out.append((html.unescape(re.sub(r"\s+", " ", title)), link, epoch))
    return out


def _position_keywords():
    """Harvest distinctive tokens from OPEN Polymarket position titles across book state
    files, so a landed print that names one of them pages as a position-specific hit."""
    kw = set()
    for pat in BOOK_STATE_GLOBS:
        for fn in glob.glob(os.path.join(HERE, pat)):
            try:
                s = json.dumps(json.load(open(fn)))
            except Exception:
                continue
            for t in re.findall(r'"(?:question|title|market)"\s*:\s*"([^"]{10,120})"', s):
                for w in re.findall(r"[A-Za-z][A-Za-z\-\.]{3,}", t.lower()):
                    if w not in STOP:
                        kw.add(w)
    return kw


def _tier1(title):
    for label, rx in TIER1:
        if rx.search(title):
            return label
    return None


def _load_state():
    try:
        return json.load(open(STATE))
    except Exception:
        return {"seen": []}


def _save_state(st):
    st["seen"] = st["seen"][-6000:]
    tmp = STATE + ".tmp"
    json.dump(st, open(tmp, "w"))
    os.replace(tmp, STATE)


def _append_log(rec):
    with open(LOG, "a") as f:
        f.write(json.dumps(rec) + "\n")


def alert(msg):
    subprocess.run(["osascript", "-e",
                    f'display notification "{msg}" with title "Macro PRINT"'],
                   capture_output=True)
    for t in ALERT_TARGETS:
        subprocess.run(["osascript", "-e",
                        f'tell application "Messages" to send "{msg}" to buddy "{t}" '
                        'of (service 1 whose service type is iMessage)'],
                       capture_output=True)


def run_once():
    st = _load_state()
    seen = set(st["seen"])
    now = int(time.time())
    poskw = _position_keywords()
    hits, alerted = 0, 0
    for q in QUERIES:
        for title, link, epoch in _parse_feed(_gnews(q)):
            if link in seen:
                continue
            seen.add(link)
            tier = _tier1(title)
            if not tier:
                continue                          # not a macro print we track
            if not LANDED.search(title):
                continue                          # pre-event chatter, not a landed result
            # position-specific match?
            toks = set(re.findall(r"[A-Za-z][A-Za-z\-\.]{3,}", title.lower()))
            pos_match = sorted(toks & poskw)[:4]
            rec = {"t": now, "pub": epoch, "tier": tier, "title": title[:240],
                   "link": link, "pos_match": pos_match}
            _append_log(rec)
            hits += 1
            if alerted < 6:                       # cap pages/run
                tag = tier + (f" · touches: {', '.join(pos_match)}" if pos_match else "")
                alert(f"[{tag}] {title[:150]}")
                alerted += 1
    st["seen"] = list(seen)
    _save_state(st)
    write_report()
    print(f"macro_print once: {hits} landed prints, {alerted} paged, "
          f"{len(poskw)} position keywords -> {VAULT}")


def _read_log():
    rows = []
    try:
        for line in open(LOG):
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    except FileNotFoundError:
        pass
    return rows


def write_report():
    rows = _read_log()
    rows.sort(key=lambda r: r["t"], reverse=True)
    os.makedirs(os.path.dirname(VAULT), exist_ok=True)
    lines = [
        "# Macro Prints — landed releases touching the book",
        "",
        "type: report",
        "status: intel",
        "",
        f"_Keyless news. {len(rows)} landed prints logged. A headline-detected print is a "
        "heads-up to LOOK, not a trade signal — the wire usually lags the tape. Tier-1 "
        "prints page (crypto/rates exposure); position-name matches page as hits._",
        "",
        "| when | tier | touches | headline |",
        "| --- | --- | --- | --- |",
    ]
    for r in rows[:60]:
        when = datetime.fromtimestamp(r["t"]).strftime("%m-%d %H:%M")
        touch = ", ".join(r.get("pos_match", [])) or "—"
        title = r["title"].replace("|", "\\|")
        lines.append(f"| {when} | {r['tier']} | {touch} | [{title}]({r['link']}) |")
    lines.append("")
    tmp = VAULT + ".tmp"
    open(tmp, "w").write("\n".join(lines))
    os.replace(tmp, VAULT)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "once"
    if cmd == "report":
        write_report()
        print(f"macro_print report: {len(_read_log())} prints -> {VAULT}")
    else:
        run_once()


if __name__ == "__main__":
    main()
