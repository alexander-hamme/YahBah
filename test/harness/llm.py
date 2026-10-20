"""ScriptedLLM: OllamaClient replies scripted per role and response schema, with every call recorded.

    llm.on("driver", "PostSubmitState", replies=[verdict("needs_fix", ["..."]), verdict("submitted")])
    llm.on("prose", handler=lambda call: answer("Yes", quotes=["..."]))
    llm.on(("driver", "vision"), "PostSubmitState", handler=judge)   # async handlers are awaited

Rules are tried in the order they were added; a rule with `replies` is used up after its last
reply, a rule with `handler` serves any number of calls. A reply is a model instance, a dict (fitted
to the requested schema with harness.contract.fit), a str (generate_text), or an exception instance
(raised, e.g. LLMError to simulate a failed call).

A call no rule matches raises UnexpectedModelCall at the call site and is recorded in `unexpected`;
the `llm` fixture also fails a passing test that had unexpected calls (code may swallow the error).
"""
import inspect
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from harness.contract import fit
from yahbah.llm.client import OllamaClient


class UnexpectedModelCall(AssertionError):
    pass


@dataclass
class ModelCall:
    role: str
    kind: str                       # "structured" | "text"
    system: str
    user: str
    schema: type[BaseModel] | None
    images: int

    @property
    def schema_name(self) -> str | None:
        return self.schema.__name__ if self.schema else None


@dataclass
class _Rule:
    roles: tuple[str, ...] | None
    schema: str | None
    replies: deque | None
    handler: Callable[[ModelCall], Any] | None
    calls: list[ModelCall] = field(default_factory=list)

    def matches(self, call: ModelCall) -> bool:
        if self.roles and call.role not in self.roles:
            return False
        if self.schema and call.schema_name != self.schema:
            return False
        return self.handler is not None or bool(self.replies)


class ScriptedLLM:
    def __init__(self) -> None:
        self.calls: list[ModelCall] = []
        self.unexpected: list[ModelCall] = []
        self._rules: list[_Rule] = []

    def on(self, role: str | Iterable[str] | None = None, schema: str | None = None, *,
           reply: Any = None, replies: Iterable[Any] | None = None,
           handler: Callable[[ModelCall], Any] | None = None) -> "ScriptedLLM":
        if sum(x is not None for x in (reply, replies, handler)) != 1:
            raise ValueError("give exactly one of reply=, replies=, handler=")
        roles = (role,) if isinstance(role, str) else (tuple(role) if role else None)
        queue = deque([reply] if reply is not None else replies) if handler is None else None
        self._rules.append(_Rule(roles, schema, queue, handler))
        return self

    def calls_for(self, role: str | None = None, schema: str | None = None) -> list[ModelCall]:
        return [c for c in self.calls if (role is None or c.role == role) and (schema is None or c.schema_name == schema)]

    async def _answer(self, call: ModelCall) -> Any:
        self.calls.append(call)
        rule = next((r for r in self._rules if r.matches(call)), None)
        if rule is None:
            self.unexpected.append(call)
            raise UnexpectedModelCall(
                f"no scripted reply for role={call.role!r} schema={call.schema_name!r}; "
                f"prompt starts: {call.user[:200]!r}")
        rule.calls.append(call)
        reply = rule.handler(call) if rule.handler else rule.replies.popleft()
        if inspect.isawaitable(reply):
            reply = await reply
        if isinstance(reply, BaseException):
            raise reply
        if call.schema is not None and isinstance(reply, dict):
            return fit(call.schema, reply)
        return reply

    def install(self, monkeypatch) -> "ScriptedLLM":
        llm = self

        async def generate_structured(client, system_prompt, user_prompt, response_model, images=None):
            return await llm._answer(ModelCall(client._role, "structured", system_prompt, user_prompt,
                                               response_model, len(images or [])))

        async def generate_text(client, system_prompt, user_prompt, images=None):
            return await llm._answer(ModelCall(client._role, "text", system_prompt, user_prompt, None,
                                               len(images or [])))

        monkeypatch.setattr(OllamaClient, "generate_structured", generate_structured)
        monkeypatch.setattr(OllamaClient, "generate_text", generate_text)
        return self


# ── Reply builders (dicts, fitted to whatever schema the code asks for) ──────────

def verdict(state: str, problems: Iterable[str] = (), reasoning: str = "scripted verdict",
            blocked_reason: str | None = None) -> dict:
    """A post-submit judge reply (PostSubmitState)."""
    return {"reasoning": reasoning, "state": state, "problems": list(problems), "blocked_reason": blocked_reason}


def answer(text: str, *, quotes: Iterable[str] = (), basis: Iterable[str] = (), job_specific: bool = False,
           insufficient_info: bool = False, generated: bool | None = None) -> dict:
    """A prose/custom-answer reply. Carries both today's `evidence` quotes and the contract's `basis`
    keys (§8); fit() keeps whichever the requested schema has."""
    reply = {"answer": text, "evidence": list(quotes), "basis": list(basis), "insufficient_info": insufficient_info,
             "job_specific": job_specific}
    if generated is not None:
        reply["generated"] = generated
    return reply


def mapping_keys(call: ModelCall) -> list[str]:
    """The field keys a mapping call asks about (the enum on its reply schema's `key`)."""
    item = call.schema.model_fields["mappings"].annotation.__args__[0]
    return list(item.model_fields["key"].annotation.__args__)


def map_all(source: str | Callable[[str], str] = "custom", confidence: float = 1.0):
    """Handler for the driver's field-mapping call: every asked key -> `source` (or source(key))."""
    def handler(call: ModelCall) -> dict:
        return {"mappings": [{"key": k, "source": source(k) if callable(source) else source, "value": "",
                              "confidence": confidence} for k in mapping_keys(call)]}
    return handler
