"""Offline probe of yahbah.agent.discover.discover() against synthetic local pages.

Model tiers are stubbed (no Ollama calls); load_prompts_config is stubbed (personal config never loaded).
For each page we report: which tier discover() stopped at, the final URL, the fields our top-frame
reader sees, and how many form controls browser-use's own DOM snapshot (selector_map) sees.
"""
import asyncio
import functools
import http.server
import sys
import threading
import time
from pathlib import Path

import yahbah.agent  # noqa: F401  (telemetry off before browser_use import)
from browser_use import BrowserProfile, BrowserSession

import yahbah.agent.discover as d
from yahbah.agent.tools import get_form_schema
from yahbah.llm.client import LLMError

SITE = Path(__file__).parent / "site"
reached: list[str] = []


class StubClient:
    def __init__(self, role):
        pass

    async def generate_structured(self, **kw):
        reached.append("oneshot")
        raise LLMError("stubbed: no model in this probe")


class StubHistory:
    structured_output = None


async def stub_run_agent(*a, **kw):
    reached.append("agent")
    return StubHistory()


d.OllamaClient = StubClient
d.run_agent = stub_run_agent
d.load_prompts_config = lambda: {"prompts": {"oneshot_discover": "x", "agent_discover_task": "x"}}
d.make_crew = lambda *a, **k: None


def serve() -> int:
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(SITE))
    handler.log_message = lambda *a: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv.server_address[1]


async def controls_seen_by_browser_use(session: BrowserSession) -> int:
    state = await session.get_browser_state_summary(include_screenshot=False)
    return sum(1 for n in state.dom_state.selector_map.values() if n.tag_name in ("input", "select", "textarea"))


async def main(pages: list[str]) -> None:
    port = serve()
    (SITE / "iframe_xo.html").write_text(
        "<!doctype html><html><head><title>Job</title></head><body><main><h1>Job</h1><p>Text.</p>"
        f'<iframe src="http://localhost:{port}/form.html" width=800 height=600></iframe></main></body></html>')
    session = BrowserSession(browser_profile=BrowserProfile(headless=True, keep_alive=True))
    await session.start()
    try:
        for page in pages:
            reached.clear()
            await session.navigate_to(f"http://127.0.0.1:{port}/{page}")
            await asyncio.sleep(3)  # as SessionRegistry.open
            bu = await controls_seen_by_browser_use(session)
            t = time.time()
            trace = d.Trace()
            try:
                res = await d.discover(session, trace)
                outcome = f"FORM-READY at {trace.rows[-1]['method'] if trace.rows else 'code'} tier"
            except d.StageFailed as exc:
                outcome = f"StageFailed({exc.reason})"
            except Exception as exc:  # noqa: BLE001
                outcome = f"RAISED {type(exc).__name__}: {str(exc)[:120]}"
            url = await session.get_current_page_url()
            fields = await get_form_schema(session)
            tabs = len(await session.get_tabs())
            print(f"{page:20s} -> {outcome:28s} models={reached or '-'} url=..{url[-22:]:22s} "
                  f"our_fields={[f['type'] for f in fields]} bu_controls_at_start={bu} tabs={tabs} "
                  f"({time.time() - t:.1f}s)")
            for r in trace.rows:
                print(f"      trace: {r['stage']}/{r['method']}: {r['note'][:100]}")
            # back to a single tab for the next case
            for tab in (await session.get_tabs())[1:]:
                from browser_use.browser.events import CloseTabEvent
                await session.event_bus.dispatch(CloseTabEvent(target_id=tab.target_id))
    finally:
        await session.kill()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
