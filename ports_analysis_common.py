#!/usr/bin/env python3
"""ports_analysis_common.py — shared helpers for the ports-terminal analysis loggers.

READ-ONLY. These loggers analyze the Bloomberg-style ports terminal snapshot
(ports_data.json, produced by gen_ports.py over live scalp_lab state + price_snap +
on-chain heatmap) and append an append-only JSONL time series, then self-export an
Obsidian Reports/<name>.md note. Paper-only, no trades, no funds, stdlib only.
See latticesnap_logger.py for the established logger idiom.

Usage from a logger:
    import ports_analysis_common as C
    d, age = C.load_ports()
    if d is None: ...                          # snapshot missing -> skip cleanly
    C.append_jsonl("legpnl", rec, dedup_key="gen")
    C.write_report("legpnl", lines)
"""
import os, json, time, fcntl
from datetime import datetime, timezone

POLY = os.path.dirname(os.path.abspath(__file__))
PORTS_DATA = os.path.expanduser("~/Documents/Codex/2026-05-28/claud/ports_data.json")
GEN_SCRIPT = os.path.expanduser("~/Documents/Codex/2026-05-28/claud/gen_ports.py")
REPORTS = os.path.expanduser("~/Documents/PolymarketVault/Reports")
MAX_LOG_LINES = 3000   # keep-last-N cap per *_log.jsonl — bounds unbounded growth (cf. moves_log.jsonl @ 1.98GB)

# the marking-honesty controls (hard rule: these MUST stay negative; positive => marking bug)
CONTROLS = ("allin", "coinflip", "microscalp", "coindown", "windowshutrand")

# legs BRAIN has retracted/killed — surface them when the naive screen "passes" one (Lesson 13)
RETRACTED = {
    "nearres": "RETRACTED 2026-06-14 — gap-through artifact; clean stops fill 45-65c below trigger",
    "truefade": "KILLED 2026-06-10 — 6% WR, long-dated politics never moved in 72h",
}


def now():
    return time.time()


def ts_iso(t=None):
    return datetime.fromtimestamp(t if t is not None else time.time(), timezone.utc).isoformat()[:19] + "Z"


def load_ports():
    """Load the terminal snapshot. Returns (data_dict, age_seconds) or (None, None).
    The wrapper (run_analysis_loggers.sh) refreshes ports_data.json before the loggers run,
    so a logger run reflects current marks; load is tolerant of a stale/missing file."""
    if not os.path.exists(PORTS_DATA):
        return None, None
    try:
        age = time.time() - os.path.getmtime(PORTS_DATA)
        return json.load(open(PORTS_DATA)), age
    except Exception:
        return None, None


def _last_record(path):
    """Read ONLY the last json record from a jsonl file — O(1) tail-read, not a full-file parse."""
    if not os.path.exists(path):
        return None
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            pos = f.tell()
            buf = b""
            while pos > 0:
                step = min(4096, pos)
                pos -= step
                f.seek(pos)
                buf = f.read(step) + buf
                if buf.count(b"\n") >= 2:
                    break
            for ln in reversed(buf.split(b"\n")):
                ln = ln.strip()
                if ln:
                    try:
                        return json.loads(ln)
                    except Exception:
                        continue
    except Exception:
        return None
    return None


def append_jsonl(name, rec, dedup_key=None):
    """Append rec to <name>_log.jsonl. ATOMIC across processes: the whole check-and-append runs
    under an flock on a sidecar .lock file, because the three cadences (serve.sh 60s, crypto-tracking
    15min, obsidian 6h) can fire simultaneously. If dedup_key is given and the last record already
    has the same value for that key, the append is SKIPPED — race-safe, since a concurrent second
    writer sees the first's row under the lock and skips. Also caps the file at MAX_LOG_LINES so the
    logs can't grow unbounded. Returns True if a line was written, False if deduped."""
    path = os.path.join(POLY, name + "_log.jsonl")
    with open(path + ".lock", "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        if dedup_key is not None:
            last = _last_record(path)
            if last and last.get(dedup_key) == rec.get(dedup_key):
                return False
        with open(path, "a") as f:
            f.write(json.dumps(rec) + "\n")
        # keep-last-N cap (only scan once a file is non-trivial; os.replace is atomic for readers)
        try:
            if os.path.getsize(path) > 2_000_000:
                with open(path) as fr:
                    lines = fr.readlines()
                if len(lines) > MAX_LOG_LINES:
                    tmp = path + ".tmp"
                    with open(tmp, "w") as fw:
                        fw.writelines(lines[-MAX_LOG_LINES:])
                    os.replace(tmp, path)
        except Exception:
            pass
        return True


def last_record(name):
    """Public O(1) tail-read of the last record in <name>_log.jsonl (no full-file parse)."""
    return _last_record(os.path.join(POLY, name + "_log.jsonl"))


def read_jsonl(name, limit=None):
    path = os.path.join(POLY, name + "_log.jsonl")
    if not os.path.exists(path):
        return []
    rows = []
    for line in open(path):
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            pass
    return rows[-limit:] if limit else rows


def write_report(name, lines):
    """Self-export the Obsidian note to PolymarketVault/Reports/<name>.md (latticesnap idiom)."""
    os.makedirs(REPORTS, exist_ok=True)
    open(os.path.join(REPORTS, name + ".md"), "w").write("\n".join(lines) + "\n")


def load_json(filename):
    """Read a sibling polymarket JSON file (e.g. analyst.json, scalp_lab_state.json)."""
    p = os.path.join(POLY, filename)
    if not os.path.exists(p):
        return None
    try:
        return json.load(open(p))
    except Exception:
        return None


def usd(x):
    x = x or 0
    if abs(x) < 0.005:          # rounds to $0.00 at 2dp -> no misleading +/- sign (handles -0.0)
        return "$0.00"
    return ("+" if x >= 0 else "-") + "$" + f"{abs(x):.2f}"


def group_by(positions, key):
    out = {}
    for p in positions:
        out.setdefault(p.get(key) or "?", []).append(p)
    return out
