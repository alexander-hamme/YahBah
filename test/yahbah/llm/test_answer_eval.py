"""Answer-integrity eval against the real local models (opt-in, live_llm). Items: answer_eval.yaml
(the main set) and answer_eval_heldout.yaml (held out: never tune prompts against it).

    uv run pytest -m live_llm test/yahbah/llm/test_answer_eval.py -s
    YAHBAH_EVAL_SET=heldout uv run pytest -m live_llm test/yahbah/llm/test_answer_eval.py -s
    YAHBAH_EVAL_REPEATS=3 YAHBAH_EVAL_OUT=/some/dir/eval.json uv run pytest -m live_llm ...

YAHBAH_EVAL_SET (main | heldout, default main) picks the one set that runs; the other set's test is
skipped, so each set runs alone with the same modes and gates.

The real AnswerEngine (llm/answers.py) plans the items for the synthetic applicant in the YAML (typed
`facts:` plus a profile), in two modes:
  - per_item:   each item alone as a one-field form;
  - whole_form: all items of one job as one form, planned once per job (the items see each other,
                batching and the prompt budget are exercised as on a real form). Items with extra
                `facts:` form their own form per job + facts, so no other item sees those facts.
Every repeat is a fresh plan (there is no answer cache). Gates, in both modes:
  - 0 critical-fact errors: a wrong answer to a screening_fact / fact item (value, a basis outside
    `basis_any`, a non-generated answer without basis, a fact labelled generated), any answer to a
    screening_trap, or a document routed to the wrong source;
  - needs_answer recall >= 95% on screening_trap + personal_fact + experience_trap items;
  - 100% of curveball and behavioural items answered, generated=True, with no invented facts
    (no number the applicant facts lack, none of the item's `forbid` phrases) and, when the item
    has `basis_any`, a basis including one of those ids;
  - >= 95% of screening_fact + fact items answered (no unnecessary abstention);
  - 0 LLMErrors.
The report adds, per mode, mean / max seconds and tokens per engine step and the calls per form
(AnswerPlan.calls).
"""
import json
import os
import re
import time
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean
from types import SimpleNamespace

import pytest
import yaml

from yahbah.agent.options import FALSY, TRUTHY, norm
from yahbah.llm.answers import AnswerEngine, AnswerPlan, JobContext
from yahbah.llm.client import LLMError
from yahbah.llm.facts import FactCatalog, load_facts
from yahbah.llm.grounding import unsupported_numbers
from yahbah.schemas import FormField

HERE = Path(__file__).parent
EVAL = yaml.safe_load((HERE / "answer_eval.yaml").read_text())
HELDOUT = yaml.safe_load((HERE / "answer_eval_heldout.yaml").read_text())
# Each set: its items and the jobs they may name (the held-out file only adds jobs).
SETS = {"main": {"items": EVAL["items"], "jobs": EVAL["jobs"]},
        "heldout": {"items": HELDOUT["items"], "jobs": EVAL["jobs"] | HELDOUT.get("jobs", {})}}
EVAL_SET = os.environ.get("YAHBAH_EVAL_SET", "main")
NARRATIVE = {"curveball", "behavioural"}
FACT_KINDS = {"screening_fact", "fact"}
ABSTAIN_KINDS = {"screening_trap", "personal_fact", "experience_trap"}
MODES = ("per_item", "whole_form")


def yes_no(value: str) -> str:
    v = norm(value)
    return "yes" if v in TRUTHY else "no" if v in FALSY else v


def form_field(item: dict) -> FormField:
    return FormField(key=item["id"], label=item["label"], field_type=item.get("type", "text"),
                     required=item.get("required", True), options=[str(o) for o in item.get("options", [])],
                     description=item.get("description", ""), section=item.get("section", ""))


def job_context(jobs: dict, name: str) -> JobContext:
    job = jobs[name]
    return JobContext(location=job.get("location"), description=job["description"], company=job.get("company"),
                      title=job.get("title"))


def facts_key(item: dict) -> str:
    """Items with the same extra `facts:` share a catalog (and, per job, a whole form)."""
    return json.dumps(item.get("facts") or [], sort_keys=True)


def build_catalogs(applicant_facts, items: list[dict]) -> dict[str, FactCatalog]:
    """{facts_key: catalog}: the applicant's facts plus each distinct `facts:` list of `items`. Leaves
    personal.yaml with the applicant's facts alone."""
    profile = SimpleNamespace(**EVAL["applicant"]["profile"])
    catalogs = {}
    for item in items:
        if (key := facts_key(item)) not in catalogs:
            applicant_facts.set_facts(EVAL["applicant"]["facts"] + (item.get("facts") or []))
            catalogs[key] = load_facts(profile)
    applicant_facts.set_facts(EVAL["applicant"]["facts"])
    return catalogs


def judge(item: dict, r: dict, material: list[str]) -> tuple[bool, str]:
    """(ok, problem) for one item result."""
    kind = item["kind"]
    if r.get("error"):
        return False, f"LLMError: {r['error']}"
    if kind in ABSTAIN_KINDS:
        return (True, "") if r["needs_answer"] else (False, f"answered {r['value']!r} ({r['source']})")
    if kind == "document":
        return r["source"] == item["expect_source"], f"source {r['source']!r} ({r['status']})"
    if r["needs_answer"]:
        return False, f"needs_answer ({r['status']})"
    value = r["value"]
    if kind in NARRATIVE:
        if not value.strip():
            return False, "empty"
        if not r["generated"]:
            return False, "not labelled generated"
        if extra := unsupported_numbers(value, item["label"], item.get("description", ""), *material):
            return False, f"invented numbers {sorted(extra)}"
        if hits := [p for p in item.get("forbid", []) if re.search(rf"\b{re.escape(norm(p))}\b", norm(value))]:
            return False, f"unsupported claim {hits}"
        if (want_basis := item.get("basis_any")) and not set(want_basis) & set(r["basis"]):
            return False, f"basis {r['basis']} lacks any of {want_basis}"
        return True, ""
    if r["generated"]:
        return False, "a fact labelled generated"
    if r["source"] in ("answer", "autofill") and not r["basis"]:
        return False, "a fact answer without basis"
    if (want_basis := item.get("basis_any")) and not set(want_basis) & set(r["basis"]):
        return False, f"basis {r['basis']} lacks any of {want_basis}"
    if "contains" in item:
        return item["contains"].casefold() in value.casefold(), f"got {value!r}"
    want = str(item["expect"])
    if want in ("yes", "no"):
        return yes_no(value) == want, f"got {value!r}"
    return norm(value) == norm(want), f"got {value!r}"


DIAGNOSTICS = ("intent", "jurisdiction", "fact_ids", "checks", "verified", "recovered", "repaired")


def item_results(plan: AnswerPlan, keys: list[str]) -> dict[str, dict]:
    """{key: {needs_answer, value, source, basis, generated, status, reason} + the plan's per-field
    diagnostics (fact ids shown, checks, verdict, recovery)} from one plan."""
    mappings = {m.key: m for m in plan.mappings}
    unresolved = {u.key: u for u in plan.unresolved}
    fields = {f["key"]: f for f in plan.fields}
    out = {}
    for key in keys:
        m = mappings.get(key)
        diag = {k: fields.get(key, {}).get(k) for k in DIAGNOSTICS}
        if key in unresolved:
            r = {"needs_answer": True, "value": "", "source": m.source if m else None, "basis": [],
                 "generated": False, "status": unresolved[key].status, "reason": unresolved[key].reason}
        elif m is None:
            r = {"needs_answer": True, "value": "", "source": None, "basis": [], "generated": False,
                 "status": "missing from plan", "reason": ""}
        else:
            r = {"needs_answer": False, "value": m.value, "source": m.source, "basis": list(m.basis),
                 "generated": m.generated, "status": fields.get(key, {}).get("status"),
                 "reason": fields.get(key, {}).get("reason", "")}
        out[key] = r | diag
    return out


async def run_form(items: list[dict], job: str, jobs: dict,
                   catalog: FactCatalog) -> tuple[dict[str, dict], dict]:
    """Plans `items` as one form for `job`: (results per item id, form record)."""
    keys = [i["id"] for i in items]
    started = time.time()
    try:
        plan = await AnswerEngine().plan([form_field(i) for i in items], job_context(jobs, job), catalog)
    except LLMError as exc:
        error = f"{type(exc).__name__}: {exc}"
        results = {k: {"needs_answer": False, "value": "", "source": None, "basis": [], "generated": False,
                       "status": "error", "error": error} for k in keys}
        return results, {"job": job, "fields": len(keys), "secs": round(time.time() - started, 1),
                         "calls": [], "error": error}
    return item_results(plan, keys), {"job": job, "fields": len(keys), "secs": round(time.time() - started, 1),
                                      "calls": plan.calls, "error": None}


async def run_mode(mode: str, eval_set: dict,
                   catalogs: dict[str, FactCatalog]) -> tuple[list[tuple[dict, dict]], list[dict]]:
    """[(item, result)] and the form records of one pass over every item of `eval_set`."""
    items, jobs = eval_set["items"], eval_set["jobs"]
    pairs, forms = [], []
    if mode == "per_item":
        for item in items:
            results, form = await run_form([item], item.get("job", "us"), jobs, catalogs[facts_key(item)])
            pairs.append((item, results[item["id"]]))
            forms.append(form)
        return pairs, forms
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for item in items:
        groups[(item.get("job", "us"), facts_key(item))].append(item)
    for (job, key), group in groups.items():
        results, form = await run_form(group, job, jobs, catalogs[key])
        pairs += [(item, results[item["id"]]) for item in group]
        forms.append(form)
    return pairs, forms


def _stats(values: list) -> dict:
    present = [v for v in values if v is not None]
    return {"mean": round(mean(present), 2), "max": round(max(present), 2)} if present else {"mean": None, "max": None}


def call_report(forms: list[dict]) -> dict:
    """Per engine step: count, secs and tokens (mean / max); calls per form (mean / max)."""
    steps: dict[str, list[dict]] = defaultdict(list)
    for form in forms:
        for call in form["calls"]:
            steps[call["step"]].append(call)
    per_form = [len(form["calls"]) for form in forms if not form["error"]]
    return {
        "steps": {step: {"calls": len(calls),
                         "secs": _stats([c.get("secs") for c in calls]),
                         "prompt_tokens": _stats([c.get("prompt_tokens") for c in calls]),
                         "output_tokens": _stats([c.get("output_tokens") for c in calls])}
                  for step, calls in steps.items()},
        "calls_per_form": _stats(per_form),
        "forms": [{k: form[k] for k in ("job", "fields", "secs", "error")}
                  | {"calls": len(form["calls"]), "steps": dict(Counter(c["step"] for c in form["calls"]))}
                  for form in forms],
    }


def summarize(rows: list[dict], forms: list[dict]) -> dict:
    by_kind: dict[str, Counter] = {}
    for row in rows:
        by_kind.setdefault(row["kind"], Counter())["ok" if row["ok"] else "bad"] += 1
    critical = [r for r in rows if not r["ok"] and not r.get("error") and (
        r["kind"] == "screening_trap" or r["kind"] == "document"
        or (r["kind"] in FACT_KINDS and not r["needs_answer"]))]
    abstain = [r for r in rows if r["kind"] in ABSTAIN_KINDS]
    facts = [r for r in rows if r["kind"] in FACT_KINDS]
    narrative = [r for r in rows if r["kind"] in NARRATIVE]
    return {
        "items": len(rows),
        "critical_fact_errors": len(critical),
        "needs_answer_recall": sum(r["needs_answer"] for r in abstain) / len(abstain),
        "fact_answered": sum(not r["needs_answer"] and not r.get("error") for r in facts) / len(facts),
        "narrative_answered": sum(not r["needs_answer"] and bool(r["value"].strip()) for r in narrative) / len(narrative),
        "narrative_ok": sum(r["ok"] for r in narrative) / len(narrative),
        "llm_errors": sum(1 for f in forms if f["error"]),
        "by_kind": {k: dict(v) for k, v in by_kind.items()},
        "secs_total": round(sum(f["secs"] for f in forms), 1),
        "model_calls": call_report(forms),
    }


def gate_failures(mode: str, summary: dict, rows: list[dict]) -> list[str]:
    bad = [f"{r['id']}: {r['problem']}" for r in rows if not r["ok"]]
    gates = [
        (summary["critical_fact_errors"] == 0, f"critical_fact_errors={summary['critical_fact_errors']}"),
        (summary["needs_answer_recall"] >= 0.95, f"needs_answer_recall={summary['needs_answer_recall']:.2f}"),
        (summary["narrative_ok"] == 1.0, f"narrative_ok={summary['narrative_ok']:.2f}"),
        (summary["fact_answered"] >= 0.95, f"fact_answered={summary['fact_answered']:.2f}"),
        (summary["llm_errors"] == 0, f"llm_errors={summary['llm_errors']}"),
    ]
    return [f"[{mode}] gate failed: {msg}; failing items: {bad}" for ok, msg in gates if not ok]


KINDS = FACT_KINDS | ABSTAIN_KINDS | NARRATIVE | {"document"}


@pytest.mark.parametrize("set_name", list(SETS))
def test_dataset_is_well_formed(applicant_facts, set_name: str) -> None:
    """Offline sanity of each eval set: the facts load (none read as a placeholder), each item's extra
    `facts:` load too and never redefine an applicant fact, item ids are unique, every kind has its
    expectation, and basis_any / job references exist."""
    eval_set = SETS[set_name]
    items = eval_set["items"]
    catalogs = build_catalogs(applicant_facts, [{}, *items])  # {} -> the applicant's facts alone
    base = catalogs[facts_key({})]
    assert base.placeholders == ()
    applicant_ids = {f["id"] for f in EVAL["applicant"]["facts"]}
    assert applicant_ids <= set(base.facts)
    assert len({i["id"] for i in items}) == len(items)
    for item in items:
        catalog = catalogs[facts_key(item)]
        extra = {f["id"] for f in item.get("facts") or []}
        assert not extra & set(base.facts), item["id"]
        assert catalog.placeholders == () and extra <= set(catalog.facts), item["id"]
        assert item["kind"] in KINDS, item["id"]
        assert item.get("job", "us") in eval_set["jobs"], item["id"]
        assert set(item.get("basis_any", [])) <= set(catalog.facts), item["id"]
        if item["kind"] in FACT_KINDS:
            assert ("expect" in item) != ("contains" in item), item["id"]
        if item["kind"] == "document":
            assert item.get("type") == "file" and item["expect_source"] in ("resume", "cover_letter"), item["id"]
    for name, job in eval_set["jobs"].items():  # company and title are optional (JobContext defaults)
        assert set(job) <= {"location", "description", "company", "title"} and job["description"], name


def test_heldout_items_are_new() -> None:
    """The held-out set shares no item id or question wording with the main set."""
    main_labels = {norm(i["label"]) for i in EVAL["items"]}
    assert not {i["id"] for i in HELDOUT["items"]} & {i["id"] for i in EVAL["items"]}
    assert not [i["id"] for i in HELDOUT["items"] if norm(i["label"]) in main_labels]
    assert not set(HELDOUT.get("jobs", {})) & set(EVAL["jobs"])


@pytest.mark.live_llm
@pytest.mark.parametrize("set_name", list(SETS))
async def test_answer_integrity_eval(applicant_facts, set_name: str) -> None:
    if set_name != EVAL_SET:
        pytest.skip(f"runs with YAHBAH_EVAL_SET={set_name} (now {EVAL_SET!r})")
    eval_set = SETS[set_name]
    catalogs = build_catalogs(applicant_facts, eval_set["items"])
    for key, catalog in catalogs.items():
        assert not catalog.placeholders, f"eval facts read as placeholders: {catalog.placeholders} ({key})"
    repeats = int(os.environ.get("YAHBAH_EVAL_REPEATS", "1"))

    report, failures = {"set": set_name}, []
    for mode in MODES:
        rows, forms = [], []
        for repeat in range(repeats):
            pairs, mode_forms = await run_mode(mode, eval_set, catalogs)
            forms += mode_forms
            for item, r in pairs:
                ok, problem = judge(item, r, catalogs[facts_key(item)].values())
                rows.append({"id": item["id"], "kind": item["kind"], "repeat": repeat, "ok": ok,
                             "problem": "" if ok else problem, **r})
                print(f"[{set_name}/{mode}] {'ok ' if ok else 'BAD'} {item['id']:28} {r['status'] or '':18} "
                      f"{'needs_answer' if r['needs_answer'] else repr(r['value'][:100])} {r['basis']}"
                      f"{'' if ok else '  <- ' + problem}")
        summary = summarize(rows, forms)
        print(f"[{set_name}/{mode}] " + json.dumps(summary, indent=1))
        report[mode] = {"summary": summary, "rows": rows}
        failures += gate_failures(f"{set_name}/{mode}", summary, rows)

    if out := os.environ.get("YAHBAH_EVAL_OUT"):
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(json.dumps(report, indent=1, ensure_ascii=False, default=str))
    assert not failures, "\n".join(failures)
