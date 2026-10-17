"""StallTracker (structural stall signs) and Escalator (hand-off to the escalation model and back)."""
from types import SimpleNamespace

from yahbah.agent.llm import Crew, Escalator
from yahbah.agent.tools import StallTracker


class _Action:
    def __init__(self, **kw) -> None:
        self.kw = kw

    def model_dump(self, exclude_none: bool = True) -> dict:
        return self.kw


def step(*actions: dict) -> SimpleNamespace:
    return SimpleNamespace(action=[_Action(**a) for a in actions])


def page(dom: str, url: str = "https://example.com/apply") -> SimpleNamespace:
    return SimpleNamespace(url=url, dom_state=SimpleNamespace(llm_representation=lambda: dom))


CLICK_9 = {"click": {"index": 9}}
READ = {"read_form_schema": {}}


def test_exact_repeats_stall() -> None:
    t = StallTracker()
    for i in range(3):
        t.on_step(page(f"p{i}"), step(CLICK_9), i)
    assert t.repeats == 2
    assert t.stalled


def test_same_target_with_varying_action_lists_stalls() -> None:
    """The real loop from the spike: [click 9, click 9] -> [click 9, read_form_schema] -> ...
    Exact-repeat matching misses it; counting the same action on the same element catches it."""
    t = StallTracker()
    for i, actions in enumerate([(CLICK_9, CLICK_9), (CLICK_9, READ), (CLICK_9,)]):
        t.on_step(page(f"p{i}"), step(*actions), i)
    assert t.repeats == 0
    assert t.target_repeats == 3
    assert t.stalled


def test_unchanged_page_stalls() -> None:
    t = StallTracker()
    for i in range(3):
        t.on_step(page("same"), step({"scroll": {"down": True, "pages": i}}), i)
    assert t.unchanged == 2
    assert t.stalled


def test_progress_is_not_a_stall() -> None:
    t = StallTracker()
    for i in range(6):
        t.on_step(page(f"p{i}"), step({"click": {"index": i}}), i)
    assert not t.stalled
    assert not t.ever_stalled


def test_reset_judges_afresh_but_remembers_the_stall() -> None:
    t = StallTracker()
    for i in range(3):
        t.on_step(page("same"), step(CLICK_9), i)
    assert t.stalled
    t.reset()
    assert not t.stalled
    assert t.ever_stalled  # keeps inspect_page_js unlocked


DRIVER = SimpleNamespace(model="qwen3-coder-next")
VISION = SimpleNamespace(model="qwen3.8:27b")
ESCALATION = SimpleNamespace(model="qwen3.8:27b (thinking)")


def _crew_and_agent():
    crew = Crew(driver=DRIVER, vision=VISION, escalation=ESCALATION, tools=None, stalls=StallTracker())
    agent = SimpleNamespace(
        llm=DRIVER,
        settings=SimpleNamespace(use_vision=False),
        state=SimpleNamespace(consecutive_failures=0),
    )
    esc = Escalator(crew)
    esc.agent = agent
    return crew, agent, esc


def test_escalates_on_stall_then_hands_back() -> None:
    crew, agent, esc = _crew_and_agent()
    for i in range(3):
        esc.on_step(page(f"p{i}"), step(CLICK_9), i + 1)
    assert agent.llm is ESCALATION and agent.settings.use_vision
    assert esc.escalations == 1

    esc.on_step(page("p3"), step({"click": {"index": 3}}), 4)
    assert agent.llm is ESCALATION  # second escalated step still to come
    esc.on_step(page("p4"), step({"click": {"index": 4}}), 5)
    assert agent.llm is DRIVER and not agent.settings.use_vision
    assert not crew.stalls.stalled  # tracker reset when handing back


def test_escalates_on_consecutive_failures() -> None:
    _, agent, esc = _crew_and_agent()
    agent.state.consecutive_failures = 2
    esc.on_step(page("p0"), step({"click": {"index": 1}}), 1)
    assert agent.llm is ESCALATION
