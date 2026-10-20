"""Shape-tolerant builders and lookups, so stubs and scripted replies survive interface changes
that the contract allows (docs/wiki/contracts/run-lifecycle.md) and fail loudly on those it doesn't.

- make(cls, **kw): an instance of an activity I/O dataclass from whatever fields it has today.
  Unknown keywords are dropped, missing required fields get empty values of their type.
- fit(model, data): a pydantic model (a structured LLM reply) from a dict. Keys the model doesn't
  have are dropped; list-of-Literal values are filtered to the allowed ones; a required field the
  harness doesn't know raises HarnessDrift, which names the field to teach the harness.
- minimal(model): the smallest valid instance (an agent's `done` output).
- contract_symbol(name): a contract function from the modules the contract allows it to live in.
"""
import dataclasses
import importlib
import inspect
import types
import typing
from typing import Any, Literal, Union, get_args, get_origin

from pydantic import BaseModel

TERMINAL = {"COMPLETED", "SUBMIT_UNCERTAIN", "FAILED", "CANCELLED", "EXPIRED", "DUPLICATE"}
IN_FLIGHT = {"PENDING", "RUNNING", "AWAITING_REVIEW"}

# Where each not-yet-implemented contract function is defined (§5, §6, §10).
_CONTRACT_HOMES = {
    "possibly_applied": ["yahbah.run_state"],
    "possibly_applied_url": ["yahbah.run_state"],
    "derive_outcome": ["yahbah.run_state"],
    "allowed_actions": ["yahbah.run_state"],
    "claim_submission": ["yahbah.run_state"],
}


class HarnessDrift(AssertionError):
    """A model or dataclass gained a required field the harness can't fill in. Teach harness.contract."""


def defect(where: str, task: int, what: str):
    """Mark an invariant test that fails today because of a confirmed defect. Strict: once the
    owning task fixes it the test XPASSes, which fails the run until the owner removes the mark."""
    import pytest

    return pytest.mark.xfail(strict=True, reason=f"{where} / task {task}: {what}")


def _empty(tp: Any) -> Any:
    origin = get_origin(tp)
    if origin in (Union, types.UnionType) and type(None) in get_args(tp):
        return None
    if origin is Literal:
        return get_args(tp)[0]
    tp = origin or tp
    for kind, value in ((bool, False), (int, 0), (float, 0.0), (str, ""), (list, []), (dict, {}), (tuple, ())):
        if tp is kind:
            return value
    if inspect.isclass(tp) and issubclass(tp, BaseModel):
        return minimal(tp)
    if dataclasses.is_dataclass(tp):
        return make(tp)
    return None


def make(cls: type, **kw: Any) -> Any:
    hints = typing.get_type_hints(cls)
    values = {}
    for f in dataclasses.fields(cls):
        if f.name in kw:
            values[f.name] = kw[f.name]
        elif f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING:
            values[f.name] = _empty(hints.get(f.name, str))
    return cls(**values)


def has_field(cls: type, name: str) -> bool:
    if dataclasses.is_dataclass(cls):
        return any(f.name == name for f in dataclasses.fields(cls))
    return name in getattr(cls, "model_fields", {})


def _literal_values(tp: Any) -> tuple | None:
    if get_origin(tp) is Literal:
        return get_args(tp)
    return None


def _unwrap_optional(tp: Any) -> Any:
    if get_origin(tp) in (Union, types.UnionType):
        args = [a for a in get_args(tp) if a is not type(None)]
        if len(args) == 1:
            return args[0]
    return tp


def _coerce(tp: Any, value: Any) -> Any:
    tp = _unwrap_optional(tp)
    if get_origin(tp) in (list, tuple) and isinstance(value, (list, tuple)):
        item = get_args(tp)[0] if get_args(tp) else Any
        allowed = _literal_values(item)
        if allowed is not None:
            return [v for v in value if v in allowed]
        if inspect.isclass(item) and issubclass(item, BaseModel):
            return [fit(item, v) if isinstance(v, dict) else v for v in value]
        return list(value)
    if inspect.isclass(tp) and issubclass(tp, BaseModel) and isinstance(value, dict):
        return fit(tp, value)
    return value


def fit(model: type[BaseModel], data: dict) -> BaseModel:
    fields = model.model_fields
    unknown = [n for n, f in fields.items() if f.is_required() and n not in data]
    if unknown:
        raise HarnessDrift(f"{model.__name__} requires {unknown}, which the scripted reply doesn't provide "
                           f"(reply keys: {sorted(data)})")
    return model.model_validate({k: _coerce(fields[k].annotation, v) for k, v in data.items() if k in fields})


def minimal(model: type[BaseModel]) -> BaseModel:
    data = {n: _empty(f.annotation) for n, f in model.model_fields.items() if f.is_required()}
    for n, f in model.model_fields.items():
        if f.is_required() and _unwrap_optional(f.annotation) is bool:
            data[n] = True  # e.g. FixOutcome.fixed / success flags: the benign answer
    return model.model_validate(data)


def contract_symbol(name: str):
    """The contract function `name`; AttributeError (the expected failure until it exists) otherwise."""
    tried = []
    for module_name in _CONTRACT_HOMES[name]:
        try:
            module = importlib.import_module(module_name)
        except ImportError as exc:
            tried.append(f"{module_name} ({exc})")
            continue
        if hasattr(module, name):
            return getattr(module, name)
        tried.append(f"{module_name} (no {name})")
    raise AttributeError(f"contract function {name}() not implemented yet; looked in: {', '.join(tried)}")


def call_with_supported(fn, *args, **kwargs):
    """Call fn with only the keyword arguments its signature accepts."""
    params = inspect.signature(fn).parameters
    if any(p.kind is p.VAR_KEYWORD for p in params.values()):
        return fn(*args, **kwargs)
    return fn(*args, **{k: v for k, v in kwargs.items() if k in params})
