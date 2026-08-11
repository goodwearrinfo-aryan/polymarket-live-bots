#!/usr/bin/env python3
"""
lawyer_adversarial_watch.py — regression-watch agent for the live Personal Lawyer app.

Runs the full adversarial test suite (lawyer_adversarial_test.py) against the live
production app on a schedule, and alerts on regression rather than raw failure count —
so a known-flaky test doesn't spam, but a genuine drop (e.g. a code change that
reopens the prompt-injection hole) is loud immediately.

State (last score) persisted to /tmp so a restart doesn't lose the baseline.
"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

STATE_PATH = "/tmp/lawyer_adversarial_watch_state.json"
RESULTS_PATH = "/tmp/lawyer_adversarial_results.json"


def _load_state():
    try:
        return json.load(open(STATE_PATH))
    except (FileNotFoundError, json.JSONDecodeError):
        return {"last_pass_count": None, "last_failed_tests": []}


def _save_state(state):
    json.dump(state, open(STATE_PATH, "w"))


def notify(title, message):
    script = f'display notification "{message}" with title "{title}" sound name "Basso"'
    subprocess.run(["osascript", "-e", script], check=False)


def main():
    import lawyer_adversarial_test as t

    # run() would sys.exit(1) on any FAIL — call main() logic directly and swallow that,
    # since we want to compare against the PREVIOUS run, not just alert on any single FAIL.
    try:
        t.main()
    except SystemExit:
        pass

    results = json.load(open(RESULTS_PATH))
    passed = sum(1 for r in results if r["verdict"] == "PASS")
    failed_tests = sorted(r["test"] for r in results if r["verdict"] == "FAIL")
    total = len(results)

    state = _load_state()
    prev_count = state.get("last_pass_count")
    prev_failed = set(state.get("last_failed_tests", []))

    newly_failed = sorted(set(failed_tests) - prev_failed)
    newly_fixed = sorted(prev_failed - set(failed_tests))

    print(f"[lawyer_adversarial_watch] {passed}/{total} PASS "
          f"(prev: {prev_count if prev_count is not None else 'n/a'}/{total})")

    if newly_failed:
        msg = f"{len(newly_failed)} test(s) newly failing: {', '.join(newly_failed)[:150]}"
        print(f"[lawyer_adversarial_watch] REGRESSION: {msg}", file=sys.stderr)
        notify("Lawyer app: adversarial regression", msg[:200])
    elif failed_tests:
        # still failing, but not NEW — don't spam, just log
        print(f"[lawyer_adversarial_watch] {len(failed_tests)} test(s) still failing "
              f"(no change): {', '.join(failed_tests)}")
    else:
        print("[lawyer_adversarial_watch] all clear")

    if newly_fixed:
        print(f"[lawyer_adversarial_watch] {len(newly_fixed)} test(s) newly fixed: "
              f"{', '.join(newly_fixed)}")

    _save_state({"last_pass_count": passed, "last_failed_tests": failed_tests})


if __name__ == "__main__":
    main()
