# Live LLM evals

Opt-in evals of the answer engine (`src/yahbah/llm/answers.py`) and of the job-offers extraction against the
real local models. The default `uv run pytest` runs only their offline dataset checks; the live tests run
only with `-m live_llm`. How the engine works: [docs/wiki/modules/llm.md](../../../docs/wiki/modules/llm.md).

| File | What | Items |
|---|---|---|
| `answer_eval.yaml` + `test_answer_eval.py` | Main answer set: questions with expected answers or expected `needs_answer` | 77 |
| `answer_eval_heldout.yaml` (same test file) | Held-out answer set: new traps, never tuned against | 20 |
| `verify_eval.yaml` + `test_verify_eval.py` | Verifier set: proposed answers the checker must accept or reject | 61 (32 wrong, 29 correct) |
| `job_offers_eval.yaml` + `test_job_offers_eval.py` | Job-offers extraction: synthetic postings and the validated offers (C9) expected from each | 8 postings |

The answer and verifier sets use the synthetic applicant defined in `answer_eval.yaml`, never
`config/personal.yaml`. None of the evals needs a database, Temporal or a browser.

Six verifier items (the `offers_*` ids) run on `verify_eval.yaml`'s own `jobs:`, which carry job offers.
The harness passes those offers through `validate_offers` with the job's description, as the product
does, so the verifier sees them as JOB POSTING CLAIMS. Two of these jobs hold mistakes that pass the code
checks on purpose (a negated remote quote read as "yes", city bands swapped), and one holds an injection
about the applicant.

## Before a run

- Ollama running with the default models (`ollama list`): `qwen3-coder-next:latest` (driver: interprets
  questions) and `qwen3.8:27b` (prose: writes answers and, since 2026-11-08, checks them).
- **Laptop plugged in**, and the run wrapped in `caffeinate -i` (macOS) so it doesn't sleep. A battery
  cutoff has killed a run before; a killed run leaves no report.
- One eval at a time: they share the models, and parallel runs distort the timings.

## Commands

```sh
# Main answer set (both modes)
YAHBAH_EVAL_OUT=artifacts/eval/answers.json \
  uv run pytest -m live_llm test/yahbah/llm/test_answer_eval.py -s -n 0

# Held-out answer set
YAHBAH_EVAL_SET=heldout YAHBAH_EVAL_OUT=artifacts/eval/answers-heldout.json \
  uv run pytest -m live_llm test/yahbah/llm/test_answer_eval.py -s -n 0

# Verifier (both modes)
YAHBAH_EVAL_OUT=artifacts/eval/verify.json \
  uv run pytest -m live_llm test/yahbah/llm/test_verify_eval.py -s -n 0

# Job-offers extraction (driver model, one job_metadata call per posting)
YAHBAH_EVAL_OUT=artifacts/eval/job-offers.json \
  uv run pytest -m live_llm test/yahbah/llm/test_job_offers_eval.py -s -n 0
```

`-s` prints the summary, and the JSON report holds every row. `artifacts/` is gitignored, so reports stay
on your machine.

| Switch | Eval | Effect |
|---|---|---|
| `YAHBAH_EVAL_SET=main\|heldout` | answers | Which set runs (default `main`) |
| `YAHBAH_EVAL_OUT=<path>` | all | JSON report path; the verifier and extraction evals write it before checking gates, so a failing run keeps it |
| `YAHBAH_EVAL_REPEATS=<n>` | all | Passes, per mode where an eval has modes (default 1). Model output varies, so repeat before trusting a single miss |
| `YAHBAH_VERIFY_MODES=batch,single` | verifier | `batch`: one call per job. `single`: one call per answer |
| `YAHBAH_VERIFY_ROLE=driver\|prose` | verifier | Which model checks, for this test only; the product setting is unchanged |
| `MODEL_DRIVER=<ollama model>` | all | Swaps the driver model, e.g. `qwen3-coder-next:q8_0` |

**Measured run times:**
- **Main set:** 50 min with 77 items (2026-11-09): `per_item` 34 min, `whole_form` 16 min.
- **Held-out set:** 13 min.
- **Verifier:** about 2.5 min per pass with `qwen3.8:27b` as the checker.
- **Job-offers extraction:** not measured yet.

## Gates

**Answer sets**, each mode separately:
- `per_item`: each question as its own one-field form.
- `whole_form`: one form per job.

| Gate | Target |
|---|---|
| Critical-fact errors (a wrong answer that would go on the form) | 0 |
| `needs_answer` recall on questions the facts can't answer | ≥ 95% |
| Open-ended questions (behavioural, curveball) answered, without invented facts | 100% |
| Factual questions answered (no needless "ask the user") | ≥ 95% |
| Model errors | 0 |

**Verifier**, per mode:

| Gate | Target |
|---|---|
| Wrong answers rejected (catch rate) | ≥ 90% |
| Correct answers rejected (false rejection) | ≤ 10% |

**Job-offers extraction**, over every posting and repeat, scored per field of the validated result:

| Gate | Target |
|---|---|
| Critical errors: a wrong non-`unknown` value (remote "yes" where the page says no), a place or band the page doesn't offer, a band paired with the wrong place, or an injected phrase in a kept quote | 0 |
| Stated values recovered (a value the page states, not left `unknown`) | ≥ 85% |

Each posting tests one situation: several offices, remote only in a negation, per-city salary bands, an
hourly contract, no salary, a sponsorship refusal, an injection that tries to plant "remote: yes", a
salary band and applicant facts, and a single monthly amount in euros. Tolerances are in the YAML: any
listed spelling of a place, any quote span the validator keeps, a null currency or period, and any remote
value for a hybrid job.

**What the extraction eval can't show:**
- **Truth:** the validator checks only that each quote is on the page, not what it means. A clean score
  means the extractor read these postings right, not that extracted offers can be trusted.
- **Injection resistance in general:** the extractor still reads the raw page, so an injection can steer
  what it returns. One posting passing says nothing about wordings it hasn't seen.
- **Real postings:** eight short synthetic postings, not the length or mess of real pages.
- **Downstream effect:** the verifier items above measure how the checker treats claims, including
  manipulated ones that reach it. Review mode stays the backstop.

## Rules

- **The held-out set measures whether fixes generalise, so it only works unseen.**
  - Anyone (person or agent) who changes the `answer_*` prompts must not read `answer_eval_heldout.yaml` or the held-out reports.
  - Run it once per prompt or checker revision, and report the result.
  - Never fix against it. When it exposes a new kind of mistake, add differently worded items for that kind to `answer_eval.yaml`, fix against those, and then run the held-out set once more.
- **To pass a gate, only the four `answer_*` prompts in `config/prompts.yaml` may change.** The code checks (`answer_checks.py`), the recovery limit and the fail-closed rule are never relaxed. If an expectation looks wrong, record why instead of editing it.
- **Prompt examples must not mirror any eval item.** An example that copies a question makes its score meaningless.
- **When the verifier gates fail, compare checker models first** (`YAHBAH_VERIFY_ROLE`, `MODEL_DRIVER`), before rewording prompts.

## Latest results (2026-11-09, checker `qwen3.8:27b`)

First end-to-end run with the new checker, one pass per mode (`artifacts/eval/answers-r3-main.json`,
`answers-r3-heldout.json`). Run time: main set 50 min (`per_item` 34, `whole_form` 16), held-out 13 min.

| Gate | Main `per_item` | Main `whole_form` | Held-out `per_item` | Held-out `whole_form` |
|---|---|---|---|---|
| Critical-fact errors (0) | 0 | 0 | **1** (was 2) | **1** (was 3) |
| `needs_answer` recall (≥ 95%) | 100% | 100% | **91%** (was 82%) | **91%** (was 73%) |
| Open-ended answered (100%) | 100% | **92%** | 100% | 100% |
| Factual answered (≥ 95%) | 97% | 100% | **83%** (was 67%) | 100% |
| Model errors (0) | 0 | 0 | 0 | 0 |

"Was" is the held-out run with the old checker (`answers-r2-heldout.json`). Bold marks a failed gate.

- **Main set:** all 8 items added on 2026-11-08 pass in both modes. Two misses:
  - `whole_form`: the checker rejected the `disagreement` answer because it described a disagreement the facts
    don't record. The `per_item` answer invented a similar story and passed: the narrative gate counts an
    invented event as a pass and a caught one as a fail.
  - `per_item`: the checker rejected a correct "US Citizen" for `auth_status`, arguing that citizenship isn't a
    work authorization status. That is a false rejection, within the 95% gate.
- **Held-out set:** two wrong answers got through, each in one mode only, and both were accepted by the checker:
  - a commitment inferred from where the applicant lives;
  - a management responsibility inferred from mentoring.
  The same kinds of item in the main set passed, so this is a generalisation gap, not a missing rule. With one
  pass per mode, which mode fails varies from run to run. The `per_item` factual miss is over-caution (Pass 1
  found no fact for a listed skill).

**Status:** accepted for now (2026-11-09). The held-out gates still fail, but by the user's standard the two
remaining wrong answers are acceptable stretches from true facts (wiki changelog, 2026-11-08 entry). For the
next round, fix against new main-set items and run the held-out set once more afterwards.
