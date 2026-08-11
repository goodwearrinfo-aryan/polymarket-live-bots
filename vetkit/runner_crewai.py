"""Per-framework runner TEMPLATE (worked example: CrewAI on Ollama).

This is the ONLY piece you write per framework: ~20 lines of glue that runs the
same task.py task through the framework on the SAME local model, and prints a
final line `SCORE <name> <hits>/<total>`. Copy this for the next framework
(runner_langgraph.py, runner_autogen.py, ...) — vet.sh runs it in the venv and
diffs its SCORE against baseline.py.
"""
import os, time
os.environ["CREWAI_DISABLE_TELEMETRY"] = "true"
os.environ["OTEL_SDK_DISABLED"] = "true"
os.environ["OPENAI_API_KEY"] = "sk-noop"
from crewai import Agent, Task, Crew, LLM
from task import CASES, PROMPT, score

llm = LLM(model="ollama/llama3.1:8b", base_url="http://localhost:11434", temperature=0.2)

def run(c):
    agent = Agent(role="Polymarket copy-trading vetter",
                  goal="Decide KEEP or DROP for copy-trading a wallet",
                  backstory="A skeptical quant; sub-15-min coinflip markets are uncopyable.",
                  llm=llm, verbose=False, allow_delegation=False)
    t = Task(description=PROMPT.format(facts=c["facts"]),
             expected_output="One word KEEP or DROP, then one sentence.", agent=agent)
    return str(Crew(agents=[agent], tasks=[t], verbose=False).kickoff()).strip()

print("FRAMEWORK crewai on ollama llama3.1:8b")
ok = 0; tot = 0.0
for c in CASES:
    t0 = time.time(); out = run(c); dt = time.time() - t0
    v, hit = score(out, c["expect"]); ok += hit; tot += dt
    print(f"  {c['id']}: {v} (expect {c['expect']}) {'OK' if hit else 'X'}  {dt:.1f}s")
print(f"SCORE crewai {ok}/{len(CASES)}  ({tot:.1f}s)")
