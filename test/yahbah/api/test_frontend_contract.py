"""The frontend's hand-written run-lifecycle unions (frontend/src/lib/types.ts) match run_state.py, so a
value added on the server can't reach the dashboard unlabelled. Static check: no Node needed."""
import re
from pathlib import Path
from typing import get_args

import pytest

from yahbah import run_state

TYPES_TS = Path(__file__).resolve().parents[3] / "frontend" / "src" / "lib" / "types.ts"


def _ts_union(name: str) -> list[str]:
    match = re.search(rf"export type {name} =(.*?);", TYPES_TS.read_text(), re.S)
    assert match, f"no `export type {name}` in {TYPES_TS}"
    return re.findall(r'"([a-z_]+)"', match.group(1))


@pytest.mark.parametrize("ts_name, literal", [
    ("Outcome", run_state.Outcome),
    ("RunAction", run_state.Action),
    ("FailureReason", run_state.FailureReason),
    ("SubmissionMode", run_state.SubmissionMode),
    ("Origin", run_state.Origin),
])
def test_frontend_union_matches_run_state(ts_name, literal) -> None:
    assert sorted(_ts_union(ts_name)) == sorted(get_args(literal))


def test_frontend_outcome_list_is_complete() -> None:
    match = re.search(r"export const OUTCOMES: readonly Outcome\[\] = \[(.*?)\];", TYPES_TS.read_text(), re.S)
    assert match
    assert sorted(re.findall(r'"([a-z_]+)"', match.group(1))) == sorted(get_args(run_state.Outcome))
