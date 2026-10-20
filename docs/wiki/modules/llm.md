# LLM Layer (`src/yahbah/llm/`, `src/yahbah/agent/llm.py`)

All models are local Ollama. Nothing sends page or personal data to the cloud.

## Model roles (`llm/models.py`)
`Role = driver | prose | vision | escalation`. `spec(role)` / `ollama_options(role)` give fixed `num_ctx`, `num_predict` (4096; 6144 when thinking), `think`, `keep_alive`, so a model is always requested with identical options and never reloads mid-run.
- driver: `settings.model_driver` (default `qwen3-coder-next:latest`, ctx 40960): agent loops, HTML work, mapping JSON, screening derivations, page judging; text only.
- prose / vision / escalation: `settings.model_prose_vision` (default `qwen3.8:27b`, ctx 32768); escalation has `think=True`.
Defaults live in `src/yahbah/config.py` (`Settings`); verify there, as they are tuning knobs.

## Clients
- `llm/client.py`: `OllamaClient(role)` with `generate_structured` (output constrained by a Pydantic JSON schema), `generate_text`, optional images. Raises `LLMError`.
- `agent/llm.py`: browser-use `ChatOllama` subclass for agent loops ([agent](agent.md)).
- `llm/logs.py`: per-run log dir `artifacts/<run_id>/llm/` (`raw.jsonl`, `steps/`, `js_calls.jsonl`) selected via a contextvar set at the start of each activity (`use_run_log_dir`, which also records the run id for `current_run_id()`); total `llm` logs capped at 250 MB (oldest pruned).

## Applicant data (`llm/profile.py`)
One canonical profile and one serializer.
- **Profile:** the DB `applicant_profile` row (seeded from `personal.yaml`'s `profile:` by `scripts/seed_profile.py`).
- **Stored answers:** `personal.yaml` `known_answers`, plus `education_*` keys derived from the profile's first education entry (an explicit known answer wins).
- **Freshness:** `config.load_prompts_config()` re-reads `prompts.yaml` and `personal.yaml` whenever either file's mtime or size changes, so an edited stored answer applies to the next mapping without a worker restart. `load_prompts_config.cache_clear()` forces a re-read.
- **`facts(profile)`:** the citable namespace for `basis`. It holds every stored-answer key and every profile field with its current value: `full_name`, `first_name`, `skills`, `bio`, `work_experience[i]`, `education[i]`. Entries with `use_in_custom_prompts: false` are left out. The job description is never in it.
- **`profile_text(profile, contact=True)`:** the only profile serializer, one `key: value` line per fact. Each `work_experience[i]` line carries that role's bullets (`summary`). Field mapping, custom answers, cover letters and the run-time match score (`contact=False`) use it.

## Field mapping (`llm/field_mapper.py`): facts are retrieved, narratives are generated
`FieldMapper(cache_namespace="live").map(fields, profile, job_description, prior, *, job_location=None)` returns one `FieldMapping` per field, each with `basis` (the keys backing it) and `generated`. For a required field it cannot back, it raises `NeedsAnswer(label)`, and the run ends `FAILED` / `needs_answer`. An optional field it cannot back is left empty (`skip`).
1. **Autofill tokens** (`given-name`, `email`, ...) map to profile keys in code.
2. **One constrained driver call** picks a source per remaining field: a stored-answer key, a profile key, `resume` / `cover_letter`, `custom` or `skip`. JSON-schema enums allow only real keys.
3. **Screening questions** (`llm/screening.py`) are answered only from typed stored facts, never by the prose model. The driver's pick is only a preference. Details in the next section.
4. **Stored-answer and profile keys** are resolved in code (`basis = [key]`). A model-written value for a composite profile key (`skills`, `work_experience`, `education`, `bio`) must be found in that field; otherwise the question goes to the custom path.
5. **Custom questions** go to the prose model (`fallback_answer` prompt).
   - **Input:** the question, the recent answers on the form, the applicant facts (stored answers plus `profile_text`, with the bullets) and the job description. The job description is sent only when the reply may be prose, and is labelled "context only, never evidence".
   - **Reply schema** (`_FallbackAnswer`, enums built per call): `generated`, `insufficient_info`, `answer` (an enum of the options when there are options), `basis` (an enum of the fact keys) and `job_specific`. Every property is required in the decoding schema.
   - **Checks in code:**
     - A factual answer (`generated=False`) needs a basis, and must be found in the values it cites (`grounding.supported`; yes/no answers are exempt). Its numbers must appear in those values.
     - **Material questions are never generated:** yes/no choices, `date` / `number` / `email` / `tel` / `url` fields, credential questions ("Do you have a driver's license?"), and choice fields not phrased as a personality pick. Their schema has no `generated` field, and an unbacked answer is rejected.
     - An open question answered without a basis is labelled `generated=True`. A curveball with nothing to cite is therefore still answered.
     - **Generated prose may not introduce numbers:** every number must appear in the applicant facts or the question. Otherwise there is one rewrite, then the offending sentences are dropped (`grounding.unsupported_numbers` / `drop_sentences`).
     - "N/A" to a conditional follow-up is accepted only with a basis. If the reply cites none, it inherits the basis of the preceding negative answer.
   - **The prompt** asks for grounding in the bullets, with no invented employers, projects, metrics, tools or credentials, STAR structure for behavioural questions, and brief in-character answers for curveballs. The old NDA and "strong familiarity" instructions are gone.

## Screening facts (`llm/screening.py`)
- **`classify(label)`:** keyword rules decide which fact kinds a question asks about:
  - `work_authorization`, `sponsorship`, `citizenship`: per jurisdiction;
  - `skill_years`: per skill, e.g. "years of experience with Python";
  - `total_years`, `start_date`, `salary`, `clearance`, `age`, `relocation`, `background_check`, `criminal_record`, `prior_employment`, `non_compete`;
  - `eeo_gender`, `eeo_race`, `eeo_hispanic`, `eeo_veteran`, `eeo_disability`, `eeo_orientation`, `eeo_transgender`, `eeo_pronouns`.

  STAR and other open prompts ("Tell us about a time you debugged a race condition") are excluded unless they ask about authorization, sponsorship, salary or notice.
- **Typed facts, by stored-key name:**
  - Unsuffixed `work_authorization`, `sponsorship_required` and `citizenship` describe the country of residence (stored `country_of_residence`, default the US).
  - Other jurisdictions use suffixed keys: a country name or code, or `eu` (`work_authorization_germany`, `sponsorship_required_eu`). A jurisdiction matches only itself.
  - `work_status` ("US Citizen") counts for authorization and citizenship in the country its value names.
  - Per-skill years are `years_<skill>`. Total years is the profile's `years_of_experience`.
  - The full list of key patterns is in `fact_kinds()`. `config/personal.example.yaml` documents the common ones.
- **The job's country** answers unscoped questions ("Will you require sponsorship?"):
  - from `job_location` first (the workflow passes `discovered.job_location or metadata.location`), including structured locations such as "Munich, BY, DE";
  - otherwise from the countries, cities and US states named in the job description;
  - if neither names one, it is the country of residence;
  - if several are named, it is ambiguous, and the question gets `NeedsAnswer`.
- **`resolve()`, in code where possible:**
  - the stored value as is;
  - a flipped yes/no for "...without sponsorship?";
  - years thresholds ("5+ years?") and range options ("6-8"), compared numerically;
  - digits for `number` fields;
  - dates only from date-shaped values.
- **Otherwise,** one driver call (`screening_answer` prompt, `_ScreeningAnswer` schema) sees only that question's candidate facts. `basis` is an enum of their keys, and a cited key is required. This covers compound questions ("authorized ... without sponsorship?"), "Are you a US citizen?" answered from `work_status`, and option lists such as a work-authorization status.
- **No fact for a kind and scope** gives `NeedsAnswer` with no model call.

## Answer cache (`llm/answer_cache.py`, table `answer_cache`, migration 0019)
- **What is cached:** only grounded factual custom answers (`generated=False`, with a basis). Generated prose is never cached, nor are conditional labels ("If yes…", "Other", "Please specify", labels under 3 words) or company- and job-specific labels ("us", "this role", a capitalized name that occurs in the job description, or the model's `job_specific`).
- **Provenance per row:** `namespace` and `question_key` together form the primary key. `basis` holds the cited keys and `basis_hash` hashes their values plus the set of stored-answer keys. The row also records `model`, `prompt_hash` and `run_id` (a foreign key to `application_run`, `ON DELETE SET NULL`).
- **Reuse:** only while basis_hash, model and prompt hash all still match; otherwise the row is a miss and is overwritten.
- **Precedence:** stored answers are resolved before the cache is consulted.
- **Debug runs:** `scripts/run_pipeline.py` uses namespace `debug`.
- **Migration 0019** deletes the rows that predate provenance.

## Cover letter (`llm/cover_letter.py`)
- `cover_letter_requested(field_mappings)` is true when a field maps to `cover_letter`. The workflow runs GENERATE_COVER_LETTER only then; otherwise FILL and SUBMIT get no cover letter (a cover-letter field that first appears during fill is then skipped if optional, a fill problem if required).
- `CoverLetterGenerator.generate` (prose model):
  - Input: `profile_text(contact=False)`.
  - System prompt: the style prompt `cover_letter` (`personal.yaml` overrides the default in `prompts.yaml`), with the fixed `cover_letter_rules` (grounding) always appended.
  - The letter gets the same number check as generated answers: one rewrite, then the offending sentences are dropped.
- PDF rendering with fpdf2 lives in the `generate_cover_letter_activity`.

## Other tasks
- `FieldMapper.compute_match_score` (driver) produces a single gestalt score; the decomposed scorer is roadmap B-level work (audit LLM F3).
- Metadata extraction (`job_metadata`, `job_tech_extraction`) is in `workflows/activities/llm.py`.

## Prompts (`config/prompts.yaml`, top key `prompts:`)
- **Prompt keys:** `cover_letter` (default style), `cover_letter_rules`, `field_mapper`, `fallback_answer`, `screening_answer`, `job_metadata`, `job_tech_extraction`, `match_score`, `email_status_classification`, `oneshot_form_map`, `oneshot_discover`, `agent_discover_task`, `agent_map_form_task`, `agent_dropdown_task`, `agent_fix_task`, `option_pick`, `post_submit_judge`.
- **Loading:** `config.load_prompts_config()` (re-read on change) merges in the gitignored `config/personal.yaml` (prompts, known answers, profile). Never copy its contents into docs.

## Tests and eval
- Offline, with `ScriptedLLM` and the synthetic applicant: `test/yahbah/llm/` (`test_field_mapper.py`, `test_screening.py`, `test_profile.py`, `test_answer_cache.py`) and the invariants in `test/invariants/task5_answer_integrity/`.
- **Live eval** (opt-in): `uv run pytest -m live_llm test/yahbah/llm/test_answer_eval.py -s` runs the 53 items in `answer_eval.yaml`: screening facts, jurisdiction and JD-injection traps, plain facts, personal-detail traps, curveballs and STAR questions, on a synthetic profile.
  - **Gates:** 0 critical-fact errors, `NeedsAnswer` recall of at least 95% on traps, and 100% of curveball and behavioural items answered as `generated` with no invented numbers or forbidden claims.
  - `YAHBAH_EVAL_REPEATS` and `YAHBAH_EVAL_OUT` set the repeats and a JSON report.

## Limits
- **Fact-kind coverage:** the keyword classification only covers the kinds listed above. A material question outside them (e.g. "Will you work weekends?") goes through the prose path. It is then material only if it is a yes/no choice or a credential question; otherwise it can come back as `generated=True` prose, which review shows.
- **Unchecked semantic support:** code checks that cited keys exist and that a short answer appears in them, not that the citation logically entails the answer. The batch-2 reviewer pass is meant to cover that.
- **Inconsistent profile sources:** the alert-time match score still builds its own profile text from YAML (`gmail/alert_parser.build_profile_summary`) instead of `profile_text`.
