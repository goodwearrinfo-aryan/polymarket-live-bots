#!/usr/bin/env python3
"""
catalyst_watch.py — 24/7 worker #2 (pm-catalyst-scout, as a launchd script).

Position-aware: for each OPEN held position, checks fresh LOCAL catalyst caches
(keyless, $0 — breaking_news_signals.json / news_velocity_signals.json) and judges
on the FREE model whether news makes the held side MORE wrong (THESIS-BREAK),
MORE right (THESIS-CONFIRM), or is already priced / irrelevant (NOISE).
iMessage ONLY on a NEW thesis-break (deduped). Read-only, paper.
Run: python3 catalyst_watch.py once
"""
import os, sys, json
import worker_lib as W

STEM = "catalyst_watch"
NEWS_FILES = ["breaking_news_signals.json", "news_velocity_signals.json",
              "news_velocity_signals.json", "newstrategy.json"]


def open_positions():
    try:
        import analyst_book
        snap = analyst_book.mark_book(persist=False)
        return [r for r in snap.get("rows", []) if r.get("state") == "open"]
    except Exception as e:
        W.log(STEM, f"book unavailable ({e})")
        return []


def recent_headlines(cap=30):
    """Pull headline-ish strings from local news caches (schema-tolerant)."""
    out = []
    seen = set()
    def walk(o):
        if len(out) >= cap:
            return
        if isinstance(o, dict):
            for k in ("news_headline", "news_lead", "headline", "title", "topic",
                      "market", "text", "summary", "q", "question", "desc"):
                v = o.get(k)
                if isinstance(v, str) and 8 < len(v) < 300 and v not in seen:
                    seen.add(v); out.append(v)
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    for f in NEWS_FILES:
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), f)
        if os.path.exists(p):
            try:
                walk(json.load(open(p)))
            except Exception:
                pass
    return out[:cap]


def main():
    opens = open_positions()
    news = recent_headlines()
    if not opens:
        W.vault_write(STEM, "# Catalyst Watch\n\nNo open positions — nothing to guard.")
        W.log(STEM, "no open positions")
        return
    if not news:
        W.vault_write(STEM, f"# Catalyst Watch\n\n{len(opens)} open positions, but no fresh "
                            "catalyst cache available this cycle.")
        W.log(STEM, f"{len(opens)} open, no news cache")
        return

    def _pl(i, r):
        p = r.get("p", {})
        return (f"- [{i}] {str(p.get('q','?'))[:110]} | held={p.get('side','?')} "
                f"true_prob={p.get('true_prob','?')} mkt_now={r.get('mkt_now','?')} | "
                f"thesis: {str(p.get('thesis',''))[:110]}")
    pos_lines = "\n".join(_pl(i, r) for i, r in enumerate(opens[:20]))
    news_lines = "\n".join(f"- {h}" for h in news)

    system = (
        "You are guarding OPEN paper prediction-market positions. For EACH position, judge ONLY "
        "whether the fresh news changes the held thesis. Default to NOISE if no headline is clearly "
        "relevant — most news is irrelevant to a given market. Be strict and concrete.")
    user = (f"OPEN POSITIONS:\n{pos_lines}\n\nFRESH HEADLINES:\n{news_lines}\n\n"
            "Return JSON {\"verdicts\":[{\"i\":<index>,\"market\":\"<short>\","
            "\"verdict\":\"THESIS-BREAK|THESIS-CONFIRM|NOISE\",\"why\":\"<=15 words\"}]} "
            "with one entry per position.")
    shape = {"verdicts": [{"i": 0, "market": "str", "verdict": "NOISE", "why": "str"}]}

    res = W.free_judge(system, user, schema_hint=shape)
    served = W.prov()
    verdicts = (res or {}).get("verdicts", []) if isinstance(res, dict) else (res or [])

    breaks = [v for v in verdicts if str(v.get("verdict", "")).upper().startswith("THESIS-BREAK")]
    # alert only on NEW breaks
    alerted = []
    for v in breaks:
        # dedup by STABLE position slug (not the model's volatile 'why' wording, which
        # changes when the primary NIM falls back to ollama and re-fires the same break).
        try:
            slug = opens[int(v.get("i"))].get("p", {}).get("slug", "")
        except Exception:
            slug = ""
        key = "break:" + (slug or str(v.get("market", ""))[:50])
        if W.is_new_alert(STEM, key):
            alerted.append(v)
    if alerted:
        msg = "⚠️ THESIS-BREAK:\n" + "\n".join(
            f"• {a.get('market','?')}: {a.get('why','')}" for a in alerted[:5])
        W.send_imessage(msg)

    # vault
    lines = ["# Catalyst Watch", f"\n_Served by: {served} | {len(opens)} open, "
             f"{len(news)} headlines, {len(breaks)} break(s), {len(alerted)} new alert(s)_\n"]
    if verdicts:
        for v in verdicts:
            lines.append(f"- **{str(v.get('verdict','?')).upper()}** — {v.get('market','?')}: {v.get('why','')}")
    else:
        lines.append("_(free LLM unavailable this cycle)_")
    W.vault_write(STEM, "\n".join(lines))
    W.log(STEM, f"{len(opens)} open | {len(breaks)} break | {len(alerted)} new-alert | served={served}")


if __name__ == "__main__":
    main()
