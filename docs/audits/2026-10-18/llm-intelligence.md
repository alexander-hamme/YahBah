# LLM & Candidate Intelligence Audit — llm-intelligence-engineer

- **Date:** 2026-10-18
- **Agent:** llm-intelligence-engineer (Claude Code development agent)
- **Revision examined:** branch `browser-use` @ `2911263`, plus uncommitted `CLAUDE.md` edit (no source changes)
- **Scope:** Runtime intelligence layer: field mapping, custom answers, answer cache, applicant-profile flow, match scoring, metadata extraction, prompts, model settings, LLM observability and evaluation.
- **Status:** Historical point-in-time audit. Findings may become outdated; the wiki and source are authoritative.

## Summary
The structural parts of the layer are sound. Mapping replies are enum-constrained to real field keys and valid sources. Known answers and simple profile values are resolved in code. Option picks return an index. Each model is always requested with one fixed set of options. I confirmed that browser-use passes `think` and `keep_alive` at the top level (`.venv/.../browser_use/llm/ollama/chat.py:22,87`), so models do not reload. The largest observed prompt was 11.9k tokens, well under the 32k/40k contexts. Two models are resident, about 78 GB of 128 GB unified memory.

The serious weakness is the free-text "custom" answer path. That path decides every question not covered by a stored answer, and it can state material applicant facts that nobody provided. In a live probe with the example profile, it answered work-authorization and sponsorship questions for Germany/EU that way. The code-side "evidence" check does not catch this. The answer cache then stores these answers permanently, with no context, and serves them ahead of the user's stored answers. A live debug run has already put one such answer in the production `answer_cache`. The user calls match scoring arbitrary, and the evidence agrees: it is one uncalibrated gestalt number, computed twice on different inputs, and the admission gate uses the noisier one. Nothing evaluates intelligence quality: the only LLM tests fake the models.

## Findings

### F1. Custom answers state material applicant facts that are not in the profile, and the evidence check does not detect it
- **Priority:** Critical
- **Confidence:** Confirmed defect (seen in a live run, reproduced with live models and with deterministic tests)
- **Issue or opportunity:** Any field the driver routes to `custom` goes to the prose model. That model is asked for an answer plus "evidence" quotes. The check at `src/yahbah/llm/field_mapper.py:355-357` only tests whether each quote is a substring of the material. It does not test whether the quote supports the answer. The material includes the whole job description and every stored answer, so the model can cite any unrelated line and pass. Stored answers also carry no jurisdiction: `work_authorization: "Yes"` gets generalized to any country.
- **Evidence:**
  - *Live run:* `artifacts/debug-20261017-001456/llm/raw.jsonl`, second prose record. The required question "Available from" got a start-date answer ('Immediately', the case already recorded at `test/yahbah/llm/test_field_mapper.py:175`). Its only evidence was two quotes from the unrelated `willing_to_relocate` and `currently_located_near_job` stored answers. The check passed and the answer was cached (F2). The previous run (`debug-20261016-235635`) gave the same answer before the evidence check existed. So the check, added to fix exactly this case, does not fix it.
  - *Live probe:* example profile only, real `FieldMapper.map` against the configured models, cache stubbed. Script: `/private/tmp/claude-501/-Users-alex-Documents-YahBah/507601a9-f685-4b67-b6bc-a4c95c794a1d/scratchpad/llm-intelligence-engineer/live_probe.py`. The profile is a US citizen; the job is a Munich role. Results:
    - "Are you legally authorized to work in Germany?" got **Yes**, evidence `work_authorization: Yes`, `work_status: US Citizen`.
    - "Will you now or in the future require visa sponsorship to work in the EU?" got **No**.
    - "Do you hold an active security clearance?" got **No**, evidence `work_status: US Citizen`.
    - "Years of experience with Python" got **5**, which is total years restated as years with one skill.
    - All of these passed the check.
  - *Correct routing:* the driver itself handled the US-scoped questions correctly (`work_authorization` / `sponsorship_required`) and sent the non-US ones to `custom`. The fabrication happens in the prose tier.
  - *Uninformative confidence:* model confidence was 1.0 on 16 of 16 probe mappings, and custom answers are floored at 0.75 (`field_mapper.py:323`). Nothing downstream uses confidence (`grep` shows no use in `agent/` or `workflows/`).
  - *Second path (traced in code, not observed):* for profile keys outside `_profile_value` (`education`, `skills`, `work_experience`, `bio`), the model's own `value` is typed in without checking it against the profile (`field_mapper.py:313-314`).
  - *Third path (seam with the forms code):* `match_option` (`src/yahbah/agent/fill.py:252-266`) does character-prefix matching. Stored answer "No" maps to **"Now"** for options `["Now", "In the future", "Never"]`, and to "Not sure" for `["Yes", "Not sure"]` (verified by calling it directly).
- **Impact:** Applications can be submitted with false work-authorization, sponsorship, clearance, availability or experience claims. These break the "never fabricate applicant information" rule and can disqualify the applicant or misrepresent them.
- **Proposed resolution:**
  1. **Typed screening facts.** Classify high-stakes screening questions deterministically, with keyword rules plus the driver's existing call: work authorization and sponsorship per country, start date or notice period, age, clearance, years overall and per skill, relocation, salary, EEO, background checks, prior employment. These may only be answered from a typed stored fact. Store authorization and sponsorship per jurisdiction. A country with no stored fact raises `NeedsAnswer`.
  2. **Citations to keys instead of quotes.** The prose schema returns `basis: list[Literal[<stored keys + profile fields>]]`, enum-constrained exactly like the mapping schema. For short-answer, select and radio fields, code accepts the answer only if it equals a cited stored value or the option `match_option` picks for that value; otherwise it raises `NeedsAnswer`. Narrative textarea answers may still be generated but are labelled `generated` for the planned review stage.
  3. **Word-boundary matching.** Make `match_option`'s prefix tier match whole words.
- **Failure scenario & validating test:**
  - *Deterministic:* `/private/tmp/claude-501/-Users-alex-Documents-YahBah/507601a9-f685-4b67-b6bc-a4c95c794a1d/scratchpad/llm-intelligence-engineer/test_audit_repro.py::test_irrelevant_evidence_passes_the_honesty_check` currently passes (4/4 repro tests pass). After the fix, its assertions should flip to `NeedsAnswer`.
  - *Live:* the F5 eval should require 0 errors on the Germany/EU, clearance and start-date items.

### F2. The answer cache keeps unverified model answers forever, ignores context, and overrides the user's stored answers
- **Priority:** Critical
- **Confidence:** Confirmed defect (traced in code, reproduced with deterministic tests, a bad row exists in the DB)
- **Issue or opportunity:**
  - *Precedence:* `_custom_answer` checks the cache before the model and before the stored answers (`field_mapper.py:338-339`).
  - *What gets cached:* answers are stored whenever the model labels them `job_specific=false` (`:365-366`), and the prompt's own examples encourage that label (`config/prompts.yaml:67-69`).
  - *Key:* only the normalized label plus options (`:226-228`). There is no profile or stored-answer version, prompt or model version, parent-question context, or provenance.
  - *Reuse:* a cached answer skips the evidence check and has no invalidation, TTL or approval step.
  - *Debug runs:* `scripts/run_pipeline.py` writes to the same production table.
- **Evidence:**
  - *Live DB* (read-only query): `answer_cache` has 2 rows. One is "Available from", 11-character answer, created 2026-10-17 00:15:24, the same second as the F1 live record. It will be typed into every future "Available from" field without any model call.
  - *Live probe:* 7 of 9 custom answers would have been cached, including the Germany/EU authorization answers and "If yes, please provide details" → "N/A".
  - *Deterministic repros* (`test_audit_repro.py`):
    - `test_conditional_followup_cache_ignores_the_parent_answer`: a cached "N/A" is reused when the parent question was answered "Yes".
    - `test_cache_shadows_an_updated_stored_answer`: after the stored `sponsorship_required` changes to "Yes", the cached "No" is still returned.
  - *Fixed-answer check:* the existing unit test `test_cached_answer_skips_the_model` (`test_field_mapper.py:76-81`) confirms that reuse never goes back to the model.
- **Impact:** One bad generation becomes a wrong answer on every later form that uses the same label. Edits to `personal.yaml` are silently ignored for any question that went through the custom path. This will also undermine the planned "edit profile from the UI" feature.
- **Proposed resolution:**
  - **Cache only two kinds of row.** (a) The mapping label → stored key; the value is resolved at use time so edits propagate. (b) Narrative answers the user has approved. Each row records provenance: run, model, prompt version, basis keys, and a hash of profile plus stored answers. A hash mismatch counts as a miss.
  - **Fix precedence:** stored answer, then approved cache, then generation.
  - **Never cache** conditional or generic labels ("If yes…", "Please specify", "Other"), or key them on the parent question and its answer.
  - **Give debug runs their own namespace** (a `scope` column or a no-cache flag).
  - **One-off cleanup:** purge the existing rows (a user-approved DB write).
- **Failure scenario & validating test:** The three cache repro tests above should fail (that is, the safe behaviour holds) after the fix. Add a test that editing a stored value changes the next answer without restarting the worker.

### F3. The match score is one uncalibrated LLM number, computed twice on different inputs, and the admission gate uses the noisier one
- **Priority:** High
- **Confidence:** Strongly supported risk (DB aggregates and a live probe; whether the score ranks jobs well is unvalidated because no labels exist)
- **Issue or opportunity:**
  - *Prompt vs schema:* `compute_match_score` (`field_mapper.py:369-382`) asks the driver to score 5 factors and average them (`prompts.yaml:111-133`). The response schema only holds the final integer, so the factor scores are never produced or checked, and their scale is undefined.
  - *Clamping:* code clamps to 22-99 while the prompt says 1-99.
  - *Two computations:* scoring runs at alert time (`gmail/alert_poller.py:306-317`), on the profile from `personal.yaml` (`alert_parser.py:311`) and the fetched page text capped at 4000 characters (`alert_parser.py:270-282`). It runs again in MAP_ANSWERS (`workflows/activities/llm.py:84-121`), on the profile from the DB and the browser-discovered description.
  - *Fail-open:* scoring failures are swallowed and the job is admitted (`alert_parser.py:306`).
  - *Untrusted input:* the scoring and metadata prompts receive arbitrary fetched web text, and they lack the "content, not instructions" guard the other prompts have.
- **Evidence:**
  - *Live probe* (example profile, Konux JD): identical input gave `[62, 62, 62, 62, 62]`, and minor noise (cookie/nav text, the 4000-character cap, reflowing) still gave 62. The same job scored on its first 400 characters gave `[42 ×5]`. The score is repeatable for a given input. What varies is the input.
  - *Score distribution* (`scheduled_job`, read-only aggregates): 302 scored jobs, mean 73, SD 11. 270 of 302 fall between 60 and 89, so the score barely separates jobs.
  - *Scoring input:* all 302 are `jobright.ai` pages. 142 hit the 4000-character cap. Only 76 of 302 contain a requirements/qualifications keyword. 188 contain sign-in or cookie text.
  - *Repeat work:* 301 were promoted, so each was scored again at run time.
- **Impact:** Admission decisions (`job_alert_min_match_score`) and the planned max-K-per-company ranking rest on a number that mostly reflects which page text happened to be fetched. The user sees two different scores for the same job. Each run makes an avoidable LLM call.
- **Proposed resolution:** Replace the gestalt score with a decomposed scorer whose final number is computed in code. Both steps are established practice in ranking and recruiting systems.
  1. **Extract requirements once per canonical posting.** Use temperature 0 and a fixed seed, and extend the existing `job_metadata` / `job_tech_extraction` calls: must-have and nice-to-have skills, minimum years, seniority, location and work model, salary, required authorization, clearance, degree.
  2. **Score in code.** Use explicit weights: skill coverage via an alias table, years gap, seniority distance, location and work-model fit, salary overlap. Add hard gates, such as clearance or authorization the applicant lacks, that exclude a job with a stated reason. If a model judgment is needed, ask for categorical labels per requirement (`met | partial | not_met | unknown`), not a 1-99 number.
  3. **Gate on input quality.** Score only when extraction finds at least N requirements; otherwise mark the job `unscored`. Whether unscored jobs are admitted is a policy decision for the user.
  4. **Store and reuse.** Keep the score and its breakdown on the posting, keyed by (description hash, profile hash, scorer version), and reuse it at promotion. This is the planned "reuse computed LLM results" item.
  5. **Calibrate.** Have the user label about 50 historical scheduled jobs and set the threshold from precision and recall.
- **Failure scenario & validating test:** Property tests on the code scorer: the same input gives the same output, and adding a required skill the applicant has never lowers the score. Integration test: promotion reuses the stored score with no LLM call. Offline eval: the agreement rate with the user's labels, for the old scorer versus the new one.

### F4. Generated answers see an incomplete, inconsistent profile; the prompt invites overclaiming; profile data has two sources of truth
- **Priority:** High
- **Confidence:** Confirmed defect (traced in code; repro test; live probe output)
- **Issue or opportunity:**
  - *Missing bullets:* `profile_text` drops the work-experience `summary` bullets (`field_mapper.py:180-189`), yet the `fallback_answer` prompt says to "draw directly from the work experience bullets" and give concrete specifics (`prompts.yaml:49-51`).
  - *Overclaiming:* the same prompt says to "convey strong familiarity in an industry/production-level setting". When no links exist, it says to claim the work was "done under NDA or in a proprietary environment" (`:53-56`). That is a factual assertion the profile does not contain.
  - *Four serializations:* the profile is serialized four ways with different contents:
    - `profile_text`: from the DB, no summaries.
    - Cover letter: from the DB, summaries but no education (`cover_letter.py:15-28`).
    - Run-time match score: from the DB (`activities/llm.py:87-104`).
    - Alert-time match score: from YAML (`alert_parser.py:311-334`).
  - *Two sources of truth:* education known answers come from YAML (`field_mapper.py:131-143`) while the rest of the profile comes from the DB seeded by `scripts/seed_profile.py`. `load_prompts_config` is `lru_cache`d (`config.py:121`), so YAML edits need a worker restart and a re-seed, and the two can drift apart.
  - *Cover letter:* it is generated on every run whether or not the form has a cover-letter field (`workflows/application.py:156-162`). It is free text at temperature 0.7 with no grounding check, and it is uploaded automatically.
- **Evidence:**
  - `test_audit_repro.py::test_profile_text_omits_work_experience_summaries` passes.
  - Live probe: "Describe a project where you deployed a backend service to AWS" got a specific-sounding claim ("At Acme Corp, I deployed backend services to AWS using Docker and Python…"), assembled from the skills list. The example profile's actual bullets say something different. The answer was marked non-job-specific, so it would be cached (F2).
- **Impact:** Narrative answers are generic or invented, and the same fact can come out differently in mapping, cover letter and scoring. The planned UI profile editing and multi-profile support would sit on this split.
- **Proposed resolution:**
  - **One canonical profile:** the DB, versioned, with known answers moved alongside it. Use a single serializer, and give each consumer a view selected by purpose (screening facts, narrative experience, scoring).
  - **Fix the prompt:** include the experience bullets and drop the overclaiming and NDA instructions.
  - **Cover letter:** generate it only when the mapped schema has a `cover_letter` source, and run the same basis/citation check on its claims before upload.
- **Failure scenario & validating test:** Golden test: `profile_text` contains every bullet marked `use_in_custom_prompts`. Live eval item: an experience question whose correct answer is only in a bullet.

### F5. No evaluation of intelligence quality, and LLM logs cannot show which task a call served or what prompt it used
- **Priority:** Medium
- **Confidence:** Confirmed defect (traced in code)
- **Issue or opportunity:**
  - *Mocked tests:* `test/yahbah/llm/test_field_mapper.py` fakes both models, so it checks code routing only. Its evidence test passed while the live run cached a fabricated answer (F1). No labelled set of questions or answers exists anywhere in `test/`.
  - *Untagged logs:* `raw.jsonl` records role, model, timing, tokens and reply (`client.py:109-120`), but not the call site, prompt template or version, or schema name. Mapping, match score, metadata, option pick, post-submit judge and email classification all appear only as `driver`.
  - *Missing logs:* calls outside an activity are not logged at all, because `logs.append` does nothing without a run directory (`logs.py:66-71`). That covers the Gmail-side calls: alert scoring, status classification, code extraction.
  - *Sampling:* structured calls use temperature 0.1 with no seed (`client.py:86`).
- **Impact:** Prompt or model changes cannot be checked for regressions, latency and errors cannot be attributed to a task, and the admission scores cannot be audited after the fact.
- **Proposed resolution:**
  1. **Tag every call.** Add `task` and `prompt_id@hash` to every `generate_*` call and log record. Log Gmail-side calls to a dated `artifacts/_service/llm/` directory. Set temperature 0 and a fixed seed for structured calls.
  2. **Smallest useful eval.** About 40 labelled screening items in YAML, using the example profile (no personal data). Cover direction pairs (authorized vs needs sponsorship), non-US jurisdictions, start date, clearance, per-skill years, EEO, conditional follow-ups, option-wording variants, and long narrative questions. Expected outcomes are a stored key, an option, or `NeedsAnswer`. Run it live against the configured models whenever prompts or models change.
  3. **Gating metrics.** Critical-fact errors must be 0, `NeedsAnswer` recall at least 95%, plus source accuracy.
  4. **Offline regressions.** Lock in the code-side guarantees with mocked regression tests like the scratchpad repros.
- **Failure scenario & validating test:** The eval run is the validation. Today it would flag at least the Germany/EU, clearance and per-skill-years items from F1.

## Documentation discrepancies
- `docs/wiki/modules/llm.md` and the `field_mapper.py:9` docstring say custom questions get "the full profile". `profile_text` leaves out the work-experience summaries (F4).
- `docs/wiki/modules/llm.md` ("`config/prompts.yaml` is currently modified in the working tree") and the architecture page's "In flux" section are stale; both were committed in 999f57a/2911263.
- `docs/wiki/modules/llm.md` does not say that match scores are computed twice, from different profile sources (YAML at alert time, DB at run time), or that the answer cache is written by debug runs and never invalidated.

## Cross-area notes
- [gmail-job-discovery-engineer] All 302 scheduled jobs were scored on fetched `jobright.ai` page text (`alert_parser.fetch_job_description`), not the employer's job description. Resolve to the canonical employer posting before scoring and metadata extraction.
- [application-forms-engineer] `match_option` (`fill.py:252-266`): character-prefix matching turns "No" into "Now", "Not sure" or "Not at this time" when there is no exact "No" option. Use whole-word prefixes. I will coordinate on the option-semantics contract.
- [product-dashboard-engineer] `answer_cache` has no review, edit or purge UI. This is needed for the planned pre-submit review and for clearing bad rows (F2).

## Proposed action plan
1. **Close the fabrication paths (F1 + F2 together).** Typed screening facts with jurisdiction, key-citation validation in code, a provenance- and version-keyed cache with stored-answer precedence, a separate debug namespace, and a purge of current rows. Outcome: no material applicant fact reaches a form unless a stored key or the user's approval backs it.
2. **Build the screening-question eval and tag LLM calls (F5).** Outcome: every prompt or model change is checked against 0 critical-fact errors, and each logged call shows its task and prompt version.
3. **Replace the match score with extract-then-score-in-code, stored per posting and reused at promotion (F3).** Outcome: the score is reproducible, explainable and calibrated against the user's labels, and the admission threshold means something.
4. **One profile source and serializer; fix the `fallback_answer` prompt and cover-letter grounding (F4).** Outcome: answers are consistent, with every claim traceable to profile content.
- **Dependencies / collaboration:**
  - *Steps 1 and 4 need new tables:* a profile/known-answer table and cache provenance, both via temporal-platform-engineer.
  - *Step 1 needs a review UI:* approval and edit, via product-dashboard-engineer.
  - *Step 1 touches the option-matching contract:* shared with application-forms-engineer.
  - *Step 3's input:* depends on gmail-job-discovery-engineer resolving canonical job descriptions.
  - *Step 2's harness:* with browser-agent-qa-engineer.
- **Defer:**
  - Embeddings or a vector store for answer reuse; normalized keys plus provenance are enough at current volume.
  - Fine-tuning or distillation, until the eval exists.
  - Escalation latency: about 100-140 s per escalated step, with native thinking plus a duplicate schema `thinking` field. Revisit with browser-navigation-engineer once trajectories are evaluated.
  - Removing the unused `anthropic` and `pydantic-ai` dependencies (`pyproject.toml`). No imports exist in `src/`, `scripts/` or `test/`, so this is hygiene only and consistent with the local-only policy. Do it with the next dependency change.
