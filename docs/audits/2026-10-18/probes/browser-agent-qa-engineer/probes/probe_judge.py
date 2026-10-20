"""Real submit.judge() on the (public) Greenhouse fixture after a submit click that changed nothing.
No click is made; the form is untouched. Counts verdicts and which model roles were called."""
import asyncio, json, sys, time
import yahbah.agent  # noqa
from browser_use import BrowserProfile, BrowserSession
from yahbah.agent import submit as sub
from yahbah.agent.boards import detect_board
from yahbah.agent.tools import get_form_schema, run_js
from yahbah.llm import client as llm_client

calls = []
_orig = llm_client.OllamaClient.generate_structured
async def spy(self, *a, **k):
    calls.append(self._role); return await _orig(self, *a, **k)
llm_client.OllamaClient.generate_structured = spy

URL = "http://127.0.0.1:8765/greenhouse_konux.html"
INJECT = """(() => { const p = document.createElement('p');
  p.textContent = 'Thank you! Your application has been received and our team will review it shortly.';
  (document.querySelector('main, [role=main], article') || document.body).prepend(p); return true; })()"""

async def main(n: int):
    s = BrowserSession(browser_profile=BrowserProfile(headless=True, keep_alive=True))
    await s.start()
    try:
        for variant in ("baseline", "misleading_text"):
            for i in range(n):
                await s.navigate_to(URL); await asyncio.sleep(0.5)
                await get_form_schema(s)                       # stamps field keys, as map_form does
                board = await detect_board(lambda c: run_js(s, c), URL)
                await run_js(s, board.submit_button_js or sub.FIND_SUBMIT_JS)  # tags submit-cta (no click)
                if variant == "misleading_text":
                    await run_js(s, INJECT)
                sig = await sub.read_signals(s, board)
                calls.clear(); t = time.time()
                try:
                    v = await sub.judge(s, board, sig, URL)
                    out = f"{v.state:10} problems={len(v.problems)} reason={v.reasoning[:110]!r}"
                except Exception as e:
                    out = f"EXC {type(e).__name__}: {str(e)[:120]}"
                print(f"{variant:16} #{i} fields={sig.form_fields_present} btn={sig.submit_button_visible} "
                      f"errors={len(sig.errors)} roles={calls} {time.time()-t:5.1f}s -> {out}", flush=True)
    finally:
        await s.kill()
asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 4))
