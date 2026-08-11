#!/usr/bin/env python3
"""
notion_trades_sync.py — mirror the bot's open positions + resolved trades into Notion.

Reads scalp_lab_state.json (same source as obsidian_resolved_trades.py) and
pushes to two Notion data sources under the "Trading" page:
  - Resolved Trades: append-only, watermarked by closed_at (never re-pushes a row)
  - Open Positions: reconciled — creates newly-opened rows, archives ones that closed

Requires NOTION_API_KEY in ~/Documents/polymarket/.env (a Notion internal
integration token, shared with the "Trading" page). If absent, this script
logs a message and exits 0 (fail-soft — no key yet is not an error).

Atomic local watermark file (tmp + os.replace), keyless besides the Notion
call itself, $0. PAPER ONLY — read-only on bot state, only writes to Notion.
"""

import json, os, sys, urllib.request, urllib.error
from datetime import datetime, timezone

STATE = os.path.expanduser("~/Documents/polymarket/scalp_lab_state.json")
ENV = os.path.expanduser("~/Documents/polymarket/.env")
SYNC_STATE = os.path.expanduser("~/Documents/polymarket/notion_sync_state.json")

NOTION_VERSION = "2025-09-03"
API = "https://api.notion.com/v1"

RESOLVED_DATA_SOURCE_ID = "24010f3b-13aa-486b-b33b-15216f5d071d"
OPEN_DATA_SOURCE_ID = "2df6babf-28ec-47c7-8b14-a840c905622d"


def _load_env_key(name):
    if not os.path.exists(ENV):
        return None
    for line in open(ENV):
        line = line.strip()
        if line.startswith(f"{name}="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def _dt(s):
    try:
        return datetime.fromisoformat((s or "").replace("Z", "+00:00"))
    except Exception:
        return None


def _req(method, path, token, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{API}{path}", data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Notion-Version", NOTION_VERSION)
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read())


def collect(state):
    resolved, open_pos = [], []
    for leg, cfg in (state or {}).items():
        if not isinstance(cfg, dict):
            continue
        for ex in cfg.get("closed", []) or []:
            if not isinstance(ex, dict) or (ex.get("reason") or "").lower() != "resolved":
                continue
            resolved.append({**ex, "leg": leg})
        for op in cfg.get("open", []) or []:
            if not isinstance(op, dict):
                continue
            open_pos.append({**op, "leg": leg})
    resolved.sort(key=lambda r: r.get("closed_at") or "")
    return resolved, open_pos


def push_resolved(token, resolved, watermark):
    wm_dt = _dt(watermark)
    new = [r for r in resolved if not wm_dt or (_dt(r.get("closed_at")) and _dt(r.get("closed_at")) > wm_dt)]
    pushed = 0
    for r in new:
        entry, exitp = r.get("entry_fill"), r.get("exit_fill")
        pnl = float(r.get("pnl_usdc") or 0.0)
        closed_at = r.get("closed_at")
        props = {
            "Market": {"title": [{"text": {"content": (r.get("q") or "")[:200]}}]},
            "Leg": {"select": {"name": r.get("leg") if r.get("leg") else "other"}},
            "Side": {"select": {"name": r.get("side") or "YES"}},
            "PnL USDC": {"number": pnl},
            "Result": {"select": {"name": "win" if pnl > 0 else ("loss" if pnl < 0 else "unknown")}},
        }
        if entry is not None:
            props["Entry"] = {"number": float(entry)}
        if exitp is not None:
            props["Exit"] = {"number": float(exitp)}
        if closed_at:
            props["Closed At"] = {"date": {"start": closed_at}}
        try:
            _req("POST", "/pages", token, {
                "parent": {"type": "data_source_id", "data_source_id": RESOLVED_DATA_SOURCE_ID},
                "properties": props,
            })
            pushed += 1
        except urllib.error.HTTPError as e:
            sys.stderr.write(f"resolved push failed for {r.get('q')!r}: {e.read().decode()[:300]}\n")
            break  # stop on first failure so watermark doesn't skip ahead past it
    new_watermark = new[pushed - 1]["closed_at"] if pushed else watermark
    return pushed, new_watermark


def _open_key(op):
    return op.get("id") or f"{op.get('leg')}|{op.get('q')}|{op.get('opened_at')}"


def push_open(token, open_pos, known_pages):
    live_keys = {_open_key(op) for op in open_pos}
    # archive pages for positions no longer open
    archived = 0
    for key, page_id in list(known_pages.items()):
        if key not in live_keys:
            try:
                _req("PATCH", f"/pages/{page_id}", token, {"in_trash": True})
            except urllib.error.HTTPError:
                pass
            del known_pages[key]
            archived += 1
    # create pages for newly-open positions
    created = 0
    for op in open_pos:
        key = _open_key(op)
        if key in known_pages:
            continue
        props = {
            "Market": {"title": [{"text": {"content": (op.get("q") or "")[:200]}}]},
            "Leg": {"rich_text": [{"text": {"content": op.get("leg") or ""}}]},
            "Side": {"select": {"name": op.get("side") or "YES"}},
            "Status": {"select": {"name": "open"}},
        }
        if op.get("entry_fill") is not None:
            props["Entry Fill"] = {"number": float(op["entry_fill"])}
        if op.get("size") is not None:
            props["Size"] = {"number": float(op["size"])}
        if op.get("opened_at"):
            props["Opened At"] = {"date": {"start": op["opened_at"]}}
        try:
            resp = _req("POST", "/pages", token, {
                "parent": {"type": "data_source_id", "data_source_id": OPEN_DATA_SOURCE_ID},
                "properties": props,
            })
            known_pages[key] = resp["id"]
            created += 1
        except urllib.error.HTTPError as e:
            sys.stderr.write(f"open push failed for {op.get('q')!r}: {e.read().decode()[:300]}\n")
    return created, archived


def main():
    token = os.environ.get("NOTION_API_KEY") or _load_env_key("NOTION_API_KEY")
    if not token:
        print("no NOTION_API_KEY in env/.env yet — skipping Notion sync (fail-soft, not an error)")
        return 0
    if not os.path.exists(STATE):
        sys.stderr.write(f"no state file at {STATE}\n")
        return 1

    state = json.load(open(STATE))
    resolved, open_pos = collect(state)

    sync = {}
    if os.path.exists(SYNC_STATE):
        try:
            sync = json.load(open(SYNC_STATE))
        except Exception:
            sync = {}
    watermark = sync.get("last_resolved_closed_at")
    known_pages = sync.get("open_position_pages") or {}

    pushed, new_watermark = push_resolved(token, resolved, watermark)
    created, archived = push_open(token, open_pos, known_pages)

    sync["last_resolved_closed_at"] = new_watermark
    sync["open_position_pages"] = known_pages
    sync["updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    tmp = SYNC_STATE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(sync, f, indent=2)
    os.replace(tmp, SYNC_STATE)

    print(f"notion sync: +{pushed} resolved, +{created}/-{archived} open positions")
    return 0


if __name__ == "__main__":
    sys.exit(main())
