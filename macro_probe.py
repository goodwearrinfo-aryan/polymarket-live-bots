#!/usr/bin/env python3
"""macro_probe.py — READ-ONLY macro signal probe for Polymarket crypto markets.

Two keyless macro sources cross-referenced against live crypto markets:
  1. Fear & Greed Index  — api.alternative.me/fng (7d)
  2. DeFiLlama Global TVL — api.llama.fi/v2/globalCharts (7d pct-change)

For each crypto market the probe computes a macro_signal:
  fear_vs_bullish    — F&G < 25 AND market YES > 0.55
  greed_vs_bearish   — F&G > 75 AND market YES < 0.45
  tvl_drop_vs_bullish — TVL 7d -10%+  AND market YES > 0.60
  tvl_rise_vs_bearish — TVL 7d +10%+  AND market YES < 0.40

Outputs:
  ~/Documents/polymarket/macro_cache.json              (atomic write)
  ~/Documents/PolymarketVault/Reports/macro_probe.md   (Obsidian report)
  ~/Documents/polymarket/macro_findings.md             (graphify node)
  ~/Documents/PolymarketVault/Reports/macro_findings.md (same, dual-write)

Paper-only. Read-only. No API keys. No trades. No Postgres.

Usage:
  python3 macro_probe.py          # run scan + write all outputs
  python3 macro_probe.py once     # same
  python3 macro_probe.py report   # print last cached results to terminal
"""

import json, os, sys, time, urllib.request
from datetime import datetime, timezone

HERE  = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "macro_cache.json")
VAULT_PROBE    = os.path.expanduser("~/Documents/PolymarketVault/Reports/macro_probe.md")
VAULT_FINDINGS = os.path.expanduser("~/Documents/PolymarketVault/Reports/macro_findings.md")
LOCAL_FINDINGS = os.path.join(HERE, "macro_findings.md")

UA      = {"User-Agent": "Mozilla/5.0"}
TIMEOUT = 14

GAMMA_URL = ("https://gamma-api.polymarket.com/markets"
             "?active=true&closed=false&order=volume&ascending=false&limit=100")
FNG_URL   = "https://api.alternative.me/fng/?limit=7"
TVL_URL   = "https://api.llama.fi/v2/historicalChainTvl"

CRYPTO_KEYWORDS = [
    "btc", "eth", "sol", "crypto", "bitcoin", "ethereum", "defi",
    "token", "coin", "blockchain", "altcoin", "bnb", "xrp", "ada",
    "doge", "polygon", "matic", "avax", "chainlink", "link", "uniswap",
]

# ── thresholds ──────────────────────────────────────────────────────────────

FG_FEAR_MAX   = 25    # F&G ≤ 25 = Fear / Extreme Fear
FG_GREED_MIN  = 75    # F&G ≥ 75 = Greed / Extreme Greed
TVL_DROP_PCT  = -10.0 # 7d TVL fell >10%
TVL_RISE_PCT  =  10.0 # 7d TVL rose >10%
YES_BULLISH   = 0.55  # market too optimistic vs fear
YES_BEARISH   = 0.45  # market too pessimistic vs greed
YES_BULL_TVL  = 0.60  # market too optimistic vs TVL drop
YES_BEAR_TVL  = 0.40  # market too pessimistic vs TVL rise


# ── HTTP ────────────────────────────────────────────────────────────────────

def _get(url, timeout=TIMEOUT):
    """GET → parsed JSON, or None on any failure (fail-soft)."""
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception as exc:
        print(f"[macro] WARN fetch failed {url[:60]}... — {exc}")
        return None


# ── Fear & Greed ─────────────────────────────────────────────────────────────

def fetch_fg():
    """Return {value, classification, trend, avg7d} or None."""
    raw = _get(FNG_URL)
    if not raw or not raw.get("data"):
        return None
    data = raw["data"]          # list newest-first
    try:
        values = [int(d["value"]) for d in data]
        current = values[0]
        avg7d   = round(sum(values) / len(values), 1)
        # trend: newest vs oldest in the window
        trend = "rising" if values[0] > values[-1] else "falling" if values[0] < values[-1] else "flat"
        label = data[0].get("value_classification", "")
        return {
            "value":          current,
            "classification": label,
            "trend":          trend,
            "avg7d":          avg7d,
            "history":        values,
        }
    except Exception as exc:
        print(f"[macro] WARN F&G parse error — {exc}")
        return None


# ── DeFiLlama TVL ─────────────────────────────────────────────────────────────

def fetch_tvl():
    """Return {tvl_now, tvl_7d_ago, pct_change} or None.

    DeFiLlama /v2/historicalChainTvl returns a list of dicts:
      [{"date": <unix_ts>, "tvl": <float>}, ...]  ascending by date.
    """
    raw = _get(TVL_URL)
    if not isinstance(raw, list) or len(raw) < 8:
        return None
    try:
        # Extract (ts, tvl) regardless of whether elements are lists or dicts
        def _row(r):
            if isinstance(r, dict):
                return int(r.get("date", 0)), float(r.get("tvl", 0))
            return int(r[0]), float(r[1])  # legacy [ts, tvl] format

        now_ts, tvl_now    = _row(raw[-1])
        ago_ts, tvl_7d_ago = _row(raw[-8])   # ~7 days back (daily points)
        if tvl_7d_ago == 0:
            return None
        pct = round((tvl_now - tvl_7d_ago) / tvl_7d_ago * 100, 2)
        return {
            "tvl_now":    round(tvl_now / 1e9, 3),    # billions USD
            "tvl_7d_ago": round(tvl_7d_ago / 1e9, 3),
            "pct_change": pct,
            "ts_now":     now_ts,
            "ts_7d_ago":  ago_ts,
        }
    except Exception as exc:
        print(f"[macro] WARN TVL parse error — {exc}")
        return None


# ── Polymarket crypto markets ────────────────────────────────────────────────

def _yes_price(m):
    """Extract YES probability from outcomePrices field."""
    op = m.get("outcomePrices")
    if isinstance(op, str):
        try:
            op = json.loads(op)
        except Exception:
            return None
    if isinstance(op, list) and op:
        try:
            return float(op[0])
        except Exception:
            return None
    return None


def _is_crypto(question):
    q = question.lower()
    return any(kw in q for kw in CRYPTO_KEYWORDS)


def fetch_crypto_markets():
    """Return list of {question, yes, vol, slug} for open crypto markets."""
    raw = _get(GAMMA_URL)
    if not isinstance(raw, list):
        return []
    out = []
    for m in raw:
        q = m.get("question") or ""
        if not _is_crypto(q):
            continue
        yes = _yes_price(m)
        if yes is None:
            continue
        vol = float(m.get("volumeNum") or m.get("volume") or 0)
        out.append({
            "question": q,
            "yes":      round(yes, 4),
            "no":       round(1 - yes, 4),
            "vol":      vol,
            "slug":     m.get("slug") or "",
        })
    return out


# ── Signal computation ───────────────────────────────────────────────────────

def compute_signals(markets, fg, tvl):
    """Return list of {question, yes, signal_type, detail} for flagged markets."""
    sigs = []
    for m in markets:
        yes = m["yes"]
        q   = m["question"]

        # Fear & Greed signals
        if fg:
            fgv = fg["value"]
            if fgv <= FG_FEAR_MAX and yes > YES_BULLISH:
                sigs.append({
                    "question":    q,
                    "yes":         yes,
                    "signal_type": "fear_vs_bullish",
                    "detail":      (f"F&G={fgv} ({fg['classification']}) but market YES={yes:.0%} "
                                    f"— macro fear contradicts bullish pricing"),
                    "vol":         m["vol"],
                    "slug":        m["slug"],
                })
            elif fgv >= FG_GREED_MIN and yes < YES_BEARISH:
                sigs.append({
                    "question":    q,
                    "yes":         yes,
                    "signal_type": "greed_vs_bearish",
                    "detail":      (f"F&G={fgv} ({fg['classification']}) but market YES={yes:.0%} "
                                    f"— macro greed contradicts bearish pricing"),
                    "vol":         m["vol"],
                    "slug":        m["slug"],
                })

        # DeFiLlama TVL signals
        if tvl:
            pct = tvl["pct_change"]
            if pct <= TVL_DROP_PCT and yes > YES_BULL_TVL:
                sigs.append({
                    "question":    q,
                    "yes":         yes,
                    "signal_type": "tvl_drop_vs_bullish",
                    "detail":      (f"DeFiLlama TVL 7d {pct:+.1f}% (now ${tvl['tvl_now']:.1f}B) "
                                    f"but market YES={yes:.0%} — TVL contraction vs bullish market"),
                    "vol":         m["vol"],
                    "slug":        m["slug"],
                })
            elif pct >= TVL_RISE_PCT and yes < YES_BEAR_TVL:
                sigs.append({
                    "question":    q,
                    "yes":         yes,
                    "signal_type": "tvl_rise_vs_bearish",
                    "detail":      (f"DeFiLlama TVL 7d {pct:+.1f}% (now ${tvl['tvl_now']:.1f}B) "
                                    f"but market YES={yes:.0%} — TVL growth vs bearish market"),
                    "vol":         m["vol"],
                    "slug":        m["slug"],
                })

    # Sort by volume descending so highest-liquidity signals lead
    sigs.sort(key=lambda x: x["vol"], reverse=True)
    return sigs


# ── Atomic JSON write ────────────────────────────────────────────────────────

def _atomic_write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=2)
    os.replace(tmp, path)


# ── Obsidian report ──────────────────────────────────────────────────────────

def export_obsidian(data):
    ts_str = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(data["ts"]))
    fg  = data.get("fg") or {}
    tvl = data.get("defi") or {}
    sigs = data.get("signals", [])

    L = [
        f"# Macro Probe — {ts_str}",
        "",
        "> Read-only macro signal report: Fear & Greed + DeFiLlama TVL vs live Polymarket crypto markets.",
        "> Paper-only. No trades, no keys.",
        "",
        "## Fear & Greed Index",
        "",
    ]

    if fg:
        direction = fg.get("trend", "—")
        trend_arrow = "↑" if direction == "rising" else "↓" if direction == "falling" else "→"
        L += [
            f"| Metric | Value |",
            f"|--------|-------|",
            f"| Current | **{fg['value']}** — {fg.get('classification', '—')} |",
            f"| 7d Average | {fg.get('avg7d', '—')} |",
            f"| Trend (7d) | {trend_arrow} {direction} |",
            "",
        ]
        hist = fg.get("history", [])
        if hist:
            hist_str = " → ".join(str(v) for v in reversed(hist))
            L.append(f"7d history (oldest→newest): `{hist_str}`")
            L.append("")
    else:
        L += ["_F&G fetch failed — source unavailable._", ""]

    L += ["## DeFiLlama Global TVL (7d)", ""]

    if tvl:
        sign = "+" if tvl["pct_change"] >= 0 else ""
        L += [
            f"| Metric | Value |",
            f"|--------|-------|",
            f"| TVL Now | **${tvl['tvl_now']:.2f}B** |",
            f"| TVL 7d Ago | ${tvl['tvl_7d_ago']:.2f}B |",
            f"| 7d Change | **{sign}{tvl['pct_change']:.2f}%** |",
            "",
        ]
    else:
        L += ["_DeFiLlama fetch failed — source unavailable._", ""]

    # Signal type counts
    type_counts: dict = {}
    for s in sigs:
        type_counts[s["signal_type"]] = type_counts.get(s["signal_type"], 0) + 1

    L += [
        "## Macro Signals",
        "",
        f"**{len(sigs)} flagged markets** across {len(type_counts)} signal types.",
        "",
    ]

    if type_counts:
        L += ["| Signal Type | Count |", "|-------------|-------|"]
        for stype, cnt in sorted(type_counts.items(), key=lambda x: -x[1]):
            L.append(f"| `{stype}` | {cnt} |")
        L.append("")

    if sigs:
        L += ["### Flagged Markets", ""]
        L += ["| Market | YES | Signal | Volume |",
              "|--------|-----|--------|--------|"]
        for s in sigs[:30]:
            q_short = s["question"][:60] + ("…" if len(s["question"]) > 60 else "")
            vol_k   = f"${s['vol']/1000:.0f}k" if s["vol"] >= 1000 else f"${s['vol']:.0f}"
            L.append(f"| {q_short} | {s['yes']:.0%} | `{s['signal_type']}` | {vol_k} |")
        L.append("")

        L += ["### Signal Details", ""]
        for i, s in enumerate(sigs[:20], 1):
            L.append(f"**{i}. {s['question']}**")
            L.append(f"- Signal: `{s['signal_type']}`")
            L.append(f"- {s['detail']}")
            vol_k = f"${s['vol']/1000:.0f}k" if s["vol"] >= 1000 else f"${s['vol']:.0f}"
            L.append(f"- Volume: {vol_k}")
            L.append("")
    else:
        L += ["_No signals triggered — macro and market pricing are broadly consistent._", ""]

    L += [
        "---",
        f"*Generated {ts_str} — macro_probe.py (paper-only, read-only)*",
        "",
    ]

    content = "\n".join(L) + "\n"
    os.makedirs(os.path.dirname(VAULT_PROBE), exist_ok=True)
    with open(VAULT_PROBE, "w") as f:
        f.write(content)
    print(f"[macro] Obsidian → {VAULT_PROBE}")
    return content


# ── Graphify / findings node ─────────────────────────────────────────────────

def write_findings(data):
    """Write graphify-readable structured summary (dual-write: local + vault)."""
    ts_str = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(data["ts"]))
    fg  = data.get("fg") or {}
    tvl = data.get("defi") or {}
    sigs = data.get("signals", [])

    type_counts: dict = {}
    for s in sigs:
        type_counts[s["signal_type"]] = type_counts.get(s["signal_type"], 0) + 1

    L = [
        f"# Macro Probe Findings — {ts_str}",
        "",
        "Signal source: Fear & Greed index (alternative.me) + DeFiLlama global TVL vs Polymarket crypto market pricing.",
        "",
        "## Macro State",
        "",
    ]

    if fg:
        L.append(f"- Fear & Greed: **{fg['value']}** ({fg.get('classification','—')}) "
                 f"| 7d avg {fg.get('avg7d','—')} | trend {fg.get('trend','—')}")
    else:
        L.append("- Fear & Greed: UNAVAILABLE")

    if tvl:
        sign = "+" if tvl["pct_change"] >= 0 else ""
        L.append(f"- DeFiLlama TVL: **${tvl['tvl_now']:.2f}B** now vs ${tvl['tvl_7d_ago']:.2f}B 7d ago "
                 f"({sign}{tvl['pct_change']:.2f}%)")
    else:
        L.append("- DeFiLlama TVL: UNAVAILABLE")

    L += ["", "## Signal Summary", ""]
    if type_counts:
        for stype, cnt in sorted(type_counts.items(), key=lambda x: -x[1]):
            L.append(f"- `{stype}`: {cnt} market(s)")
    else:
        L.append("- No macro divergence signals triggered.")

    L += ["", "## Top Signals", ""]
    for s in sigs[:12]:
        L.append(f"- **{s['question']}**: YES={s['yes']:.0%} → `{s['signal_type']}` — {s['detail']}")

    if not sigs:
        L.append("- No signals flagged.")

    L += [
        "",
        f"*macro_probe.py — {ts_str}*",
        "",
    ]

    content = "\n".join(L) + "\n"

    # local copy
    with open(LOCAL_FINDINGS, "w") as f:
        f.write(content)
    print(f"[macro] findings → {LOCAL_FINDINGS}")

    # vault copy
    os.makedirs(os.path.dirname(VAULT_FINDINGS), exist_ok=True)
    with open(VAULT_FINDINGS, "w") as f:
        f.write(content)
    print(f"[macro] findings → {VAULT_FINDINGS}")


# ── Main scan ────────────────────────────────────────────────────────────────

def scan():
    print("[macro] Fetching Fear & Greed index…")
    fg = fetch_fg()
    if fg:
        print(f"[macro]   F&G = {fg['value']} ({fg['classification']}) | "
              f"7d avg {fg['avg7d']} | {fg['trend']}")
    else:
        print("[macro]   F&G unavailable — proceeding without it")

    print("[macro] Fetching DeFiLlama global TVL…")
    tvl = fetch_tvl()
    if tvl:
        sign = "+" if tvl["pct_change"] >= 0 else ""
        print(f"[macro]   TVL now ${tvl['tvl_now']:.2f}B | 7d ago ${tvl['tvl_7d_ago']:.2f}B "
              f"| {sign}{tvl['pct_change']:.2f}%")
    else:
        print("[macro]   DeFiLlama TVL unavailable — proceeding without it")

    print("[macro] Fetching Polymarket crypto markets…")
    markets = fetch_crypto_markets()
    print(f"[macro]   {len(markets)} crypto markets found")

    sigs = compute_signals(markets, fg, tvl)
    print(f"[macro]   {len(sigs)} signals triggered")

    data = {
        "ts":      int(time.time()),
        "fg":      fg,
        "defi":    tvl,
        "signals": sigs,
        "n_crypto_markets": len(markets),
    }

    _atomic_write_json(CACHE, data)
    print(f"[macro] cache → {CACHE}")

    return data


# ── Report (terminal print from cache) ──────────────────────────────────────

def report(data=None):
    if data is None:
        if not os.path.exists(CACHE):
            print("[macro] No cache yet — run without args first")
            return
        with open(CACHE) as f:
            data = json.load(f)

    ts_str = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(data.get("ts", 0)))
    fg  = data.get("fg") or {}
    tvl = data.get("defi") or {}
    sigs = data.get("signals", [])

    print(f"\n=== Macro Probe — {ts_str} ===")
    if fg:
        print(f"  Fear & Greed : {fg['value']} ({fg.get('classification','—')}) | "
              f"7d avg {fg.get('avg7d','—')} | {fg.get('trend','—')}")
    else:
        print("  Fear & Greed : UNAVAILABLE")

    if tvl:
        sign = "+" if tvl["pct_change"] >= 0 else ""
        print(f"  DeFiLlama TVL: ${tvl['tvl_now']:.2f}B (7d {sign}{tvl['pct_change']:.2f}%)")
    else:
        print("  DeFiLlama TVL: UNAVAILABLE")

    print(f"\n  Crypto markets scanned : {data.get('n_crypto_markets', '?')}")
    print(f"  Signals triggered      : {len(sigs)}")

    if sigs:
        print()
        for s in sigs[:10]:
            q = s["question"][:68]
            print(f"  [{s['signal_type']}] YES={s['yes']:.0%} | {q}")
            print(f"    {s['detail'][:100]}")
    else:
        print("  No macro divergence signals.")
    print()


# ── Entry point ──────────────────────────────────────────────────────────────

def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "once"
    if cmd == "report":
        report()
        return
    data = scan()
    export_obsidian(data)
    write_findings(data)
    report(data)


if __name__ == "__main__":
    main()
