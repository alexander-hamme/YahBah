"""The headed fallback's trigger and lifecycle rules (contract §2 "Headed fallback"), adversarially.

test_headed_fallback.py (nav) covers the main path: a wall headless, the page headed; headed also walled;
no headed browser; the board route first; the profile deleted on close. Here: what must NOT fire it
(job_closed, browser_error, captcha, login_wall), a headed page showing some other dead end, "headed: yes"
on every later failure, a failed board route followed by the headed attempt, profile deletion on a
failed launch and on replacement, and a superseded caller during the swap.

As in test_headed_fallback.py, the "headed" browser is injected (SessionRegistry._start): both are
headless Chrome, and HOST resolves to the main fixture server in one and to a second server in the other.
"""
import asyncio
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from browser_use import BrowserSession
from temporalio.exceptions import ApplicationError

from harness.browser import offline_profile
from harness.server import FixtureServer
from yahbah.agent import boards, session as session_mod
from yahbah.agent.discover import StageFailed, Trace, discover
from yahbah.agent.session import HEADED_PROFILE_PREFIX, SessionRegistry, SessionSuperseded

FIXTURES = Path(__file__).parents[1] / "fixtures" / "discover"
REAL_SLEEP = asyncio.sleep  # fake_sessions patches asyncio.sleep itself (session_mod.asyncio is the module)
HOST = "careers.acme.test"
WALL = (FIXTURES / "akamai_access_denied.html").read_text()
FORM = ('<!doctype html><title>Engineer | Acme</title><main><h1>Engineer</h1><form>'
        '<label for="a">First name</label><input id="a"><label for="b">Last name</label><input id="b">'
        '<label for="c">Email</label><input id="c" type="email"></form></main>')
NO_FORM = '<!doctype html><title>Engineer | Acme</title><main><h1>Engineer</h1><p>Great job.</p></main>'


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


def mapped(port: int):
    return offline_profile(args=[f"--host-resolver-rules=MAP {HOST} 127.0.0.1:{port} , MAP * ~NOTFOUND , "
                                 "EXCLUDE localhost , EXCLUDE 127.0.0.1"])


class Sites:
    """The same path served by two servers: what the headless browser sees, and what the headed one does."""

    def __init__(self, pages, tmp_path: Path) -> None:
        self.pages, self.dir = pages, tmp_path / "headed"
        self.dir.mkdir()
        self.headed_server = FixtureServer(root=self.dir).start()
        self.headed_server.mount(pages.prefix, self.dir)

    def serve(self, headless: tuple[str, int], headed: tuple[str, int] = (FORM, 200), name: str = "job.html",
              query: str = "") -> str:
        self.pages.write(name, headless[0], status=headless[1])
        (self.dir / name).write_text(headed[0])
        if headed[1] != 200:
            self.headed_server.respond(f"{self.pages.prefix}/{name}", headed[1])
        return f"http://{HOST}/{self.pages.prefix}/{name}{query}"


@pytest.fixture
def sites(pages, tmp_path):
    s = Sites(pages, tmp_path)
    yield s
    s.headed_server.stop()


@pytest.fixture
def registry(monkeypatch):
    reg = SessionRegistry()
    monkeypatch.setattr(SessionRegistry, "_instance", reg)
    yield reg


@pytest.fixture
def launches(monkeypatch, fixture_server, sites):
    """Every launch's `headed`, in order."""
    log: list[bool] = []

    async def start(headed: bool) -> BrowserSession:
        log.append(headed)
        session = BrowserSession(browser_profile=mapped(sites.headed_server.port if headed else fixture_server.port))
        await session.start()
        return session
    monkeypatch.setattr(SessionRegistry, "_start", staticmethod(start))
    return log


async def run(registry, url: str, **kw) -> tuple[StageFailed | None, object]:
    session = await registry.open("run-1", url)
    try:
        return None, await discover(session, Trace(), headed_fallback=True, **kw)
    except StageFailed as exc:
        return exc, None


# ── what must never fire it ───────────────────────────────────────────────────

@pytest.mark.browser
@pytest.mark.parametrize("page, status, reason", [
    ("greenhouse_job_not_found.html", 404, "job_closed"),   # rule 4: the job is gone, a window won't help
    ("service_unavailable.html", 503, "browser_error"),     # rule 8: retryable, not a wall
])
async def test_the_fallback_never_fires_for_an_early_dead_end_that_is_not_a_wall(
        registry, sites, launches, llm, agent_llm, page, status, reason) -> None:
    try:
        failed, _ = await run(registry, sites.serve((fixture(page), status)))
        assert failed is not None and failed.reason == reason, failed
        assert launches == [False] and "headed" not in failed.detail
    finally:
        await registry.close_all()


@pytest.mark.browser
@pytest.mark.parametrize("page, reason", [("captcha_gate.html", "captcha"), ("login_wall.html", "login_wall")])
async def test_the_fallback_never_fires_for_a_captcha_or_a_login_wall(
        registry, sites, launches, llm, agent_llm, page, reason) -> None:
    """Decided after the tiers (rules 6-7): no window solves a captcha for us, none logs in."""
    llm.on("driver", "NavDecision", handler=lambda call: {"reasoning": "unclear", "state": "unknown"})
    agent_llm.done(data={"state": "other", "explanation": "No form."})
    try:
        failed, _ = await run(registry, sites.serve((fixture(page), 200)))
        assert failed is not None and failed.reason == reason, failed
        assert launches == [False] and "headed" not in failed.detail
    finally:
        await registry.close_all()


# ── the headed page re-judged, and the headed run's later failures ─────────────

@pytest.mark.browser
@pytest.mark.parametrize("headed, reason", [
    ((fixture("greenhouse_job_not_found.html"), 404), "job_closed"),   # headed sees the job is gone
    ((fixture("service_unavailable.html"), 503), "browser_error"),
])
async def test_a_headed_page_showing_another_dead_end_ends_with_that_reason_marked_headed(
        registry, sites, launches, llm, agent_llm, headed, reason) -> None:
    try:
        failed, _ = await run(registry, sites.serve((WALL, 403), headed))
        assert failed is not None and failed.reason == reason, failed
        assert "; headed: yes" in failed.detail
        assert launches == [False, True]
    finally:
        await registry.close_all()


@pytest.mark.browser
async def test_every_later_failure_of_a_headed_run_says_it_was_headed(
        registry, sites, launches, llm, agent_llm) -> None:
    """The headed page loads (no wall) but has no form, and the models find none: form_not_found, headed."""
    llm.on("driver", "NavDecision", handler=lambda call: {"reasoning": "no apply control", "state": "unknown"})
    agent_llm.done(data={"state": "other", "explanation": "No form."})
    try:
        failed, _ = await run(registry, sites.serve((WALL, 403), (NO_FORM, 200)))
        assert failed is not None and failed.reason == "form_not_found", failed
        assert "; headed: yes" in failed.detail
        assert registry.get("run-1").browser_profile is not None and launches == [False, True]
    finally:
        await registry.close_all()


@pytest.mark.browser
async def test_a_failed_board_route_comes_first_then_the_one_headed_attempt(
        registry, sites, launches, llm, agent_llm, monkeypatch) -> None:
    """Order: route (here: its page isn't the job's form), then headed (here: walled too), then blocked;
    the detail keeps both."""
    sites.pages.write("not_the_form.html", NO_FORM)
    monkeypatch.setattr(boards, "GREENHOUSE_FORM",
                        f"http://{HOST}/{sites.pages.prefix}/not_the_form.html?for={{token}}&token={{job_id}}")
    url = sites.serve((WALL, 403), (WALL, 403), query="?gh_jid=2000003")
    try:
        failed, _ = await run(registry, url)
        assert failed is not None and failed.reason == "blocked", failed
        assert "board route: unverified" in failed.detail and "; headed: blocked" in failed.detail
        assert failed.detail.index("board route") < failed.detail.index("headed")
        assert launches == [False, True]
    finally:
        await registry.close_all()


# ── the swap under a superseded caller ────────────────────────────────────────

@pytest.mark.browser
async def test_a_superseded_caller_during_the_swap_is_not_reported_as_no_headed_browser(
        registry, sites, launches, llm, agent_llm, monkeypatch) -> None:
    """discover lets SessionSuperseded through: it is no "headed: unavailable" verdict on someone else's run."""
    async def taken(session, url):
        raise SessionSuperseded("run run-1's browser belongs to a newer attempt")
    monkeypatch.setattr(registry, "reopen_headed", taken)
    session = await registry.open("run-1", sites.serve((WALL, 403)))
    try:
        with pytest.raises(SessionSuperseded):
            await discover(session, Trace(), headed_fallback=True)
    finally:
        await registry.close_all()


async def test_a_superseded_caller_during_the_swap_ends_the_activity_superseded(
        registry, activity_env, monkeypatch) -> None:
    """A newer attempt took the run while this one was about to go headed: the activity must say
    superseded (non-retryable, writes nothing), not fail as a retryable browser_error."""
    from yahbah.schemas import DiscoverInput
    from yahbah.workflows.activities import browser as activities

    async def open_(run_id, url, *, headed=False):
        return SimpleNamespace(browser_profile=SimpleNamespace(headless=True, user_data_dir=None))

    async def discover_(session, trace, *, headed_fallback=False):
        raise SessionSuperseded("run run-1's browser belongs to a newer attempt")
    monkeypatch.setattr(registry, "open", open_)
    monkeypatch.setattr(activities, "discover", discover_)

    async def call():
        return await activities.discover_activity(DiscoverInput(run_id="00000000-0000-0000-0000-000000000001",
                                                                job_url=f"http://{HOST}/job", headed_fallback=True))
    with pytest.raises(ApplicationError) as err:
        await activity_env(workflow_id="application-x").call(call)
    assert err.value.type == "superseded" and err.value.non_retryable


# ── the temporary profile: deleted whatever happens ───────────────────────────

class FakeSession:
    fail_headed = False

    def __init__(self, browser_profile=None, **_kw) -> None:
        self.browser_profile = browser_profile
        self.killed = False

    async def start(self) -> None:
        if FakeSession.fail_headed and self.browser_profile.headless is False:
            raise RuntimeError("no display")

    async def navigate_to(self, url: str) -> None:
        self.url = url

    async def kill(self) -> None:
        self.killed = True


@pytest.fixture
def fake_sessions(monkeypatch):
    FakeSession.fail_headed = False
    monkeypatch.setattr(session_mod, "BrowserSession", FakeSession)

    async def no_sleep(_s):
        pass
    monkeypatch.setattr(session_mod.asyncio, "sleep", no_sleep)
    made: list[Path] = []
    real = tempfile.mkdtemp

    def mkdtemp(*a, **kw):
        made.append(Path(real(*a, **kw)))
        return str(made[-1])
    monkeypatch.setattr(session_mod.tempfile, "mkdtemp", mkdtemp)
    yield made
    for d in made:  # never leave a profile behind, even when a test fails
        if d.exists():
            import shutil
            shutil.rmtree(d, ignore_errors=True)


async def test_a_headed_launch_that_fails_deletes_its_temporary_profile(registry, fake_sessions) -> None:
    FakeSession.fail_headed = True
    headless = await registry.open("run-1", "https://example.com/job")
    with pytest.raises(RuntimeError):
        await registry.reopen_headed(headless, "https://example.com/job")
    assert fake_sessions and all(d.name.startswith(HEADED_PROFILE_PREFIX) for d in fake_sessions)
    assert not any(d.exists() for d in fake_sessions)
    with pytest.raises(RuntimeError):
        await registry.open("run-2", "https://example.com/job", headed=True)
    assert not any(d.exists() for d in fake_sessions)
    await registry.close_all()


async def test_replacing_a_headed_session_deletes_the_old_profile(registry, fake_sessions) -> None:
    """open() over a run's headed browser (a retry within the workflow) starts clean."""
    first = await registry.open("run-1", "https://example.com/job", headed=True)
    first_dir = Path(first.browser_profile.user_data_dir)
    second = await registry.open("run-1", "https://example.com/job", headed=True)
    assert first.killed and not first_dir.exists()
    assert Path(second.browser_profile.user_data_dir) != first_dir
    headless = await registry.open("run-1", "https://example.com/job")
    assert headless.browser_profile.headless is True and not Path(second.browser_profile.user_data_dir).exists()
    await registry.close_all()


async def test_a_headless_session_never_gets_a_yahbah_profile_and_close_deletes_nothing_else(
        registry, fake_sessions, tmp_path) -> None:
    """_discard deletes only our temporary profiles: a user_data_dir that isn't one is left alone."""
    session = await registry.open("run-1", "https://example.com/job")
    assert fake_sessions == []  # headless: browser-use's own temp profile, no mkdtemp of ours
    keep = tmp_path / "Chrome Profile"
    keep.mkdir()
    session.browser_profile = SimpleNamespace(headless=True, user_data_dir=str(keep))
    await registry.close("run-1")
    assert keep.exists()


# ── the start-before-kill swap under §7 ownership, closes and cancellation ────
# reopen_headed checks ownership, then awaits the headed launch (seconds), then replaces whatever the run
# holds. Gated fakes pause the launch (or the old browser's kill) so another attempt can act in between.

class GatedSession(FakeSession):
    all: list["GatedSession"] = []
    start_gate: asyncio.Event | None = None
    kill_gate: asyncio.Event | None = None

    def __init__(self, browser_profile=None, **kw) -> None:
        super().__init__(browser_profile, **kw)
        GatedSession.all.append(self)

    async def start(self) -> None:
        if self.browser_profile.headless is False and GatedSession.start_gate is not None:
            await GatedSession.start_gate.wait()

    async def kill(self) -> None:
        if self.browser_profile.headless is not False and GatedSession.kill_gate is not None:
            await GatedSession.kill_gate.wait()
        self.killed = True


@pytest.fixture
def gated(monkeypatch, fake_sessions):
    GatedSession.all, GatedSession.start_gate, GatedSession.kill_gate = [], None, None
    monkeypatch.setattr(session_mod, "BrowserSession", GatedSession)
    owner = {"now": ("application-run-1", 5, 1)}
    monkeypatch.setattr(session_mod, "activity_owner", lambda: owner["now"])
    return SimpleNamespace(owner=owner, profiles=fake_sessions)


def headed_sessions() -> list[GatedSession]:
    return [s for s in GatedSession.all if s.browser_profile.headless is False]


async def until(cond, timeout: float = 2.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not cond():
        assert asyncio.get_running_loop().time() < deadline, "timed out"
        await REAL_SLEEP(0.01)


async def test_a_newer_attempt_taking_the_run_during_the_headed_launch_keeps_its_browser(registry, gated) -> None:
    older = await registry.open("run-1", "https://example.com/job")
    GatedSession.start_gate = asyncio.Event()
    swap = asyncio.create_task(registry.reopen_headed(older, "https://example.com/job"))
    await until(lambda: headed_sessions())                     # the headed launch is under way
    gated.owner["now"] = ("application-run-1", 5, 2)           # the attempt timed out; its retry opens the run
    newer = await registry.open("run-1", "https://example.com/job")
    gated.owner["now"] = ("application-run-1", 5, 1)
    GatedSession.start_gate.set()
    with pytest.raises(SessionSuperseded):
        await swap
    gated.owner["now"] = ("application-run-1", 5, 2)
    assert registry.get("run-1") is newer and not newer.killed   # the retry's browser survives
    assert all(s.killed for s in headed_sessions())              # the older attempt's headed one doesn't leak
    assert not any(d.exists() for d in gated.profiles)
    await registry.close_all()


async def test_a_run_closed_during_the_headed_launch_leaks_no_browser_or_profile(registry, gated) -> None:
    headless = await registry.open("run-1", "https://example.com/job")
    GatedSession.start_gate = asyncio.Event()
    swap = asyncio.create_task(registry.reopen_headed(headless, "https://example.com/job"))
    await until(lambda: headed_sessions())
    await registry.close("run-1")                               # cancel/close arrives meanwhile
    GatedSession.start_gate.set()
    with pytest.raises((SessionSuperseded, KeyError)):
        await swap
    assert all(s.killed for s in headed_sessions())
    assert not any(d.exists() for d in gated.profiles)


async def test_a_cancellation_during_the_swap_leaks_no_browser_or_profile(registry, gated) -> None:
    headless = await registry.open("run-1", "https://example.com/job")
    GatedSession.kill_gate = asyncio.Event()                    # the old browser's kill hangs
    swap = asyncio.create_task(registry.reopen_headed(headless, "https://example.com/job"))
    await until(lambda: headed_sessions())
    await REAL_SLEEP(0.05)
    swap.cancel()                                               # the activity is cancelled (heartbeat timeout, user)
    with pytest.raises(asyncio.CancelledError):
        await swap
    GatedSession.kill_gate.set()
    await REAL_SLEEP(0.05)                                      # a shielded kill of the old browser finishes
    await registry.close_all()                                  # the run's ending closes whatever it holds
    assert headless.killed                                      # the old Chrome dies too, cancelled or not
    assert all(s.killed for s in headed_sessions())
    assert not any(d.exists() for d in gated.profiles)


async def test_a_cancellation_during_the_headed_launch_deletes_its_profile(registry, gated) -> None:
    headless = await registry.open("run-1", "https://example.com/job")
    GatedSession.start_gate = asyncio.Event()                   # the launch hangs
    swap = asyncio.create_task(registry.reopen_headed(headless, "https://example.com/job"))
    await until(lambda: headed_sessions())
    swap.cancel()
    with pytest.raises(asyncio.CancelledError):
        await swap
    assert registry.get("run-1") is headless and not headless.killed  # nothing changed for the run
    assert not any(d.exists() for d in gated.profiles)
    await registry.close_all()


def test_no_session_is_ever_given_a_shared_profile_or_saved_state(monkeypatch) -> None:
    """Cookies never carry over: headless gets browser-use's per-launch temporary profile (None here), headed a
    fresh mkdtemp of ours; neither gets storage_state or a cookies file."""
    profiles = []

    class Capture(FakeSession):
        def __init__(self, browser_profile=None, **kw) -> None:
            super().__init__(browser_profile, **kw)
            profiles.append(browser_profile)
    monkeypatch.setattr(session_mod, "BrowserSession", Capture)
    for headed in (False, True, True):
        asyncio.run(SessionRegistry._start(headed))
    headless, *headed = profiles
    assert headless.user_data_dir is None
    assert len({p.user_data_dir for p in headed}) == 2
    for p in profiles:
        assert p.storage_state is None
        assert not getattr(p, "cookies_file", None)
    import shutil
    for p in headed:
        shutil.rmtree(p.user_data_dir, ignore_errors=True)


# ── later stages: the run's mode, never a fallback of their own ───────────────

@pytest.mark.parametrize("headed", [False, True])
async def test_a_later_stage_reopens_in_the_runs_mode_and_never_falls_back(
        registry, activity_env, monkeypatch, headed) -> None:
    """map/fill/submit after a lost browser: open(headed=<the run's mode>), and the re-discover is called
    without headed_fallback, so a wall there ends the stage instead of swapping browsers mid-run."""
    from yahbah.workflows.activities import browser as activities
    opened, discovered = [], []

    async def open_(run_id, url, *, headed=False):
        opened.append(headed)
        return SimpleNamespace(browser_profile=SimpleNamespace(headless=not headed, user_data_dir=None))

    async def discover_(session, trace, **kw):
        discovered.append(kw)
        return None
    monkeypatch.setattr(registry, "open", open_)
    monkeypatch.setattr(activities, "discover", discover_)

    async def call():
        return await activities._session_with_form("run-1", f"http://{HOST}/job", None, Trace(), headed=headed)
    await activity_env(workflow_id="application-x").call(call)
    assert opened == [headed]
    assert discovered == [{}] or discovered == [{"headed_fallback": False}]


@pytest.mark.browser
async def test_the_fallback_never_fires_when_discover_ends_form_not_found(
        registry, sites, launches, llm, agent_llm) -> None:
    """A page that loads but has no form (rule 9) is no wall: no headed browser."""
    llm.on("driver", "NavDecision", handler=lambda call: {"reasoning": "no apply control", "state": "unknown"})
    agent_llm.done(data={"state": "other", "explanation": "No form."})
    try:
        failed, _ = await run(registry, sites.serve((NO_FORM, 200)))
        assert failed is not None and failed.reason == "form_not_found", failed
        assert launches == [False] and "headed" not in failed.detail
    finally:
        await registry.close_all()


async def test_without_an_owner_a_run_reopened_during_the_headed_launch_keeps_its_new_browser(registry, gated) -> None:
    """Outside an activity (scripts/run_pipeline.py) there is no owner, so ownership can't tell the two apart:
    the registry entry must still hold the session being replaced, or the swap stands down."""
    gated.owner["now"] = None
    first = await registry.open("run-1", "https://example.com/job")
    GatedSession.start_gate = asyncio.Event()
    swap = asyncio.create_task(registry.reopen_headed(first, "https://example.com/job"))
    await until(lambda: headed_sessions())
    second = await registry.open("run-1", "https://example.com/job")   # the run re-opened meanwhile
    GatedSession.start_gate.set()
    with pytest.raises(SessionSuperseded):
        await swap
    assert registry.get("run-1") is second and not second.killed
    assert all(s.killed for s in headed_sessions()) and not any(d.exists() for d in gated.profiles)
    await registry.close_all()
