"""Per-framework runner: pydantic-ai (lightweight, minimal scaffolding) on Ollama.
The fair counter-test to CrewAI — does a THIN framework also degrade the weak model,
or was the damage CrewAI's heavy role/goal/backstory scaffolding specifically?
Prints `SCORE pydantic-ai H/N`. Ollama via its OpenAI-compatible endpoint."""
import time
from task import CASES, PROMPT, score

# pydantic-ai's API has drifted across versions — import defensively.
from pydantic_ai import Agent
try:
    from pydantic_ai.models.openai import OpenAIModel
    from pydantic_ai.providers.openai import OpenAIProvider
    model = OpenAIModel("llama3.1:8b",
                        provider=OpenAIProvider(base_url="http://localhost:11434/v1", api_key="ollama"))
except Exception:
    from pydantic_ai.models.openai import OpenAIModel
    model = OpenAIModel("llama3.1:8b", base_url="http://localhost:11434/v1", api_key="ollama")

agent = Agent(model, system_prompt="You vet Polymarket wallets for copy-trading. Be decisive.")

def run(c):
    r = agent.run_sync(PROMPT.format(facts=c["facts"]))
    return str(getattr(r, "output", None) or getattr(r, "data", "")).strip()

print("FRAMEWORK pydantic-ai on ollama llama3.1:8b")
ok = 0; tot = 0.0
for c in CASES:
    t0 = time.time(); out = run(c); dt = time.time() - t0
    v, hit = score(out, c["expect"]); ok += hit; tot += dt
    print(f"  {c['id']}: {v} (expect {c['expect']}) {'OK' if hit else 'X'}  {dt:.1f}s")
print(f"SCORE pydantic-ai {ok}/{len(CASES)}  ({tot:.1f}s)")
