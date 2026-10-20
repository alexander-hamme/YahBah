"""The real API routers on a bare FastAPI app (no lifespan, no middleware), for route-level tests.

    async with api_client(temporal=FakeTemporal()) as client:
        resp = await client.post(f"/runs/{run_id}/retry")

`temporal` becomes app.state.temporal: a FakeTemporal that records what the routes ask of
Temporal, or a real client from the `temporal_env` fixture. Unhandled route errors come back as
500 responses (raise_app_exceptions=False), like the real server.
"""
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

import httpx
from fastapi import FastAPI


def make_app(temporal=None) -> FastAPI:
    from yahbah.api.routes import jobs, runs

    app = FastAPI()
    app.include_router(jobs.router, prefix="/jobs")
    app.include_router(runs.router, prefix="/runs")
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
