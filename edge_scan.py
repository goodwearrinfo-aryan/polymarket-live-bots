#!/usr/bin/env python3
"""
edge_scan.py — cheap DETERMINISTIC scheduled edge scan (NO LLM, paper, read-only).

Runs the two checks that aren't already scheduled elsewhere: live basket locks +
crypto data-arb "already-settled by spot but price hasn't caught up" (the one proven
vein). Reads edge_ledger failure-patterns (the loop), logs to the vault, and
iMessages ONLY on a real, new candidate (deduped + quiet-hours aware via worker_lib).

Low yield BY DESIGN — the venue is calibrated, so the honest steady state is NULL.
This is an autonomous re-scan that pings the instant something de-calibrates, not a hunt.
Run: python3 edge_scan.py once
"""
import os, sys, json, re, urllib.request
import worker_lib as W

STEM = "edge_scan"
CG = {"chainlink": "chainlink", "link": "chainlink", "uni": "uniswap", "uniswap": "uniswap",
      "bnb": "binancecoin", "ethena": "ethena", "zcash": "zcash", "bitcoin": "bitcoin",
      "btc": "bitcoin", "ethereum": "ethereum", "eth": "ethereum"}


def _gj(url):
    return json.load(urllib.request.urlopen(
        urllib.request.Request(url, headers={"User-Agent": "r"}), timeout=25))


def basket_locks():
    try:
        import basket_arb
        return sorted(basket_arb.get_locked_baskets(0.0), key=lambda b: -b.get("edge", 0))[:5]
    except Exception as e:
        W.log(STEM, f"basket_arb err {e}")
        return []


def data_settled():
    """Crypto reach/dip markets the data ALREADY settles in-window but price hasn't.
    Uses the FIXED unit parser (no '$500 by' → 5e11 bug) and spot-only settlement
    (sufficient, no false positives)."""
    hits = []
    try:
        book = []
        for off in (0, 500, 1000, 1500):
            ms = _gj(f"https://gamma-api.polymarket.com/markets?closed=false&limit=500&offset={off}")
            if not ms:
                break
            book += ms
        cands = []
        for m in book:
            ql = (m.get("question") or "").lower()
            t = re.search(r"\$([0-9][0-9,\.]*)([kmb])?(?![a-z0-9])", ql)
            if not t:
                continue
            thr = float(t.group(1).replace(",", "")) * {"k": 1e3, "m": 1e6, "b": 1e9, None: 1}[t.group(2)]
            d = "dip" if any(w in ql for w in ("dip to", "fall to", "drop to")) else \
                ("reach" if any(w in ql for w in ("reach", "hit ", "above")) else None)
            asset = next((CG[w] for w in re.findall(r"[a-z]+", ql) if w in CG), None)
            if not (asset and d):
                continue
            try:
                y = float(json.loads(m.get("outcomePrices") or "[]")[0])
            except Exception:
                continue
            cands.append((m.get("question"), asset, thr, d, y, m.get("slug")))
        ids = sorted({c[1] for c in cands})
        spot = _gj("https://api.coingecko.com/api/v3/simple/price?ids=" + ",".join(ids) +
                   "&vs_currencies=usd") if ids else {}
        for q, asset, thr, d, y, slug in cands:
            s = spot.get(asset, {}).get("usd")
            if s is None:
                continue
            already = (s >= thr) if d == "reach" else (s <= thr)
            if already and y < 0.90:
                hits.append({"q": q, "slug": slug, "spot": s, "thr": thr, "dir": d, "y": y})
    except Exception as e:
        W.log(STEM, f"data_settled err {e}")
    return hits


def main():
    locks = basket_locks()
    settled = data_settled()
    patterns = []
    try:
        import edge_ledger
        patterns = [p["type"] for p in edge_ledger.recent_failures().get("avoid", [])[:5]]
    except Exception:
        pass

    alerts = []
    for h in settled:                       # spot-settled + cheap = a real LEAD (verify on book)
        if W.is_new_alert(STEM, f"settled:{h['slug']}"):
            alerts.append(f"data-arb? {h['q'][:48]} | spot ${h['spot']} {h['dir']} ${h['thr']:g} MET, YES {h['y']:.2f}")
    # NOTE: basket locks are NOT alerted here — basket-watch / pm-arb-track already own
    # them, and the loop ledger shows they're the capacity-dead / already-covered class.
    # They stay in the vault report below for context; the data-arb vein is the only alert.
    if alerts:
        W.send_imessage("🔎 edge_scan lead(s):\n" + "\n".join(alerts[:5]))

    body = [f"# Edge Scan (deterministic) — {len(locks)} locks, {len(settled)} data-settled",
            f"\n_loop: recent failure-types to skip → {patterns}_\n"]
    if settled:
        body.append("## data-arb leads (CONFIRM on the executable book before trust)\n" +
                    "\n".join(f"- {h['q'][:60]} | spot ${h['spot']} vs ${h['thr']:g} | YES {h['y']}" for h in settled))
    if locks:
        body.append("## basket locks\n" +
                    "\n".join(f"- {str(b.get('title','?'))[:50]} | edge={b.get('edge')}" for b in locks))
    if not settled and not locks:
        body.append("**NULL** — no deterministic candidate (expected on a calibrated venue).")
    W.vault_write(STEM, "\n".join(body))
    W.log(STEM, f"locks={len(locks)} settled={len(settled)} alerts={len(alerts)}")


if __name__ == "__main__":
    main()
