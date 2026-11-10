"""Option matching and yes/no vocabulary, decided in code. Pure: no browser, no model.

Shared by fill (writing a value into a widget) and the answer checks (llm/answer_checks.py), so an
answer is judged against a widget's options exactly as fill will match it.
"""
import re

TRUTHY = frozenset({"yes", "true", "1", "checked", "agree", "i agree", "accept", "y", "on"})
FALSY = frozenset({"no", "false", "0", "unchecked", "n", "off", "disagree", "decline"})


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.casefold()).strip()


def match_option(value: str, options: list[str]) -> str | None:
    """The option meaning `value`, decided in code: exact, then a unique prefix of whole words ("No" is a
    prefix of "No, thanks", not of "Now"), then a unique whole-word match. None when ambiguous (a model
    decides then)."""
    v = norm(value)
    if not v:
        return None
    options = [o for o in options if o and o != "..."]
    for candidates in (
        [o for o in options if norm(o) == v],
        [o for o in options if norm(o).startswith(f"{v} ") or (norm(o) and v.startswith(f"{norm(o)} "))],
        [o for o in options if re.search(rf"\b{re.escape(v)}\b", norm(o))],
    ):
        if len(candidates) == 1 or (candidates and all(norm(c) == v for c in candidates)):
            return candidates[0]
    return None
