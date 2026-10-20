"""Offline: does the one-shot discover prompt input (llm_representation) contain a below-the-fold CTA? No models."""
import asyncio, sys
import yahbah.agent  # noqa: F401
from browser_use import BrowserProfile, BrowserSession
sys.path.insert(0, __file__.rsplit("/", 1)[0])
from probe_discover import serve
from yahbah.agent.discover import FIND_APPLY_JS
from yahbah.agent.tools import run_js

async def main():
    port = serve()
    s = BrowserSession(browser_profile=BrowserProfile(headless=True, keep_alive=True))
    await s.start()
    try:
        await s.navigate_to(f"http://127.0.0.1:{port}/long_jd.html")
        await asyncio.sleep(1)
        print("FIND_APPLY_JS ->", await run_js(s, FIND_APPLY_JS))
        st = await s.get_browser_state_summary(include_screenshot=False)
        rep = st.dom_state.llm_representation()
        print("page height px:", await run_js(s, "document.body.scrollHeight"), "viewport:", await run_js(s, "innerHeight"))
        print("one-shot input contains CTA:", "Apply for this role" in rep, "| indexed elements:", len(st.dom_state.selector_map))
        print("indexed:", [n.tag_name + ":" + (n.attributes or {}).get("href", "") for n in st.dom_state.selector_map.values()])
    finally:
        await s.kill()
asyncio.run(main())
