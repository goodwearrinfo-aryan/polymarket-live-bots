"""Baseline runner — a plain direct Ollama call, no framework, ~no deps. THIS is the
bar any framework must beat. Generic across tasks (reads task.py). stdlib only.
Usage: python3 baseline.py [model]   (default llama3.1:8b)"""
import sys, time, json, urllib.request
from task import CASES, PROMPT, score

MODEL = sys.argv[1] if len(sys.argv) > 1 else "llama3.1:8b"

def ollama(prompt):
    body = json.dumps({"model": MODEL, "messages": [{"role": "user", "content": prompt}],
                       "stream": False, "options": {"temperature": 0.2}}).encode()
    req = urllib.request.Request("http://localhost:11434/api/chat", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)["message"]["content"].strip()

print(f"BASELINE plain-ollama [{MODEL}] (0 deps)")
ok = 0; tot = 0.0
for c in CASES:
    t0 = time.time(); out = ollama(PROMPT.format(facts=c["facts"])); dt = time.time() - t0
    v, hit = score(out, c["expect"]); ok += hit; tot += dt
    print(f"  {c['id']}: {v} (expect {c['expect']}) {'OK' if hit else 'X'}  {dt:.1f}s")
print(f"SCORE baseline {ok}/{len(CASES)}  ({tot:.1f}s)")
