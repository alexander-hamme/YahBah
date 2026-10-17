# LLM Layer (`src/yahbah/llm/`, `src/yahbah/agent/llm.py`)

All models are local Ollama. Nothing sends page or personal data to the cloud.

## Model roles (`llm/models.py`)
`Role = driver | prose | vision | escalation`. `spec(role)` / `ollama_options(role)` give fixed `num_ctx`, `num_predict` (4096; 6144 when thinking), `think`, `keep_alive`, so a model is always requested with identical options and never reloads mid-run.
- driver: `settings.model_driver` (default `qwen3-coder-next:latest`, ctx 40960): agent loops, HTML work, mapping JSON, page judging; text only.
- prose / vision / escalation: `settings.model_prose_vision` (default `qwen3.8:27b`, ctx 32768); escalation has `think=True`.
Defaults live in `src/yahbah/config.py` (`Settings`); verify there, as they are tuning knobs.

## Clients
- `llm/client.py`: `OllamaClient(role)` with `generate_structured` (output constrained by a Pydantic JSON schema), `generate_text`, optional images. Raises `LLMError`.
- `agent/llm.py`: browser-use `ChatOllama` subclass for agent loops ([agent](agent.md)).
- `llm/logs.py`: per-run log dir `artifacts/<run_id>/llm/` (`raw.jsonl`, `steps/`, `js_calls.jsonl`) selected via a contextvar set at the start of each activity; total `llm` logs capped at 250 MB (oldest pruned).

## Task modules
- `llm/field_mapper.py`: `FieldMapper.map` maps form fields to profile values (known answers and profile resolved in code; constrained mapping call; prose model for custom questions; `NeedsAnswer` when unanswerable), `compute_match_score`, `profile_text`. Question answers cached in DB table `answer_cache` (keyed by normalized question+options; added in migration 0017).
- `llm/cover_letter.py`: `CoverLetterGenerator.generate` (prose model); PDF rendering with fpdf2 lives in the `generate_cover_letter_activity`.
- Metadata extraction (`job_metadata`, `job_tech_extraction`) is in `workflows/activities/llm.py`.

## Prompts (`config/prompts.yaml`, top key `prompts:`)
`field_mapper`, `fallback_answer`, `job_metadata`, `job_tech_extraction`, `match_score`, `email_status_classification`, `oneshot_form_map`, `oneshot_discover`, `agent_discover_task`, `agent_map_form_task`, `agent_dropdown_task`, `agent_fix_task`, `option_pick`, `post_submit_judge`. Loaded by `config.load_prompts_config()` (lru-cached), merged with gitignored `config/personal.yaml` (cover letter + known answers; never copy its contents into docs). `config/prompts.yaml` is currently modified in the working tree.
