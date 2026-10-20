"""Offline probes of fill/submit behaviour against local pages. No LLM, no network beyond 127.0.0.1."""
import asyncio, json, threading, functools, http.server, sys
from pathlib import Path
import yahbah.agent  # noqa: F401  (sets browser-use env first)
from browser_use import BrowserProfile, BrowserSession
from yahbah.agent import submit as S
from yahbah.agent.boards import GENERIC
from yahbah.agent.fill import FillContext, fill_text, read_values
from yahbah.agent.tools import get_form_schema, run_js

PAGES = Path(__file__).parent / "pages"
handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(PAGES))
class Q(handler.func):
    def log_message(self, *a): pass
srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Q, directory=str(PAGES)))
port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
URL = f"http://127.0.0.1:{port}/"

async def load(session, name):
    await session.navigate_to(URL + name)
    await asyncio.sleep(1.0)

async def main():
    session = BrowserSession(browser_profile=BrowserProfile(headless=True, keep_alive=True))
    await session.start()
    try:
        # P1: schema coverage on widget zoo
        await load(session, "widgets.html")
        fields = await get_form_schema(session)
        print("P1 schema fields:")
        for f in fields: print("   ", f["label"], "|", f["type"], "| req", f["required"])
        print("   (page has: date, native select, role=radiogroup, role=checkbox, aria-haspopup button, contenteditable, shadow-DOM input, iframe input)")

        # P2: which button is chosen as the "submit" CTA on wizard pages
        for name in ("wizard1.html", "wizard2.html", "mock_ats.html"):
            await load(session, name)
            await get_form_schema(session)
            print("P2", name, "-> FIND_SUBMIT_JS ->", repr(await run_js(session, S.FIND_SUBMIT_JS)))

        # P3: needs_fix verdict on an in-flight (slow) successful submit -> second click?
        await load(session, "mock_ats.html")
        schema = await get_form_schema(session)
        ctx = FillContext(fields={f["key"]: {"label": f["label"], "type": f["type"], "required": f["required"], "options": []} for f in schema},
                          mappings={}, files={})
        for f in schema:
            ctx.written[f["key"]] = await fill_text(session, f["key"], "x@example.com" if f["type"] == "email" else "Test")
        verdicts = iter([S.PostSubmitState(reasoning="form still present after submit", state="needs_fix", problems=["submit did not go through"]),
                         S.PostSubmitState(reasoning="thank you page", state="submitted")])
        async def fake_generate_structured(self, *a, **k): return next(verdicts)
        S.OllamaClient.generate_structured = fake_generate_structured
        async def noop_fix(*a, **k): return None
        S.fix_with_agent = noop_fix
        marks = []
        async def mark(): marks.append("marked")
        S.settings.submit_max_attempts = 4
        res = await S.submit_and_verify(session, GENERIC, ctx, "a@b.c", False, mark)
        clicks = await run_js(session, "window.__clicks")
        print("P3 marks:", marks, "result url:", res.confirmation_url, "submit clicks on page:", clicks)
    finally:
        await session.kill()

# P4: pure-python: does the code treat 'submitted' + form still fully present + button visible as contradiction?
sig = S.Signals(url="https://x/apply", form_fields_present=13, submit_button_visible=True, errors=[], code_inputs=0)
print("P4 contradicts(submitted, form still present, no errors):", S.contradicts(S.PostSubmitState(reasoning="", state="submitted"), sig))
asyncio.run(main())
