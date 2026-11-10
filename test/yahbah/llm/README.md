# Live LLM evals

Opt-in evals of the answer engine (`src/yahbah/llm/answers.py`) against the real local models. The default
`uv run pytest` runs only their offline dataset checks; the live tests run only with `-m live_llm`. How the
engine works: [docs/wiki/modules/llm.md](../../../docs/wiki/modules/llm.md).

| File | What | Items |
|---|---|---|
| `answer_eval.yaml` + `test_answer_eval.py` | Main answer set: questions with expected answers or expected `needs_answer` | 77 |
| `answer_eval_heldout.yaml` (same test file) | Held-out answer set: new traps, never tuned against | 20 |
| `verify_eval.yaml` + `test_verify_eval.py` | Verifier set: proposed answers the checker must accept or reject | 55 (29 wrong, 26 correct) |

All three use the synthetic applicant defined in `answer_eval.yaml`, never `config/personal.yaml`. They
need no database, Temporal or browser.

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
```

`-s` prints the summary, and the JSON report holds every row. `artifacts/` is gitignored, so reports stay
on your machine.

| Switch | Eval | Effect |
|---|---|---|
| `YAHBAH_EVAL_SET=main\|heldout` | answers | Which set runs (default `main`) |
| `YAHBAH_EVAL_OUT=<path>` | both | JSON report path; the verifier writes it before checking gates, so a failing run keeps it |
| `YAHBAH_EVAL_REPEATS=<n>` | both | Passes per mode (default 1). Model output varies, so repeat before trusting a single miss |
| `YAHBAH_VERIFY_MODES=batch,single` | verifier | `batch`: one call per job. `single`: one call per answer |
| `YAHBAH_VERIFY_ROLE=driver\|prose` | verifier | Which model checks, for this test only; the product setting is unchanged |
| `MODEL_DRIVER=<ollama model>` | both | Swaps the driver model, e.g. `qwen3-coder-next:q8_0` |

**Measured run times:**
- **Main set:** 50 min with 77 items (2026-11-09): `per_item` 34 min, `whole_form` 16 min.
- **Held-out set:** 13 min.
- **Verifier:** about 2.5 min per pass with `qwen3.8:27b` as the checker.

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
