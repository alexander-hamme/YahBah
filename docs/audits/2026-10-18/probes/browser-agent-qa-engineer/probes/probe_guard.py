"""Probe: does GUARD_SUBMIT_JS(true) stop a REAL click (browser-use ClickElementEvent, as an agent's
generic click tool does) on submit controls of the shapes SPA ATSs use? Synthetic local page, no network."""
import asyncio, json
import yahbah.agent  # noqa: F401 (env vars before browser_use)
from browser_use import BrowserProfile, BrowserSession
from browser_use.browser.events import ClickElementEvent, SendKeysEvent
from yahbah.agent.fill import GUARD_SUBMIT_JS
from yahbah.agent.tools import node_for_key, run_js, focus_key

PAGE = """<!doctype html><html><body><main><h1>Apply</h1>
<form id="f" novalidate>
  <label for="n">Full name</label><input id="n" name="n" data-ja-field="t1" value="Test Person">
  <p><button type="submit" data-ja-field="c1">Submit application</button></p>
  <p><button data-ja-field="c2">Send application</button></p>
  <p><button type="button" data-ja-field="c3" onclick="window.__sent.push('c3 button[type=button] onclick (fetch-style)')">Submit</button></p>
  <p><div role="button" tabindex="0" data-ja-field="c4" onclick="window.__sent.push('c4 div[role=button] onclick')">Submit application</div></p>
  <p><a href="#" data-ja-field="c5" onclick="window.__sent.push('c5 <a> onclick'); return false;">Apply now</a></p>
</form>
<div class="spa-form">
  <label for="m">Email</label><input id="m" data-ja-field="t2">
  <p><button data-ja-field="c6" onclick="window.__sent.push('c6 <button> outside any <form>')">Submit</button></p>
</div>
<script>
window.__sent = [];
document.getElementById('f').addEventListener('submit', (e) => { e.preventDefault(); window.__sent.push('native form submit event'); });
</script>
</main></body></html>"""

async def load(s, html):
    await s.navigate_to("about:blank")
    cdp = await s.get_or_create_cdp_session()
    tree = await cdp.cdp_client.send.Page.getFrameTree(session_id=cdp.session_id)
    await cdp.cdp_client.send.Page.setDocumentContent(params={"frameId": tree["frameTree"]["frame"]["id"], "html": html}, session_id=cdp.session_id)
    await asyncio.sleep(0.5)

async def click(s, key):
    node = await node_for_key(s, key)
    if node is None:
        return "not indexed (disabled?)"
    ev = s.event_bus.dispatch(ClickElementEvent(node=node)); await ev
    try:
        await ev.event_result(raise_if_any=True, raise_if_none=False)
    except Exception as e:
        return f"click raised {type(e).__name__}"
    await asyncio.sleep(0.3)
    return "clicked"

async def main():
    s = BrowserSession(browser_profile=BrowserProfile(headless=True, keep_alive=True))
    await s.start()
    try:
        rows = []
        for key in ["c1", "c2", "c3", "c4", "c5", "c6", "enter-in-text"]:
            await load(s, PAGE)
            await run_js(s, f"{GUARD_SUBMIT_JS}(true)")
            if key == "enter-in-text":
                await focus_key(s, "t1"); await s.event_bus.dispatch(SendKeysEvent(keys="Enter")); await asyncio.sleep(0.3); how = "Enter key"
            else:
                how = await click(s, key)
            disabled = await run_js(s, f"!!document.querySelector('[data-ja-field={json.dumps(key)}]')?.disabled")
            sent = await run_js(s, "JSON.stringify(window.__sent)")
            rows.append((key, how, disabled, json.loads(sent)))
        for r in rows:
            verdict = "BLOCKED" if not r[3] else "GOT THROUGH"
            print(f"{r[0]:14} {r[1]:28} disabled={str(r[2]):5} -> {verdict}: {r[3]}")
    finally:
        await s.kill()

if __name__ == "__main__":
    asyncio.run(main())
