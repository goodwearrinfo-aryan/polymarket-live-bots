#!/usr/bin/env python3
"""flow_logger.py — READ-ONLY analysis logger (places NO trades). Does smart-money net USD flow into
a market line up with the position's live P&L? Paper-only, stdlib only.
Usage: python3 flow_logger.py once | report"""
import sys
import ports_analysis_common as C

NAME = "flow"


def _pearson(xs, ys):
    """Pearson correlation over paired samples; None if <3 points or zero variance."""
    n = len(xs)
    if n < 3:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return round(sxy / (sxx ** 0.5 * syy ** 0.5), 4)


def _sign(x):
    return 1 if x > 0 else (-1 if x < 0 else 0)


def once():
    d, age = C.load_ports()
    if d is None:
        print(NAME + ": no ports_data.json snapshot — skipped"); return
    positions = d.get("positions", []) or []
    fp = [p for p in positions if p.get("netflow") is not None]

    # net flow deduplicated to DISTINCT markets: a market held by N legs replicates the SAME
    # market-level netflow N times, so a naive per-position sum overstates distinct inflow by
    # leg multiplicity. The honest headline is the per-market sum; keep the raw sum for reference.
    by_market = {}
    for p in fp:
        by_market[p.get("q") or p.get("sym")] = p["netflow"]
    total_netflow_markets = round(sum(by_market.values()), 1)
    n_markets = len(by_market)
    total_netflow_raw = round(sum(p["netflow"] for p in fp), 1)   # per-position (double-counts shared markets)

    # top 10 DISTINCT markets by net USD inflow
    top_mkt = sorted(by_market.items(), key=lambda kv: kv[1], reverse=True)[:10]
    sample = {(p.get("q") or p.get("sym")): p for p in fp}
    top_inflow = []
    for q, nf in top_mkt:
        p = sample.get(q, {})
        top_inflow.append({"sym": p.get("sym"), "strat": p.get("strat"), "side": p.get("side"),
                           "netflow": nf, "momentum": p.get("momentum"), "unreal": p.get("unreal")})

    # flow-vs-pnl correlation over positions with a live mark
    pairs = [(p["netflow"], p["unreal"]) for p in fp if p.get("unreal") is not None]
    flow_unreal_corr = _pearson([x for x, _ in pairs], [y for _, y in pairs]) if pairs else None

    # alignment: among positions with non-zero flow AND non-zero P&L, do the signs agree?
    sig = [p for p in fp if p.get("unreal") is not None and p["unreal"] != 0 and p["netflow"] != 0]
    aligned_share = (round(sum(1 for p in sig if _sign(p["netflow"]) == _sign(p["unreal"])) / len(sig), 3)
                     if sig else None)

    rec = {"ts": C.ts_iso(), "gen": d.get("generated_at"), "n_flow": len(fp), "n_markets": n_markets,
           "total_netflow_markets": total_netflow_markets, "total_netflow_raw": total_netflow_raw,
           "flow_unreal_corr": flow_unreal_corr, "aligned_share": aligned_share, "top_inflow": top_inflow}
    C.append_jsonl(NAME, rec, dedup_key="gen")
    report()
    corr_s = "n/a" if flow_unreal_corr is None else f"{flow_unreal_corr:+.4f}"
    al_s = "n/a" if aligned_share is None else f"{aligned_share:.3f}"
    print(NAME + ": " + str(n_markets) + " distinct markets ($" + f"{total_netflow_markets:,.0f}"
          + " net inflow) across " + str(len(fp)) + " positions · corr(flow,unreal)=" + corr_s
          + " aligned=" + al_s)


def report():
    rows = C.read_jsonl(NAME)
    lines = ["# Flow Logger — smart-money net flow vs live P&L", "",
             "> READ-ONLY logger (no trades). Tracks on-chain net USD flow per held market and asks whether "
             "flow lines up with the position's mark-to-market P&L.",
             "> Updated " + C.ts_iso() + " · " + str(len(rows)) + " samples", ""]
    if not rows:
        lines += ["_No samples yet._"]; C.write_report(NAME, lines); return
    last = rows[-1]
    corr = last.get("flow_unreal_corr"); al = last.get("aligned_share")
    corr_s = "n/a (insufficient/degenerate)" if corr is None else f"{corr:+.4f}"
    al_s = "n/a" if al is None else f"{al:.3f}"
    tnf = last.get("total_netflow_markets"); raw = last.get("total_netflow_raw")
    lines += ["## Latest snapshot", "",
              "- distinct flowed markets: " + str(last.get("n_markets")) + " (across " + str(last.get("n_flow")) + " positions)",
              "- net flow into distinct markets: **$" + (f"{tnf:,.1f}" if isinstance(tnf, (int, float)) else str(tnf)) + "**",
              "- (per-position sum, double-counts shared markets: $" + (f"{raw:,.1f}" if isinstance(raw, (int, float)) else str(raw)) + ")",
              "- corr(netflow, unreal): **" + corr_s + "**",
              "- aligned share (sign netflow == sign unreal): **" + al_s + "**",
              "",
              "## Top inflow markets (highest net USD into the market)", "",
              "| sym | strat | side | net flow $ | momentum | unreal $ |",
              "|-----|-------|------|-----------:|---------:|---------:|"]
    for t in last.get("top_inflow", []):
        nf = t.get("netflow"); mo = t.get("momentum"); ur = t.get("unreal")
        nf_s = f"{nf:,.1f}" if isinstance(nf, (int, float)) else "—"
        mo_s = f"{mo:,.1f}" if isinstance(mo, (int, float)) else "—"
        ur_s = C.usd(ur) if isinstance(ur, (int, float)) else "—"
        lines += ["| " + str(t.get("sym")) + " | " + str(t.get("strat")) + " | " + str(t.get("side")) +
                  " | " + nf_s + " | " + mo_s + " | " + ur_s + " |"]
    lines += ["", "## Honest caveat", "",
              "> `netflow` is the **net USD into the market as a whole**, not flow into the side this position "
              "holds. So direction here is a PROXY, not a per-side smart-money signal: a market can attract net "
              "inflow while our NO leg loses, and the sign-alignment / correlation will look noisy for exactly "
              "that reason. The headline uses the **distinct-market** sum (the per-position sum overstates inflow "
              "by leg multiplicity). Treat corr and aligned_share as a rough sanity gauge, not a tradable edge — "
              "and note they are computed over **per-position** pairs, so a market held by N legs is counted N "
              "times: the effective independent sample is the distinct-market count (`n_markets`), not `n_flow`, "
              "and high-multiplicity markets are over-weighted in both."]
    C.write_report(NAME, lines)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "once"
    if cmd == "once":
        once()
    elif cmd == "report":
        report()
    else:
        print("usage: flow_logger.py once|report")


if __name__ == "__main__":
    main()
