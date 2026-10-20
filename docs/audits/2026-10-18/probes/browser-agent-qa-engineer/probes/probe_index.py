import asyncio, sys
sys.path.insert(0, "/private/tmp/claude-501/-Users-alex-Documents-YahBah/507601a9-f685-4b67-b6bc-a4c95c794a1d/scratchpad/browser-agent-qa-engineer/probes")
import yahbah.agent  # noqa
from browser_use import BrowserProfile, BrowserSession
from probe_guard import PAGE, load

async def main():
    s = BrowserSession(browser_profile=BrowserProfile(headless=True, keep_alive=True))
    await s.start()
    try:
        await load(s, PAGE)
        st = await s.get_browser_state_summary(include_screenshot=False)
        sm = await s.get_selector_map()
        print("url", st.url, "selector_map", len(sm))
        for i, n in list(sm.items())[:12]:
            print(i, n.tag_name, dict(list((n.attributes or {}).items())[:4]))
    finally:
        await s.kill()
asyncio.run(main())
