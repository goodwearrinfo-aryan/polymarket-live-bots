#!/usr/bin/env python3
"""logger_headlines.py — compose a digest of the research loggers' notable findings
and send it via iMessage. READ-ONLY over the logger outputs; sends no trades.

Pulls the noteworthy items from the last DIGEST_H hours across all 8 loggers +
the country rollup, formats compact "headlines", and texts them (same proven
AppleScript-argv path as insider_watch). Only sends when there's something new.

Usage:
  python3 logger_headlines.py            # compose + send (if non-empty)
  python3 logger_headlines.py --dry      # print only, never send
"""
import os, sys, json, time, subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
try:
    from country_tag import country_of
except Exception:
    country_of = lambda t: "?"

TARGETS = ["krisharyan@icloud.com", "+918449447444"]
DIGEST_H = 24
SENTINEL = os.path.join(HERE, ".headlines_last")

_OSA = ('on run argv\n'
        'tell application "Messages"\n'
        '  set s to 1st service whose service type = iMessage\n'
        '  set b to buddy (item 1 of argv) of s\n'
        '  send (item 2 of argv) to b\n'
        'end tell\n'
        'end run')


def send_imessage(body):
    ok = True
    for t in TARGETS:
        try:
            r = subprocess.run(["osascript", "-e", _OSA, t, body], capture_output=True, text=True, timeout=30)
            if r.returncode != 0:
                ok = False; sys.stderr.write(f"[imsg FAIL] {t}: {r.stderr.strip()}\n")
        except Exception as e:
            ok = False; sys.stderr.write(f"[imsg FAIL] {t}: {e}\n")
    return ok


def _log(fn):
    p = os.path.join(HERE, fn)
    return [json.loads(l) for l in open(p)] if os.path.exists(p) else []


def _state(fn):
    p = os.path.join(HERE, fn)
    try:
        return json.load(open(p)) if os.path.exists(p) else {}
    except Exception:
        return {}


def _last(fn):
    """Last record of a jsonl — the ports analysis loggers are append-only; we only need the latest."""
    p = os.path.join(HERE, fn)
    if not os.path.exists(p):
        return None
    last = None
    try:
        for line in open(p):
            line = line.strip()
            if line:
                last = line
        return json.loads(last) if last else None
    except Exception:
        return None


def headlines(since):
    H = []
    def fresh(recs, tkey="ts"):
        return [r for r in recs if int(r.get(tkey, r.get("t0", r.get("finalized", 0)) or 0)) >= since]

    # sharpdrift — new vetted-wallet consensus (pending) + matured drift
    sd = _state("sharpdrift_state.json")
    npend = sum(1 for ev in sd.get("pending", {}).values() if int(ev.get("t0", 0)) >= since)
    if npend:
        ev = max(sd["pending"].values(), key=lambda e: e.get("t0", 0))
        H.append(f"🟢 sharpdrift: {npend} new consensus · {ev.get('n_wallets')}w {ev.get('outcome')}@{ev.get('mid0'):.2f} {(ev.get('title') or '')[:32]}")
    sdm = fresh(_log("sharpdrift_log.jsonl"), "finalized")
    for r in sdm:
        m48 = r.get("marks", {}).get("48")
        if m48 is not None and abs(m48 - r["mid0"]) >= 0.03:
            H.append(f"📈 sharpdrift matured: {(m48-r['mid0'])*100:+.0f}¢/48h {(r.get('title') or '')[:34]}")

    # whalexit — sharp exits
    wx = _state("whalexit_state.json")
    nx = sum(1 for ev in wx.get("pending", {}).values() if int(ev.get("t0", 0)) >= since)
    if nx:
        H.append(f"🔻 whalexit: {nx} sharp exit(s) logged (watching for decline)")

    # tapeshock — big news-quiet shocks
    ts = _state("tapeshock_state.json")
    for ev in ts.get("pending", {}).values():
        if int(ev.get("t0", 0)) >= since and ev.get("usd", 0) >= 5000:
            H.append(f"⚡ tapeshock: ${ev['usd']:,} {('BUY' if ev.get('side',1)>0 else 'SELL')} shock {(ev.get('title') or '')[:32]}")

    # favwatch — favorites that CRATERED (gap-through) at resolution
    for r in fresh(_log("favwatch_log.jsonl"), "t0"):
        if r.get("fav_won") is False:
            H.append(f"📉 favwatch: favorite {r.get('fav0'):.2f} LOST (gap) {(r.get('title') or '')[:32]}")

    # deadline — resolved take-NO outcomes
    dl = [r for r in fresh(_log("deadline_log.jsonl"), "t0") if r.get("takeno_pnl") is not None]
    if dl:
        paid = sum(1 for r in dl if "NO_paid" in r.get("outcome", ""))
        H.append(f"⏳ deadline: {len(dl)} resolved, {paid} take-NO paid")

    # overround — real executable Dutch books
    for r in fresh(_log("overround_log.jsonl")):
        if r.get("dutch_book"):
            H.append(f"⚠️ overround: REAL Dutch book ask-sum={r['sum_ask']} {(r.get('title') or '')[:30]}")

    # latticesnap — judged breaks with positive at-ask
    for r in fresh(_log("latticesnap_log.jsonl"), "t0"):
        if r.get("atask_net") and r["atask_net"] > 0.01:
            H.append(f"🪜 latticesnap: laggard paid {r['atask_net']*100:+.0f}¢ {(r.get('title') or '')[:30]}")

    # redeemdisc — any discount found
    for r in fresh(_log("redeemdisc_log.jsonl")):
        if r.get("discount", 0) >= 0.02:
            H.append(f"💰 redeemdisc: {r['discount']*100:.0f}¢ discount {(r.get('title') or '')[:30]}")

    # country rollup — hottest geography (from the breakdown file's data)
    allrecs = []
    for fn in ("favwatch_log.jsonl", "overround_log.jsonl", "sharpdrift_log.jsonl"):
        allrecs += _log(fn)
    if allrecs:
        from collections import Counter
        c = Counter(country_of(r.get("title") or r.get("event") or "") for r in allrecs)
        c.pop("Global", None); c.pop("Other", None)
        if c:
            top = c.most_common(1)[0]
            H.append(f"🌍 hottest geo: {top[0]} ({top[1]} events)")

    # --- ports analysis loggers: SIGNAL ONLY (edge candidate / band edge / marking bug / analyst +EV) ---
    # steady state (algo null) emits nothing, so the digest stays quiet unless something real happens.
    es = _last("edge_screen_log.jsonl")
    if es and es.get("n_pass", 0) > 0:
        H.append(f"🎯 edge_screen: {es['n_pass']} leg(s) clear bootstrap CI>0 — "
                 f"{', '.join(es.get('pass_names', []))} (VALIDATE via DSR before sizing)")
    bc = _last("bandcal_log.jsonl")
    if bc and bc.get("n_candidate_bands", 0) > 0:
        bands = ", ".join("[%.1f,%.1f)" % (b / 10, b / 10 + 0.1) for b in bc.get("candidate_bands", []))
        H.append(f"📊 bandcal: realized band edge {bands} (CI>0, non-control)")
    cl = _last("controls_log.jsonl")
    if cl and cl.get("any_positive"):
        offs = ", ".join(c.get("name") for c in cl.get("controls", []) if c.get("flag_positive"))
        H.append(f"🚨 controls: MARKING BUG — {offs} POSITIVE at n>={cl.get('min_n', 30)} "
                 f"(investigate marking, NOT an edge)")
    am = _last("analyst_mtm_log.jsonl")
    if am and (am.get("total_unreal_usd") or 0) > 0:
        H.append(f"🧠 analyst: track NET +EV — MTM ${am['total_unreal_usd']:+.2f} "
                 f"({am.get('n_marked', 0)}/{am.get('n', 0)} priced)")
    return H


def main():
    dry = "--dry" in sys.argv
    since = 0
    if os.path.exists(SENTINEL):
        try:
            since = int(open(SENTINEL).read().strip())
        except Exception:
            since = 0
    since = max(since, int(time.time()) - DIGEST_H * 3600)
    H = headlines(since)
    if not H:
        print("logger_headlines: nothing new to report")
        return
    body = "📊 Polymarket logger headlines\n" + time.strftime("%Y-%m-%d %H:%M", time.localtime()) + "\n\n" + "\n".join(H[:14])
    print(body)
    if dry:
        print("\n[--dry: not sent]")
        return
    if send_imessage(body):
        open(SENTINEL, "w").write(str(int(time.time())))
        print(f"\n[sent {len(H)} headlines via iMessage]")
    else:
        print("\n[iMessage send FAILED — see stderr]")


if __name__ == "__main__":
    main()
