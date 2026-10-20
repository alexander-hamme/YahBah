"""Offline: for false-positive 'form-ready' pages, what map_form's code tier accepts and which button
submit.FIND_SUBMIT_JS would tag as the submit button. Read-only (FIND_SUBMIT_JS only tags + scrolls); no clicks,
no models (map_form stops at its code tier on these pages), personal config never loaded."""
import asyncio
import sys

import yahbah.agent  # noqa: F401
from browser_use import BrowserProfile, BrowserSession

import yahbah.agent.discover as d
from yahbah.agent.boards import GENERIC
from yahbah.agent.submit import FIND_SUBMIT_JS
from yahbah.agent.tools import run_js

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from probe_discover import serve  # noqa: E402

d.capture_empty_values = lambda *a, **k: asyncio.sleep(0)
d.load_prompts_config = lambda: (_ for _ in ()).throw(RuntimeError("model tier reached"))


async def main(pages):
    port = serve()
    s = BrowserSession(browser_profile=BrowserProfile(headless=True, keep_alive=True))
    await s.start()
    try:
        for p in pages:
            await s.navigate_to(f"http://127.0.0.1:{port}/{p}")
            await asyncio.sleep(1)
            fields = await d.map_form(s, GENERIC, d.Trace())
            submit = await run_js(s, FIND_SUBMIT_JS)
            print(f"{p:20s} map_form(code tier) -> {[(f['label'], f['type'], f['required']) for f in fields]}"
                  f"  FIND_SUBMIT_JS tags -> {submit!r}")
    finally:
        await s.kill()


asyncio.run(main(sys.argv[1:]))
