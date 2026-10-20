# YahBah Wiki Index

Read this first, then open only the pages you need. Source is the authority; pages note where they may lag.
State: branch `browser-use`. The agentic revamp is committed (`999f57a`, `2911263`); batch-1 safety work from the [roadmap](../audits/2026-10-18/roadmap.md) is in progress in the working tree: wave 1 (harness, discovery gate, dashboard security) has landed, wave 2 is next (see [changelog](changelog.md) and [contracts/run-lifecycle.md](contracts/run-lifecycle.md)).

| Page | Covers | Read when |
|---|---|---|
| [architecture.md](architecture.md) | Processes, end-to-end flow, design rules, revamp notes and pending change | Starting any task; need the big picture |
| [changelog.md](changelog.md) | Running record of architectural, structural and decision changes, newest first, back to the first commit | Need the *why* behind the current structure; adding an entry after any architecturally meaningful change |
| [modules/agent.md](modules/agent.md) | `src/yahbah/agent/`: tiered code/one-shot/agent browser automation, board profiles, submit gating | Touching discover/map/fill/submit, a job board, or browser tools |
| [modules/workflows.md](modules/workflows.md) | Temporal workflow, activities, worker, run slots, session registry | Changing run stages, retries, timeouts, activity inputs (`schemas.py`) |
| [modules/llm.md](modules/llm.md) | Ollama model roles, `OllamaClient`, prompts (`config/prompts.yaml`), field mapper, answer cache, LLM logs | Changing models, prompts, answer mapping, cover letters |
| [modules/data-model.md](modules/data-model.md) | DB tables, alembic migrations, config/settings files | Schema changes, migrations, config keys |
| [modules/gmail.md](modules/gmail.md) | Gmail client, verification codes, status poller, job-alert poller | Email-related behaviour |
| [modules/api-frontend.md](modules/api-frontend.md) | FastAPI routes and the Next.js frontend | Adding endpoints or UI |
| [development.md](development.md) | Setup/run commands, tests, scripts, stale docs | Running, testing, debugging a board |
| [contracts/run-lifecycle.md](contracts/run-lifecycle.md) | **Target contract (batch 1, in progress):** run statuses, review gate, submission claim, dedup predicate, retry/cancel, evidence & API shapes, file ownership | Touching run state, submit, dedup, review, or any batch-1 task |

Historical audit reports (point-in-time findings, not authoritative) are indexed in [`docs/audits/`](../audits/README.md); the latest synthesis and plan is the [2026-10-18 roadmap](../audits/2026-10-18/roadmap.md).

Maintenance rules live in the root `CLAUDE.md`.
