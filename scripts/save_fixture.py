"""
Save a live page as an offline regression fixture:

  uv run python scripts/save_fixture.py <url> <name>

Scripts and external stylesheets are dropped (the fixture must load offline), so the page's visibility
is baked in: elements hidden on the live page get data-ja-hidden, hidden again by one CSS rule.
Then add a case for <name> to test/yahbah/fixtures/cases.json with the field list you've verified by hand.
"""
import asyncio
import re
import sys
from pathlib import Path

import yahbah.agent  # noqa: F401  (sets browser-use env vars before it is imported)
from browser_use import BrowserProfile, BrowserSession

from yahbah.agent.tools import run_js

MARK_HIDDEN_JS = r"""
(() => {
  let n = 0;
  for (const el of document.body.querySelectorAll('*')) {
    if (!(el.offsetWidth || el.offsetHeight || el.getClientRects().length)) { el.setAttribute('data-ja-hidden', ''); n++; }
  }
  return n;
})()
"""

FIXTURES = Path(__file__).parents[1] / "test" / "yahbah" / "fixtures"


async def main(url: str, name: str) -> None:
    s = BrowserSession(browser_profile=BrowserProfile(headless=True))
    await s.start()
    try:
        await s.navigate_to(url)
        await asyncio.sleep(3)
        print("hidden elements marked:", await run_js(s, MARK_HIDDEN_JS))
        html = await run_js(s, "'<!doctype html>' + document.documentElement.outerHTML")
    finally:
        await s.kill()
    html = re.sub(r"<script\b[\s\S]*?</script>", "", html, flags=re.I)
    html = re.sub(r"<link\b[^>]*>", "", html, flags=re.I)
    html = html.replace("</head>", "<style>[data-ja-hidden]{display:none!important}</style></head>", 1)
    out = FIXTURES / f"{name}.html"
    out.write_text(html)
    print(f"saved {out} ({len(html)} bytes) — now add a hand-verified case to {FIXTURES / 'cases.json'}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2]))
