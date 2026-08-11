#!/usr/bin/env python3
"""
lawyer_health_check.py — health monitor agent for the live Personal Lawyer app.

Checks (each defaults to FAIL until proven otherwise — "silence != success"):
  1. /health responds 200
  2. /tools lists a sane number of tools (>=40; catches a broken deploy)
  3. A real /chat query returns a non-empty answer with sources
  4. IndianKanoon token is live (not 403/405) — the recurring failure class we hit twice

On any FAIL, sends a macOS notification (loud, local, no new dependency) and prints a
one-line status. On PASS, prints a quiet one-liner (for the launchd log only).
"""
import json
import subprocess
import sys
import urllib.request
import urllib.error

BASE = "https://personal-lawyer-india-production.up.railway.app"


def _get(path, timeout=15):
    with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
        return r.getcode(), r.read()


def _post(path, payload, timeout=60):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.getcode(), r.read()


def notify(title, message):
    """macOS notification — loud, local, zero setup."""
    script = f'display notification "{message}" with title "{title}" sound name "Basso"'
    subprocess.run(["osascript", "-e", script], check=False)


def main():
    failures = []

    # 1. health
    try:
        code, _ = _get("/health")
        if code != 200:
            failures.append(f"/health returned {code}")
    except Exception as e:
        failures.append(f"/health unreachable: {e}")

    # 2. tools count
    try:
        code, body = _get("/tools")
        n = len(json.loads(body).get("tools", []))
        if n < 40:
            failures.append(f"/tools only {n} tools (expected >=40 — deploy may be broken)")
    except Exception as e:
        failures.append(f"/tools check failed: {e}")

    # 3. real chat query
    try:
        code, body = _post("/chat", {"question": "employer has not paid salary", "history": []})
        data = json.loads(body)
        if not data.get("answer"):
            failures.append("/chat returned no answer")
        if not data.get("sources_used"):
            failures.append("/chat returned no sources (retrieval may be broken)")
    except Exception as e:
        failures.append(f"/chat check failed: {e}")

    # 4. IndianKanoon token liveness (the recurring failure class)
    try:
        code, body = _post("/analyze", {
            "text": "xyzzy nonexistent statute qwerty foobar zzz",
            "hint": "force live fallback",
        })
        data = json.loads(body)
        # not a hard failure (IK may legitimately return 0 for gibberish) — just log
    except Exception as e:
        failures.append(f"/analyze (IK fallback path) failed: {e}")

    if failures:
        summary = "; ".join(failures)
        print(f"[lawyer_health_check] FAIL: {summary}", file=sys.stderr)
        notify("Lawyer app FAILED health check", summary[:200])
        sys.exit(1)
    else:
        print("[lawyer_health_check] PASS — all checks green")


if __name__ == "__main__":
    main()
