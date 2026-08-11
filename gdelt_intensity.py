#!/usr/bin/env python3
"""
gdelt_intensity.py — one-call GDELT news-intensity covariate (free, no key).
Ported from the gdelt-doc-api pattern (alex9smith) in stdlib-only lab idiom.

intensity(keyword) -> float | None
    Mean daily article volume (% of all GDELT-monitored coverage) for the
    keyword over the last 3 days, from the DOC 2.0 API `timelinevol` mode.
    Higher = the topic is LOUD in world news right now.

Cached 30 min per keyword in scalp_lab_cache.json-compatible dict supplied by
caller (in-memory caches die between once-runs), else module-level fallback.
Read-only research; paper lab support.
"""
import json, time, urllib.parse, urllib.request

API = "https://api.gdeltproject.org/api/v2/doc/doc"
UA  = {"User-Agent": "Mozilla/5.0"}
TTL = 1800
_cache = {}   # keyword -> {"ts": float, "val": float|None}
_last_call = [0.0]   # GDELT hard-limits to 1 request / 5 seconds


def intensity(keyword, cache=None):
    """Mean of the last ~3 days of timelinevol for `keyword`. None on failure."""
    store = cache if cache is not None else _cache
    k = f"gdelt::{keyword.lower()}"
    hit = store.get(k)
    if hit and time.time() - hit["ts"] < TTL:
        return hit["val"]
    val = None
    try:
        wait = 10.0 - (time.time() - _last_call[0])   # empirically 5s is too tight
        if wait > 0:
            time.sleep(wait)
        _last_call[0] = time.time()
        q = urllib.parse.quote(f'"{keyword}"')
        url = (f"{API}?query={q}&mode=timelinevol&timespan=3d&format=json")
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
        pts = (data.get("timeline") or [{}])[0].get("data") or []
        if pts:
            val = round(sum(float(p.get("value", 0)) for p in pts) / len(pts), 4)
    except Exception:
        val = None
    store[k] = {"ts": time.time(), "val": val}
    return val


if __name__ == "__main__":
    for kw in ("ceasefire", "invasion", "bitcoin", "esports"):
        print(f"{kw:12s} intensity={intensity(kw)}")
