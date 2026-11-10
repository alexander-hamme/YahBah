"""Headless browser-use sessions for tests.

Chrome resolves only localhost (--host-resolver-rules), so a saved page's CDN images, captcha
iframes and analytics can't leave the machine. browser-use's default extensions are off: they are
downloaded from the Chrome web store on first use (network) and change pages (ad/cookie blockers).
Chrome's automatic HTTPS upgrades are off: the fixture server speaks plain HTTP, and since Chrome 157
(2026-11-09) an http:// URL for a mapped public host such as job-boards.greenhouse.io is upgraded and,
failing that, refused with net::ERR_BLOCKED_BY_CLIENT. Live runs keep the upgrades.
"""
import asyncio
import json

from browser_use import BrowserProfile, BrowserSession

from yahbah.agent.tools import run_js

OFFLINE_ARGS = ["--host-resolver-rules=MAP * ~NOTFOUND , EXCLUDE localhost , EXCLUDE 127.0.0.1"]
NO_HTTPS_UPGRADES = ("--disable-features=HttpsUpgrades,HttpsFirstModeV2,HttpsFirstBalancedModeAutoEnable,"
                     "HttpsFirstModeV2ForTypicallySecureUsers")


def offline_profile(**overrides) -> BrowserProfile:
    """`args` replaces the resolver rules; the HTTPS-upgrade switch is always added."""
    args = [*overrides.pop("args", OFFLINE_ARGS), NO_HTTPS_UPGRADES]
    return BrowserProfile(**{"headless": True, "keep_alive": True, "enable_default_extensions": False,
                             "args": args, **overrides})


async def start_session() -> BrowserSession:
    session = BrowserSession(browser_profile=offline_profile())
    await session.start()
    return session


async def load(session: BrowserSession, url: str, timeout: float = 10.0) -> None:
    """Navigate and wait until the document has loaded (no fixed sleep)."""
    await session.navigate_to(url)
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        try:
            state = await run_js(session, "JSON.stringify([location.href, document.readyState])")
            href, ready = json.loads(state)
            if ready == "complete" and href.split("#")[0].startswith(url.split("#")[0].split("?")[0]):
                return
        except Exception:  # the old document can vanish under us while navigating
            pass
        if asyncio.get_running_loop().time() > deadline:
            raise TimeoutError(f"page did not load: {url}")
        await asyncio.sleep(0.05)


async def element_index(session: BrowserSession, **attrs: str) -> int | None:
    """browser-use's index of the element whose attributes match (from the latest browser state)."""
    selector_map = await session.get_selector_map()
    for index, node in selector_map.items():
        a = node.attributes or {}
        if all(a.get(k.replace("_", "-")) == v for k, v in attrs.items()):
            return index
    return None
