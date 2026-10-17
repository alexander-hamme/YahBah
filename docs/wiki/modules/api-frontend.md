# API and Frontend

## FastAPI (`src/yahbah/api/main.py`, routers in `api/routes/`)
Prefixes: `/applications` (list, `/stats`), `/jobs` (`POST` enqueue -> 201), `/runs` (`GET /{id}`, `/steps`, `/artifacts`, `/status-updates`, artifact download, `POST /bulk-delete`, `/{id}/cancel`, `/{id}/retry`, `DELETE /{id}`), `/scheduled` (create, list, `/stats`, get, `approve`/`reject`/`hold`/`promote`, delete). Response models are Pydantic classes in the route files and `schemas.py`. Also `GET /health`, `POST /gmail/poll`, `POST /gmail/alert-poll` (manual poller triggers) in `main.py`. Runtime settings: `GET /settings`, `PUT /settings/{key}` in `main.py` (backed by `config/settings.yaml`). Run: `uv run uvicorn yahbah.api.main:app --reload --port 8000`.

## Frontend (`frontend/`, Next 16.2.1 / React 19)
App routes `src/app/{page,applications,scheduled}`; components in `src/components` (application list, status pipeline, run info card, step timeline, artifact viewer, submit form, testing toggle); API client `src/lib/api.ts`, types `src/lib/types.ts` (must mirror API response schemas; both modified in working tree). `frontend/AGENTS.md` warns this Next version has breaking changes: read `node_modules/next/dist/docs/` before writing Next code. Dev: `npm run dev` in `frontend/`.
