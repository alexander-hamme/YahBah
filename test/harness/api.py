"""The real API on a test app (no lifespan, no middleware), for route-level tests.

    async with api_client(temporal=FakeTemporal()) as client:
        resp = await client.post(f"/runs/{run_id}/retry")

make_app() mounts every route of yahbah.api.main.app (the routers and the app's own /settings and
/health endpoints) and installs the real app's exception handlers, so errors have the real bodies
(contract §8, e.g. FastAPI's validation errors as `invalid_request`). What it leaves out: the
lifespan (it would connect to Temporal) and the host-check/CORS middleware.

`temporal` becomes app.state.temporal: a FakeTemporal that records what the routes ask of
Temporal, or a real client from the `temporal_env` fixture. Unhandled route errors come back as
500 responses (raise_app_exceptions=False), like the real server.
"""
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

import httpx
from fastapi import FastAPI
from fastapi.routing import APIRoute


def make_app(temporal=None) -> FastAPI:
    from yahbah.api.main import app as real

    app = FastAPI()
    app.router.routes.extend(route for route in real.router.routes if isinstance(route, APIRoute))
    for exc_class, handler in real.exception_handlers.items():
        app.add_exception_handler(exc_class, handler)
    app.state.temporal = temporal
    return app


@asynccontextmanager
async def api_client(temporal=None):
    transport = httpx.ASGITransport(app=make_app(temporal), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as client:
        yield client


@dataclass
class FakeHandle:
    temporal: "FakeTemporal"
    id: str

    async def cancel(self) -> None:
        self.temporal.actions.append(("cancel", self.id))

    async def terminate(self, *args, **kwargs) -> None:
        self.temporal.actions.append(("terminate", self.id))

    async def signal(self, signal, *args, **kwargs) -> None:
        name = signal if isinstance(signal, str) else getattr(signal, "__name__", str(signal))
        self.temporal.actions.append(("signal", self.id, name, tuple(kwargs.get("args") or args)))

    async def describe(self):
        raise RuntimeError(f"FakeTemporal: no workflow {self.id}")


@dataclass
class FakeTemporal:
    """Records start_workflow and handle calls; starts nothing."""

    started: list[dict] = field(default_factory=list)
    actions: list[tuple] = field(default_factory=list)

    async def start_workflow(self, workflow, arg=None, *, id: str, task_queue: str, **kwargs):
        self.started.append({"workflow": workflow, "arg": arg, "id": id, "task_queue": task_queue, **kwargs})
        return FakeHandle(self, id)

    def get_workflow_handle(self, workflow_id: str, **kwargs) -> FakeHandle:
        return FakeHandle(self, workflow_id)
