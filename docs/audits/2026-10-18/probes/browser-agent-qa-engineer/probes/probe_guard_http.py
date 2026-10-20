import asyncio, json, sys
sys.path.insert(0, "/private/tmp/claude-501/-Users-alex-Documents-YahBah/507601a9-f685-4b67-b6bc-a4c95c794a1d/scratchpad/browser-agent-qa-engineer/probes")
import yahbah.agent  # noqa
from browser_use import BrowserProfile, BrowserSession
from browser_use.browser.events import SendKeysEvent
from yahbah.agent.fill import GUARD_SUBMIT_JS
from yahbah.agent.tools import run_js, focus_key
from probe_guard import click

URL = "http://127.0.0.1:8765/guard.html"

async def main():
    s = BrowserSession(browser_profile=BrowserProfile(headless=True, keep_alive=True))
    await s.start()
    try:
        await s.navigate_to(URL); await asyncio.sleep(0.5)
        await s.get_browser_state_summary(include_screenshot=False)
        print("indexed elements:", len(await s.get_selector_map()))
        for guard in (False, True):
            print(f"--- guard {'ON' if guard else 'OFF'}")
            for key in ["c1", "c2", "c3", "c4", "c5", "c6", "enter-in-text"]:
                await s.navigate_to(URL); await asyncio.sleep(0.4)
                if guard:
                    await run_js(s, f"{GUARD_SUBMIT_JS}(true)")
                if key == "enter-in-text":
                    await focus_key(s, "t1"); await s.event_bus.dispatch(SendKeysEvent(keys="Enter")); await asyncio.sleep(0.4); how = "Enter key"
                else:
                    how = await click(s, key)
                sent = json.loads(await run_js(s, "JSON.stringify(window.__sent || [])"))
                print(f"  {key:14} {how:26} -> {'blocked' if not sent else 'FIRED: ' + '; '.join(sent)}")
    finally:
        await s.kill()
asyncio.run(main())
