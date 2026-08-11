#!/usr/bin/env python3
"""
insider_finder.py — autonomous insider-hunting pipeline. PAPER RESEARCH ONLY.
Designed to run 24/7 via the insider-finder scheduled task (every 6h).

An "insider" here = a wallet whose edge looks INFORMATION-driven rather than
mechanical. Mechanical winners (set-arb, laddering, latency bots) are common;
information traders are rare and valuable to watch. Heuristic (all required):

  1. Profitable:   realized P&L > $1k AND win rate >= 60% at >= 20 closed
  2. Not machinery: cadence is HUMAN/HYBRID (median gap > 60s) — pure-bot
                    cadence means latency/structure, not information
  3. Event-driven:  >= 40% of positions in politics/world/news categories
                    (insiders per the literature concentrate there, NOT
                    esports/crypto coinflips)
  4. Conviction:    p90 bet >= 3x median bet (information traders size up
                    when they know something)

Pipeline per run:
  a. Discover candidates: live market sweep (wallet_scanner.fetch_wallets)
     + onchain_sharp_candidates.json (on-chain backfill harvest)
  b. Skip wallets already watched or already rejected (.insider_rejected.json)
  c. Profile each candidate: positions (P&L/WR/categories) + trade cadence
  d. Flag qualifiers -> auto-add to insider_wallets.json as INSIDER-cand|HYBRID
     (watcher reloads the list each cycle and starts alerting within 60s)
  e. iMessage a summary when a new candidate is found. Silence = nothing found.

Run: python3 insider_finder.py [max_candidates_per_run=25]
"""
import fcntl, json, os, re, sys, time, urllib.request
from collections import Counter

BASE     = os.path.expanduser("~/Documents/polymarket")
WATCH_F  = os.path.join(BASE, "insider_wallets.json")
ELITE_F  = os.path.join(BASE, "insider_wallets_elite.json")   # WR>79% only
REJECT_F = os.path.join(BASE, ".insider_rejected.json")
CAND_F   = os.path.join(BASE, "onchain_sharp_candidates.json")
LOG_F    = os.path.join(BASE, "insider_finder_log.json")
VAULT_F  = os.path.expanduser("~/Documents/PolymarketVault/Reports/insider_wallets.md")
LOCK_F   = os.path.join(BASE, ".insider_finder.lock")
UA       = {"User-Agent": "Mozilla/5.0"}
MAX_CAND = int(sys.argv[1]) if len(sys.argv) > 1 else 25


def _atomic_dump(obj, path, indent=1):
    """Crash/race-safe write: serialize to a tmp file, then atomic os.replace.
    Prevents the truncate-on-open corruption two overlapping runs caused."""
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=indent)
    os.replace(tmp, path)


def tier(wr):
    """Win-rate colour tier. Returns (dot, name) or (None, None) if below floor."""
    if wr > 79:  return "🟢", "ELITE"   # also written to insider_wallets_elite.json
    if wr >= 70: return "🟡", "HIGH"
    if wr >= 60: return "🟠", "MID"
    return None, None

EVENT_KW = ("election", "president", "senate", "congress", "trump", "nominee",
            "governor", "minister", "ceasefire", "war", "invasion", "sanction",
            "israel", "iran", "russia", "ukraine", "nato", "tariff", "fed ",
            "nobel", "impeach", "indict", "resign", "blockade", "treaty")


def _get(url):
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except Exception:
        return None


def _load(path, default):
    try:
        return json.load(open(path))
    except Exception:
        return default


def discover():
    """Candidate addresses from live top-market sweep + on-chain harvest."""
    cands = set()
    cands |= set(_load(CAND_F, {}).keys())
    # live sweep: wallets active in current top markets (reuses wallet_scanner)
    try:
        sys.path.insert(0, BASE)
        import wallet_scanner as ws
        for cid in (ws.fetch_top_market_ids(30) or [])[:30]:
            rows = _get(f"https://data-api.polymarket.com/trades?conditionId={cid}&limit=200") or []
            for t in rows:
                a = (t.get("proxyWallet") or t.get("user") or "").lower()
                if a.startswith("0x"):
                    cands.add(a)
    except Exception as e:
        print(f"[discover] live sweep failed: {e}")
    return cands


def profile(addr):
    """(passes, why, label, is_elite) — win-% tiered screen.

    Accepts on win-rate (>=60% over >=20 closed, profitable). Event-share,
    cadence and conviction sizing are NO LONGER hard gates — they're surfaced
    as informational flags so a high-win-% wallet isn't silently dropped for
    trading sports or sizing flat. FAVPAD flag marks wallets whose win-rate
    comes from buying near-certain favorites (avg entry >= 0.80) = high WR,
    little real edge (the favorite-padding trap), so you can tell them apart.
    """
    pos = _get(f"https://data-api.polymarket.com/positions?user={addr}&limit=500") or []
    if len(pos) < 20:
        return False, "n<20 positions", None, False
    real = sum(float(p.get("realizedPnl") or 0) for p in pos)
    wins = sum(1 for p in pos if float(p.get("realizedPnl") or 0) > 0)
    losses = sum(1 for p in pos if float(p.get("realizedPnl") or 0) < 0)
    closed = wins + losses
    if closed < 20:
        return False, f"closed {closed} < 20", None, False
    if real <= 0:
        return False, f"unprofitable (${real:,.0f})", None, False
    wr = 100 * wins / closed
    dot, tname = tier(wr)
    if not tname:
        return False, f"WR {wr:.0f}% < 60", None, False

    # informational flags (do NOT reject) ----------------------------------
    n_event = sum(1 for p in pos
                  if any(k in (p.get("title") or "").lower() for k in EVENT_KW))
    event_pct = 100 * n_event / len(pos)
    # favorite-padding: a high win-rate earned by buying near-certain
    # favorites. Measured on WINNING positions only — if the wins came in at
    # avg entry >= 0.80, the WR is padding (little real edge), not calls.
    win_prices = [float(p.get("avgPrice") or 0) for p in pos
                  if float(p.get("realizedPnl") or 0) > 0 and float(p.get("avgPrice") or 0) > 0]
    win_entry = sum(win_prices) / len(win_prices) if win_prices else 0.0
    favpad = win_entry >= 0.80
    is_elite = wr > 79

    flags = []
    if favpad:          flags.append("FAVPAD")
    if event_pct >= 40: flags.append("EVENT")
    flag_s = ("|" + "|".join(flags)) if flags else ""
    label = f"{dot}WR{wr:.0f}%-${real/1000:.0f}k-{tname}{flag_s}"
    why = (f"WR {wr:.0f}% n={closed}, +${real:,.0f}, win-entry {win_entry:.2f}"
           f"{' FAVORITE-PADDING' if favpad else ''}, {event_pct:.0f}% event mkts")
    return True, why, label, is_elite


def _dollars(label):
    m = re.search(r"\$(-?\d+)k", label or "")
    return int(m.group(1)) if m else 0


def write_vault_report(watched, elite):
    """Mirror the colour-tiered watchlist into Obsidian every run. The file's
    timestamp doubles as a stall-detector: stale report == discovery stopped."""
    tiers = {"🟢": [], "🟡": [], "🟠": [], "⚪": []}
    legacy = []
    for a, l in watched.items():
        d = (l or " ")[0]
        (tiers[d] if d in tiers else legacy).append((a, l))
    now = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
    names = {"🟢": "ELITE (>79% win rate)", "🟡": "HIGH (70-79%)", "🟠": "MID (60-70%)",
             "⚪": "LOW (<60%, slipped)"}
    out = [
        "---", "type: report", "status: live", f"date: {time.strftime('%Y-%m-%d')}",
        "tags: [insider, wallets, watchlist]", "---", "",
        "# 🕵️ Insider Wallet Watchlist (win-% tiered)",
        f"_Updated: **{now}** · auto every 10 min via `com.aryan.polymarket-insider-finder`._",
        f"_If this timestamp is stale, discovery has stalled — kickstart the launchd job._", "",
        f"**{len(watched)} watched** · 🟢 {len(tiers['🟢'])} elite · 🟡 {len(tiers['🟡'])} · "
        f"🟠 {len(tiers['🟠'])} · ⚪ {len(tiers['⚪'])} · legacy {len(legacy)}", "",
        "> FAVPAD = high win-rate from buying near-certain favorites (padding, not edge). "
        "WR≈100% is partly inflated (losers redeem to $0). Vet before trusting — copying loses OOS.", "",
    ]
    for dot in ("🟢", "🟡", "🟠", "⚪"):
        rows = sorted(tiers[dot], key=lambda x: _dollars(x[1]), reverse=True)
        if not rows:
            continue
        out.append(f"## {dot} {names[dot]} — {len(rows)}")
        out.append("| wallet | flag |")
        out.append("|---|---|")
        for a, l in rows:
            out.append(f"| `{a}` | {l} |")
        out.append("")
    if legacy:
        out.append(f"## ⬜ legacy / unprofilable (kept) — {len(legacy)}")
        out.append(", ".join(f"`{a[:10]}…`" for a, _ in legacy[:40]))
        out.append("")
    try:
        os.makedirs(os.path.dirname(VAULT_F), exist_ok=True)
        tmp = VAULT_F + ".tmp"
        open(tmp, "w").write("\n".join(out))
        os.replace(tmp, VAULT_F)   # atomic
    except Exception as e:
        print(f"[vault] {e}")


def main():
    # single-instance lock: if a prior run (or a kickstart-restarted one) is
    # still going, skip this cycle rather than race it and corrupt the JSON.
    lockf = open(LOCK_F, "w")
    try:
        fcntl.flock(lockf, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("another insider_finder instance holds the lock; skipping this cycle")
        return
    # lock auto-releases when this process exits

    watched  = _load(WATCH_F, {})
    elite    = _load(ELITE_F, {})
    rejected = _load(REJECT_F, {})
    cands = [a for a in discover() if a not in watched and a not in rejected]
    print(f"{len(cands)} unseen candidates; profiling up to {MAX_CAND}")
    found = []
    for addr in cands[:MAX_CAND]:
        ok, why, label, is_elite = profile(addr)
        print(f"  {'PASS' if ok else 'rej '} {addr[:10]}… {why}")
        if ok:
            watched[addr] = label
            if is_elite:
                elite[addr] = label
            found.append((addr, label, why))
        else:
            rejected[addr] = {"why": why, "ts": time.strftime("%Y-%m-%d")}
        time.sleep(1.0)

    _atomic_dump(rejected, REJECT_F, indent=1)
    if found:
        _atomic_dump(watched, WATCH_F, indent=2)
        _atomic_dump(elite, ELITE_F, indent=2)
        try:
            import insider_watch as iw
            body = (f"🕵️ INSIDER FINDER: {len(found)} new candidate(s) auto-watched\n\n" +
                    "\n\n".join(f"{a[:10]}… {l}\n{w}" for a, l, w in found))
            iw.send_imessage(body)
        except Exception as e:
            print(f"[alert] {e}")
    # mirror to Obsidian EVERY run (fresh timestamp = autonomous stall-detector)
    write_vault_report(watched, elite)

    log = _load(LOG_F, [])
    log.append({"ts": time.strftime("%Y-%m-%dT%H:%M"), "checked": len(cands[:MAX_CAND]),
                "found": len(found), "watchlist": len(watched), "elite": len(elite)})
    _atomic_dump(log[-200:], LOG_F, indent=1)
    print(f"done: {len(found)} found, watchlist now {len(watched)}, "
          f"elite(WR>79%) {len(elite)}, rejected pool {len(rejected)}")


if __name__ == "__main__":
    main()
