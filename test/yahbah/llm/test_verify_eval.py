"""Verifier eval against the real local models (opt-in, live_llm). Items: verify_eval.yaml.

    uv run pytest -m live_llm test/yahbah/llm/test_verify_eval.py -s
    YAHBAH_VERIFY_MODES=single YAHBAH_VERIFY_ROLE=prose uv run pytest -m live_llm test/yahbah/llm/test_verify_eval.py -s
    YAHBAH_EVAL_REPEATS=3 YAHBAH_EVAL_OUT=/some/dir/verify.json uv run pytest -m live_llm ...

AnswerEngine.verify_answers (the independent verifier of the two-pass answer engine) judges synthetic
proposed answers for the applicant of answer_eval.yaml (plus verify_eval.yaml's `facts:`). Seeded-wrong
items must be rejected, correct ones accepted. Switches (environment):
  - YAHBAH_VERIFY_MODES: comma list of modes, default `batch,single`:
      batch   one verify_answers call per job (the engine splits it into VERIFY_BATCH chunks), as a
              whole form is verified;
      single  one verify_answers call per proposal, as a one-field form (or a recovery round) is.
  - YAHBAH_VERIFY_ROLE: `driver` | `prose`, default the engine's VERIFY_ROLE. Test-only: patched into
    yahbah.llm.answers for this test, the product default is unchanged. Each summary records the role
    and its model (models.spec(role).model, so a MODEL_DRIVER override shows).
  - YAHBAH_EVAL_REPEATS: passes per mode (default 1).
  - YAHBAH_EVAL_OUT: path of a JSON report {role, model, modes: {mode: {summary, rows}}}, written
    before the gates are asserted (a failing run keeps its report).
Gates, per mode (failures of every mode are collected and asserted once at the end):
  - catch rate >= 90% on `unsupported` items;
  - false rejection <= 10% on `supported` items.
A missing verdict counts as neither a catch nor an acceptance: it fails the item either way. Each
summary also gives the missed / rejected-correct ids, the no-verdict count, total seconds and mean
seconds per verify_answers call.
"""
import json
import os
import re
import time
from collections import defaultdict
from pathlib import Path
from statistics import mean
from types import SimpleNamespace

import pytest
import yaml

from yahbah.agent.options import norm
from yahbah.llm.answer_checks import PROFILE_RECORD_KINDS
from yahbah.llm.answers import VERIFY_ROLE, AnswerEngine, JobContext, ProposedAnswer
from yahbah.llm.facts import FactCatalog, load_facts
from yahbah.llm.models import spec
from yahbah.schemas import FormField

HERE = Path(__file__).parent
ANSWER_EVAL = yaml.safe_load((HERE / "answer_eval.yaml").read_text())
VERIFY_EVAL = yaml.safe_load((HERE / "verify_eval.yaml").read_text())
ITEMS = VERIFY_EVAL["items"]
# The answer-eval applicant's facts plus the verify set's own (cited only by the items naming them).
FACTS = ANSWER_EVAL["applicant"]["facts"] + VERIFY_EVAL.get("facts", [])
NO_VERDICT = "no verdict"  # the problem verify_answers gives a verdict the model left out
MODES = ("batch", "single")
ROLES = ("driver", "prose")


def proposal(item: dict) -> ProposedAnswer:
    f = FormField(key=item["id"], label=item["label"], field_type=item.get("type", "text"), required=True,
                  options=[str(o) for o in item.get("options", [])], description=item.get("description", ""),
                  section=item.get("section", ""))
    return ProposedAnswer(field=f, value=str(item["value"]), composed=item.get("composed", False),
                          evidence_ids=list(item["evidence"]), jurisdiction=item.get("jurisdiction"),
                          qualifiers=list(item.get("qualifiers", [])), quotes=list(item.get("quotes", [])),
                          absent=item.get("absent"))


def test_dataset_meets_the_minimum_and_cites_real_facts(applicant_facts) -> None:
    """Offline sanity of verify_eval.yaml: >= 12 items per class, unique ids, every evidence id exists,
    and quotes / absences are ones C7 lets through to the verifier (each quote is literally in a cited
    fact; an absence cites a fact that is not a profile record), so the verifier is what judges them."""
    applicant_facts.set_facts(FACTS)
    catalog = load_facts(SimpleNamespace(**ANSWER_EVAL["applicant"]["profile"]))
    assert catalog.placeholders == ()
    assert {f["id"] for f in FACTS} <= set(catalog.facts)
    ids = [i["id"] for i in ITEMS]
    assert len(ids) == len(set(ids))
    expects = [i["expect"] for i in ITEMS]
    assert expects.count("unsupported") >= 12 and expects.count("supported") >= 12
    assert set(expects) == {"supported", "unsupported"}
    unknown = {(i["id"], e) for i in ITEMS for e in i["evidence"] if e not in catalog.facts}
    assert not unknown, f"evidence ids not in the eval catalog: {sorted(unknown)}"
    assert {i.get("job", "us") for i in ITEMS} <= set(ANSWER_EVAL["jobs"])
    for item in ITEMS:
        quotes = item.get("quotes", [])
        assert isinstance(quotes, list) and all(isinstance(q, str) and q.strip() for q in quotes), item["id"]
        assert item.get("absent") is None or (isinstance(item["absent"], str) and item["absent"].strip()), item["id"]
        cited = catalog.values(item["evidence"])
        for q in quotes:
            assert any(re.search(rf"\b{re.escape(norm(q))}\b", norm(t)) for t in cited), (item["id"], q)
        if item.get("absent"):
            facts = [catalog.facts[e] for e in item["evidence"]]
            assert any(not (f.source == "profile" and f.kind in PROFILE_RECORD_KINDS) for f in facts), item["id"]
        proposal(item)


def job_context(name: str) -> JobContext:
    job = ANSWER_EVAL["jobs"][name]
    return JobContext(location=job.get("location"), description=job["description"], company=job.get("company"),
                      title=job.get("title"))


def calls_for(mode: str) -> list[tuple[str, list[dict]]]:
    """[(job, items)] verified together: every item of a job (batch) or each item alone (single)."""
    if mode == "single":
        return [(item.get("job", "us"), [item]) for item in ITEMS]
    by_job: dict[str, list[dict]] = defaultdict(list)
    for item in ITEMS:
        by_job[item.get("job", "us")].append(item)
    return list(by_job.items())


async def run_mode(mode: str, catalog: FactCatalog, repeats: int) -> tuple[list[dict], list[float]]:
    """(a row per item and repeat, seconds per verify_answers call) for one mode."""
    rows, call_secs = [], []
    for repeat in range(repeats):
        for job_name, items in calls_for(mode):
            started = time.time()
            verdicts = await AnswerEngine().verify_answers(job_context(job_name), catalog,
                                                           [proposal(i) for i in items])
            secs = round(time.time() - started, 1)
            call_secs.append(secs)
            for item in items:
                v = verdicts[item["id"]]
                # verify_answers fills a verdict the model left out as unsupported, "no verdict"
                supported = None if (not v.supported and v.problem == NO_VERDICT) else bool(v.supported)
                ok = supported is not None and supported == (item["expect"] == "supported")
                rows.append({"id": item["id"], "expect": item["expect"], "repeat": repeat, "job": job_name,
                             "supported": supported, "problem": v.problem, "ok": ok, "call_secs": secs})
                print(f"[{mode}] {'ok ' if ok else 'BAD'} {item['id']:42} expect={item['expect']:11} "
                      f"got={'no verdict' if supported is None else ('supported' if supported else 'unsupported')}"
                      f"{'  (' + v.problem + ')' if v.problem else ''}")
    return rows, call_secs


def summarize(rows: list[dict], call_secs: list[float], role: str, model: str) -> dict:
    wrong = [r for r in rows if r["expect"] == "unsupported"]
    correct = [r for r in rows if r["expect"] == "supported"]
    return {
        "role": role,
        "model": model,
        "items": len(rows),
        "calls": len(call_secs),
        "catch_rate": sum(r["supported"] is False for r in wrong) / len(wrong),
        "false_rejection": sum(r["supported"] is not True for r in correct) / len(correct),
        "no_verdict": sum(r["supported"] is None for r in rows),
        "missed": sorted({r["id"] for r in wrong if r["supported"] is not False}),
        "rejected_correct": sorted({r["id"] for r in correct if r["supported"] is not True}),
        "secs_total": round(sum(call_secs), 1),
        "secs_per_call_mean": round(mean(call_secs), 2),
    }


def gate_failures(mode: str, summary: dict) -> list[str]:
    gates = [
        (summary["catch_rate"] >= 0.90, f"catch_rate={summary['catch_rate']:.2f} (missed {summary['missed']})"),
        (summary["false_rejection"] <= 0.10,
         f"false_rejection={summary['false_rejection']:.2f} (rejected {summary['rejected_correct']})"),
    ]
    return [f"[{mode}] gate failed: {msg}" for ok, msg in gates if not ok]


@pytest.mark.live_llm
async def test_verifier_eval(applicant_facts, monkeypatch) -> None:
    modes = [m.strip() for m in os.environ.get("YAHBAH_VERIFY_MODES", ",".join(MODES)).split(",") if m.strip()]
    assert modes and set(modes) <= set(MODES), f"YAHBAH_VERIFY_MODES={modes}: pick from {MODES}"
    role = os.environ.get("YAHBAH_VERIFY_ROLE") or VERIFY_ROLE
    assert role in ROLES, f"YAHBAH_VERIFY_ROLE={role!r}: pick from {ROLES}"
    monkeypatch.setattr("yahbah.llm.answers.VERIFY_ROLE", role)
    model = spec(role).model
    applicant_facts.set_facts(FACTS)
    catalog = load_facts(SimpleNamespace(**ANSWER_EVAL["applicant"]["profile"]))
    repeats = int(os.environ.get("YAHBAH_EVAL_REPEATS", "1"))

    report, failures = {"role": role, "model": model, "modes": {}}, []
    for mode in modes:
        rows, call_secs = await run_mode(mode, catalog, repeats)
        summary = summarize(rows, call_secs, role, model)
        print(f"[{mode}] " + json.dumps(summary, indent=1))
        report["modes"][mode] = {"summary": summary, "rows": rows}
        failures += gate_failures(mode, summary)

    if out := os.environ.get("YAHBAH_EVAL_OUT"):
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(json.dumps(report, indent=1, ensure_ascii=False, default=str))
    assert not failures, "\n".join(failures)
