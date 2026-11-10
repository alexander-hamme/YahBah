# Changelog

Running record of architectural, structural, decision-making and workflow-paradigm changes, newest first. Not a commit log: no function- or bug-level detail (`git log` has that).

- **Practice:** every architecturally meaningful change (structure, interfaces, decisions, workflow paradigm) adds an entry when it lands; each batch/wave close-out adds one. Keep entries terse. Maintained by `project-knowledge-maintainer`, reviewed by the main session.
- **Entry shape:** What / Why / Governs (commit, report or contract) / Supersedes. Short SHAs are as of 2026-10-20; `browser-use` history has been rewritten before (the reflog shows amends; `development.md` cites `d1965aa`, now unreachable, with the same subject as `7f13f32`), so if a SHA does not resolve, search `git log --all` by commit subject.
- **Evidence:** entries from 2026-10-17 on are verified against the tree and the docs they cite. Older entries are reconstructed from git history; claims about motive that no commit message or doc states are marked *(inferred)*.
- **State vs. history:** this page records why things changed. For how things work now, read the module pages; for agreed targets, the contracts.

---

## 2026-11-08: the verifier runs on the prose model (`qwen3.8:27b`)

- **What.** `VERIFY_ROLE = "prose"` in `llm/answers.py`: answer verification moves from `qwen3-coder-next` to `qwen3.8:27b`, the model that also composes. The verifier eval gained a `single` mode (one call per proposal, beside `batch`), a test-only checker switch (`YAHBAH_VERIFY_ROLE`, and `MODEL_DRIVER` for the driver model) and a JSON report; it now has 55 proposals, with new seeded-wrong items for residence taken as travel or on-call agreement, absence disguised as a quote (PhD "No" quoting a bachelor's degree) and mentoring taken as managing, and new correct ones (inverse-polarity "No", "would X be a problem?" "No", a PhD "No" backed by an `education_complete` fact, composed answers naming both Acme companies). The main answer set gained 8 items for the same traps (77 items). Uncommitted.
- **Why.** The held-out run (entry below) showed the verifier accepting three wrong answers of exactly these kinds. Measured on the new set, 3 repeats per mode: 27b caught 100% (`batch`) / 96.6% (`single`; misses `on_call_yes_from_residence` every time), the driver 82.8% / 82.8% (misses the same five items every time), the driver's `q8_0` build no better (82.8% / 79.3%, one run), so the gap is the model, not its quantization. No model rejected a correct proposal in any run (0 of 78 per mode), and 27b was no slower per call. The false rejections seen in earlier engine runs did not reproduce with the reverted configuration in either mode.
- **Measured end to end (2026-11-09, one pass per mode).** Main set (77 items): every gate passes except `whole_form` narrative at 92%. That miss was the checker rejecting a `disagreement` answer that described an event the facts don't record; the `per_item` answer invented a similar story and was accepted. Held-out set: critical-fact errors 1 / 1 (old checker 2 / 3), `needs_answer` recall 91% / 91% (was 82% / 73%), factual answered 83% / 100% (was 67% / 100%). It still fails: both remaining errors (a commitment inferred from residence, a management responsibility inferred from mentoring) were accepted by the 27b checker, each in one mode only. Details: [`test/yahbah/llm/README.md`](../../test/yahbah/llm/README.md). **Risk:** the same model writes and checks composed answers; the accepted invented disagreement is one instance of that.
- **Accepted for now (user, 2026-11-09).** Answer quality is good enough to move on to the rest of the app. Accuracy in the last run: 98.7% of main-set answers in both modes, 90% / 95% held-out. **The user's direction for a later round:** on initial screener questions, stretching from true facts is acceptable (example: "Yes" to on-site five days from living in Boston plus willingness to relocate), but fabricating experience or skills the applicant isn't close to having is not. The engine's intended policy is stricter than that: a stretch should end `needs_answer`, and the eval counts one as a critical error. Enforcement is imperfect: in the last held-out run the engine produced two stretches (on-site from residence plus relocation, direct reports from mentoring), each with `verified=true` in one mode. The eval flagged them only afterwards; nothing at runtime caught them. Review mode is what shows such answers to the user before submission. Changing the policy is a decision to revisit together with the gates and the "accurate applicant information" invariant. Open ideas parked: a quote requirement for commitment questions (the checker still has to judge the meaning), and asking once for real stories behind behavioural questions.
- **Governs:** [modules/llm.md](modules/llm.md) (verifier, eval), [development.md](development.md) (eval switches). **Supersedes:** the 2026-10-24 plan's `VERIFY_ROLE = "prose"` contingency (now the default) and its "verifier on a different model than the composer" separation.

---

## 2026-11-07: answer engine round 2 follow-up (job identity for the writers, check 11)

- **What.** Three engine changes decided by the main session after the round-2 live runs, plus prompt iteration 3. Uncommitted.
  - **Job identity.** `JobContext` gains `company` and `title` (default `None`). Interpret and compose show one `JOB (untrusted page data, never evidence)` section: location, company and title (each ≤120 chars) and the trimmed description. `MapAnswersInput`, `FillInput` and `MapNewFieldsInput` gain `job_company` / `job_title` (default `None`, replay-safe); the workflow passes `discovered.job_company or metadata.company` and `discovered.job_title or metadata.title`; activities build the context with `activities.llm.job_context`.
  - **Verifier: tried and reverted (2026-11-08).** Iteration 3 also showed the verifier the company and title and made its verdict reason first (`{field_id, problem, supported}`). Measured live it was worse: per_item narrative 58.3% and facts 90.9%, whole_form narrative 91.7%. Checking six correct answers alone 4 times each, the verifier rejected 22 of 24 with both changes, 11 with company only, 10 with reason first only, 8 with neither. Both are reverted: the verifier sees only the location, and the verdict is `{field_id, supported, problem}`. The reverted configuration has not been run live.
  - **Check 11.** A composed answer to a free-text field whose Pass 1 intent doesn't start with `open-ended:` is `invalid_value` ("only an open-ended question gets a composed answer; answer this field from its facts") and goes through the normal recovery round.
  - **Prompt iteration 3.** The verifier's guidance rule applies only to cited facts that carry guidance.
- **Why.** In the round-2 runs the verifier, which saw only the job's location, rejected most motivation answers that named the hiring company next to the applicant's similarly named employer (reproduced 21/24 alone or in small batches), and none of three prompt wordings fixed it; giving it the company made that worse (above), so motivation answers remain the verifier's main weak spot. A whole-form run labelled a work-authorization summary `generated`, which bypasses the fact checks. The iteration-2 guidance rule, applied to every fact, made the verifier reject a correct residence "No" (4/16 alone, 0/16 without it).
- **Governs:** [modules/llm.md](modules/llm.md) (JobContext, verifier, check 11), [modules/workflows.md](modules/workflows.md) (activity inputs). Contract §8 and the `answer_plan` shape are unchanged.
- **Held-out run (2026-11-08, once, untuned, on the reverted configuration;** `artifacts/eval/answers-r2-heldout.json`**).** 20 new items. Narrative answered 100% in both modes, 0 `LLMError`s. Failing gates: critical-fact errors 2 `per_item` / 3 `whole_form`; `needs_answer` recall 82% / 73%; facts answered 67% `per_item` (100% `whole_form`). All three critical errors were accepted by the verifier:
  - "Can you commit to on-site five days a week in Boston?" → "Yes", because the applicant lives in Boston (residence is not a commitment to an on-site arrangement), both modes.
  - "Do you hold a master's degree or higher?" → "No", from an education list holding one bachelor's degree, both modes. The answer rests on absence but was presented as something found, so checks 8–10 pass it; only the verifier could catch it, and didn't.
  - "Have you had direct reports as a people manager?" → "Yes" from "mentored two junior engineers" (mentoring is not managing), `whole_form`; `per_item` correctly blocked it via check 9.
  - Over-cautious, `per_item` only: the verifier rejected a correct "No" to "Do you lack hands-on experience with Kafka?" (its reason calls the answer correct), and "Would daily work with Docker be a problem?" found no fact although Docker is a listed skill (Pass 1 chose none).
  - Reading: the main-set results overstated how well the meaning-level rules (neighbouring concepts, absence) generalise, and the verifier, the only gate for those, misses them. Per the held-out rule the set stays unchanged; fixes are made against new items in `answer_eval.yaml`.

---

## 2026-11-07: answer engine round 2 (named technologies, absence answers, recovery, held-out set)

- **What.** Four changes to the two-pass engine ([modules/llm.md](modules/llm.md)), answering the open decisions of the 2026-10-24 entry. Uncommitted, on top of that tree; the `answer_*` prompts, `answer_checks.py` and `answers.py` changed, the contracts and the artifact shape did not.
  - **Named technologies (decision 1).** An open-ended question about experience with a specific named technology, tool, product, certification or domain that no fact mentions is no longer composed (no bridging to related work, no denial): Pass 2 returns `needs_more_evidence` (a fact in `OTHER FACTS` might mention it) or `missing_fact` with `requested_info` "Your experience with X, or that you have none". A required field blocks as `needs_answer` with a `fact_template`; an optional one stays blank, as before. Once the user stores a fact that mentions the thing (e.g. "No professional AWS experience; I deploy Docker services on our own servers"), later applications compose an honest answer from it. General behavioural questions with no such named thing are still composed from the bio and work history. The verifier also rejects a composed answer that claims or denies experience with a named thing no cited fact mentions. Pass 1 keeps the name in the intent and selects facts whose description or aliases mention it. New eval kind `experience_trap` (counted in the `needs_answer` recall gate).
  - **Absence answers (decision 2).** `ComposedAnswer`/`ProposedAnswer` gain `quotes` (words copied exactly from the cited facts, what was found) and `absent` (what was looked for and not found). **Profile records** are the profile facts of kind `skills`, `bio`, `work_experience` and `education` (`PROFILE_RECORD_KINDS`). Three new deterministic checks in `llm/answer_checks.py`, after the number check: **8** each quote must appear (whole words, `norm()` forms) in a cited fact's value, else `unsupported`; **9** an `absent` that rests on profile records alone is `unsupported`; **10** a non-composed answer citing only profile records with neither `quotes` nor `absent` is `uncited`. They ignore the answer's polarity. The verifier now sees `quotes` and `absent` and judges whether the quoted words establish this answer to this question (another name for the same thing counts, a different thing under a similar name does not) and whether a cited stated fact establishes an absence or the completeness of a list. A "have you worked for X before?" or "do you have experience with Y?" answered "no" therefore needs a stored fact that says so or declares the list complete: the user adds `employment_history_complete` (in `config/personal.example.yaml`, with the `kubernetes_experience` example).
  - **Recovery shows cited-but-unshown facts (decision 3).** In the one recovery round, an answered field whose problem is `cites_unshown` gets every cited id that is in the catalog but was not shown added to what Pass 2 sees, with the feedback "The facts you cited without having been shown them are now shown in full; cite a fact only if its content supports the answer." No extra call; the verifier still checks the recomposed answer.
  - **Overfit (decision 4).** The two prompt examples that mirrored eval items (notice period vs start date; a similarly named company) were reworded; no prompt example may mirror an item in `answer_eval.yaml`, `verify_eval.yaml` or the held-out file. A held-out set of 20 new items, `test/yahbah/llm/answer_eval_heldout.yaml`, runs through the same live harness (`YAHBAH_EVAL_SET=heldout`) with the same gates. Items may carry their own `facts:` (seen only by that item; `whole_form` groups items by job + facts). The main set grew to 69 items and the verifier eval to 46 proposals (25 wrong, 21 correct; proposals may carry `quotes` and `absent`).
- **Why.** The 2026-10-24 evals passed only because compose refused absence answers by prompt rule, the verifier could not see them (it missed answers resting on absence from a list), and a passing narrative answer still said "While I have not deployed to AWS…". The aim is a safety net that does not depend on the models' manners, without teaching code what the words mean.
- **Principle (user).** **Code checks bookkeeping, models judge meaning.** Whether "Postgres" is PostgreSQL, Java is JavaScript, or the "Acme" in a question is the applicant's Acme Data or the hiring company, given the job, are model judgments. Code only enforces that a quote is literally in the cited fact, that an absence cannot rest on profile lists alone, and that a profile-list answer says which of the two it is. No synonym tables, no deterministic equivalence rules.
- **Decisions (user, 2026-11-07).**
  1. **Named technologies the facts don't mention are not composed:** `missing_fact` naming the thing, so a required field blocks as `needs_answer`; the user stores the fact once.
  2. **Revised absence rule:** quotes/absent bookkeeping (checks 8–10) plus the user storing `employment_history_complete`.
  3. **Recovery shows cited-but-unshown facts** in full (no extra call).
  4. **Overfit:** reword the two mirroring prompt examples and add a held-out set of new traps, run once without tuning.
- **User step.** Add `employment_history_complete` to the `facts:` list of `config/personal.yaml` **if your work history in the profile is complete** (template: `config/personal.example.yaml`); until then, "have you worked for X before?" questions block as `needs_answer`. Optionally store facts such as `kubernetes_experience` for technologies you get asked about.
- **Process rule.** The held-out file is written by someone who does not read `config/prompts.yaml`; prompt editors do not read it; it is run once per prompt revision and never used to tune a prompt ([development.md](development.md#live-evals-answer-engine-opt-in-live_llm)).
- **Open risks.** Expect more `needs_answer` runs (named technologies and absence questions now wait for a stored fact). Whether a stored fact establishes an absence, and whether quoted words answer a question, are still model judgments (the verifier is one independent pass); review stays the final gate.
- **Governs:** [modules/llm.md](modules/llm.md) (engine, checks 8–10, tests and eval), [development.md](development.md) (eval commands). Contract §8 is unchanged (`FieldMapping` and the `answer_plan` shape; quotes never enter the artifact). **Supersedes:** the 2026-10-24 prompt rule that an OPEN field about a technology the facts lack "describes the closest real work without naming the missing one", and its open decisions (1)–(4).

---


- **What.** Application questions are answered by `llm/answers.py` instead of `FieldMapper`: code autofill → **Pass 1** (driver) interprets every field and selects fact ids → code retrieves the full fact blocks → **Pass 2** (prose) composes the value and cites fact ids → deterministic checks (`llm/answer_checks.py`) → an independent **verifier** (driver) → one bounded recovery round (requested facts or a `reselect` call → `recompose` → checks → `reverify`) → a `FieldMapping` plan that the unchanged deterministic fill executes. Typical cost is 3 model calls per form batch, at most 6. Detail: [modules/llm.md](modules/llm.md).
  - **Facts.** The applicant's knowledge is a typed `facts:` list in the gitignored `config/personal.yaml` (`id`, `kind`, `value`, `data_type`, `description`, `aliases`, `examples`, `guidance`, `jurisdiction`, `sensitivity`), plus `profile.<field>` facts built from the profile (the run's alias is the `profile.email` fact). Placeholder values are ignored; `kind` is only a retrieval aid. Edits apply to the next plan without a restart. `llm/facts.py`.
  - **Schema context.** `FormField` gains `description` (aria-describedby helper text) and `section` (nearest heading); option lists are shown up to 40 instead of 15.
  - **`FieldMapping`.** `confidence` is gone (with `Settings.min_field_confidence`); `source` is `autofill | answer | resume | cover_letter | skip`; `basis` holds fact ids; the `email` alias override is gone. Old in-flight histories still fill: `FillContext.value_for` reads `value` for any source other than skip/resume/cover_letter, and nothing rebuilds `FieldMapping(**m)` from history.
  - **Answer plan artifact.** Every plan writes an `answer_plan` artifact (stages `map_answers`, `fill`, `map_new_fields`; fact ids only, never values), replacing `field_mappings`. `scripts/run_pipeline.py` writes `answer_plan*.json`.
  - **Fill-side guards.** `fill_field` writes only the planned value in every mode (the unknown-key schema read is gone); `upload_document` checks its planned document in every mode; the agent's free-text `input` is refused (`VALUE_NOT_PLANNED`) unless contained in a planned value; a planned value matching none of the live widget's options is re-answered once through the engine with the page's options (`NoMatchingOption`, `replan_option_misses`, first fill only), never chosen by an agent. `agent/options.py` holds `norm`, `match_option`, `TRUTHY`, `FALSY`.
  - **Baseline in every fill.** The approved-state baseline (2026-10-20) was taken only on the locked re-fill. `fill_form` now takes it in every fill, and `problems_for` reports in every mode a field without a planned answer (no mapping, `skip` or empty value) whose content changed from its baseline. That closes the unlocked-fill write paths the `input` guard cannot see (`send_keys`, `clear:false` fragments, `select_dropdown`, radio clicks, browser-use's `upload_file`); a first fill now fails closed (`fill_failed`). `locked` governs only new-field handling and the `wrote_from` check; `FillOutput.baseline` is set after first fills too, but only the re-fill's is carried to SUBMIT. Contract §4 "Approved-state baseline".
  - **Needs-answer UX.** The run page of a `needs_answer` run lists each unresolved question with its status, reason, needed information and a paste-ready YAML fact skeleton (`unanswered-questions.tsx`); `error_message` lists `label (status)` for every blocking question.
  - **Match score** moved to `llm/match_score.py` (`compute_match_score`), salary from the `salary_expectation` fact; the alert poller imports it from there.
- **Why.** Coverage depended on regex phrases over question labels and on key names in a flat `known_answers:` map, so a rephrased question or an unconventional key silently fell to the prose model or to `needs_answer`. The engine reads the form's own context (label, helper text, section, neighbours), selects evidence by meaning, and still lets code, not a model, decide what is written and whether it is supported.
- **Decisions (user, 2026-10-24).**
  1. **Legacy `known_answers`: migrate once, then cut over.** `scripts/migrate_known_answers.py` writes a draft `config/personal.facts-draft.yaml` and never touches `personal.yaml`; after the cutover the loader reads only `facts:`, and a leftover `known_answers:` fails loudly (`FactsConfigError` with the migration steps).
  2. **`answer_cache`: dropped** (module, model, tests, and the table by migration 0020).
  3. **Unsupported answers on required fields fail closed.** If the verifier or a check rejects an answer and the one recovery round doesn't fix it, the run ends `FAILED` / `needs_answer`; optional fields are left blank.
  4. **Needs-answer UX.** The failed run's page lists each unresolved question; the user edits `personal.yaml` and retries. No write API.
  - Main session: the plan's contracts C1–C8 fixed the interfaces before implementation; the verifier runs on the driver (`VERIFY_ROLE`), to be switched to prose only if the live verifier eval misses its gate.
- **Removed.** `llm/field_mapper.py`, `llm/screening.py`, `llm/answer_cache.py`, the `AnswerCache` model, `grounding.supported`, the profile's `known_answers` / `stored_answers` / `education_answers` / `facts` / `stored_text`, the prompt keys `field_mapper`, `fallback_answer`, `screening_answer`, `Settings.min_field_confidence`, the `field_mappings` artifact write, and their tests (`test_field_mapper.py`, `test_screening.py`, `test_answer_cache.py`).
- **Migration steps for the user.** Until step 2, live runs end at MAP_ANSWERS as `internal_error` carrying these instructions.
  1. `uv run python scripts/migrate_known_answers.py`
  2. Review `config/personal.facts-draft.yaml`, move its `facts:` list into `config/personal.yaml` and delete `known_answers:`.
  3. `uv run alembic upgrade head` (applies 0020, which drops `answer_cache`).

  Then `uv run python scripts/run_pipeline.py <job-url>` (no submit) to inspect `answer_plan.json`.
- **Verification (main session, 2026-10-25, final tree).** `uv run pytest` (default): 1145 passed, 3 skipped (one earlier run hit the known random-URL-prefix flake in `test_discover_blocks.py::test_a_walled_wrapper_naming_a_job_opens_the_greenhouse_form[path_id]`, which passed on rerun; unrelated to this change); C7 acceptance gate 3/3 (`--complete test/invariants/c7_acceptance`), its `answer_plan` artifacts hold fact ids only; frontend `npm test` 126 passed, tsc and lint clean. No application has been submitted against a live site.
- **Live eval results (2026-10-24/25;** driver `qwen3-coder-next:latest`, prose `qwen3.8:27b`, verifier on the driver). Commands and gates: [development.md](development.md#live-evals-answer-engine-opt-in-live_llm); full numbers: [modules/llm.md](modules/llm.md#tests-and-eval).
  - **First prompts missed the answer gates** (3 critical-fact errors, `needs_answer` recall 84.2%, curveball/behavioural 61.5% per item and 23.1% whole-form). **Three tuning iterations of the four `answer_*` prompts** (the plan's limit; no code check or fail-closed rule was relaxed) changed Pass 1 to mark open-ended questions with an `open-ended:` intent, Pass 2 to class fields OPEN vs FACT, never answer from absence, treat guidance as binding and cite only shown facts, and the verifier to read a boolean fact's value over its description and reject "no / none / never" experience answers no fact states.
  - **Result:** every gate passes in `whole_form` (0 critical-fact errors, recall 100%, curveball/behavioural 100%, facts answered 100%, 0 `LLMError`s). `per_item` passes all but curveball/behavioural at 92.3% (`aws_deploy`: the verifier rejected a composed answer that denied AWS experience, twice). Verifier: catch 90.5% (21 wrong, 18 correct after QA added 3 absence items) at exactly the 90% gate, 0% false rejection; it misses answers resting on absence from a skills list or work history, which compose now refuses (`missing_fact`). Calls per form: `per_item` mean 2.98 (max 5); `whole_form` 12 for a 54-field form, 4 for 4–6 fields.
  - **Open decisions for the user:** (1) whether `aws_deploy` / `kubernetes_bait` belong in the 100% narrative gate (passing whole-form answers still say "While I have not deployed to AWS…", which the gate doesn't catch); (2) a code-side rule rejecting a "no" whose only basis is profile list facts, or a stronger verifier model, since the verifier gate passes at exactly 90.5%; (3) treating cited-but-unshown catalog ids like requested ids during recovery (a whole-form `postgres` answer failed `cites_unshown` twice in one baseline run); (4) possible eval overfit, as two prompt examples sit close to eval items. **Resolved by the [2026-11-07 entry](#2026-11-07-answer-engine-round-2-named-technologies-absence-answers-recovery-held-out-set):** (1) decision 1 there (a named technology no fact mentions is not composed), (2) decision 2 and the new checks 8–10 (the stronger verifier model was not needed as a stand-in), (3) decision 3, (4) decision 4.
- **Open risks.**
  - Retrieval and support depend on the facts' descriptions and on two local models: a vague description is found less often, and the verifier is one independent model pass (no entailment check in code beyond citations, formats, dates and numbers). Review remains the final gate; the batch-2 reviewer pass is meant to add another.
  - The verifier is the weakest gate (catch 90.5% against a 90% gate, and blind to absence-based "no" answers); the planned fallback `VERIFY_ROLE = "prose"` was not exercised and the checks and the fail-closed rule must not be relaxed to compensate. Wall time is long on local models (1797 s `per_item`, 780 s `whole_form` over the 64 items). *Absence-based answers are now also gated in code by the 2026-11-07 checks 8–10.*
  - A required question outside every fact now stops the run (`needs_answer`) where the prose model used to improvise; until the user's facts are complete, expect more `needs_answer` runs. The migration draft's kinds and jurisdictions are guesses from key names and need review.
  - Worst case is 6 model calls per form batch plus the fill-time re-plans, so answering is slower than the single mapper call it replaces.
  - In-flight runs already `AWAITING_REVIEW` approve and re-fill normally, but one that reaches `map_new_fields` before the migration ends `internal_error`.
  - The working tree is uncommitted.
- **State.** Uncommitted, on top of the 2026-10-23 tree. **Process.** Led by the main session with specialists for the LLM layer, forms/fill, browser schema, dashboard, platform (migration) and QA *(roles inferred from the plan; nothing authoritative records who built which part)*.
- **Governs:** [contracts/run-lifecycle.md](contracts/run-lifecycle.md) §2, §4 ("Planned values only"), §8 (`FieldMapping`, `answer_plan`), §9 (migration 0020), and the module pages ([llm](modules/llm.md), [agent](modules/agent.md), [workflows](modules/workflows.md), [data-model](modules/data-model.md), [api-frontend](modules/api-frontend.md)). **Supersedes:** the 2026-10-20 "answer integrity (task 5)" mechanism (typed `known_answers` screening, the grounded answer cache, `FieldMapper`'s lexical grounding checks) and migration 0019's cache provenance; its safety intent (facts-only factual answers, `NeedsAnswer` instead of guessing, generated prose labelled and number-checked) is kept and now enforced by the engine.

---

## 2026-10-23: runtime settings file is machine-local

- **What.** `config/settings.yaml` is gitignored and no longer tracked. A committed template, `config/settings.example.yaml`, lists every writable runtime key with its code default (`submission_mode: review`); setup is `cp config/settings.example.yaml config/settings.yaml`. The code does not create the file: a missing file reads as `{}` (all code defaults, pollers off), and `PUT /settings/{key}` creates or rewrites it with `yaml.dump`, which drops comments. The shipped-file test in `test/yahbah/api/test_runtime_setting_validation.py` now checks the example (every key validates as it stands).
- **Why.** The file holds machine-local runtime state, such as the mode toggle, which the dashboard rewrites; committing it leaked local choices into the repo.
- **Governs:** `.gitignore` and `config/settings.example.yaml`; [modules/data-model.md](modules/data-model.md) (Configuration), README (setup). **Supersedes:** the tracked `config/settings.yaml`.

---

## 2026-10-21: discover classification, board route, headed fallback

- **What.** Discover's dead ends are now decided by code from page evidence, never by what a page or a model says.
  - **Classification.** One pure function, `classify_page(PageEvidence, board)` in `agent/discover.py`, returns `blocked`, `job_closed`, `captcha`, `login_wall`, `browser_error` or nothing, from the main document's HTTP status (Navigation Timing), vendor bot-wall markup (Akamai, Cloudflare, PerimeterX, DataDome, Imperva), the board's closure URL and the count of visible controls. `blocked`, `job_closed` and `browser_error` are checked before any model tier and again after each click that led nowhere; `captcha` and `login_wall` only after the tiers fail (a guest-apply path may exist). A visible application form always wins. The navigation agent's `job_closed`/`login_required`/`captcha` answers are advice in the detail; an uncertain one is `form_not_found`. Precedence: [contract §2](contracts/run-lifecycle.md).
  - **Board route.** A wrapper page that names a board's job (`gh_jid`, or a job-id-shaped number in the path) is swapped for the board's own form (`BoardProfile.application_routes`, `route_verified`, `closed_url_patterns`; Greenhouse only), loaded and verified in the browser, before the wrapper's own evidence decides.
  - **Headed fallback.** If the early verdict is `blocked` and no board route verifies, discover reopens the URL once in a fresh, throwaway, headed Chromium (`SessionRegistry.reopen_headed`); the run stays headed for its life, through review and the approved re-fill and submit. Gated by the runtime setting `browser_headed_fallback` (default `true`, snapshotted at run creation) with a dashboard switch beside the mode control. Threaded as `DiscoverInput.headed_fallback`, `DiscoverOutput.headed` and `headed` on `MapFormInput`/`FillInput`/`SubmitInput`; fill and submit evidence record it.
  - **Tooling.** `scripts/run_pipeline.py --headed-fallback`; the test fixture server can answer a page with its own status, headers or a redirect; 18 saved pages in `test/yahbah/fixtures/discover/`.
- **Why.** Live run `83a0efc2` (rubrik.com behind Akamai) was labelled `job_closed` by the model on a 403 "Access Denied" wall: a bot wall read as a closed job, which the dashboard then reported as "no longer accepts applications". Per the agent's background notes (not re-verified here), headless gets a request-level 403 there and headed on the same IP is served.
- **Decisions (user, 2026-10-21; contract §2).**
  1. **Board route first, browser-only:** no Greenhouse API or other side-channel HTTP. Every candidate is loaded and verified in the page (host, job id, form signature); another job's form is never accepted.
  2. **Then the headed fallback:** one attempt, setting default `true`.
  3. **Else `blocked`.** browser-use's default launch flags (including disabling `AutomationControlled`) are accepted as they are; YahBah adds no masking, and never uses stealth, captcha solving, retry loops or proxies.
  4. Main session: every `blocked` is also appended to `_observations.jsonl` with whether the URL named a board job, to show how often a walled page has no board route.
- **Process.** Browser navigation, QA and dashboard specialists, judging by the tree *(inferred; nothing authoritative records who built which part)*.
- **Verification (main session, 2026-10-23).** `uv run pytest` (default, leaves out `e2e`): 1094 passed, 2 skipped in 82 s; frontend `npm test` 120 passed (15 files), `npx tsc --noEmit` and `npm run lint` clean. No `e2e` result is recorded for this work. No application has been submitted against a live site; run `83a0efc2` only reached discover.
- **State.** Committed on top of `7b46619`.
- **Governs:** [contracts/run-lifecycle.md](contracts/run-lifecycle.md) §2 (amended through 2026-10-21) and the module pages ([agent](modules/agent.md), [workflows](modules/workflows.md), [data-model](modules/data-model.md), [api-frontend](modules/api-frontend.md)). **Supersedes:** model-decided `job_closed`/`captcha`/`login_wall` at discover (the `_NAV_FAILURES` mapping is deleted; the answers are advice); `form_not_found` as the label for a walled page (now `blocked`, or `job_closed` for a 404/410 on an inert page); the unused `resolve_application_url` profile hook (replaced by `application_routes`); headless-only browser sessions; the dashboard's old `job_closed`/`blocked` wording.

---

## 2026-10-20: review-queue triage

- **What.** `/review` opens the selected run in a compact preview by default; the full review (with its tick box) is one step away. Keys: `j`/`k` move, `y` approve, `n` reject, `o` open the full review, `u`/`Esc` undo. Frontend only: no backend or API change. See [modules/api-frontend.md](modules/api-frontend.md) (Triage).
- **Why.** The user wanted to triage the queue quickly: eyeball each run, say yes or no, and open only the unsure ones.
- **Decisions.**
  1. Main session: no bulk endpoint and no approve-all. Every decision is one approve or reject call carrying the round of the payload the preview rendered, so a stale round is a per-run 409.
  2. Accepted from the implementer: the 5 s undo window applies to reject too, and hiding the tab cancels pending decisions.
  3. Adopted from QA: an 800 ms arming dwell, after QA found that holding `y` approved one run after another.
  4. Drops and unknown outcomes (network error or 5xx) are reported visibly.
- **Process.** Product & Dashboard implemented; Browser Agent QA reviewed adversarially and found 9 defects that the 75 green tests missed, then 3 more on re-verification (dwell counting hidden-tab time, in-flight outcomes lost after leaving the page or on remount). All are fixed; the frontend suite is now 112 tests.
- **Governs:** [contracts/run-lifecycle.md](contracts/run-lifecycle.md) §4, "Triage". **Supersedes:** the roadmap's open "bulk approval" follow-up.

---

## 2026-10-20: batch 1, wave 2 + close-out

**State:** wave 1 is committed (`d04bc45`) and wave 2 is committed on top of it (`d886823`), QA-verified. Migrations 0018 and 0019 were applied to the dev database on 2026-10-20 (alembic at 0019; 338 runs, 0 claims, an empty answer cache), and `config/settings.yaml` now has `submission_mode: review` in place of `testing_mode`. Verified on 2026-10-20: the complete Python suite (`pytest --complete`, parallel by default) gives 849 passed, 1 skipped; the default `pytest` runs in about 35 s and leaves out only `e2e`; frontend 64 tests, `tsc` and `eslint` clean. All 91 wave-1 strict `xfail`s were flipped, and the C7 acceptance gate passes.

### What changed
- **Run lifecycle and review gate (task 3).** The contract is implemented: [contracts/run-lifecycle.md](contracts/run-lifecycle.md) is now authoritative, not a target.
  - **Vocabulary:** statuses `AWAITING_REVIEW`, `SUBMIT_UNCERTAIN`, `EXPIRED`; a `failure_reason` column; `submission_mode` (`testing | review`, failing closed to `review`) replaces `testing_mode`/`is_test_run`; `origin`; server-derived `outcome` and `actions`.
  - **Review gate:** the workflow fills, saves evidence, closes the browser and waits in `AWAITING_REVIEW` (default 48 h). An approval re-fills exactly the approved answers and submits; a required field nobody approved sends the run back for another review round; rejection and expiry submit nothing.
  - **Duplicate protection:** `possibly_applied()` at every dedup site, matching by canonical URL; an atomic `claim_submission()` backed by the `submission_claim` table, which outlives its run.
  - **Durability:** fenced status writes, a DB-level attempt lease (`RunLeaseInterceptor`) and `SessionRegistry` ownership, heartbeats, and resume on restart (no RUNNING-to-FAILED sweep).
  - **Retry and cancel:** retry creates a new linked run; cancel goes through the workflow, with API reconciliation when the workflow is gone; confirm-submitted and confirm-not-submitted resolve `SUBMIT_UNCERTAIN`.
  - **Evidence and API:** per-attempt evidence files and a `submit_evidence.jsonl` artifact; one error-body shape; HTML artifacts served as `text/plain` with a CSP sandbox; only `jobs.py` creates runs.
- **Submit boundary (task 4).**
  - **Success** is decided by structural page evidence and the model only advises; a pending state replaces fixed sleeps; a re-click happens only on code-detected errors or code inputs; an unproven submit raises `submit_unverified`, never a flagged result.
  - **The claim** is taken after the submit button is located.
  - **Agents cannot submit:** `GuardedTools` uses one submit/advance classifier (`agent/controls.py`) for every agent phase, through shadow DOM and frames, covering click, input, keys, focus and coordinate clicks. A post-agent tripwire records a possible submission, also when the agent run is cancelled.
  - **Fields:** `unmapped_new_fields` feeds the review loop; the §8 evidence rows; a locked, approved re-fill; the `match_option` whole-word fix.
  - **Verification codes:** `wait_for_verification_code` plus finalize-after-acceptance; `fetch_verification_code` is deleted.
- **Answer integrity (task 5).** Factual screening questions are answered only from typed stored facts with jurisdiction keys; otherwise `NeedsAnswer`. Open-ended questions are always answered and labelled `generated`. Answers cite keys (`basis`), not quotes, and the job description never counts as evidence. The answer cache holds only grounded facts, with provenance (migration 0019 purged legacy rows). `llm/profile.py` is the one profile and serializer; prompts and stored answers are re-read on file change; a cover letter is generated only when the form asks for one. An opt-in `live_llm` eval (53 items) met every target.
- **Dashboard and API (task 6b).** A `/review` queue and review panel (approve unlocks only after the user ticks the box); a mode control for new runs; a UI driven entirely by `outcome` and `actions`; an uncertain-run resolution panel and nav count; validated, effective settings; the outcome filter, sort and stats; a test that fails when the TypeScript unions drift from `run_state.py`.
- **QA and tooling.** An independent review found 10 defects plus 1 follow-up, all fixed (`test/invariants/batch1_review/`). `slow` and `e2e` markers (left out of a bare `pytest`), a `--complete` option and a parallel path; see [development](development.md).

### Decisions recorded (main session)
1. **Contract amendments:**
   - the agent tripwire always ends `SUBMIT_UNCERTAIN` and is never retried;
   - submit evidence is recorded per round;
   - the attempt lease (§7);
   - confirm-not-submitted clears `submit_attempted_at`, so FAILED still means "never attempted";
   - `PENDING` means waiting for a run slot, including after an approval;
   - run-creation and delete rules, the Temporal end states, and the error-code list.
2. **Approved-state baseline.** What fields without an approved answer showed after code's fill is carried from fill to submit (`FillOutput.baseline` to `SubmitInput.baseline`); a locked submit without one fails closed. *Why:* otherwise an agent's write is indistinguishable from a starting value.
3. **Mode control is two-way** (Testing | Review). `auto_reviewed` arrives with the batch-2 reviewer pass.
4. **Legacy read-side shim kept, and labelled:** the dashboard still reads the 27 pre-0018 `submitted_values` artifacts (`normalizeEvidenceRow` in `frontend/src/components/review/answer-evidence.tsx`; runs without a `submission_mode` show as "Legacy run"). Remove it if those runs are deleted.
5. **Accepted remaining risks:**
   - a stale attempt can claim once in the window before its retry takes the lease, but never makes a second click;
   - the approve-then-reject race (a reject in that moment is ignored; cancel still works);
   - the run-slot backlog stalls at about 100 or more waiting runs.
6. **Open risk: invented STAR incidents** in generated behavioural answers. They are labelled `generated` and visible at review. The mitigation is the batch-2 reviewer pass and an eval judge for invented events.

- **Why:** the audit's safety failures sat at the boundaries between agents, tasks and the DB; wave 2 closes them against the contract and the wave-1 harness.
- **Governs:** the [contract](contracts/run-lifecycle.md) and the updated module pages ([workflows](modules/workflows.md), [data-model](modules/data-model.md), [agent](modules/agent.md), [llm](modules/llm.md), [api-frontend](modules/api-frontend.md)). Status and follow-ups: the roadmap's "Batch 1 status (2026-10-20)".
- **Supersedes:** `testing_mode`/`is_test_run`; the worker's RUNNING-to-FAILED sweep; retry-in-place; dedup on `status == COMPLETED` only; `mark_attempted`/`mark_submit_attempted`; the quote-evidence answer check and unprovenanced cache rows; the `status`/`exclude_status` list filters and the status, failed-badge and testing-toggle components. The wave-1 harness bridges for these (including `KNOWN_DRIFT`) are deleted.

---

## 2026-10-19: batch 1 decisions, contract, wave 1

### Wave 1 close-out (committed in `d04bc45`)
Three parallel tasks plus main-session groundwork, all verified against the tree: `uv run pytest` gives 240 passed and 91 xfailed in about 67 s; frontend `npm test` 7 passed, `npx tsc --noEmit` clean.
- **QA harness and invariant suite (task 1).**
  - `test/conftest.py` and `test/harness/` form one shared, offline harness:
    - process guards (no app DB, loopback-only DNS and sockets, no Ollama outside `live_llm`, temp artifacts, a synthetic applicant);
    - a local HTTP fixture server with synthetic ATS pages;
    - scripted models (`ScriptedLLM`, and `AgentScript` driving the real browser-use loops);
    - a disposable Postgres database per session (`TEST_DATABASE_URL`);
    - a Temporal `WorkflowEnvironment` running the real worker with stubbed browser and LLM activities.
  - Markers `browser`, `db`, `temporal`, and opt-in `live_llm`.
  - `test/invariants/` holds 95 tests keyed to the contract and roadmap A2-A5/A7: 91 are strict `xfail` that the wave-2 owners flip, 4 are guards that already hold. The pure `run_state` rules are tested in `test/yahbah/test_run_state.py`. Full suite at close-out: 240 passed, 91 xfailed.
  - Finding: `job_posting.canonical_url` and `location` exist in the ORM and production DB but in no migration; the harness bridged this (`KNOWN_DRIFT`) until migration 0018 repaired it (wave 2).
- **Discovery gate (task 2).**
  - Both Gmail pollers do nothing unless their flag is exactly `true`, including the first cycle at startup (the unconditional startup polls are gone).
  - Alert admission: only a job that was scored, at or above `job_alert_min_match_score`, becomes `SCHEDULED`; an unscorable job is `SKIPPED`.
  - Promotion is bounded: `job_alert_max_promotions_per_cycle` (default 5) and `job_alert_max_age_days` (default 14); stale alerts are left alone and stale `SCHEDULED` rows expire.
  - URL intake policy (`url_utils.validate_job_url`, `fetch_public`): public http(s) hosts only, checked on every redirect hop, 2 MiB response cap. It applies at `POST /jobs`, `POST /scheduled` (422), alert links, description fetches and `submit_job_for_application`; the browser agent is not subject to it.
  - Identity: `gh_jid` is kept (it is the job id on company-hosted Greenhouse pages) and `imp_id` is stripped on jobright.ai only.
  - Verification codes: a structural heuristic replaces "first `<h1>`"; boards declare the code shape (`BoardProfile.email_code_pattern`); `GmailClient.wait_for_verification_code` changes nothing in the mailbox and `finalize_code_message` marks it processed once the page accepted the code. `agent/submit.py` still called the legacy one-step `fetch_verification_code` at this point; task 4 switched it and deleted the wrapper (wave 2).
- **Main-session groundwork.** `src/yahbah/run_state.py` is the contract's code skeleton (vocabulary, `derive_outcome`, `allowed_actions`; the DB functions raise `NotImplementedError` until task 3). `FieldMapping` gained `basis` and `generated` (nothing populates them yet). `BROWSER_USE_VERSION_CHECK=false` stops a PyPI query at every Agent start.
- **Dashboard (task 6, security part).**
  - Job-site HTML is rendered only through `frontend/src/components/untrusted-html-frame.tsx`, a sandboxed iframe with a no-network CSP fed by `frontend/src/lib/untrusted-html.ts`. No component uses `dangerouslySetInnerHTML` any more.
  - `next dev` and `next start` bind 127.0.0.1.
  - The API rejects non-loopback `Host` and `X-Forwarded-Host` headers (`TrustedHostMiddleware` plus a small ASGI check in `src/yahbah/api/main.py`; 13 tests).
  - Frontend tooling: Vitest with Testing Library, ESLint 9 (flat config; the plugins bundled with `eslint-config-next` do not support ESLint 10), and `frontend/scripts/check-untrusted-html-sandbox.mjs`, a real-browser check that the sandboxed frame runs no script and makes no request. A rules-of-hooks bug in `artifact-viewer.tsx` was fixed. The `@types/node` bump (^20 to ^22) awaits the user's OK. `npm run lint` then reported 2 older errors (`react-hooks/set-state-in-effect`) and 6 warnings; it has been clean since wave 2.
- **Why:** untrusted content must not reach trusted sinks (R5) and the unauthenticated API must not be LAN-reachable (A6). The gate lets the worker start without ingesting months of alerts and launching unreviewed runs. The harness and invariant suite give wave 2 an objective acceptance test.
- **Governs:** [roadmap](../audits/2026-10-18/roadmap.md) (A1, A6, "First batch" tasks 1, 2, 6) and the contract; test setup in [development](development.md).
- **Supersedes:** unconditional startup polls and the `<h1>`-only code heuristic; raw HTML injection of scraped content; `next dev` on all interfaces.
- **Contract revised during the wave:** review rounds (`review_round`, round-matched approve/reject), `actions` on run responses, a uniform error-body shape, the `Blocker` return type of `possibly_applied()`, and migration 0018's repair of the schema drift above.

### Batch-1 contract: run lifecycle, review gate, submission safety
- **What:** [contracts/run-lifecycle.md](contracts/run-lifecycle.md) fixes the shared names and semantics for wave 2:
  - run statuses `AWAITING_REVIEW`, `SUBMIT_UNCERTAIN`, `EXPIRED` and a `failure_reason` vocabulary;
  - the review gate with approve/reject signals;
  - `submission_mode` (`testing | review`; `auto_reviewed` in batch 2) and `origin`;
  - one dedup predicate, `possibly_applied()`;
  - an atomic `claim_submission()`;
  - fenced status writes and browser ownership;
  - retry as a new linked run;
  - server-derived `outcome` and `actions`;
  - migrations 0018 and 0019;
  - per-task file ownership.
- **Why:** the audit's safety failures sat at the boundaries between agents, tasks and the DB, so the boundaries are specified once before parallel work starts.
- **Governs:** the contract itself (owned by the main session). *Update 2026-10-20:* implemented by wave 2 and now authoritative; the module pages describe the implemented behaviour.
- **Superseded (implemented 2026-10-20):** `testing_mode`/`is_test_run`, the worker's RUNNING→FAILED sweep, retry-in-place, dedup on `status == COMPLETED` only, `mark_attempted` (replaced by `claim_submission`).

### Decisions resolved
[roadmap](../audits/2026-10-18/roadmap.md), "Conflicting recommendations: decisions" and "Decisions (resolved 2026-10-19)".
- **Every run passes a review gate (user decision).** Default `review`: fill, save evidence, wait up to 48 h for approval, then re-fill the approved answers and submit; no approval means `EXPIRED`, never auto-submit. `testing` fills only; `auto_reviewed` (a reviewer-model pass) is opt-in, batch 2. *Why:* unattended live submission is not yet safe. *Supersedes:* the boolean `testing_mode`, and Gmail F1's "fill-only for discovered jobs".
- **Worker restart policy is resume.** Remove the RUNNING→FAILED flip in `worker.fail_incomplete_runs`; add heartbeat timeouts. Cancel is how a run is stopped. *Why:* the activities already re-open the browser, and the old flip let a "FAILED" run resume and submit (audit probe B). *Supersedes:* the current FAILED sweep. Until it lands, behaviour is as described in [workflows](modules/workflows.md).
- **Uncertain outcome has one source, three views:** status `SUBMIT_UNCERTAIN`, a `failure_reason` column, a server-derived `outcome`; the frontend holds no status logic. *Why:* "possibly submitted" was being stored as FAILED, so retry and dedup re-applied.
- **Retry creates a new run** (`retry_of_run_id`) and returns 409 if the original attempted submit; retry and cancel stop the old workflow first. *Supersedes:* reset-in-place and the proposed attempt counter.
- **The agent submit guard is action-level**, not network-level: one button classifier shared by `click_submit` and a wrapper over the agent's click tool, plus a post-step tripwire. *Rejected:* blocking non-GET requests (breaks uploads and autosave).
- **Admission: stopgap now, dispatcher later:** explicit activity concurrency and a promotion cap per cycle now; a Temporal PENDING-queue dispatcher once discovery yields jobs.
- **Form-classification ownership is split by layer:** Forms owns the button classifier and agent tool wrapper (batch 1); Navigation's page classifier follows in batch 2. They never edit `agent/tools.py` together.

### Guiding direction: step-aware, multi-approach loops; replace, don't layer
- **What:** the revamp moves from a static pipeline (map the form once, answer once, fill once, one submit) to page-interaction loops that take any job URL, click through to the form, get past or report account walls, walk multi-step flows and answer any field including open-ended curveballs. Every interaction climbs the same verified tier ladder: board-seeded code, one structured model call, an agent loop with vision.
- **Replace, don't layer:** static-paradigm code is deleted when its replacement lands; no parallel old and new paths.
- **Why:** several audit findings are the old paradigm surviving in the new code (roadmap table "Static assumption still in code"). Batch 1's safety work comes first because loops on unfamiliar pages are where a stray submit or false "submitted" happens.
- **Governs:** roadmap "Guiding direction" and B0 (the step-aware application loop, centrepiece of batch 2, spec-first with the main session owning the loop contract).

### Documentation practice
- **What:** this changelog; `CLAUDE.md` "Documentation Maintenance" now requires an entry for every architecturally meaningful change, wiki updates when behaviour changes or text goes stale, and delegates documentation writing to `project-knowledge-maintainer` with main-session review. The `CLAUDE.md` name `project-wiki-manager` was wrong and is corrected.
- **Why:** every audit flagged stale "uncommitted / in flux" text, and several wiki claims had been wrong since the revamp.
- **Also:** a stale-text pass over `architecture.md`, `index.md`, `data-model.md`, `llm.md`, `agent.md`, `workflows.md`; and, at the wave-1 close-out, the README (local-only note, Gmail behaviour, tests, persistent Temporal), `CLAUDE.md` commands, `api-frontend.md` and `development.md`.

---

## 2026-10-18: seven-specialist audit; orchestration model

- **What:** seven specialist agents audited the revamp at `2911263`, each writing a report with reproduction probes; the main session synthesised the [roadmap](../audits/2026-10-18/roadmap.md) (synthesis dated 10-19). Reports are indexed in [docs/audits](../audits/README.md). Seven reports, five findings each, 30 confirmed.
- **Findings that drove priorities:** the revamped pipeline had never run for real (338 runs in the DB are all from before it; 14% function coverage); discovery had produced no usable jobs (all 302 scheduled jobs were jobright.ai aggregator pages; 301 promoted runs failed in one burst); safety invariants failed at boundaries (false "submitted" verdicts, a restart resurrecting a FAILED run, a fabricated cached answer, SPA submit controls passing the guard). Seven shared root causes (R1-R7) are listed in the roadmap.
- **Also (89b1667):** `CLAUDE.md` became the orchestration manual: the main session as Engineering Lead delegating to seven specialist development agents (distinct from runtime LLMs), with task contracts, ownership and acceptance rules.
- **Why:** onboarding audit of a large, untested rewrite before the first live submission.
- **Governs:** audit reports are historical (point-in-time); the wiki and source are authoritative.
- **Supersedes:** the single-session workflow for substantial tasks.

---

## 2026-10-17: agentic, code-first revamp lands; wiki created

### The revamp (`999f57a`, branch `browser-use`)
- **What:** `src/yahbah/agent/` replaces the Playwright/Greenhouse `browser/` package and the LangGraph generic agent.
  - Every browser stage tries deterministic code, then one structured model call, then an agent loop, and verifies each tier's result in code (discover, map form, fill, submit/verify).
  - Board profiles (`agent/boards.py`) hold portal-specific knowledge; Greenhouse and a generic fallback exist.
  - Agents get named tools only, no submit tool and no free-form JS; submit is gated code, recorded before the click.
  - Two model roles on local Ollama: `qwen3-coder-next` (driver: agents, structured calls) and `qwen3.8:27b` (prose, vision, escalation), each with one fixed `num_ctx` so Ollama never reloads; every call is logged per run.
  - Answer mapping: autofill tokens in code, one constrained driver call, prose model for custom questions, an answer cache.
  - Run slots (`max_concurrent_runs`), an email-verified flag from confirmation emails, generalised Gmail code fetch, migration 0017.
- **Why:** `999f57a` ports the browser-use spike and adds what it lacked: fill, gated submit and post-submit verification. Reliable behaviour on arbitrary sites with slow local models needs deterministic code first and models as verified fallbacks.
- **Governs:** [architecture](architecture.md) "Core design rule"; [agent](modules/agent.md).
- **Supersedes / removed:** `browser/` (Greenhouse modules, 1,708-line `greenhouse.py`), `llm/page_analyzer.py`, `config/site_templates/`, Playwright and LangGraph as dependencies. History is kept on `archive/pre-agentic`.

### Docs and wiki (`2911263`)
- **What:** README rewritten for the revamp; `docs/wiki/` created (architecture, module pages, development; progressive-disclosure index); `CLAUDE.md` quick reference and conventions; `.env.example` updated.
- **Why:** the old README described the Playwright/Greenhouse system. Depth lives in the wiki and the README stays a setup guide.
- **Supersedes:** the pre-revamp README.

---

## 2026-10-14: pivot to browser-use

- **What:** `5ae0d05` swapped the dependencies `langgraph`/`langchain-*` for `browser-use==0.13.11` and began tracking `.gitignore`. `7f13f32` committed the LangGraph-era `config/site_templates/{generic,greenhouse}.yaml` and `scripts/spike_browser_use.py` (a Phase-0 spike testing browser-use as the element-discovery/action engine on a Playwright-launched Chromium); the branch `archive/pre-agentic` points at that commit and preserves the pre-revamp code.
- **Why:** the spike tested browser-use as a replacement for hand-built element discovery, and the revamp (10-17) adopted it. *(inferred: no commit states why LangGraph was dropped.)*
- **Supersedes:** the LangGraph generic agent (next entry).

## 2026-10-11: LangGraph generic form agent *(inferred era boundary; two "commit changes before rework" commits)*

- **What:** `5e5b993`/`b72110b` added `browser/generic.py`, a LangGraph state graph (snapshot page -> LLM judge "is the real form visible" -> execute action -> extract fields, max 10 iterations), with `llm/page_analyzer.py` and `generic_form_judge`/`generic_form_analyzer` prompts. `ats_type` (`greenhouse` | `generic`) became a workflow input; the Greenhouse extractor ran first and fell back to the generic agent; generic extraction got a 30-minute timeout. YAML site templates carried per-site hints and a human-approved "confirmed patterns" idea.
- **Why:** support job sites other than Greenhouse (stated in the `generic.py` docstring). It was still LLM-judged and selector-guessing, and the run stayed Playwright-based.
- **Supersedes:** the Greenhouse-only extraction path (kept as the first attempt). Itself superseded by the revamp.

---

## 2026-04-20 to 2026-08-26: job-alert discovery and the scheduled queue

- **What (2026-04-20, `cf9cfcc`, `24272ae`):** a second Gmail poller (`gmail/alert_poller.py`, `alert_parser.py`) detects job-alert emails by sender pattern, extracts job URLs, fetches a description, scores relevance with the LLM and inserts jobs into a `scheduled_job` queue (migration 0016). A promotion step starts a real run after a delay (`job_alert_promotion_delay_hours`, default 24) unless the job is held or skipped. `/scheduled` UI page; shared `jobs.py` entry point used by both `POST /jobs` and promotion; `url_utils.py` reworked for URL normalisation and tracking-parameter stripping.
- **Why:** automate intake from email alerts (hands-off applications); the delay and match-score threshold leave a window to hold or reject. *(inferred from the design; no commit message states it.)*
- **Later:** `145fce4` (2026-08-26, tip of `main`) is frontend error handling only.
- **Governs:** [gmail](modules/gmail.md). The audit found this path had never produced a usable job (see 2026-10-18); the discovery gate (wave 1) and B2/B3 rework it.
- **Supersedes:** manual-only job entry via `POST /jobs`.

## 2026-04-01 to 2026-04-10: product surface around the pipeline

- **What:**
  - Next.js frontend (04-01); dark-cockpit restyle (04-03; branch `ui/dark-cockpit`, an ancestor of `main`, so merged).
  - Prompts moved to `config/prompts.yaml` (04-01).
  - Rich job metadata on `job_posting` (migrations 0004-0013).
  - Testing mode (04-04): fill without submitting.
  - Match score (04-07).
  - Runtime settings in `config/settings.json`, then YAML (04-07, 04-10), editable through the API.
  - Per-run email aliases so replies match their application (04-08).
  - Gmail status poller classifying replies into `application_status_update` (04-07 to 04-10; migration 0015).
- **Why:** observe and control the pipeline from the UI, track outcomes by email, and judge fit before applying. *(inferred; motive not stated in commits.)*
- **Supersedes:** the CLI/log-only view of the first pipeline.

## 2026-03-28 to 2026-03-31: original Playwright/Greenhouse pipeline

- **What:** the initial commit (`d169fcc`) set the architecture that persisted through the revamp: FastAPI control plane, a Temporal workflow `ApplicationWorkflow`, activities for browser (Playwright, Greenhouse-only), LLM (Ollama `llama3.3:70b`: field mapping and cover letter) and DB (Postgres via SQLAlchemy and alembic). Then: autofill, file and cover-letter upload, form validation handling (03-29/30), Gmail API verification-code retrieval (03-30; migration 0003), embedded/iframe form handling, and the first successful Greenhouse submission (`7625295`, 03-31).
- **Why:** a hands-off "Greenhouse job URL in, submitted application out" system on local models.
- **Governs:** the Temporal + FastAPI + Postgres + local-Ollama skeleton is unchanged; the browser, LLM-model and dependency choices were later replaced.
- **Supersedes / superseded by:** the Playwright/Greenhouse browser layer was removed in the revamp (2026-10-17); the single model became the two-role setup.
