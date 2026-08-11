#!/usr/bin/env python3
"""
fii_dii_logger.py — autonomous, KEYLESS FII/DII daily-flow logger for Indian markets.

Feeds the `fii-dii-flow-tracker` Claude skill: this script ACCUMULATES the data series
(append-only, dedup by date) so the skill can interpret it on demand without re-scraping.
Source: NSE public endpoint (no key, browser headers). Fail-soft: any error → no write,
non-zero log line, never fabricates. accumulate-don't-trade: logs data, never a signal.

Outputs:
  - fii_dii_log.jsonl            (append-only history, dedup by date)
  - PolymarketVault/Reports/FII-DII Flows.md   (Obsidian mirror, latest + 10-day table)
"""
import json, os, sys, urllib.request
from datetime import datetime, timezone

HERE   = os.path.dirname(os.path.abspath(__file__))
LOG    = os.path.join(HERE, "fii_dii_log.jsonl")
VAULT  = os.path.expanduser("~/Documents/PolymarketVault/Reports/FII-DII Flows.md")
URL    = "https://www.nseindia.com/api/fiidiiTradeReact"
HDRS   = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
          "Accept": "application/json", "Referer": "https://www.nseindia.com/"}

def fetch():
    req = urllib.request.Request(URL, headers=HDRS)
    data = json.loads(urllib.request.urlopen(req, timeout=15).read())
    out = {}
    for row in data:
        cat = "FII" if "FII" in row.get("category", "") else ("DII" if "DII" in row.get("category","") else None)
        if cat:
            out["date"] = row["date"]
            out[f"{cat}_buy"]  = float(row["buyValue"])
            out[f"{cat}_sell"] = float(row["sellValue"])
            out[f"{cat}_net"]  = float(row["netValue"])
    if "date" not in out or "FII_net" not in out:
        raise ValueError("unexpected payload shape")
    out["fetched_at"] = datetime.now(timezone.utc).isoformat()
    return out

def load_hist():
    if not os.path.exists(LOG):
        return []
    # Read the whole file in ONE call, then parse in memory. Iterating
    # `for ln in open(LOG)` makes the icloud_edeadlk shim retry PER readline,
    # so a single line caught under a sustained CloudDocs sync lock exhausts
    # the retry budget and re-raises Errno 11 (observed crash). A single read()
    # is one retry-wrapped syscall with a far smaller lock window — and the
    # `with` closes the handle immediately (the old open() leaked it).
    rows = []
    with open(LOG) as f:
        data = f.read()
    for ln in data.splitlines():
        ln = ln.strip()
        if ln:
            try: rows.append(json.loads(ln))
            except Exception: pass
    return rows

def write_vault(hist):
    try:
        os.makedirs(os.path.dirname(VAULT), exist_ok=True)
        latest = hist[-1]
        rows = "".join(
            f"| {r['date']} | {r.get('FII_net',0):+,.0f} | {r.get('DII_net',0):+,.0f} |\n"
            for r in hist[-10:][::-1])
        note = (f"---\ntype: dashboard\nstatus: live\n---\n\n# 🇮🇳 FII/DII Flows (₹ cr, NSE cash)\n\n"
                f"*Auto-logged by fii_dii_logger.py · keyless NSE feed · "
                f"latest {latest['date']}. Data only — NOT a signal (must clear the brain gate). "
                f"Interpret via the `fii-dii-flow-tracker` skill.*\n\n"
                f"**Latest ({latest['date']}):** FII net **{latest.get('FII_net',0):+,.0f}** · "
                f"DII net **{latest.get('DII_net',0):+,.0f}**\n\n"
                f"| Date | FII net | DII net |\n|--|--|--|\n{rows}")
        open(VAULT, "w").write(note)
    except Exception as e:
        print(f"vault write failed: {e}", file=sys.stderr)

if __name__ == "__main__":
    try:
        rec = fetch()
    except Exception as e:
        print(f"{datetime.now(timezone.utc).isoformat()} FETCH FAILED: {e}", file=sys.stderr)
        sys.exit(1)
    # File I/O below (load_hist / append / write_vault) touches ~/Documents,
    # so a sustained iCloud CloudDocs sync lock can still raise EDEADLK past
    # the retry shim. Degrade that ONE cycle to exit 0 (the data appends next
    # run once sync settles) rather than tripping the dead-job alarm. Any other
    # error still crashes loudly.
    try:
        hist = load_hist()
        if any(r.get("date") == rec["date"] for r in hist):
            print(f"{rec['date']} already logged — no append")
        else:
            with open(LOG, "a") as f:
                f.write(json.dumps(rec) + "\n")
            hist.append(rec)
            print(f"logged {rec['date']}: FII net {rec.get('FII_net'):+.0f} | DII net {rec.get('DII_net'):+.0f}")
        write_vault(hist)
    except OSError as e:
        import errno as _errno
        if getattr(e, "errno", None) == _errno.EDEADLK:
            print("degraded: iCloud sync lock (EDEADLK); skipped this cycle, exit 0", file=sys.stderr)
            sys.exit(0)
        raise
    sys.exit(0)
