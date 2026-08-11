#!/usr/bin/env python3
"""lesson_resurface.py — spaced-repetition of the paid-for Lessons (PKM practice).

Each week, surface 2 rotating Lessons (full first paragraph + link) into
Reports/Lesson Resurfacer.md so they're re-read, not re-paid-for. Deterministic
rotation by ISO week → every lesson comes back ~every N/2 weeks. Read-only over
the vault Lessons/ folder. Stdlib only.
"""
import os, glob, time, re
V = os.path.expanduser("~/Documents/PolymarketVault")
LES = sorted(p for p in glob.glob(os.path.join(V,"Lessons","*.md"))
             if "index" not in os.path.basename(p).lower())
def first_para(p):
    t = open(p,encoding="utf-8",errors="ignore").read()
    t = re.sub(r"^---.*?---\s*","",t,flags=re.S)            # strip frontmatter
    body = [l.strip() for l in t.splitlines() if l.strip() and not l.startswith("#")]
    return (body[0] if body else "")[:280]
def main():
    if not LES: return
    n = len(LES); wk = int(time.strftime("%G%V"))           # year+ISO-week
    picks = [LES[(wk*2) % n], LES[(wk*2+1) % n]]
    out = ["---","type: logger","updated: "+time.strftime("%Y-%m-%d"),"---","",
           "# Lesson Resurfacer — this week's re-read","",
           f"> Spaced-repetition of the {n} paid-for lessons (rotates weekly). Re-read these two; "
           "the whole point is to never re-pay for them. Full set: [[Lessons/Lessons index]].",""]
    for p in picks:
        name = os.path.splitext(os.path.basename(p))[0]
        out += [f"## [[{name}]]", first_para(p), ""]
    out += ["## Related","[[Weekly Review]] · [[Lessons/Lessons index]] · [[Edge Map]]"]
    open(os.path.join(V,"Reports","Lesson Resurfacer.md"),"w").write("\n".join(out)+"\n")
    print(f"resurfaced 2/{n} lessons: "+", ".join(os.path.basename(p) for p in picks))
if __name__ == "__main__":
    main()
