# YahBah Wiki Index

Read this first, then open only the pages you need. Source is the authority; pages note where they may lag.
State: branch `browser-use`. The agentic revamp is committed (`999f57a`, `2911263`). Batch-1 safety work from the [roadmap](../audits/2026-10-18/roadmap.md) is implemented: wave 1 is committed (`d04bc45`), wave 2 (run lifecycle and review gate, submit boundary, answer integrity, review dashboard) in `d886823`, QA-verified. The 2026-10-21 work (discover classification by code evidence, board route, headed fallback) is committed on top of `7b46619`; default `pytest` gave 1094 passed, 2 skipped, and the frontend 120 tests (2026-10-23). Migrations 0018 and 0019 are applied to the dev DB (2026-10-20). A live run has reached discover (run `83a0efc2`, which exposed the misclassification), but no application has been submitted against a live site. See the [changelog](changelog.md) and the [run-lifecycle contract](contracts/run-lifecycle.md).

| Page | Covers | Read when |
|---|---|---|
| [architecture.md](architecture.md) | Processes, end-to-end flow, design rules, revamp notes and pending change | Starting any task; need the big picture |
| [changelog.md](changelog.md) | Running record of architectural, structural and decision changes, newest first, back to the first commit; latest: runtime settings file made machine-local (2026-10-23), before it discover classification, board route, headed fallback (2026-10-21) | Need the *why* behind the current structure; adding an entry after any architecturally meaningful change |
| [modules/agent.md](modules/agent.md) | `src/yahbah/agent/`: tiered code/one-shot/agent browser automation, discover dead-end classification, board profiles and routes, headed fallback, submit boundary (classifier, agent guard, evidence-based verification) | Touching discover/map/fill/submit, a job board, or browser tools |
| [modules/workflows.md](modules/workflows.md) | Temporal workflow and review gate, activities, fenced writes and attempt lease, worker, run slots, session registry, run creation | Changing run stages, retries, timeouts, activity inputs (`schemas.py`) |
| [modules/llm.md](modules/llm.md) | Ollama model roles, `OllamaClient`, applicant profile, prompts (`config/prompts.yaml`), field mapper and screening facts, answer cache, cover letter, live eval, LLM logs | Changing models, prompts, answer mapping, cover letters |
| [modules/data-model.md](modules/data-model.md) | DB tables, alembic migrations, config/settings files (incl. the machine-local `settings.yaml` and its example) | Schema changes, migrations, config keys |
| [modules/gmail.md](modules/gmail.md) | Gmail client, verification codes, status poller, job-alert poller | Email-related behaviour |
| [modules/api-frontend.md](modules/api-frontend.md) | FastAPI routes, error shape and settings; the Next.js frontend (outcome-driven UI, review queue and keyboard triage) | Adding endpoints or UI |
| [development.md](development.md) | Setup/run commands, tests, scripts, stale docs | Running, testing, debugging a board |
| [contracts/run-lifecycle.md](contracts/run-lifecycle.md) | **Implemented, authoritative contract (batch 1, 2026-10-20, amended 2026-10-21; main session owns changes):** run statuses and `failure_reason`, discover-stage classification and the headed fallback (§2), `submission_mode`, review gate, `possibly_applied()`, submission claim and uncertain runs, attempt lease, retry/cancel, evidence and API shapes, migrations | Touching run state, discover dead ends, submit, dedup, review, API error codes, or proposing a change to any of them |

Historical audit reports (point-in-time findings, not authoritative) are indexed in [`docs/audits/`](../audits/README.md); the latest synthesis and plan is the [2026-10-18 roadmap](../audits/2026-10-18/roadmap.md).

Maintenance rules live in the root `CLAUDE.md`.
