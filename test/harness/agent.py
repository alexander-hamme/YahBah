"""AgentScript: a scripted browser-use chat model, so the real agent loops (run_agent, fix_with_agent,
the discover/map agents) can be driven to take specific actions without Ollama.

    script = agent_llm                                   # the fixture; installs itself
    script.click(id="spa-submit")                        # step 1: click the element with id=spa-submit
    script.send_keys("Enter")                            # step 2
    script.done()                                        # step 3 (also the default once steps run out)

Each scripted step becomes one AgentOutput. Element targets are resolved against browser-use's own
selector map at the moment the agent asks, exactly like a model reading browser_state. A target
that is not indexed (e.g. disabled by a guard) can't be clicked by a real agent either: the step
becomes `done(success=False)` and the target is recorded in `missing`.

Every role (driver, vision, escalation) shares one script, so an escalation swap doesn't change
what the agent does. Calls without an output format (look_at_screen, extraction) get "scripted".
"""
import inspect
import sys
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, get_args

from browser_use.llm.views import ChatInvokeCompletion, ChatInvokeUsage

from harness.browser import element_index
from harness.contract import fit, minimal

Step = Callable[["StepContext"], Awaitable[list[dict]] | list[dict]]


@dataclass
class StepContext:
    script: "AgentScript"
    session: Any
    output_format: type | None
    messages: list


@dataclass
class AgentCall:
    role: str
    output_format: str | None
    actions: list[dict] = field(default_factory=list)


def _done_data_model(output_format) -> type | None:
    """The `data` model of the done action (the agent's output_model_schema), if it has one."""
    action_model = get_args(output_format.model_fields["action"].annotation)[0]
    for member in get_args(action_model.model_fields["root"].annotation):
        if "done" in member.model_fields:
            params = member.model_fields["done"].annotation
            data = getattr(params, "model_fields", {}).get("data")
            return data.annotation if data else None
    return None


class AgentScript:
    def __init__(self) -> None:
        self.steps: deque[Step] = deque()
        self.calls: list[AgentCall] = []
        self.missing: list[dict] = []
        self.session = None  # set by the fixture user if steps need element lookups

    # ── step builders ──
    def step(self, fn: Step) -> "AgentScript":
        self.steps.append(fn)
        return self

    def click(self, **target: str) -> "AgentScript":
        async def click(ctx: StepContext) -> list[dict]:
            index = await element_index(ctx.session, **target)
            if index is None:
                self.missing.append(target)
                return [self.done_action(ctx, success=False, text=f"element {target} is not on the page")]
            return [{"click": {"index": index}}]
        return self.step(click)

    def send_keys(self, keys: str) -> "AgentScript":
        return self.step(lambda ctx: [{"send_keys": {"keys": keys}}])

    def done(self, success: bool = True, data: dict | None = None) -> "AgentScript":
        return self.step(lambda ctx: [self.done_action(ctx, success=success, data=data)])

    @staticmethod
    def done_action(ctx: StepContext, success: bool = True, data: dict | None = None, text: str = "done") -> dict:
        model = _done_data_model(ctx.output_format) if ctx.output_format else None
        if model is None:
            return {"done": {"text": text, "success": success}}
        built = fit(model, data) if data is not None else minimal(model)
        return {"done": {"success": success, "data": built.model_dump()}}

    # ── the chat model ──
    async def respond(self, role: str, messages: list, output_format: type | None) -> Any:
        call = AgentCall(role, output_format.__name__ if output_format else None)
        self.calls.append(call)
        if output_format is None or "action" not in output_format.model_fields:
            return "scripted"
        ctx = StepContext(self, self.session, output_format, messages)
        step = self.steps.popleft() if self.steps else (lambda c: [self.done_action(c)])
        actions = step(ctx)
        if inspect.isawaitable(actions):
            actions = await actions
        call.actions = actions
        return output_format.model_validate({"evaluation_previous_goal": "scripted", "memory": "scripted",
                                             "next_goal": "scripted", "action": actions})

    def model(self, role: str) -> "ScriptedChatModel":
        return ScriptedChatModel(self, role)

    def install(self, monkeypatch) -> "AgentScript":
        """Every chat_model(role) in yahbah returns this script's model (make_crew and direct callers)."""
        import yahbah.agent.discover  # noqa: F401  (imports chat_model by name)
        import yahbah.agent.llm as agent_llm

        original = agent_llm.chat_model
        for module in list(sys.modules.values()):
            if (module and getattr(module, "__name__", "").startswith("yahbah")
                    and getattr(module, "chat_model", None) is original):
                monkeypatch.setattr(module, "chat_model", self.model)
        return self


class ScriptedChatModel:
    """browser-use BaseChatModel protocol, answered by an AgentScript."""

    _verified_api_keys = True

    def __init__(self, script: AgentScript, role: str) -> None:
        self.script, self.role = script, role
        self.model = f"scripted-{role}"

    @property
    def provider(self) -> str:
        return "scripted"

    @property
    def name(self) -> str:
        return self.model

    @property
    def model_name(self) -> str:
        return self.model

    async def ainvoke(self, messages, output_format=None, **kwargs):
        completion = await self.script.respond(self.role, messages, output_format)
        usage = ChatInvokeUsage(prompt_tokens=0, prompt_cached_tokens=None, prompt_cache_creation_tokens=None,
                                prompt_image_tokens=None, completion_tokens=0, total_tokens=0)
        return ChatInvokeCompletion(completion=completion, usage=usage)
