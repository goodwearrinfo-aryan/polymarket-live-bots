#!/usr/bin/env python3
"""candle_fade_watch.py — autonomous watcher for the thin-liquidity overextension fade.

Every run, for BTC/ETH/SOL (15m), checks if the fade is ARMED on the last CLOSED bar
(close > Z*std above the 20-bar mean AND volume < LL*avg volume), then:
  * appends a forward-log row per coin (the honest forward dataset: price + z + vol_ratio + armed),
  * writes a vault status note (Candle Fade Watch.md),
  * sends a DEDUPED iMessage only on a FRESH arm (once per bar per coin — never cries wolf).

Reuses candle_short.py's logic (single source of truth). Read-only to markets (PAPER).
Logs to /tmp (TCC-clean). State written atomically. Fail-soft (a coin's fetch error is logged,
the run still exits 0). Keyless (Binance klines). launchd: com.aryan.candle-fade-watch (30min).

This is the SIGNAL-side forward test; the EQUITY-side forward test is the LiqFade legs in algo_lab.
Arming is a deterministic setup trigger, NOT a proven edge — the fade is thin and needs OOS
confirmation, so an alert means 'your setup just fired live', not 'this will pay'.
"""
import os, sys, json, time, subprocess, urllib.request

PM = os.path.expanduser("~/Documents/polymarket")
sys.path.insert(0, PM)
import candle_short as cs

VAULT = os.path.expanduser("~/Documents/PolymarketVault")
STATE = os.path.join(PM, "candle_fade_watch_state.json")
LOG = os.path.join(PM, "candle_fade_watch_log.jsonl")
NOTE = os.path.join(VAULT, "Candle Fade Watch.md")
COINS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
LL, Z, INTERVAL = 0.6, 1.0, "15m"
ALERT = ["krisharyan@icloud.com", "+918449447444"]   # same targets as algo_lab


def fetch(sym):
    url = f"https://api.binance.com/api/v3/klines?symbol={sym}&interval={INTERVAL}&limit=200"
    req = urllib.request.Request(url, headers={"User-Agent": "fade-watch"})
    k = json.loads(urllib.request.urlopen(req, timeout=15).read())
    return ([int(c[0] // 1000) for c in k], [float(c[1]) for c in k], [float(c[2]) for c in k],
            [float(c[3]) for c in k], [float(c[4]) for c in k], [float(c[5]) for c in k])


def imsg(body):
    osa = ('on run argv\ntell application "Messages"\nset s to 1st service whose service type = iMessage\n'
           'send (item 2 of argv) to buddy (item 1 of argv) of s\nend tell\nend run')
    for t in ALERT:
        try:
            subprocess.run(["osascript", "-e", osa, t, body], capture_output=True, timeout=10)
        except Exception:
            pass


def main():
    try:
        state = json.load(open(STATE))
    except Exception:
        state = {}
    ts = int(time.time())
    rows, armed_now, alerts = [], [], []
    for sym in COINS:
        try:
            T, O, H, L, C, V = fetch(sym)
        except Exception as e:
            rows.append({"ts": ts, "coin": sym, "error": str(e)[:80]}); continue
        if len(C) < max(cs.VN, cs.PN) + 5:
            continue
        volma, pmean, pstd = cs._prep(O, H, L, C, V)
        i = len(C) - 2                                    # last CLOSED bar (last is in-progress)
        z = (C[i] - pmean[i]) / pstd[i] if pstd[i] else 0.0
        vr = V[i] / volma[i] if volma[i] else 0.0
        armed = cs.fade_armed(O, H, L, C, V, volma, pmean, pstd, i, LL, Z, False)
        rows.append({"ts": ts, "bar": T[i], "coin": sym, "price": round(C[i], 2),
                     "z": round(z, 2), "vol_ratio": round(vr, 2), "armed": bool(armed)})
        if armed:
            armed_now.append(sym)
            if state.get(sym, {}).get("last_armed_bar") != T[i]:
                alerts.append(f"{sym} fade ARMED @ {C[i]:,.0f} (z=+{z:.2f}, vol={vr:.2f}x)")
            state[sym] = {"last_armed_bar": T[i]}

    try:
        with open(LOG, "a") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
    except Exception as e:
        sys.stderr.write(f"log append failed: {e}\n")

    try:
        lines = ["---", "type: monitor", "status: " + ("armed" if armed_now else "flat"),
                 f"date: {time.strftime('%Y-%m-%d')}", "---", "", "# Candle Fade Watch", "",
                 f"_thin-liquidity overextension fade · {INTERVAL} · LL={LL} Z={Z} · "
                 f"updated {time.strftime('%Y-%m-%d %H:%M')}_", "",
                 "| coin | price | price z | vol vs avg | armed |", "|---|---|---|---|---|"]
        for r in rows:
            if "error" in r:
                lines.append(f"| {r['coin']} | err | | | ⚠️ |"); continue
            lines.append(f"| {r['coin']} | {r['price']:,.0f} | {r['z']:+.2f} | "
                         f"{r['vol_ratio']:.2f}x | {'🔴 SHORT' if r['armed'] else '—'} |")
        lines += ["", "> Paper research signal. Arming ≠ trade — the fade edge is thin and needs "
                  "out-of-sample confirmation.", ">",
                  "> Forward log: `candle_fade_watch_log.jsonl`. Equity race: the LiqFade legs on the "
                  "ALICE portal (algo_lab)."]
        tmp = NOTE + ".tmp"
        with open(tmp, "w") as f:
            f.write("\n".join(lines))
        os.replace(tmp, NOTE)
    except Exception as e:
        sys.stderr.write(f"vault note failed: {e}\n")

    try:
        tmp = STATE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(state, f)
        os.replace(tmp, STATE)
    except Exception as e:
        sys.stderr.write(f"state write failed: {e}\n")

    # ALERT SILENCED 2026-06-26: the fade is OOS-REJECTED (overfit to the 3 majors, fails cross-coin),
    # so paging on an arm would be crying wolf. Keep forward-LOGGING the null; re-enable imsg() only if
    # an out-of-sample retest ever passes. `alerts` is still computed for the run summary below.
    print(f"candle_fade_watch: {len(rows)} coins | armed={armed_now or 'none'} | "
          f"would-arm={len(alerts)} (alerts silenced — OOS-rejected)")


if __name__ == "__main__":
    main()
