#!/usr/bin/env python3
"""redeemdisc_logger.py — READ-ONLY logger (NOT a trading leg, places NO trades).

Question: does a POST-RESOLUTION REDEMPTION DISCOUNT actually exist on the live
book? After a market resolves on-chain, the WINNING token's redemption value is
$1.00 — but holders may dump it below $1 for instant liquidity rather than wait
to redeem. If so, buying the confirmed winner at its ask < $1 is near-riskless.
The adversarial panel claimed "settled & >2c discount never co-occur (decided
names trade <1c)". This logger MEASURES whether that's true.

Mechanism (snapshot logging, no forward window — the market is already resolved):
  - scan active markets whose endDate just passed (resolved-but-maybe-not-delisted).
  - confirm TRUE on-chain resolution via ctf_resolution.py (payoutDenominator!=0).
  - read the winning token's live /book; discount = 1.00 - best_ask.
  - log every market with 0 < ask < 0.995 (a real, capturable discount).

Output answers: do discounts >2c exist, how big, how often. If the log stays
empty / all <1c, the panel was right and the edge doesn't exist — for free.
PAPER ONLY, read-only, stdlib. Usage: python3 redeemdisc_logger.py once | report
"""
import os, sys, json, time, urllib.request, urllib.parse
import calendar

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ctf_resolution as ctf
STATE = os.path.join(HERE, "redeemdisc_state.json")
LOG = os.path.join(HERE, "redeemdisc_log.jsonl")
VAULT = os.path.expanduser("~/Documents/PolymarketVault/Reports/redeemdisc.md")
UA = {"User-Agent": "Mozilla/5.0"}

# Fresh resolutions delist instantly (closed markets 404 on /book), so the only
# resolved-but-still-trading markets are stale "active" stragglers with a long-past
# endDate. Scan that whole population; any redemption discount lives there (or
# nowhere). Empty log = the discount window doesn't exist (panel confirmed).
PAST_WINDOW_H = 24 * 365  # endDate within last ~1y
FUTURE_SLACK_H = 6        # ...up to N hours ahead (endDate set early)
MAX_SCAN = 50             # cap on-chain calls per cycle
MIN_DISC = 0.005         # log discounts >= this (0.5c); >2c is the headline threshold


def _g(url, timeout=12):
    return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout))


def best_ask(token_id):
    try:
        bk = _g(f"https://clob.polymarket.com/book?token_id={token_id}")
        asks = bk.get("asks") or []
        return float(asks[0]["price"]) if asks else None     # asks sorted asc -> [0] is best
    except Exception:
        return None


def candidates():
    """Active markets whose endDate is in [now-48h, now+6h] — the resolved-but-
    not-yet-delisted window where a dump discount could live."""
    try:
        ms = _g("https://gamma-api.polymarket.com/markets?" + urllib.parse.urlencode(
            {"closed": "false", "active": "true", "limit": 400, "order": "endDate", "ascending": "true"}))
    except Exception:
        return []
    now = time.time(); out = []
    for m in ms:
        end = m.get("endDate") or ""
        try:
            ts = calendar.timegm(time.strptime(end[:19], "%Y-%m-%dT%H:%M:%S"))
        except Exception:
            continue
        if (now - PAST_WINDOW_H * 3600) <= ts <= (now + FUTURE_SLACK_H * 3600):
            out.append(m)
    return out[:MAX_SCAN]


def load_state():
    if os.path.exists(STATE):
        try:
            return json.load(open(STATE))
        except Exception:
            pass
    return {"seen": []}


def save_state(st):
    tmp = STATE + ".tmp"; json.dump(st, open(tmp, "w")); os.replace(tmp, STATE)


def scan(st):
    seen = set(st.get("seen", []))
    new = 0; checked = 0
    for m in candidates():
        cid = m.get("conditionId")
        if not cid or cid in seen:
            continue
        checked += 1
        try:
            win = ctf.winning_outcome(cid)            # None=unresolved, 0/1=winner idx, -1=no winner
        except Exception:
            win = None
        if win not in (0, 1):
            continue                                  # not resolved on-chain yet -> skip (recheck next cycle)
        seen.add(cid)
        toks = m.get("clobTokenIds"); toks = json.loads(toks) if isinstance(toks, str) else toks
        if not toks or win >= len(toks):
            continue
        ask = best_ask(toks[win])
        if ask is None or not (0 < ask < 0.995):
            continue
        disc = round(1.0 - ask, 4)
        rec = {"cid": cid, "win_idx": win, "win_ask": ask, "discount": disc,
               "title": m.get("question"), "ts": int(time.time())}
        with open(LOG, "a") as f:
            f.write(json.dumps(rec) + "\n")
        new += 1
        print(f"  DISCOUNT {disc*100:.1f}c  win-ask={ask:.3f}  {(m.get('question') or '')[:48]}")
    st["seen"] = list(seen)[-5000:]                   # cap memory
    return new, checked


def _read_log():
    return [json.loads(l) for l in open(LOG)] if os.path.exists(LOG) else []


def summarize():
    recs = _read_log()
    n = len(recs)
    if not n:
        return {"n": 0}
    ds = [r["discount"] for r in recs]
    over2 = [d for d in ds if d >= 0.02]
    return {"n": n, "mean_discount_c": round(100 * sum(ds) / n, 2),
            "max_discount_c": round(100 * max(ds), 2),
            "share_over_2c": round(len(over2) / n, 2),
            "n_over_2c": len(over2)}


def export_obsidian(st):
    s = summarize()
    L = ["# Redeemdisc Logger — post-resolution redemption discount",
         "",
         "> READ-ONLY logger (no trades). After on-chain resolution, does the winning token's",
         "> ask trade below $1 (a near-riskless buy-and-redeem discount)?",
         f"> Updated {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())} · resolved-mkts-logged={s.get('n',0)}",
         ""]
    if s.get("n"):
        L += [f"- **mean discount: {s['mean_discount_c']}¢** · max {s['max_discount_c']}¢",
              f"- **share with >2¢ discount: {int(s['share_over_2c']*100)}%** ({s['n_over_2c']} markets)",
              "", "**Read:** real edge ⇒ a meaningful share with >2¢ discount that persists long enough "
              "to buy. If mean stays <1¢, the panel was right — decided names trade ~$1, no edge."]
    else:
        L += ["_No resolved-but-trading markets with a discount logged yet. If this stays empty, "
              "the redemption-discount window doesn't exist on the live book (panel confirmed)._"]
    os.makedirs(os.path.dirname(VAULT), exist_ok=True)
    open(VAULT, "w").write("\n".join(L) + "\n")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "once"
    st = load_state()
    if cmd == "once":
        new, checked = scan(st)
        save_state(st)
        export_obsidian(st)
        print(f"redeemdisc: candidates_checked={checked}  new_discounts={new}  total_logged={summarize().get('n',0)}")
    elif cmd == "report":
        export_obsidian(st)
        print(json.dumps(summarize(), indent=2))
    else:
        print("usage: redeemdisc_logger.py once|report")


if __name__ == "__main__":
    main()
