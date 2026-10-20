import asyncio, threading, functools, http.server
from pathlib import Path
import yahbah.agent  # noqa
from browser_use import BrowserProfile, BrowserSession
from yahbah.agent.fill import GUARD_SUBMIT_JS
from yahbah.agent.tools import run_js
PAGES = Path(__file__).parent / "pages"
class Q(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a): pass
srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Q, directory=str(PAGES)))
threading.Thread(target=srv.serve_forever, daemon=True).start()
async def main():
    s = BrowserSession(browser_profile=BrowserProfile(headless=True, keep_alive=True)); await s.start()
    try:
        await s.navigate_to(f"http://127.0.0.1:{srv.server_address[1]}/guard.html"); await asyncio.sleep(1)
        await run_js(s, f"{GUARD_SUBMIT_JS}(true)")
        for bid in ("b_form_submit", "b_form_untyped", "b_onclick", "b_div"):
            await run_js(s, f"document.getElementById('{bid}').click()")  # same effect as a real click on an enabled control
        print(await run_js(s, "JSON.stringify({form_submit_handler_runs: window.sent_form||0, spa_button_onclick_runs: window.sent_spa||0, div_role_button_runs: window.sent_div||0})"))
        print("disabled by guard:", await run_js(s, "JSON.stringify([...document.querySelectorAll('[data-ja-guarded]')].map(e=>e.id))"))
    finally:
        await s.kill()
asyncio.run(main())
