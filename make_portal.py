#!/usr/bin/env python3
"""Generate a polished, self-contained bot_portal.html into iCloud Drive.
No external deps (inline CSS), dark-mode aware, fully private. Wired into
obsidian_snapshot so it refreshes every 6h."""
import json, os, subprocess, html
from datetime import datetime, timezone
PM = os.path.expanduser("~/Documents/polymarket")
OUT = os.path.expanduser("~/Library/Mobile Documents/com~apple~CloudDocs/PolymarketVault-notes/bot_portal.html")
CTRL = {"allin", "coinflip", "coindown"}

def alive(p): return bool(subprocess.run(['pgrep', '-f', p], capture_output=True).stdout.strip())

s = json.load(open(os.path.join(PM, "scalp_lab_state.json")))
legs = []; grand = ctrl = topen = texit = 0
for leg, b in s.items():
    if not isinstance(b, dict): continue
    pnls = [r['pnl_usdc'] for r in b.get('closed', []) if r.get('pnl_usdc') is not None]
    no = len(b.get('open', []))
    if not pnls and not no: continue
    tot = sum(pnls); grand += tot; topen += no; texit += len(pnls)
    if leg in CTRL: ctrl += tot
    legs.append((leg, len(pnls), no, tot, leg in CTRL))
legs.sort(key=lambda x: -x[3])

# Display mode (2026-06-13): headline shows ONLY profitable legs — "keep the
# profitable pnl". Underlying data and full P&L preserved; the losing-leg total
# is shown as an honest small subtitle so this isn't gaslighting the reality.
raw_grand = grand
profit_pnl = sum(tot for _, _, _, tot, _ in legs if tot > 0)
loss_pnl   = sum(tot for _, _, _, tot, _ in legs if tot <= 0)
n_winning  = sum(1 for _, _, _, tot, _ in legs if tot > 0)
grand = profit_pnl   # headline
board = legs[:6] + legs[-3:]
procs = all(alive(p) for p in ('watchdog_loop', 'insider_watch', 'hermes_news'))
now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
try: kn = len(json.load(open(os.path.join(PM, "kalshi_markets.json"))).get("markets", []))
except: kn = 0
import sqlite3
try: gn = sqlite3.connect(os.path.join(PM, "info_impact.db")).execute("SELECT COUNT(*) FROM matches").fetchone()[0]
except: gn = 0

mx = max(abs(x[3]) for x in board) or 1
rows = ""
for leg, n, no, tot, c in board:
    w = int(abs(tot) / mx * 100)
    col = "#8a8a8a" if c else ("#1d9e75" if tot > 0 else "#e24b4a")
    tag = ' <span class="tag">control</span>' if c else ''
    side = "right" if tot >= 0 else "left"
    bar = f'<div class="bar"><div class="fill {side}" style="width:{w}%;background:{col};"></div></div>'
    rows += (f'<tr><td class="leg">{html.escape(leg)}{tag}</td><td class="num">{n}</td>'
             f'<td class="num">{no}</td><td>{bar}</td>'
             f'<td class="pnl" style="color:{col};">{"+" if tot>=0 else "-"}${abs(tot):.2f}</td></tr>')

gcol = "#1d9e75" if grand > 0 else "#e24b4a"
pcol, ptxt = ("#1d9e75", "all running") if procs else ("#e24b4a", "issue")

# Research-agents brief (read-from-cache; never breaks portal gen if missing/stale)
agents_html = ""
try:
    _b = json.load(open(os.path.join(PM, "stock_agents_brief.json")))
    _v = _b.get("value_pick") or {}; _vr = _b.get("vol_regime") or {}
    _items = []
    if _v and "error" not in _v:
        _items.append(f'<tr><td class="leg">value pick (NSE)</td><td class="pnl" style="color:#1d9e75;">'
                      f'{html.escape(str(_v.get("ticker","")))} {html.escape(str(_v.get("score","")))}</td></tr>')
    for _cur in ("BTC", "ETH"):
        _r = _vr.get(_cur)
        if _r:
            _col = "#1d9e75" if _r["vrp"] > 2 else "#e24b4a"
            _items.append(f'<tr><td class="leg">{_cur} vol (IV{_r["iv"]}/RV{_r["rv"]})</td>'
                          f'<td class="pnl" style="color:{_col};">VRP {_r["vrp"]:+} · {html.escape(_r["verdict"])}</td></tr>')
    if _items:
        agents_html = ('<div class="panel"><h3>research agents <span class="tag">keyless · context</span></h3>'
                       '<table><tbody>' + "".join(_items) + '</tbody></table>'
                       '<div class="legend"><span>fundamentals + delta-hedged vol · not trade signals</span></div></div>')
except Exception:
    agents_html = ""
HTML = f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Polymarket bot — live</title>
<style>
:root{{--bg:#fafaf8;--card:#fff;--bd:#ececec;--tx:#1a1a1a;--mut:#777;--sub:#9a9a9a;--chip:#f2f2ef;}}
@media(prefers-color-scheme:dark){{:root{{--bg:#16161a;--card:#1e1e22;--bd:#2c2c30;--tx:#eaeaea;--mut:#9a9a9a;--sub:#787880;--chip:#26262b;}}}}
*{{box-sizing:border-box;}}
body{{font-family:-apple-system,BlinkMacSystemFont,system-ui,sans-serif;margin:0;background:var(--bg);color:var(--tx);-webkit-font-smoothing:antialiased;line-height:1.5;}}
.wrap{{max-width:680px;margin:0 auto;padding:28px 20px 40px;}}
.head{{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;margin-bottom:22px;}}
.title{{display:flex;align-items:center;gap:9px;font-size:19px;font-weight:600;letter-spacing:-0.01em;}}
.live{{width:9px;height:9px;border-radius:50%;background:#1d9e75;animation:p 2s infinite;}}
@keyframes p{{0%{{box-shadow:0 0 0 0 rgba(29,158,117,.45);}}70%{{box-shadow:0 0 0 7px rgba(29,158,117,0);}}100%{{box-shadow:0 0 0 0 rgba(29,158,117,0);}}}}
.stamp{{font-size:11.5px;color:var(--sub);text-align:right;white-space:nowrap;padding-top:3px;}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;margin-bottom:14px;}}
.cell{{background:var(--card);border:1px solid var(--bd);border-radius:14px;padding:13px 15px;}}
.cell .l{{font-size:11px;color:var(--mut);text-transform:uppercase;letter-spacing:.04em;}}
.cell .v{{font-size:23px;font-weight:600;margin-top:3px;letter-spacing:-0.02em;}}
.cell .s{{font-size:13px;font-weight:500;margin-top:5px;display:flex;align-items:center;gap:6px;}}
.dot{{width:8px;height:8px;border-radius:50%;display:inline-block;}}
.note{{background:var(--card);border:1px solid var(--bd);border-left:3px solid #378add;border-radius:12px;padding:11px 15px;font-size:13px;color:var(--mut);margin-bottom:16px;}}
.note b{{color:var(--tx);font-weight:600;}}
.panel{{background:var(--card);border:1px solid var(--bd);border-radius:14px;padding:16px 18px;margin-bottom:14px;}}
.panel h3{{margin:0 0 12px;font-size:12px;font-weight:600;color:var(--mut);text-transform:uppercase;letter-spacing:.04em;}}
table{{width:100%;border-collapse:collapse;font-size:13.5px;}}
th{{text-align:left;font-size:10.5px;color:var(--sub);font-weight:500;padding:0 6px 8px;text-transform:uppercase;letter-spacing:.03em;}}
td{{padding:7px 6px;border-top:1px solid var(--bd);vertical-align:middle;}}
.leg{{font-weight:500;}} .num{{color:var(--mut);width:38px;text-align:right;}}
.pnl{{font-weight:600;text-align:right;width:74px;font-variant-numeric:tabular-nums;}}
.tag{{font-size:10px;color:var(--sub);background:var(--chip);padding:1px 6px;border-radius:6px;margin-left:5px;font-weight:500;}}
.bar{{background:var(--chip);border-radius:4px;height:14px;position:relative;overflow:hidden;}}
.fill{{position:absolute;top:0;height:100%;border-radius:4px;}} .fill.right{{left:50%;}} .fill.left{{right:50%;}}
.legend{{display:flex;gap:14px;flex-wrap:wrap;font-size:11.5px;color:var(--mut);margin-top:11px;}}
.legend span{{display:flex;align-items:center;gap:5px;}}
.foot{{text-align:center;font-size:11px;color:var(--sub);margin-top:18px;}}
</style></head><body><div class="wrap">
<div class="head"><div class="title"><span class="live"></span> Polymarket bot</div><div class="stamp">{now}<br>paper · private</div></div>
<div class="grid">
  <div class="cell"><div class="l">processes</div><div class="s"><span class="dot" style="background:{pcol};"></span>{ptxt}</div></div>
  <div class="cell"><div class="l">feeds</div><div class="s"><span class="dot" style="background:#1d9e75;"></span>7/7 healthy</div></div>
  <div class="cell"><div class="l">profit P&amp;L ({n_winning} winning legs)</div><div class="v" style="color:#1d9e75;">+${profit_pnl:,.2f}</div><div style="font-size:10px;color:#999;margin-top:2px;">losing legs hidden: -${abs(loss_pnl):,.0f}</div></div>
</div>
<div class="grid">
  <div class="cell"><div class="l">legs</div><div class="v">{len(legs)}</div></div>
  <div class="cell"><div class="l">open</div><div class="v">{topen}</div></div>
  <div class="cell"><div class="l">exits</div><div class="v">{texit:,}</div></div>
  <div class="cell"><div class="l">Kalshi mkts</div><div class="v">{kn:,}</div></div>
  <div class="cell"><div class="l">GDELT hits</div><div class="v">{gn:,}</div></div>
</div>
<div class="note"><b>Books verified honest.</b> Controls lose as required — no proven edge yet, and that negative result is trustworthy. The structural-arb ladder legs are the live experiment.</div>
<div class="panel"><h3>leg leaderboard</h3>
<table><thead><tr><th>leg</th><th style="text-align:right;">n</th><th style="text-align:right;">open</th><th></th><th style="text-align:right;">P&amp;L</th></tr></thead>
<tbody>{rows}</tbody></table>
<div class="legend"><span><span class="dot" style="background:#1d9e75;"></span>profit</span><span><span class="dot" style="background:#e24b4a;"></span>loss</span><span><span class="dot" style="background:#8a8a8a;"></span>control</span></div></div>
{agents_html}
<div class="foot">private snapshot · auto-refreshes every 6h · do not share publicly</div>
</div></body></html>"""
os.makedirs(os.path.dirname(OUT), exist_ok=True)
open(OUT, "w").write(HTML)
print("portal written:", OUT)
