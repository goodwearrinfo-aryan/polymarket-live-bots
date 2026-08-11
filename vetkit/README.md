# vetkit — vet any LLM framework in one command

Turns "should we adopt framework X?" from an opinion into a **measured number**, the
way we vetted CrewAI on 2026-06-21. The insight: a framework only earns its weight if
it makes the **same local model** do a task **at least as well as a plain call**. So we
score framework-vs-baseline on a task with **known-correct answers**.

## Use

```bash
cd ~/Documents/polymarket/vetkit
./vet.sh <pip-package> <runner.py> [extra-pip-pkgs...]
# e.g.
./vet.sh crewai runner_crewai.py
```

It spins an isolated `uv` venv in `/tmp` (never the live tree), installs the framework
(timed + disk-sized), runs `baseline.py` (plain Ollama, 0 deps) and your `runner.py`
(the framework, same model), prints both `SCORE` lines, then **deletes the sandbox**.
Keyless; needs `uv` + a local `ollama` serving the model in `task.py`.

## Files
- `task.py` — the known-answer benchmark (currently: wallet copyability, 2 cases). Swap
  `CASES`/`PROMPT` for any judgment you care about; keep `expect` so scoring is objective.
- `baseline.py` — plain direct Ollama call. The bar to beat. Generic, stdlib only.
- `runner_crewai.py` — worked per-framework runner (the only ~20 lines you write per
  framework). Copy it to `runner_<x>.py` for the next one and print `SCORE <name> H/N`.
- `vet.sh` — the orchestrator.

## The rule
Adopt only if `framework SCORE >= baseline SCORE`. Otherwise the framework is adding
dependencies + overhead while making the model no better (or worse).

## Result log
- **CrewAI 1.14.7 (2026-06-21): REJECTED.** framework **1/2** vs baseline **2/2** on the
  wallet task — same `llama3.1:8b`. CrewAI flipped 0xbd04 (the real sharp) to DROP; its
  role/goal/backstory scaffolding confused the weak model. Cost: 635 MB venv, telemetry
  on by default. On the open-weight constraint, the wrapper hurts. See
  `memory/crewai-vet-2026-06-21.md`.
- **pydantic-ai (2026-06-21): NEUTRAL.** framework **2/2** = baseline **2/2**, 253 MB (vs
  CrewAI 635). A THIN framework (bare system prompt, no role/goal/backstory) did NOT degrade
  the weak model — it tied the plain call. Refined finding: it's not "frameworks bad," it's
  **heavy scaffolding** that hurts weak local models. Ranking on the open-weight constraint:
  minimal-scaffolding ≥ plain call > heavy-scaffolding. Neither BEATS a plain call here, so
  the plain `llm_client` still wins on simplicity for the bot; pydantic-ai is the safe pick
  IF typed/structured outputs are wanted later.
