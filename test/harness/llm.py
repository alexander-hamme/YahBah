"""ScriptedLLM: OllamaClient replies scripted per role and response schema, with every call recorded.

    llm.on("driver", "PostSubmitState", replies=[verdict("needs_fix", ["..."]), verdict("submitted")])
    llm.on("prose", "ComposeResponse", handler=compose_all(lambda fid, row: composed(fid, "Yes", ["fact_id"])))
    llm.on(("driver", "vision"), "PostSubmitState", handler=judge)   # async handlers are awaited

Rules are tried in the order they were added; a rule with `replies` is used up after its last
reply, a rule with `handler` serves any number of calls. A reply is a model instance, a dict (fitted
to the requested schema with harness.contract.fit), a str (generate_text), or an exception instance
(raised, e.g. LLMError to simulate a failed call).

A call no rule matches raises UnexpectedModelCall at the call site and is recorded in `unexpected`;
the `llm` fixture also fails a passing test that had unexpected calls (code may swallow the error).
"""
import inspect
import json
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any, get_args

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


# ── Answer engine (yahbah.llm.answers): prompt sections and reply builders ──────

def prompt_section(call: ModelCall, header: str) -> Any:
    """The parsed JSON of one `HEADER (JSON):` section of an answer-engine prompt (KeyError if absent)."""
    for chunk in call.user.split("\n\n"):
        head, _, body = chunk.partition("\n")
        if head == f"{header} (JSON):":
            return json.loads(body)
    raise KeyError(f"no {header!r} section in the prompt")


def asked_ids(call: ModelCall) -> list[str]:
    """The field ids an answer-engine call asks about (the enum on its items' `field_id`)."""
    item = get_args(next(iter(call.schema.model_fields.values())).annotation)[0]
    return list(get_args(item.model_fields["field_id"].annotation))


def interpretation(field_id: str, fact_ids: Iterable[str] = (), *, intent: str = "scripted", document: str = "none",
                   jurisdiction: str | None = None, qualifiers: Iterable[str] = (),
                   ambiguity: str | None = None) -> dict:
    """One Pass 1 (InterpretResponse) entry."""
    return {"field_id": field_id, "intent": intent, "document": document, "jurisdiction": jurisdiction,
            "qualifiers": list(qualifiers), "fact_ids": list(fact_ids), "ambiguity": ambiguity}


def interpret_all(fact_ids: dict[str, Any] | Callable[[str], Any] = lambda fid: []):
    """Handler for Pass 1: an entry for every asked id. fact_ids (a dict, or a function of the id) gives
    the fact ids to select, or a whole interpretation(...) dict for that field."""
    def handler(call: ModelCall) -> dict:
        entries = []
        for fid in asked_ids(call):
            pick = fact_ids(fid) if callable(fact_ids) else fact_ids.get(fid, [])
            entries.append(pick if isinstance(pick, dict) else interpretation(fid, pick))
        return {"fields": entries}
    return handler


def composed(field_id: str, value: str | None, evidence: Iterable[str] = (), *, quotes: Iterable[str] = (),
             absent: str | None = None, status: str = "answered", composed: bool = False,
             requested: Iterable[str] = (), requested_info: str | None = None, reason: str = "scripted") -> dict:
    """One Pass 2 (ComposeResponse) entry. quotes: words of the cited facts the answer rests on; absent:
    what it rests on not finding (an answer citing only profile records needs one of them)."""
    return {"field_id": field_id, "status": status, "value": value, "composed": composed,
            "evidence_ids": list(evidence), "requested_fact_ids": list(requested), "requested_info": requested_info,
            "reason": reason, "quotes": list(quotes), "absent": absent}


def compose_all(fn: Callable[[str, dict], dict]):
    """Handler for Pass 2 (compose and recompose): fn(field_id, its FIELDS TO ANSWER row) -> composed(...)."""
    def handler(call: ModelCall) -> dict:
        rows = {r["id"]: r for r in prompt_section(call, "FIELDS TO ANSWER")}
        return {"answers": [fn(fid, rows[fid]) for fid in asked_ids(call)]}
    return handler


def verification(field_id: str, supported: bool = True, problem: str | None = None) -> dict:
    """One verifier (VerifyResponse) verdict."""
    return {"field_id": field_id, "supported": supported, "problem": problem}


def verify_all(fn: Callable[[str], bool | str] = lambda fid: True):
    """Handler for the verifier: fn(field_id) -> True (supported), False, or a problem string (unsupported)."""
    def handler(call: ModelCall) -> dict:
        verdicts = []
        for fid in asked_ids(call):
            result = fn(fid)
            if isinstance(result, str):
                verdicts.append(verification(fid, False, result))
            else:
                verdicts.append(verification(fid, bool(result), None if result else "scripted rejection"))
        return {"verdicts": verdicts}
    return handler


def reselect_all(fact_ids: dict[str, list[str]]):
    """Handler for the recovery reselect call: every asked id -> fact_ids.get(id, [])."""
    def handler(call: ModelCall) -> dict:
        return {"fields": [{"field_id": fid, "fact_ids": list(fact_ids.get(fid, []))} for fid in asked_ids(call)]}
    return handler
