#!/usr/bin/env python3
"""wallet_report.py — build wallet_intel_report.pdf: SHARP wallet intelligence,
reverse-engineered algos, position-level P&L anatomy, and lessons for the lab.
Reads wallet_algo.json + wallet_deep.json (run those first). Paper research only."""
import os, json, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
OUT  = os.path.join(HERE, "wallet_intel_report.pdf")
NOW  = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
DATE = datetime.datetime.now().strftime("%A, %d %B %Y")


def build():
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import inch
    from reportlab.lib import colors
    from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer,
                                    HRFlowable, Table, TableStyle)
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

    deep = json.load(open(os.path.join(HERE, "wallet_deep.json")))
    algo = json.load(open(os.path.join(HERE, "wallet_algo.json")))

    def esc(s): return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    ss = getSampleStyleSheet()
    H1  = ParagraphStyle('H1', parent=ss['Title'], fontSize=17, spaceAfter=2)
    SUB = ParagraphStyle('SUB', parent=ss['Normal'], fontSize=8.5, textColor=colors.grey, spaceAfter=8)
    H2  = ParagraphStyle('H2', parent=ss['Heading2'], fontSize=12, spaceBefore=10, spaceAfter=4,
                         textColor=colors.HexColor('#1a3a5c'))
    B   = ParagraphStyle('B', parent=ss['Normal'], fontSize=9, leading=12.5, spaceAfter=4)
    SM  = ParagraphStyle('SM', parent=ss['Normal'], fontSize=8, leading=10.5, spaceAfter=3)

    s = []
    s.append(Paragraph("SHARP Wallet Intelligence Report", H1))
    s.append(Paragraph(f"{DATE} · Generated {NOW} · PAPER RESEARCH ONLY — no real money, "
                       "public on-chain/API data", SUB))
    s.append(HRFlowable(width="100%", color=colors.lightgrey))

    # 1 · What this is
    s.append(Paragraph("1 · What this report is", H2))
    s.append(Paragraph(
        "We track 7 profitable Polymarket wallets ('SHARP' watchlist) found by scanning "
        "top traders by win rate and realized profit. Every new trade they make fires an "
        "iMessage alert within 60 seconds (insider_watch.py). This report explains WHO "
        "they are, HOW each one trades (reverse-engineered from up to 600 recent trades "
        "per wallet), and WHERE their money is actually made or lost (position-level "
        "anatomy across ~5,000 positions). The goal: learn from real winners, avoid "
        "copying fake ones.", B))

    # 2 · Summary table
    s.append(Paragraph("2 · The seven wallets — net P&amp;L truth", H2))
    s.append(Paragraph(
        "'Realized' = profit already banked. 'Open' = unrealized P&amp;L on current "
        "positions. NET = the honest number. A wallet can look brilliant on realized "
        "P&amp;L while sitting on huge open losses — the same win-rate trap our own lab "
        "guards against.", B))
    rows = [["Wallet", "Style", "Net $", "Realized $", "Open $", "Verdict"]]
    style_map = {
        "SHARP-80pct-$112k":        "Sports ladder bot (live games)",
        "SHARP-74pct-$47k":         "Crypto 5-min coinflip set-arb",
        "SHARP-62pct-$23k":         "Crypto 5-min coinflip set-arb",
        "SHARP-49pct-$34k":         "Crypto 5-min coinflip set-arb",
        "SHARP-46pct-$1.4k":        "Favorites ladder (no time gate)",
        "SHARP-57pct-$199":         "Human — geopolitics news",
        "SHARP-18pct-$3.7k-bigbets": "Politics wide-net, big bets",
    }
    order = sorted(deep.items(), key=lambda kv: -(kv[1].get("net") or 0))
    for addr, d in order:
        lbl = d.get("label", "?")
        net = d.get("net", 0) or 0
        verdict = ("REAL WINNER" if net > 10000 else
                   "marginal" if net > 0 else "NET LOSER")
        rows.append([lbl.replace("SHARP-", ""), style_map.get(lbl, "?"),
                     f"{net:+,.0f}", f"{d.get('realized',0):+,.0f}",
                     f"{d.get('unrealized',0):+,.0f}", verdict])
    t = Table(rows, colWidths=[1.05*inch, 2.0*inch, 0.8*inch, 0.85*inch, 0.8*inch, 1.0*inch])
    t.setStyle(TableStyle([
        ('FONTSIZE', (0, 0), (-1, -1), 7.5),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#e8eef5')),
        ('GRID', (0, 0), (-1, -1), 0.4, colors.lightgrey),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
    ]))
    s.append(t)

    # 3 · Per-wallet explanation
    s.append(Paragraph("3 · How each wallet actually trades", H2))
    notes = {
        "SHARP-80pct-$112k": (
            "A bot trading live sports (MLB/NBA/soccer spreads and totals) — 95% of its "
            "trades are under 60s apart, laddering into team spreads around 0.5-0.6. "
            "NET +$41k, the only clearly profitable non-crypto wallet. BUT: it loses "
            "heavily in esports (−$54k over just 20 positions, one −$44.5k LoL bet). "
            "Its profit lives in CHEAP entries: bands 0.0-0.3 earn +$127k, bands 0.4-0.9 lose."),
        "SHARP-74pct-$47k": (
            "Pure 5-minute 'Bitcoin Up or Down' bot. Fires every ~4 seconds, buys BOTH "
            "sides of the coinflip cheap and redeems complete sets at $1. NET +$50k — "
            "genuinely profitable, but it is market-making machinery, not prediction. Its "
            "high 'win rate' is set-redemption accounting."),
        "SHARP-62pct-$23k": (
            "Same playbook as above across BTC/ETH/SOL 5-min markets. NET +$22k. "
            "Profit concentrated at entries under 0.30; everything above 0.40 loses."),
        "SHARP-49pct-$34k": (
            "Third coinflip set-arb bot. NET only +$4k — the thinnest margin of the trio, "
            "showing how competitive this niche is."),
        "SHARP-46pct-$1.4k": (
            "The cautionary tale: ladders into FAVORITES at 0.8-0.9 across sports, esports, "
            "and crypto — superficially the closest wallet to our own nearres strategy. "
            "NET −$5.8k, losing in every category. Lesson: the favorites band alone is NOT "
            "an edge; nearres works because of the <4h-to-resolution esports filter, which "
            "this wallet lacks."),
        "SHARP-57pct-$199": (
            "The only human (~2 trades/day): geopolitics headlines — Iran sanctions, Israel "
            "airspace, ceasefires. Buys near-certainty at 0.90-1.00 and holds. NET −$537: "
            "roughly breakeven, not an edge worth copying."),
        "SHARP-18pct-$3.7k-bigbets": (
            "Wide-net event bot with occasional $10k bets. Politics is genuinely +$3.6k, "
            "but one −$7.7k cricket bet wiped it out; NET −$1.5k. Sells a third of positions "
            "early — active risk management, lottery-style book."),
    }
    for addr, d in order:
        lbl = d.get("label", "?")
        s.append(Paragraph(f"<b>{esc(lbl)}</b> — {esc(notes.get(lbl, ''))}", SM))

    # 4 · Lessons
    s.append(Paragraph("4 · What this means for our lab", H2))
    for lesson in (
        "All profitable wallets earn at CHEAP entries (0.00-0.30) and bleed at 0.40-0.90. "
        "Whale money is in longshot/both-sides value, not favorites.",
        "Even the best wallet loses badly in esports — big money avoids or fails in our "
        "lane. nearres's [0.88-0.95] <4h esports niche stays uncrowded (consistent with "
        "the literature: insiders concentrate in politics, not esports).",
        "The nearres-lookalike wallet (favorites ladder, no time gate) is a NET LOSER — "
        "a live negative control proving the time-to-resolution filter is the edge, "
        "not the price band.",
        "Win rate without dollars is the same trap everywhere: 3 of 7 'SHARP' wallets "
        "with 73-86% win rates are net losers.",
        "The crypto coinflip trio validates the csarb (complete-set) thesis — but they "
        "win on 3-second maker cadence our 60s paper loop cannot honestly replicate.",
    ):
        s.append(Paragraph("• " + esc(lesson), B))

    # 5 · Monitoring
    s.append(Paragraph("5 · Live monitoring", H2))
    s.append(Paragraph(
        "insider_watch.py polls all 7 wallets every 60s (launchd, auto-restart) and sends "
        "every new trade to iMessage in full: side, outcome, price, dollars, share count, "
        "full market title, and a polymarket.com link. Profilers: wallet_algo.py (trade "
        "timing/category/band) and wallet_deep.py (position anatomy) — re-run monthly or "
        "when a wallet's behaviour shifts.", B))

    SimpleDocTemplate(OUT, pagesize=letter,
                      topMargin=0.55*inch, bottomMargin=0.55*inch,
                      leftMargin=0.6*inch, rightMargin=0.6*inch,
                      title=f"SHARP Wallet Intelligence {DATE}").build(s)
    print(f"wrote {OUT}")


if __name__ == "__main__":
    build()
