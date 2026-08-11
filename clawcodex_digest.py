#!/usr/bin/env python3
"""
clawcodex_digest.py — the 24/7 job for ClawCodex, done the SAFE way.

Design (mirrors the project's own harness: Python gathers, LLM only judges):
  1. THIS script fetches + holds the data (crypto spot, latest FII/DII) — verified, keyless.
  2. ClawCodex runs in REASONING-ONLY mode (no tools at all) over that data block, so there
     is NO WebFetch, NO shell, NO filesystem, and NO hallucination-on-denial risk — the data
     it reasons over is already in the prompt and already true.
  3. Output is written to Obsidian as LLM COMMENTARY, explicitly NOT a signal.

Honest by construction: clawcodex never touches a tool here; it cannot fabricate the data
(it's given) and cannot act (no tools). It only produces a paragraph of interpretation, which
the project treats as commentary, never a conviction (free-LLM p is miscalibrated).
"""
import json, os, subprocess, sys, urllib.request
from datetime import datetime, timezone

HERE   = os.path.dirname(os.path.abspath(__file__))
WRAP   = os.path.join(HERE, "clawcodex_agent.py")
VAULT  = os.path.expanduser("~/Documents/PolymarketVault/Reports/ClawCodex Market Read.md")
FIIDII = os.path.join(HERE, "fii_dii_log.jsonl")

def _get(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        return json.loads(urllib.request.urlopen(req, timeout=12).read())
    except Exception:
        return None

def gather():
    """Verified data block — keyless. Returns (text_block, raw_dict) or (None, None)."""
    cg = _get("https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum,solana"
              "&vs_currencies=usd&include_24hr_change=true")
    if not cg:
        return None, None
    lines = ["Crypto (CoinGecko, live):"]
    for k, sym in (("bitcoin","BTC"),("ethereum","ETH"),("solana","SOL")):
        d = cg.get(k, {})
        lines.append(f"  {sym}: ${d.get('usd')}  24h {d.get('usd_24h_change',0):+.2f}%")
    # latest FII/DII from the local logger
    try:
        rows = [json.loads(l) for l in open(FIIDII) if l.strip()]
        if rows:
            r = rows[-1]
            lines.append(f"India institutional flows ({r['date']}, NSE cash, Rs cr): "
                         f"FII net {r.get('FII_net',0):+.0f}, DII net {r.get('DII_net',0):+.0f}")
    except Exception:
        pass
    return "\n".join(lines), cg

def main():
    block, raw = gather()
    if not block:
        print("data fetch failed — no digest written", file=sys.stderr); sys.exit(1)
    prompt = ("You are given VERIFIED market data below. Do NOT fetch anything; reason only "
              "over what is given. In 3-4 sentences: read the crypto tape (risk-on/off, "
              "what's leading/lagging) and note what the FII/DII flow implies for Indian "
              "equities. End with one line: 'Commentary only — not a trade signal.'\n\n"
              f"DATA:\n{block}")
    # reasoning-only: NO --allow-domain → all tools denied (wrapper blocks the dangerous ones too)
    try:
        out = subprocess.run([sys.executable, WRAP, "--max-turns", "3", prompt],
                             capture_output=True, text=True, timeout=120)
        read = (out.stdout or "").strip() or "(no output)"
    except Exception as e:
        read = f"(clawcodex run failed: {e})"
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    note = (f"---\ntype: dashboard\nstatus: live\n---\n\n"
            f"# 🐋 ClawCodex Market Read\n\n"
            f"> ⚠️ **LLM COMMENTARY — NOT a signal.** Free-NVIDIA reasoning over verified data; "
            f"free-LLM output is miscalibrated and must clear the brain gate before it means anything. "
            f"Auto-generated {now} by clawcodex_digest.py (reasoning-only, zero tools).\n\n"
            f"## Verified data in\n```\n{block}\n```\n\n## ClawCodex read\n{read}\n")
    try:
        os.makedirs(os.path.dirname(VAULT), exist_ok=True)
        open(VAULT, "w").write(note)
        print(f"wrote {VAULT}")
    except Exception as e:
        print(f"vault write failed: {e}", file=sys.stderr); sys.exit(1)
    sys.exit(0)

if __name__ == "__main__":
    main()
