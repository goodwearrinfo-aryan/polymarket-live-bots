#!/usr/bin/env python3
"""analyst_mtm_logger.py — READ-ONLY analysis logger (places NO trades). Marks the sourced
analyst-desk bets to market to track the only +EV track, AND surfaces WITHDRAWN bets so the
June-30 scoreboard does not condition on survival (Lesson 13: dropping the losers fakes an edge).
Paper-only, stdlib only.
Usage: python3 analyst_mtm_logger.py once | report"""
import sys, json, hashlib
import ports_analysis_common as C

NAME = "analyst_mtm"


def _mark(bet, ps):
    """Mark one analyst bet to market. cur/unreal are None when the market has no live price."""
    mid = bet.get("market_id")
    ym = ps.get(mid)
    if isinstance(ym, dict):                  # tolerate {yes_mid: ..} or plain-float price_snap shapes
        ym = ym.get("yes_mid")
    side = (bet.get("side") or "").upper()
    entry = bet.get("entry_price")
    cur = None if (ym is None or entry is None) else round(ym if side == "YES" else (1.0 - ym), 4)
    if cur is None or entry is None:
        unreal_pts = unreal_usd = None
    else:
        unreal_pts = round(cur - entry, 4)
        size = bet.get("size_usdc") or 0
        unreal_usd = round(size * (cur - entry) / entry, 2) if entry > 0 else None
    return {
        "id": bet.get("id"), "q": (bet.get("q") or "")[:60], "side": side, "entry": entry,
        "cur": cur, "unreal_pts": unreal_pts, "unreal_usd": unreal_usd,
        "edge_pts": bet.get("edge_pts"), "conviction": bet.get("conviction"),
        "research_prob_yes": bet.get("research_prob_yes"),
    }


def once():
    aj = C.load_json("analyst.json")
    cache = C.load_json("scalp_lab_cache.json")
    if aj is None or cache is None:
        print(NAME + ": analyst.json or scalp_lab_cache.json missing — skipped"); return
    ps = (cache or {}).get("price_snap", {})
    if not ps:
        print(NAME + ": no price_snap in scalp_lab_cache.json — skipped"); return

    bets = (aj or {}).get("open", []) or []
    rows = [_mark(b, ps) for b in bets]
    marked = [r for r in rows if r["unreal_usd"] is not None]
    total_unreal_usd = round(sum(r["unreal_usd"] for r in marked), 2)
    total_unreal_pts = round(sum(r["unreal_pts"] for r in marked), 4)

    # survivorship guard: withdrawn bets must be visible, not silently dropped (Lesson 13)
    withdrawn = (aj or {}).get("withdrawn", []) or []
    wrows = [{"id": w.get("id"), "side": (w.get("side") or "").upper(), "entry": w.get("entry_price"),
              "size_usdc": w.get("size_usdc"), "conviction": w.get("conviction"),
              "reason": w.get("withdraw_reason"), "withdrawn_at": (w.get("withdrawn_at") or "")[:19]}
             for w in withdrawn]

    # content fingerprint: log only when a live mark or the open/withdrawn set actually changes.
    # Stable md5 (NOT Python's per-process hash()) so the dedup matches across the 3 cadences' processes.
    fp = hashlib.md5(json.dumps([[r["id"], r["cur"]] for r in rows] + [len(withdrawn)],
                                sort_keys=True).encode()).hexdigest()
    rec = {
        "ts": C.ts_iso(), "fp": fp, "n": len(bets), "n_marked": len(marked),
        "total_unreal_usd": total_unreal_usd, "total_unreal_pts": total_unreal_pts,
        "n_withdrawn": len(withdrawn), "deployed_usdc": (aj or {}).get("deployed_usdc"),
        "capital_usdc": (aj or {}).get("capital_usdc"),
        "bets": rows, "withdrawn": wrows,
    }
    C.append_jsonl(NAME, rec, dedup_key="fp")
    report()
    print(NAME + ": " + str(len(bets)) + " open (" + str(len(marked)) + " priced) unreal=" +
          C.usd(total_unreal_usd) + " (" + f"{total_unreal_pts:+.4f}" + " pts) · " +
          str(len(withdrawn)) + " withdrawn — analyst track = only +EV path")


def report():
    recs = C.read_jsonl(NAME)
    last = recs[-1] if recs else None
    lines = [
        "# Analyst Desk — Mark-to-Market",
        "",
        "> READ-ONLY logger (no trades). Marks the sourced analyst-desk bets (analyst.json `open`) to live",
        "> price_snap, and lists WITHDRAWN bets so the scoreboard isn't survivorship-biased. The analyst",
        "> track is the ONLY +EV path; this feeds the June-30 review.",
        "> Updated " + C.ts_iso() + " · " + str(len(recs)) + " samples",
        "",
    ]
    if not last:
        lines += ["_No samples yet._"]; C.write_report(NAME, lines); return

    nm = last.get("n_marked", 0)
    lines += [
        "## Open bets — latest mark (" + str(last.get("ts")) + ")",
        "",
        "- open bets: **" + str(last.get("n")) + "** (" + str(nm) + " with a live mark)",
        "- total unrealized (priced only): **" + C.usd(last.get("total_unreal_usd")) + "**  (" +
        f"{(last.get('total_unreal_pts') or 0):+.4f}" + " pts)",
        "- deployed: " + C.usd(last.get("deployed_usdc")) + " of " + C.usd(last.get("capital_usdc")) + " capital",
        "",
        "| id | side | entry | cur | unreal_pts | unreal_usd | edge | conv |",
        "|----|------|------:|----:|-----------:|-----------:|-----:|-----:|",
    ]
    for r in last.get("bets", []):
        cur = "—" if r.get("cur") is None else f"{r['cur']:.4f}"
        upts = "—" if r.get("unreal_pts") is None else f"{r['unreal_pts']:+.4f}"
        uusd = "—" if r.get("unreal_usd") is None else C.usd(r["unreal_usd"])
        entry = "—" if r.get("entry") is None else f"{r['entry']:.4f}"
        edge = "—" if r.get("edge_pts") is None else f"{r['edge_pts']:+.1f}"
        conv = "—" if r.get("conviction") is None else f"{r['conviction']:.2f}"
        lines.append("| " + " | ".join([str(r.get("id")), str(r.get("side")), entry, cur, upts, uusd, edge, conv]) + " |")

    if nm <= 1:
        lines += ["", "> ⚠️ **n_marked=" + str(nm) + "** — this 'total' is " + ("one mark" if nm == 1 else "no marks") +
                  ", not a scoreboard yet. Unpriced bets (`—`) have no price_snap match and are excluded from the $ total (no fake fills)."]

    # survivorship guard
    wr = last.get("withdrawn", []) or []
    lines += ["", "## Withdrawn bets — survivorship guard (" + str(len(wr)) + ")", ""]
    if wr:
        lines += ["> These bets were withdrawn BEFORE resolution. The June-30 scoreboard reads only `open`,",
                  "> so it must NOT silently drop these — that's the reason==resolve / drop-the-losers pattern",
                  "> (Lesson 13) that faked truefade/nearres/FL-premium. Account for them (most were pulled for",
                  "> negative-EV or unverifiable reasons, not because they were losing — but count them, don't hide them).",
                  "",
                  "| id | side | entry | size | conviction | withdraw reason |",
                  "|----|------|------:|-----:|-----------:|------------------|"]
        for w in wr:
            lines.append("| " + str(w.get("id")) + " | " + str(w.get("side")) + " | " +
                         (f"{w['entry']:.3f}" if isinstance(w.get("entry"), (int, float)) else "—") + " | " +
                         (C.usd(w.get("size_usdc")) if isinstance(w.get("size_usdc"), (int, float)) else "—") + " | " +
                         (f"{w['conviction']:.2f}" if isinstance(w.get("conviction"), (int, float)) else "—") + " | " +
                         (str(w.get("reason") or "")[:80]) + " |")
    else:
        lines += ["_No withdrawn bets recorded._"]
    C.write_report(NAME, lines)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "once"
    if cmd == "once":
        once()
    elif cmd == "report":
        report()
    else:
        print("usage: analyst_mtm_logger.py once|report")


if __name__ == "__main__":
    main()
