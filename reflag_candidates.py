#!/usr/bin/env python3
"""One-shot clean re-flag of the EXISTING onchain_sharp_candidates.json with the
new coinflip filter + fresh stats. The coinflip/stale fixes only ever REMOVE
candidates, so re-checking just the current candidates (not all 214k wallets) is
sufficient and ~271 calls instead of a 2-hour full rescan. Retries None results
(transient 429) so a real candidate isn't wrongly dropped. Paper, read-only API."""
import json, time
from pathlib import Path
import wallet_scanner as ws

HERE = Path(__file__).resolve().parent
F = HERE / "onchain_sharp_candidates.json"
BAK = HERE / "onchain_sharp_candidates.preReflag.bak.json"
MINPOS = 5
now = int(time.time())

cur = json.loads(F.read_text())
BAK.write_text(json.dumps(cur, indent=2))
print(f"re-flagging {len(cur)} existing candidates (backup -> {BAK.name})")

def check(addr, tries=3):
    for a in range(tries):
        r = ws.flag_wallet(addr, MINPOS)
        if r is not None:
            return r
        time.sleep(2 * (a + 1))
    return None

kept, drop_cf, drop_ns, errs = {}, [], [], []
focus = {"0xc76f85c258044adea3167f4ee7b29c24c4eba5db","0x6eb36a963362361cfc7a896f0134d1e3c9af70de",
         "0x3139bb6fe34b010dbea552158ea36ef635e8096e","0xbd0477e08d82d35a855ff19644a88a9213d8fbb0"}
for i, (addr, old) in enumerate(cur.items(), 1):
    r = check(addr)
    if r is None:
        errs.append(addr); kept[addr] = old  # keep old entry if we truly can't reach it
        verdict = "ERR(kept-old)"
    elif r["flag"] != "SHARP":
        drop_ns.append(addr); verdict = f"DROP non-sharp (was {old.get('win_rate')}% now {r['win_rate']}%)"
    elif r.get("coinflip_share", 0) > 0.5:
        drop_cf.append(addr); verdict = f"DROP coinflip {r['coinflip_share']:.0%}"
    else:
        kept[addr] = {"win_rate": r["win_rate"], "realized_pnl": r["realized_pnl"], "closed": r["closed"],
                      "avg_bet": r["avg_bet"], "onchain_fills": old.get("onchain_fills"),
                      "coinflip_share": r.get("coinflip_share", 0), "flagged_at": now}
        verdict = "KEEP"
    if addr.lower() in focus:
        print(f"  [focus] {addr[:12]} {verdict}")
    time.sleep(0.25)

F.write_text(json.dumps(kept, indent=2))
print(f"\nDONE: {len(kept)} kept | {len(drop_cf)} coinflip-dropped | {len(drop_ns)} non-sharp-dropped | {len(errs)} unreachable(kept-old)")
print(f"  was {len(cur)} -> now {len(kept)}")
