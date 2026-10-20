"""Submit-boundary helpers for browser tests against the synthetic ATS pages
(test/yahbah/fixtures/synthetic/; each page documents its behaviour in a header comment).

    ctx = await filled_form(session, {"First name": "Jane", "Email": "jane@example.test"})
    claim = Claim()
    result = await submit(session, GENERIC, ctx, claim=claim)
    assert await page_count(session, "__clicks") == 1 and claim.calls == 1
"""
import asyncio
import inspect
import json

from yahbah.agent.fill import FillContext, fill_form
from yahbah.agent.submit import submit_and_verify
from yahbah.agent.tools import get_form_schema, run_js

APPLICANT_EMAIL = "jane.doe+job-test@example.test"


class Claim:
    """The contract's submission claim (§6): `claim() -> bool`, counted."""

    def __init__(self, granted: bool = True) -> None:
        self.granted = granted
        self.calls = 0

    async def __call__(self) -> bool:
        self.calls += 1
        return self.granted


async def submit(session, board, ctx: FillContext, *, claim: Claim, already_attempted: bool = False,
                 map_new_fields=None, email: str = APPLICANT_EMAIL):
    """submit_and_verify with the contract's `claim` callback (§6).

    Until task 4 replaces `mark_attempted` with `claim`, the claim is passed as mark_attempted (its
    result ignored), so today's tests fail on today's behaviour rather than on the signature.
    Delete the legacy branch when task 4 lands.
    """
    params = inspect.signature(submit_and_verify).parameters
    kwargs = {"email": email, "already_attempted": already_attempted, "map_new_fields": map_new_fields}
    if "claim" in params:
        kwargs["claim"] = claim
    else:  # legacy (pre-task-4)
        async def mark_attempted() -> None:
            await claim()
        kwargs["mark_attempted"] = mark_attempted
    return await submit_and_verify(session, board, ctx, **{k: v for k, v in kwargs.items() if k in params})


def context_for(schema: list[dict], values: dict[str, str]) -> FillContext:
    """A FillContext for the page's fields, with `values` (by label) as code-resolved mappings."""
    by_label = {f["label"]: f for f in schema}
    unknown = set(values) - set(by_label)
    if unknown:
        raise AssertionError(f"no field labelled {sorted(unknown)} on the page (labels: {sorted(by_label)})")
    return FillContext(
        fields={f["key"]: {"label": f["label"], "type": f["type"], "required": f["required"],
                           "options": f.get("options") or [], "empty_value": f.get("empty_value")} for f in schema},
        mappings={by_label[label]["key"]: {"key": by_label[label]["key"], "label": label,
                                           "field_type": by_label[label]["type"], "source": "custom",
                                           "value": value, "confidence": 1.0}
                  for label, value in values.items()},
        files={},
    )


async def filled_form(session, values: dict[str, str], board=None) -> FillContext:
    """Read the page's form, fill `values` (by label) with the real fill_form, assert it's clean."""
    from yahbah.agent.boards import GENERIC

    ctx = context_for(await get_form_schema(session), values)
    problems = await fill_form(session, board or GENERIC, ctx)
    assert problems == [], f"the form didn't fill cleanly: {problems}"
    return ctx


_PAGE_STATE_JS = """JSON.stringify({
  confirmed: !!document.querySelector('#confirmation'),
  errors: [...document.querySelectorAll('[role=alert]')].filter((e) => !e.hidden && e.textContent.trim())
            .map((e) => e.textContent.trim())})"""


def page_judge(session, when_form_still_there: str = "needs_fix"):
    """A plausible post-submit judge for ScriptedLLM: it reads the page itself. A confirmation means
    `submitted`, visible validation errors mean `needs_fix` with them, and a form that is still
    there gets `when_form_still_there` (`needs_fix` is the in-flight misjudgement of audit probe P3)."""
    from harness.llm import verdict

    async def judge(call):
        state = json.loads(await run_js(session, _PAGE_STATE_JS))
        if state["confirmed"]:
            return verdict("submitted", reasoning="A confirmation message is shown.")
        if state["errors"]:
            return verdict("needs_fix", state["errors"], reasoning="Validation errors are shown.")
        if when_form_still_there == "submitted":
            return verdict("submitted", reasoning="Nothing indicates a problem.")
        return verdict("needs_fix", ["The form is still showing; the application did not go through."],
                       reasoning="The application form is still visible.")
    return judge


class CappedAsyncio:
    """`asyncio` for one module, with every sleep capped: for pages whose state after a click is final
    at once, a fixed wait only costs time. Never use it where the test depends on how long code waits."""

    def __init__(self, max_delay: float) -> None:
        self.max_delay = max_delay

    def __getattr__(self, name):
        return getattr(asyncio, name)

    async def sleep(self, delay, result=None):
        return await asyncio.sleep(min(delay, self.max_delay), result)


def cap_submit_waits(monkeypatch, max_delay: float = 0.5) -> None:
    import yahbah.agent.submit as submit_module

    monkeypatch.setattr(submit_module, "asyncio", CappedAsyncio(max_delay))


async def page_count(session, name: str) -> int:
    """A window counter the synthetic pages keep (e.g. __clicks); 0 if unset."""
    return int(await run_js(session, f"window[{json.dumps(name)}] || 0") or 0)


async def page_list(session, name: str) -> list:
    return json.loads(await run_js(session, f"JSON.stringify(window[{json.dumps(name)}] || [])"))
