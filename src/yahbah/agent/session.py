"""
SessionRegistry — worker-level singleton holding one browser-use BrowserSession per run.

Keyed by run_id so the browser activities of one run (discover → map form → fill → submit)
share the same page without re-navigating. Each session launches its own headless Chrome
with its own temporary profile, so concurrent runs never share cookies or state.
A session only survives across activities within the same worker process; activities
re-open the job URL when it's gone (e.g. after a worker restart).
"""
import asyncio
import shutil
from pathlib import Path

from browser_use import BrowserProfile, BrowserSession
from loguru import logger

from yahbah.config import settings


def artifact_path(run_id: str, filename: str) -> str:
    """Absolute path for an artifact file of a run, creating the run's dir if needed."""
    dir_ = Path(settings.artifacts_dir) / run_id
    dir_.mkdir(parents=True, exist_ok=True)
    return str((dir_ / filename).resolve())


class SessionRegistry:
    _instance: "SessionRegistry | None" = None

    def __init__(self) -> None:
        self._sessions: dict[str, BrowserSession] = {}

    @classmethod
    def instance(cls) -> "SessionRegistry":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def get(self, run_id: str) -> BrowserSession | None:
        return self._sessions.get(run_id)

    async def open(self, run_id: str, url: str) -> BrowserSession:
        """Start a fresh browser for the run and load the URL (replacing any session it had)."""
        if run_id in self._sessions:
            logger.warning(f"Session already open for run {run_id} — closing old one")
            await self.close(run_id)
        session = BrowserSession(browser_profile=BrowserProfile(
            headless=settings.browser_headless,
            keep_alive=True,
            # No user_data_dir: browser-use creates a fresh temp profile per session.
        ))
        await session.start()
        self._sessions[run_id] = session
        await session.navigate_to(url)
        await asyncio.sleep(3)  # let client-side apps render; discover re-checks the page anyway
        return session

    async def screenshot(self, run_id: str, label: str, full_page: bool = True) -> str | None:
        """Save artifacts/<run_id>/screenshot_<label>.png; None if the run has no session."""
        session = self._sessions.get(run_id)
        if session is None:
            return None
        path = artifact_path(run_id, f"screenshot_{label}.png")
        try:
            await session.take_screenshot(path=path, full_page=full_page)
        except Exception as exc:
            logger.warning(f"[session] screenshot {label!r} failed for run {run_id}: {exc}")
            return None
        return path

    async def close(self, run_id: str) -> None:
        session = self._sessions.pop(run_id, None)
        if session is None:
            return
        profile_dir = session.browser_profile.user_data_dir
        try:
            await session.kill()
        except Exception as exc:
            logger.warning(f"[session] kill failed for run {run_id}: {exc}")
        if profile_dir and "browser-use-user-data-dir-" in str(profile_dir):
            shutil.rmtree(profile_dir, ignore_errors=True)

    async def close_all(self) -> None:
        for run_id in list(self._sessions):
            await self.close(run_id)
