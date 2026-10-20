from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from loguru import logger
from pydantic import BaseModel
from starlette.datastructures import Headers
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send
from temporalio.client import Client as TemporalClient

from yahbah.config import (
    RUNTIME_SETTINGS,
    get_effective_runtime_settings,
    set_runtime_setting,
    settings,
    validate_runtime_setting,
)
from yahbah.api.errors import api_error
from yahbah.api.routes import applications, jobs, runs, scheduled


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("API starting up")
    app.state.temporal = await TemporalClient.connect(
        settings.temporal_host,
        namespace=settings.temporal_namespace,
    )
    yield
    logger.info("API shutting down")


app = FastAPI(
    title="YahBah — Autonomous Job Application API",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Local-only API: reject any Host other than loopback names (blunts DNS
# rebinding). Starlette's parser splits on the first ":", so IPv6 literals
# like [::1] cannot match; uvicorn binds 127.0.0.1 only, so none arrive.
_LOCAL_HOSTS = ["localhost", "127.0.0.1"]


def _reject_foreign_forwarded_host(app: ASGIApp) -> ASGIApp:
    """The Next /api rewrite sets Host to the target (localhost:8000) and puts
    the browser's Host in X-Forwarded-Host, so check that one too."""
    async def middleware(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket"):
            forwarded = Headers(scope=scope).get("x-forwarded-host")
            if forwarded is not None and any(
                h.strip().split(":")[0] not in _LOCAL_HOSTS for h in forwarded.split(",")
            ):
                await PlainTextResponse("Invalid host header", status_code=400)(scope, receive, send)
                return
        await app(scope, receive, send)
    return middleware


# Added last = outermost: host checks run before CORS and routing.
app.add_middleware(_reject_foreign_forwarded_host)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=_LOCAL_HOSTS)

_LOCATIONS = {"body", "query", "path", "header", "cookie"}


def _validation_message(errors: list[dict]) -> str:
    """FastAPI's validation errors in one line: "page: Input should be greater than or equal to 1"."""
    parts = []
    for err in errors[:3]:
        loc = [str(p) for p in err.get("loc", ()) if p not in _LOCATIONS]
        parts.append(f"{'.'.join(loc)}: {err.get('msg', 'invalid')}" if loc else str(err.get("msg", "invalid")))
    more = f" (and {len(errors) - 3} more)" if len(errors) > 3 else ""
    return f"Invalid request: {'; '.join(parts)}{more}."


@app.exception_handler(RequestValidationError)
async def _invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Request validation failures use the contract §8 error body too: {"detail": {"code", "message"}}."""
    return JSONResponse(status_code=422, content={
        "detail": {"code": "invalid_request", "message": _validation_message(list(exc.errors()))}})


app.include_router(applications.router, prefix="/applications", tags=["applications"])
app.include_router(jobs.router, prefix="/jobs", tags=["jobs"])
app.include_router(runs.router, prefix="/runs", tags=["runs"])
app.include_router(scheduled.router, prefix="/scheduled", tags=["scheduled"])


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


# ── Runtime settings (contract §3, §8) ────────────────────────────────────

async def _effective_settings() -> dict:
    """The settings as runs use them (submission_mode fails closed to review), known keys only."""
    effective = await get_effective_runtime_settings()
    return {key: value for key, value in effective.items() if key in RUNTIME_SETTINGS}


@app.get("/settings")
async def get_settings() -> dict:
    return await _effective_settings()


class _SettingUpdate(BaseModel):
    value: Any


@app.put("/settings/{key}")
async def update_setting(key: str, body: _SettingUpdate) -> dict:
    """Validated by config.validate_runtime_setting(); returns the effective settings after the write.
    submission_mode and review_window_hours are snapshotted per run, so a change affects new runs only."""
    if key not in RUNTIME_SETTINGS:
        raise api_error(404, "unknown_setting", f"There is no runtime setting {key!r}.")
    try:
        value = validate_runtime_setting(key, body.value)
    except ValueError as exc:
        raise api_error(422, "invalid_setting", f"{exc}.") from None
    await set_runtime_setting(key, value)
    return await _effective_settings()


@app.post("/gmail/poll")
async def trigger_gmail_poll() -> dict:
    """Manually trigger a Gmail status poll cycle."""
    from yahbah.gmail.poller import trigger_poll
    try:
        processed, skipped = await trigger_poll()
        return {"success": True, "processed": processed, "skipped": skipped}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@app.post("/gmail/alert-poll")
async def trigger_gmail_alert_poll() -> dict:
    """Manually trigger a Gmail job alert poll cycle."""
    from yahbah.gmail.alert_poller import trigger_alert_poll
    try:
        processed, skipped = await trigger_alert_poll()
        return {"success": True, "processed": processed, "skipped": skipped}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


class _BackfillRequest(BaseModel):
    since: str  # ISO date, e.g. "2026-04-10"

@app.post("/gmail/alert-backfill")
async def trigger_alert_backfill(body: _BackfillRequest) -> dict:
    """Delete the alert checkpoint and backfill from a given date."""
    from yahbah.gmail.alert_poller import trigger_backfill
    try:
        processed, skipped = await trigger_backfill(body.since)
        return {"success": True, "processed": processed, "skipped": skipped}
    except Exception as exc:
        return {"success": False, "error": str(exc)}
