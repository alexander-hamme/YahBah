"""The headed fallback (contract §2): board route, then one headed reopen, then `blocked`.

Offline, the "headed" browser is injected (SessionRegistry._start): both browsers are headless Chrome, and
careers.acme.test resolves to a different fixture server in each, one walling the page off (Akamai's
403), the other serving it. So discover's own fallback logic, the registry's swap and the profile
lifecycle run for real; only the window is missing. The real headed window is the opt-in live test at
the end (YAHBAH_LIVE_BROWSER=1).
"""
import os
from pathlib import Path

import pytest
from browser_use import BrowserSession

from harness.browser import offline_profile
from harness.server import FixtureServer
from yahbah.agent import boards, discover as discover_mod, session as session_mod
from yahbah.agent.discover import StageFailed, Trace, discover
from yahbah.agent.session import HEADED_PROFILE_PREFIX, SessionRegistry

WALL = (Path(__file__).parents[1] / "fixtures" / "discover" / "akamai_access_denied.html").read_text()
FORM = ('<!doctype html><title>Engineer | Acme</title><main><h1>Engineer</h1><form>'
        '<label for="a">First name</label><input id="a"><label for="b">Last name</label><input id="b">'
        '<label for="c">Email</label><input id="c" type="email"></form></main>')
HOST = "careers.acme.test"


def mapped(port: int):
    return offline_profile(args=[f"--host-resolver-rules=MAP {HOST} 127.0.0.1:{port} , MAP * ~NOTFOUND , "
                                 "EXCLUDE localhost , EXCLUDE 127.0.0.1"])


class Sites:
    """The same path served by two servers: `headless` (the main fixture server) and `headed`."""

    def __init__(self, pages, tmp_path: Path) -> None:
        self.pages, self.dir = pages, tmp_path / "headed"
        self.dir.mkdir()
        self.headed_server = FixtureServer(root=self.dir).start()
        self.headed_server.mount(pages.prefix, self.dir)

    def serve(self, headless: tuple[str, int], headed: tuple[str, int]) -> str:
        self.pages.write("job.html", headless[0], status=headless[1])
        (self.dir / "job.html").write_text(headed[0])
        if headed[1] != 200:
            self.headed_server.respond(f"{self.pages.prefix}/job.html", headed[1])
        return f"http://{HOST}/{self.pages.prefix}/job.html"


@pytest.fixture
def sites(pages, tmp_path):
    s = Sites(pages, tmp_path)
    yield s
    s.headed_server.stop()


@pytest.fixture
def registry(monkeypatch):
    """A fresh registry; nothing leaks into other tests."""
    reg = SessionRegistry()
    monkeypatch.setattr(SessionRegistry, "_instance", reg)
    yield reg


@pytest.fixture
def no_models(monkeypatch):
    async def forbidden(*_a, **_k):
        raise AssertionError("a model tier ran on a page code should have settled")
    monkeypatch.setattr(discover_mod, "oneshot_navigate", forbidden)
    monkeypatch.setattr(discover_mod, "run_agent", forbidden)


@pytest.fixture
def launches(monkeypatch, fixture_server, sites):
    """SessionRegistry._start injected: the headless browser sees the wall, the "headed" one the page.
    Records each launch's `headed`; `fail_headed` makes a headed launch raise (no GUI session)."""
    log = {"launched": [], "fail_headed": False}

    async def start(headed: bool) -> BrowserSession:
        log["launched"].append(headed)
        if headed and log["fail_headed"]:
            raise RuntimeError("no display")
        session = BrowserSession(browser_profile=mapped(sites.headed_server.port if headed else fixture_server.port))
        await session.start()
        return session
    monkeypatch.setattr(SessionRegistry, "_start", staticmethod(start))
    return log


@pytest.mark.browser
async def test_a_page_walled_off_headless_continues_in_the_headed_browser(registry, sites, launches,
                                                                           no_models) -> None:
    url = sites.serve(headless=(WALL, 403), headed=(FORM, 200))
    headless = await registry.open("run-1", url)
    try:
        trace = Trace()
        result = await discover(headless, trace, headed_fallback=True)
        assert result.headed is True
        assert launches["launched"] == [False, True]
        live = registry.get("run-1")
        assert live is not headless and await live.get_current_page_url() == url  # the run keeps the headed one
        assert any(r["note"].startswith("headed fallback: reopened") for r in trace.rows)
    finally:
        await registry.close_all()


@pytest.mark.browser
@pytest.mark.parametrize("fallback", [False, True])
async def test_without_the_fallback_or_with_a_walled_headed_page_the_run_ends_blocked(
        registry, sites, launches, no_models, fallback) -> None:
    url = sites.serve(headless=(WALL, 403), headed=(WALL, 403))
    headless = await registry.open("run-1", url)
    try:
        with pytest.raises(StageFailed) as exc:
            await discover(headless, Trace(), headed_fallback=fallback)
        assert exc.value.reason == "blocked"
        assert ("; headed: blocked" in exc.value.detail) is fallback
        assert launches["launched"] == ([False, True] if fallback else [False])  # one headed attempt at most
    finally:
        await registry.close_all()


@pytest.mark.browser
async def test_no_headed_browser_ends_blocked_unavailable_and_keeps_the_headless_one(registry, sites, launches,
                                                                                    no_models) -> None:
    launches["fail_headed"] = True
    url = sites.serve(headless=(WALL, 403), headed=(FORM, 200))
    headless = await registry.open("run-1", url)
    try:
        with pytest.raises(StageFailed) as exc:
            await discover(headless, Trace(), headed_fallback=True)
        assert exc.value.reason == "blocked" and "; headed: unavailable" in exc.value.detail
        assert registry.get("run-1") is headless  # still there for the failure screenshot
    finally:
        await registry.close_all()


@pytest.mark.browser
async def test_a_board_route_comes_before_the_headed_fallback(registry, sites, launches, no_models,
                                                             monkeypatch) -> None:
    """Order: board route, then headed. A verified board form never launches a headed browser."""
    pages = sites.pages
    pages.write("board.html", FORM)
    board_url = pages.url("board.html")

    async def routes(board, url, run_js, dead_end):
        return [(boards.GREENHOUSE, boards.BoardRoute(board_url, "4242424", "acme", False))]
    monkeypatch.setattr(discover_mod, "board_routes", routes)
    monkeypatch.setattr(boards.GREENHOUSE, "route_verified", lambda landed, html, job_id: landed == board_url)
    url = sites.serve(headless=(WALL, 403), headed=(FORM, 200))
    headless = await registry.open("run-1", url)
    try:
        result = await discover(headless, Trace(), headed_fallback=True)
        assert result.headed is False and result.page_url == board_url
        assert launches["launched"] == [False]
    finally:
        await registry.close_all()


# ── The registry's headed sessions: a fresh temporary profile, deleted on close ─────────────────

class FakeSession:
    def __init__(self, browser_profile=None, **_kw) -> None:
        self.browser_profile = browser_profile
        self.killed = False

    async def start(self) -> None:
        pass

    async def navigate_to(self, url: str) -> None:
        self.url = url

    async def kill(self) -> None:
        self.killed = True


@pytest.fixture
def fake_sessions(monkeypatch):
    monkeypatch.setattr(session_mod, "BrowserSession", FakeSession)

    async def no_sleep(_s):
        pass
    monkeypatch.setattr(session_mod.asyncio, "sleep", no_sleep)


async def test_a_headed_session_gets_its_own_temporary_profile_deleted_on_close(registry, fake_sessions) -> None:
    session = await registry.open("run-1", "https://example.com/job", headed=True)
    profile_dir = Path(session.browser_profile.user_data_dir)
    assert session.browser_profile.headless is False
    assert profile_dir.name.startswith(HEADED_PROFILE_PREFIX) and profile_dir.is_dir()
    other = await registry.open("run-2", "https://example.com/job", headed=True)
    assert Path(other.browser_profile.user_data_dir) != profile_dir  # never shared between sessions or runs
    await registry.close("run-1")
    assert session.killed and not profile_dir.exists()
    await registry.close_all()
    assert not Path(other.browser_profile.user_data_dir).exists()


async def test_open_stays_headless_by_default(registry, fake_sessions) -> None:
    session = await registry.open("run-1", "https://example.com/job")
    assert session.browser_profile.headless is True  # settings.browser_headless in tests
    await registry.close_all()


async def test_reopen_headed_starts_the_new_browser_before_closing_the_old(registry, fake_sessions,
                                                                          monkeypatch) -> None:
    headless = await registry.open("run-1", "https://example.com/job")
    headed = await registry.reopen_headed(headless, "https://example.com/job")
    assert headless.killed and not headed.killed and registry.get("run-1") is headed
    assert headed.browser_profile.headless is False and headed.url == "https://example.com/job"

    async def no_display(self) -> None:
        raise RuntimeError("no display")
    monkeypatch.setattr(FakeSession, "start", no_display)
    with pytest.raises(RuntimeError):
        await registry.reopen_headed(headed, "https://example.com/job")
    assert registry.get("run-1") is headed and not headed.killed  # a failed launch changes nothing
    with pytest.raises(KeyError):
        await registry.reopen_headed(FakeSession(), "https://example.com/job")  # not a run's browser
    await registry.close_all()


# ── Opt-in live check: a real headed window (YAHBAH_LIVE_BROWSER=1) ──────────────────────────────

@pytest.mark.skipif(os.environ.get("YAHBAH_LIVE_BROWSER") != "1", reason="opt-in: YAHBAH_LIVE_BROWSER=1")
async def test_live_rubrik_without_gh_jid_reaches_a_form_in_the_headed_fallback(registry, monkeypatch) -> None:
    """Discover only, nothing filled. The board route is switched off so the headed fallback is what runs
    (with it on, this URL takes the board route to Greenhouse's form instead)."""
    async def no_routes(*_a, **_k):
        return []
    monkeypatch.setattr(discover_mod, "board_routes", no_routes)
    monkeypatch.setattr(session_mod.settings, "browser_headless", True)
    url = "https://www.rubrik.com/company/careers/departments/job.8274601"
    session = await registry.open("live-headed", url)
    try:
        trace = Trace()
        result = await discover(session, trace, headed_fallback=True)
        print("\n".join(f"[{r['stage']}] {r['method']}: {r['note']}" for r in trace.rows))
        assert result.headed is True
    finally:
        await registry.close_all()
