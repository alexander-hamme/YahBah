"""Answer-integrity eval against the real local models (opt-in, live_llm). Items: answer_eval.yaml.

    uv run pytest -m live_llm test/yahbah/llm/test_answer_eval.py -s
    YAHBAH_EVAL_REPEATS=3 YAHBAH_EVAL_OUT=/some/dir/eval.json uv run pytest -m live_llm ...

Each item is a one-field form mapped by the real FieldMapper (driver mapping call, screening code,
prose answers) for the synthetic applicant in the YAML. The answer cache is an in-memory stand-in
that never hits, so every repeat is a fresh answer. Gates (roadmap task 5):
  - 0 critical-fact errors: a wrong stored-fact answer, or any answer to a screening trap;
  - NeedsAnswer recall >= 95% on screening traps;
  - 100% of curveball and behavioural items answered, generated=True, with no invented facts
    (no number the applicant facts lack, none of the item's `forbid` phrases).
"""
import json
import os
import time
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from yahbah.llm import answer_cache
from yahbah.llm.client import OllamaClient
from yahbah.llm.field_mapper import FieldMapper, NeedsAnswer
from yahbah.llm.grounding import unsupported_numbers
from yahbah.llm.profile import facts as all_facts
from yahbah.llm.screening import yes_no
from yahbah.schemas import FormField

EVAL = yaml.safe_load((Path(__file__).parent / "answer_eval.yaml").read_text())
NARRATIVE = {"curveball", "behavioural"}


def _norm(s: str) -> str:
    return " ".join(s.casefold().split())


def judge(item: dict, r: dict, material: list[str]) -> tuple[bool, str]:
    """(ok, problem) for one result."""
    kind = item["kind"]
    if kind in ("screening_trap", "personal_fact"):
        return (True, "") if r["needs_answer"] else (False, f"answered {r['value']!r}")
    if r["needs_answer"]:
        return False, "NeedsAnswer"
    value = r["value"]
    if kind in NARRATIVE:
        if not value.strip():
            return False, "empty"
        if not r["generated"]:
            return False, "not labelled generated"
        if extra := unsupported_numbers(value, item["label"], *material):
            return False, f"invented numbers {sorted(extra)}"
        if hits := [p for p in item.get("forbid", []) if p in _norm(value)]:
            return False, f"unsupported claim {hits}"
        return True, ""
    if r["generated"]:
        return False, "a fact labelled generated"
    if "contains" in item:
        return (item["contains"].casefold() in value.casefold(), f"got {value!r}")
    want = str(item["expect"])
    if want in ("yes", "no"):
        return yes_no(value) == want, f"got {value!r}"
    return _norm(value) == _norm(want), f"got {value!r}"


@pytest.fixture
def no_cache(monkeypatch):
    async def lookup(*args, **kwargs):
        return None

    async def store(*args, **kwargs):
        return None

    monkeypatch.setattr(answer_cache, "lookup", lookup)
    monkeypatch.setattr(answer_cache, "store", store)


@pytest.fixture
def model_calls(monkeypatch):
    calls: list[tuple[str, float]] = []
    real = OllamaClient._call_ollama

    async def counted(self, *args, **kwargs):
        started = time.time()
        try:
            return await real(self, *args, **kwargs)
        finally:
            calls.append((self._role, time.time() - started))

    monkeypatch.setattr(OllamaClient, "_call_ollama", counted)
    return calls


async def run_item(item: dict, profile, jobs: dict, calls: list) -> dict:
    f = FormField(key="q1", label=item["label"], field_type=item.get("type", "text"), required=True,
                  options=[str(o) for o in item.get("options", [])])
    job = jobs[item.get("job", "us")]
    before, started = len(calls), time.time()
    try:
        m = (await FieldMapper().map([f], profile, job_description=job["description"],
                                     job_location=job.get("location")))[0]
        result = {"needs_answer": False, "value": m.value, "basis": m.basis, "generated": m.generated,
                  "source": m.source}
    except NeedsAnswer:
        result = {"needs_answer": True, "value": "", "basis": [], "generated": False, "source": None}
    result.update(secs=round(time.time() - started, 1), calls=[role for role, _ in calls[before:]])
    return result


def summarize(rows: list[dict]) -> dict:
    by_kind: dict[str, Counter] = {}
    for row in rows:
        by_kind.setdefault(row["kind"], Counter())["ok" if row["ok"] else "bad"] += 1
    critical = [r for r in rows if not r["ok"] and r["kind"] in ("screening_fact", "screening_trap", "fact")
                and not (r["kind"] != "screening_trap" and r["needs_answer"])]
    traps = [r for r in rows if r["kind"] == "screening_trap"]
    narrative = [r for r in rows if r["kind"] in NARRATIVE]
    return {
        "items": len(rows),
        "critical_fact_errors": len(critical),
        "needs_answer_recall": sum(r["needs_answer"] for r in traps) / len(traps),
        "narrative_answered": sum(not r["needs_answer"] and bool(r["value"].strip()) for r in narrative) / len(narrative),
        "narrative_ok": sum(r["ok"] for r in narrative) / len(narrative),
        "by_kind": {k: dict(v) for k, v in by_kind.items()},
        "model_calls": dict(Counter(c for r in rows for c in r["calls"])),
        "secs_total": round(sum(r["secs"] for r in rows), 1),
    }


@pytest.mark.live_llm
async def test_answer_integrity_eval(applicant_facts, no_cache, model_calls) -> None:
    applicant_facts.set(EVAL["applicant"]["known_answers"])
    profile = SimpleNamespace(**EVAL["applicant"]["profile"])
    material = list(all_facts(profile).values())
    repeats = int(os.environ.get("YAHBAH_EVAL_REPEATS", "1"))

    rows = []
    for repeat in range(repeats):
        for item in EVAL["items"]:
            r = await run_item(item, profile, EVAL["jobs"], model_calls)
            ok, problem = judge(item, r, material)
            rows.append({"id": item["id"], "kind": item["kind"], "repeat": repeat, "ok": ok,
                         "problem": "" if ok else problem, **r})
            print(f"{'ok ' if ok else 'BAD'} {item['id']:28} {r['secs']:6.1f}s {r['calls']} "
                  f"{'NeedsAnswer' if r['needs_answer'] else repr(r['value'][:110])}"
                  f"{'' if ok else '  <- ' + problem}")

    summary = summarize(rows)
    print(json.dumps(summary, indent=1))
    if out := os.environ.get("YAHBAH_EVAL_OUT"):
        Path(out).write_text(json.dumps({"summary": summary, "rows": rows}, indent=1, ensure_ascii=False))

    failures = [f"{r['id']}: {r['problem']}" for r in rows if not r["ok"]]
    assert summary["critical_fact_errors"] == 0, failures
    assert summary["needs_answer_recall"] >= 0.95, failures
    assert summary["narrative_ok"] == 1.0, failures
