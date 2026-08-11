#!/usr/bin/env python3
"""
portal_obsidian_sync.py
Reads ALICE portal /api/state and writes live Obsidian vault notes.
stdlib only — no external deps.
"""

import json
import os
import sys
from datetime import datetime, timezone
from urllib.request import urlopen
from urllib.error import URLError

PORTAL_URL = "http://localhost:8090/api/state"
VAULT_LIVE = os.path.expanduser("~/Documents/PolymarketVault/Live")
TIMEOUT = 10  # seconds

# ── helpers ───────────────────────────────────────────────────────────────────

def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def now_human():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

def from_ts(ts):
    """Unix timestamp → human string."""
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        return str(ts)

def age_label(age_s, fresh):
    """Return OK/STALE based on age."""
    if fresh:
        return "OK"
    if age_s is not None and age_s < 3600:
        return "OK"
    return "STALE"

def write_note(filename, content):
    """Write note to vault, print confirmation."""
    path = os.path.join(VAULT_LIVE, filename)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"wrote Live/{filename}")

def frontmatter(status, ts=None):
    ts = ts or now_iso()
    return f"---\ntype: live-status\nupdated: {ts}\nstatus: {status}\n---\n\n"

def footer():
    return "\n\n<!-- portal-sync -->\n"

def note(title, status, body, ts=None):
    human = now_human()
    return (
        frontmatter(status, ts)
        + f"# {title}\n\n"
        + f"> Updated: {human}\n\n"
        + body
        + footer()
    )

def safe_get(d, *keys, default="—"):
    """Nested dict safe get."""
    for k in keys:
        if not isinstance(d, dict):
            return default
        d = d.get(k, None)
        if d is None:
            return default
    return d

# ── note builders ──────────────────────────────────────────────────────────────

def build_alice_portal(state):
    pnl = state.get("total_paper_pnl", "?")
    ts_raw = state.get("ts")
    ts_human = from_ts(ts_raw) if ts_raw else "?"
    fleet = state.get("fleet", {})
    ok = fleet.get("ok", "?")
    total = fleet.get("total", "?")
    bad = fleet.get("bad", "?")

    body = f"""\
## Overview

| Key | Value |
|-----|-------|
| Total Paper P&L | **${pnl:+.2f}** |
| Portal snapshot | {ts_human} |
| Fleet agents | {ok}/{total} OK ({bad} bad) |

## Live Notes

- [[Provider Chain]] — LLM provider health
- [[Arb Track]] — basket locks & dataarb
- [[Edge Hunt]] — dossier hunt
- [[Edge Bots]] — algo-lab equity & signals
- [[Crypto Movers]] — top movers
- [[FII DII Flow]] — money flow signals
- [[DSR Gates]] — scalp leg status
"""
    return note("ALICE Portal", "OK", body)


def build_provider_chain(prov):
    if "error" in prov:
        return note("Provider Chain", "ERROR", f"Panel error: {prov['error']}\n")

    status = prov.get("status", "?")
    down = prov.get("down", [])
    streak = prov.get("streak", "?")
    updated = prov.get("updated", "?")
    tiers = prov.get("tiers", {})
    age_s = prov.get("age_s")
    fresh = prov.get("fresh", False)
    st = age_label(age_s, fresh)

    rows = "\n".join(
        f"| {tier} | {'✓ UP' if up else '✗ DOWN'} |"
        for tier, up in tiers.items()
    )

    body = f"""\
## Summary

| Field | Value |
|-------|-------|
| Status | {status} |
| Streak ok | {streak} |
| Last updated | {updated} |
| Down providers | {', '.join(down) if down else 'none'} |

## Tier Health

| Provider | Status |
|----------|--------|
{rows}
"""
    return note("Provider Chain", st, body)


def build_arb_track(arb):
    if "error" in arb:
        return note("Arb Track", "ERROR", f"Panel error: {arb['error']}\n")

    open_n = arb.get("open", 0)
    closed = arb.get("closed", 0)
    dataarb = arb.get("dataarb_open", 0)
    last_scan = arb.get("last_scan", "?")
    locks = arb.get("locks", [])
    age_s = arb.get("age_s")
    fresh = arb.get("fresh", False)
    st = age_label(age_s, fresh)

    rows = ""
    for lk in locks:
        q = lk.get("q", "?")[:60]
        kind = lk.get("kind", "?")
        edge = lk.get("edge", 0)
        n = lk.get("n", "?")
        rows += f"| {q} | {kind} | {edge:.1%} | {n} |\n"

    if not rows:
        rows = "| — | — | — | — |\n"

    body = f"""\
## Summary

| Field | Value |
|-------|-------|
| Open locks | {open_n} |
| Closed | {closed} |
| Dataarb open | {dataarb} |
| Last scan | {last_scan} |

## Basket Locks

| Market | Kind | Edge% | N-way |
|--------|------|-------|-------|
{rows}
"""
    return note("Arb Track", st, body)


def build_edge_hunt(eh):
    if "error" in eh:
        return note("Edge Hunt", "ERROR", f"Panel error: {eh['error']}\n")

    count = eh.get("count", 0)
    last_hunt = eh.get("last_hunt", "?")
    dossiers = eh.get("dossiers", [])
    age_s = eh.get("age_s")
    fresh = eh.get("fresh", False)
    st = age_label(age_s, fresh)

    dos_lines = ""
    for d in dossiers[:10]:
        q = str(d.get("q", d))[:70]
        dos_lines += f"- {q}\n"
    if not dos_lines:
        dos_lines = "- No dossiers found\n"

    body = f"""\
## Summary

| Field | Value |
|-------|-------|
| Dossier count | {count} |
| Last hunt | {last_hunt} |

## Leads

{dos_lines}
"""
    return note("Edge Hunt", st, body)


def build_edge_bots(algos):
    """Maps to algo-lab panel."""
    if "error" in algos:
        return note("Edge Bots", "ERROR", f"Panel error: {algos['error']}\n")

    age_s = algos.get("age_s")
    fresh = algos.get("fresh", False)
    st = age_label(age_s, fresh)
    champion = algos.get("champion") or "none"
    regime = algos.get("regime", "?")
    regime_leader = algos.get("regime_leader", "?")
    buyhold = algos.get("buyhold", 0)
    beat = algos.get("beat", 0)
    days = algos.get("days", 0)
    results = algos.get("results", [])

    rows = ""
    for r in results:
        name = r.get("name", "?")[:35]
        pnl = r.get("pnl", 0)
        trades = r.get("trades", 0)
        rows += f"| {name} | ${pnl:+.2f} | {trades} |\n"

    if not rows:
        rows = "| — | — | — |\n"

    body = f"""\
## Summary

| Field | Value |
|-------|-------|
| Champion | {champion} |
| Regime | {regime} ({regime_leader}) |
| Buy-hold P&L | ${buyhold:+.2f} |
| Algos beating B&H | {beat}% |
| Days tracked | {days:.1f} |

## Algo Results

| Algo | P&L | Trades |
|------|-----|--------|
{rows}
"""
    return note("Edge Bots", st, body)


def build_crypto_movers(crypto):
    if "error" in crypto:
        return note("Crypto Movers", "ERROR", f"Panel error: {crypto['error']}\n")

    coins = []
    for name, data in crypto.items():
        if isinstance(data, dict):
            p = data.get("p", 0)
            c = data.get("c", 0)
            coins.append((name.upper(), p, c))

    coins_sorted = sorted(coins, key=lambda x: x[2], reverse=True)
    top = coins_sorted[0] if coins_sorted else ("?", 0, 0)
    bot = coins_sorted[-1] if coins_sorted else ("?", 0, 0)

    avg_chg = sum(c[2] for c in coins) / len(coins) if coins else 0

    rows = "\n".join(
        f"| {name} | ${p:,.2f} | {c:+.2f}% |"
        for name, p, c in coins_sorted
    )

    body = f"""\
## Summary

| Field | Value |
|-------|-------|
| Top gainer | {top[0]} ({top[2]:+.2f}%) |
| Top loser | {bot[0]} ({bot[2]:+.2f}%) |
| Avg 24h change | {avg_chg:+.2f}% |

## All Movers

| Coin | Price | 24h % |
|------|-------|-------|
{rows}
"""
    return note("Crypto Movers", "OK", body)


def build_fii_dii(moneyflow):
    """Uses moneyflow recent signals as the FII/DII proxy."""
    if "error" in moneyflow:
        return note("FII DII Flow", "ERROR", f"Panel error: {moneyflow['error']}\n")

    age_s = moneyflow.get("age_s")
    fresh = moneyflow.get("fresh", False)
    st = age_label(age_s, fresh)
    count = moneyflow.get("count", 0)
    recent = moneyflow.get("recent", [])[:5]

    rows = ""
    for rec in recent:
        venue = rec.get("venue", "?")
        symbol = str(rec.get("symbol", "?"))[:50]
        sig = rec.get("sig", "?")
        direction = "BUY" if rec.get("dir", 0) > 0 else "SELL"
        ts_r = rec.get("ts")
        ts_h = from_ts(ts_r) if ts_r else "?"
        rows += f"| {ts_h} | {venue} | {symbol} | {sig} | {direction} |\n"

    if not rows:
        rows = "| — | — | — | — | — |\n"

    body = f"""\
## Summary

| Field | Value |
|-------|-------|
| Total signals | {count} |
| Showing | last 5 |

## Recent Flow Signals

| Time | Venue | Market | Signal | Dir |
|------|-------|--------|--------|-----|
{rows}
"""
    return note("FII DII Flow", st, body)


def build_dsr_gates(scalp):
    """Per-leg status derived from scalp panel."""
    if "error" in scalp:
        return note("DSR Gates", "ERROR", f"Panel error: {scalp['error']}\n")

    age_s = scalp.get("age_s")
    fresh = scalp.get("fresh", False)
    st = age_label(age_s, fresh)

    legs = scalp.get("legs", [])
    worst = scalp.get("worst", [])
    all_legs = legs + worst

    rows = ""
    for leg in all_legs:
        name = leg.get("leg", "?")
        n = leg.get("n", 0)
        pnl = leg.get("pnl", 0)
        mean_per = round(pnl / n, 3) if n > 0 else 0
        gap_to_30 = 30 - n

        # Gate verdict
        if pnl > 0 and n >= 10:
            gate = "GATE"
        elif pnl < -5 and n >= 20:
            gate = "KILL"
        elif n < 10:
            gate = "ACCUM"
        else:
            gate = "REVIEW"

        rows += f"| {name} | {n} | ${mean_per:+.3f} | {gap_to_30} | {gate} |\n"

    if not rows:
        rows = "| — | — | — | — | — |\n"

    realized = scalp.get("realized", 0)
    open_n = scalp.get("open", 0)
    closed_n = scalp.get("closed", 0)

    body = f"""\
## Summary

| Field | Value |
|-------|-------|
| Realized P&L | ${realized:+.2f} |
| Open positions | {open_n} |
| Closed positions | {closed_n} |

## Per-Leg Gate Status

| Leg | N | Mean $/exit | Gap to 30 | Status |
|-----|---|------------|-----------|--------|
{rows}
"""
    return note("DSR Gates", st, body)


# ── new panel builders ────────────────────────────────────────────────────────

def build_market_structure(ms):
    if "error" in ms:
        return note("Market Structure", "ERROR", f"Panel error: {ms['error']}\n")

    age_s = ms.get("age_s")
    fresh = ms.get("fresh", False)
    st = age_label(age_s, fresh)

    fg = ms.get("fear_greed", {})
    fg_val = safe_get(fg, "value", default="—")
    fg_cls = safe_get(fg, "value_classification", default="—")

    dom = ms.get("dominance", {})
    btc_dom = safe_get(dom, "btc", default="—")
    eth_dom = safe_get(dom, "eth", default="—")

    mcap = ms.get("market_cap", {})
    total_b = safe_get(mcap, "total_b", default="—")
    chg_24h = safe_get(mcap, "change_24h_pct", default="—")

    oi = ms.get("open_interest", {})
    btc_oi = safe_get(oi, "btc_b", default="—")
    eth_oi = safe_get(oi, "eth_b", default="—")
    sol_oi = safe_get(oi, "sol_b", default="—")

    iv = ms.get("implied_vol", {})
    btc_iv = safe_get(iv, "btc_atm_iv", default="—")
    eth_iv = safe_get(iv, "eth_atm_iv", default="—")

    def fmt_pct(v):
        try:
            return f"{float(v):.2f}%"
        except Exception:
            return str(v)

    def fmt_b(v):
        try:
            return f"${float(v):.2f}B"
        except Exception:
            return str(v)

    body = f"""\
## Overview

| Metric | Value |
|--------|-------|
| Fear & Greed | {fg_val} ({fg_cls}) |
| BTC Dominance | {fmt_pct(btc_dom)} |
| ETH Dominance | {fmt_pct(eth_dom)} |
| Total Market Cap | {fmt_b(total_b)} |
| 24h Change | {fmt_pct(chg_24h)} |

## Open Interest

- BTC: {fmt_b(btc_oi)}
- ETH: {fmt_b(eth_oi)}
- SOL: {fmt_b(sol_oi)}

## Implied Volatility (Deribit)

- BTC ATM IV: {fmt_pct(btc_iv)}
- ETH ATM IV: {fmt_pct(eth_iv)}
"""
    return note("Market Structure", st, body)


def build_liquidations(liq):
    if "error" in liq:
        return note("Liquidations", "ERROR", f"Panel error: {liq['error']}\n")

    age_s = liq.get("age_s")
    fresh = liq.get("fresh", False)
    st = age_label(age_s, fresh)

    total = liq.get("count", 0)
    events = liq.get("events", [])[:10]

    rows = ""
    for ev in events:
        symbol = str(ev.get("symbol", ev.get("s", "?"))).upper()
        side = ev.get("side", ev.get("S", "?"))
        notional = ev.get("notional", ev.get("q", "?"))
        ts_r = ev.get("ts", ev.get("T"))
        ts_h = from_ts(ts_r) if ts_r else "?"
        try:
            notional_str = f"${float(notional):,.0f}"
        except Exception:
            notional_str = str(notional)
        rows += f"| {symbol} | {side} liq | {notional_str} | {ts_h} |\n"

    if not rows:
        rows = "| — | — | — | — |\n"

    body = f"""\
Total captured: {total}

| Symbol | Side | Notional | Time |
|--------|------|----------|------|
{rows}
"""
    return note("Liquidations", st, body)


def build_hermes_news(hermes):
    if "error" in hermes:
        return note("Hermes News", "ERROR", f"Panel error: {hermes['error']}\n")

    age_s = hermes.get("age_s")
    fresh = hermes.get("fresh", False)
    st = age_label(age_s, fresh)

    headlines = hermes.get("headlines", hermes.get("items", []))
    count = len(headlines)

    rows = ""
    for h in headlines[:20]:
        title = str(h.get("title", h if isinstance(h, str) else "?"))[:90].replace("|", "-")
        source = str(h.get("source", h.get("feed", "?"))).replace("|", "-")
        rows += f"| {title} | {source} |\n"

    if not rows:
        rows = "| No headlines available | — |\n"

    body = f"""\
{count} headlines

| Title | Source |
|-------|--------|
{rows}
"""
    return note("Hermes News", st, body)


def build_edgebots_live(eb):
    if "error" in eb:
        return note("Edge-Bots Live", "ERROR", f"Panel error: {eb['error']}\n")

    age_s = eb.get("age_s")
    fresh = eb.get("fresh", False)
    st = age_label(age_s, fresh)

    def bot_section(label, data):
        if not data:
            return f"## {label}\n\n_No data_\n\n"
        equity = data.get("equity", 0)
        pnl = data.get("pnl", data.get("unrealized_pnl", 0))
        open_n = data.get("open", data.get("open_positions", 0))
        closed_n = data.get("closed", data.get("closed_positions", 0))
        closed_pnl = data.get("closed_pnl", data.get("realized_pnl", 0))
        positions = data.get("positions", data.get("top_positions", []))[:5]

        rows = ""
        for pos in positions:
            sym = str(pos.get("symbol", pos.get("sym", "?"))).upper()
            ann = pos.get("ann_rate", pos.get("rate", "?"))
            collected = pos.get("funding_collected", pos.get("collected", "?"))
            try:
                ann_str = f"{float(ann):.2f}%"
            except Exception:
                ann_str = str(ann)
            try:
                coll_str = f"${float(collected):,.4f}"
            except Exception:
                coll_str = str(collected)
            rows += f"| {sym} | {ann_str} | {coll_str} |\n"

        if not rows:
            rows = "| — | — | — |\n"

        try:
            eq_str = f"${float(equity):,.4f}"
            pnl_str = f"+${float(pnl):,.4f}" if float(pnl) >= 0 else f"-${abs(float(pnl)):,.4f}"
            cpnl_str = f"${float(closed_pnl):,.4f}"
        except Exception:
            eq_str, pnl_str, cpnl_str = str(equity), str(pnl), str(closed_pnl)

        return f"""\
## {label}

- Equity: {eq_str} (P&L: {pnl_str})
- Open positions: {open_n} | Closed: {closed_n}
- Closed P&L: {cpnl_str}

### Top Positions

| Symbol | Ann Rate | Funding Collected |
|--------|----------|-------------------|
{rows}
"""

    funding = eb.get("funding", {})
    basis = eb.get("basis", {})

    body = bot_section("Funding Bot", funding) + bot_section("Basis Bot", basis)
    return note("Edge-Bots Live", st, body)


def build_sentiment(ms):
    """Dedicated Sentiment note with extra frontmatter fields for Obsidian automations."""
    if "error" in ms:
        # Minimal error note
        ts = now_iso()
        return (
            f"---\ntype: live-status\nupdated: {ts}\nstatus: ERROR\n---\n\n"
            f"# Sentiment Snapshot\n\n> No data: {ms.get('error', 'missing')}\n\n<!-- portal-sync -->\n"
        )

    fg = ms.get("fear_greed", {})
    fg_val = safe_get(fg, "value", default="")
    fg_cls = safe_get(fg, "value_classification", default="")

    dom = ms.get("dominance", {})
    btc_dom = safe_get(dom, "btc", default="")

    mcap = ms.get("market_cap", {})
    total_b = safe_get(mcap, "total_b", default="")
    chg_24h = safe_get(mcap, "change_24h_pct", default="")

    age_s = ms.get("age_s")
    fresh = ms.get("fresh", False)
    st = age_label(age_s, fresh)

    ts = now_iso()
    human = now_human()

    # Build extra frontmatter fields safely
    def safe_num(v):
        try:
            return float(v)
        except Exception:
            return v

    fm_extras = ""
    if fg_val != "":
        fm_extras += f"fg_value: {safe_num(fg_val)}\n"
    if fg_cls != "":
        fm_extras += f"fg_class: {fg_cls}\n"
    if btc_dom != "":
        fm_extras += f"btc_dom: {safe_num(btc_dom)}\n"
    if total_b != "":
        fm_extras += f"total_mcap_b: {safe_num(total_b)}\n"
    if chg_24h != "":
        fm_extras += f"mcap_change_24h_pct: {safe_num(chg_24h)}\n"

    def fmt_pct(v):
        try:
            return f"{float(v):.2f}%"
        except Exception:
            return str(v) if v != "" else "—"

    def fmt_b(v):
        try:
            return f"${float(v):.2f}B"
        except Exception:
            return str(v) if v != "" else "—"

    content = (
        f"---\ntype: live-status\nupdated: {ts}\nstatus: {st}\n"
        + fm_extras
        + f"---\n\n"
        + f"# Sentiment Snapshot\n\n"
        + f"> Updated: {human}\n\n"
        + f"| Metric | Value |\n"
        + f"|--------|-------|\n"
        + f"| Fear & Greed | {fg_val} — {fg_cls} |\n"
        + f"| BTC Dominance | {fmt_pct(btc_dom)} |\n"
        + f"| Total Market Cap | {fmt_b(total_b)} |\n"
        + f"| 24h Market Cap Change | {fmt_pct(chg_24h)} |\n"
        + f"\n\n<!-- portal-sync -->\n"
    )
    return content


# ── standalone file-backed note builders ─────────────────────────────────────

def build_macd():
    """Reads macd_paper_state.json — no portal dependency."""
    src = os.path.expanduser("~/Documents/polymarket/macd_paper_state.json")
    ts = now_iso()
    human = now_human()
    try:
        with open(src, encoding="utf-8") as f:
            d = json.load(f)
        file_ts = d.get("ts")
        age_s = None
        if file_ts:
            age_s = int(datetime.now(timezone.utc).timestamp()) - int(file_ts)
        status = "OK" if (age_s is not None and age_s < 7200) else "STALE"

        signals   = d.get("signals", {})
        positions = d.get("positions", {})
        realized  = d.get("realized_pnl", 0.0)

        rows = ""
        for asset in sorted(signals.keys()):
            sig = signals[asset]
            pos = positions.get(asset, {})
            price    = sig.get("price", "—")
            macd     = sig.get("macd", "—")
            sl       = sig.get("signal_line", "—")
            hist     = sig.get("hist", "—")
            signal   = sig.get("signal", "—")
            side     = pos.get("side", "flat")
            upnl     = pos.get("unrealized_pnl", 0.0)

            def fmt(v):
                try: return f"{float(v):.4f}"
                except Exception: return str(v)

            rows += (
                f"| {asset} | {fmt(price)} | {fmt(macd)} | {fmt(sl)} "
                f"| {fmt(hist)} | {signal} | {side} | {fmt(upnl)} |\n"
            )

        body = (
            f"| Asset | Price | MACD | Signal Line | Hist | Signal | Position | Unrealized PnL |\n"
            f"|-------|-------|------|-------------|------|--------|----------|----------------|\n"
            + rows
            + f"\n**Realized PnL:** `${realized:.4f}`\n"
        )
        if file_ts:
            body += f"\n_State timestamp: {from_ts(file_ts)}_\n"

        fm = (
            f"---\ntype: live-status\nupdated: {ts}\nstatus: {status}\n---\n\n"
            f"# MACD Signals\n\n> Updated: {human}\n\n"
        )
        return fm + body + footer()

    except Exception as e:
        return note("MACD Signals", "ERROR", f"_Error reading state: {e}_\n")


def build_vol_regime():
    """Reads vol_regime_latest.json — no portal dependency."""
    src = os.path.expanduser("~/Documents/polymarket/vol_regime_latest.json")
    ts = now_iso()
    human = now_human()
    try:
        with open(src, encoding="utf-8") as f:
            d = json.load(f)

        file_ts   = d.get("ts", "")
        iv_regime = d.get("iv_regime", "unknown")
        btc_dvol  = d.get("btc_dvol", "—")
        eth_dvol  = d.get("eth_dvol", "—")
        iv_rv_btc = d.get("iv_rv_spread_btc", "—")
        iv_rv_eth = d.get("iv_rv_spread_eth", "—")
        btc_hv20  = d.get("btc_hv20", "—")
        eth_hv20  = d.get("eth_hv20", "—")
        btc_hv_pct = d.get("btc_hv_pct", "—")
        eth_hv_pct = d.get("eth_hv_pct", "—")

        # Traffic-light based on BTC DVOL
        try:
            dvol_f = float(btc_dvol)
            if dvol_f > 60:
                light = "🔴 elevated"
            elif dvol_f < 30:
                light = "🟢 suppressed"
            else:
                light = "🟡 normal"
        except Exception:
            light = "⚪ unknown"

        status = "OK" if iv_regime != "unknown" else "STALE"

        def fp(v, unit=""):
            try: return f"{float(v):.2f}{unit}"
            except Exception: return str(v)

        body = (
            f"| Metric | BTC | ETH |\n"
            f"|--------|-----|-----|\n"
            f"| DVOL (implied vol) | {fp(btc_dvol)} | {fp(eth_dvol)} |\n"
            f"| HV20 (realized vol) | {fp(btc_hv20)} | {fp(eth_hv20)} |\n"
            f"| HV20 % | {fp(btc_hv_pct, '%')} | {fp(eth_hv_pct, '%')} |\n"
            f"| IV-RV Spread | {fp(iv_rv_btc)} | {fp(iv_rv_eth)} |\n"
            f"\n**IV Regime:** `{iv_regime}` — {light}\n"
        )
        if file_ts:
            body += f"\n_Data as of: {file_ts}_\n"

        fm = (
            f"---\ntype: live-status\nupdated: {ts}\nstatus: {status}\n"
            f"iv_regime: {iv_regime}\nbtc_dvol: {btc_dvol}\neth_dvol: {eth_dvol}\n"
            f"iv_rv_spread_btc: {iv_rv_btc}\n---\n\n"
            f"# Vol Regime\n\n> Updated: {human}\n\n"
        )
        return fm + body + footer()

    except Exception as e:
        return note("Vol Regime", "ERROR", f"_Error reading state: {e}_\n")


def build_alerts_log():
    """Reads last 20 lines of pnl_alert.log — no portal dependency."""
    src = os.path.expanduser("~/Documents/polymarket/pnl_alert.log")
    ts = now_iso()
    human = now_human()
    try:
        with open(src, encoding="utf-8") as f:
            lines = f.readlines()

        last20 = lines[-20:] if len(lines) >= 20 else lines
        block  = "".join(last20).rstrip()

        # Count alerts in last 24 h by scanning timestamps if present;
        # fall back to counting non-empty lines in last 20 as a proxy.
        cutoff = datetime.now(timezone.utc).timestamp() - 86400
        count_24h = 0
        for ln in lines:
            ln = ln.strip()
            if not ln:
                continue
            # Try ISO prefix e.g. "2026-06-27T10:11:33Z …"
            try:
                dt = datetime.strptime(ln[:20], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                if dt.timestamp() >= cutoff:
                    count_24h += 1
            except Exception:
                # No parseable timestamp — count non-separator lines
                if not ln.startswith("─") and ln:
                    count_24h += 1  # rough proxy for non-timestamped logs

        body = (
            f"**Alerts in last 24 h:** `{count_24h}`\n\n"
            f"```\n{block}\n```\n"
        )

        fm = (
            f"---\ntype: live-status\nupdated: {ts}\n---\n\n"
            f"# Portal Alerts\n\n> Updated: {human}\n\n"
        )
        return fm + body + footer()

    except FileNotFoundError:
        return note("Portal Alerts", "OK", "_No alert log found yet._\n")
    except Exception as e:
        return note("Portal Alerts", "ERROR", f"_Error reading log: {e}_\n")


# ── offline note ──────────────────────────────────────────────────────────────

def build_offline_note():
    body = """\
## Portal Offline

The ALICE portal at `http://localhost:8090` was unreachable.

Other live notes have been left unchanged.

_Check that `portal.py` is running (launchd job: `com.aryan.portal`)._
"""
    return note("ALICE Portal", "ERROR", body)


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    os.makedirs(VAULT_LIVE, exist_ok=True)

    # Fetch state
    try:
        with urlopen(PORTAL_URL, timeout=TIMEOUT) as resp:
            state = json.loads(resp.read().decode())
    except (URLError, Exception) as e:
        print(f"portal unreachable: {e}", file=sys.stderr)
        write_note("ALICE Portal.md", build_offline_note())
        sys.exit(0)

    # Write each note; fail-soft per panel
    panels = {
        "ALICE Portal.md":   lambda: build_alice_portal(state),
        "Provider Chain.md": lambda: build_provider_chain(state.get("provider", {"error": "missing"})),
        "Arb Track.md":      lambda: build_arb_track(state.get("arb", {"error": "missing"})),
        "Edge Hunt.md":      lambda: build_edge_hunt(state.get("edgehunt", {"error": "missing"})),
        "Edge Bots.md":      lambda: build_edge_bots(state.get("algos", {"error": "missing"})),
        "Crypto Movers.md":  lambda: build_crypto_movers(state.get("crypto", {"error": "missing"})),
        "FII DII Flow.md":   lambda: build_fii_dii(state.get("moneyflow", {"error": "missing"})),
        "DSR Gates.md":        lambda: build_dsr_gates(state.get("scalp", {"error": "missing"})),
        "Market Structure.md": lambda: build_market_structure(state.get("market_structure", {"error": "missing"})),
        "Liquidations.md":     lambda: build_liquidations(state.get("liquidations", {"error": "missing"})),
        "Hermes News.md":      lambda: build_hermes_news(state.get("hermes", {"error": "missing"})),
        "Edge Bots Live.md":   lambda: build_edgebots_live(state.get("edgebots_live", {"error": "missing"})),
        "Sentiment.md":        lambda: build_sentiment(state.get("market_structure", {"error": "missing"})),
        # File-backed builders — independent of portal state
        "MACD Signals.md":     lambda: build_macd(),
        "Vol Regime.md":       lambda: build_vol_regime(),
        "Portal Alerts.md":    lambda: build_alerts_log(),
    }

    for filename, builder in panels.items():
        try:
            content = builder()
            write_note(filename, content)
        except Exception as e:
            print(f"ERROR building {filename}: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
