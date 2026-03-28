"""
BrowserRegistry — worker-level singleton that manages open Playwright pages.

Keyed by run_id so that sequential browser activities (extract → fill)
share the same page/session without re-navigating.

Lifecycle:
  worker.py calls BrowserRegistry.start() on worker boot.
  worker.py calls BrowserRegistry.stop() on worker shutdown.
  browser_extract_activity calls open_page(run_id, url).
  browser_fill_and_submit_activity calls get_page(run_id) then close_page(run_id).
"""
import os
from pathlib import Path

from loguru import logger
from playwright.async_api import (
    async_playwright,
    Browser,
    BrowserContext,
    Page,
    Playwright,
)

from yahbah.config import settings


class BrowserRegistry:
    _instance: "BrowserRegistry | None" = None

    def __init__(self) -> None:
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        # run_id → (context, page)
        self._pages: dict[str, tuple[BrowserContext, Page]] = {}

    @classmethod
    def instance(cls) -> "BrowserRegistry":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def start(self) -> None:
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=True)
        logger.info("Playwright browser started")

    async def stop(self) -> None:
        for run_id in list(self._pages):
            await self.close_page(run_id)
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        logger.info("Playwright browser stopped")

    def artifact_path(self, run_id: str, filename: str) -> str:
        """Returns absolute path for an artifact file, creating dir if needed."""
        dir_ = Path(settings.artifacts_dir) / run_id
        dir_.mkdir(parents=True, exist_ok=True)
        return str(dir_ / filename)

    async def open_page(self, run_id: str, url: str) -> Page:
        if run_id in self._pages:
            logger.warning(f"Page already open for run {run_id} — closing old one")
            await self.close_page(run_id)

        assert self._browser is not None, "BrowserRegistry.start() was not called"

        trace_path = self.artifact_path(run_id, "trace.zip")
        context = await self._browser.new_context(
            viewport={"width": 1280, "height": 900},
            record_video_dir=None,
        )
        await context.tracing.start(screenshots=True, snapshots=True, sources=True)

        page = await context.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)

        self._pages[run_id] = (context, page)
        logger.info(f"Opened page for run {run_id}: {url}")
        return page

    def get_page(self, run_id: str) -> Page | None:
        entry = self._pages.get(run_id)
        return entry[1] if entry else None

    async def screenshot(self, run_id: str, label: str) -> str:
        path = self.artifact_path(run_id, f"screenshot_{label}.png")
        page = self.get_page(run_id)
        if page:
            await page.screenshot(path=path, full_page=True)
            logger.debug("Screenshot saved: %s", path)
        return path

    async def close_page(self, run_id: str) -> None:
        entry = self._pages.pop(run_id, None)
        if entry is None:
            return
        context, _ = entry
        trace_path = self.artifact_path(run_id, "trace.zip")
        try:
            await context.tracing.stop(path=trace_path)
        except Exception:
            pass
        await context.close()
        logger.info(f"Closed page and saved trace for run {run_id}")
