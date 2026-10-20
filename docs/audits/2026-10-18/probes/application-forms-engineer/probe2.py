import asyncio, threading, functools, http.server
from pathlib import Path
import yahbah.agent  # noqa
from browser_use import BrowserProfile, BrowserSession
from yahbah.agent.fill import fill_text, read_values, value_ok
from yahbah.agent.tools import get_form_schema
PAGES = Path(__file__).parent / "pages"
class Q(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a): pass
srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Q, directory=str(PAGES)))
threading.Thread(target=srv.serve_forever, daemon=True).start()
URL = f"http://127.0.0.1:{srv.server_address[1]}/typed.html"
async def main():
    s = BrowserSession(browser_profile=BrowserProfile(headless=True, keep_alive=True)); await s.start()
    try:
        await s.navigate_to(URL); await asyncio.sleep(1)
        schema = {f["label"]: f for f in await get_form_schema(s)}
        print("schema keys per field:", {k: sorted(v.keys()) for k, v in schema.items()})
        for label, val in [("Available from (date) *".rstrip(" *"), "11/01/2026"), ("Short answer", "A" * 60), ("Phone", "+1 (555) 010-9999")]:
            f = schema[label]
            written = await fill_text(s, f["key"], val)
            got = (await read_values(s, [f["key"]]))[f["key"]]
            print(f"{label!r}: wrote {written[:24]!r}; page shows {got['value'][:24]!r} (len {len(got['value'])}); value_ok={value_ok(f, written, got)}")
        f = schema["Available from (date)"]
        w = await fill_text(s, f["key"], "2026-11-01"); g = (await read_values(s, [f["key"]]))[f["key"]]
        print("ISO date:", g["value"], value_ok(f, w, g))
    finally:
        await s.kill()
asyncio.run(main())
