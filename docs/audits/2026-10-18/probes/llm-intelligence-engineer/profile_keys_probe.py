import asyncio, sys
exec(open("/private/tmp/claude-501/-Users-alex-Documents-YahBah/507601a9-f685-4b67-b6bc-a4c95c794a1d/scratchpad/llm-intelligence-engineer/live_probe.py").read().split("async def main()")[0])
FIELDS = [
    ("1", "Highest level of education completed", "select", ["High school", "Bachelor's", "Master's", "PhD"]),
    ("2", "Current employer", "text", []),
    ("3", "Current job title", "text", []),
    ("4", "Years of experience with Python", "number", []),
    ("5", "University", "text", []),
    ("6", "List your top technical skills", "text", []),
]
async def go():
    m = FieldMapper()
    orig = m._custom_answer
    async def wrapped(f, *a):
        try: return "[prose] " + await orig(f, *a)
        except NeedsAnswer: return "<NEEDS_ANSWER>"
    m._custom_answer = wrapped
    out = await m.map([FormField(key=k, label=l, field_type=t, required=True, options=o) for k, l, t, o in FIELDS], PROFILE, JD)
    for x in out: print(f"  [{x.key}] {x.label!r:45} source={x.source:22} conf={x.confidence} value={x.value[:80]!r}")
asyncio.run(go())
