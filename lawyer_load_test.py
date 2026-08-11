#!/usr/bin/env python3
"""
lawyer_load_test.py — concurrency/load stress test for the live Personal Lawyer app.

Distinct from lawyer_adversarial_test.py (correctness/security under adversarial
INPUT) — this tests RELIABILITY under concurrent LOAD: does the app hold up when
multiple users hit it at once, does latency degrade gracefully, does anything
error out or hang under concurrency.

Modes:
  --light N   N concurrent /health requests (cheap, tests raw connection handling)
  --heavy N   N concurrent /chat requests (expensive, tests real LLM-call concurrency)
  --mixed     a realistic mix: several /health + a few /chat + one /courtroom at once
"""
import json
import sys
import time
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE = "https://personal-lawyer-india-production.up.railway.app"

CHAT_QUESTIONS = [
    "What is the punishment for cheque bounce?",
    "How do I file an RTI application?",
    "What are my rights if my employer doesn't pay salary?",
    "Builder is delaying possession, what can I do?",
    "How much gratuity am I owed after 8 years?",
    "What is the limitation period for a consumer complaint?",
    "Can I get anticipatory bail for a cheating case?",
    "What is Section 138 of the NIA?",
]


def _timed_request(path, payload=None, timeout=90):
    t0 = time.time()
    try:
        if payload is not None:
            req = urllib.request.Request(
                BASE + path, data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"}, method="POST")
        else:
            req = urllib.request.Request(BASE + path)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
            code = r.getcode()
        elapsed = time.time() - t0
        if payload is None:
            ok = code == 200
        else:
            parsed = json.loads(body)
            # different endpoints use different "did it actually answer" keys
            ok = code == 200 and bool(
                parsed.get("answer") or parsed.get("tools") or parsed.get("status")
                or parsed.get("verdict") or parsed.get("analysis") or parsed.get("result")
            )
        return {"path": path, "ok": ok, "code": code, "elapsed": round(elapsed, 2), "error": None}
    except Exception as e:
        elapsed = time.time() - t0
        return {"path": path, "ok": False, "code": None, "elapsed": round(elapsed, 2), "error": f"{type(e).__name__}: {e}"}


def run_concurrent(jobs, label):
    print(f"\n=== {label}: {len(jobs)} concurrent requests ===")
    t0 = time.time()
    results = []
    with ThreadPoolExecutor(max_workers=len(jobs)) as ex:
        futures = [ex.submit(_timed_request, path, payload) for path, payload in jobs]
        for f in as_completed(futures):
            results.append(f.result())
    wall_clock = time.time() - t0

    oks = sum(1 for r in results if r["ok"])
    fails = [r for r in results if not r["ok"]]
    times = [r["elapsed"] for r in results]

    print(f"  wall-clock total: {wall_clock:.2f}s")
    print(f"  {oks}/{len(results)} succeeded")
    if times:
        print(f"  latency: min={min(times):.2f}s max={max(times):.2f}s avg={sum(times)/len(times):.2f}s")
    if fails:
        print(f"  FAILURES:")
        for f in fails:
            print(f"    {f['path']}: code={f['code']} error={f['error']}")
    return {"label": label, "wall_clock": round(wall_clock, 2), "ok": oks, "total": len(results),
             "fails": fails, "latencies": times}


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "--mixed"

    all_results = []

    if mode == "--light":
        n = int(sys.argv[2]) if len(sys.argv) > 2 else 20
        jobs = [("/health", None) for _ in range(n)]
        all_results.append(run_concurrent(jobs, f"light: {n}x /health"))

    elif mode == "--heavy":
        n = int(sys.argv[2]) if len(sys.argv) > 2 else 5
        jobs = [("/chat", {"question": CHAT_QUESTIONS[i % len(CHAT_QUESTIONS)], "history": []})
                for i in range(n)]
        all_results.append(run_concurrent(jobs, f"heavy: {n}x /chat"))

    else:  # --mixed
        jobs = [("/health", None) for _ in range(10)]
        jobs += [("/chat", {"question": q, "history": []}) for q in CHAT_QUESTIONS[:4]]
        jobs += [("/tools", None)]
        jobs += [("/courtroom", {"case": "Landlord refuses to return security deposit"})]
        all_results.append(run_concurrent(jobs, "mixed realistic load"))

    print("\n" + "=" * 70)
    total_ok = sum(r["ok"] for r in all_results)
    total_n = sum(r["total"] for r in all_results)
    print(f"OVERALL: {total_ok}/{total_n} succeeded across {len(all_results)} round(s)")

    with open("/tmp/lawyer_load_test_results.json", "w") as f:
        json.dump(all_results, f, indent=2, default=str)

    if total_ok < total_n:
        sys.exit(1)


if __name__ == "__main__":
    main()
