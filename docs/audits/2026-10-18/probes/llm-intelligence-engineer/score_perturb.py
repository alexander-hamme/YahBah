import asyncio, sys, time
sys.argv = ["x", "none"]
exec(open("/private/tmp/claude-501/-Users-alex-Documents-YahBah/507601a9-f685-4b67-b6bc-a4c95c794a1d/scratchpad/llm-intelligence-engineer/live_probe.py").read().split("async def main()")[0])
async def go():
    mapper = FieldMapper()
    summary = (f"Name: {p['full_name']}\nLocation: {p['location']}\nYears of experience: {p['years_of_experience']}\n"
               f"Skills: {', '.join(p['skills'])}\nBio: {p['bio']}\nWork experience:\n"
               + "\n".join(f"  - {e['title']} at {e['company']} ({e['duration']})" for e in p["work_experience"]))
    noise = "Skip to content. Cookie settings: we use cookies to improve your experience. Accept all. Reject all. Sign in. Careers home. Search jobs. "
    for label, jd in (("JD[:4000] (alert-fetch cap)", JD[:4000]), ("nav/cookie text + full JD", noise + JD), ("full JD, whitespace-reflowed", JD.replace(". ", ".\n"))):
        t = time.time(); s, _ = await mapper.compute_match_score(summary, jd)
        print(f"{label}: {s}  ({time.time()-t:.0f}s)", flush=True)
asyncio.run(go())
