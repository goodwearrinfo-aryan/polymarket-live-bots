#!/usr/bin/env bash
# vet.sh — vet any LLM framework against a plain-call baseline, then self-destruct.
#
#   ./vet.sh <pip-package> <runner.py> [<extra-pip-pkg>...]
#   e.g. ./vet.sh crewai runner_crewai.py
#
# What it does (the proven method, one command):
#   1. isolated uv venv in a throwaway sandbox (never the live tree)
#   2. pip-install the framework (timed + disk-sized)
#   3. run baseline.py (plain Ollama, system python, 0 deps)  -> SCORE baseline X/N
#   4. run <runner.py> inside the venv (framework, same model) -> SCORE <fw> Y/N
#   5. print the verdict (framework must MATCH OR BEAT baseline to be worth its weight)
#   6. delete the sandbox (reclaim disk — Lesson 12)
#
# Requires: uv, a local ollama serving the model in task.py. Keyless. Paper-safe.
set -uo pipefail
KIT="$(cd "$(dirname "$0")" && pwd)"
PKG="${1:?usage: vet.sh <pip-package> <runner.py> [extra-pkgs...]}"
RUNNER="${2:?usage: vet.sh <pip-package> <runner.py> [extra-pkgs...]}"
shift 2; EXTRA="$*"
SB="$(mktemp -d "/tmp/vetkit-${PKG//[^a-zA-Z0-9]/_}.XXXXXX")"
trap 'rm -rf "$SB"; echo "[cleanup] removed $SB"' EXIT

cp "$KIT/task.py" "$KIT/baseline.py" "$KIT/$RUNNER" "$SB/" 2>/dev/null || { echo "missing kit files"; exit 1; }
cd "$SB"
echo "[1/4] venv"; uv venv .venv --python 3.12 >/dev/null 2>&1
echo "[2/4] install $PKG $EXTRA"
b=$(df -k / | awk 'NR==2{print $4}'); t0=$(date +%s)
uv pip install --python .venv/bin/python "$PKG" $EXTRA >/dev/null 2>&1 || { echo "  install FAILED"; exit 1; }
t1=$(date +%s); a=$(df -k / | awk 'NR==2{print $4}')
echo "  installed in $((t1-t0))s · venv $(du -sh .venv | cut -f1) · ~$(( (b-a)/1024 ))MiB disk"
echo "[3/4] baseline (plain ollama, 0 deps)"; python3 baseline.py | sed 's/^/  /'
echo "[4/4] framework"; CREWAI_DISABLE_TELEMETRY=true OTEL_SDK_DISABLED=true .venv/bin/python "$RUNNER" 2>/dev/null | grep -E "^  |^SCORE|^FRAMEWORK" | sed 's/^/  /'
echo
echo "VERDICT: a framework earns adoption only if its SCORE >= baseline SCORE."
echo "         (CrewAI 2026-06-21: framework 1/2 vs baseline 2/2 -> rejected.)"
